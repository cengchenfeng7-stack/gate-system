#!/usr/bin/env python3
"""ui/app 来源登记（M3 图形界面）离线测试：offscreen Qt + 假拉取注入。
按钮存在性、登记表落位、订阅一键更新往返、feed 登记更新。
没有 PySide6 的环境整文件跳过。"""
import base64
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication
    _QT = True
except ImportError:
    _QT = False

if _QT:
    from client.core.orchestrator import Orchestrator
    from client.core.store import Store
    from client.tests.test_gui_app import FakeEngine, ok_verifier, seed
    from client.tests.test_subscription import FAKE_SUB_URL, THREE_LINKS
    from client.ui import app as app_mod

    FEED_HEADER = ("HostName,IP,Score,Ping,Speed,CountryLong,CountryShort,"
                   "NumVpnSessions,Uptime,OpenVPN_ConfigData_Base64")

    def _ovpn_b64(host, port=443, proto="udp"):
        cfg = (f"client\ndev tun\nproto {proto}\nremote {host} {port}\n"
               "resolv-retry infinite\n")
        return base64.b64encode(cfg.encode()).decode()

    def _feed_row(host, ip, sessions=10, speed=8000000):
        return ",".join([host, ip, "100", "50", str(speed), "Fake Country",
                         "FC", str(sessions), "86400000", _ovpn_b64(host)])

    def _feed_csv(*rows):
        return FEED_HEADER + "\n" + "\n".join(rows) + "\n"


@unittest.skipUnless(_QT, "无 PySide6，跳过 GUI 测试")
class TestSourcesGui(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "client.db")
        store = Store(self.db)
        seed(store)
        eng = FakeEngine()
        orch = Orchestrator(store, {"openvpn": eng}, ok_verifier)
        self.win = app_mod.MainWindow(store, orchestrator=orch,
                                      engines={"openvpn": eng})

        def sync_run_task(tag, fn):
            try:
                result = (True, fn())
            except Exception as exc:  # noqa: BLE001
                result = (False, exc)
            self.win._on_task_finished(tag, result)

        self.win._run_task = sync_run_task
        self.store = store
        self.addCleanup(self.win.close)
        self.addCleanup(self.tmp.cleanup)

    def _action_texts(self, source):
        return [t for t, _ in self.win.pages[source]._import_actions()]

    # 1. 每页都有「更新已登记来源」；feed/OpenVPN 页各有登记入口
    def test_buttons_exist(self):
        for source in ("openvpn", "vpngate_feed", "manual"):
            self.assertIn("更新已登记来源", self._action_texts(source))
        self.assertIn("登记并更新 feed", self._action_texts("vpngate_feed"))
        self.assertIn("登记 feed 镜像", self._action_texts("openvpn"))

    # 2. 登记表落在节点库同目录 sources.json
    def test_registry_path_next_to_db(self):
        self.assertEqual(self.win._registry().path,
                         os.path.join(self.tmp.name, "sources.json"))

    # 3. 无登记时给明确提示，不报错
    def test_update_no_entries_hint(self):
        text = self.win._update_sources_for_page("manual")
        self.assertIn("没有已登记的来源", text)

    # 4. 订阅登记 → 一键更新：新增 3，再更新一次全是更新（验真口径不变）
    def test_subscription_update_roundtrip(self):
        self.win._registry().register("subscription", FAKE_SUB_URL, "测试订阅")
        r1 = self.win._update_sources_for_page(
            "manual", fetch_fn=lambda e: THREE_LINKS)
        self.assertIn("新增 3", r1)
        self.assertEqual(self.store.count_nodes(), 7)  # seed 4 + 新增 3
        r2 = self.win._update_sources_for_page(
            "manual", fetch_fn=lambda e: THREE_LINKS)
        self.assertIn("新增 0", r2)
        self.assertIn("更新 3", r2)

    # 5. feed 登记更新：按钮走的工作线程同步替身，状态栏显示汇总
    def test_feed_register_and_update_via_button(self):
        self.win.tabs.setCurrentWidget(self.win.pages["vpngate_feed"])
        self.win.register_and_update_feed(
            fetch_fn=lambda e: _feed_csv(
                _feed_row("f1.opengw.net", "192.0.2.11")))
        self.assertIn("新增 1", self.win.status_label.text())
        feed_nodes = self.store.list_nodes(source="vpngate_feed")
        self.assertEqual(len(feed_nodes), 1)
        self.assertEqual(feed_nodes[0].host, "f1.opengw.net")


if __name__ == "__main__":
    unittest.main()
