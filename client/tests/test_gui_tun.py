#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ui/app TUN 接线离线测试（task6 集成，v0.10.0）：offscreen Qt + 假引擎。

没有 PySide6 的环境整文件跳过。覆盖：模式控件两档与引擎属性同步、
连接中禁用、连接时把模式写进引擎、检测② TUN 口径文案、检测④巡检告警。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    _QT = True
except ImportError:
    _QT = False

if _QT:
    from client.core.model import ExitProfile, Node
    from client.core.orchestrator import Orchestrator
    from client.core.store import Store
    from client.core.verify import VerifyOutcome
    from client.engines.singbox_engine import SingBoxEngine
    from client.ui import app as app_mod

    CONFLICT_PROXY = {"available": True, "mode": "manual",
                      "http_host": "127.0.0.1", "http_port": 7890,
                      "error": ""}
    NO_PROXY = {"available": False, "mode": None, "http_host": None,
                "http_port": None, "error": "测试替身：无系统代理"}

    def ok_verifier(node):
        return VerifyOutcome(ok=True, exit_profile=ExitProfile(
            ip="5.6.7.8", country="日本", country_code="JP",
            network_type="residential", is_clean=True), scenes={"ai"})

    class FakeTunEngine(SingBoxEngine):
        """sing-box 替身：真是 SingBoxEngine 子类（过 isinstance 关），start
        不碰二进制，只记录连接时的 takeover_mode。"""

        def __init__(self):
            super().__init__(socks_port=18096, http_port=18097)
            self.started_modes = []

        @property
        def http_proxy_url(self):
            return "http://127.0.0.1:18097"

        def start(self, node, on_event=None):
            self.started_modes.append(self.takeover_mode)
            return {"node_id": node.id}

        def stop(self, handle):
            return None

        def status(self, handle):
            return "running"


@unittest.skipUnless(_QT, "无 PySide6，跳过 GUI 测试")
class TestGuiTun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self):
        store = Store(":memory:")
        store.upsert_node(Node(
            id="manual:ss:1.2.3.4:8388", name="ss-测试", protocol="ss",
            source="manual", host="1.2.3.4", port=8388,
            extra={"method": "aes-256-gcm", "password": "x"}))
        eng = FakeTunEngine()
        orch = Orchestrator(store, {"ss": eng}, ok_verifier)
        # 开窗体检读真实 gsettings：测试里一律打桩成「无系统代理」，
        # 个别用例再改桩。
        proxy_patch = mock.patch.object(app_mod, "read_system_proxy",
                                        return_value=dict(NO_PROXY))
        proxy_patch.start()
        self.addCleanup(proxy_patch.stop)
        win = app_mod.MainWindow(store, orchestrator=orch, engines={"ss": eng})

        def sync_run_task(tag, fn):
            try:
                result = (True, fn())
            except Exception as exc:  # noqa: BLE001
                result = (False, exc)
            win._on_task_finished(tag, result)

        win._run_task = sync_run_task
        self.addCleanup(win.close)
        return win, eng, store

    def pump(self):
        self.app.processEvents()

    def select_ss(self, win):
        win.tabs.setCurrentWidget(win.pages["manual"])
        page = win.pages["manual"]
        page.table.setCurrentCell(0, 0)
        return page

    # ---------------- 模式控件 ----------------
    def test_mode_combo_two_modes_default_proxy(self):
        win, eng, _store = self.make_window()
        self.assertEqual(win.mode_combo.count(), 2)
        self.assertEqual(win.mode_combo.currentData(), "proxy")
        self.assertEqual(eng.takeover_mode, "proxy")
        self.assertTrue(win.mode_combo.isEnabled())

    def test_mode_switch_syncs_engine_attribute(self):
        win, eng, _store = self.make_window()
        win.mode_combo.setCurrentIndex(1)
        self.pump()
        self.assertEqual(win.mode_combo.currentData(), "tun")
        self.assertEqual(eng.takeover_mode, "tun")
        self.assertIn("TUN", win.status_label.text())
        win.mode_combo.setCurrentIndex(0)
        self.pump()
        self.assertEqual(eng.takeover_mode, "proxy")

    def test_mode_combo_disabled_when_connected(self):
        win, eng, _store = self.make_window()
        self.select_ss(win)
        win.connect_selected()
        self.pump()
        self.assertEqual(win.orchestrator.state, Orchestrator.CONNECTED)
        self.assertFalse(win.mode_combo.isEnabled())
        win.disconnect()
        self.pump()
        self.assertEqual(win.orchestrator.state, Orchestrator.IDLE)
        self.assertTrue(win.mode_combo.isEnabled())

    def test_connect_applies_combo_mode_to_engine(self):
        win, eng, _store = self.make_window()
        win.mode_combo.setCurrentIndex(1)
        self.select_ss(win)
        win.connect_selected()
        self.pump()
        self.assertEqual(eng.started_modes, ["tun"])
        self.assertEqual(win.orchestrator.state, Orchestrator.CONNECTED)

    # ---------------- 检测② TUN 口径文案 ----------------
    def test_sysproxy_conflict_uses_tun_copy_in_tun_mode(self):
        win, eng, store = self.make_window()
        node = store.get_node("manual:ss:1.2.3.4:8388")
        with mock.patch.object(app_mod, "read_system_proxy",
                               return_value=dict(CONFLICT_PROXY)):
            win.mode_combo.setCurrentIndex(1)
            self.assertTrue(win._health_precheck(node))
            self.assertTrue(not win.alert_bar.isHidden())
            self.assertIn("TUN 已接管全机流量", win.alert_bar.text())
            self.assertIn("到不了隧道", win.alert_bar.text())

    def test_sysproxy_conflict_uses_proxy_copy_in_proxy_mode(self):
        win, _eng, store = self.make_window()
        node = store.get_node("manual:ss:1.2.3.4:8388")
        with mock.patch.object(app_mod, "read_system_proxy",
                               return_value=dict(CONFLICT_PROXY)):
            self.assertTrue(win._health_precheck(node))
            self.assertIn("不是本客户端", win.alert_bar.text())
            self.assertNotIn("TUN 已接管", win.alert_bar.text())

    # ---------------- 检测④巡检 ----------------
    def test_tun_takeover_check_alerts_and_recovers(self):
        win, eng, _store = self.make_window()
        win.mode_combo.setCurrentIndex(1)
        self.select_ss(win)
        win.connect_selected()
        self.pump()
        self.assertEqual(win.orchestrator.state, Orchestrator.CONNECTED)
        # 巡检里的健康探测/系统代理复查线程在本用例不跑，只测检测④。
        win._maybe_health_probe = lambda: None
        win._maybe_sysproxy_check = lambda: None
        with mock.patch.object(
                app_mod, "check_tun_takeover",
                return_value={"effective": False,
                              "reason": "TUN 网卡已建立，但默认路由未接管到 "
                                        "gate-tun——流量没有走隧道"}):
            win._health_tick = 5
            win.poll_once()
            self.assertTrue(win._tun_alert)
            self.assertTrue(not win.alert_bar.isHidden())
            self.assertIn("不会自动修路由", win.alert_bar.text())
        with mock.patch.object(
                app_mod, "check_tun_takeover",
                return_value={"effective": True,
                              "reason": "默认路由已接管到 gate-tun，流量走隧道"}):
            win._health_tick = 11
            win.poll_once()
            self.assertFalse(win._tun_alert)
            self.assertTrue(win.alert_bar.isHidden())


if __name__ == "__main__":
    unittest.main()
