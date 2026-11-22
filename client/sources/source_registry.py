#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
来源登记原型（sources/source_registry.py 阶段二前置）
======================================================
来源「登记制」：可再拉取的来源（订阅 URL / VPN Gate feed）把地址登记
下来，一键更新；更新时与库内已有节点**按身份合并**——已验出的出口/延迟
样本不丢，新节点进来、消失的节点标记而不是直接删。
（task8/task23 落页：feed→OpenVPN 镜像条目废除，两入口合一为 feed；
存量 sources.json 由 sources/landing.py 启动迁移，work8/DESIGN §5。）
设计全文见本目录 DESIGN.md；已集成进 client 包（task3 原型落地 v0.6.0）：不发任何网络请求除非
真实调用方显式使用默认拉取函数（测试一律注入假拉取函数，零联网）。

复用不重造（口径见 DESIGN.md §10）：
- feed 解析：client.sources.feed.parse_feed_to_nodes（source 参数决定落页；
  自动识别官方 CSV / 镜像 JSON；坏配置节点在门口拦下）
- 订阅解析：format_import.import_any_text（task2 统一入口，已集成进
  client/sources；识别不了显式报错，绝不静默空结果）
- 节点存取：client.core.store.Store（只读材料，鸭子类型调用，不改它）

本模块刻意不含：测速、连接、排序、选择（铁律）；定时器、后台线程
（「定时更新」只在 DESIGN.md §9 的设计里）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import time
import urllib.request
from dataclasses import dataclass

from ..core.model import Node

# task8/task23 落页（work8/DESIGN §5.1）：feed_mirror 类型废除，两入口
# 合一——同一份 VPNGate feed 只剩「feed」一个登记入口，落 VPNGate 合并页。
ENTRY_TYPES = ("subscription", "feed")

# 类型 → 来源页归属：来源第一维度（用户定 2026-11-19），节点落哪页 =
# 产出 Node 的 source 字段，由 type 唯一决定，不可配。
ENTRY_PAGE = {
    "subscription": "manual",        # 手动导入页（D1：订阅导入归手动页）
    "feed": "vpngate",               # VPNGate 来源页（合并页，task8/task23）
}

# VPN Gate feed 官方地址与回退镜像。出处是一级资料：自家流水线天天在拉
# 的地址（gate-system/frontend/vpngate.py:81-85），不是网上抄的；
# 官方失败回退镜像也是流水线的既有口径（vpngate.py:267）。
FEED_DEFAULT_ADDRESS = "https://www.vpngate.net/api/iphone/"
FEED_FALLBACK_ADDRESS = ("https://raw.githubusercontent.com/fdciabdul/"
                         "Vpngate-Scraper-API/main/json/data.json")

# 本地侧 extra 白名单：这些键由 verify.py 写在节点行上（orig_name:323、
# quarantined/quarantine_reason:330-334），来源解析永远不产它们——
# 刷新合并时必须从库内旧节点带回，否则一次刷新就洗掉验真成果。
# merged_superseded 是 task8 无损兜底快照（work8/DESIGN §3.9，键=旧记录
# id 的整记录原样）：task23 落页起入白名单——不加则第一次 feed 刷新就把
# 迁移保下来的备选配置/败值快照洗掉（work8/DESIGN §8 集成硬要求第 3 条）。
_LOCAL_EXTRA_KEYS = ("orig_name", "quarantined", "quarantine_reason",
                     "merged_superseded")
# 消失标记三键（§8）：打上 = 源里暂时没了；来源里重新出现即清除。
_GONE_KEYS = ("source_gone", "source_gone_at", "source_gone_from")

# 真实拉取的 UA（与 subscription._BROWSER_UA 同一惯例：裸 urllib UA 容易被拒）
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


# ---------------------------------------------------------------------------
# 依赖模块惰性加载（失败显式报错，绝不静默降级）
# ---------------------------------------------------------------------------
_feed_mod = None
_format_import_mod = None


def _load_feed_module():
    """client.sources.feed：feed 与镜像共用的解析器。只依赖标准库 +
    core.model，不牵 PyYAML。三步链：包内相对 → client-dev 树（包）→ 失败
    原样抛出（client-dev 是本原型的硬依赖，没有它 Node 都没有）。"""
    global _feed_mod
    if _feed_mod is None:
        from ..sources import feed as m
        _feed_mod = m
    return _feed_mod


