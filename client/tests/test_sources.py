#!/usr/bin/env python3
"""sources 离线测试：fixture 严格按真实产物 schema 构造（见各注释）。"""
import base64
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.sources import feed as feed_src
from client.sources import manual as manual_src
from client.sources import pipeline as pipeline_src


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def make_ovpn(proto="tcp", port=443, host="1.2.3.4"):
    # 与 test_vpngate.make_ovpn 同款结构
    return (f"dev tun\nproto {proto}\nremote {host} {port}\n"
            "cipher AES-128-CBC\nauth SHA1\nresolv-retry infinite\n"
            "nobind\npersist-key\npersist-tun\nclient\nverb 3\n"
            "<ca>\n-----BEGIN CERTIFICATE-----\nMIIFAKE\n-----END CERTIFICATE-----\n</ca>\n")


# ---- data.json fixture：结构对齐 vpngate.build_outputs / check_one / evaluate_stability
DATA_JSON = {
    "generated_at": "2026-10-17 12:00:00 UTC",
    "source": "fixture",
    "worker": "https://***.workers.dev",
    "edge_hosts": ["saas.sin.fan:443"],
    "stats": {"success": 2},
    "countries": {},
    "available": [
        {
            "host": "vpn123456.opengw.net", "port": 443, "ip": "1.2.3.4",
            "country": "Japan", "country_code": "JP",
            "uptime_hours": 30.0, "sessions": 10, "speed_mbps": 12.5,
            "score": 100, "ping_feed": 20,
            "protocol": "sstp", "link": "sstp://vpn:vpn@vpn123456.opengw.net:443",
            "status": "success", "success": True, "checked_at": "2026-10-17 12:00 UTC",
            "latency_ms": 300, "jitter_ms": 50,
            "exit": {"ip": "5.6.7.8", "country": "Japan", "country_code": "JP",
                     "city": "Tokyo", "continent": "AS", "asn": 2500,
                     "org": "NTT EAST", "type": "isp", "is_datacenter": False,
                     "is_abuser": False, "is_tor": False, "is_bogon": False,
                     "is_clean": True},
            "residential": "residential",
            "tier": "platinum", "tier_label": "⭐客服稳连", "composite_score": 500.0,
            "support_ready": True, "game_ready": True, "speed_ready": True,
            "ai_ready": True, "is_clean": True, "fish_ready": False,
            "is_stable": True, "volatility": 0.1,
            "region_group": "asia", "is_datacenter_node": False, "pinned": False,
        },
        {
            "host": "public-vpn-78.opengw.net", "port": 443, "ip": "9.9.9.9",
            "country": "United States", "country_code": "US",
            "uptime_hours": 300.0, "sessions": 200, "speed_mbps": 50.0,
            "score": 90, "ping_feed": 30,
            "protocol": "sstp", "link": "sstp://vpn:vpn@public-vpn-78.opengw.net:443",
            "status": "success", "success": True, "checked_at": "2026-10-17 12:00 UTC",
            "latency_ms": 800, "jitter_ms": None,
            "exit": {"ip": "9.9.9.10", "country": "United States", "country_code": "US",
                     "city": "NYC", "continent": "NA", "asn": 13335,
                     "org": "Cloudflare", "type": "hosting", "is_datacenter": True,
                     "is_abuser": True, "is_tor": False, "is_bogon": False,
                     "is_clean": False},
            "residential": "datacenter",
            "tier": "standard", "tier_label": "普通可用", "composite_score": 2000.0,
            "support_ready": False, "game_ready": False, "speed_ready": False,
            "ai_ready": False, "is_clean": False, "fish_ready": False,
            "is_stable": False, "volatility": None,
            "region_group": "west", "is_datacenter_node": True, "pinned": True,
        },
    ],
    "datacenter_nodes": [], "datacenter_premium": [], "fish_nodes": [], "watch_nodes": [],
}

