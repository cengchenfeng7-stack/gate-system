#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task15 异常覆盖测试（test_faults.py）
======================================
对照 work15/DESIGN.md §2 异常分类表逐项断言。三層断言结构，口径不许混：

1. **替身断期望**：SessionTwin/FakeEngine/FakeProbe 实现 DESIGN §1 的期望
   口径，断言异常路径的行为（显式报错、回 IDLE、不留半开隧道、亮告警）；
2. **现状断事实**：DB 损坏在临时目录造真损坏文件，按 store.py 打开口径
   探测 sqlite 现状语义（0 字节静默空库 / 坏头 DatabaseError），这些是
   SQLite 的确定性行为，直接断言不迂回；
3. **现状断锚点**：client-dev 现状一律走 FILE_ANCHORS 文件锚点（读文件
   断言落点存在，不 import client 包）；现状与期望有差距的，断言
   「缺口被如实记录在 DESIGN.md」（GAPS ↔ DESIGN 登记互验）——**不为绿
   改口径迁就现状**。

端口口径见 DESIGN §4：只用 29311 起的冷门段，避开 7897/7898、
10818-10829、18089-18095（0.9.0 撞车教训）。全程不连外网。
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest

from client.core import faults

HERE = os.path.dirname(os.path.abspath(__file__))
DESIGN_PATH = os.path.join(os.path.dirname(faults.__file__), "DESIGN.md")
SOAK_PATH = os.path.join(os.path.dirname(faults.__file__), "soak.py")


def _design_text() -> str:
    with open(DESIGN_PATH, "r", encoding="utf-8") as f:
        return f.read()


def _client_root() -> str:
    return faults.client_root()


CLIENT_ROOT_EXISTS = os.path.isdir(_client_root())


# ---------------------------------------------------------------------------
# 假世界搭建
# ---------------------------------------------------------------------------
def build_world(engine_behavior="normal", probe_behavior="normal"):
    """一套可连接的假世界：store 带两个假节点 + normal 引擎 + normal probe。
    返回 (twin, store, engine, probe, node_id)。"""
    store = faults.TwinStore()
    engine = faults.FakeEngine(behavior=engine_behavior)
    probe = faults.FakeProbe(behavior=probe_behavior)
    store.upsert_node(faults.make_node("node-ok", "fake-proto"))
    store.upsert_node(faults.make_node("node-banned", "fake-proto",
                                       banned=True))
    twin = faults.SessionTwin(store=store,
                              engines={"fake-proto": engine},
                              verifier=probe)
    return twin, store, engine, probe


class FakeOutcome:
    """最小 verify 结果替身（FaultEngineError 家族外的返回形态）。"""

    def __init__(self, alive=True, exit_ip="203.0.113.7", error=""):
        self.alive = alive
        self.exit_ip = exit_ip
        self.error = error