def _load_format_import():
    """format_import（task2 统一入口）：订阅刷新的解析器。已集成进
    client/sources；加载不到显式 ImportError——绝不静默降级回
    parse_subscription_text 旧启发式，那会把 task2 修掉的 F1/F2/F3 带回来。
    惰性：feed 路径不加载它。"""
    global _format_import_mod
    if _format_import_mod is not None:
        return _format_import_mod
    from ..sources import format_import as m
    _format_import_mod = m
    return _format_import_mod


# ---------------------------------------------------------------------------
# 真实拉取（只许真实调用方用；测试必须注入 fetch_fn，绝不触发这两个函数）
# ---------------------------------------------------------------------------
def fetch_subscription_text(entry) -> str:
    """订阅地址 → 文本。复用 subscription.fetch_subscription_text
    （http/https 白名单 + 浏览器 UA），登记时已校验过 scheme。"""
    from ..sources.subscription import fetch_subscription_text as f
    return f(entry["address"])


def fetch_feed_bytes(entry) -> bytes:
    """feed 地址 → 原始字节（官方 CSV 是 UTF-16/BOM 系，交给
    decode_feed_bytes 认 BOM，别在这里猜编码）。"""
    req = urllib.request.Request(entry["address"], headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=20.0) as resp:
        return resp.read()


def _default_fetch(entry):
    """按类型路由的默认拉取（真联网）。仅真实调用方触达；
    update_entry 的 fetch_fn 参数是测试与定制注入点。"""
    if entry["type"] == "subscription":
        return fetch_subscription_text(entry)
    return fetch_feed_bytes(entry)


def _fetch_raw(entry, fetch_fn):
    """① 拉取。返回 (原始内容, feed_from)；feed 类官方失败回退镜像
    （仅当地址是官方默认——登记了私有地址的尊重登记，不回退）。"""
    fetch = fetch_fn or _default_fetch
    if entry["type"] == "subscription":
        return fetch(entry), ""
    try:
        return fetch(entry), "登记地址"
    except Exception as first_exc:
        if (entry["type"] in ("feed", "feed_mirror")
                and entry["address"] == FEED_DEFAULT_ADDRESS):
            try:
                mirror_entry = dict(entry, address=FEED_FALLBACK_ADDRESS)
                return fetch(mirror_entry), "镜像"
            except Exception:
                raise first_exc from None   # 镜像也挂 → 报主源（官方）错误
        raise


def _parse_raw(entry, raw):
    """② 解析（纯本地）。返回 (nodes, skipped, parser 名)。坏内容显式抛
    （RuntimeError/ValueError），由 update_entry 拦下走失败记账，库不动。"""
    feed = _load_feed_module()
    if entry["type"] in ("feed", "feed_mirror"):
        if isinstance(raw, bytes):
            raw = feed.decode_feed_bytes(raw)
        # source 即归属（=ENTRY_PAGE[type_]）：feed 落 VPNGate 合并页
        # （task8/task23 落页后唯一 feed 类入口；镜像类型已废除，旧条目
        # 由 sources/landing.py 启动迁移转正/删除）。同一解析器、同一个
        # source 参数，落页口径在 make_node_id 里收口。
        nodes = feed.parse_feed_to_nodes(raw, source=entry["page"])
        return nodes, 0, "feed.parse_feed_to_nodes"
    fi = _load_format_import()
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", "replace")
    res = fi.import_any_text(raw, source=entry["page"],
                             subscription_url=entry["address"],
                             subscription_name=entry.get("name") or "")
    return res.nodes, res.skipped, "format_import.import_any_text"


