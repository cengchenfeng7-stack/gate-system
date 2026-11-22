#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""work21/test_failover.py —— U6 容灾/自动替换 离线测试（task21）。

全部离线：不联网、不依赖 PySide6（Qt 原型自检用子进程跑、缺 Qt 自动
跳过，可用环境变量 FAILOVER_QT_PYTHON 指定带 PySide6 的解释器）。

覆盖（简报验收清单）：
- 只有 dead / dropped 触发；suspect 不触发；总开关关时零动作零记录；
- 挑选顺序严格 = 组序（低延迟排组尾、高延迟排组首 → 选组首，反延迟
  排序证明）；
- 跳过 banned / quarantined / 最近验真已死；组耗尽 → IDLE 告警不循环重试；
- failover 样本 kind/success/detail 形状（§5.3）且零 latency 样本；
- 被换下标记置位、验真成功清除；对 banned/quarantined 只读不写；
- backup_group 往返无损、原子写、<2 拒绝开启、清组正确；
- 禁词自检（两个英文禁词 + 无按延迟排序/定时对比的代码路径）；
- wiring.diff 对 0.25.0 实树可应用（有 client-dev 时才跑）。

双形态导入：work21/ 内直测（顶层 import），或拷进 client/tests/
（包内相对 import）——两种形态测试等价。
"""

from __future__ import annotations

import dataclasses
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

try:
    from ..core import backup_group, failover    # 集成形态：拷进 client/tests/
except ImportError:
    import backup_group, failover                # work21/ 独立形态

BASE = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# 假件（依赖注入的离线替身，全部内存态）
# ---------------------------------------------------------------------------

class FakeNode:
    def __init__(self, nid, name=None, banned=False, extra=None,
                 latency_ms=None, quarantined=False):
        self.id = nid
        self.name = name or nid
        self.banned = banned
        self.extra = dict(extra or {})
        if quarantined:                 # 与 verify._apply_quarantine 同口径
            self.extra["quarantined"] = "1"
        self.latency_ms = latency_ms    # 只是展示口径的数据，容灾链路不读它


class FakeStore:
    """假节点库：get_node/upsert_node/set_banned/delete_node。

    set_banned / delete_node 在这里存在，正是为了让测试证明容灾链路
    **没有**调用它们（§5.4：对拉黑/删除只读不写）。
    """

    def __init__(self, nodes=()):
        self.nodes = {n.id: n for n in nodes}
        self.samples = []               # record_sample 落点（假样本）
        self.upserts = []
        self.set_banned_calls = []
        self.delete_calls = []

    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def upsert_node(self, node):
        self.upserts.append(node.id)
        self.nodes[node.id] = node
        return node

    def set_banned(self, node_id, banned=True):
        self.set_banned_calls.append((node_id, banned))
        return node_id in self.nodes

    def delete_node(self, node_id):
        self.delete_calls.append(node_id)
        return self.nodes.pop(node_id, None) is not None


class FakeOrch:
    """假会话通道：disconnect/connect 动作序列照录，connect 结果可编排。"""

    IDLE, CONNECTED = "idle", "connected"

    def __init__(self, active=None, connect_script=None):
        self.state = self.CONNECTED if active else self.IDLE
        self.active_node_id = active
        self.calls = []                 # [("disconnect", None) / ("connect", id)]
        self.connect_script = list(connect_script or [])   # None=成功，Exception=抛

    def disconnect(self, *a, **k):
        self.calls.append(("disconnect", None))
        self.state = self.IDLE
        self.active_node_id = None
        return "disconnected"

    def connect(self, node_id, *a, **k):
        self.calls.append(("connect", node_id))
        outcome = self.connect_script.pop(0) if self.connect_script else None
        if isinstance(outcome, Exception):
            self.state = self.IDLE
            self.active_node_id = None
            raise outcome
        self.state = self.CONNECTED
        self.active_node_id = node_id
        return "connected"


def make_ops(store, orch, group, link_results=None, alerts=None):
    """组装 failover.FailoverOps（与 WIRING.md 落点③同一接线形状）。

    group 是活 dict：开关关测试直接改它，控制器每次 handle 现读。
    link_results: {node_id: bool}——最近一条 connect/verify 样本成败
    （history.last_result 口径；缺键 = 从没测过，None）。
    """
    if alerts is None:
        alerts = []
    link_results = link_results or {}

    def update_extra(node_id, extra):
        node = store.get_node(node_id)
        if node is not None:
            node.extra = dict(extra)
            store.upsert_node(node)

    ops = failover.FailoverOps(
        enabled=lambda: bool(group.get("enabled", False)),
        group_node_ids=lambda: list(group.get("node_ids", [])),
        get_node=store.get_node,
        last_link_ok=lambda nid: link_results.get(nid),
        active_node_id=lambda: orch.active_node_id,
        disconnect=orch.disconnect,
        connect=orch.connect,
        record_sample=lambda nid, success, detail: store.samples.append(
            {"node_id": nid, "kind": "failover", "success": success,
             "detail": detail}),
        update_node_extra=update_extra,
        alert=alerts.append,
    )
    return ops, alerts


def make_world(group=None, nodes=(), active=None, link_results=None):
    store = FakeStore(nodes)
    orch = FakeOrch(active=active)
    group = group or {"version": 1, "enabled": True, "node_ids": []}
    ops, alerts = make_ops(store, orch, group, link_results)
    ctrl = failover.FailoverController(ops)
    return ctrl, store, orch, alerts


# ---------------------------------------------------------------------------
# 控制器：触发口径（§5.1）
# ---------------------------------------------------------------------------

class TriggerTests(unittest.TestCase):
    """只有 dead / dropped 触发；suspect 不触发；开关关零动作零记录。"""

    def _world(self, enabled=True):
        nodes = [FakeNode("A", "甲节点"), FakeNode("B", "乙节点"),
                 FakeNode("C", "丙节点")]
        group = {"version": 1, "enabled": enabled, "node_ids": ["B", "C"]}
        return make_world(group=group, nodes=nodes, active="A")

    def test_suspect_cannot_trigger(self):
        """suspect 显式忽略：控制器连入口都没有，喂了当场 ValueError。"""
        ctrl, store, orch, alerts = self._world()
        with self.assertRaises(ValueError):
            ctrl.handle("suspect", "A")
        with self.assertRaises(ValueError):
            ctrl.handle("unknown_thing", "A")
        self.assertEqual(orch.calls, [])
        self.assertEqual(store.samples, [])
        self.assertEqual(alerts, [])

    def test_health_dead_and_dropped_trigger(self):
        ctrl, store, orch, _ = self._world()
        self.assertIsNotNone(ctrl.on_health_dead("A"))
        self.assertEqual(orch.calls, [("disconnect", None), ("connect", "B")])
        # 复位后 dropped 也触发（掉线场景 active 已是 None）
        ctrl2, store2, orch2, _ = self._world()
        orch2.state = FakeOrch.IDLE
        orch2.active_node_id = None
        self.assertIsNotNone(ctrl2.on_dropped("A"))
        self.assertEqual(orch2.calls, [("connect", "B")])

    def test_switch_off_zero_action_zero_record(self):
        """总开关关：信号来了也零动作零记录——不连接、不写样本、不打标记、
        不弹告警，连节点库都不 upsert（§5.5「连记录都不写」）。"""
        for feed in ("on_health_dead", "on_dropped"):
            ctrl, store, orch, alerts = self._world(enabled=False)
            self.assertIsNone(getattr(ctrl, feed)("A"))
            self.assertEqual(orch.calls, [])
            self.assertEqual(store.samples, [])
            self.assertEqual(store.upserts, [])
            self.assertEqual(alerts, [])
            self.assertEqual(store.nodes["A"].extra, {})   # 标记也没打

    def test_switch_on_requires_group_candidates(self):
        ctrl, store, orch, alerts = make_world(
            group={"version": 1, "enabled": True, "node_ids": []},
            nodes=[FakeNode("A")], active="A")
        payload = ctrl.on_health_dead("A")
        self.assertEqual(payload["kind"], failover.ALERT_EXHAUSTED)
        self.assertEqual(orch.calls, [])          # 无动作


# ---------------------------------------------------------------------------
# 控制器：挑选顺序（§5.3 反延迟排序证明）
# ---------------------------------------------------------------------------

class SelectionOrderTests(unittest.TestCase):

    def test_group_order_beats_latency(self):
        """挑选顺序严格 = 组内人工排序：故意把低延迟节点排组尾、高延迟
        排组首，断言选中的是组首（反延迟排序证明）。"""
        nodes = [
            FakeNode("A", "死节点"),
            FakeNode("Nslow", "高延迟节点", latency_ms=9000),
            FakeNode("Nfast", "低延迟节点", latency_ms=120),
        ]
        group = {"version": 1, "enabled": True,
                 "node_ids": ["Nslow", "Nfast"]}   # 人工排序：慢的在前
        ctrl, store, orch, _ = make_world(group=group, nodes=nodes, active="A")
        ctrl.on_health_dead("A")
        self.assertEqual(orch.calls,
                         [("disconnect", None), ("connect", "Nslow")],
                         "选中的必须是组首（高延迟）节点——顺序只认组内人工排序")

    def test_interface_has_no_latency_lever(self):
        """结构证明：Ops 注入接口没有任何延迟/择优/排序入口——控制器
        想按延迟挑都拿不到数据（铁律的接口层落点）。"""
        names = [f.name.lower() for f in dataclasses.fields(failover.FailoverOps)]
        for banned_word in ("latency", "best", "fast", "sort", "speed"):
            self.assertFalse(
                any(banned_word in n for n in names),
                f"FailoverOps 出现可疑字段：{banned_word}")

    def test_group_survivors_keep_manual_order(self):
        """被跳过的节点不改变组序：首个合格者 = 组序里第一个能用的。"""
        nodes = [FakeNode("A"), FakeNode("N1"), FakeNode("N2"), FakeNode("N3")]
        group = {"version": 1, "enabled": True,
                 "node_ids": ["N1", "N2", "N3"]}
        link = {"N1": False}                    # N1 最近验真已死
        # 掉线场景：orchestrator 已自己回 IDLE（active=None）
        ctrl, store, orch, _ = make_world(group=group, nodes=nodes,
                                          active=None, link_results=link)
        ctrl.on_dropped("A")
        self.assertEqual(orch.calls, [("connect", "N2")])

    def test_dead_node_itself_excluded_even_if_in_group(self):
        """被换下节点本身在组内也不作候选（哪怕它最近验真是成功的）。"""
        nodes = [FakeNode("A"), FakeNode("B")]
        group = {"version": 1, "enabled": True, "node_ids": ["A", "B"]}
        ctrl, store, orch, _ = make_world(group=group, nodes=nodes,
                                          active="A", link_results={"A": True})
        ctrl.on_health_dead("A")
        self.assertEqual(orch.calls, [("disconnect", None), ("connect", "B")])


# ---------------------------------------------------------------------------
# 控制器：跳过规则与组耗尽（§5.3）
# ---------------------------------------------------------------------------

class SkipAndExhaustTests(unittest.TestCase):

    def test_skip_banned_quarantined_recently_dead(self):
        nodes = [
            FakeNode("A"),
            FakeNode("Nban", banned=True),
            FakeNode("Nquar", quarantined=True),
            FakeNode("Ndead"),                       # 最近验真已死
            FakeNode("Nok"),
        ]
        group = {"version": 1, "enabled": True,
                 "node_ids": ["Nban", "Nquar", "Ndead", "Nok"]}
        link = {"Ndead": False}
        ctrl, store, orch, _ = make_world(group=group, nodes=nodes,
                                          active="A", link_results=link)
        ctrl.on_health_dead("A")
        self.assertEqual(orch.calls, [("disconnect", None), ("connect", "Nok")])

    def test_skipped_nodes_untouched(self):
        """跳过只读：被跳过节点（ban/隔离/验死）extra 与 ban 位零写入。"""
        nodes = [FakeNode("A"), FakeNode("Nban", banned=True),
                 FakeNode("Nquar", quarantined=True)]
        group = {"version": 1, "enabled": True, "node_ids": ["Nban", "Nquar"]}
        ctrl, store, orch, _ = make_world(group=group, nodes=nodes, active="A")
        payload = ctrl.on_health_dead("A")
        self.assertEqual(payload["kind"], failover.ALERT_EXHAUSTED)
        for nid in ("Nban", "Nquar"):
            self.assertNotIn("failover_out_at", store.nodes[nid].extra)
            self.assertNotIn("failover_reason", store.nodes[nid].extra)

    def test_deleted_node_skipped(self):
        """组内节点已被后台删除（库里查不到）→ 跳过，不炸。"""
        nodes = [FakeNode("A"), FakeNode("Nok")]
        group = {"version": 1, "enabled": True, "node_ids": ["Ngone", "Nok"]}
        ctrl, store, orch, _ = make_world(group=group, nodes=nodes, active=None)
        ctrl.on_dropped("A")
        self.assertEqual(orch.calls, [("connect", "Nok")])

    def test_group_exhausted_idle_alert_no_retry(self):
        """组耗尽 → IDLE 告警；一轮只扫一遍，不循环重试已跳过的节点。"""
        nodes = [FakeNode("A"), FakeNode("N1", banned=True)]
        group = {"version": 1, "enabled": True, "node_ids": ["N1"]}
        ctrl, store, orch, alerts = make_world(group=group, nodes=nodes,
                                               active="A")
        payload = ctrl.on_health_dead("A")
        self.assertEqual(payload["kind"], failover.ALERT_EXHAUSTED)
        self.assertIn("备用组无可用节点", payload["text"])
        self.assertEqual(orch.calls, [])          # 没有任何连接动作
        self.assertEqual(store.samples, [])       # 没有样本
        self.assertEqual(len(alerts), 1)          # 一轮触发恰好一条告警
        # 同一轮内不会自己再扫：控制器没有重试循环（再来一次是新信号）
        payload2 = ctrl.on_health_dead("A")
        self.assertEqual(len(alerts), 2)
        self.assertEqual(orch.calls, [])

    def test_no_infinite_polling_after_failed_connect(self):
        """新节点连接失败 → 停手；同一触发内绝不换下一个候选。"""
        nodes = [FakeNode("A"), FakeNode("B"), FakeNode("C")]
        group = {"version": 1, "enabled": True, "node_ids": ["B", "C"]}
        store = FakeStore(nodes)
        orch = FakeOrch(active="A",
                        connect_script=[RuntimeError("验出口失败: 出口对不上")])
        ops, alerts = make_ops(store, orch, group)
        ctrl = failover.FailoverController(ops)
        payload = ctrl.on_health_dead("A")
        self.assertEqual(payload["kind"], failover.ALERT_REPLACED_FAILED)
        self.assertEqual(orch.calls, [("disconnect", None), ("connect", "B")],
                         "只试了组首一个候选，没有自动滑到下一个")


# ---------------------------------------------------------------------------
# 控制器：动作序列、样本形状、标记（§5.3 / §5.4）
# ---------------------------------------------------------------------------

class ActionAndRecordTests(unittest.TestCase):

    def _world(self, connect_script=None):
        nodes = [FakeNode("A", "甲节点"), FakeNode("B", "乙节点")]
        group = {"version": 1, "enabled": True, "node_ids": ["B"]}
        store = FakeStore(nodes)
        orch = FakeOrch(active="A", connect_script=connect_script)
        ops, alerts = make_ops(store, orch, group)
        return failover.FailoverController(ops), store, orch, alerts

    def test_sequence_disconnect_before_connect_and_template(self):
        ctrl, store, orch, alerts = self._world()
        payload = ctrl.on_health_dead("A")
        # 动作顺序：先断开，再连组序下一个
        self.assertEqual(orch.calls, [("disconnect", None), ("connect", "B")])
        # 提示模板（§5.3）：「已按容灾替换：A → B（原因：…）」
        self.assertEqual(payload["kind"], failover.ALERT_REPLACED)
        self.assertEqual(payload["text"],
                         "已按容灾替换：甲节点 → 乙节点（原因：节点连续 5 次探测失败）")
        self.assertEqual(alerts, [payload])

    def test_dropped_reason_text_and_no_disconnect_when_idle(self):
        """掉线场景 orchestrator 已回 IDLE：不多调一次 disconnect。"""
        ctrl, store, orch, _ = self._world()
        orch.state = FakeOrch.IDLE
        orch.active_node_id = None
        payload = ctrl.on_dropped("A")
        self.assertEqual(orch.calls, [("connect", "B")])
        self.assertIn("（原因：引擎掉线）", payload["text"])

    def test_failover_sample_shape_no_latency(self):
        """样本形状（§5.3）：kind=failover、success=新节点连接结果、
        detail=「A → B + 触发原因」；零 latency 字段（想污染都没有入口）。"""
        ctrl, store, orch, _ = self._world()
        ctrl.on_health_dead("A")
        self.assertEqual(len(store.samples), 1)
        s = store.samples[0]
        self.assertEqual(s["node_id"], "B")       # 落在新节点上
        self.assertEqual(s["kind"], "failover")
        self.assertTrue(s["success"])
        self.assertEqual(s["detail"],
                         "甲节点 → 乙节点（原因：节点连续 5 次探测失败）")
        self.assertNotIn("latency_ms", s)         # 无 latency 字段
        self.assertNotIn("latency", s)

    def test_failed_connect_sample_records_failure(self):
        ctrl, store, orch, _ = self._world(
            connect_script=[RuntimeError("验出口失败: 出口对不上")])
        payload = ctrl.on_health_dead("A")
        s = store.samples[0]
        self.assertFalse(s["success"])
        self.assertIn("甲节点 → 乙节点（原因：节点连续 5 次探测失败）", s["detail"])
        self.assertIn("新节点连接失败：验出口失败: 出口对不上", s["detail"])
        self.assertEqual(payload["kind"], failover.ALERT_REPLACED_FAILED)

    def test_replaced_out_mark_set(self):
        """被换下节点只写 extra 的 failover_out_at / failover_reason（§5.4）。"""
        ctrl, store, orch, _ = self._world()
        before = time.time() - 5
        ctrl.on_health_dead("A")
        extra = store.nodes["A"].extra
        self.assertIn("failover_out_at", extra)
        self.assertIn("failover_reason", extra)
        self.assertEqual(extra["failover_reason"], failover.REASON_HEALTH_DEAD)
        self.assertIsInstance(extra["failover_out_at"], float)
        self.assertGreaterEqual(extra["failover_out_at"], before)
        # 新节点 B 不带任何标记
        self.assertEqual(store.nodes["B"].extra, {})

    def test_readonly_for_banned_and_quarantined(self):
        """容灾链路对拉黑/隔离只读不写：无 set_banned 调用、无隔离键写入、
        无删除调用（§5.4：互不写）。"""
        nodes = [FakeNode("A"), FakeNode("B"), FakeNode("Nban", banned=True),
                 FakeNode("Nquar", quarantined=True)]
        group = {"version": 1, "enabled": True, "node_ids": ["Nban", "Nquar", "B"]}
        store = FakeStore(nodes)
        orch = FakeOrch(active="A")
        ops, alerts = make_ops(store, orch, group)
        ctrl = failover.FailoverController(ops)
        ctrl.on_health_dead("A")
        self.assertEqual(store.set_banned_calls, [])
        self.assertEqual(store.delete_calls, [])
        snapshot = {n.id: dict(n.extra) for n in nodes}
        for nid in ("Nban", "Nquar"):
            self.assertEqual(store.nodes[nid].extra, snapshot[nid])
            self.assertNotIn("failover_out_at", store.nodes[nid].extra)
        # 隔离标记原值未动（只读证据）：Nquar 的 quarantined 仍是 "1"
        self.assertEqual(store.nodes["Nquar"].extra.get("quarantined"), "1")

    def test_single_flight_ignores_concurrent_signal(self):
        """单飞：一轮替换进行中，后续信号直接忽略（不排队不叠加）。"""
        ctrl, store, orch, alerts = self._world()
        ctrl._lock.acquire()                      # 模拟上一轮还在跑
        try:
            self.assertIsNone(ctrl.on_health_dead("A"))
        finally:
            ctrl._lock.release()
        self.assertEqual(orch.calls, [])
        self.assertEqual(store.samples, [])
        self.assertEqual(alerts, [])

    def test_group_file_unreadable_stops_hand(self):
        """组文件读不了：不动作（开关当关处理），告警说明停手原因。"""
        ctrl, store, orch, alerts = self._world()
        ops, alerts = make_ops(store, orch, {"version": 1, "enabled": True,
                                             "node_ids": ["B"]})
        ops.enabled = mock.Mock(side_effect=RuntimeError("磁盘炸了"))
        ctrl = failover.FailoverController(ops)
        payload = ctrl.on_health_dead("A")
        self.assertEqual(payload["kind"], failover.ALERT_GROUP_UNREADABLE)
        self.assertEqual(orch.calls, [])


# ---------------------------------------------------------------------------
# 标记纯函数与清除接线助手（§5.4）
# ---------------------------------------------------------------------------

class MarkTests(unittest.TestCase):

    def test_mark_and_clear_pure_roundtrip(self):
        extra = {"uuid": "x", "config_format": "b64"}
        marked = failover.mark_failover_out(extra, "engine_dropped", ts=1700000000)
        # 不改入参、其余键原样保留
        self.assertEqual(extra, {"uuid": "x", "config_format": "b64"})
        self.assertEqual(marked["failover_out_at"], 1700000000.0)
        self.assertEqual(marked["failover_reason"], "engine_dropped")
        self.assertEqual(marked["uuid"], "x")
        self.assertTrue(failover.is_failover_out(marked))
        self.assertFalse(failover.is_failover_out(extra))
        cleared = failover.clear_failover_out(marked)
        self.assertEqual(cleared, {"uuid": "x", "config_format": "b64"})
        self.assertEqual(marked["failover_reason"], "engine_dropped")  # 原样
        self.assertFalse(failover.is_failover_out(cleared))

    def test_clear_mark_on_link_success(self):
        """验真成功 → 清标记；验真失败 / 无标记 → 原样。"""
        node = FakeNode("A", extra=failover.mark_failover_out({}, "health_dead",
                                                             ts=123.0))
        store = FakeStore([node])
        failover.clear_mark_on_link_success(store, "A", False)
        self.assertIn("failover_out_at", store.nodes["A"].extra)   # 失败不清
        failover.clear_mark_on_link_success(store, "A", True)
        self.assertNotIn("failover_out_at", store.nodes["A"].extra)
        self.assertNotIn("failover_reason", store.nodes["A"].extra)
        self.assertIn("A", store.upserts)                          # 写回了
        # 无标记节点：no-op，不 upsert
        before = len(store.upserts)
        failover.clear_mark_on_link_success(store, "A", True)
        self.assertEqual(len(store.upserts), before)
        # 节点不存在：no-op 不炸
        failover.clear_mark_on_link_success(store, "ghost", True)

    def test_clear_only_touches_failover_keys(self):
        """清除只动 failover_* 两键；隔离等既有标记原样保留（§5.4 互不写）。"""
        node = FakeNode("A", extra={"quarantined": "1",
                                    **failover.mark_failover_out({}, "x", ts=1)})
        store = FakeStore([node])
        failover.clear_mark_on_link_success(store, "A", True)
        self.assertEqual(store.nodes["A"].extra, {"quarantined": "1"})


# ---------------------------------------------------------------------------
# backup_group.py：落盘 / 校验 / 门卫 / 清组
# ---------------------------------------------------------------------------

class BackupGroupTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="failover_test_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "backup_group.json")

    def test_roundtrip_lossless(self):
        group = {"version": 1, "enabled": True,
                 "node_ids": ["节点一", "node-2", "n3"]}
        backup_group.save_group(self.path, group)
        loaded = backup_group.load_group(self.path)
        self.assertEqual(loaded, group)           # 含中文 id 无损

    def test_load_missing_returns_default_off(self):
        loaded = backup_group.load_group(self.path)
        self.assertEqual(loaded, {"version": 1, "enabled": False,
                                  "node_ids": []})

    def test_atomic_write_no_tmp_residue_and_keeps_old_on_failure(self):
        backup_group.save_group(self.path, {"version": 1, "enabled": False,
                                            "node_ids": ["a"]})
        with open(self.path, encoding="utf-8") as f:
            first = f.read()
        self.assertFalse(os.path.exists(self.path + ".tmp"))   # 无残留
        with mock.patch.object(backup_group.os, "replace",
                               side_effect=OSError("盘满")):
            with self.assertRaises(OSError):
                backup_group.save_group(self.path, {"version": 1,
                                                    "enabled": True,
                                                    "node_ids": ["a", "b"]})
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), first)     # 原文件完好无损

    def test_save_rejects_invalid_structure(self):
        bad = [{"version": 2, "enabled": False, "node_ids": []},       # 版本错
               {"version": 1, "enabled": "yes", "node_ids": []},       # enabled 非 bool
               {"version": 1, "enabled": 1, "node_ids": []},
               {"version": 1, "enabled": False, "node_ids": "ab"},     # 非列表
               {"version": 1, "enabled": False, "node_ids": [1]},      # 非 str
               {"version": 1, "enabled": False, "node_ids": ["", "  "]},
               {"version": 1, "enabled": False, "node_ids": ["a", "a"]},  # 重复
               "not a dict"]
        for g in bad:
            with self.assertRaises(backup_group.BackupGroupError):
                backup_group.save_group(self.path, g)
        self.assertFalse(os.path.exists(self.path))   # 一个字节都没写过

    def test_load_rejects_corrupt_file_loudly(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{\"version\": 1, \"enabled\": tru}")     # 坏 JSON
        with self.assertRaises(backup_group.BackupGroupError):
            backup_group.load_group(self.path)
        # 结构非法（合法 JSON 但字段坏）同样拒读，不静默重置
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"version": 9, "enabled": False,
                       "node_ids": []}, f)
        with self.assertRaises(backup_group.BackupGroupError):
            backup_group.load_group(self.path)

    def test_enable_gate_below_two_nodes(self):
        """<2 拒绝开启并说明；关闭永远成功（§5.2）。"""
        for ids in ([], ["only-one"]):
            group = {"version": 1, "enabled": False, "node_ids": list(ids)}
            new_group, ok, reason = backup_group.set_enabled(group, True)
            self.assertFalse(ok)
            self.assertEqual(new_group, group)          # 原样返回，未被改
            self.assertTrue(reason)                     # 有给人看的原因
            self.assertIn("没有容灾意义", reason)
            self.assertEqual(group["enabled"], False)   # 入参也没被改
        group = {"version": 1, "enabled": False, "node_ids": ["a", "b"]}
        new_group, ok, _ = backup_group.set_enabled(group, True)
        self.assertTrue(ok)
        self.assertTrue(new_group["enabled"])
        new_group, ok, _ = backup_group.set_enabled(new_group, False)
        self.assertTrue(ok)
        self.assertFalse(new_group["enabled"])

    def test_prune_group_deleted_and_banned(self):
        """清组：被删/被拉黑 → 移出 + 待提示清单；余序与 enabled 原样。"""
        group = {"version": 1, "enabled": True,
                 "node_ids": ["n1", "n2", "n3", "n4"]}
        states = {"n1": {"exists": True, "banned": True},    # 拉黑 → banned
                  "n2": {"exists": True, "banned": False},   # 在 → 留
                  # n3 不在字典 = 已删除
                  "n4": {"exists": False, "banned": False}}  # 显式不存在
        cleaned, removed = backup_group.prune_group(group, states)
        self.assertEqual(cleaned["node_ids"], ["n2"])
        self.assertEqual(cleaned["enabled"], True)      # 开关不替用户动
        self.assertEqual(group["node_ids"],
                         ["n1", "n2", "n3", "n4"])      # 入参未被改
        self.assertEqual(removed, [
            {"node_id": "n1", "reason": backup_group.REASON_BANNED},
            {"node_id": "n3", "reason": backup_group.REASON_DELETED},
            {"node_id": "n4", "reason": backup_group.REASON_DELETED}])
        self.assertEqual(backup_group.removed_reason_text("banned"), "节点已拉黑")
        self.assertEqual(backup_group.removed_reason_text("deleted"), "节点已删除")

    def test_backup_group_path_sits_with_sources_json(self):
        """落点与 sources.json 同目录（store.path 推导口径同 ui 层）。"""
        p = backup_group.backup_group_path("/data/gate/client.db")
        self.assertEqual(os.path.dirname(p), "/data/gate")
        self.assertEqual(os.path.basename(p), "backup_group.json")
        self.assertEqual(os.path.basename(
            backup_group.backup_group_path("")), "backup_group.json")

    def test_file_backed_controller_end_to_end(self):
        """集成形态：真文件做组存储 + 假件做会话——开关/组序全部现读文件。"""
        path = self.path
        group = {"version": 1, "enabled": True, "node_ids": ["B", "C"]}
        backup_group.save_group(path, group)
        nodes = [FakeNode("A"), FakeNode("B", "乙"), FakeNode("C", "丙")]
        store, orch = FakeStore(nodes), FakeOrch(active="A")
        ops = failover.FailoverOps(
            enabled=lambda: backup_group.load_group(path).get("enabled", False),
            group_node_ids=lambda: backup_group.load_group(path).get("node_ids", []),
            get_node=store.get_node,
            last_link_ok=lambda nid: None,
            active_node_id=lambda: orch.active_node_id,
            disconnect=orch.disconnect,
            connect=orch.connect,
            record_sample=lambda nid, success, detail: store.samples.append(
                {"node_id": nid, "kind": "failover", "success": success,
                 "detail": detail}),
            update_node_extra=lambda nid, extra: store.upsert_node(
                type(nodes[0])(nid, extra=extra)),
            alert=lambda p: None,
        )
        ctrl = failover.FailoverController(ops)
        ctrl.on_health_dead("A")
        self.assertEqual(orch.calls, [("disconnect", None), ("connect", "B")])
        # 文件里把开关关掉：下一个信号零动作
        group["enabled"] = False
        backup_group.save_group(path, group)
        orch.calls.clear()
        self.assertIsNone(ctrl.on_dropped("A"))
        self.assertEqual(orch.calls, [])


# ---------------------------------------------------------------------------
# 禁词自检（§5.5）
# ---------------------------------------------------------------------------

def _impl_sources():
    """本段实现文件（backup_group.py / failover.py / proto 源码）。"""
    paths = [os.path.join(BASE, "backup_group.py"),
             os.path.join(BASE, "failover.py"),
             os.path.join(BASE, "proto", "failover_zone_proto.py")]
    out = []
    for p in paths:
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                out.append((os.path.basename(p), f.read()))
    return out


class ForbiddenWordsTests(unittest.TestCase):
    """§5.5 四禁词自检：实现文件里两个英文禁词零出现；且不存在按延迟
    排序备用组 / 定时对比换节点的代码路径（无 sort、无定时器）。

    禁词在测试里拆开拼写，避免本文件自身成为全库 grep 的命中项
    （§5.5 的 grep 验收在集成时由审查方对全库再跑一遍）。"""

    W1 = "connect_" + "best"
    W2 = "connect_" + "fast" + "est"

    def test_two_english_forbidden_words_zero_occurrence(self):
        for fname, text in _impl_sources():
            low = text.lower()
            self.assertNotIn(self.W1, low, f"{fname} 出现禁词 {self.W1}")
            self.assertNotIn(self.W2, low, f"{fname} 出现禁词 {self.W2}")

    def test_no_latency_sort_and_no_timer_path(self):
        for fname, text in _impl_sources():
            self.assertNotIn("sorted(", text, f"{fname} 出现排序调用")
            self.assertNotIn(".sort(", text, f"{fname} 出现排序调用")
            for timer in ("QTimer", "setInterval", "callLater", "Timer("):
                self.assertNotIn(timer, text,
                                 f"{fname} 出现定时器（定时对比换节点路径）")

    def test_group_list_locks_manual_order(self):
        """④区原型把列表自动排序显式关死（setSortingEnabled(False)），
        顺序只随人工拖动/上移/下移走。"""
        proto = dict(_impl_sources()).get("failover_zone_proto.py")
        if proto is None:      # 集成后 proto 不随拷贝——本项只在 work21/ 跑
            self.skipTest("proto 源码不在（集成形态）")
        self.assertIn("setSortingEnabled(False)", proto)

    def test_ops_interface_has_no_latency_lever(self):
        names = [f.name.lower() for f in dataclasses.fields(failover.FailoverOps)]
        for banned_word in ("latency", "best", "fast", "sort", "speed"):
            self.assertFalse(any(banned_word in n for n in names))


# ---------------------------------------------------------------------------
# wiring.diff 对实树可应用（有 client-dev 时才跑）
# ---------------------------------------------------------------------------

CLIENT_DEV = os.path.expanduser("~/workspace/vpn-project/client-dev")
WIRING_DIFF = os.path.join(BASE, "wiring.diff")


class WiringDiffAppliesTests(unittest.TestCase):
    """「拷入 pristine 副本 + patch --dry-run」验证 diff 与树一致——
    审查方按 DESIGN 对照表 + diff + WIRING.md 集成前的第一道保险。"""

    def setUp(self):
        if not os.path.isdir(CLIENT_DEV):
            self.skipTest("client-dev 不在本机（纯 work21 环境）")
        if not os.path.exists(WIRING_DIFF):
            self.skipTest("wiring.diff 不在")
        if shutil.which("patch") is None:
            self.skipTest("无 patch 工具")
        self.tmp = tempfile.mkdtemp(prefix="wiring_check_")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        for rel in ("client/ui/app.py", "client/ui/usage_window.py",
                    "client/ui/cli.py", "client/core/__init__.py",
                    "client/__init__.py"):
            src = os.path.join(CLIENT_DEV, rel)
            dst = os.path.join(self.tmp, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy(src, dst)

    def test_patch_applies_clean_and_wires_lands(self):
        """真应用 wiring.diff（非 dry-run）：rc=0 + 新文件落地 + 接线标记
        在补丁后的副本里可 grep 到。"""
        # 先干跑校验（-i 显式给补丁文件；不给 -i patch 会读 stdin，空转 rc=0）
        dry = subprocess.run(["patch", "--dry-run", "-p1", "-i", WIRING_DIFF,
                              "-d", self.tmp], capture_output=True, text=True)
        self.assertEqual(dry.returncode, 0,
                         f"diff 与树不匹配：\n{dry.stdout}\n{dry.stderr}")
        r = subprocess.run(["patch", "-p1", "-i", WIRING_DIFF, "-d", self.tmp],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, f"应用失败：\n{r.stdout}\n{r.stderr}")
        # 新文件落地且可编译
        for fn in ("backup_group.py", "failover.py"):
            src = os.path.join(self.tmp, "client", "core", fn)
            self.assertTrue(os.path.exists(src), f"{fn} 未被 patch 创建")
            with open(src, encoding="utf-8") as f:
                compile(f.read(), fn, "exec")
        # 三个既有文件的接线标记在位
        def _read(rel):
            with open(os.path.join(self.tmp, rel), encoding="utf-8") as f:
                return f.read()
        uw = _read("client/ui/usage_window.py")
        app = _read("client/ui/app.py")
        cli = _read("client/ui/cli.py")
        self.assertIn("self._setup_failover()", uw)
        self.assertIn("REASON_ENGINE_DROPPED, ev.node_id", uw)
        self.assertIn("backend._failover_feed = usage._failover_feed", app)
        self.assertIn("failover.REASON_HEALTH_DEAD, node_id", app)
        self.assertIn("failover.clear_mark_on_link_success(store, node.id, True)",
                      cli)
        # 补丁后的三个文件仍是合法 Python
        for name, text in (("usage_window.py", uw), ("app.py", app),
                           ("cli.py", cli)):
            compile(text, name, "exec")


# ---------------------------------------------------------------------------
# ④区原型 offscreen 自检（有 PySide6 时才跑；FAILOVER_QT_PYTHON 可指定
# 带 Qt 的解释器，如临时 venv）
# ---------------------------------------------------------------------------

QT_PYTHON = os.environ.get("FAILOVER_QT_PYTHON", sys.executable)
PROTO_PATH = os.path.join(BASE, "proto", "failover_zone_proto.py")


def _pyside6_available() -> bool:
    try:
        return subprocess.run([QT_PYTHON, "-c", "import PySide6"],
                              capture_output=True).returncode == 0
    except OSError:
        return False


@unittest.skipUnless(os.path.exists(PROTO_PATH), "proto 源码不在（集成形态）")
@unittest.skipUnless(_pyside6_available(), "缺 PySide6（可用 FAILOVER_QT_PYTHON "
                                           "指定带 Qt 的解释器）")
class ProtoOffscreenTests(unittest.TestCase):

    def test_selfcheck_passes_offscreen(self):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
        r = subprocess.run([QT_PYTHON, PROTO_PATH, "--selfcheck"],
                           capture_output=True, text=True, env=env,
                           timeout=120)
        self.assertEqual(r.returncode, 0,
                         f"proto 自检失败：\n{r.stdout}\n{r.stderr}")
        self.assertIn("selfcheck 全绿", r.stdout)


if __name__ == "__main__":
    unittest.main()
