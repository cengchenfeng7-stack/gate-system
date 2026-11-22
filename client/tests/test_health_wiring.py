#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task5 集成接线测试（test_health_wiring.py）
===========================================
只测 task5 落地时新加的接线行为，health 模块本身的 47 个测试在
test_health.py。全部离线：端口占用用真实 bind 本地临时端口制造
（红线允许）；GUI 部分用 offscreen Qt + 假 verifier。

覆盖：
1. 引擎守卫：SingBoxEngine.start() 在会话端口被占时直接报人话错误。
2. CLI：connect 预检（被占 → 退出码 2）、系统代理冲突只警告不阻断、
   status 两行体检。
3. GUI：connect_selected 预检（被占 → 告警条 + 不发起连接）、
   健康探测结果落地（suspect/dead/探测出错/会话已换丢弃样本）、
   系统代理复查落地、健康结果不误清用户任务的 _busy。
"""
from __future__ import annotations

import io
import os
import socket
import sys
import tempfile
import unittest
from contextlib import contextmanager, redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from client.core.model import ExitProfile, Node  # noqa: E402
from client.core.orchestrator import Orchestrator  # noqa: E402
from client.core.store import Store  # noqa: E402
from client.core.verify import VerifyOutcome  # noqa: E402
from client.engines.base import EngineError  # noqa: E402
from client.engines.singbox_engine import SingBoxEngine  # noqa: E402
from client.tests.test_cli import (  # noqa: E402
    FakeEngine, fake_probe_factory, run_cli)
from client.ui import app as app_mod  # noqa: E402
from client.ui import cli  # noqa: E402

try:
    from PySide6.QtWidgets import QApplication
    _QT = True
except ImportError:
    _QT = False

VLESS_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


@contextmanager
def listener(host: str, port: int):
    """在本机临时端口起一个真实 TCP 监听（制造占用场景）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind((host, port))
    sock.listen(5)
    try:
        yield sock
    finally:
        sock.close()


