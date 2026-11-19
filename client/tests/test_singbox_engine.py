#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
engines/singbox_engine 离线测试
=================================
不依赖真 sing-box 二进制、不联网：
- build_config 直接断言生成结构（含 P0 红线：单出站、无 urltest/selector）
- 生命周期用临时目录里的假二进制（Python 脚本模拟 sing-box：解析 -c 配置、
  绑住 socks 端口、sleep），覆盖 start/stop/status 与临时文件清理
"""
import base64
import glob
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.model import Node
from client.engines.base import EngineError
from client.engines.singbox_engine import (
    DEFAULT_HTTP_PORT,
    DEFAULT_SOCKS_PORT,
    SINGBOX_PROTOCOLS,
    SingBoxEngine,
)

VLESS_UUID = "11111111-2222-3333-4444-555555555555"
SOCKS_PORT = 18089          # 冷门端口，避免和真服务/真 sing-box 默认端口冲突
HTTP_PORT = 18090

VMESS_FIELDS = {"v": "2", "ps": "vm-测试", "add": "vm.example.com", "port": "8443",
                "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", "aid": "0",
                "net": "ws", "tls": "tls", "host": "vmh.example.com",
                "path": "/vmpath", "sni": "vmsni.example.com", "scy": "auto"}

FAKE_GOOD = r"""#!/usr/bin/env python3
import json, socket, sys, time
cfg_path = sys.argv[sys.argv.index("-c") + 1]
with open(cfg_path, "r", encoding="utf-8") as f:
    cfg = json.load(f)
socks = [i for i in cfg["inbounds"] if i.get("type") == "socks"][0]
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((socks["listen"], socks["listen_port"]))
s.listen(16)
sys.stdout.write("fake sing-box ready\n")
sys.stdout.flush()
while True:
    time.sleep(60)
