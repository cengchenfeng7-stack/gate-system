#!/usr/bin/env python3
"""ui/cli 离线测试：临时库 + 假 probe/假引擎注入，绝不触网、绝不拨号。"""
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.verify import ProbeResult
from client.tests.test_sources import DATA_JSON
from client.ui import cli


def fake_probe_factory():
    return lambda node: ProbeResult(
        alive=True, exit_ip="5.6.7.8", country="Japan", country_code="JP",
        org="NTT EAST", is_clean=True, latency_ms=300.0, jitter_ms=50.0, muse_code=307)


class FakeEngine:
    name = "openvpn"

    def __init__(self):
        self.alive = True

    def start(self, node, on_event=None):
        self.alive = True
        return {"node_id": node.id}

    def stop(self, handle):
        self.alive = False

    def status(self, handle):
        return "running" if self.alive else "stopped"


def run_cli(argv, **kw):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv, **kw)
    return rc, out.getvalue(), err.getvalue()


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "client.db")
        self.data_path = os.path.join(self.tmp.name, "data.json")
        with open(self.data_path, "w", encoding="utf-8") as f:
            json.dump(DATA_JSON, f)

    def tearDown(self):
        self.tmp.cleanup()

    def test_intake_list_pin_verify_flow(self):
        rc, out, _ = run_cli(["--db", self.db, "intake", "--pipeline-data", self.data_path])
        self.assertEqual(rc, 0)
        self.assertIn("收纳 2", out)

        rc, out, _ = run_cli(["--db", self.db, "list"])
        self.assertEqual(rc, 0)
        self.assertIn("vpn123456.opengw.net", out)

        rc, out, _ = run_cli(["--db", self.db, "list", "--scene", "support"])
        self.assertIn("vpn123456", out)
        self.assertNotIn("public-vpn-78", out)

        node_id = "pipeline:sstp:vpn123456.opengw.net:443"
        rc, _o, _e = run_cli(["--db", self.db, "pin", node_id])
        self.assertEqual(rc, 0)
        rc, out, _ = run_cli(["--db", self.db, "verify", node_id],
                             probe_factory=fake_probe_factory)
        self.assertEqual(rc, 0)
        self.assertIn("5.6.7.8", out)

    def test_empty_db_friendly(self):
        rc, out, _ = run_cli(["--db", self.db, "list"])
        self.assertEqual(rc, 0)
        self.assertIn("空", out)
        rc, _o, err = run_cli(["--db", self.db, "verify", "ghost"])
        self.assertEqual(rc, 2)
        self.assertIn("不存在", err)
        rc, _o, err = run_cli(["--db", self.db, "intake"])
        self.assertEqual(rc, 2)

    def test_connect_with_fake_engine(self):
        run_cli(["--db", self.db, "intake", "--feed",
                 self._write_feed()])
        rc, out, err = run_cli(
            ["--db", self.db, "connect", "vpngate_feed:openvpn:vpn123456.opengw.net:443"],
            probe_factory=fake_probe_factory,
            engine_factory=lambda: {"openvpn": FakeEngine()})
        self.assertEqual(rc, 0, err)
        self.assertIn("connected", out)

    def test_connect_unknown_node_friendly_error(self):
        rc, _o, err = run_cli(["--db", self.db, "connect", "ghost"],
                              probe_factory=fake_probe_factory,
                              engine_factory=lambda: {"openvpn": FakeEngine()})
        self.assertEqual(rc, 2)
        self.assertIn("不存在", err)

    def _write_feed(self):
        from client.tests.test_sources import FEED_CSV
        path = os.path.join(self.tmp.name, "feed.csv")
        with open(path, "w", encoding="utf-8") as f:
            f.write(FEED_CSV)
        return path


if __name__ == "__main__":
    unittest.main()


class TestImportSubscriptionUnifiedEntry(unittest.TestCase):
    """订阅导入统一入口（阶段二 task2）：CLI --file 走 format_import。

    混合输入（注释头 + 整段 b64 + 一条明文链接）应完整收纳，不丢整块。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "client.db")

    def tearDown(self):
        self.tmp.cleanup()

    def test_mixed_file_imports_both_branches(self):
        import base64
        plain = ("vless://11111111-2222-3333-4444-555555555555"
                 "@a.example.com:443?security=tls#mixed-plain")
        blob = base64.b64encode(
            ("trojan://fake-pw@c.example.com:443#mixed-b64\n"
             ).encode("utf-8")).decode("ascii")
        path = os.path.join(self.tmp.name, "mixed.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write("# 注释头\n" + blob + "\n" + plain + "\n")
        rc, out, _ = run_cli(["--db", self.db, "import-subscription",
                              "--file", path])
        self.assertEqual(rc, 0)
        self.assertIn("已收纳 2 个节点", out)
        self.assertIn("格式 mixed", out)
        rc, out, _ = run_cli(["--db", self.db, "list"])
        self.assertIn("mixed-plain", out)
        self.assertIn("mixed-b64", out)

