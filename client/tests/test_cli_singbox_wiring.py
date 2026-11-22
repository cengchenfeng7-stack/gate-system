#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/cli sing-box 引擎接线离线测试
===================================
只验证接线层：默认注册双引擎、engine_factory 注入优先、协议键路由到引擎。
不触网、不拨号、不依赖真 sing-box 二进制（SingBoxEngine 构造器不碰文件系统）。
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.engines.openvpn_engine import OpenVPNEngine
from client.engines.singbox_engine import SINGBOX_PROTOCOLS, SingBoxEngine
from client.sources import manual as manual_src
from client.tests.test_cli import FakeEngine, fake_probe_factory, run_cli
from client.ui import cli


class RecordingEngine(FakeEngine):
    """在现成 FakeEngine 上加启动计数，证明 connect 真的路由到了注入引擎。"""

    def __init__(self):
        super().__init__()
        self.started = 0

    def start(self, node, on_event=None):
        self.started += 1
        return super().start(node, on_event=on_event)


class TestCliSingboxWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "client.db")

    def tearDown(self):
        self.tmp.cleanup()

    # 1. 默认接线：键集合恰为八个协议；七个 sing-box 协议共用同一实例
    def test_default_wiring_registers_both_engines(self):
        from client.core.store import Store
        store = Store(self.db)
        try:
            orch = cli._build_orchestrator(store, fake_probe_factory)
        finally:
            store.close()
        self.assertEqual(set(orch.engines.keys()),
                         {"openvpn", "vless", "ss", "trojan", "vmess",
                          "hysteria2", "tuic", "wireguard"})
        self.assertIsInstance(orch.engines["openvpn"], OpenVPNEngine)
        singbox = orch.engines["vless"]
        self.assertIsInstance(singbox, SingBoxEngine)
        for proto in SINGBOX_PROTOCOLS:
            self.assertIs(orch.engines[proto], singbox)   # 一个实例供七协议共用

    # 2. 注入优先：engine_factory 给什么就用什么，默认接线不介入
    def test_engine_factory_injection_wins(self):
        from client.core.store import Store
        store = Store(self.db)
        try:
            fake = {"vless": FakeEngine(), "openvpn": FakeEngine()}
            orch = cli._build_orchestrator(store, fake_probe_factory,
                                           engine_factory=lambda: fake)
        finally:
            store.close()
        self.assertEqual(set(orch.engines.keys()), {"vless", "openvpn"})
        self.assertIs(orch.engines["vless"], fake["vless"])
        self.assertIs(orch.engines["openvpn"], fake["openvpn"])

    # 3. 协议路由冒烟：vless 节点走 run_cli connect，路由到注入的假引擎
    def test_connect_routes_vless_to_registered_engine(self):
        link = ("vless://11111111-2222-3333-4444-555555555555"
                "@example.com:443?security=tls#vless-wiring")
        node = manual_src.parse_link(link)
        rc, out, err = run_cli(["--db", self.db, "intake", "--link", link])
        self.assertEqual(rc, 0, err)
        engine = RecordingEngine()
        rc, out, err = run_cli(["--db", self.db, "connect", node.id],
                               probe_factory=fake_probe_factory,
                               engine_factory=lambda: {"vless": engine})
        self.assertEqual(rc, 0, err)
        self.assertIn("connected", out)
        self.assertEqual(engine.started, 1)   # 协议键 vless 对上了注入引擎


if __name__ == "__main__":
    unittest.main()
