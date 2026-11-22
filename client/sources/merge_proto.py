#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task8 迁移原型（merge_proto.py）
================================
OpenVPN 页与 VPNGate feed 页合并为一页（canonical 来源键 vpngate）的
**纯 dict 迁移原型**。设计全文见本目录 DESIGN.md；口径一一对应：

- CANONICAL_SOURCE / LEGACY_SOURCE_KEYS     DESIGN §1（分类定名与旧键映射表）
- identity_of / canonical_id                DESIGN §2（身份＝登记制 Node.id 口径去源段）
- merge_records(a, b)                       DESIGN §3（逐字段合并规则）
- migrate_library(records)                  DESIGN §4（整库迁移：改键、按身份去重合并）
- sample_id_map / remap_samples             DESIGN §4.3（verify_samples.node_id 重映射）
- migrate_registry_json(data)               DESIGN §5.3（sources.json 存量迁移）

独立性红线：只用标准库；不 import client 包；不依赖 PySide6；不发任何网络
请求；不含任何测速/连接/排序优选逻辑（本模块的排序只为确定性，不评节点优劣）。
输入输出都是 Node.to_dict() 形状的纯 dict（可直接对着 store 的行 JSON 用）。

无损机制（DESIGN §3.9）：两条不同记录合并时，两份完整旧记录原样收进
extra["merged_superseded"]（键＝旧记录 id）——任何逐字段规则出错，旧数据也
一条不少。幂等机制（DESIGN §4.4）：跑完一遍后每身份恰一条、无旧键可改，
第二遍零变更；merge_records(r, r) == r。
"""
from __future__ import annotations

import copy
import re

# ---------------------------------------------------------------------------
# 分类常量与旧键映射表（DESIGN §1）
# ---------------------------------------------------------------------------
CANONICAL_SOURCE = "vpngate"          # 合并页：VPNGate 来源页
LEGACY_SOURCE_KEYS = {
    "openvpn": CANONICAL_SOURCE,      # 旧 OpenVPN 页（协议当来源的假象）
    "vpngate_feed": CANONICAL_SOURCE, # 旧 VPN Gate feed 页（接入形态当来源的假象）
}
UNTOUCHED_SOURCES = ("manual", "pipeline")   # 不在合并范围：逐字节原样

MERGED_SUPERSEDED_KEY = "merged_superseded"  # 无损兜底快照的 extra 键（§3.9）

# 登记条目（DESIGN §5）：feed_mirror 类型废除，镜像条目并进唯一 feed 条目
FEED_ENTRY_ID = "feed"                       # 合并后唯一 feed 类条目 id（不变）
LEGACY_MIRROR_ENTRY_ID = "feed-mirror"       # 旧镜像条目 id（废除）
FEED_FAMILY_STAMPS = (FEED_ENTRY_ID, LEGACY_MIRROR_ENTRY_ID)
FEED_DEFAULT_ADDRESS = "https://www.vpngate.net/api/iphone/"   # 同 source_registry

# 旧 id 尾段 digest 判定（sha1(discriminator) 前 8 位；与 diag.mask_node_id 同款）
_DIGEST_RE = re.compile(r"^[0-9a-f]{8}$")


# ---------------------------------------------------------------------------
# 身份口径（DESIGN §2：登记制 Node.id 去掉 source 段）
# ---------------------------------------------------------------------------
def _digest_of(node_id) -> str:
    """旧 id 尾段恰为 8 位十六进制 ⇒ sha1 digest，原样沿用（原文不可逆，
    重算不出；沿用保住唯一性语义）。两页实际不产 digest，防御性规则。"""
    parts = str(node_id or "").split(":")
    if parts and _DIGEST_RE.match(parts[-1] or ""):
        return parts[-1]
    return ""


def identity_of(record: dict) -> tuple:
    """合并用的身份：(protocol, host, port, digest)。口径＝登记制按 Node.id
    合并（source_registry.update_entry 的 old_by_id），id 的第一段是 source，
    剥掉后即「同一台服务器」。port 归一成 int，防 str 混入。"""
    try:
        port = int(record.get("port") or 0)
    except (TypeError, ValueError):
        port = 0
    return (str(record.get("protocol") or ""),
            str(record.get("host") or ""),
            port,
            _digest_of(record.get("id")))


def canonical_id(record: dict) -> str:
    """合并后 id：source 段换成 canonical 键，其余段照旧（digest 沿用）。"""
    proto, host, port, digest = identity_of(record)
    new_id = f"{CANONICAL_SOURCE}:{proto}:{host}:{port}"
    if digest:
        new_id += f":{digest}"
    return new_id


# ---------------------------------------------------------------------------
# 侧别与胜者判定（DESIGN §3.0：内容驱动，与参数顺序无关）
# ---------------------------------------------------------------------------
def _side_of(record: dict):
    src = record.get("source")
    if src == "vpngate_feed":
        return "feed"
    if src == "openvpn":
        return "openvpn"
    return None                      # 已迁移（vpngate）或范围外来源


def _priority_of(record: dict) -> int:
    return {"feed": 0, "openvpn": 1}.get(_side_of(record), 2)


def _pick_by_side(a: dict, b: dict) -> tuple:
    """side-priority 胜者：feed 侧 > openvpn 侧 > 其他；同级按 id 字典序。"""
    pa, pb = _priority_of(a), _priority_of(b)
    if pa != pb:
        return (a, b) if pa < pb else (b, a)
    ka, kb = str(a.get("id") or ""), str(b.get("id") or "")
    return (a, b) if ka <= kb else (b, a)


def _verified_at(record: dict) -> float:
    try:
        return float((record.get("exit_profile") or {}).get("verified_at") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _is_verified(record: dict) -> bool:
    ep = record.get("exit_profile") or {}
    return bool(ep.get("ip") or ep.get("verified_at"))


def _pick_by_recency(a: dict, b: dict) -> tuple:
    """verified_at 新者胜（平局 → side-priority）。用于「最近实测快照」语义
    的字段：出口画像、实测延迟样本。"""
    ta, tb = _verified_at(a), _verified_at(b)
    if ta != tb:
        return (a, b) if ta > tb else (b, a)
    return _pick_by_side(a, b)


def _name_winner(a: dict, b: dict) -> dict:
    """DESIGN §3.2：带 orig_name 的一侧胜（其 name 是实测出口名）；两侧都有
    → verified_at 新者；都没有 → feed 侧（对齐登记刷新稳态）。"""
    ea, eb = a.get("extra") or {}, b.get("extra") or {}
    oa, ob = bool(ea.get("orig_name")), bool(eb.get("orig_name"))
    if oa and not ob:
        return a
    if ob and not oa:
        return b
    if oa and ob:
        return _pick_by_recency(a, b)[0]
    return _pick_by_side(a, b)[0]


# ---------------------------------------------------------------------------
# 逐字段合并（DESIGN §3）
# ---------------------------------------------------------------------------
def _merge_config(a: dict, b: dict) -> tuple:
    """返回 (config_ref, config_format)。规则 §3.3：一空一有→有者；相等→原样；
    不等→feed b64 为主，同级按 id 序。非主方整份配置由整记录快照兜底（§3.9）。"""
    ca, cb = a.get("config_ref") or "", b.get("config_ref") or ""
    fa = (a.get("extra") or {}).get("config_format")
    fb = (b.get("extra") or {}).get("config_format")
    if not ca and not cb:
        return "", None
    if ca and not cb:
        return ca, fa
    if cb and not ca:
        return cb, fb
    if ca == cb:
        return ca, fa if fa is not None else fb
    a_b64, b_b64 = (fa == "b64"), (fb == "b64")
    if a_b64 != b_b64:
        return (ca, fa) if a_b64 else (cb, fb)
    winner = _pick_by_side(a, b)[0]
    return (winner.get("config_ref") or "",
            (winner.get("extra") or {}).get("config_format"))


def _merge_exit_profile(a: dict, b: dict) -> dict:
    """DESIGN §3.4：恰一侧验过→验过侧整体沿用（merge_fresh 同口径）；双侧都验
    →verified_at 新者整体为主，dirty 取验过侧并集（拒绝史不丢）。"""
    va, vb = _is_verified(a), _is_verified(b)
    if va and vb:
        winner = _pick_by_recency(a, b)[0]
    elif va:
        winner = a
    elif vb:
        winner = b
    else:
        winner = _pick_by_side(a, b)[0]
    prof = dict(winner.get("exit_profile") or {})
    if va and vb:
        dirty = set(prof.get("dirty") or [])
        for other in (a, b):
            if other is not winner:
                dirty |= set((other.get("exit_profile") or {}).get("dirty") or [])
        prof["dirty"] = sorted(dirty)
    return prof


def _merge_metrics(a: dict, b: dict) -> dict:
    """DESIGN §3.5/§3.6。实测样本 latency/jitter：None≠0，恰一侧非 None→该侧；
    冲突→verified_at 新侧（apply_outcome 把画像与实测同步写入，时间可比）。
    feed 报告值 sessions/speed/uptime：有值侧并入，冲突→feed 侧（可再生，
    下次刷新以 feed 行为准）。"""
    out = {}
    for key in ("latency_ms", "jitter_ms"):
        x = (a.get("metrics") or {}).get(key)
        y = (b.get("metrics") or {}).get(key)
        if x is None:
            out[key] = y
        elif y is None or x == y:
            out[key] = x
        else:
            out[key] = (_pick_by_recency(a, b)[0].get("metrics") or {}).get(key)
    winner, loser = _pick_by_side(a, b)
    for key in ("sessions", "speed_mbps", "uptime_hours"):
        wv = (winner.get("metrics") or {}).get(key)
        lv = (loser.get("metrics") or {}).get(key)
        out[key] = wv if wv is not None else lv
    return out


def _merge_extra(a: dict, b: dict) -> dict:
    """DESIGN §3.8。固定语义键逐组处理，其余键单侧保留/冲突 side-priority
    胜者留主位；一切败值由整记录快照兜底（§3.9），这里只管「主位放谁」。"""
    out = {}
    ea, eb = a.get("extra") or {}, b.get("extra") or {}

    # 登记戳：任一侧 feed 系戳、或任一侧本就是 feed 侧记录（feed 页存量
    # 全部是 feed 解析器所产，§5.2 补戳）→ 统一指向合并后唯一条目
    stamps = [ea.get("registry_entry"), eb.get("registry_entry")]
    if (any(v in FEED_FAMILY_STAMPS for v in stamps)
            or _side_of(a) == "feed" or _side_of(b) == "feed"):
        out["registry_entry"] = FEED_ENTRY_ID
    elif any(v is not None for v in stamps):
        out["registry_entry"] = ea.get("registry_entry")
        if out["registry_entry"] is None:
            out["registry_entry"] = eb.get("registry_entry")

    # 消失标记三键一组：任一侧带 → 保留（at 取较早侧；缺 at 视为最早）
    gone_sides = [r for r in (a, b)
                  if (r.get("extra") or {}).get("source_gone") == "1"]
    if gone_sides:
        winner = min(gone_sides,
                     key=lambda r: float((r.get("extra") or {})
                                         .get("source_gone_at") or 0.0))
        out["source_gone"] = "1"
        for key in ("source_gone_at", "source_gone_from"):
            v = (winner.get("extra") or {}).get(key)
            if v is not None:
                out[key] = v

    # 隔离标记：任一侧有 → 保留；两侧都有 → reason 去重拼接
    quar_sides = [r for r in (a, b)
                  if (r.get("extra") or {}).get("quarantined") == "1"]
    if quar_sides:
        out["quarantined"] = "1"
        reasons = set()
        for r in quar_sides:
            text = (r.get("extra") or {}).get("quarantine_reason") or ""
            reasons.update(seg for seg in str(text).split(",") if seg)
        if reasons:
            out["quarantine_reason"] = ",".join(sorted(reasons))

    # 其余键：并集；双侧不等 → side-priority 胜者留主位
    winner, _loser = _pick_by_side(a, b)
    for key in sorted(set(ea) | set(eb)):
        if key in out or key == MERGED_SUPERSEDED_KEY:
            continue                      # 语义键已处理；快照键由 merge_records 统一挂
        x, y = ea.get(key), eb.get(key)
        if x is None:
            out[key] = y
        elif y is None or x == y:
            out[key] = x
        else:
            out[key] = (winner.get("extra") or {}).get(key)
    return {k: v for k, v in out.items() if v is not None}


def merge_records(a: dict, b: dict) -> dict:
    """按 DESIGN §3 逐字段规则合并两条**同身份**记录；返回新 dict，不改输入。

    a == b（逐字节相同）→ 原样深拷贝返回，不建快照（幂等微观基础）。
    身份不同 → ValueError（调用方先用 identity_of 分组）。
    无损：两份完整旧记录进 extra[merged_superseded]（键＝旧 id，§3.9）。"""
    if identity_of(a) != identity_of(b):
        raise ValueError(
            f"身份不同，拒绝合并: {a.get('id')!r} vs {b.get('id')!r}")
    if a == b:
        return copy.deepcopy(a)

    lo, hi = sorted((a, b), key=lambda r: str(r.get("id") or ""))
    name_winner = _name_winner(a, b)
    config_ref, config_format = _merge_config(a, b)

    merged = {
        "id": canonical_id(a),            # 同身份 ⇒ canonical_id(a)==canonical_id(b)
        "name": str(name_winner.get("name") or ""),
        "protocol": a.get("protocol"),
        "source": CANONICAL_SOURCE,
        "host": a.get("host"),
        "port": identity_of(a)[2],
        "config_ref": config_ref,
        "exit_profile": _merge_exit_profile(a, b),
        "scenes": sorted(set(a.get("scenes") or []) | set(b.get("scenes") or [])),
        "metrics": _merge_metrics(a, b),
        "pinned": bool(a.get("pinned")) or bool(b.get("pinned")),
        "banned": bool(a.get("banned")) or bool(b.get("banned")),
        "extra": _merge_extra(a, b),
    }
    # name / orig_name 同侧配对（restore-names 靠 orig_name 还原，§3.8）
    orig_name = (name_winner.get("extra") or {}).get("orig_name")
    if orig_name is not None:
        merged["extra"]["orig_name"] = orig_name
    # 无损兜底：两份完整旧记录原样留档（键＝旧 id；键序按 id 字典序，确定性）
    merged["extra"][MERGED_SUPERSEDED_KEY] = {
        str(lo.get("id") or ""): copy.deepcopy(lo),
        str(hi.get("id") or ""): copy.deepcopy(hi),
    }
    return merged


# ---------------------------------------------------------------------------
# 整库迁移（DESIGN §4）
# ---------------------------------------------------------------------------
def _rewrite_legacy(record: dict) -> dict:
    """旧两键记录：改 source、重算 id、登记戳按 §5.2 处理。其余记录深拷贝原样。"""
    r = copy.deepcopy(record)
    src = r.get("source")
    if src not in LEGACY_SOURCE_KEYS:
        return r
    new_id = canonical_id(r)              # 先算（digest 取自旧 id 尾段）
    r["source"] = CANONICAL_SOURCE
    r["id"] = new_id
    extra = r.setdefault("extra", {})
    stamp = extra.get("registry_entry")
    if stamp == LEGACY_MIRROR_ENTRY_ID:
        extra["registry_entry"] = FEED_ENTRY_ID       # 镜像戳改写（§3.8）
    elif stamp is None and src == "vpngate_feed":
        # feed 页存量全部是 feed 解析器所产：补登记戳（§5.2 归属等价性论证）
        extra["registry_entry"] = FEED_ENTRY_ID
    # source=openvpn 且无戳（手导 .ovpn）：不补戳——保持「没戳的绝不碰」保护
    return r


def migrate_library(records) -> list:
    """整库迁移（DESIGN §4.1）：旧两键改写 → 按 identity 去重合并 → 稳定输出。

    - 只动 source ∈ {openvpn, vpngate_feed} 的记录；manual/pipeline/未知来源
      逐字节原样（深拷贝等值）。
    - 每身份恰一条：组内仅一条走规范化改写（_rewrite_legacy：改键、重算
      id、登记戳按 §5.2 处理）；组内多条直接对**合并前的原始记录**两两
      fold merge_records（它自己完成改键、重算 id、戳处理，且快照留档的
      是原始记录——旧键、旧 id、全部字段原样进 stash，无损含旧标识）。
    - 输出按各身份首次出现顺序排列（确定性，便于 diff 与测试）。
    - 幂等（§4.4）：第二遍无旧键可改、无组可并、快照不被触碰 → 深等价。
    - 非 dict 记录显式报错（绝不静默跳过）。"""
    if records is None:
        raise TypeError("records 不能是 None")
    groups: dict = {}
    order: list = []
    for record in records:
        if not isinstance(record, dict):
            raise TypeError(f"记录必须是 dict，得到 {type(record).__name__}")
        ident = identity_of(record)      # 改键不改 protocol/host/port/digest ⇒ 同一身份
        if ident not in groups:
            groups[ident] = []
            order.append(ident)
        groups[ident].append(record)
    out = []
    for ident in order:
        members = groups[ident]
        if len(members) == 1:
            out.append(_rewrite_legacy(members[0]))
            continue
        acc = members[0]
        for nxt in members[1:]:
            acc = merge_records(acc, nxt)
        # 防御：组内成员逐字节相同（store 不可能出现的重复输入）时
        # merge_records 原样返回原始记录，此处补上规范化改写。
        if acc.get("source") in LEGACY_SOURCE_KEYS:
            acc = _rewrite_legacy(acc)
        out.append(acc)
    return out


# ---------------------------------------------------------------------------
# 验真样本表重映射（DESIGN §4.3：store.verify_samples 按 node_id 存）
# ---------------------------------------------------------------------------
def sample_id_map(before_records, after_records) -> dict:
    """由身份对齐推导 {旧 node_id: 新 node_id}。范围外记录映射到自身。"""
    new_by_ident = {}
    for r in after_records:
        new_by_ident[identity_of(r)] = r.get("id")
    mapping = {}
    for r in before_records:
        new_id = new_by_ident.get(identity_of(r))
        if new_id is not None:
            mapping[r.get("id")] = new_id
    return mapping


def remap_samples(samples, mapping: dict) -> list:
    """样本行（含 node_id 键的 dict）按映射改写；映射外的行原样保留。
    行数与内容零丢失；同 id 样本序列次序不变（samples_for 本就 ORDER BY ts）。"""
    out = []
    for s in samples:
        s2 = dict(s)
        old = s2.get("node_id")
        if old in mapping:
            s2["node_id"] = mapping[old]
        out.append(s2)
    return out


# ---------------------------------------------------------------------------
# sources.json 存量迁移（DESIGN §5.3）
# ---------------------------------------------------------------------------
def migrate_registry_json(data: dict) -> dict:
    """登记表 JSON（{"version":1,"entries":[...],"last_updates":{...}}）迁移：

    - feed 条目在 → page 改 vpngate，其余不动；
    - feed 不存在、镜像存在 → 首个镜像就地转正（type/id/page），记账挂靠 feed；
    - 两者都在且镜像登记了非官方默认的自定义地址而 feed 是官方默认 →
      feed 条目采纳镜像地址（显式登记 > 自动填的默认；副作用：不再有官方
      回退，见 DESIGN §5.3）；
    - 其余镜像条目与其记账删除（转正情形记账保留挂靠；报告记账非节点数据）。
    幂等：无 feed_mirror 残留时仅 page 归一，深等价返回。"""
    if not isinstance(data, dict):
        raise TypeError("登记表数据必须是 dict")
    d = copy.deepcopy(data)
    entries = d.setdefault("entries", [])
    last = d.setdefault("last_updates", {})

    def _is_feed(e):
        return e.get("type") == "feed" or e.get("id") == FEED_ENTRY_ID

    def _is_mirror(e):
        return e.get("type") == "feed_mirror" or e.get("id") == LEGACY_MIRROR_ENTRY_ID

    feed = next((e for e in entries if _is_feed(e)), None)
    if feed is not None:
        feed["page"] = CANONICAL_SOURCE

    promoted = False
    kept = []
    for entry in entries:
        if not _is_mirror(entry):
            kept.append(entry)
            continue
        if feed is not None:
            # 两者都在：自定义镜像地址显式登记优先；镜像条目与记账删除
            addr = entry.get("address")
            if (addr and addr != FEED_DEFAULT_ADDRESS
                    and feed.get("address") == FEED_DEFAULT_ADDRESS):
                feed["address"] = addr
            last.pop(LEGACY_MIRROR_ENTRY_ID, None)
            continue
        if not promoted:
            # 只登记过镜像：首个镜像转正为 feed 条目，记账跟条目走
            entry["type"] = "feed"
            entry["id"] = FEED_ENTRY_ID
            entry["page"] = CANONICAL_SOURCE
            if LEGACY_MIRROR_ENTRY_ID in last:
                last[FEED_ENTRY_ID] = last.pop(LEGACY_MIRROR_ENTRY_ID)
            promoted = True
            kept.append(entry)
            continue
        last.pop(LEGACY_MIRROR_ENTRY_ID, None)       # 防御：多余镜像一并清掉
    d["entries"] = kept
    return d
