#!/usr/bin/env python3
"""core/store 离线测试（内存库 + 临时文件库）。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.model import Node
from client.core.store import Store, VerifySample


def node(nid, scenes=(), name=None):
    return Node(id=nid, name=name or nid, protocol="sstp", source="pipeline",
                host="h", port=443, scenes=set(scenes))


class TestStore(unittest.TestCase):
    def setUp(self):
        self.store = Store(":memory:")

    def tearDown(self):
        self.store.close()

    def test_upsert_and_get(self):
        self.store.upsert_node(node("a"))
        got = self.store.get_node("a")
        self.assertIsNotNone(got)
        self.assertEqual(got.id, "a")
        self.assertIsNone(self.store.get_node("missing"))

    def test_upsert_updates_not_duplicates(self):
        self.store.upsert_node(node("a", name="old"))
        self.store.upsert_node(node("a", name="new"))
        self.assertEqual(self.store.count_nodes(), 1)
        self.assertEqual(self.store.get_node("a").name, "new")

    def test_upsert_preserves_pin_ban(self):
        self.store.upsert_node(node("a"))
        self.store.set_pinned("a", True)
        self.store.set_banned("a", True)
        self.store.upsert_node(node("a", name="refreshed"))   # 来源刷新不冲人工标记
        got = self.store.get_node("a")
        self.assertTrue(got.pinned)
        self.assertTrue(got.banned)

    def test_list_by_scene_and_banned_filter(self):
        self.store.upsert_nodes([node("a", {"support"}), node("b", {"ai"}),
                                 node("c", {"support", "ai"})])
        self.assertEqual({n.id for n in self.store.list_nodes(scene="support")}, {"a", "c"})
        self.assertEqual({n.id for n in self.store.list_nodes(scene="ai")}, {"b", "c"})
        self.store.set_banned("c", True)
        self.assertEqual({n.id for n in self.store.list_nodes(scene="support")}, {"a"})
        self.assertEqual({n.id for n in self.store.list_nodes(scene="support",
                                                             include_banned=True)}, {"a", "c"})

    def test_list_bad_scene_raises(self):
        with self.assertRaises(ValueError):
            self.store.list_nodes(scene="bogus")

    def test_pin_ban_missing_node_returns_false(self):
        self.assertFalse(self.store.set_pinned("nope", True))
        self.assertFalse(self.store.set_banned("nope", True))

    def test_samples_roundtrip_and_kind_filter(self):
        self.store.add_sample(VerifySample("a", "verify", True, ts=1.0, latency_ms=100))
        self.store.add_sample(VerifySample("a", "connect", False, ts=2.0, detail="x"))
        self.store.add_sample(VerifySample("b", "verify", True, ts=3.0))
        all_a = self.store.samples_for("a")
        self.assertEqual(len(all_a), 2)
        self.assertEqual([s.kind for s in self.store.samples_for("a", kind="verify")], ["verify"])
        self.assertEqual(all_a[0].latency_ms, 100)

    def test_file_backed_persistence(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "sub", "client.db")
            with Store(path) as s:
                s.upsert_node(node("a", {"ai"}))
            with Store(path) as s2:
                self.assertEqual(s2.get_node("a").scenes, {"ai"})

    def test_cross_thread_use(self):
        """M3 图形界面的工作线程与主线程共用一个 Store：不许再报
        'SQLite objects created in a thread can only be used in that
        same thread'（2026-11-20 真机实测踩过，红条原样贴回）。"""
        import threading
        errors = []

        def worker():
            try:
                for i in range(30):
                    self.store.upsert_node(node(f"t{i % 3}", {"ai"}))
                    self.store.get_node("t0")
                    self.store.list_nodes()
                    self.store.add_sample(VerifySample(
                        node_id="t0", kind="verify", success=True, latency_ms=10))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(self.store.count_nodes(), 3)


if __name__ == "__main__":
    unittest.main()
