#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
名单复查机制（core/list_recheck.py，task28 · 白/黑名单复查）
=============================================================
口径出处（本模块只实现机制，不重裁决）：
- 主人 2026-11-22 04:29 定：黑名单与白名单都须每几天复查探查一次
  （状态会变，不是一次定终身）。
- work10/DESIGN.md §6.3（周期/触发/范围定稿）、§6.4（处置推荐口径）。

设计红线（写作时冻结，改动前先读）：
- 纯标准库、不 import client 包（store/settings/node 全部鸭型注入）、
  零网络（探针由调用方经 verify_fn 注入）、零定时器（调度由调用方注入）。
- 白名单 = 派生视图（ExitProfile.is_clean is True 且未被隔离），不落
  存储；复查只经既有验真流水线刷新画像，视图随之自动更新，本模块
  不回写任何名单本体。
- **不自动拉黑、不自动放回**——work10 §6.1/§6.4 待主人点头项 A/B/C
  不在本段裁决，本模块没有这两条分支；banned 恢复可连只记提示，
  人工确认才解除（store._upsert_node_impl 保 pin/ban 的既有口径）。
- 不复刻 verify.py 的判定/写回逻辑（classify_network / pollution_of /
  _apply_quarantine 行为冻结，复用不改写）：隔离挂牌与摘牌由既有
  apply_outcome → _apply_quarantine 完成，本模块只读隔离标记的前后
  变化做处置归类。

verify_fn 合同（调用方注入；run_recheck 内固定以 verify_fn(node) 调用）：
- 跑既有单节点验真流水线：探针（接线方按 _test_node 同款「临时独立端口
  引擎 + make_real_probe」口径对齐既有调用点）→ verify_exit → 成功则
  apply_outcome 写回画像并 upsert；返回 VerifyOutcome 形态对象（至少
  .ok/.reason；.latency_ms/.jitter_ms 可选，缺席时样本对应列留空）。
- 本模块不复刻探针/判定/写回的任何一步，只按返回结果与节点前后状态
  做处置归类。接线方粘合（ui/cli.py / ui/app.py）均复用既有管道，
  不新造探测。

复查日志：每个实测节点落一条 verify_samples（kind="recheck"，既有自由
文本口径，不动表结构；detail 写处置明细如「变脏移出白名单 / 隔离摘牌 /
复查失败」）。样本经 store.add_sample 鸭型写入（字段集与
store.VerifySample 一致，见 _RecheckSample）。OpenVPN skip 与计划后
不在库的节点不记样本（不记假样本，与 _test_node 同口径）。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

# ---------------------------------------------------------------------------
# 周期口径（work10 §6.3 定稿：默认 3 天，可调 2–7；越界显式报错不夹取）
# ---------------------------------------------------------------------------
RECHECK_DAYS_DEFAULT = 3
RECHECK_DAYS_MIN = 2
RECHECK_DAYS_MAX = 7
SECONDS_PER_DAY = 86400

SCOPE_WHITELIST = "whitelist"      # 白名单派生视图内（干净且未被隔离）
SCOPE_QUARANTINE = "quarantine"    # 隔离区（quarantined 标记在）

# 处置 token（report item["disposition"] / counts 键；呈现方据此渲染）
DISP_SKIP = "skip"                    # OpenVPN 隧道模式未连接测不了，如实跳过
DISP_MISSING = "missing"              # 计划后节点已不在库（并发删除等），跳过
DISP_PASSED = "passed"                # 复查通过，仍在白名单视图
DISP_DIRTY_OUT = "dirty_out"          # 复查变脏，移出白名单视图（不自动拉黑）
DISP_VIEW_LOST = "view_lost"          # 复查后干净度未知，移出白名单视图（不自动拉黑）
DISP_FAILED = "recheck_failed"        # 链路死/引擎起不来：画像不动，视图保留
DISP_QCLEARED = "quarantine_cleared"  # 隔离恢复干净，自动摘牌（既有口径）
DISP_QKEPT = "still_quarantined"      # 复查仍污染，隔离标记保留
DISP_BANNED_OK = "banned_recoverable"  # 黑名单节点复查可连：只提示，不自动放回

DISPOSITIONS = (DISP_SKIP, DISP_MISSING, DISP_PASSED, DISP_DIRTY_OUT,
                DISP_VIEW_LOST, DISP_FAILED, DISP_QCLEARED, DISP_QKEPT,
                DISP_BANNED_OK)