# ---------------------------------------------------------------------------
# 合并（§5 逐字段口径）
# ---------------------------------------------------------------------------
def merge_fresh(old, fresh, entry_id) -> Node:
    """按身份合并：来源侧字段以新拉取为准，本地侧状态必须保住。
    old=None（新身份）→ 只打登记戳。返回合并后的 Node，不落库。
    消失标记在此清除（回归口径）——样本自始至终没人碰过，节点连续性完整。"""
    fresh.extra["registry_entry"] = entry_id
    if old is None:
        return fresh
    # 人工标记（store.upsert_node 本也会保，显式带上，口径自洽不靠巧合）
    fresh.pinned = old.pinned
    fresh.banned = old.banned
    # 出口画像只来自本地验真（verify.py:268），来源解析永远没有：
    # 旧节点验过（有 ip/verified_at）就整体沿用，别让刷新洗掉。
    if old.exit_profile.ip or old.exit_profile.verified_at:
        fresh.exit_profile = old.exit_profile
    # 实测延迟/抖动快照（verify.py:271-273）：来源不报（None）时保留本地值。
    # 同 R6：None ≠ 0，绝不用来源的 0 冒充实测。
    if fresh.metrics.latency_ms is None:
        fresh.metrics.latency_ms = old.metrics.latency_ms
    if fresh.metrics.jitter_ms is None:
        fresh.metrics.jitter_ms = old.metrics.jitter_ms
    # 出口改名（verify.py:323）：extra.orig_name 存在 = 现名是实测出口名，
    # 刷新不把名字打回来源原名（原名仍由 orig_name 保着）。
    if old.extra.get("orig_name"):
        fresh.name = old.name
    # 本地 extra 白名单带回（§5 表）。merged_superseded 随白名单存活：
    # task8 迁移快照（work8/DESIGN §3.9）经刷新后原样保留，快照里的
    # 备选配置/败值历史不丢（work8/DESIGN §8 集成硬要求第 3 条）。
    for key in _LOCAL_EXTRA_KEYS:
        if key in old.extra:
            fresh.extra[key] = old.extra[key]
    # 回归：来源里重新出现 → 清掉消失标记
    for key in _GONE_KEYS:
        fresh.extra.pop(key, None)
    # 场景标签并集（流水线来源用；本原型三来源恒空，保通用不丢信息）
    fresh.scenes = set(old.scenes) | set(fresh.scenes)
    return fresh


# ---------------------------------------------------------------------------
# 更新报告
# ---------------------------------------------------------------------------
@dataclass
class UpdateReport:
    """一次一键更新的结果（成功与失败同一形状，UI 一套展示逻辑）。"""
    entry_id: str
    ok: bool
    error: str = ""          # 失败时 "异常类型: 消息"，成功为空
    fetched: int = 0         # 解析出的节点数（批内去重后）
    added: int = 0           # 新身份入库
    updated: int = 0         # 已有身份按 §5 合并刷新
    gone: int = 0            # 归属范围内这次没拉到 → 标记（不删）
    returned: int = 0        # 之前标过消失、这次又拉到 → 标记清除
    skipped: int = 0         # 订阅坏行数（feed 无此概念，坏配置门口拦下）
    parser: str = ""         # 实际用的解析器（诊断用）
    feed_from: str = ""      # feed 类：字节来自"登记地址"还是"镜像"
    ts: float = 0.0

    def summary(self) -> str:
        if not self.ok:
            return f"更新失败：{self.error}"
        bits = [f"新增 {self.added}", f"更新 {self.updated}", f"消失 {self.gone}"]
        if self.returned:
            bits.append(f"回归 {self.returned}")
        if self.skipped:
            bits.append(f"跳过坏行 {self.skipped}")
        return "，".join(bits)


