#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ui/cli sing-box 连接验出口走代理 离线测试
============================================
核心断言「探针选对了路」：sing-box 引擎的节点，验出口必须以**引擎实例自己的
proxy_url** 造探针（monkeypatch client.ui.cli.make_real_probe 记录 proxy 参数）；
其它引擎维持 probe_factory 的那一个探针。不触网、不拉真进程——
sing-box 引擎用子类假实现（isinstance 仍成立），探针全部假造。
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.verify import ProbeResult
from client.engines.singbox_engine import SingBoxEngine
from client.sources import manual as manual_src
from client.tests.test_cli import FakeEngine, run_cli
from client.ui import cli

VLESS_LINK = ("vless://11111111-2222-3333-4444-555555555555"
              "@example.com:443?security=tls#vless-proxy-verify")

DEFAULT_PROXY = "http://127.0.0.1:7897"      # SingBoxEngine 默认 http_port（探针走 HTTP 入站）
CUSTOM_PROXY = "http://127.0.0.1:18095"       # 构造参数改过端口的；18089 已被本机 ZCode 中继占用


class FakeSingBoxEngine(SingBoxEngine):
    """isinstance(SingBoxEngine) 成立，但 start/stop/status 全假——绝不真拉进程。"""

    def start(self, node, on_event=None):
        return {"node_id": node.id}

    def stop(self, handle):
        pass

    def status(self, handle):
        return "running"


class ProbeRecorder:
    """两条探针路分开记账：probe_factory 探针被调几次 / make_real_probe 的 proxy 参数。"""

    def __init__(self):
        self.factory_probe_calls = 0
        self.proxy_probe_calls = 0
        self.make_real_args = []            # 每次 make_real_probe(...) 收到的 proxy

    def factory(self):
        def probe(node):
            self.factory_probe_calls += 1
            return self._ok("5.6.7.8")      # 与代理探针的出口 IP 故意不同
        return probe

    def fake_make_real_probe(self, *args, **kwargs):
        proxy = kwargs.get("proxy") if kwargs else None
        if proxy is None and args:
            proxy = args[0]
        self.make_real_args.append(proxy)

        def probe(node):
            self.proxy_probe_calls += 1
            return self._ok("9.9.9.9")
        return probe

    @staticmethod
    def _ok(ip):
        return ProbeResult(alive=True, exit_ip=ip, country="Japan", country_code="JP",
                           org="NTT EAST", is_clean=True, latency_ms=300.0,
                           jitter_ms=50.0, muse_code=307)


class TestVerifyThroughProxy(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "client.db")
        self.rec = ProbeRecorder()

    def tearDown(self):
        self.tmp.cleanup()

    # 1. vless + 真 SingBoxEngine 实例（子类假实现）→ 验出口经引擎 http_proxy_url
    def test_vless_via_singbox_engine_verifies_through_proxy(self):
        rc, out, err = self._connect_vless(FakeSingBoxEngine())
        self.assertEqual(rc, 0, err)
        self.assertIn("connected", out)
        self.assertEqual(self.rec.make_real_args, [DEFAULT_PROXY])
        self.assertEqual(self.rec.proxy_probe_calls, 1)     # 代理探针恰好用一次
        self.assertEqual(self.rec.factory_probe_calls, 0)   # probe_factory 探针没被用
        self.assertIn("9.9.9.9", out)                       # 出口画像来自代理探针

    # 2. openvpn + 假 OpenVPN 引擎 → 维持 probe_factory 探针，连接照常成功
    def test_openvpn_keeps_default_probe(self):
        from client.tests.test_sources import FEED_CSV
        feed_path = os.path.join(self.tmp.name, "feed.csv")
        with open(feed_path, "w", encoding="utf-8") as f:
            f.write(FEED_CSV)
        rc, _out, err = run_cli(["--db", self.db, "intake", "--feed", feed_path])
        self.assertEqual(rc, 0, err)
        with mock.patch.object(cli, "make_real_probe", self.rec.fake_make_real_probe):
            rc, out, err = run_cli(
                ["--db", self.db, "connect",
                 "vpngate:openvpn:vpn123456.opengw.net:443"],
                probe_factory=self.rec.factory,
                engine_factory=lambda: {"openvpn": FakeEngine()})
        self.assertEqual(rc, 0, err)
        self.assertIn("connected", out)
        self.assertEqual(self.rec.make_real_args, [])       # 没有经 sing-box 代理造探针
        self.assertEqual(self.rec.factory_probe_calls, 1)   # 走的是 probe_factory 探针
        self.assertIn("5.6.7.8", out)

    # 3. 自定义端口 → proxy 用引擎实例自己的 http_proxy_url，不许写死 7897
    def test_custom_http_port_is_used_not_hardcoded(self):
        rc, out, err = self._connect_vless(FakeSingBoxEngine(http_port=18095))
        self.assertEqual(rc, 0, err)
        self.assertIn("connected", out)
        self.assertEqual(self.rec.make_real_args, [CUSTOM_PROXY])
        self.assertEqual(self.rec.factory_probe_calls, 0)

    # ---------------- helpers ----------------
    def _connect_vless(self, engine):
        node = manual_src.parse_link(VLESS_LINK)
        rc, _out, err = run_cli(["--db", self.db, "intake", "--link", VLESS_LINK])
        self.assertEqual(rc, 0, err)
        with mock.patch.object(cli, "make_real_probe", self.rec.fake_make_real_probe):
            rc, out, err = run_cli(["--db", self.db, "connect", node.id],
                                   probe_factory=self.rec.factory,
                                   engine_factory=lambda: {"vless": engine})
        return rc, out, err


if __name__ == "__main__":
    unittest.main()