# ---------------------------------------------------------------------------
# 引擎异常（DESIGN §2.1）
# ---------------------------------------------------------------------------
class TestEngineFaults(unittest.TestCase):
    """E1-E5：启动失败 / 中途退出 / stop 残留 / 回调豁免。"""

    def test_e1_normal_connect_flow(self):
        """E1 达标基线：正常世界 connect → connected、状态 CONNECTED。"""
        twin, store, engine, probe = build_world()
        ev = twin.connect("node-ok")
        self.assertEqual(ev.kind, "connected")
        self.assertEqual(twin.state, faults.SessionTwin.CONNECTED)
        self.assertEqual(twin.active_node_id, "node-ok")
        kinds = [e.kind for e in twin.events]
        self.assertEqual(kinds, ["connect_started", "engine_started",
                                 "connected"])

    def test_e1_start_fail_is_explicit_error_and_stays_idle(self):
        """E1：启动失败 → 显式 error 事件、落失败样本、状态保持 IDLE、
        不留活动引擎（不假装成功）。"""
        twin, store, engine, probe = build_world(engine_behavior="start_fail")
        ev = twin.connect("node-ok")
        self.assertEqual(ev.kind, "error")
        self.assertIn("引擎启动失败", ev.detail)
        self.assertEqual(twin.state, faults.SessionTwin.IDLE)
        self.assertIsNone(twin.active_node_id)
        self.assertEqual(engine.live_count, 0)
        fails = [s for s in store.samples if not s.success]
        self.assertEqual(len(fails), 1)
        self.assertEqual(fails[0].kind, "connect")

    def test_e1_start_fail_then_other_node_connectable(self):
        """E1：启动失败后世界是干净的——换节点能连（不留脏状态）。"""
        twin, store, engine, probe = build_world(engine_behavior="start_fail")
        twin.connect("node-ok")
        engine.behavior = "normal"
        ev = twin.connect("node-ok")     # 失败后仍在 IDLE，可再连
        self.assertEqual(ev.kind, "connected")

    def test_e1_start_fail_error_names_cause(self):
        """E1：EngineError 的人话要求——失败文案点名成因（对照
        client-dev `_missing_binary_error` / 端口占用报错口径）。"""
        engine = faults.FakeEngine(behavior="start_fail")
        with self.assertRaises(faults.FaultEngineError) as ctx:
            engine.start(faults.make_node("node-ok", "fake-proto"))
        self.assertIn("启动失败注入", str(ctx.exception))

    def test_e2_mid_death_poll_emits_dropped_and_goes_idle(self):
        """E2：中途退出 → poll 上报 dropped、回 IDLE、清 active_node_id、
        落 drop 样本；引擎句柄收干净（无残留句柄）。"""
        twin, store, engine, probe = build_world(engine_behavior="mid_death")
        twin.connect("node-ok")
        engine.kill_active()
        ev = twin.poll()
        self.assertIsNotNone(ev)
        self.assertEqual(ev.kind, "dropped")
        self.assertIn("不会自动切换", ev.detail)
        self.assertEqual(twin.state, faults.SessionTwin.IDLE)
        self.assertIsNone(twin.active_node_id)
        drops = [s for s in store.samples if s.kind == "drop"]
        self.assertEqual(len(drops), 1)
        self.assertFalse(drops[0].success)
        self.assertEqual(engine.live_count, 0)

    def test_e2_poll_honest_when_running(self):
        """E2：引擎活着时 poll 返回 None（不误报）。"""
        twin, store, engine, probe = build_world()
        twin.connect("node-ok")
        self.assertIsNone(twin.poll())

    def test_e3_stop_residue_is_surfaced_not_swallowed(self):
        """GAP-E1 期望侧：stop 后进程残留 → 替身显式记 residue + 发 error
        告警（对照 client-dev 现状三处吞错，DESIGN §2.1-E3）。"""
        twin, store, engine, probe = build_world(
            engine_behavior="stop_residue")
        twin.connect("node-ok")
        twin.disconnect()
        # 显式亮告警：error 事件 + residues 登记
        errors = twin.events_of("error")
        self.assertEqual(len(errors), 1)
        self.assertIn("残留", errors[0].detail)
        self.assertIn("pid", errors[0].detail)
        self.assertEqual(len(twin.residues), 1)
        self.assertIn("不会自动重连/切换", errors[0].detail)
        # 会话状态照常回 IDLE（残留不阻塞状态机）
        self.assertEqual(twin.state, faults.SessionTwin.IDLE)

    def test_e3_stop_normal_no_residue(self):
        """GAP-E1 对照：正常 stop 无残留、无告警（告警只在真残留时亮）。"""
        twin, store, engine, probe = build_world()
        twin.connect("node-ok")
        twin.disconnect()
        self.assertEqual(twin.residues, [])
        self.assertEqual(twin.events_of("error"), [])

    def test_e5_engine_event_callback_exception_is_tolerated(self):
        """E5 豁免：on_event 回调抛错不弄丢已就绪会话（对照 client-dev
        singbox_engine.py:892-896 的注释豁免）。"""
        engine = faults.FakeEngine(behavior="normal")

        def bad_callback(node):
            raise RuntimeError("回调炸了")

        # 假引擎的 start 签名容忍回调注入并吞其异常（形状对齐真引擎）
        handle = engine.start(faults.make_node("node-ok", "fake-proto"),
                              on_event=bad_callback)
        self.assertIsNotNone(handle)
        self.assertEqual(engine.status(handle), "running")