# ---- nodes.txt fixture：行格式对齐 vpngate.build_nodes_text
NODES_TXT = (
    "saas.sin.fan:443#日本-稳连-01$sstp://vpn:vpn@vpn123456.opengw.net:443\n"
    "cdn.204910.best:443#美国-机房-01$sstp://vpn:vpn@public-vpn-78.opengw.net:443\n"
)

# ---- subscription.txt fixture：行格式对齐 vpngate.build_subscription_text
SUB_TXT = (
    "# ============================================\n"
    "# 日本 住宅 01 | 延迟=300ms 抖动=50ms 在线=30h 会话=10 场景=客服/AI\n"
    "sstp://vpn:vpn@vpn123456.opengw.net:443#%E6%97%A5%E6%9C%AC-%E4%BD%8F%E5%AE%85-01\n"
)

# ---- feed CSV fixture：表头/行格式对齐官方 feed（同 test_vpngate 的构造法）
CSV_HEADER = ("#HostName,IP,Score,Ping,Speed,CountryLong,CountryShort,NumVpnSessions,"
              "Uptime,TotalUsers,TotalTraffic,LogType,Operator,Message,"
              "OpenVPN_ConfigData_Base64")


def csv_line(host, cfg_text, cc="JP", country="Japan", sessions=5, uptime=7200000):
    cfg = b64(cfg_text) if cfg_text else ""
    return (f"{host},9.9.9.9,100,20,50000000,{country},{cc},{sessions},{uptime},"
            f"10,100,2weeks,op,msg,{cfg}")


FEED_CSV = "\n".join([
    "*vpn_servers",
    CSV_HEADER,
    csv_line("vpn123456", make_ovpn("tcp", 443, "1.2.3.4")),
    csv_line("vpn99999", make_ovpn("udp", 1194, "5.5.5.5"), cc="US", country="United States"),
    csv_line("vpn00000", ""),   # 无配置 → 应被 rows_to_feed_configs 过滤
]) + "\n"

MIRROR_JSON = json.dumps([{"servers": [{
    "hostname": "vpn55555", "ip": "3.3.3.3", "score": 10, "ping": 30,
    "speed": 20000000, "countrylong": "Korea", "countryshort": "KR",
    "numvpnsessions": 3, "uptime": 3600000,
    "openvpn_configdata_base64": b64(make_ovpn("tcp", 443, "3.3.3.3")),
}]}])


class TestPipelineDataJson(unittest.TestCase):
    def test_parse_available(self):
        nodes = pipeline_src.parse_data_json(json.dumps(DATA_JSON))
        self.assertEqual(len(nodes), 2)
        n = nodes[0]
        self.assertEqual(n.protocol, "sstp")
        self.assertEqual(n.source, "pipeline")
        self.assertEqual(n.scenes, {"support", "games", "download", "ai"})
        self.assertEqual(n.exit_profile.ip, "5.6.7.8")
        self.assertEqual(n.exit_profile.network_type, "residential")
        self.assertTrue(n.exit_profile.is_clean)
        self.assertEqual(n.metrics.latency_ms, 300)

    def test_dirty_exit_marked(self):
        n = pipeline_src.parse_data_json(json.dumps(DATA_JSON))[1]
        self.assertIn("abuser", n.exit_profile.dirty)
        self.assertIn("dirty_ip", n.exit_profile.dirty)
        self.assertFalse(n.exit_profile.is_clean)
        self.assertTrue(n.pinned)   # data.json 的 pinned 字段带过来

    def test_nodes_text_format(self):
        nodes = pipeline_src.parse_nodes_text(NODES_TXT)
        self.assertEqual(len(nodes), 2)
        self.assertEqual(nodes[0].host, "vpn123456.opengw.net")
        self.assertEqual(nodes[0].protocol, "sstp")
        self.assertEqual(nodes[0].source, "pipeline")

    def test_subscription_text_format(self):
        nodes = pipeline_src.parse_nodes_text(SUB_TXT)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].host, "vpn123456.opengw.net")


