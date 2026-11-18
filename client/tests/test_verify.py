#!/usr/bin/env python3
"""core/verify 离线测试：纯函数 + 假 probe 编排，绝不发网络请求。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.model import Node, NodeMetrics
from client.core.verify import (ProbeResult, SceneInput, apply_outcome,
                                classify_network, derive_is_clean,
                                evaluate_scenes, org_looks_datacenter,
                                verdict_muse, verify_exit)


def make_node(**metrics_kw):
    return Node(id="sstp:vpn123456.opengw.net:443", name="n", protocol="sstp",
                source="pipeline", host="vpn123456.opengw.net", port=443,
                metrics=NodeMetrics(**metrics_kw))


class TestClassifyNetwork(unittest.TestCase):
    def test_is_datacenter_flag_wins(self):
        self.assertEqual(classify_network("vpn123456.opengw.net", "NTT EAST", True), "datacenter")
        self.assertEqual(classify_network("public-vpn-1.opengw.net", "GOOGLE", False), "residential")

    def test_org_keywords(self):
        self.assertEqual(classify_network("x.example", "Google LLC", None), "datacenter")
        self.assertEqual(classify_network("x.example", "NTT EAST", None), "residential")
        self.assertEqual(classify_network("x.example", "Some Small ISP", None), "unknown")

    def test_host_patterns(self):
        self.assertEqual(classify_network("public-vpn-78.opengw.net", None, None), "datacenter")
        self.assertEqual(classify_network("vpn123456.opengw.net", None, None), "residential")
        self.assertEqual(classify_network("vpnv12.opengw.net", None, None), "residential")
        self.assertEqual(classify_network("random.example.com", None, None), "unknown")

    def test_org_looks_datacenter_hints(self):
        self.assertTrue(org_looks_datacenter("Cloudflare, Inc."))
        self.assertFalse(org_looks_datacenter("NTT East"))


class TestVerdictMuse(unittest.TestCase):
    def test_codes(self):
        self.assertTrue(verdict_muse(307)[0])
        self.assertTrue(verdict_muse(200)[0])
        self.assertFalse(verdict_muse(400)[0])
        self.assertFalse(verdict_muse(403)[0])
        self.assertFalse(verdict_muse(0)[0])
        self.assertFalse(verdict_muse(999)[0])


class TestDeriveIsClean(unittest.TestCase):
    def test_cases(self):
        self.assertIsNone(derive_is_clean(None))
        self.assertIsNone(derive_is_clean({}))
        self.assertTrue(derive_is_clean({"is_clean": True}))
        self.assertFalse(derive_is_clean({"is_abuser": True}))
        self.assertTrue(derive_is_clean({"is_abuser": False, "is_tor": False}))


class TestEvaluateScenes(unittest.TestCase):
    def base(self, **kw):
        d = dict(success=True, is_clean=True, residential="residential",
                 uptime_hours=30.0, sessions=10, jitter_ms=50.0,
                 latency_ms=300.0, speed_mbps=20.0, stable=True)
        d.update(kw)
        return SceneInput(**d)

    def test_all_scenes_pass(self):
        self.assertEqual(evaluate_scenes(self.base()),
                         {"support", "ai", "games", "download"})

    def test_failed_node_no_scenes(self):
        self.assertEqual(evaluate_scenes(self.base(success=False)), set())

    def test_support_requires_residential_and_clean(self):
        self.assertNotIn("support", evaluate_scenes(self.base(residential="datacenter")))
        self.assertNotIn("support", evaluate_scenes(self.base(is_clean=False)))
        self.assertNotIn("support", evaluate_scenes(self.base(is_clean=None)))

    def test_support_thresholds(self):
        self.assertNotIn("support", evaluate_scenes(self.base(uptime_hours=5.0)))   # <6h
        self.assertNotIn("support", evaluate_scenes(self.base(sessions=151)))       # >150
        self.assertNotIn("support", evaluate_scenes(self.base(jitter_ms=None)))     # 未复测
        self.assertNotIn("support", evaluate_scenes(self.base(latency_ms=3501)))

    def test_ai_requires_clean_but_not_residential(self):
        scenes = evaluate_scenes(self.base(residential="datacenter"))
        self.assertIn("ai", scenes)
        self.assertNotIn("ai", evaluate_scenes(self.base(is_clean=None)))
        self.assertNotIn("ai", evaluate_scenes(self.base(uptime_hours=2.0)))        # <3h

    def test_games_allows_unknown_clean_and_short_uptime(self):
        scenes = evaluate_scenes(self.base(is_clean=None, uptime_hours=1.5))
        self.assertIn("games", scenes)
        self.assertNotIn("games", evaluate_scenes(self.base(is_clean=False)))
        self.assertNotIn("games", evaluate_scenes(self.base(latency_ms=4001)))

    def test_download_requires_stable_and_bandwidth(self):
        self.assertNotIn("download", evaluate_scenes(self.base(stable=False)))
        self.assertNotIn("download", evaluate_scenes(self.base(speed_mbps=1.0)))
        self.assertIn("download", evaluate_scenes(self.base(latency_ms=4999)))


class TestVerifyExitWithFakeProbe(unittest.TestCase):
    def test_ok_flow_updates_profile_and_scenes(self):
        node = make_node(uptime_hours=30.0, sessions=10, speed_mbps=20.0)
        probe = lambda n: ProbeResult(  # noqa: E731
            alive=True, exit_ip="5.6.7.8", country="Japan", country_code="JP",
            org="NTT EAST", is_clean=True, latency_ms=300.0, jitter_ms=50.0,
            muse_code=307)
        out = verify_exit(node, probe, stable=True)
        self.assertTrue(out.ok)
        self.assertTrue(out.muse_ok)
        apply_outcome(node, out)
        self.assertEqual(node.exit_profile.ip, "5.6.7.8")
        self.assertEqual(node.exit_profile.network_type, "residential")
        self.assertIn("support", node.scenes)

    def test_dead_link_not_ok_and_profile_untouched(self):
        node = make_node()
        out = verify_exit(node, lambda n: ProbeResult(alive=False, error="链路不通"))
        self.assertFalse(out.ok)
        apply_outcome(node, out)
        self.assertIsNone(node.exit_profile.ip)

    def test_muse_rejected_marks_dirty(self):
        node = make_node(uptime_hours=30.0, sessions=10)
        probe = lambda n: ProbeResult(  # noqa: E731
            alive=True, exit_ip="9.9.9.9", org="Cloudflare", is_datacenter=True,
            is_clean=True, latency_ms=100.0, jitter_ms=10.0, muse_code=400)
        out = verify_exit(node, probe)
        self.assertTrue(out.ok)
        self.assertFalse(out.muse_ok)
        self.assertIn("muse", out.exit_profile.dirty)
        self.assertEqual(out.exit_profile.network_type, "datacenter")

    def test_dirty_ip_flagged(self):
        node = make_node()
        probe = lambda n: ProbeResult(  # noqa: E731
            alive=True, exit_ip="9.9.9.9", org="Some ISP", is_abuser=True,
            latency_ms=100.0, jitter_ms=10.0)
        out = verify_exit(node, probe)
        self.assertIn("dirty_ip", out.exit_profile.dirty)
        self.assertFalse(out.exit_profile.is_clean)
        self.assertNotIn("ai", out.scenes)


if __name__ == "__main__":
    unittest.main()
