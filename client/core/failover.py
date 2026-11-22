#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
容灾自动替换控制器（core/failover.py）—— U6（task21）
=======================================================
work10/DESIGN.md §5 全章定稿口径的纯逻辑实现：**只替换被显示红色
（不可用）的节点，活节点之间永不按延迟切换**（铁律在本模块的落点，
见下面「绝不出现的代码」一节）。

职责与边界（§5.1–§5.5）：
- 触发只吃两路红色信号：连接健康判死（GUI `_finish_health_probe`
  HEALTH_DEAD 分支喂入）与引擎进程掉线（orchestrator dropped 事件喂入）。
  `HEALTH_SUSPECT` 不触发（GUI 分级口径：suspect 进状态栏、dead 进告警条，
  本模块对 suspect 连入口都没有——feed 喂未知原因会当场 ValueError）。
  verify_failed 是连接即时失败、本就回到 IDLE，不走本模块（§5.1）。
- 替换动作全部经注入的既有会话通道执行（disconnect/connect），orchestrator
  零改动；**顺序只认组内人工排序（数组顺序 = 替换优先级），绝不按延迟挑**
  （§5.3）。挑选时跳过：被换下节点本身、已被 ban、已被隔离、最近验真
  已死（最近一条 connect/verify 样本失败）的节点。
- 组耗尽 → 输出「备用组无可用节点」IDLE 告警载荷后停手：一轮触发只扫一遍
  组，已失败/已跳过的节点不重试，不无限轮询（§5.3）。
- 记录：failover 样本 kind="failover" 落在新节点上，success 按新节点连接
  结果，latency 恒为空（不污染养鱼池延迟统计，§5.3）；被换下节点只写
  extra 的 failover_out_at/failover_reason（纯函数置位，§5.4），不自动
  拉黑、不进隔离区——本模块对 banned/quarantined **只有读**（§5.4）。
- 总开关默认关（§5.5）：开关关时任何信号零动作零记录（连样本、标记、
  告警都不产生），在 handle 最前置的 enabled() 检查处直接返回。

**绝不出现的代码**（§5.5 四禁词，审查方全库 grep 验收；本文件自带
自检锁死）：任何「选最快/选最优」的自动连接入口、任何把备用组按指标
重排的路径、任何定时对比后自动换节点的路径。本模块：没有任何「延迟」
输入（Ops 接口连延迟字段都没有）、没有任何 sort、没有任何定时器——
挑选就是「按数组顺序取第一个合格者」。

依赖注入：本模块只用标准库、不 import client 包任何模块（与
core/health.py 同一独立口径）。会话通道 / 节点库 / 样本写入 / 告警展示
全部经 FailoverOps 注入，离线可测（test_failover.py 全部用假 Ops）。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# 触发原因（§5.3 提示文案口径）
# ---------------------------------------------------------------------------

REASON_HEALTH_DEAD = "health_dead"      # 连接健康判死（末尾连续 5 次探测失败）
REASON_ENGINE_DROPPED = "engine_dropped"  # 引擎进程掉线（orchestrator dropped）

REASON_TEXT = {
    REASON_HEALTH_DEAD: "节点连续 5 次探测失败",
    REASON_ENGINE_DROPPED: "引擎掉线",
}

# 告警载荷 kind（GUI 侧只展示 text，kind 供测试与将来细分）
ALERT_REPLACED = "replaced"             # 替换成功
ALERT_REPLACED_FAILED = "replaced_failed"  # 替换动作做了，但新节点连接失败
ALERT_EXHAUSTED = "exhausted"           # 备用组无可用节点（停在 IDLE）
ALERT_GROUP_UNREADABLE = "group_unreadable"  # 组文件读不了（不动作）

# 被换下标记的 extra 键（§5.4：failover_out_at = epoch 秒 + failover_reason）
FAILOVER_OUT_AT = "failover_out_at"
FAILOVER_REASON = "failover_reason"


# ---------------------------------------------------------------------------
# 被换下标记：置位 / 清除 纯函数（§5.4）
# ---------------------------------------------------------------------------

def mark_failover_out(extra: dict, reason: str, ts: Optional[float] = None) -> dict:
    """节点 extra 置被换下标记 → 新 dict（不改入参）。

    只写 failover_out_at / failover_reason 两个键，extra 其余键（含
    quarantined 等既有标记）原样保留——§5.4：容灾标记与黑名单/隔离区
    互不写。ts 缺省取当前 epoch 秒。
    """
    out = dict(extra or {})
    out[FAILOVER_OUT_AT] = float(ts if ts is not None else time.time())
    out[FAILOVER_REASON] = reason
    return out


def clear_failover_out(extra: dict) -> dict:
    """清除被换下标记 → 新 dict（不改入参）。没有标记时原样返回副本。"""
    out = dict(extra or {})
    out.pop(FAILOVER_OUT_AT, None)
    out.pop(FAILOVER_REASON, None)
    return out


def is_failover_out(extra: dict) -> bool:
    """节点是否带着被换下标记（界面「容灾换下 HH:MM」小徽标的判据）。"""
    return bool((extra or {}).get(FAILOVER_OUT_AT))