def free_port() -> int:
    """拿一个当前空闲的本地端口（bind(0) 后立刻关）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def vless_node() -> Node:
    return Node(id="manual:vless:example.com:443", name="vless-测试",
                protocol="vless", source="manual", host="example.com",
                port=443, extra={"uuid": VLESS_UUID})


# ---------------------------------------------------------------------------
# 1. 引擎守卫
# ---------------------------------------------------------------------------
class TestEnginePortGuard(unittest.TestCase):
    def test_start_blocked_when_http_port_occupied(self):
        socks, http = free_port(), free_port()
        with listener("127.0.0.1", http):
            eng = SingBoxEngine(binary="/bin/true",
                                socks_port=socks, http_port=http)
            with self.assertRaises(EngineError) as ctx:
                eng.start(vless_node())
            self.assertIn("占用", str(ctx.exception))
            self.assertIn(str(http), str(ctx.exception))

    def test_start_blocked_when_socks_port_occupied(self):
        socks, http = free_port(), free_port()
        with listener("127.0.0.1", socks):
            eng = SingBoxEngine(binary="/bin/true",
                                socks_port=socks, http_port=http)
            with self.assertRaises(EngineError) as ctx:
                eng.start(vless_node())
            self.assertIn("占用", str(ctx.exception))
            self.assertIn(str(socks), str(ctx.exception))


# ---------------------------------------------------------------------------
# 2. CLI 接线
# ---------------------------------------------------------------------------
class TestCliHealthPrecheck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "client.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _seed_vless(self):
        store = Store(self.db)
        try:
            store.upsert_node(vless_node())
        finally:
            store.close()

    def test_status_shows_port_and_sysproxy_lines(self):
        out, _err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(_err):
            rc = cli.main(["--db", self.db, "status"])
        self.assertEqual(rc, 0)
        text = out.getvalue()
        self.assertIn("端口 7897（HTTP 入站）", text)
        self.assertIn("端口 7898（SOCKS 入站）", text)
        self.assertIn("系统代理", text)

    def test_connect_blocked_when_ports_occupied(self):
        self._seed_vless()
        socks, http = free_port(), free_port()
        with listener("127.0.0.1", http):
            rc, out, err = run_cli(
                ["--db", self.db, "connect", "manual:vless:example.com:443"],
                probe_factory=fake_probe_factory,
                engine_factory=lambda: {
                    "vless": SingBoxEngine(binary="/bin/true",
                                           socks_port=socks, http_port=http)})
        self.assertEqual(rc, 2)
        self.assertIn("占用", err)
        self.assertNotIn("connected", out)

    def test_connect_warns_but_continues_on_sysproxy_conflict(self):
        # openvpn 节点 + 假引擎：走②冲突警告分支（不占 7897/7898 的口径）。
        from client.tests.test_sources import FEED_CSV
        feed = os.path.join(self.tmp.name, "feed.csv")
        with open(feed, "w", encoding="utf-8") as f:
            f.write(FEED_CSV)
        run_cli(["--db", self.db, "intake", "--feed", feed])
        orig = cli.read_system_proxy
        cli.read_system_proxy = lambda runner=None: {
            "available": True, "mode": "manual",
            "http_host": "127.0.0.1", "http_port": 7890, "error": ""}
        try:
            rc, out, err = run_cli(
                ["--db", self.db, "connect",
                 "vpngate:openvpn:vpn123456.opengw.net:443"],
                probe_factory=fake_probe_factory,
                engine_factory=lambda: {"openvpn": FakeEngine()})
        finally:
            cli.read_system_proxy = orig
        self.assertEqual(rc, 0, err)
        self.assertIn("[警告]", out)
        self.assertIn("connected", out)


# ---------------------------------------------------------------------------
# 3. GUI 接线（offscreen）
# ---------------------------------------------------------------------------
def ok_verifier(node):
    return VerifyOutcome(ok=True, exit_profile=ExitProfile(
        ip="5.6.7.8", country="日本", country_code="JP",
        network_type="residential", is_clean=True), scenes={"ai"})


@unittest.skipUnless(_QT, "无 PySide6，跳过 GUI 测试")
class TestGuiHealthPrecheck(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, socks, http):
        store = Store(":memory:")
        store.upsert_node(vless_node())
        eng = SingBoxEngine(binary="/bin/true", socks_port=socks,
                            http_port=http)
        orch = Orchestrator(store, {"vless": eng}, ok_verifier)
        win = app_mod.MainWindow(store, orchestrator=orch,
                                 engines={"vless": eng})

        def sync_run_task(tag, fn):
            try:
                result = (True, fn())
            except Exception as exc:  # noqa: BLE001
                result = (False, exc)
            win._on_task_finished(tag, result)

        win._run_task = sync_run_task
        self.addCleanup(win.close)
        return win, eng, store

    def select_manual_row(self, win, row=0):
        win.tabs.setCurrentWidget(win.pages["manual"])
        win.pages["manual"].table.setCurrentCell(row, 0)
        self.app.processEvents()

    def test_connect_blocked_when_ports_occupied(self):
        socks, http = free_port(), free_port()
        win, _eng, _store = self.make_window(socks, http)
        self.select_manual_row(win)
        with listener("127.0.0.1", http):
            win.connect_selected()
            self.app.processEvents()
        self.assertTrue(not win.alert_bar.isHidden())
        self.assertIn("启动被挡", win.alert_bar.text())
        self.assertEqual(win.orchestrator.state, Orchestrator.IDLE)

    def test_health_probe_dead_after_five_failures(self):
        win, _eng, _store = self.make_window(free_port(), free_port())
        win.orchestrator.state = Orchestrator.CONNECTED
        win.orchestrator.active_node_id = "manual:vless:example.com:443"
        for _ in range(3):
            win._on_task_finished(
                "health_probe",
                (True, ("manual:vless:example.com:443", False, None, "链路不通")))
        self.assertIn("可疑", win.status_label.text())
        for _ in range(2):
            win._on_task_finished(
                "health_probe",
                (True, ("manual:vless:example.com:443", False, None, "链路不通")))
        self.assertTrue(not win.alert_bar.isHidden())
        self.assertIn("疑似已死", win.alert_bar.text())

    def test_health_probe_error_skips_sample(self):
        win, _eng, _store = self.make_window(free_port(), free_port())
        win.orchestrator.state = Orchestrator.CONNECTED
        win.orchestrator.active_node_id = "manual:vless:example.com:443"
        win._on_task_finished("health_probe", (False, RuntimeError("boom")))
        self.assertEqual(len(win._health_samples), 0)
        self.assertIn("健康探测出错", win.status_label.text())

    def test_health_probe_from_old_session_dropped(self):
        win, _eng, _store = self.make_window(free_port(), free_port())
        win.orchestrator.state = Orchestrator.CONNECTED
        win.orchestrator.active_node_id = "manual:vless:example.com:443"
        # 上个会话的探测晚到：节点已换，不应计入新会话样本。
        win._on_task_finished(
            "health_probe",
            (True, ("manual:vless:other:443", False, None, "链路不通")))
        self.assertEqual(len(win._health_samples), 0)

    def test_health_probe_does_not_clear_busy(self):
        win, _eng, _store = self.make_window(free_port(), free_port())
        win._busy = True  # 模拟用户任务在跑
        win._on_task_finished(
            "health_probe",
            (True, ("manual:vless:example.com:443", True, 123.4, "")))
        self.assertTrue(win._busy)

    def test_sysproxy_check_show_and_clear(self):
        win, _eng, _store = self.make_window(free_port(), free_port())
        win._on_task_finished(
            "sysproxy_check",
            (True, (True, "系统代理为 manual 模式，指向 127.0.0.1:7890")))
        self.assertTrue(not win.alert_bar.isHidden())
        self.assertTrue(win._sysproxy_alert)
        win._on_task_finished(
            "sysproxy_check",
            (True, (False, "系统代理未开启（mode=none）")))
        self.assertTrue(win.alert_bar.isHidden())
        self.assertFalse(win._sysproxy_alert)

    def test_maybe_probe_skips_when_busy(self):
        win, _eng, _store = self.make_window(free_port(), free_port())
        win.orchestrator.state = Orchestrator.CONNECTED
        win.orchestrator.active_node_id = "manual:vless:example.com:443"
        win._busy = True
        win._maybe_health_probe()
        self.assertFalse(win._health_active)
        win._busy = False
        win._health_active = True  # 上轮没回来
        win._maybe_health_probe()
        self.assertTrue(win._health_active)  # 未重复起线程


if __name__ == "__main__":
    unittest.main()