# ---------------------------------------------------------------------------
# 网络断绝（DESIGN §2.3）
# ---------------------------------------------------------------------------
class TestNetworkFaults(unittest.TestCase):
    """N1-N5：探测超时 / 解析失败 / probe 抛错 / 健康分级 / 端口预检。"""

    def test_n1_probe_timeout_explicit_verify_failed(self):
        """N1（超时注入）：验出口失败 → 显式 verify_failed、断开回 IDLE、
        不留半开隧道、落失败样本。"""
        twin, store, engine, probe = build_world(probe_behavior="timeout")
        ev = twin.connect("node-ok")
        self.assertEqual(ev.kind, "verify_failed")
        self.assertIn("探测超时", ev.detail)
        self.assertIn("防假连", ev.detail)
        self.assertEqual(twin.state, faults.SessionTwin.IDLE)
        self.assertIsNone(twin.active_node_id)
        self.assertEqual(engine.live_count, 0)   # 引擎已被 teardown 收走
        fails = [s for s in store.samples if not s.success]
        self.assertEqual(len(fails), 1)

    def test_n1_probe_parse_fail_explicit_verify_failed(self):
        """N1（解析失败注入）：取不到出口 IP → 同口径显式失败、回 IDLE。"""
        twin, store, engine, probe = build_world(
            probe_behavior="parse_fail")
        ev = twin.connect("node-ok")
        self.assertEqual(ev.kind, "verify_failed")
        self.assertIn("解析失败", ev.detail)
        self.assertEqual(twin.state, faults.SessionTwin.IDLE)
        self.assertEqual(engine.live_count, 0)

    def test_n1_fake_probe_result_carries_error_kind(self):
        """GAP-N1 期望侧：假 probe 结果带 error_kind 分级字段
        （现状 client-dev 无此字段——见 FILE_ANCHORS + DESIGN 登记）。"""
        self.assertEqual(faults.FakeProbe(behavior="timeout")(None).error_kind,
                         "timeout")
        self.assertEqual(faults.FakeProbe(behavior="parse_fail")(None).error_kind,
                         "parse")

    def test_n2_health_grading_suspect_at_3(self):
        """N2：末尾连败 3 → suspect（对齐 health.py SUSPECT_FAILS=3）。"""
        samples = [{"ok": False}, {"ok": False}, {"ok": False}]
        self.assertEqual(faults.twin_health_detail(samples)["status"],
                         faults.HEALTH_SUSPECT)

    def test_n2_health_grading_dead_at_5(self):
        """N2：连败 5 → dead（对齐 health.py DEAD_FAILS=5）。"""
        samples = [{"ok": False}] * 5
        self.assertEqual(faults.twin_health_detail(samples)["status"],
                         faults.HEALTH_DEAD)

    def test_n2_health_recovery_resets_streak(self):
        """N2：任何一次成功清零连败（恢复即 ok）。"""
        samples = [{"ok": False}] * 4 + [{"ok": True}, {"ok": False}]
        detail = faults.twin_health_detail(samples)
        self.assertEqual(detail["status"], faults.HEALTH_OK)
        self.assertEqual(detail["fail_streak"], 1)

    def test_n2_health_unknown_when_no_samples(self):
        """N2：无样本 → unknown（不猜）。"""
        detail = faults.twin_health_detail([])
        self.assertEqual(detail["status"], faults.HEALTH_UNKNOWN)
        self.assertIsNone(detail["last_ok"])

    def test_n2_health_bad_sample_raises_not_swallowed(self):
        """N2：坏样本（缺 ok / 非 bool）当场抛错，不静默当成败
        （对齐 health.py `_sample_ok` 口径）。"""
        with self.assertRaises(ValueError):
            faults.twin_health_detail([{"ok": 1}])
        with self.assertRaises(ValueError):
            faults.twin_health_detail([{"nope": True}])

    def test_dead_grading_never_triggers_auto_action(self):
        """N2 铁律反向断言：dead 分级本身不触发任何会话动作——
        twin 无自动方法、dead 样本喂给 poll/poll 只看引擎死活。"""
        twin, store, engine, probe = build_world()
        twin.connect("node-ok")
        dead = [{"ok": False}] * 10
        self.assertEqual(faults.twin_health_detail(dead)["status"],
                         faults.HEALTH_DEAD)
        # 引擎进程活着 → 即使健康全 dead，poll 也不动会话
        self.assertIsNone(twin.poll())
        self.assertEqual(twin.state, faults.SessionTwin.CONNECTED)

    def test_n3_probe_raise_is_caught_no_half_open_tunnel(self):
        """GAP-E2 期望侧：probe 抛错（而非返回失败）→ 替身接住、teardown、
        显式 error 事件、回 IDLE，**绝无半开隧道**。"""
        twin, store, engine, probe = build_world(probe_behavior="raise")
        ev = twin.connect("node-ok")
        self.assertEqual(ev.kind, "error")
        self.assertIn("验出口异常", ev.detail)
        self.assertEqual(twin.state, faults.SessionTwin.IDLE)
        self.assertIsNone(twin.active_node_id)
        self.assertEqual(engine.live_count, 0)
        self.assertEqual(engine.stop_calls, 1)   # 引擎被显式停掉

    def test_n5_port_precheck_refuses_occupied(self):
        """N5/三件套①：端口被占 → 预检显式拒绝（bind 探测，测完即关）。"""
        port = faults.pick_free_port()
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            blocker.bind(("127.0.0.1", port))
            blocker.listen(1)
            occupied = faults.twin_check_ports_free([port])
            self.assertEqual(len(occupied), 1)
            self.assertEqual(occupied[0]["port"], port)
        finally:
            blocker.close()

    def test_n5_port_precheck_passes_free_port(self):
        port = faults.pick_free_port()
        self.assertEqual(faults.twin_check_ports_free([port]), [])


