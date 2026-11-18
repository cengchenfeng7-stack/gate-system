#!/usr/bin/env python3
"""
来源四页结构测试（用户定，2026-11-19）：
- 来源是第一组织维度：openvpn / vpngate_feed / manual / pipeline 四页
- 同一端点从不同来源进来 = 两条独立记录，永不合并
- 同来源重复导入 = upsert，不产生副本
- CLI list 默认按来源分组，--source 只看一页
"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.model import SOURCES
from client.core.store import Store
from client.sources import feed as feed_src
from client.sources import manual as manual_src
from client.sources import pipeline as pipeline_src
from client.tests.test_sources import DATA_JSON, FEED_CSV, make_ovpn
from client.ui import cli


class TestSourceSeparation(unittest.TestCase):
    def test_source_enum_exact(self):
        self.assertEqual(SOURCES, ("openvpn", "vpngate_feed", "manual", "pipeline"))

    def test_same_endpoint_feed_vs_ovpn_are_two_records(self):
        store = Store(":memory:")
        feed_nodes = feed_src.parse_feed_to_nodes(FEED_CSV)
        feed_node = next(n for n in feed_nodes if n.host == "vpn123456.opengw.net")
        # 把 .ovpn 的 remote 对齐到同一端点，模拟“同节点不同来源”
        ovpn_node = manual_src.parse_ovpn_text(
            make_ovpn("tcp", 443, "1.2.3.4").replace(
                "remote 1.2.3.4 443", "remote vpn123456.opengw.net 443"),
            name="same.ovpn")
        self.assertEqual(feed_node.host, ovpn_node.host)
        self.assertNotEqual(feed_node.id, ovpn_node.id)
        self.assertEqual(feed_node.source, "vpngate_feed")
        self.assertEqual(ovpn_node.source, "openvpn")
        store.upsert_nodes([feed_node, ovpn_node])
        self.assertEqual(store.count_nodes(), 2)               # 两条独立记录
        self.assertEqual(len(store.list_nodes(source="vpngate_feed")), 1)
        self.assertEqual(len(store.list_nodes(source="openvpn")), 1)
        store.close()

    def test_same_endpoint_pipeline_vs_manual_link(self):
        store = Store(":memory:")
        p = pipeline_src.parse_data_json(json.dumps(DATA_JSON))[0]
        m = manual_src.parse_link("sstp://vpn:vpn@vpn123456.opengw.net:443")
        self.assertNotEqual(p.id, m.id)
        store.upsert_nodes([p, m])
        self.assertEqual(store.count_nodes(), 2)
        self.assertEqual(store.list_nodes(source="pipeline")[0].id, p.id)
        self.assertEqual(store.list_nodes(source="manual")[0].id, m.id)
        store.close()

    def test_reimport_same_source_no_duplicates(self):
        store = Store(":memory:")
        nodes = feed_src.parse_feed_to_nodes(FEED_CSV)
        store.upsert_nodes(nodes)
        store.upsert_nodes(feed_src.parse_feed_to_nodes(FEED_CSV))
        self.assertEqual(store.count_nodes(), len(nodes))
        store.close()

    def test_store_rejects_unknown_source(self):
        store = Store(":memory:")
        with self.assertRaises(ValueError):
            store.list_nodes(source="feed")   # 旧名已废弃，必须用四页枚举
        store.close()


def run_cli(argv, **kw):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv, **kw)
    return rc, out.getvalue(), err.getvalue()


class TestCliGroupedList(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "client.db")
        data_path = os.path.join(self.tmp.name, "data.json")
        with open(data_path, "w", encoding="utf-8") as f:
            json.dump(DATA_JSON, f)
        feed_path = os.path.join(self.tmp.name, "feed.csv")
        with open(feed_path, "w", encoding="utf-8") as f:
            f.write(FEED_CSV)
        run_cli(["--db", self.db, "intake", "--pipeline-data", data_path])
        run_cli(["--db", self.db, "intake", "--feed", feed_path])
        run_cli(["--db", self.db, "intake", "--link",
                 "vless://495c7195-85b8-498a-bf20-2ea9ce9175b5@162.159.135.234:443#us"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_list_groups_by_source_in_order(self):
        rc, out, _ = run_cli(["--db", self.db, "list"])
        self.assertEqual(rc, 0)
        positions = [out.index(f"== {src}（") for src in SOURCES]
        self.assertEqual(positions, sorted(positions))         # 四页按定序输出
        self.assertIn("OpenVPN 节点页", out)
        self.assertIn("自家流水线产物页", out)

    def test_source_filter_shows_one_page(self):
        rc, out, _ = run_cli(["--db", self.db, "list", "--source", "manual"])
        self.assertEqual(rc, 0)
        self.assertIn("== manual（", out)
        self.assertNotIn("== pipeline（", out)
        self.assertIn("162.159.135.234", out)

    def test_scene_is_within_page_filter(self):
        rc, out, _ = run_cli(["--db", self.db, "list", "--source", "pipeline",
                              "--scene", "support"])
        self.assertEqual(rc, 0)
        self.assertIn("vpn123456", out)
        self.assertNotIn("public-vpn-78", out)


if __name__ == "__main__":
    unittest.main()
