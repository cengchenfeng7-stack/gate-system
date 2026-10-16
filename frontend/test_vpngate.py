#!/usr/bin/env python3
"""
vpngate.py 离线测试 —— 不需要网络、不需要 Worker。
用法: python -m unittest test_vpngate -v
"""
import base64
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import vpngate as vp


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def make_ovpn(proto="tcp", port=443, host="1.2.3.4", extra=""):
    return (
        f"dev tun\nproto {proto}\nremote {host} {port}\n"
        "cipher AES-128-CBC\nauth SHA1\nresolv-retry infinite\n"
        "nobind\npersist-key\npersist-tun\nclient\nverb 3\n"
        "<ca>\n-----BEGIN CERTIFICATE-----\nMIIFAKE\n-----END CERTIFICATE-----\n</ca>\n"
        f"{extra}"
    )


CSV_HEADER = ("#HostName,IP,Score,Ping,Speed,CountryLong,CountryShort,NumVpnSessions,Uptime,"
              "TotalUsers,TotalTraffic,LogType,Operator,Message,OpenVPN_ConfigData_Base64")


def csv_line(host, score, country, cc, cfg_text, ping=20, speed=50000000, sessions=5, uptime=7200000):
    cfg = b64(cfg_text) if cfg_text else ""
    return f"{host},9.9.9.9,{score},{ping},{speed},{country},{cc},{sessions},{uptime},10,100,2weeks,op,msg,{cfg}"


def make_feed(lines):
    return "*vpn_servers\n" + CSV_HEADER + "\n" + "\n".join(lines) + "\n*\n"


class TestParseCsv(unittest.TestCase):
    def test_basic_parsing(self):
        feed = make_feed([csv_line("vpn100", 900, "Japan", "JP", make_ovpn())])
        rows = vp.parse_csv(feed)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["host"], "vpn100")
        self.assertEqual(rows[0]["country_short"], "JP")

    def test_star_prefix_not_skipped(self):
        feed = make_feed([csv_line("vpn100", 900, "Japan", "JP", make_ovpn())])
        feed = feed.replace("vpn100,", "*vpn100,", 1)
        rows = vp.parse_csv(feed)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["host"], "vpn100")

    def test_missing_columns_safe_default(self):
        # parse_csv 要求至少 7 列，不足的行被跳过
        feed = "*vpn_servers\n#HostName,IP,Score\nvpn100,9.9.9.9,900\n*\n"
        rows = vp.parse_csv(feed)
        self.assertEqual(len(rows), 0)
        # 7 列但缺省列安全默认
        feed2 = "*vpn_servers\n#HostName,IP,Score,Ping,Speed,CountryLong,CountryShort\nvpn100,9.9.9.9,900,20,5000,Japan,JP\n*\n"
        rows2 = vp.parse_csv(feed2)
        self.assertEqual(len(rows2), 1)
        self.assertEqual(rows2[0]["host"], "vpn100")
        self.assertEqual(rows2[0]["speed_bps"], 5000)

    def test_empty_feed_raises(self):
        with self.assertRaises(Exception):
            vp.parse_csv("no header here")

    def test_uptime_zero_means_unknown(self):
        feed = make_feed([csv_line("vpn100", 900, "Japan", "JP", make_ovpn(), uptime=0)])
        rows = vp.parse_csv(feed)
        self.assertEqual(rows[0]["uptime_ms"], 0)


class TestToSstpNodes(unittest.TestCase):
    def test_tcp_node_extracted(self):
        feed = make_feed([csv_line("vpn100", 900, "Japan", "JP", make_ovpn("tcp", 443))])
        rows = vp.parse_csv(feed)
        nodes = vp.to_sstp_nodes(rows)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0]["host"], "vpn100.opengw.net")
        self.assertEqual(nodes[0]["port"], 443)

    def test_udp_node_excluded(self):
        feed = make_feed([csv_line("vpn100", 900, "Japan", "JP", make_ovpn("udp", 1194))])
        rows = vp.parse_csv(feed)
        nodes = vp.to_sstp_nodes(rows)
        self.assertEqual(len(nodes), 0)

    def test_host_suffix_added(self):
        feed = make_feed([csv_line("vpn100", 900, "Japan", "JP", make_ovpn())])
        rows = vp.parse_csv(feed)
        nodes = vp.to_sstp_nodes(rows)
        self.assertTrue(nodes[0]["host"].endswith(".opengw.net"))

    def test_already_suffixed(self):
        feed = make_feed([csv_line("vpn100.opengw.net", 900, "Japan", "JP", make_ovpn())])
        rows = vp.parse_csv(feed)
        nodes = vp.to_sstp_nodes(rows)
        self.assertEqual(nodes[0]["host"], "vpn100.opengw.net")

    def test_invalid_port_excluded(self):
        feed = make_feed([csv_line("vpn100", 900, "Japan", "JP", make_ovpn("tcp", 99999))])
        rows = vp.parse_csv(feed)
        nodes = vp.to_sstp_nodes(rows)
        self.assertEqual(len(nodes), 0)