"""

FAKE_QUIT = r"""#!/usr/bin/env python3
import sys
sys.stderr.write("FAKE-ERR 配置解析失败\n")
sys.exit(1)
"""


def vless_node(query=None, uuid=VLESS_UUID):
    extra = {"query": dict(query or {})}
    if uuid:
        extra["uuid"] = uuid
    return Node(id="manual:vless:example.com:443", name="vless-测试", protocol="vless",
                source="manual", host="example.com", port=443,
                config_ref="vless://fake-link", extra=extra)


def ss_node(method="aes-256-gcm", password="ss-pass-123"):
    extra = {"query": {}}
    if method:
        extra["method"] = method
    if password:
        extra["password"] = password
    return Node(id="manual:ss:1.2.3.4:8388", name="ss-测试", protocol="ss",
                source="manual", host="1.2.3.4", port=8388,
                config_ref="ss://fake-link", extra=extra)


def trojan_node(query=None, password="trojan-pass-456"):
    extra = {"query": dict(query or {})}
    if password:
        extra["password"] = password
    return Node(id="manual:trojan:5.6.7.8:443", name="trojan-测试", protocol="trojan",
                source="manual", host="5.6.7.8", port=443,
                config_ref="trojan://fake-link", extra=extra)


def vmess_ref(fields) -> str:
    raw = json.dumps(fields, ensure_ascii=False).encode("utf-8")
    return "vmess://" + base64.b64encode(raw).decode("ascii")


def vmess_node(fields=None, ref=None):
    if ref is None:
        ref = vmess_ref(fields if fields is not None else VMESS_FIELDS)
    return Node(id="manual:vmess:9.9.9.9:443", name="vmess-测试", protocol="vmess",
                source="manual", host="9.9.9.9", port=443,
                config_ref=ref, extra={"query": {}})


def write_fake_binary(directory, name, content) -> str:
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    os.chmod(path, 0o755)
    return path


def singbox_temp_dirs() -> set:
    """引擎会话临时目录（gate-singbox-*）在系统临时区的当前集合。"""
    return set(glob.glob(os.path.join(tempfile.gettempdir(), "gate-singbox-*")))


class TestBasics(unittest.TestCase):
    def test_module_constants_and_proxy_url(self):
        self.assertEqual(DEFAULT_SOCKS_PORT, 10808)
        self.assertEqual(DEFAULT_HTTP_PORT, 10809)
        self.assertEqual(SINGBOX_PROTOCOLS, ("vless", "ss", "trojan", "vmess"))
        eng = SingBoxEngine()
        self.assertEqual(eng.name, "sing-box")
        self.assertEqual(eng.proxy_url, "socks5://127.0.0.1:10808")
        eng = SingBoxEngine(socks_port=18089)
        self.assertEqual(eng.proxy_url, "socks5://127.0.0.1:18089")

    def test_status_stop_of_none_handle(self):
        eng = SingBoxEngine()
        self.assertEqual(eng.status(None), "stopped")
        eng.stop(None)      # 不抛


class TestBuildConfig(unittest.TestCase):
    def setUp(self):
        self.eng = SingBoxEngine(socks_port=SOCKS_PORT, http_port=HTTP_PORT)

    def outbound(self, node) -> dict:
        cfg = self.eng.build_config(node)
        self.assertEqual(len(cfg["outbounds"]), 1)
        return cfg["outbounds"][0]

    def test_vless_basic_tls(self):
        out = self.outbound(vless_node(
            {"security": "tls", "sni": "cdn.example.com", "fp": "safari"}))
        self.assertEqual(out["type"], "vless")
        self.assertEqual(out["server"], "example.com")
        self.assertEqual(out["server_port"], 443)
        self.assertEqual(out["uuid"], VLESS_UUID)
        self.assertNotIn("flow", out)
        self.assertNotIn("transport", out)
        self.assertEqual(out["tls"]["enabled"], True)
        self.assertEqual(out["tls"]["server_name"], "cdn.example.com")
        self.assertEqual(out["tls"]["utls"]["enabled"], True)
        self.assertEqual(out["tls"]["utls"]["fingerprint"], "safari")

    def test_vless_reality(self):
        out = self.outbound(vless_node({
            "security": "reality", "pbk": "PUBKEY-xyz", "sid": "ab12",
            "sni": "www.apple.com", "fp": "chrome",
            "flow": "xtls-rprx-vision"}))
        self.assertEqual(out["flow"], "xtls-rprx-vision")
        tls = out["tls"]
        self.assertEqual(tls["server_name"], "www.apple.com")
        self.assertEqual(tls["reality"]["enabled"], True)
        self.assertEqual(tls["reality"]["public_key"], "PUBKEY-xyz")
        self.assertEqual(tls["reality"]["short_id"], "ab12")
        self.assertEqual(tls["utls"]["enabled"], True)      # reality 必开 utls

    def test_vless_ws_transport(self):
        out = self.outbound(vless_node({
            "type": "ws", "path": "/ws-path", "host": "ws.example.com",
            "security": "tls"}))
        self.assertEqual(out["transport"],
                         {"type": "ws", "path": "/ws-path",
                          "headers": {"Host": "ws.example.com"}})
        # sni 缺省 → server_name 回退传输 host 参数
        self.assertEqual(out["tls"]["server_name"], "ws.example.com")

    def test_vless_ws_without_host_no_headers(self):
        out = self.outbound(vless_node({"type": "ws"}))
        self.assertEqual(out["transport"], {"type": "ws", "path": "/"})
        self.assertNotIn("headers", out["transport"])
        self.assertNotIn("tls", out)            # security 缺省 → 不写 tls

    def test_vless_grpc_transport(self):
        out = self.outbound(vless_node({"type": "grpc", "serviceName": "svc-name"}))
        self.assertEqual(out["transport"],
                         {"type": "grpc", "service_name": "svc-name"})

    def test_vless_tcp_no_transport(self):
        out = self.outbound(vless_node({"type": "tcp"}))
        self.assertNotIn("transport", out)

    def test_ss(self):
        out = self.outbound(ss_node())
        self.assertEqual(out["type"], "shadowsocks")
        self.assertEqual(out["server"], "1.2.3.4")
        self.assertEqual(out["server_port"], 8388)
        self.assertEqual(out["method"], "aes-256-gcm")
        self.assertEqual(out["password"], "ss-pass-123")
        self.assertNotIn("tls", out)
        self.assertNotIn("transport", out)

    def test_trojan_tls_sni(self):
        out = self.outbound(trojan_node({"sni": "t.example.com"}))
        self.assertEqual(out["type"], "trojan")
        self.assertEqual(out["server"], "5.6.7.8")
        self.assertEqual(out["server_port"], 443)
        self.assertEqual(out["password"], "trojan-pass-456")
        self.assertEqual(out["tls"],
                         {"enabled": True, "server_name": "t.example.com"})

    def test_trojan_sni_fallback_to_server(self):
        out = self.outbound(trojan_node({}))
        self.assertEqual(out["tls"]["server_name"], "5.6.7.8")

    def test_vmess_from_json(self):
        out = self.outbound(vmess_node())
        self.assertEqual(out["type"], "vmess")
        self.assertEqual(out["server"], "vm.example.com")           # 以 JSON 为准
        self.assertEqual(out["server_port"], 8443)                  # 字符串端口 → int
        self.assertEqual(out["uuid"], "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        self.assertEqual(out["security"], "auto")
        self.assertEqual(out["alter_id"], 0)
        self.assertEqual(out["transport"]["type"], "ws")
        self.assertEqual(out["transport"]["path"], "/vmpath")
        self.assertEqual(out["transport"]["headers"], {"Host": "vmh.example.com"})
        self.assertEqual(out["tls"]["enabled"], True)
        self.assertEqual(out["tls"]["server_name"], "vmsni.example.com")

    def test_vmess_defaults(self):
        fields = dict(VMESS_FIELDS, net="tcp", tls="", scy="chacha20-poly1305",
                      aid="64")
        out = self.outbound(vmess_node(fields))
        self.assertEqual(out["alter_id"], 64)
        self.assertEqual(out["security"], "chacha20-poly1305")
        self.assertNotIn("transport", out)              # net=tcp → 不写 transport
        self.assertNotIn("tls", out)                    # tls 空 → 不开

    def test_vmess_tls_server_name_fallback(self):
        fields = dict(VMESS_FIELDS, tls="tls", sni="", host="")
        out = self.outbound(vmess_node(fields))
        self.assertEqual(out["tls"]["server_name"], "vm.example.com")


class TestRedLines(unittest.TestCase):
    """P0：单节点单出站，无 urltest/selector；入站只绑本机。"""

    def setUp(self):
        self.eng = SingBoxEngine(socks_port=SOCKS_PORT, http_port=HTTP_PORT)

    def _nodes(self):
        return [vless_node({"security": "tls"}), ss_node(),
                trojan_node({}), vmess_node()]

    def test_no_urltest_selector_single_outbound(self):
        for node in self._nodes():
            with self.subTest(protocol=node.protocol):
                text = json.dumps(self.eng.build_config(node), ensure_ascii=False)
                self.assertNotIn("urltest", text)
                self.assertNotIn("selector", text)
                cfg = json.loads(text)
                self.assertEqual(len(cfg["outbounds"]), 1)
                self.assertEqual(cfg["outbounds"][0]["tag"], "node-out")
                self.assertEqual(cfg["route"], {"final": "node-out"})

    def test_top_level_keys_exact(self):
        for node in self._nodes():
            with self.subTest(protocol=node.protocol):
                cfg = self.eng.build_config(node)
                self.assertEqual(set(cfg.keys()),
                                 {"log", "inbounds", "outbounds", "route"})
                self.assertEqual(cfg["log"], {"level": "warn", "timestamp": True})

    def test_inbounds_localhost_only_and_ports_match_ctor(self):
        cfg = self.eng.build_config(vless_node({}))
        self.assertEqual(len(cfg["inbounds"]), 2)
        socks_in, http_in = cfg["inbounds"]
        self.assertEqual(socks_in, {"type": "socks", "tag": "socks-in",
                                    "listen": "127.0.0.1",
                                    "listen_port": SOCKS_PORT})
        self.assertEqual(http_in, {"type": "http", "tag": "http-in",
                                   "listen": "127.0.0.1",
                                   "listen_port": HTTP_PORT})


class TestConfigErrors(unittest.TestCase):
    def test_vless_missing_uuid(self):
        eng = SingBoxEngine()
        with self.assertRaises(EngineError) as ctx:
            eng.build_config(vless_node({}, uuid=""))
        self.assertIn("uuid", str(ctx.exception))

    def test_vless_reality_missing_pbk(self):
        eng = SingBoxEngine()
        with self.assertRaises(EngineError) as ctx:
            eng.build_config(vless_node({"security": "reality", "sid": "ab"}))
        self.assertIn("pbk", str(ctx.exception))

    def test_ss_missing_method_or_password(self):
        eng = SingBoxEngine()
        with self.assertRaises(EngineError) as ctx:
            eng.build_config(ss_node(method=""))
        self.assertIn("method", str(ctx.exception))
        with self.assertRaises(EngineError) as ctx:
            eng.build_config(ss_node(password=""))
        self.assertIn("password", str(ctx.exception))

    def test_unsupported_protocol(self):
        eng = SingBoxEngine()
        node = Node(id="manual:sstp:7.7.7.7:443", name="sstp-测试", protocol="sstp",
                    source="manual", host="7.7.7.7", port=443,
                    config_ref="sstp://fake-link", extra={"query": {}})
        with self.assertRaises(EngineError) as ctx:
            eng.build_config(node)
        self.assertIn("sstp", str(ctx.exception))

    def test_vmess_bad_payload(self):
        eng = SingBoxEngine()
        cases = [
            ("坏 base64", "vmess://!!!!"),
            ("base64 但不是 JSON", "vmess://"
                + base64.b64encode(b"not-valid-json").decode("ascii")),
            ("缺 id", vmess_ref({"add": "vm.example.com", "port": 443})),
            ("缺 add", vmess_ref({"id": "some-id", "port": 443})),
            ("缺前缀", "vmess-plain-text"),
        ]
        for label, ref in cases:
            with self.subTest(case=label):
                with self.assertRaises(EngineError) as ctx:
                    eng.build_config(vmess_node(ref=ref))
                self.assertIn("vmess", str(ctx.exception))


class TestMissingBinary(unittest.TestCase):
    def test_nonexistent_binary_raises_install_guide(self):
        before = singbox_temp_dirs()
        eng = SingBoxEngine(binary="/nonexistent/dir/fake-sing-box")
        self.assertEqual(eng.find_binary(), "")     # 明确给的路径不存在 → 不回落
        with self.assertRaises(EngineError) as ctx:
            eng.start(vless_node({}))
        msg = str(ctx.exception)
        self.assertIn("release", msg)               # 安装指引关键词
        self.assertIn("v1.14.4", msg)
        self.assertIn("~/.local/bin", msg)
        self.assertIn("GATE_CLIENT_SINGBOX_BIN", msg)
        self.assertEqual(singbox_temp_dirs(), before)   # 连临时目录都没建


class TestLifecycle(unittest.TestCase):
    def test_start_ready_stop_cleanup(self):
        with tempfile.TemporaryDirectory() as d:
            fake = write_fake_binary(d, "fake-sing-box", FAKE_GOOD)
            eng = SingBoxEngine(binary=fake, socks_port=SOCKS_PORT,
                                http_port=HTTP_PORT, ready_timeout=10.0)
            holder = {}
            self.addCleanup(lambda: eng.stop(holder.get("h")))
            handle = eng.start(vless_node({"security": "tls"}))
            holder["h"] = handle
            self.assertEqual(eng.status(handle), "running")
            self.assertEqual(eng.proxy_url, f"socks5://127.0.0.1:{SOCKS_PORT}")
            cdir = handle.config_dir
            self.assertTrue(os.path.basename(cdir).startswith("gate-singbox-"))
            self.assertTrue(os.path.isfile(os.path.join(cdir, "config.json")))
            self.assertTrue(os.path.isfile(os.path.join(cdir, "sing-box.log")))
            with open(os.path.join(cdir, "config.json"), "r",
                      encoding="utf-8") as f:
                on_disk = json.load(f)
            self.assertEqual(on_disk,
                             eng.build_config(vless_node({"security": "tls"})))
            eng.stop(handle)
            holder["h"] = None
            self.assertEqual(eng.status(handle), "stopped")
            self.assertFalse(os.path.exists(cdir))   # 含 uuid/密码的临时配置已删
            eng.stop(handle)                          # 重复 stop 无害
            self.assertEqual(eng.status(handle), "stopped")

    def test_early_exit_reports_log_tail_and_cleans_up(self):
        with tempfile.TemporaryDirectory() as d:
            fake = write_fake_binary(d, "fake-sing-box-quit", FAKE_QUIT)
            eng = SingBoxEngine(binary=fake, socks_port=18091, http_port=18092,
                                ready_timeout=10.0)
            before = singbox_temp_dirs()
            with self.assertRaises(EngineError) as ctx:
                eng.start(vless_node({}))
            msg = str(ctx.exception)
            self.assertIn("sing-box 启动失败", msg)
            self.assertIn("FAKE-ERR", msg)           # 假二进制的 stderr 进了日志尾巴
            self.assertEqual(singbox_temp_dirs(), before)   # 临时目录已清理


if __name__ == "__main__":
    unittest.main()
