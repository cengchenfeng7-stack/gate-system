#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
免费节点搜集原型（task9 / collector.py，阶段四·产品化 第三段）
================================================================
核心循环第一环「自己出去找源」：种子健康账本 + 候选地址抽取 +
一轮搜集编排。设计全文见同目录 DESIGN.md；本模块只做搜集段
（DESIGN §3①–⑤：拉种子 → 抽候选 → 逐个拉取 → 候选池），
⑥验真与⑦入库是集成段把池子喂给既有 client-dev 流水线——
候选未经既有验真流水线，绝不进正式库（主人「只认验真出口」
铁律在搜集段的落点，DESIGN §0 红线 1）。

- 只用标准库；不依赖 PySide6、不 import client 包；
- 网络与解析全部可注入：fetch_fn(url)->str / parse_fn(text)->list，
  测试一律注入假函数，本模块自身不发任何网络请求；
- 源随时会死是常态：任何单点失败都吞进整轮报告，整轮永不中断，
  零产出轮如实报告（DESIGN §4）；
- 铁律边界：不含任何测速/优选/自动切换逻辑（DESIGN §0 红线 4）。
"""
from __future__ import annotations

import html
import json
import os
import re
import time
import uuid
from urllib.parse import urlsplit

# ---------------- 健康账本参数（DESIGN §2.2，逐条对应） ----------------
LEDGER_VERSION = 1
W_SUCCESS = 0.2            # R1/R5：正常成功恢复一档
W_FAIL = 0.25              # R3：失败降权一档
W_ZERO = 0.2               # R2：连续零产出计满后的降权幅度
FAIL_DISABLE_STREAK = 3    # R4：连续失败停用线
ZERO_DISABLE_STREAK = 3    # R2：连续「活而空」计数线（计满降权一次并清零重计）
PROBATION_COOLDOWN_S = 7 * 24 * 3600   # R6：停用种子的缓刑复测冷却
MANUAL_DISABLE_REASON = "手动停用"      # R7：手动停用不复测、不被自动复用


# ---------------------------------------------------------------------------
# 健康账本（DESIGN §2：落盘口径同 SourceRegistry——tmp + os.replace 原子写）
# ---------------------------------------------------------------------------
def new_ledger() -> dict:
    """空账本。结构：{"version": 1, "seeds": {seed_id: 记录}}。"""
    return {"version": LEDGER_VERSION, "seeds": {}}


def load_ledger(path: str) -> dict:
    """读账本：文件不存在 → 空账本（首轮正常启动）；JSON 损坏或版本
    不符 → 显式 ValueError——绝不静默当空账本用，那会把停用状态洗掉。"""
    if not os.path.exists(path):
        return new_ledger()
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError as exc:
            raise ValueError(f"健康账本损坏（{path}）：{exc}") from None
    if (not isinstance(data, dict) or data.get("version") != LEDGER_VERSION
            or not isinstance(data.get("seeds"), dict)):
        raise ValueError(f"健康账本格式不符（{path}）："
                         f"version 须为 {LEDGER_VERSION} 且 seeds 为对象")
    return data


def save_ledger(path: str, ledger: dict) -> None:
    """写账本：种子按 id 排序，临时文件 + os.replace 原子替换，
    杜绝半截 JSON（口径同 source_registry.SourceRegistry._save）。"""
    data = {"version": LEDGER_VERSION,
            "seeds": {sid: ledger["seeds"][sid]
                      for sid in sorted(ledger.get("seeds", {}))}}
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _seed_record(ledger: dict, seed_id: str, now: float) -> dict:
    """取种子记录，没有则按 R0 建档（weight=1.0、enabled、计数全零）。"""
    rec = ledger["seeds"].get(seed_id)
    if rec is None:
        rec = {"weight": 1.0, "enabled": True, "disabled_reason": "",
               "ok_count": 0, "fail_count": 0, "produced_total": 0,
               "consecutive_failures": 0, "zero_streak": 0,
               "last_ok_at": None, "last_fail_at": None,
               "recovered_at": None, "created_at": now, "updated_at": now}
        ledger["seeds"][seed_id] = rec
    return rec


def record_result(ledger: dict, seed_id: str, ok: bool, produced,
                  now: float = None) -> dict:
    """按 DESIGN §2.2 R1–R8 记一轮成败，更新权重与停用状态。
    - ok=True 且 produced>0：R1 正常成功（权重回升；停用的自动种子按
      R6 立即复用——复测成功且真有货才算复活）；
    - ok=True 且 produced==0：R2 活而空（零产出不进连续失败）；
    - ok=False：R3 失败（降权 + 连续失败计数，R4 计满停用）；
    - produced 口径（R8）：该种子本轮抽出且首次出现的候选地址数；
      候选后续死活不算种子成败，不在这里记。
    返回该种子记录的副本（防调用方误改账本内部）。"""
    ts = time.time() if now is None else float(now)
    rec = _seed_record(ledger, seed_id, ts)
    rec["updated_at"] = ts
    if ok:
        rec["ok_count"] += 1
        rec["last_ok_at"] = ts
        rec["consecutive_failures"] = 0
        if produced and int(produced) > 0:
            rec["produced_total"] += int(produced)
            rec["zero_streak"] = 0
            # 权重每次落值都取整到 4 位小数：0.2/0.25 的浮点累加会留下
            # 5.5e-17 这类尾数，把「权重耗尽」的 <=0 判定卡死
            rec["weight"] = min(1.0, round(rec["weight"] + W_SUCCESS, 4))
            # R6：停用的自动种子复测成功且产出>0 → 复用；手动停用不动（R7）
            if (not rec["enabled"]
                    and rec["disabled_reason"] != MANUAL_DISABLE_REASON):
                rec["enabled"] = True
                rec["disabled_reason"] = ""
                rec["recovered_at"] = ts
        else:
            rec["zero_streak"] += 1
            if rec["zero_streak"] >= ZERO_DISABLE_STREAK:
                rec["zero_streak"] = 0
                rec["weight"] = round(max(0.0, rec["weight"] - W_ZERO), 4)
                if rec["weight"] <= 0.0 and rec["enabled"]:
                    rec["enabled"] = False
                    if rec["disabled_reason"] != MANUAL_DISABLE_REASON:
                        rec["disabled_reason"] = "权重耗尽（连续零产出）"
    else:
        rec["fail_count"] += 1
        rec["last_fail_at"] = ts
        rec["consecutive_failures"] += 1
        rec["weight"] = round(max(0.0, rec["weight"] - W_FAIL), 4)
        if rec["enabled"]:
            if rec["consecutive_failures"] >= FAIL_DISABLE_STREAK:
                rec["enabled"] = False
                if rec["disabled_reason"] != MANUAL_DISABLE_REASON:
                    rec["disabled_reason"] = (
                        f"连续失败 {rec['consecutive_failures']} 次")
            elif rec["weight"] <= 0.0:
                rec["enabled"] = False
                if rec["disabled_reason"] != MANUAL_DISABLE_REASON:
                    rec["disabled_reason"] = "权重耗尽"
    return dict(rec)


def pick_probation(ledger: dict, now: float = None):
    """R6：从停用种子里挑本轮缓刑复测对象——冷却满 7 天、取
    last_fail_at 最旧（最久没试过）者；手动停用的不复测（R7）。
    没有合适对象返回 None。每轮至多复测一个（调用方遵守）。"""
    ts = time.time() if now is None else float(now)
    best_key, best_id = None, None
    for sid, rec in ledger.get("seeds", {}).items():
        if rec.get("enabled", True):
            continue
        if rec.get("disabled_reason") == MANUAL_DISABLE_REASON:
            continue
        last_fail = rec.get("last_fail_at") or 0.0
        if ts - last_fail < PROBATION_COOLDOWN_S:
            continue
        key = (last_fail, sid)          # 最旧优先；同刻按 id 稳定排序
        if best_key is None or key < best_key:
            best_key, best_id = key, sid
    return best_id


# ---------------------------------------------------------------------------
# 候选地址抽取（DESIGN §3②：markdown 链接 / html href / 纯文本 URL）
# ---------------------------------------------------------------------------
# markdown 链接：[任意文字](URL)；URL 里不含空白与右括号（嵌套括号的
# markdown 少见，抽不到就让它死在位置更靠后的纯文本形态兜底）
_MD_RE = re.compile(r"\[[^\]]*\]\(\s*(https?://[^)\s]+?)\s*\)", re.I)
# html href：href='URL' / href="URL"；值里的 &amp; 等实体还原成真字符
_HREF_RE = re.compile(r"""href\s*=\s*(["'])(https?://[^"']+)\1""", re.I)
# 纯文本 URL：到空白/引号/尖括号/反引号为止（markdown、href 里的地址
# 也会被它扫到，归一化后与对应形态同串，去重自然合并）
_PLAIN_RE = re.compile(r"""https?://[^\s"'<>`]+""", re.I)

# 纯文本里挂在地址尾部的标点（中英文常用集合）；右括号单独按配平剥
_TRAIL_PUNCT = ".,;:!?、。，；：！？》」』】\"'"


def _normalize_candidate(url: str) -> str:
    """候选地址归一：剥两端引号与尾部标点；尾部右括号仅在配不平时剥
    （防句子括号与 markdown 括号残尾，不伤合法的括号路径）。"""
    url = url.strip().strip("\"'")
    while url and url[-1] in _TRAIL_PUNCT:
        url = url[:-1]
    while url.endswith(")") and url.count("(") < url.count(")"):
        url = url[:-1]
    return url


def extract_candidates(text: str) -> list:
    """聚合文本 → 候选订阅/节点清单地址列表。
    - 三形态全扫：markdown 链接、html href、纯文本 URL；
    - 只收 http/https（可 fetch 的地址；节点分享链接 vless:// 等不是
      「要再拉取的地址」，不抽——DESIGN §3②）；
    - 按出现位置去重保序（先出现者胜，口径同批内去重惯例）。
    纯函数：不抛异常（输入非 str 视为无候选）。"""
    if not isinstance(text, str):
        return []
    hits = []                       # (位置, 扫描顺序, 原文)
    for m in _MD_RE.finditer(text):
        hits.append((m.start(), 0, m.group(1)))
    for m in _HREF_RE.finditer(text):
        hits.append((m.start(), 1, html.unescape(m.group(2))))
    for m in _PLAIN_RE.finditer(text):
        hits.append((m.start(), 2, m.group(0)))
    hits.sort(key=lambda h: (h[0], h[1]))

    out, seen = [], set()
    for _pos, _ord, raw in hits:
        url = _normalize_candidate(raw)
        if not url or not url.lower().startswith(("http://", "https://")):
            continue
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
            continue
        if url not in seen:
            seen.add(url)
            out.append(url)
    return out


# ---------------------------------------------------------------------------
# 一轮搜集（DESIGN §3①–⑤ + §4 失效耐受）
# ---------------------------------------------------------------------------
def _err_text(exc: Exception) -> str:
    """异常归一成「异常类型: 消息」（口径同 source_registry 失败记账）。"""
    return f"{type(exc).__name__}: {exc}"


def _identity_of(item):
    """池内条目的身份键（口径复用登记制 identity = node.id，先出现者胜）。
    - dict / 对象带 id 字段 → ("id", str(id))——集成后即 make_node_id 产物；
    - 没有身份的条目退化为内容指纹（本轮内去重兜底，不冒充 node.id）。"""
    if isinstance(item, dict):
        iid = item.get("id")
    else:
        iid = getattr(item, "id", None)
    if iid is not None:
        return ("id", str(iid))
    try:
        return ("raw", json.dumps(item, ensure_ascii=False, sort_keys=True,
                                  default=str))
    except (TypeError, ValueError):
        return ("raw", repr(item))


def _stamp(item, kv: dict) -> None:
    """给池内条目打溯源标记（DESIGN §3.3 四键）：dict 打进 item["extra"]，
    对象打进 item.extra；两者都不是就放着不动——不为了盖章改条目形状。"""
    if isinstance(item, dict):
        ex = item.get("extra")
        if not isinstance(ex, dict):
            ex = {}
            item["extra"] = ex
        ex.update(kv)
        return
    ex = getattr(item, "extra", None)
    if isinstance(ex, dict):
        ex.update(kv)


def _summary(report: dict) -> str:
    """一句话总结；零产出轮必须落在「产出 0」上，不假装有收获（§4.2）。"""
    bits = [
        f"种子 {report['seeds_total']}：成功 {report['seeds_ok']}、"
        f"失败 {report['seeds_failed']}、停用跳过 {report['seeds_skipped']}",
        f"候选 {report['candidates_total']}（去重 {report['candidates_unique']}"
        f"、死 {report['candidates_dead']}）",
        f"节点池 {report['nodes_unique']}（验真与入库：既有流水线，集成段）",
    ]
    if report["zero_yield"]:
        bits.append("本轮产出 0：如实报告，无收获，不进正式库"
                    "（逐种子成败见 seeds 数组）")
    return "；".join(bits)


def run_collection(seeds, fetch_fn, parse_fn, ledger=None, now=None) -> dict:
    """跑一轮搜集（DESIGN §3①–⑤）：拉种子 → 抽候选 → 逐个拉取 →
    解析替身 → 候选池（身份去重）。

    - seeds：种子描述 dict 列表，必填 id/address，可选 category/enabled
      （有账本时账本 enabled 优先）；结构错误当场 ValueError——调用方
      bug 不许伪装成源失败；网络与解析的单点失败才全吞（§4.1）。
    - fetch_fn(url)->str：可抛任意异常；parse_fn(text)->list：可抛解析
      错，空列表/非列表也算死候选。
    - ledger：给了就用 pick_probation 挑缓刑复测（每轮至多 1 个）、
      停用种子默认跳过、逐种子 record_result；不给就纯跑一轮。
    - 返回整轮报告（§4.2），任何单点失败都被吞进报告、不向上抛。"""
    ts = time.time() if now is None else float(now)
    round_id = ("R" + time.strftime("%Y%m%dT%H%M%SZ", time.gmtime(ts))
                + "-" + uuid.uuid4().hex[:6])
    if not isinstance(seeds, (list, tuple)):
        raise ValueError("seeds 必须是种子描述列表")
    seeds = list(seeds)
    for s in seeds:                       # 结构校验：当场报（§4.1）
        if (not isinstance(s, dict) or not s.get("id")
                or not s.get("address")):
            raise ValueError("每个种子须是含 id 与 address 的 dict "
                             "（结构错误当场报，不属于运行期单点失败）")

    probation_id = (pick_probation(ledger, now=ts)
                    if ledger is not None else None)

    report = {
        "version": 1, "ts": ts, "round_id": round_id,
        "seeds_total": len(seeds),
        "seeds": [],
        "seeds_ok": 0, "seeds_failed": 0, "seeds_skipped": 0,
        "candidates_total": 0, "candidates_unique": 0,
        "candidates_dead": 0, "dead_candidates": [],
        "nodes_total": 0, "nodes_unique": 0,
        "pool": [],
        "zero_yield": False, "summary": "",
    }
    seen_candidates = {}      # 归一地址 → 首个发现它的 seed_id（R8 计功口径）
    pool_keys = set()

    for seed in seeds:
        sid = str(seed["id"])
        category = str(seed.get("category") or "unknown")
        row = {"seed_id": sid, "category": category, "ok": False,
               "error": "", "candidates": 0, "nodes": 0,
               "skipped_disabled": False, "probation": False}
        report["seeds"].append(row)

        # 停用判定：账本优先；无账本看 seed["enabled"]（默认 true）
        enabled = bool(seed.get("enabled", True))
        if ledger is not None:
            rec = ledger["seeds"].get(sid)
            if rec is not None:
                enabled = bool(rec.get("enabled", True))
        if not enabled:
            if sid == probation_id:       # R6：本轮唯一缓刑复测对象
                row["probation"] = True
            else:
                row["skipped_disabled"] = True
                report["seeds_skipped"] += 1
                continue

        # ① 拉种子内容（失败 → R3 记账，跳过该种子，整轮不断）
        try:
            text = fetch_fn(seed["address"])
            if not isinstance(text, str):
                raise TypeError(f"fetch_fn 返回 {type(text).__name__}，须为 str")
        except Exception as exc:
            row["error"] = _err_text(exc)
            report["seeds_failed"] += 1
            if ledger is not None:
                record_result(ledger, sid, ok=False, produced=0, now=ts)
            continue

        # ② 抽候选 + 跨种子去重（首次发现者计功，R8）
        candidates = extract_candidates(text)
        row["candidates"] = len(candidates)
        report["candidates_total"] += len(candidates)
        fresh = []
        for url in candidates:
            if url not in seen_candidates:
                seen_candidates[url] = sid
                fresh.append(url)
        if not fresh:
            # 拉取成功但零候选：R2「活而空」，不算失败
            row["ok"] = True
            report["seeds_ok"] += 1
            if ledger is not None:
                record_result(ledger, sid, ok=True, produced=0, now=ts)
            continue

        # ③④ 逐个拉候选 + 解析替身（死链/垃圾 → dead_candidates，继续）
        seed_nodes = 0
        for url in fresh:
            try:
                ctext = fetch_fn(url)
                if not isinstance(ctext, str):
                    raise TypeError(f"fetch_fn 返回 {type(ctext).__name__}，"
                                    "须为 str")
            except Exception as exc:
                report["dead_candidates"].append(
                    {"url": url, "seed_id": sid, "stage": "fetch",
                     "error": _err_text(exc)})
                continue
            try:
                items = parse_fn(ctext)
                if not isinstance(items, list):
                    raise TypeError(f"parse_fn 返回 {type(items).__name__}，"
                                    "须为 list")
                if not items:
                    raise ValueError("解析出 0 条（垃圾内容或上游格式漂移）")
            except Exception as exc:
                report["dead_candidates"].append(
                    {"url": url, "seed_id": sid, "stage": "parse",
                     "error": _err_text(exc)})
                continue

            # ⑤ 候选池 + 身份去重（本轮口径：先出现者胜）+ 溯源标记
            report["nodes_total"] += len(items)
            for it in items:
                _stamp(it, {"collect_seed_id": sid,
                            "collect_seed_category": category,
                            "collect_round": round_id,
                            "collected_from": url})
                key = _identity_of(it)
                if key in pool_keys:
                    continue
                pool_keys.add(key)
                report["pool"].append(it)
                seed_nodes += 1
        row["nodes"] = seed_nodes
        row["ok"] = True
        report["seeds_ok"] += 1
        if ledger is not None:
            # produced = 首次发现的候选数（R8）；候选死活不算种子成败
            record_result(ledger, sid, ok=True, produced=len(fresh), now=ts)

    report["candidates_unique"] = len(seen_candidates)
    report["candidates_dead"] = len(report["dead_candidates"])
    report["nodes_unique"] = len(report["pool"])
    report["zero_yield"] = report["nodes_unique"] == 0
    report["summary"] = _summary(report)
    return report
