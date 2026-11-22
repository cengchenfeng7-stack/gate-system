#!/usr/bin/env python3
"""ui/app（M3 图形界面）离线测试：offscreen Qt + 假引擎/假 verifier。

没有 PySide6 的环境整文件跳过（不许让无 Qt 的 CI 红掉）。
慢操作的工作线程在测试里被同步替身替换，行为路径与真实一致。
"""
import os
import sys
import shutil
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
    from types import SimpleNamespace

    from PySide6.QtCore import QSettings
    from client.core.model import ExitProfile, Node
    from client.core.orchestrator import Orchestrator
    from client.core.store import Store
    from client.core.verify import ProbeResult, VerifyOutcome
    from client.engines.base import EngineError
    from client.ui import app as app_mod


class FakeEngine:
    name = "openvpn"

    def __init__(self, fail_start=False):
        self.fail_start = fail_start
        self.alive = True
        self.started = []

    def start(self, node, on_event=None):
        if self.fail_start:
            raise EngineError("假引擎启动失败")
        self.started.append(node.id)
        self.alive = True
        return {"node_id": node.id}

    def stop(self, handle):
        self.alive = False

    def status(self, handle):
        return "running" if self.alive else "stopped"


class FakeSingBoxEngine:
    """临时测速引擎替身：只记启动/停止，代理地址随便给（probe 被打桩）。"""
    name = "sing-box"

    def __init__(self):
        self.started = []
        self.stopped = False

    @property
    def http_proxy_url(self):
        return "http://127.0.0.1:10819"

    def start(self, node, on_event=None):
        self.started.append(node.id)
        return {"node_id": node.id}

    def stop(self, handle):
        self.stopped = True

    def status(self, handle):
        return "running"


def ok_verifier(node):
    return VerifyOutcome(ok=True, exit_profile=ExitProfile(
        ip="5.6.7.8", country="日本", country_code="JP",
        network_type="residential", is_clean=True), scenes={"ai"})


def bad_verifier(node):
    return VerifyOutcome(ok=False, reason="出口 IP 没变（假连）")


def seed(store):
    store.upsert_node(Node(id="ov1", name=" ovpn节点", protocol="openvpn",
                           source="vpngate", host="h1", port=1194))
    store.upsert_node(Node(id="m1", name="手动节点", protocol="openvpn",
                           source="manual", host="h2", port=443, scenes={"ai"}))
    store.upsert_node(Node(id="m2", name="隔离节点", protocol="openvpn",
                           source="manual", host="h3", port=443,
                           extra={"quarantined": "1", "quarantine_reason": "脏IP"}))
    store.upsert_node(Node(id="p1", name="流水线节点", protocol="openvpn",
                           source="pipeline", host="h4", port=443))