# ---------------------------------------------------------------------------
# 登记表
# ---------------------------------------------------------------------------
class SourceRegistry:
    """来源登记表：增删查 + last_update 记账。

    原型期持久化为独立 JSON 文件（临时文件 + os.replace 原子替换）；
    path=None 为内存态（测试用）。集成落位：折进 core/store.py 成
    source_registry 表，与 nodes 共用 RLock，事务性随 Store 白得
    （DESIGN.md §3.3）。本原型单线程使用；跨线程集成时靠 Store 的锁序列化。
    """

    def __init__(self, path: str = None):
        self.path = path
        self._entries = {}        # id -> entry dict
        self._last = {}           # id -> last_update dict
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            self._entries = {e["id"]: e for e in data.get("entries", [])}
            self._last = dict(data.get("last_updates", {}))

    def _save(self):
        if not self.path:
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1,
                       "entries": sorted(self._entries.values(),
                                         key=lambda e: e["id"]),
                       "last_updates": self._last},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    # ---------------- 增删查 ----------------
    def register(self, type_: str, address: str = "", name: str = "",
                 enabled: bool = True, now: float = None) -> dict:
        """登记一个可续期来源。feed 地址可留空（自动填官方默认）；
        subscription 必须给 http/https 地址。返回登记条目副本。
        （feed_mirror 类型已废除——task8/task23 落页，work8/DESIGN §5.1；
        传它会在这里显式报错。）"""
        if type_ not in ENTRY_TYPES:
            raise ValueError(f"未知来源类型: {type_}（可选 {ENTRY_TYPES}）")
        address = (address or "").strip()
        name = (name or "").strip()
        if type_ == "subscription":
            scheme = address.split("://", 1)[0].lower() if "://" in address else ""
            if scheme not in ("http", "https"):
                raise ValueError("订阅地址只允许 http/https（登记制存的就是它）")
            entry_id = "sub-" + hashlib.sha1(
                address.encode("utf-8")).hexdigest()[:8]
            if entry_id in self._entries:
                raise ValueError("该订阅地址已登记过（同地址不重复登记）")
        else:
            if any(e["type"] == type_ for e in self._entries.values()):
                raise ValueError(f"{type_} 是单例来源，已登记，不可重复")
            if address:
                scheme = address.split("://", 1)[0].lower() if "://" in address else ""
                if scheme not in ("http", "https"):
                    raise ValueError("feed 地址只允许 http/https")
            else:
                address = FEED_DEFAULT_ADDRESS
            entry_id = "feed"                # feed 类唯一条目 id（不变）
        entry = {
            "id": entry_id, "type": type_, "page": ENTRY_PAGE[type_],
            "address": address, "name": name, "enabled": bool(enabled),
            "created_at": now if now is not None else time.time(),
        }
        self._entries[entry_id] = entry
        self._save()
        return dict(entry)

    def remove(self, entry_id: str) -> bool:
        """删除登记：只删条目，**节点原地不动**（样本/置顶/拉黑/消失标记
        都在节点上，DESIGN §8）；此后该来源无人刷新，节点成为孤儿，
        extra 溯源字段仍在。返回是否删到。"""
        if entry_id in self._entries:
            del self._entries[entry_id]
            self._last.pop(entry_id, None)
            self._save()
            return True
        return False

    def set_enabled(self, entry_id: str, enabled: bool) -> None:
        entry = self._entries.get(entry_id)
        if entry is None:
            raise ValueError(f"未登记的来源: {entry_id}")
        entry["enabled"] = bool(enabled)
        self._save()

    def entries(self) -> list:
        return [dict(e) for e in self._entries.values()]

    def get(self, entry_id: str):
        e = self._entries.get(entry_id)
        return dict(e) if e else None

    def last_update(self, entry_id: str):
        """上次更新结果（含上次时间与结果计数）；从未更新过返回 None。"""
        lu = self._last.get(entry_id)
        return dict(lu) if lu else None

    def _record(self, entry_id: str, payload: dict) -> None:
        self._last[entry_id] = payload
        self._save()

    # ---------------- 归属范围（§4.2 三条规则） ----------------
    def owned_nodes(self, store, entry) -> list:
        """这个登记条目「管得到」的库内节点（消失判定只作用于这个范围）。
        含拉黑节点——banned 节点也是库内真实记录，源里没了照样标消失。
        - feed：只认登记戳（task23 落页：合并页混着手导 .ovpn——含第三方
          服务器——没戳的绝不碰，work8/DESIGN §5.2。合并前 feed 是「整页
          归属」、其页内节点全是 feed 解析器所产；启动迁移已给全部 feed 系
          存量补戳 extra.registry_entry="feed"，归属集合与合并前等价，
          「手导永不标消失」保护原样保留）
        - subscription：登记戳 或 subscription_url 溯源（收编登记制前的老节点）
        """
        nodes = store.list_nodes(source=entry["page"], include_banned=True)
        out = []
        for n in nodes:
            if n.extra.get("registry_entry") == entry["id"]:
                out.append(n)
            elif (entry["type"] == "subscription"
                  and n.extra.get("subscription_url") == entry["address"]):
                out.append(n)
        return out

    def gone_nodes(self, store, entry_id: str) -> list:
        """当前带消失标记的归属节点（UI「已消失」徽标 / 清理确认用）。"""
        entry = self.get(entry_id)
        if entry is None:
            raise ValueError(f"未登记的来源: {entry_id}")
        return [n for n in self.owned_nodes(store, entry)
                if n.extra.get("source_gone") == "1"]