# ---------------------------------------------------------------------------
# 会话状态机与铁律（DESIGN §1，反向断言）
# ---------------------------------------------------------------------------
class TestSessionTwinContract(unittest.TestCase):
    """预检拒绝 + 铁律反向断言：异常处置的终点永远是回 IDLE。"""

    def test_double_connect_refused(self):
        twin, _, _, _ = build_world()
        twin.connect("node-ok")
        with self.assertRaises(faults.TwinError) as ctx:
            twin.connect("node-banned")
        self.assertIn("已有活动连接", str(ctx.exception))

    def test_banned_node_refused(self):
        twin, _, _, _ = build_world()
        with self.assertRaises(faults.TwinError) as ctx:
            twin.connect("node-banned")
        self.assertIn("拉黑", str(ctx.exception))
        self.assertEqual(twin.events, [])    # 预检在 connect_started 之前

    def test_unknown_node_refused(self):
        twin, _, _, _ = build_world()
        with self.assertRaises(faults.TwinError) as ctx:
            twin.connect("ghost")
        self.assertIn("节点不存在", str(ctx.exception))

    def test_unknown_protocol_refused(self):
        twin, _, _, _ = build_world()
        twin.store.upsert_node(faults.make_node("node-x", "weird-proto"))
        with self.assertRaises(faults.TwinError) as ctx:
            twin.connect("node-x")
        self.assertIn("暂无引擎", str(ctx.exception))

    def test_disconnect_when_idle_is_honest(self):
        twin, _, _, _ = build_world()
        ev = twin.disconnect()
        self.assertEqual(ev.kind, "disconnected")
        self.assertIn("本来就没有活动连接", ev.detail)

    def test_no_auto_methods_on_twin(self):
        """铁律反向断言：替身公开面不存在任何自动切换/重连方法。"""
        twin = faults.SessionTwin()
        for name in faults.SessionTwin.FORBIDDEN_METHODS:
            self.assertFalse(hasattr(twin, name),
                             f"替身出现了禁止的自动方法: {name}")

    def test_no_auto_methods_in_client_orchestrator(self):
        """铁律反向断言（现状侧）：client-dev 编排层不存在自动切换/
        自动重连的方法定义（负向文件锚点）。"""
        assume_root = unittest.skipIf(not CLIENT_ROOT_EXISTS,
                                      "client-dev 不在本机，跳过现状锚点")
        assume_root(self)
        for rel, needle, negative, err in faults.iter_anchor_failures():
            if rel == "client/core/orchestrator.py" and negative:
                self.fail(f"铁律现状漂移: {rel} 出现 {needle!r}")

    def test_exception_never_leads_to_new_connection(self):
        """铁律反向断言：把各种异常路径走一遍后，替身不产生任何
        connect_started 之外的「自发新连接」——事件流里每个 connected
        前面都有且只有一次显式 connect() 调用（计数对账）。"""
        for behavior in ("start_fail", "timeout", "parse_fail", "raise"):
            twin, store, engine, probe = build_world()
            if behavior in ("start_fail",):
                engine.behavior = behavior
            else:
                probe.behavior = behavior
            twin.connect("node-ok")
            connect_calls = len([e for e in twin.events
                                 if e.kind == "connect_started"])
            connected = len(twin.events_of("connected"))
            self.assertEqual(connected, 0,
                             f"{behavior} 注入下不应有 connected")
            # 没有任何后续自动动作：除首发事件外无新事件产生
            twin.poll()
            twin.disconnect()
            self.assertEqual(
                len([e for e in twin.events
                     if e.kind == "connect_started"]), connect_calls,
                f"{behavior} 注入后出现自发的连接尝试（违反铁律）")