@unittest.skipUnless(_QT, "无 PySide6，跳过 GUI 测试")
class TestGuiApp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_window(self, verifier=ok_verifier):
        store = Store(":memory:")
        seed(store)
        eng = FakeEngine()
        orch = Orchestrator(store, {"openvpn": eng}, verifier)
        win = app_mod.MainWindow(store, orchestrator=orch, engines={"openvpn": eng})

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

    def select(self, win, source, row=0):
        win.tabs.setCurrentWidget(win.pages[source])
        page = win.pages[source]
        page.table.setCurrentCell(row, 0)
        return page

    # ---------------- 页面结构 ----------------
    def test_three_pages_source_split(self):
        """自 task23 起行为反转、依据 work8/DESIGN §7：四页 → 三页
        （OpenVPN 页与 VPNGate feed 页合并为 vpngate 一页）。"""
        win, _eng, _store = self.make_window()
        self.assertEqual(win.tabs.count(), 3)
        self.assertEqual(set(win.pages), {"vpngate", "manual", "pipeline"})
        self.assertEqual(win.pages["vpngate"].table.rowCount(), 1)
        self.assertEqual(win.pages["manual"].table.rowCount(), 1)  # 隔离默认隐藏
        self.assertEqual(win.pages["pipeline"].table.rowCount(), 1)

    def test_quarantine_toggle(self):
        win, _eng, _store = self.make_window()
        page = win.pages["manual"]
        self.assertEqual(page.table.rowCount(), 1)
        page.show_quarantined_chk.setChecked(True)
        self.pump()
        self.assertEqual(page.table.rowCount(), 2)

    def test_scene_filter(self):
        win, _eng, _store = self.make_window()
        page = win.pages["manual"]
        page.scene_combo.setCurrentIndex(2)  # AI
        self.pump()
        self.assertEqual(page.table.rowCount(), 1)
        page.scene_combo.setCurrentIndex(1)  # 客服：手动节点无此场景
        self.pump()
        self.assertEqual(page.table.rowCount(), 0)

    # ---------------- 会话 ----------------
    def test_connect_disconnect(self):
        win, _eng, _store = self.make_window()
        self.select(win, "vpngate")
        win.connect_selected()
        self.pump()
        self.assertEqual(win.orchestrator.state, Orchestrator.CONNECTED)
        self.assertIn("已连接", win.status_label.text())
        self.assertFalse(win.connect_btn.isEnabled())
        win.disconnect()
        self.pump()
        self.assertEqual(win.orchestrator.state, Orchestrator.IDLE)
        self.assertEqual(win.status_label.text(), "未连接")

    def test_verify_failed_alert(self):
        win, _eng, _store = self.make_window(verifier=bad_verifier)
        self.select(win, "vpngate")
        win.connect_selected()
        self.pump()
        self.assertEqual(win.orchestrator.state, Orchestrator.IDLE)
        self.assertTrue(not win.alert_bar.isHidden())
        self.assertIn("假连", win.alert_bar.text())

    def test_drop_poll_alert(self):
        win, eng, _store = self.make_window()
        self.select(win, "vpngate")
        win.connect_selected()
        self.pump()
        self.assertEqual(win.orchestrator.state, Orchestrator.CONNECTED)
        eng.alive = False
        win.poll_once()
        self.pump()
        self.assertEqual(win.orchestrator.state, Orchestrator.IDLE)
        self.assertTrue(not win.alert_bar.isHidden())
        self.assertIn("掉线", win.alert_bar.text())

    def test_engine_start_failure_alert(self):
        store = Store(":memory:")
        seed(store)
        eng = FakeEngine(fail_start=True)
        orch = Orchestrator(store, {"openvpn": eng}, ok_verifier)
        win = app_mod.MainWindow(store, orchestrator=orch, engines={"openvpn": eng})
        win._run_task = lambda tag, fn: win._on_task_finished(
            tag, (True, fn()))
        self.addCleanup(win.close)
        self.select(win, "vpngate")
        win.connect_selected()
        self.pump()
        self.assertEqual(win.orchestrator.state, Orchestrator.IDLE)
        self.assertTrue(not win.alert_bar.isHidden())

    def test_connect_real_worker_thread(self):
        """不许用同步替身：真 QThread 跑连接全流程（2026-11-20 真机
        踩过 SQLite 跨线程坑，同步替身测不出来）。"""
        import time
        store = Store(":memory:")
        seed(store)
        eng = FakeEngine()
        orch = Orchestrator(store, {"openvpn": eng}, ok_verifier)
        win = app_mod.MainWindow(store, orchestrator=orch, engines={"openvpn": eng})
        self.addCleanup(win.close)
        self.select(win, "vpngate")
        win.connect_selected()
        deadline = time.time() + 10
        while time.time() < deadline and (
                win.orchestrator.state != Orchestrator.CONNECTED or win._busy):
            self.app.processEvents()
            time.sleep(0.02)
        self.assertEqual(win.orchestrator.state, Orchestrator.CONNECTED)
        self.assertTrue(win.alert_bar.isHidden())
        win.disconnect()
        deadline = time.time() + 10
        while time.time() < deadline and win.orchestrator.state != Orchestrator.IDLE:
            self.app.processEvents()
            time.sleep(0.02)
        self.assertEqual(win.orchestrator.state, Orchestrator.IDLE)

    # ---------------- 节点操作 ----------------
    def test_pin_toggle(self):
        win, _eng, store = self.make_window()
        self.select(win, "vpngate")
        win.toggle_pin()
        self.assertTrue(store.get_node("ov1").pinned)
        win.toggle_pin()
        self.assertFalse(store.get_node("ov1").pinned)

    def test_verify_button_openvpn_unconnected_skips(self):
        """OpenVPN 未连接时测不了：如实提示、且不许记假样本（旧实现拿
        没启动的引擎代理地址乱测，测谁都全灭——2026-11-20 真机教训）。"""
        win, _eng, store = self.make_window()
        self.select(win, "vpngate")
        win.verify_selected()
        self.pump()
        self.assertIn("隧道模式", win.status_label.text())
        self.assertEqual(store.samples_for("ov1"), [])

    def _patch_probe(self, fn):
        orig = app_mod.make_real_probe
        app_mod.make_real_probe = lambda **kw: fn
        self.addCleanup(lambda: setattr(app_mod, "make_real_probe", orig))

    def test_test_node_vless_temp_engine(self):
        """sing-box 节点测速：临时引擎起停 + 延迟落样本/指标 + 最近列。"""
        win, _eng, store = self.make_window()
        store.upsert_node(Node(id="v1", name="vless节点", protocol="vless",
                               source="manual", host="h9", port=443,
                               extra={"uuid": "u-1", "query": {"type": "xhttp"}}))
        win.refresh_pages()
        fake_eng = FakeSingBoxEngine()
        win._make_temp_engine = lambda: fake_eng
        self._patch_probe(lambda node: ProbeResult(
            alive=True, exit_ip="9.9.9.9", country="美国", country_code="US",
            org="Some ISP", is_datacenter=False, is_clean=True, latency_ms=88))
        page = win.pages["manual"]
        win.tabs.setCurrentWidget(page)
        row = page.visible_node_ids().index("v1")
        page.table.setCurrentCell(row, 0)
        win.verify_selected()
        self.pump()
        self.assertIn("测速通过", win.status_label.text())
        self.assertIn("88ms", win.status_label.text())
        self.assertEqual(fake_eng.started, ["v1"])
        self.assertTrue(fake_eng.stopped)          # 临时引擎测完即停
        samples = store.samples_for("v1")
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].latency_ms, 88)
        self.assertEqual(store.get_node("v1").metrics.latency_ms, 88)
        last_col = page.COLUMNS.index("最近")
        self.assertTrue(page.table.item(row, last_col).text().startswith("可连"))
        lat_col = page.COLUMNS.index("延迟")
        self.assertEqual(page.table.item(row, lat_col).text(), "88ms")

    def test_batch_test_page(self):
        """测本页：一活一死，汇总计数对、样本各落一条、状态复位。"""
        win, _eng, store = self.make_window()
        for nid, name in (("v1", "活节点"), ("v2", "死节点")):
            store.upsert_node(Node(id=nid, name=name, protocol="vless",
                                   source="manual", host="h", port=443,
                                   extra={"uuid": "u", "query": {}}))
        win.refresh_pages()

        def probe(node):
            if node.id == "v1":
                return ProbeResult(alive=True, exit_ip="9.9.9.9",
                                   country="美国", country_code="US",
                                   is_clean=True, latency_ms=50)
            return ProbeResult(alive=False, error="存活探针全灭：链路不通")

        self._patch_probe(probe)
        win._make_temp_engine = lambda: FakeSingBoxEngine()
        win.tabs.setCurrentWidget(win.pages["manual"])
        win.toggle_batch_test()
        self.pump()
        self.assertIn("批量测速完成", win.status_label.text())
        self.assertIn("可连 1", win.status_label.text())
        self.assertIn("不通 1", win.status_label.text())
        self.assertFalse(win._batch_active)
        self.assertEqual(win.batch_btn.text(), "测本页")
        self.assertEqual(len(store.samples_for("v1")), 1)
        self.assertEqual(len(store.samples_for("v2")), 1)

    def test_batch_all_nodes(self):
        """一键全测：跨来源全库都测（openvpn 未连接 → 跳过不记样本）。"""
        win, _eng, store = self.make_window()
        store.upsert_node(Node(id="v1", name="活节点", protocol="vless",
                               source="manual", host="h", port=443,
                               extra={"uuid": "u", "query": {}}))
        win.refresh_pages()
        self._patch_probe(lambda node: ProbeResult(
            alive=True, exit_ip="9.9.9.9", country="美国", country_code="US",
            is_clean=True, latency_ms=50))
        win._make_temp_engine = lambda: FakeSingBoxEngine()
        win.toggle_batch_test(all_nodes=True)
        self.pump()
        # seed 4 个 openvpn（ov1/m1/m2/p1）全跳过 + v1 可连
        self.assertIn("全部节点", win.status_label.text())
        self.assertIn("可连 1", win.status_label.text())
        self.assertIn("跳过 4", win.status_label.text())
        self.assertEqual(len(store.samples_for("v1")), 1)
        self.assertEqual(store.samples_for("ov1"), [])
        self.assertFalse(win._batch_active)
        self.assertEqual(win.batch_all_btn.text(), "一键全测")

    # ---------------- 自动探测 ----------------
    def test_auto_probe_healthy_floats_first(self):
        """自动探测一轮：活/死样本各落一条，界面把可连的排到最前。"""
        win, _eng, store = self.make_window()
        for nid, name in (("a1", "a-死"), ("b1", "b-活"), ("c1", "c-死")):
            store.upsert_node(Node(id=nid, name=name, protocol="vless",
                                   source="manual", host="h", port=443,
                                   extra={"uuid": "u", "query": {}}))
        win.refresh_pages()

        def probe(node):
            if node.id == "b1":
                return ProbeResult(alive=True, exit_ip="9.9.9.9",
                                   country="美国", country_code="US",
                                   is_clean=True, latency_ms=66)
            return ProbeResult(alive=False, error="存活探针全灭：链路不通")

        self._patch_probe(probe)
        win._make_temp_engine = lambda: FakeSingBoxEngine()
        win._make_auto_engine = lambda: FakeSingBoxEngine()
        win.auto_chk.setChecked(True)
        calls = []

        def fake_launch(fn):
            calls.append(fn)
            win._on_task_finished("auto_probe", (True, fn()))

        win._launch_auto = fake_launch
        win._maybe_auto_probe()
        self.assertEqual(len(calls), 1)                 # 开了一轮
        self.assertFalse(win._auto_active)              # 收尾复位
        self.assertIn("自动探测完成", win.status_label.text())
        self.assertIn("可连 1", win.status_label.text())
        self.assertIn("不通 2", win.status_label.text())
        # 样本落库（openvpn 的 seed 节点不参与）
        self.assertEqual(len(store.samples_for("b1")), 1)
        self.assertEqual(store.samples_for("b1")[0].latency_ms, 66)
        self.assertEqual(len(store.samples_for("a1")), 1)
        self.assertFalse(store.samples_for("a1")[0].success)
        self.assertEqual(store.samples_for("ov1"), [])
        # 表格自动重排：可连的 b1 浮到最前
        page = win.pages["manual"]
        self.assertEqual(page.visible_node_ids()[0], "b1")

    def test_auto_probe_guards(self):
        """守卫：开关关了 / 上轮在跑 / 用户任务占用 / 会话连接中，都不开新一轮。"""
        win, _eng, store = self.make_window()
        store.upsert_node(Node(id="b1", name="b-活", protocol="vless",
                               source="manual", host="h", port=443,
                               extra={"uuid": "u", "query": {}}))
        win.refresh_pages()
        win._make_auto_engine = lambda: FakeSingBoxEngine()
        calls = []
        win._launch_auto = lambda fn: calls.append(fn)

        win.auto_chk.setChecked(False)
        win._maybe_auto_probe()
        self.assertEqual(calls, [])                     # 开关关闭
        win.auto_chk.setChecked(True)

        win._auto_active = True
        win._maybe_auto_probe()
        self.assertEqual(calls, [])                     # 上轮未完
        win._auto_active = False

        win._busy = True
        win._maybe_auto_probe()
        self.assertEqual(calls, [])                     # 用户任务占用
        win._busy = False

        # 会话连接中：经真连接流程进入 CONNECTED
        self.select(win, "vpngate")
        win.connect_selected()
        self.pump()
        self.assertEqual(win.orchestrator.state, Orchestrator.CONNECTED)
        win._maybe_auto_probe()
        self.assertEqual(calls, [])                     # 连接期间不跑

    def test_auto_probe_ids_rotating_slice(self):
        """轮扫切片：每轮 40 个，游标推进、到尾回卷，多轮覆盖全库。"""
        win, _eng, store = self.make_window()
        for i in range(90):
            store.upsert_node(Node(id=f"v{i:02d}", name=f"节点{i:02d}",
                                   protocol="vless", source="manual",
                                   host="h", port=443,
                                   extra={"uuid": "u", "query": {}}))
        win.refresh_pages()
        ids1 = win._auto_probe_ids()
        self.assertEqual(len(ids1), 40)
        self.assertEqual(ids1[:3], ["v00", "v01", "v02"])
        ids2 = win._auto_probe_ids()
        self.assertEqual(len(ids2), 40)
        self.assertTrue(set(ids1).isdisjoint(ids2))     # 游标推进，两轮不重
        ids3 = win._auto_probe_ids()
        self.assertEqual(len(ids3), 40)                 # 80→90 只剩 10 个，回卷补头
        overlap = set(ids3) & (set(ids1) | set(ids2))
        self.assertEqual(len(overlap), 30)

    def test_auto_probe_ids_no_duplicate_when_below_slice(self):
        """库存不足一轮时不得回卷重复（曾把 3 个节点重复成 6 个任务）。"""
        win, _eng, store = self.make_window()
        for i in range(3):
            store.upsert_node(Node(id=f"v{i}", name=f"节点{i}", protocol="vless",
                                   source="manual", host="h", port=443,
                                   extra={"uuid": "u", "query": {}}))
        win.refresh_pages()
        ids = win._auto_probe_ids()
        self.assertEqual(sorted(ids), ["v0", "v1", "v2"])