# ---------------------------------------------------------------------------
# 一键更新（§4 总流程）
# ---------------------------------------------------------------------------
def update_entry(registry: SourceRegistry, store, entry_id: str,
                 fetch_fn=None, now: float = None) -> UpdateReport:
    """一键更新一条登记来源：拉取 → 解析 → 按身份合并 → 报告。

    fetch_fn(entry_dict) -> str|bytes 是拉取注入点：**测试必须注入假拉取
    函数**（本原型零联网红线）；传 None 才走真实拉取（真实调用方专用）。
    全量或不动：拉取与解析全部完成之前不写任何一个节点，任何失败走
    失败记账（last_update.ok=False），库一字节不动（DESIGN §4.3）。"""
    entry = registry.get(entry_id)
    if entry is None:
        raise ValueError(f"未登记的来源: {entry_id}")
    if not entry["enabled"]:
        raise ValueError(f"来源已停用: {entry_id}（先 set_enabled 再更新）")
    ts = now if now is not None else time.time()

    def fail(exc: Exception) -> UpdateReport:
        text = f"{type(exc).__name__}: {exc}"
        registry._record(entry_id, {"ts": ts, "ok": False, "error": text})
        return UpdateReport(entry_id=entry_id, ok=False, error=text, ts=ts)

    # ① 拉取（feed 类官方失败回退镜像，§6）
    try:
        raw, feed_from = _fetch_raw(entry, fetch_fn)
    except Exception as exc:
        return fail(exc)

    # ② 解析（纯本地；坏内容在门口拦下，绝不写库）。解析"成功"却 0 节点
    # 也算失败：多半是上游格式漂移让解析器空转，或源真被清空——两种情形
    # 都不该触发一轮"全部标消失"（保守口径，库不动，失败记账给人看）。
    try:
        nodes, skipped, parser = _parse_raw(entry, raw)
    except Exception as exc:
        return fail(exc)
    if not nodes:
        return fail(RuntimeError("解析出 0 个节点（源被清空或上游格式漂移），"
                                 "保守起见库不动"))

    # ③ 按身份合并：先算全量差异，再统一落库（§4.3）。
    # 归属范围（owned）决定**消失判定**；但**合并**要认全库同身份节点——
    # 例如手导 .ovpn 与 feed 节点同端点同 id（task8/task23 落页后同一
    # canonical 键），手导的没登记戳、不在归属范围里，却必须按 §5 合并
    # 保住验真状态，而不是被 merge_fresh(None,…) 当新节点盖掉。
    old_by_id = {n.id: n for n in registry.owned_nodes(store, entry)}
    for n in nodes:
        if n.id not in old_by_id:
            dbn = store.get_node(n.id)
            if dbn is not None:
                old_by_id[n.id] = dbn
    fetched_ids = {n.id for n in nodes}
    added = [n for n in nodes if n.id not in old_by_id]
    updated = [n for n in nodes if n.id in old_by_id]
    gone = [old_by_id[i] for i in old_by_id if i not in fetched_ids]
    returned = [old_by_id[n.id] for n in updated
                if old_by_id[n.id].extra.get("source_gone")]

    for n in added:
        store.upsert_node(merge_fresh(None, n, entry_id))
    for n in updated:
        store.upsert_node(merge_fresh(old_by_id[n.id], n, entry_id))
    for old in gone:
        old.extra["source_gone"] = "1"
        old.extra["source_gone_at"] = ts
        old.extra["source_gone_from"] = entry_id
        store.upsert_node(old)

    # ④ 报告 + 记账
    report = UpdateReport(
        entry_id=entry_id, ok=True, fetched=len(nodes), added=len(added),
        updated=len(updated), gone=len(gone), returned=len(returned),
        skipped=skipped, parser=parser, feed_from=feed_from, ts=ts)
    registry._record(entry_id, {
        "ts": ts, "ok": True, "error": "", "fetched": report.fetched,
        "added": report.added, "updated": report.updated,
        "gone": report.gone, "returned": report.returned,
        "skipped": report.skipped, "parser": parser, "feed_from": feed_from,
    })
    return report


def update_all(registry: SourceRegistry, store, fetch_fn=None,
               now: float = None) -> list:
    """一键全更：逐条更新所有启用条目，条与条独立（一条失败不影响其余，
    报告逐条返回）。停用条目跳过。「定时更新」不在这里——见 DESIGN §9，
    原型不做任何自动联网，定时器留给集成期，复用本函数即可。"""
    return [update_entry(registry, store, e["id"], fetch_fn=fetch_fn, now=now)
            for e in registry.entries() if e["enabled"]]
