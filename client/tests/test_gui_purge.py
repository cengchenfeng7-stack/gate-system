#!/usr/bin/env python3
"""ui/app 删除与批量测调整（v0.7.0）离线测试：多选删除、一键删废节点、
批量测收集本轮不通/污染名单。offscreen Qt，没有 PySide6 整文件跳过。"""
import os
import shutil
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
    from client.core import history
    from client.core.model import ExitProfile
    from client.core.orchestrator import Orchestrator
    from client.core.store import Store
    from client.tests.test_gui_app import FakeEngine, ok_verifier, seed
    from client.ui import app as app_mod


@unittest.skipUnless(_QT, "无 PySide6，跳过 GUI 测试")
class TestPurgeGui(unittest.TestCase):
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

    # 1. 多选：选中一行 → selected_node_ids 给出该行 id；批量删按 id 列表删
    def test_selected_ids_and_batch_delete(self):
        win = self._window()
        page = win.pages["manual"]
        page.table.selectRow(0)
        ids = page.selected_node_ids()
        self.assertEqual(len(ids), 1)
        self.assertIn(ids[0], ("m1", "m2"))
        n = win.delete_nodes_by_ids(["m1", "m2"])
        self.assertEqual(n, 2)
        self.assertIsNone(win.store.get_node("m1"))
        self.assertIsNone(win.store.get_node("m2"))
        self.assertIsNotNone(win.store.get_node("ov1"))  # 其他页不动

    # 2. 废节点口径：最近验真失败 = 不通；隔离标记 = 污染；一键删只删这两类
    def test_waste_ids_and_purge(self):
        win = self._window()
        history.record(win.store, "m1", "verify", False, detail="链路不通")
        dead, dirty = win.waste_node_ids("manual")
        self.assertEqual(dead, ["m1"])
        self.assertEqual(dirty, ["m2"])  # seed 的 m2 带 quarantined 标记
        n = win.purge_waste_on_page("manual")
        self.assertEqual(n, 2)
        self.assertIsNone(win.store.get_node("m1"))
        self.assertIsNone(win.store.get_node("m2"))
        # 没测过的不冤枉：openvpn 页 ov1 还在
        self.assertIsNotNone(win.store.get_node("ov1"))

    # 3. 批量测收集本轮名单：失败进 fail_ids，验过但出口污染进 dirty_ids
    def test_run_batch_collects_waste(self):
        win = self._window()
        node = win.store.get_node("m2")
        node.exit_profile = ExitProfile(ip="1.2.3.4", is_clean=False)
        win.store.upsert_node(node)

        def fake_test(nid, fast=True):
            if nid == "m1":
                return ("verify_failed", "链路不通")
            return ("verify_ok", "出口 1.2.3.4")

        win._test_node = fake_test
        payload = win._run_batch(["m1", "m2"], "本页")
        (kind, ok_n, fail_n, skip_n, done, total, label,
         fail_ids, dirty_ids) = payload
        self.assertEqual(kind, "batch_done")
        self.assertEqual((ok_n, fail_n), (1, 1))
        self.assertEqual(fail_ids, ["m1"])
        self.assertEqual(dirty_ids, ["m2"])

    # 4. 批量测完不弹模态框：废名单记在 _batch_waste，一键清掉
    def test_batch_waste_pending_and_purge(self):
        win = self._window()
        payload = ("batch_done", 0, 1, 0, 1, 1, "全部节点", ["m1"], [])
        win._on_task_finished("batch", (True, payload))
        self.assertEqual(win._batch_waste, ["m1"])
        self.assertIn("一键删废节点", win.status_label.text())
        n = win.purge_batch_waste()
        self.assertEqual(n, 1)
        self.assertIsNone(win.store.get_node("m1"))
        self.assertEqual(win._batch_waste, [])


if __name__ == "__main__":
    unittest.main()
