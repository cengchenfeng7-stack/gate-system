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
import shutil
import subprocess
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

FAKE_XHTTP_NO = r"""#!/usr/bin/env python3
import sys
if len(sys.argv) > 1 and sys.argv[1] == "check":
    sys.stderr.write("FATAL decode config: outbounds[0].transport: "
                     "unknown transport type: xhttp\n")
    sys.exit(1)
sys.exit(0)
"""

FAKE_XHTTP_OK = r"""#!/usr/bin/env python3
import sys
if len(sys.argv) > 1 and sys.argv[1] == "check":
    sys.exit(0)
""" + FAKE_GOOD.split("\n", 1)[1]


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
        self.assertEqual(DEFAULT_SOCKS_PORT, 7898)
        self.assertEqual(DEFAULT_HTTP_PORT, 7897)
        self.assertEqual(SINGBOX_PROTOCOLS, ("vless", "ss", "trojan", "vmess"))
        eng = SingBoxEngine()
        self.assertEqual(eng.name, "sing-box")
        self.assertEqual(eng.proxy_url, "socks5://127.0.0.1:7898")
        self.assertEqual(eng.http_proxy_url, "http://127.0.0.1:7897")
        eng = SingBoxEngine(socks_port=18089, http_port=18090)
        self.assertEqual(eng.proxy_url, "socks5://127.0.0.1:18089")
        self.assertEqual(eng.http_proxy_url, "http://127.0.0.1:18090")

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

    def test_dns_remote_resolution_detours_through_node(self):
        # 真机验收教训（2026-11-20）：目标域名必须在出口侧解析——本机
        # 解析器被 fake-ip 劫持时，假 IP 送到出口服务器连接必死；
        # 节点服务器自身域名走本地解析（final=local-dns）防自举死锁。
        cfg = self.eng.build_config(vless_node({"security": "tls"}))
        servers = {s["tag"]: s for s in cfg["dns"]["servers"]}
        self.assertEqual(servers["remote-dns"]["type"], "udp")
        self.assertEqual(servers["remote-dns"]["detour"], "node-out")
        self.assertEqual(servers["local-dns"]["type"], "local")
        self.assertEqual(cfg["dns"]["final"], "local-dns")
        self.assertEqual(cfg["route"]["rules"],
                         [{"action": "resolve", "server": "remote-dns",
                           "strategy": "ipv4_only"}])
        self.assertEqual(cfg["route"]["final"], "node-out")
        self.assertEqual(cfg["route"]["default_domain_resolver"], "local-dns")

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

    def test_vless_xhttp_transport(self):
        # 2026-11-20 真机缺口：二猫池里有 xhttp 节点（如瑞典 SWE 2），
        # 引擎原先直接拒。host 是顶层字段、不是 Host 头。
        out = self.outbound(vless_node({
            "type": "xhttp", "path": "/xh", "host": "xh.example.com",
            "mode": "packet-up", "security": "tls"}))
        self.assertEqual(out["transport"],
                         {"type": "xhttp", "path": "/xh",
                          "host": "xh.example.com", "mode": "packet-up"})
        # sni 缺省 → server_name 回退传输 host 参数（与 ws 同口径）
        self.assertEqual(out["tls"]["server_name"], "xh.example.com")

    def test_vless_xhttp_defaults_and_bad_mode(self):
        out = self.outbound(vless_node({"type": "xhttp", "mode": "bogus"}))
        self.assertEqual(out["transport"], {"type": "xhttp", "path": "/"})
        out2 = self.outbound(vless_node({"type": "xhttp", "mode": "auto"}))
        self.assertEqual(out2["transport"]["mode"], "auto")

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
                # route.final 必须仍是 node-out；允许仅含 resolve 动作的
                # 规则（出口侧解析目标域名），不得引入任何切换类动作
                self.assertEqual(cfg["route"]["final"], "node-out")
                for rule in cfg["route"].get("rules", []):
                    self.assertEqual(rule.get("action"), "resolve")

    def test_top_level_keys_exact(self):
        for node in self._nodes():
            with self.subTest(protocol=node.protocol):
                cfg = self.eng.build_config(node)
                self.assertEqual(set(cfg.keys()),
                                 {"log", "dns", "inbounds", "outbounds", "route"})
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

    def test_xhttp_rejected_by_kernel_clear_error(self):
        """官方内核不认 xhttp、又没装 lx：start 必须快速给出人话错误、
        不留临时目录（2026-11-20 真机实测 v1.14.4 check 报
        unknown transport type）。lx 路径钉死不存在，保证测试与本机
        是否真装了 lx 无关。"""
        with tempfile.TemporaryDirectory() as d:
            fake = write_fake_binary(d, "fake-sing-box-noxhttp", FAKE_XHTTP_NO)
            eng = SingBoxEngine(binary=fake, socks_port=18093, http_port=18094,
                                lx_binary="/nonexistent/dir/sing-box-lx")
            before = singbox_temp_dirs()
            with self.assertRaises(EngineError) as ctx:
                eng.start(vless_node({"type": "xhttp", "security": "tls"}))
            msg = str(ctx.exception)
            self.assertIn("不支持 xhttp", msg)
            self.assertIn("内核", msg)
            self.assertEqual(singbox_temp_dirs(), before)

    def test_xhttp_capable_kernel_starts(self):
        """内核认 xhttp（check 过）时 start 照常走完生命周期。"""
        with tempfile.TemporaryDirectory() as d:
            fake = write_fake_binary(d, "fake-sing-box-xhttp", FAKE_XHTTP_OK)
            eng = SingBoxEngine(binary=fake, socks_port=18095, http_port=18096,
                                ready_timeout=10.0, lx_binary=fake)
            handle = eng.start(vless_node({"type": "xhttp", "security": "tls"}))
            self.addCleanup(lambda: eng.stop(handle))
            self.assertEqual(eng.status(handle), "running")
            eng.stop(handle)
            self.assertEqual(eng.status(handle), "stopped")

    def test_xhttp_prefers_lx_binary(self):
        """主内核不认 xhttp、lx 认：start 自动改用 lx 跑通。
        非 xhttp 节点则仍然走主内核（lx 只伺候 xhttp）。"""
        with tempfile.TemporaryDirectory() as d:
            main_fake = write_fake_binary(d, "fake-main", FAKE_XHTTP_NO)
            lx_fake = write_fake_binary(d, "fake-lx", FAKE_XHTTP_OK)
            eng = SingBoxEngine(binary=main_fake, lx_binary=lx_fake,
                                socks_port=18097, http_port=18098,
                                ready_timeout=10.0)
            handle = eng.start(vless_node({"type": "xhttp", "security": "tls"}))
            self.addCleanup(lambda: eng.stop(handle))
            self.assertEqual(eng.status(handle), "running")
            eng.stop(handle)
            # 非 xhttp：主内核是好的、lx 是坏的——能跑通就证明没碰 lx
            good_main = write_fake_binary(d, "fake-main-good", FAKE_GOOD)
            bad_lx = write_fake_binary(d, "fake-lx-bad", FAKE_QUIT)
            eng2 = SingBoxEngine(binary=good_main, lx_binary=bad_lx,
                                 socks_port=18099, http_port=18100,
                                 ready_timeout=10.0)
            handle2 = eng2.start(vless_node({"security": "tls"}))
            self.addCleanup(lambda: eng2.stop(handle2))
            self.assertEqual(eng2.status(handle2), "running")
            eng2.stop(handle2)

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