# ---------------------------------------------------------------------------
# DB 损坏（DESIGN §2.2）
# ---------------------------------------------------------------------------
class TestDbCorruption(unittest.TestCase):
    """D1-D4：临时目录里造真损坏文件，断言现状事实 + DESIGN 登记。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="work15-db-")
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def _db_path(self, name="client.db", rows=5):
        path = os.path.join(self.dir, name)
        faults.make_db(path, rows=rows)
        return path

    def test_d1_healthy_db_baseline(self):
        """基线：健康库打开 → 5 行都在（对照口径先跑通）。"""
        path = self._db_path()
        beh = faults.open_behavior(path)
        self.assertTrue(beh["opened"])
        self.assertTrue(beh["header_magic_ok"])
        self.assertEqual(beh["node_count"], 5)
        self.assertIsNone(beh["error"])

    def test_d1_truncate_to_zero_silently_empties(self):
        """GAP-D1 现状事实：截断成 0 字节 → SQLite 当合法新库静默打开，
        数据「消失」且无任何报错——store.py 现状（IF NOT EXISTS + 无校验）
        会据此新建空库覆盖，数据无声丢失。这是 SQLite 确定性行为，
        直接断言；缺口登记见 test_gaps_documented_in_design。"""
        path = self._db_path()
        faults.corrupt_truncate(path, keep_bytes=0)
        self.assertEqual(os.path.getsize(path), 0)
        beh = faults.open_behavior(path)
        self.assertTrue(beh["opened"],
                        "0 字节文件被 SQLite 当新库打开（现状事实）")
        self.assertIsNone(beh["error"])
        self.assertEqual(beh["node_count"], 0,
                         "旧数据无声丢失：静默空库（GAP-D1 现状）")
        self.assertFalse(beh["header_magic_ok"])

    def test_d1_truncate_partial_header_lost(self):
        """GAP-D1 现状事实（部分截断，保留少量字节）：头部不完整 →
        打开期直接炸原始错误（现状=无校验无修复，原始栈冒出）。"""
        path = self._db_path()
        faults.corrupt_truncate(path, keep_bytes=16)   # 头部只剩一半
        beh = faults.open_behavior(path)
        self.assertFalse(beh["opened"])
        self.assertIsNotNone(beh["error"])
        self.assertIn("DatabaseError", beh["error"])

    def test_d2_overwritten_header_raises_raw_error(self):
        """GAP-D2 现状事实：覆写文件头 → 打开口径炸 sqlite3.DatabaseError
        原始错误（「file is not a database」），无翻译无人话——现状
        CLI/GUI 构造点不捕（FILE_ANCHORS: ui/cli.py、ui/app.py）。"""
        path = self._db_path()
        faults.corrupt_header(path)
        beh = faults.open_behavior(path)
        self.assertFalse(beh["opened"])
        self.assertIsNotNone(beh["error"])
        self.assertIn("DatabaseError", beh["error"])
        self.assertFalse(beh["header_magic_ok"])
        # 文件本体未被打开动作改动（拒开≠重建）
        with open(path, "rb") as f:
            head = f.read(len(faults.SQLITE_HEADER_MAGIC))
        self.assertNotEqual(head, faults.SQLITE_HEADER_MAGIC)

    def test_d4_uncommitted_write_rolls_back_no_half_record(self):
        """D4 达标部分：commit 前断连 → 回滚，不留半条（SQLite 事务原子，
        store.py 每次写入 commit 的口径依赖这层）。"""
        path = os.path.join(self.dir, "journal.db")
        faults.make_db(path, rows=2)
        conn = sqlite3.connect(path)
        conn.execute("INSERT INTO nodes (id, name) VALUES ('half', '半条')")
        conn.close()                        # 未 commit → 回滚
        beh = faults.open_behavior(path)
        self.assertTrue(beh["opened"])
        self.assertEqual(beh["node_count"], 2,
                         "半条不存在：断连回滚（达标部分）")

    def test_d4_page_corruption_detected_by_integrity_check(self):
        """GAP-D4 现状事实：页级损坏（头部完好）→ 损坏必被报出——
        或由 integrity_check 返回非 ok 行，或 integrity_check 自身炸出
        DatabaseError（本机 sqlite 实测两种形态都会出现）；store.py 现状
        无任何自检路径（负向锚点 user_version/integrity_check 缺失），
        用户只会在日后查询期撞上原始错误。"""
        path = self._db_path()
        faults.corrupt_middle(path)
        conn = sqlite3.connect(path)
        detected = False
        detail = ""
        try:
            result = conn.execute("PRAGMA integrity_check").fetchall()
            joined = " ".join(str(r[0]) for r in result)
            detected = joined.strip().lower() != "ok"
            detail = joined[:200]
        except sqlite3.DatabaseError as exc:
            detected = True                 # 自检本身炸原始错误=损坏被报出
            detail = f"integrity_check 抛 DatabaseError: {exc}"
        finally:
            conn.close()
        self.assertTrue(detected,
                        "页级损坏必须被报出（修复路径依赖的事实）: " + detail)

    def test_d3_no_schema_version_in_store(self):
        """GAP-D3 现状锚点（负向）：store.py 全文无 user_version /
        integrity_check / quick_check——无版本机制、无自检路径
        （designed 断言在 DESIGN 登记互验）。"""
        if not CLIENT_ROOT_EXISTS:
            self.skipTest("client-dev 不在本机，跳过现状锚点")
        root = _client_root()
        with open(os.path.join(root, "client/core/store.py"),
                  encoding="utf-8", errors="replace") as f:
            text = f.read()
        for needle in ("user_version", "integrity_check", "quick_check"):
            self.assertNotIn(needle, text,
                             f"store.py 出现 {needle!r}——GAP-D3/D4 现状"
                             "漂移，请更新 DESIGN §2.2")


# ---------------------------------------------------------------------------
# 缺口登记 ↔ DESIGN.md 互验（「不为绿改口径迁就现状」的机制）
# ---------------------------------------------------------------------------
class TestDesignRegistry(unittest.TestCase):
    """GAPS 每条必须如实记录在 DESIGN.md；DESIGN 的总口径与铁律必须在场。"""

    def test_gaps_documented_in_design(self):
        text = _design_text()
        for gap in faults.GAPS:
            self.assertIn(gap["id"], text,
                          f"{gap['id']} 未登记进 DESIGN.md：{gap['title']}")
            # 登记处必须带修责（该由哪一段修）——验收口径要求按图判断
            row_zone = text[text.index(gap["id"]):
                            text.index(gap["id"]) + 400]
            self.assertTrue(
                ("修责" in row_zone) or ("段" in row_zone),
                f"{gap['id']} 的登记缺少修责段位")

    def test_design_expected_criteria_present(self):
        text = _design_text()
        for needle in ("期望行为总口径", "不静默吞", "不丢已有数据",
                       "绝不因异常自动切换", "回 IDLE"):
            self.assertIn(needle, text, f"DESIGN 缺总口径关键词: {needle}")

    def test_design_three_categories_present(self):
        text = _design_text()
        for needle in ("引擎异常", "DB 损坏", "网络断绝"):
            self.assertIn(f"### 2", text)
            self.assertIn(needle, text)

    def test_design_reachable_anchors_sampled(self):
        """DESIGN 里引用的 client-dev 落点抽查：文件名在场（防编造的
        文档级抽查；逐子串锚点由 FILE_ANCHORS 负责）。"""
        text = _design_text()
        for needle in ("engines/singbox_engine.py", "core/orchestrator.py",
                       "core/health.py", "core/store.py", "core/verify.py",
                       "ui/cli.py", "ui/app.py"):
            self.assertIn(needle, text, f"DESIGN 未引用真实落点: {needle}")

    def test_design_soak_criteria_present(self):
        text = _design_text()
        for needle in ("子进程", "fd", "RSS", "DB 文件大小", "判据",
                       "真引擎模式", "退出码"):
            self.assertIn(needle, text, f"DESIGN 长跑设计缺关键词: {needle}")

    def test_design_ports_policy(self):
        text = _design_text()
        self.assertIn("7897", text)
        self.assertIn("10818", text)
        self.assertIn("18089", text)
        self.assertIn("29311", text)


# ---------------------------------------------------------------------------
# client-dev 现状文件锚点（防编造）
# ---------------------------------------------------------------------------
@unittest.skipIf(not CLIENT_ROOT_EXISTS,
                 "client-dev 不在本机（设 GATE_CLIENT_DEV_ROOT 指向它）")
class TestFileAnchors(unittest.TestCase):
    """现状断言全部走文件锚点：FILE_ANCHORS 必须逐条命中，
    FILE_NEGATIVE_ANCHORS 必须逐条不命中。"""

    def test_all_positive_anchors_hit(self):
        failures = [f for f in faults.iter_anchor_failures()
                    if not f[2]]
        self.assertEqual(failures, [],
                         "正向锚点失守（现状漂移或编造）: " +
                         "; ".join(f"{r}:{n[:30]} {e}" for r, n, _, e
                                   in failures))

    def test_all_negative_anchors_hold(self):
        failures = [f for f in faults.iter_anchor_failures() if f[2]]
        self.assertEqual(failures, [],
                         "负向锚点失守（现状漂移）: " +
                         "; ".join(f"{r}:{n[:30]} {e}" for r, n, _, e
                                   in failures))

    def test_store_open_pattern_anchor(self):
        """GAP-D1/D2 现状锚：store.py 打开期「连接即建表、无校验」的
        具体语句在场。"""
        root = _client_root()
        with open(os.path.join(root, "client/core/store.py"),
                  encoding="utf-8", errors="replace") as f:
            text = f.read()
        self.assertIn("self._conn = sqlite3.connect(path, "
                      "check_same_thread=False)", text)
        self.assertIn("self._conn.executescript(_SCHEMA)", text)

    def test_wiring_store_construction_uncaught(self):
        """GAP-D2 现状锚：CLI/GUI 构造点 `store = Store(args.db)` 在场
        （两处均无 try 包裹——若后续段修了行为本体，此锚会红，届时请
        同步更新 DESIGN §2.2-D2 的现状列）。"""
        root = _client_root()
        for rel in ("client/ui/cli.py", "client/ui/app.py"):
            with open(os.path.join(root, rel), encoding="utf-8",
                      errors="replace") as f:
                self.assertIn("Store(resolve_db_path(explicit_db=args.db",
                              f.read(), rel)

    def test_engine_swallow_chain_anchor(self):
        """GAP-E1 现状锚：singbox_engine.py 的 _terminate 宽 except 在场
        （二次超时吞错点）；stop 先摘句柄的顺序在场。"""
        root = _client_root()
        with open(os.path.join(root, "client/engines/singbox_engine.py"),
                  encoding="utf-8", errors="replace") as f:
            text = f.read()
        self.assertIn("except (OSError, subprocess.TimeoutExpired, "
                      "ValueError):", text)
        stop_body = text[text.index("def stop(self, handle) -> None"):]
        self.assertLess(stop_body.index("self._untrack(handle)"),
                        stop_body.index("self._terminate(proc)"),
                        "stop 应先摘句柄再 terminate（GAP-E1 现状链）")

    def test_version_baseline(self):
        """版本针一致性（REVIEW_FAIL 适配 4）：client/VERSION 实读 ==
        faults.FILE_ANCHORS 的版本针（针值随集成推进同步改，本测试只断
        两者一致，不写死版本号）；DESIGN 仍声明 task15 审计基线 0.17.0
        （历史事实，不随针漂移）。版本推进时：改 faults.py 针值即可，
        行号落点漂移先核对函数名再动 DESIGN 现状列。"""
        root = _client_root()
        with open(os.path.join(root, "client/VERSION"), encoding="utf-8") as f:
            version = f.read().strip()
        needle = faults.FILE_ANCHORS["client/VERSION"][0]
        self.assertEqual(version, needle,
                         "client/VERSION 与版本针不一致：改 faults.py 针值"
                         "（REVIEW_FAIL 适配 4），不要改假数据")
        self.assertIn("0.17.0", _design_text())


# ---------------------------------------------------------------------------
# soak.py 原型集成测试
# ---------------------------------------------------------------------------
class TestSoakDryRun(unittest.TestCase):
    """长跑原型：假引擎干跑满轮 + 报告判据（真引擎模式本段不跑）。"""

    def test_dry_run_full_rounds_report_pass(self):
        import shutil
        out_dir = tempfile.mkdtemp(prefix="work15-soak-")
        self.addCleanup(shutil.rmtree, out_dir, ignore_errors=True)
        report = os.path.join(out_dir, "report.json")
        proc = subprocess.run(
            [sys.executable, SOAK_PATH,
             "--rounds", "6", "--interval", "0", "--report", report],
            capture_output=True, text=True, timeout=120, cwd=HERE)
        self.assertEqual(proc.returncode, 0,
                         f"soak 干跑失败: {proc.stderr or proc.stdout}")
        with open(report, encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["mode"], "fake")
        self.assertGreaterEqual(data["rounds_run"], 6)
        self.assertTrue(data["criteria"]["rounds_complete"]["pass"])
        self.assertTrue(data["criteria"]["subprocess_leak"]["pass"])
        self.assertTrue(data["criteria"]["fd_leak"]["pass"])
        self.assertTrue(data["criteria"]["db_growth"]["pass"])
        self.assertIn("stdout", data)     # 人话小结同时回执

    def test_real_mode_refused(self):
        """真引擎模式本段只写入口与判读口径——执行被拒、退出码 3、
        不跑任何真连接。"""
        proc = subprocess.run(
            [sys.executable, SOAK_PATH,
             "--mode", "real", "--rounds", "1"],
            capture_output=True, text=True, timeout=60, cwd=HERE)
        self.assertEqual(proc.returncode, 3)
        self.assertIn("本段不真跑", proc.stdout + proc.stderr)
        self.assertIn("判读口径", proc.stdout + proc.stderr)

    def test_bad_args_exit_code_2(self):
        proc = subprocess.run(
            [sys.executable, SOAK_PATH,
             "--rounds", "0"], capture_output=True, text=True, timeout=60,
            cwd=HERE)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()


# 集成适配（work15 原件 → client-dev 集成版的全部改动，审查方留痕）：
# 1. import faults 改为 from client.core import faults（REVIEW_FAIL 适配 2）。
# 2. DESIGN_PATH 改为 faults.__file__ 所在目录（client/core/DESIGN.md）。
# 3. soak 子进程路径改用 SOAK_PATH（faults.__file__ 所在目录，client/core/soak.py）。
# 其余与原件逐行一致；VERSION 针为 0.20.0（集成版本号，适配 4）。
