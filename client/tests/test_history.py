#!/usr/bin/env python3
"""core/history 离线测试。"""
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core import history
from client.core.model import Node
from client.core.store import Store, VerifySample

DAY = 86400.0
T0 = datetime(2026, 11, 10, 12, 0, tzinfo=timezone.utc).timestamp()


def sample(ok=True, lat=100.0, ts=T0, kind="verify"):
    return VerifySample("n1", kind, ok, ts=ts, latency_ms=lat)


class TestStats(unittest.TestCase):
    def test_empty(self):
        st = history.compute_stats([])
        self.assertEqual(st.total, 0)
        self.assertEqual(st.success_rate, 0.0)
        self.assertIsNone(st.median_latency_ms)
        self.assertFalse(st.stable)
        self.assertIsNone(st.last_ok)

    def test_success_rate_and_median(self):
        samples = [sample(True, 100, T0), sample(True, 200, T0), sample(False, None, T0)]
        st = history.compute_stats(samples)
        self.assertEqual(st.total, 3)
        self.assertAlmostEqual(st.success_rate, 2 / 3)
        self.assertEqual(st.median_latency_ms, 150)

    def test_volatility_and_stability(self):
        self.assertIsNone(history.calc_volatility([100.0]))
        steady = [sample(True, 100 + (i % 2), T0 + i) for i in range(6)]
        st = history.compute_stats(steady)
        self.assertTrue(st.stable)
        wild = [sample(True, lat, T0 + i) for i, lat in enumerate([50, 500, 60, 600, 55])]
        self.assertFalse(history.compute_stats(wild).stable)

    def test_active_and_streak_days(self):
        samples = [sample(True, 100, T0), sample(True, 100, T0 + DAY),
                   sample(True, 100, T0 + 2 * DAY), sample(True, 100, T0 + 4 * DAY)]
        st = history.compute_stats(samples)
        self.assertEqual(st.active_days, 4)
        self.assertEqual(st.streak_days, 1)      # 最近一天与前一天隔了 2 天
        samples2 = [sample(True, 100, T0 + i * DAY) for i in range(3)]
        self.assertEqual(history.compute_stats(samples2).streak_days, 3)


class TestSortAndRecord(unittest.TestCase):
    def test_record_and_stats_via_store(self):
        store = Store(":memory:")
        history.record(store, "n1", "verify", True, latency_ms=120, ts=T0)
        history.record(store, "n1", "verify", True, latency_ms=130, ts=T0 + 1)
        st = history.stats_for(store, "n1")
        self.assertEqual(st.success_count, 2)
        store.close()

    def test_last_result(self):
        store = Store(":memory:")
        self.assertIsNone(history.last_result(store, "n1"))
        history.record(store, "n1", "verify", False, ts=T0)
        history.record(store, "n1", "disconnect", True, ts=T0 + 1)  # 不算测速结果
        self.assertEqual(history.last_result(store, "n1"), (False, T0))
        history.record(store, "n1", "connect", True, ts=T0 + 2)
        self.assertEqual(history.last_result(store, "n1"), (True, T0 + 2))
        store.close()

    def test_sort_prefers_pinned_then_reliable(self):
        store = Store(":memory:")
        a = Node(id="a", name="a", protocol="sstp", source="pipeline")
        b = Node(id="b", name="b", protocol="sstp", source="pipeline")
        c = Node(id="c", name="c", protocol="sstp", source="pipeline", pinned=True)
        for nid, lats in (("a", [100, 110]), ("b", [50, 55]), ("c", [900, 950])):
            for i, lat in enumerate(lats):
                history.record(store, nid, "verify", True, latency_ms=lat, ts=T0 + i)
        ordered = history.sorted_nodes(store, [a, b, c])
        self.assertEqual([n.id for n in ordered], ["c", "b", "a"])  # pin 第一，其次低延迟
        store.close()

    def test_last_ok_tracks_latest_link_sample(self):
        """last_ok 只看最近一条 connect/verify：disconnect 是用户主动断开，
        不代表链路能用；drop 是巡检告警，也不算。"""
        store = Store(":memory:")
        history.record(store, "n1", "verify", True, latency_ms=120, ts=T0)
        self.assertIs(history.stats_for(store, "n1").last_ok, True)
        history.record(store, "n1", "verify", False, ts=T0 + 1)
        self.assertIs(history.stats_for(store, "n1").last_ok, False)
        # 主动断开（success=True）不许把 last_ok 翻回 True
        history.record(store, "n1", "disconnect", True, ts=T0 + 2)
        self.assertIs(history.stats_for(store, "n1").last_ok, False)
        store.close()

    def test_sort_recent_ok_before_untested_before_dead(self):
        """排序新口径（2026-11-21 需求：能用的节点一直放前面）：
        最近成功 > 从未测过 > 最近失败；曾经全通但刚挂的沉底。"""
        store = Store(":memory:")
        ok1 = Node(id="ok1", name="ok节点", protocol="sstp", source="pipeline")
        fresh = Node(id="fresh1", name="没测过", protocol="sstp", source="pipeline")
        dead = Node(id="dead1", name="死节点", protocol="sstp", source="pipeline")
        history.record(store, "ok1", "verify", True, latency_ms=900, ts=T0 + 10)
        # dead 曾经秒通（延迟 50ms 优于 ok1），但最近一次挂了——必须排后面
        history.record(store, "dead1", "verify", True, latency_ms=50, ts=T0)
        history.record(store, "dead1", "verify", False, ts=T0 + 5)
        ordered = history.sorted_nodes(store, [dead, fresh, ok1])
        self.assertEqual([n.id for n in ordered], ["ok1", "fresh1", "dead1"])
        store.close()


if __name__ == "__main__":
    unittest.main()