class TestEvaluateStability(unittest.TestCase):
    def _node(self, **kw):
        base = {
            "host": "vpn100.opengw.net", "port": 443, "success": True,
            "latency_ms": 2500, "jitter_ms": 200, "uptime_hours": 20.0,
            "sessions": 50, "speed_mbps": 100.0, "residential": "residential",
            "exit": {"is_clean": True, "is_abuser": False, "is_tor": False, "is_bogon": False},
        }
        base.update(kw)
        return base

    def test_support_ready(self):
        node = self._node()
        result = vp.evaluate_stability(node)
        self.assertTrue(result["support_ready"])

    def test_game_ready(self):
        node = self._node(latency_ms=300, jitter_ms=50)
        result = vp.evaluate_stability(node)
        self.assertTrue(result["game_ready"])

    def test_dirty_ip_blocked(self):
        node = self._node()
        node["exit"]["is_clean"] = None  # 让函数自己算
        node["exit"]["is_abuser"] = True
        result = vp.evaluate_stability(node)
        self.assertFalse(result["support_ready"])
        self.assertFalse(result["ai_ready"])

    def test_first_round_no_history_stable(self):
        node = self._node()
        result = vp.evaluate_stability(node, None)
        self.assertTrue(result["is_stable"])

    def test_high_latency_not_stable_first_round(self):
        node = self._node(latency_ms=6000)
        result = vp.evaluate_stability(node, None)
        self.assertFalse(result["is_stable"])

    def test_volatility_calculation(self):
        self.assertIsNone(vp.calc_volatility([]))
        self.assertIsNone(vp.calc_volatility([100]))
        vol = vp.calc_volatility([100, 100, 100])
        self.assertAlmostEqual(vol, 0.0)
        vol2 = vp.calc_volatility([100, 200, 300])
        self.assertGreater(vol2, 0.3)


class TestClassifyNetwork(unittest.TestCase):
    def test_datacenter_by_org(self):
        self.assertEqual(vp.classify_network("host", "AMAZON AWS", None), "datacenter")

    def test_residential_by_org(self):
        self.assertEqual(vp.classify_network("host", "KDDI CORPORATION", None), "residential")

    def test_public_vpn_is_datacenter(self):
        self.assertEqual(vp.classify_network("public-vpn-1", "", None), "datacenter")

    def test_vpn_numbered_is_residential(self):
        self.assertEqual(vp.classify_network("vpn12345", "", None), "residential")


class TestMaskWorkerUrl(unittest.TestCase):
    def test_masking(self):
        masked = vp.mask_worker_url("https://myworker.workers.dev/check?sstp=vpn:vpn@")
        self.assertIn("***", masked)
        self.assertNotIn("myworker", masked)

    def test_invalid_url(self):
        masked = vp.mask_worker_url("not a url")
        self.assertIn("***", masked)


class TestLoadHostList(unittest.TestCase):
    def test_ban_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("vpn12345.opengw.net\n# comment\nvpn678.opengw.net:443\n")
            f.flush()
            hosts = vp.load_host_list(f.name)
        os.unlink(f.name)
        self.assertEqual(hosts, {"vpn12345.opengw.net", "vpn678.opengw.net:443"})

    def test_missing_file(self):
        hosts = vp.load_host_list("/nonexistent/path.txt")
        self.assertEqual(hosts, set())


class TestBuildNodesText(unittest.TestCase):
    def test_basic_output(self):
        data = {
            "countries": {"Japan": {"code": "JP", "nodes": [{
                "host": "vpn1.opengw.net", "port": 443, "country_code": "JP",
                "residential": "residential", "tier": "standard",
                "pinned": False, "latency_ms": 2500, "jitter_ms": 200,
            }]}},
            "edge_hosts": ["edge1.example.com:443"],
        }
        text = vp.build_nodes_text(data, mode="all")
        self.assertIn("vpn1.opengw.net:443", text)
        self.assertIn("sstp://", text)

    def test_empty_data(self):
        data = {"countries": {}, "edge_hosts": ["edge1.example.com:443"]}
        text = vp.build_nodes_text(data, mode="all")
        self.assertIn("无", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