def validate_recheck_days(value) -> int:
    """list_recheck_days 校验（唯一事实源；settings_proto 与本模块同用
    一份，不另造镜像）：int（bool 拒收）且 RECHECK_DAYS_MIN..MAX。
    越界/非法显式报错，不静默夹取。合法时原值返回。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"名单复查周期 list_recheck_days 必须是整数"
                         f"（bool 不算），得到 {value!r}")
    if not RECHECK_DAYS_MIN <= value <= RECHECK_DAYS_MAX:
        raise ValueError(f"名单复查周期 list_recheck_days 越界"
                         f"（合法区间 {RECHECK_DAYS_MIN}..{RECHECK_DAYS_MAX} 天），"
                         f"得到 {value!r}")
    return value


def _recheck_days(settings) -> int:
    """从注入的设置对象读周期（鸭型：只认 list_recheck_days 属性；
    非法/越界照 validate_recheck_days 显式报错，不静默夹取）。"""
    return validate_recheck_days(getattr(settings, "list_recheck_days", None))


def _in_whitelist_view(node) -> bool:
    """白名单派生视图谓词（work10 §6.1 定稿口径）：ExitProfile.is_clean
    is True 且未被隔离。刻意不含 banned 过滤——banned 是人工标记不是
    视图条件，主人定的「黑名单也须复查」与 §6.4 的 banned 恢复处置
    都依赖 banned 且画像干净的节点仍在复查范围内（恢复可连 → 只提示
    是否放回，不自动放回）。"""
    profile = getattr(node, "exit_profile", None)
    return (profile is not None and profile.is_clean is True
            and node.extra.get("quarantined") != "1")


def _is_quarantined(node) -> bool:
    """隔离区标记（verify._apply_quarantine 写/清，本模块只读）。"""
    return node.extra.get("quarantined") == "1"


@dataclass
class _RecheckSample:
    """verify_samples 行的鸭型替身：字段集与 store.VerifySample 一致，
    经 store.add_sample 写入（本模块不 import client 包，故自带此形；
    store._add_sample_impl 只按属性取值，不验类型）。"""
    node_id: str
    kind: str
    success: bool
    ts: float = 0.0
    latency_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    exit_ip: Optional[str] = None
    detail: str = ""


def plan_recheck(store, settings, now=None) -> list:
    """算本轮复查对象（不探测、不落库、不报错——纯计划）。

    范围（work10 §6.3 定稿）：白名单派生视图内节点 + 隔离区节点；其中
    OpenVPN 节点照旧标 skip（隧道模式未连接测不了，如实跳过不实测）。
    banned 节点不整体排除（见 _in_whitelist_view 注记）。banned 且画像
    脏又未被隔离的节点两头不沾、不进计划（work10 §6.3「复查只对白名单
    视图与隔离区跑」字面口径；其画像本就只在验真流水线里刷新）。

    到期判定：节点上次验真时间 = exit_profile.verified_at（成功验真才
    更新；复查链路死画像不动，下一轮自然再排上——恢复可连要靠反复探）；
    距今超过 list_recheck_days 天即到期。verified_at 缺失（历史数据/
    手工画像）视为到期，如实补测。空对象返回空清单，不报错。

    返回 list[dict]，每项：
      node_id / protocol / source / scope（whitelist|quarantine）/
      banned / skip（OpenVPN=True）/ last_verified_at（None=未验真）。
    """
    days = _recheck_days(settings)
    now = time.time() if now is None else now
    threshold = now - days * SECONDS_PER_DAY
    plan = []
    for node in store.list_nodes(include_banned=True):
        if _in_whitelist_view(node):
            scope = SCOPE_WHITELIST
        elif _is_quarantined(node):
            scope = SCOPE_QUARANTINE
        else:
            continue
        profile = getattr(node, "exit_profile", None)
        verified_at = profile.verified_at if profile is not None else None
        if verified_at is not None and verified_at > threshold:
            continue                      # 上次验真还在周期内，未到期
        plan.append({
            "node_id": node.id,
            "protocol": node.protocol,
            "source": node.source,
            "scope": scope,
            "banned": bool(node.banned),
            "skip": node.protocol == "openvpn",
            "last_verified_at": verified_at,
        })
    return plan


def run_recheck(store, settings, verify_fn, now=None) -> dict:
    """跑一轮复查：plan_recheck → 逐节点调注入 verify_fn（合同见模块头）
    → 按 work10 §6.4 口径归类处置 → 每个实测节点落一条 kind="recheck"
    样本 → 返回逐节点处置报告。

    处置归类（只读节点前后状态；判定与写回全在既有验真流水线里）：
    - verify_fn 报败（ok=False）→ recheck_failed 复查失败：画像不动
      （apply_outcome 既有口径：失败不覆盖旧画像），白名单视图保留；
    - 成功，白名单视图节点：
      · 复查后进了隔离（画像刷新为脏，_apply_quarantine 挂牌）→
        dirty_out 复查变脏移出视图（派生视图零动作，不自动拉黑）；
      · 复查后不在视图（干净度未知，未挂牌）→ view_lost 移出视图
        （不自动拉黑）；此为防御分支——真管道下 verify_exit 的
        derive_is_clean 对恒非空的 exit_info dict 只返回 True/False
        （未知情报按干净放行，冻结口径），不会产生 is_clean=None；
      · 仍在视图 → passed 复查通过；
    - 成功，隔离区节点：复查后隔离标记清除 → quarantine_cleared 自动
      摘牌（既有 _apply_quarantine 干的活，这里只记账）；标记仍在 →
      still_quarantined 复查仍污染；
    - banned 节点复查可连（实测成功且未被隔离）→ 额外记 prompt 提示
      「复查可连，是否放回」，人工确认才解除，绝不自动放回；变脏
      （复查后被隔离）不提示放回。
    - OpenVPN skip 与计划后不在库的节点：不调 verify_fn、不记样本。

    返回 dict：days（周期）/ now / planned（计划数）/ verified（实测数）/
    counts（按处置 token 计数）/ items（逐节点：node_id / disposition /
    reason / scope / banned / prompt）。只报计数与处置，不回显节点地址
    原文（无 name/host/出口 IP 字段）。
    """
    days = _recheck_days(settings)
    now = time.time() if now is None else now
    plan = plan_recheck(store, settings, now=now)
    items = []
    counts = {token: 0 for token in DISPOSITIONS}
    for entry in plan:
        base = {"node_id": entry["node_id"], "scope": entry["scope"],
                "banned": entry["banned"], "prompt": False}
        if entry["skip"]:
            counts[DISP_SKIP] += 1
            items.append({**base, "disposition": DISP_SKIP,
                          "reason": "OpenVPN 节点是隧道模式，未连接时测不了——"
                                    "复查如实跳过，不记假样本"})
            continue
        node = store.get_node(entry["node_id"])
        if node is None:
            counts[DISP_MISSING] += 1
            items.append({**base, "disposition": DISP_MISSING,
                          "reason": "节点已不在库中（本轮跳过，不记样本）"})
            continue
        outcome = verify_fn(node)
        ok = bool(getattr(outcome, "ok", False))
        why = str(getattr(outcome, "reason", "") or "")
        latency = getattr(outcome, "latency_ms", None)
        jitter = getattr(outcome, "jitter_ms", None)
        # verify_fn 可能以另一对象写回（接线管道按 id 重取节点）：处置
        # 一律按库内现态读，不信入参对象的旧快照。
        after = store.get_node(entry["node_id"]) or node
        post_quarantined = _is_quarantined(after)
        profile = getattr(after, "exit_profile", None)
        post_in_view = (profile is not None and profile.is_clean is True
                        and not post_quarantined)
        if not ok:
            disposition = DISP_FAILED
            reason = f"复查失败：{why or '链路不通'}" \
                     "（画像未动，白名单视图保留；下轮复查再探）"
        elif entry["scope"] == SCOPE_WHITELIST:
            if post_quarantined:
                disposition = DISP_DIRTY_OUT
                reason = "复查变脏：画像刷新后不再干净，白名单视图自动移出" \
                         "（不自动拉黑，是否拉黑由人工决定）"
            elif not post_in_view:
                disposition = DISP_VIEW_LOST
                reason = "画像刷新后干净度未知，移出白名单视图" \
                         "（不自动拉黑，是否拉黑由人工决定）"
            else:
                disposition = DISP_PASSED
                reason = "复查通过：仍在白名单视图内"
        else:
            if not post_quarantined:
                disposition = DISP_QCLEARED
                reason = "复查恢复干净：隔离标记自动摘牌（既有验真口径）"
            else:
                disposition = DISP_QKEPT
                reason = "复查仍污染：隔离标记保留"
        if ok and not post_quarantined and after.banned:
            # 黑名单恢复可连：只提示（work10 §6.4 待主人点头项 C 不裁决，
            # 不实现自动放回；人工确认才解除）。
            base["prompt"] = True
            counts[DISP_BANNED_OK] += 1
            reason += "；黑名单节点复查可连，是否放回由人工确认（不自动放回）"
        store.add_sample(_RecheckSample(
            node_id=entry["node_id"], kind="recheck", success=ok, ts=now,
            latency_ms=latency, jitter_ms=jitter, detail=reason))
        counts[disposition] += 1
        items.append({**base, "disposition": disposition, "reason": reason})
    verified = len(plan) - counts[DISP_SKIP] - counts[DISP_MISSING]
    return {
        "days": days,
        "now": now,
        "planned": len(plan),
        "verified": verified,
        "counts": counts,
        "items": items,
    }