def clear_mark_on_link_success(store, node_id: str, success: bool) -> None:
    """验真/连接成功后清被换下标记（§5.4 清除规则的接线助手）。

    store 只要求 get_node / upsert_node 两个方法（鸭型接口，与
    orchestrator 持有 Store 的方式一致）。success=False 或无标记时
    no-op。恢复与否由既有验真口径说了算，本函数不做任何自己的判断。
    只动 failover_* 两个键；对 banned/quarantined 只读不写。
    """
    if not success:
        return
    node = store.get_node(node_id)
    if node is None or not is_failover_out(node.extra):
        return
    node.extra = clear_failover_out(node.extra)
    store.upsert_node(node)


# ---------------------------------------------------------------------------
# 注入接口
# ---------------------------------------------------------------------------

@dataclass
class FailoverOps:
    """控制器依赖的全部外界接口（全部可注入，离线测试用假实现）。

    - enabled()          总开关现读（GUI 侧每次现读 backup_group.json，
                         改即见；§5.5 默认关）。
    - group_node_ids()   组内节点 id 列表，**数组顺序 = 替换优先级**。
                         每次触发现读，编辑立即生效。
    - get_node(id)       节点对象或 None；需要 .name / .banned / .extra
                         （store.get_node 原样可用）。
    - last_link_ok(id)   最近一条 connect/verify 样本的成败（True/False，
                         没测过 None；history.last_result 口径）。
    - active_node_id()   当前活动节点 id（无连接 None；orchestrator 原样）。
    - disconnect()       既有会话通道断开（orchestrator.disconnect）。
    - connect(id)        既有会话通道连接（orchestrator.connect），失败抛错。
    - record_sample(id, success, detail)  样本写入（kind 固定 "failover"）。
    - update_node_extra(id, extra)        把标记后的 extra 写回节点库。
    - alert(payload)     告警展示（GUI 侧 _show_alert + tray.showMessage）。

    接口里**没有延迟字段**——不是忘了，是铁律：控制器拿不到延迟，
    才拿延迟挑不了节点（§5.3）。
    """
    enabled: Callable[[], bool]
    group_node_ids: Callable[[], list]
    get_node: Callable[[str], object]
    last_link_ok: Callable[[str], Optional[bool]]
    active_node_id: Callable[[], Optional[str]]
    disconnect: Callable[[], object]
    connect: Callable[[str], object]
    record_sample: Callable[[str, bool, str], None]
    update_node_extra: Callable[[str, dict], None]
    alert: Callable[[dict], None]


# ---------------------------------------------------------------------------
# 控制器
# ---------------------------------------------------------------------------