class TestFeed(unittest.TestCase):
    def test_csv_to_nodes(self):
        nodes = feed_src.parse_feed_to_nodes(FEED_CSV)
        hosts = {n.host for n in nodes}
        self.assertEqual(hosts, {"vpn123456.opengw.net", "vpn99999.opengw.net"})
        n = next(n for n in nodes if n.host.startswith("vpn123456"))
        self.assertEqual(n.protocol, "openvpn")
        self.assertEqual(n.source, "vpngate_feed")   # feed 页，不与 OpenVPN 页合并
        self.assertEqual(n.port, 443)
        self.assertEqual(n.extra["config_format"], "b64")
        self.assertEqual(n.metrics.sessions, 5)
        self.assertEqual(n.metrics.uptime_hours, 2.0)

    def test_mirror_json_to_nodes(self):
        nodes = feed_src.parse_feed_to_nodes(MIRROR_JSON)
        self.assertEqual(len(nodes), 1)
        self.assertEqual(nodes[0].host, "vpn55555.opengw.net")
        self.assertEqual(nodes[0].extra["country_code"], "KR")

    def test_bad_csv_raises(self):
        with self.assertRaises(RuntimeError):
            feed_src.parse_feed_text("not,a,feed\n1,2,3")


class TestManual(unittest.TestCase):
    def test_vless_link(self):
        n = manual_src.parse_link(
            "vless://495c7195-85b8-498a-bf20-2ea9ce9175b5@162.159.135.234:443"
            "?encryption=none&security=tls&type=ws&host=example.com#US%20node")
        self.assertEqual(n.protocol, "vless")
        self.assertEqual(n.host, "162.159.135.234")
        self.assertEqual(n.port, 443)
        self.assertEqual(n.extra["uuid"], "495c7195-85b8-498a-bf20-2ea9ce9175b5")
        self.assertEqual(n.extra["query"]["type"], "ws")
        self.assertEqual(n.name, "US node")

    def test_ss_link_plain_and_b64_userinfo(self):
        n = manual_src.parse_link("ss://aes-256-gcm:secret@1.2.3.4:8388#ss1")
        self.assertEqual(n.extra["method"], "aes-256-gcm")
        self.assertEqual(n.extra["password"], "secret")
        ui = base64.urlsafe_b64encode(b"chacha20-ietf-poly1305:pw2").decode().rstrip("=")
        n2 = manual_src.parse_link(f"ss://{ui}@5.6.7.8:8389#ss2")
        self.assertEqual(n2.extra["method"], "chacha20-ietf-poly1305")
        self.assertEqual(n2.extra["password"], "pw2")

    def test_trojan_and_sstp_links(self):
        n = manual_src.parse_link("trojan://pw@h.example:443?sni=h.example#t1")
        self.assertEqual(n.extra["password"], "pw")
        s = manual_src.parse_link("sstp://vpn:vpn@vpn123456.opengw.net:443")
        self.assertEqual(s.source, "manual")
        self.assertEqual(s.extra["username"], "vpn")
        self.assertEqual(s.host, "vpn123456.opengw.net")

    def test_bad_link_raises(self):
        with self.assertRaises(ValueError):
            manual_src.parse_link("http://example.com/x")
        with self.assertRaises(ValueError):
            manual_src.parse_link("not a link")

    def test_ovpn_text(self):
        n = manual_src.parse_ovpn_text(make_ovpn("tcp", 1443, "7.7.7.7"), name="mine.ovpn")
        self.assertEqual(n.protocol, "openvpn")
        self.assertEqual(n.source, "openvpn")   # .ovpn 一律归 OpenVPN 页
        self.assertEqual(n.host, "7.7.7.7")
        self.assertEqual(n.port, 1443)
        self.assertEqual(n.extra["config_format"], "text")
        with self.assertRaises(ValueError):
            manual_src.parse_ovpn_text("dev tun\nclient\n")


if __name__ == "__main__":
    unittest.main()