class TestLifecycleGuard(unittest.TestCase):
    """生命周期兜底（2026-11-21）：所有者异常消失不留孤儿引擎。
    真机教训：探测引擎残留一下午（13:58 起、占 10818 无人回收）。"""

    def test_start_writes_owner_meta_and_tracks_live(self):
        with tempfile.TemporaryDirectory() as d:
            fake = write_fake_binary(d, "fake-sing-box", FAKE_GOOD)
            eng = SingBoxEngine(binary=fake, socks_port=18101,
                                http_port=18102, ready_timeout=10.0)
            handle = eng.start(vless_node({"security": "tls"}))
            self.addCleanup(lambda: eng.stop(handle))
            meta_path = os.path.join(handle.config_dir, "engine.json")
            self.assertTrue(os.path.isfile(meta_path))
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
            self.assertEqual(meta["owner_pid"], os.getpid())
            self.assertEqual(meta["proc_pid"], handle.proc.pid)
            self.assertIn(handle, eng._live_handles)
            eng.stop(handle)
            self.assertNotIn(handle, eng._live_handles)

    def test_stop_all_live_stops_everything(self):
        with tempfile.TemporaryDirectory() as d:
            fake = write_fake_binary(d, "fake-sing-box", FAKE_GOOD)
            eng = SingBoxEngine(binary=fake, socks_port=18103,
                                http_port=18104, ready_timeout=10.0)
            handle = eng.start(vless_node({"security": "tls"}))
            self.addCleanup(lambda: eng.stop(handle))
            eng._stop_all_live()
            self.assertEqual(eng.status(handle), "stopped")
            self.assertEqual(eng._live_handles, [])

    def test_reap_kills_orphan_keeps_live_owner(self):
        root = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        orphan_dir = os.path.join(root, "gate-singbox-orphan")
        os.makedirs(orphan_dir)
        sleeper = subprocess.Popen(["sleep", "300"])
        self.addCleanup(
            lambda: sleeper.poll() is None and sleeper.kill())
        with open(os.path.join(orphan_dir, "engine.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"owner_pid": 999999, "proc_pid": sleeper.pid}, f)
        live_dir = os.path.join(root, "gate-singbox-live")
        os.makedirs(live_dir)
        sleeper2 = subprocess.Popen(["sleep", "300"])
        self.addCleanup(
            lambda: sleeper2.poll() is None and sleeper2.kill())
        with open(os.path.join(live_dir, "engine.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"owner_pid": os.getpid(), "proc_pid": sleeper2.pid}, f)
        reaped = SingBoxEngine.reap_stale_engines(root=root)
        self.assertEqual(reaped, 1)
        self.assertIsNotNone(sleeper.poll())     # 孤儿已被杀（poll 收尸）
        self.assertFalse(os.path.exists(orphan_dir))
        self.assertIsNone(sleeper2.poll())        # 活所有者的没动
        self.assertTrue(os.path.isdir(live_dir))


if __name__ == "__main__":
    unittest.main()