@unittest.skipUnless(_QT, "需要 PySide6")
class TestLastNodeMemory(unittest.TestCase):
    """上次节点恢复（2026-11-21）：连接成功记下节点，开窗自动选中
    （只选中、不自动连接）；节点已删则静默跳过。测试用真实
    QSettings 但键在用后即删，残留值指向不存在的节点也无副作用。"""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        QSettings("gate-client", "gui").remove("last_node_id")
        self.addCleanup(
            lambda: QSettings("gate-client", "gui").remove("last_node_id"))

    def _store(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        store = Store(os.path.join(d, "test.db"))
        seed(store)
        return store

    def _window(self, store):
        eng = FakeEngine()
        orch = Orchestrator(store, {"openvpn": eng}, ok_verifier)
        win = app_mod.MainWindow(store, orchestrator=orch,
                                 engines={"openvpn": eng})
        self.addCleanup(win.close)
        return win

    def test_connected_event_saves_last_node(self):
        win = self._window(self._store())
        ev = SimpleNamespace(kind="connected", node_id="m1", detail="ok")
        win._on_session_event(ev)
        saved = QSettings("gate-client", "gui").value(
            "last_node_id", "", type=str)
        self.assertEqual(saved, "m1")

    def test_startup_restores_selection_and_page(self):
        QSettings("gate-client", "gui").setValue("last_node_id", "m1")
        win = self._window(self._store())
        page = win.pages["manual"]
        self.assertEqual(page.selected_node_id(), "m1")
        self.assertIs(win.tabs.currentWidget(), page)

    def test_startup_with_deleted_node_is_silent(self):
        QSettings("gate-client", "gui").setValue("last_node_id", "ghost")
        win = self._window(self._store())   # 不抛错即过
        self.assertIsNone(win.pages["manual"].selected_node_id())


@unittest.skipUnless(_QT, "需要 PySide6")
class TestDeleteNodeGui(unittest.TestCase):
    """删除按钮背后的动作（2026-11-21 用户亲点）：删节点并刷新，
    删不存在的返回 False 不报错。对话框确认不在此测（delete_node_by_id
    与确认已分离）。"""

    def setUp(self):
        self.app = QApplication.instance() or QApplication([])

    def _window(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(d, ignore_errors=True))
        store = Store(os.path.join(d, "test.db"))
        seed(store)
        eng = FakeEngine()
        orch = Orchestrator(store, {"openvpn": eng}, ok_verifier)
        win = app_mod.MainWindow(store, orchestrator=orch,
                                 engines={"openvpn": eng})
        self.addCleanup(win.close)
        return win

    def test_delete_node_by_id(self):
        win = self._window()
        self.assertIsNotNone(win.store.get_node("m1"))
        self.assertTrue(win.delete_node_by_id("m1"))
        self.assertIsNone(win.store.get_node("m1"))
        self.assertIsNone(win.pages["manual"].selected_node_id())

    def test_delete_missing_node_returns_false(self):
        win = self._window()
        self.assertFalse(win.delete_node_by_id("ghost"))


if __name__ == "__main__":
    unittest.main()
