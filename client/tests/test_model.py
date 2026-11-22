#!/usr/bin/env python3
"""core/model 离线测试。用法: python -m unittest discover -s client/tests -t . """
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.model import ExitProfile, Node, NodeMetrics, make_node_id


def sample_node():
    return Node(
        id=make_node_id("pipeline", "sstp", "vpn123456.opengw.net", 443),
        name="vpn123456.opengw.net:443 (JP)",
        protocol="sstp",
        source="pipeline",
        host="vpn123456.opengw.net",
        port=443,
        config_ref="sstp://vpn:vpn@vpn123456.opengw.net:443",
        exit_profile=ExitProfile(ip="5.6.7.8", country="Japan", country_code="JP",
                                 network_type="residential", org="NTT EAST",
                                 is_clean=True, dirty={"muse"}, verified_at=123.0),
        scenes={"support", "ai"},
        metrics=NodeMetrics(latency_ms=300, jitter_ms=50, uptime_hours=30.0,
                            sessions=10, speed_mbps=12.5),
        pinned=True,
        extra={"tier": "platinum"},
    )


class TestModel(unittest.TestCase):
    def test_roundtrip_dict(self):
        n = sample_node()
        n2 = Node.from_dict(n.to_dict())
        self.assertEqual(n, n2)
        self.assertEqual(n2.scenes, {"support", "ai"})
        self.assertEqual(n2.exit_profile.dirty, {"muse"})

    def test_roundtrip_json(self):
        n = sample_node()
        self.assertEqual(Node.from_json(n.to_json()), n)

    def test_make_id_stable_and_discriminated(self):
        a = make_node_id("vpngate", "openvpn", "h.example", 443)
        self.assertEqual(a, make_node_id("vpngate", "openvpn", "h.example", 443))
        b = make_node_id("manual", "vless", "h.example", 443, "uuid-1")
        c = make_node_id("manual", "vless", "h.example", 443, "uuid-2")
        self.assertNotEqual(b, c)
        self.assertTrue(b.startswith("manual:vless:h.example:443:"))
        # 来源是 ID 第一段：同端点跨页 = 不同 ID。
        # 自 task23 起行为反转、依据 work8/DESIGN §7：openvpn / vpngate_feed
        # 两个旧来源键退役（同页同端点的 feed 节点与手导 .ovpn 同 ID），
        # 跨页区分由 vpngate / manual / pipeline 承担。
        self.assertNotEqual(make_node_id("vpngate", "openvpn", "h.example", 443),
                            make_node_id("manual", "openvpn", "h.example", 443))

    def test_from_dict_defaults_and_scene_filter(self):
        n = Node.from_dict({"id": "x", "scenes": ["support", "bogus"]})
        self.assertEqual(n.scenes, {"support"})
        self.assertEqual(n.exit_profile.network_type, "unknown")
        self.assertEqual(n.metrics.sessions, 0)

    def test_exit_profile_bad_network_type_normalized(self):
        ep = ExitProfile.from_dict({"network_type": "weird", "dirty": ["muse"]})
        self.assertEqual(ep.network_type, "unknown")
        self.assertEqual(ep.dirty, {"muse"})


if __name__ == "__main__":
    unittest.main()
