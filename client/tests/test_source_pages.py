#!/usr/bin/env python3
"""
来源页结构测试（用户定，2026-11-19；task8/task23 落页改写）：
- 来源是第一组织维度：vpngate / manual / pipeline 三页
  （自 task23 起行为反转、依据 work8/DESIGN §7：OpenVPN 页与 VPNGate
  feed 页本就同一份 feed，合并为 vpngate 一页，work8/DESIGN §1）
- 跨页（vpngate/manual/pipeline 之间）同端点 = 两条独立记录，永不合并
- 同页同端点 = 同 id，upsert 不产生副本（feed 节点与手导 .ovpn 同落
  vpngate 页，同端点从此是一条记录）
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
        self.assertEqual(SOURCES, ("vpngate", "manual", "pipeline"))

    def test_same_endpoint_feed_vs_ovpn_is_one_record(self):
        """自 task23 起行为反转、依据 work8/DESIGN §7：四页时代同一端点在
        feed 页与 OpenVPN 页是两条记录；合并落页后两键归一为 vpngate，
        同端点同 id = upsert 一条记录，不再产生副本（合并口径与无损兜底
        见 work8/DESIGN §2/§3；存量迁移由 sources/landing.py 完成）。"""
        store = Store(":memory:")
        feed_nodes = feed_src.parse_feed_to_nodes(FEED_CSV)
        feed_node = next(n for n in feed_nodes if n.host == "vpn123456.opengw.net")
        # 把 .ovpn 的 remote 对齐到同一端点，模拟“同节点不同接入形态”
        ovpn_node = manual_src.parse_ovpn_text(
            make_ovpn("tcp", 443, "1.2.3.4").replace(
                "remote 1.2.3.4 443", "remote vpn123456.opengw.net 443"),
            name="same.ovpn")
        self.assertEqual(feed_node.host, ovpn_node.host)
        self.assertEqual(feed_node.source, "vpngate")          # 两键归一（§1）
        self.assertEqual(ovpn_node.source, "vpngate")
        self.assertEqual(feed_node.id, ovpn_node.id)           # 同端点同 id
        store.upsert_nodes([feed_node, ovpn_node])
        self.assertEqual(store.count_nodes(), 1)               # 一条记录
        self.assertEqual(len(store.list_nodes(source="vpngate")), 1)
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
            store.list_nodes(source="feed")   # 旧名已废弃，必须用来源枚举
        # 自 task23 起行为反转、依据 work8/DESIGN §7：openvpn / vpngate_feed
        # 两个旧键随合并退役，不再是合法页键（存量由启动迁移改写）。
        with self.assertRaises(ValueError):
            store.list_nodes(source="openvpn")
        with self.assertRaises(ValueError):
            store.list_nodes(source="vpngate_feed")
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
        self.assertEqual(positions, sorted(positions))         # 三页按定序输出
        self.assertIn("VPNGate 来源页", out)   # 合并页（task23 落页）
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