class FailoverController:
    """容灾自动替换控制器（纯逻辑，动作全走注入接口）。

    线程模型：信号在 GUI 主线程喂入，handle 在既有工作线程通道执行；
    用非阻塞锁做单飞——一轮替换进行中，后续信号直接忽略（不排队、
    不叠加：§5.3「不无限轮询」的控制器侧落点）。
    """

    def __init__(self, ops: FailoverOps):
        self.ops = ops
        self._lock = threading.Lock()

    # ---------------- 信号入口 ----------------
    def on_health_dead(self, node_id: str, detail: str = ""):
        """连接健康判死信号（GUI `_finish_health_probe` HEALTH_DEAD 分支喂入）。"""
        return self.handle(REASON_HEALTH_DEAD, node_id, detail)

    def on_dropped(self, node_id: str, detail: str = ""):
        """引擎掉线信号（orchestrator dropped 事件分支喂入）。"""
        return self.handle(REASON_ENGINE_DROPPED, node_id, detail)

    # ---------------- 主流程 ----------------
    def handle(self, reason: str, dead_node_id: str, detail: str = ""):
        """一轮容灾替换。返回告警载荷 dict（无动作返回 None，测试/GUI 可查）。

        流程（§5.3 定稿，一次触发最多一次替换尝试）：
        开关关 → 零动作零记录；单飞锁 → 已在进行中直接忽略；
        按组序找第一个「未 ban、未隔离、最近验真未死」的节点（跳过被换下
        节点本身）→ 有活动连接先 disconnect → 标记被换下节点 → connect →
        按连接结果落 failover 样本 → 告警。找不到可换节点 → IDLE 告警停手，
        不重试、不轮询。
        """
        if reason not in REASON_TEXT:
            # suspect 等其它状态不该到得了这里（GUI 分级口径：suspect 只
            # 提醒不动作）。喂错了就是接线错误，当场暴露，不静默吞。
            raise ValueError(f"容灾控制器不认识这个触发原因: {reason!r}"
                             "（只有 health_dead / engine_dropped 触发；"
                             "suspect 不触发替换）")

        # §5.5：总开关关 = 完全不干预，连记录都不写。任何信号来了都从这里
        # 直接返回，后面的一切（扫描、动作、样本、标记、告警）都不发生。
        try:
            enabled = self.ops.enabled()
        except Exception as exc:
            return self._alert_group_unreadable(exc)
        if not enabled:
            return None

        # 单飞：上一轮替换还没收尾时忽略新信号（不排队不重试）。
        if not self._lock.acquire(blocking=False):
            return None
        try:
            return self._handle_locked(reason, dead_node_id, detail)
        finally:
            self._lock.release()

    def _handle_locked(self, reason: str, dead_node_id: str, detail: str):
        reason_text = REASON_TEXT[reason]

        try:
            group_ids = list(self.ops.group_node_ids())
        except Exception as exc:
            return self._alert_group_unreadable(exc)

        # 按组序挑下一个候选。**顺序只认组内人工排序**——这里就是一个
        # for 循环取第一个合格者：没有任何排序、没有任何延迟参与（铁律
        # 落点；禁词自检 test_failover.py 锁死）。
        chosen_id = None
        for nid in group_ids:
            if nid == dead_node_id:
                continue                      # 被换下的就是它，不在候选里
            node = self.ops.get_node(nid)
            if node is None:
                continue                      # 已被后台删除
            if getattr(node, "banned", False):
                continue                      # 已拉黑（§5.3 挑选条件一）
            if (getattr(node, "extra", None) or {}).get("quarantined") == "1":
                continue                      # 已隔离（§5.3 挑选条件二）
            if self.ops.last_link_ok(nid) is False:
                continue                      # 最近验真已死（§5.3 挑选条件三）
            chosen_id = nid
            break

        if chosen_id is None:
            # §5.3：找不到可换节点 → 停在 IDLE 并告警，不重试已失败节点、
            # 不无限轮询。一轮只扫这一遍，扫完即停。
            payload = {
                "kind": ALERT_EXHAUSTED,
                "text": (f"容灾替换未发生：备用组无可用节点"
                         f"（组内 {len(group_ids)} 个候选全部不可用——已删除/"
                         "已拉黑/已隔离/最近验真失败都会被跳过）。"
                         "已停在未连接状态，请手动选择节点。"),
                "reason": reason,
                "from_node_id": dead_node_id,
                "to_node_id": None,
            }
            self.ops.alert(payload)
            return payload

        a_node = self.ops.get_node(dead_node_id)
        a_name = getattr(a_node, "name", None) or (dead_node_id or "原节点")
        b_node = self.ops.get_node(chosen_id)
        b_name = getattr(b_node, "name", None) or chosen_id

        # §5.3 动作序列第一步：disconnect。dropped 路径 orchestrator 已
        # 自己回到 IDLE，再调 disconnect 只会产出「本来就没有活动连接」
        # 的空事件——有活动连接才调，避免噪音。
        if self.ops.active_node_id() is not None:
            self.ops.disconnect()

        # §5.4：被换下节点只写 failover_out_at / failover_reason 两个键，
        # 不自动拉黑、不进隔离区（对 banned/quarantined 只读不写）。
        if a_node is not None:
            self.ops.update_node_extra(
                dead_node_id,
                mark_failover_out(getattr(a_node, "extra", None) or {},
                                  reason))

        # §5.3 动作序列最后一步：connect。一次触发只连这一个候选；连不上
        # 不自动换下一个（那条「不重试已失败节点、不无限轮询」红线），
        # 用户看到失败告警后自行决定。
        connect_error = ""
        try:
            self.ops.connect(chosen_id)
        except Exception as exc:               # OrchestratorError 等，含验证失败
            connect_error = str(exc)

        # §5.3 记录：failover 样本落在新节点上，success 按新节点连接结果，
        # detail = 「A → B + 触发原因」。latency 恒为空（样本写入接口
        # 只给这三个参数，想污染延迟统计都拿不到入口）。
        sample_detail = f"{a_name} → {b_name}（原因：{reason_text}）"
        if connect_error:
            sample_detail += f"；新节点连接失败：{connect_error}"
        self.ops.record_sample(chosen_id, success=not connect_error,
                               detail=sample_detail)

        payload = {
            "kind": ALERT_REPLACED_FAILED if connect_error else ALERT_REPLACED,
            "text": (f"已按容灾替换：{a_name} → {b_name}（原因：{reason_text}）"
                     if not connect_error else
                     f"已按容灾替换：{a_name} → {b_name}（原因：{reason_text}），"
                     f"但新节点连接失败：{connect_error}。已停在未连接状态。"),
            "reason": reason,
            "from_node_id": dead_node_id,
            "to_node_id": chosen_id,
            "connect_error": connect_error,
        }
        self.ops.alert(payload)
        return payload

    def _alert_group_unreadable(self, exc: Exception) -> dict:
        """备用组文件读不了：不动作（开关当关处理），告警说明停手原因。
        绝不静默重置用户的组文件（backup_group.BackupGroupError 口径）。"""
        payload = {
            "kind": ALERT_GROUP_UNREADABLE,
            "text": f"备用组文件读不了，容灾替换停在这里（未做任何动作）：{exc}",
            "reason": None,
            "from_node_id": None,
            "to_node_id": None,
        }
        self.ops.alert(payload)
        return payload
