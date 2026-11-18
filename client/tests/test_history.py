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


if __name__ == "__main__":
    unittest.main()
