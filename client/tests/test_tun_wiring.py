#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TUN 接线测试（task6 集成，v0.10.0）
=====================================
引擎 build_config 的 tun 合并、takeover_mode 校验、start() 提权前提
检查（显式拒绝）、CLI --mode 与 status 行。全部离线；TUN 激活本身
（pkexec + root wrapper）未实现，start() 必须显式报错、绝不静默半接管。
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.model import Node
from client.core.store import Store
from client.engines.base import EngineError
from client.engines.singbox_engine import SingBoxEngine
from client.tests.test_singbox_engine import ss_node
from client.ui import cli


class TestEngineTunWiring(unittest.TestCase):
    def test_proxy_mode_config_has_no_tun(self):
        eng = SingBoxEngine()
        self.assertEqual(eng.takeover_mode, "proxy")
        cfg = eng.build_config(ss_node())
        tags = [i.get("tag") for i in cfg["inbounds"]]
        self.assertEqual(tags, ["socks-in", "http-in"])
        self.assertNotIn("auto_detect_interface", cfg["route"])
        self.assertNotIn("rules", cfg["dns"])

    def test_tun_mode_config_merges_against_real_engine(self):
        eng = SingBoxEngine(takeover_mode="tun")
        cfg = eng.build_config(ss_node())
        tags = [i.get("tag") for i in cfg["inbounds"]]
        self.assertEqual(tags, ["socks-in", "http-in", "tun-in"])
        tun = cfg["inbounds"][2]
        self.assertEqual(tun["type"], "tun")
        self.assertEqual(tun["interface_name"], "gate-tun")
        self.assertEqual(tun["address"], ["172.19.0.1/30"])
        # 规则顺序：sniff → hijack-dns 在既有 resolve 规则之前
        rules = cfg["route"]["rules"]
        self.assertEqual(rules[0], {"action": "sniff"})
        self.assertEqual(rules[1], {"protocol": "dns", "action": "hijack-dns"})
        self.assertEqual(rules[2]["action"], "resolve")
        # DNS 劫持指向经节点绕行的 remote-dns（自动识别，非硬编码猜测）
        self.assertEqual(cfg["dns"]["rules"],
                         [{"inbound": ["tun-in"], "server": "remote-dns",
                           "strategy": "ipv4_only"}])
        self.assertTrue(cfg["route"]["auto_detect_interface"])
        # 单节点单出站语义原样
        self.assertEqual(cfg["route"]["final"], "node-out")
        self.assertEqual(len(cfg["outbounds"]), 1)

    def test_tun_mode_config_does_not_mutate_prototype(self):
        eng = SingBoxEngine(takeover_mode="tun")
        cfg1 = eng.build_config(ss_node())
        cfg2 = eng.build_config(ss_node())
        self.assertEqual(cfg1, cfg2)
        self.assertEqual(len(cfg1["inbounds"]), 3)

    def test_invalid_takeover_mode_rejected_at_construction(self):
        with self.assertRaises(EngineError):
            SingBoxEngine(takeover_mode="bogus")

    def test_start_tun_fails_closed_without_privilege_launcher(self):
        eng = SingBoxEngine(takeover_mode="tun")
        with self.assertRaises(EngineError) as ctx:
            eng.start(ss_node())
        self.assertIn("TUN", str(ctx.exception))
        self.assertIn("尚未就绪", str(ctx.exception))


class FakeTunEngine(SingBoxEngine):
    """CLI 测试替身：真是 SingBoxEngine 子类（过 isinstance 关），但 start
    不碰二进制——只记录当时的 takeover_mode 并按模式返回/报错。"""

    def __init__(self):
        super().__init__(socks_port=18096, http_port=18097)
        self.started_modes = []

    @property
    def http_proxy_url(self):
        return "http://127.0.0.1:18097"

    def start(self, node, on_event=None):
        self.started_modes.append(self.takeover_mode)
        if self.takeover_mode == "tun":
            raise EngineError("TUN 模式尚未就绪（测试替身）")
        return {"node_id": node.id}

    def stop(self, handle):
        return None

    def status(self, handle):
        return "running"


def run_cli(argv, **kw):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv, **kw)
    return rc, out.getvalue(), err.getvalue()


class TestCliTunMode(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "client.db")
        self.store = Store(self.db)
        self.store.upsert_node(Node(
            id="manual:ss:1.2.3.4:8388", name="ss-测试", protocol="ss",
            source="manual", host="1.2.3.4", port=8388,
            extra={"method": "aes-256-gcm", "password": "x"}))
        self.store.close()
        self.fake = FakeTunEngine()

    def tearDown(self):
        self.tmp.cleanup()

    def test_parser_accepts_mode(self):
        args = cli.build_parser().parse_args(
            ["connect", "manual:ss:1.2.3.4:8388", "--mode", "tun"])
        self.assertEqual(args.mode, "tun")
        args = cli.build_parser().parse_args(
            ["connect", "manual:ss:1.2.3.4:8388"])
        self.assertEqual(args.mode, "proxy")

    def test_connect_tun_sets_mode_and_fails_explicitly(self):
        rc, out, _err = run_cli(
            ["--db", self.db, "connect", "manual:ss:1.2.3.4:8388",
             "--mode", "tun"],
            engine_factory=lambda: {"ss": self.fake})
        self.assertEqual(self.fake.started_modes, ["tun"])
        self.assertEqual(rc, 1)
        self.assertIn("TUN", out)
        self.assertIn("尚未就绪", out)

    def test_connect_proxy_mode_passed_to_engine(self):
        # proxy 模式下 start 成功；验出口走假 probe（CLI 的 sing-box 分流
        # 用 make_real_probe，测试里打桩成假 probe）。
        from client.core.verify import ProbeResult
        from client.tests.test_cli import fake_probe_factory
        real_make_probe = cli.make_real_probe
        cli.make_real_probe = lambda proxy=None: fake_probe_factory()
        try:
            rc, out, err = run_cli(
                ["--db", self.db, "connect", "manual:ss:1.2.3.4:8388"],
                probe_factory=fake_probe_factory,
                engine_factory=lambda: {"ss": self.fake})
        finally:
            cli.make_real_probe = real_make_probe
        self.assertEqual(self.fake.started_modes, ["proxy"], err)
        self.assertEqual(rc, 0, err)
        self.assertIn("connected", out)

    def test_status_mentions_takeover_mode(self):
        rc, out, _err = run_cli(["--db", self.db, "status"])
        self.assertEqual(rc, 0)
        self.assertIn("接管方式", out)
        self.assertIn("TUN", out)


if __name__ == "__main__":
    unittest.main()
