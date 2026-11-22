#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task4 引擎侧消费离线测试（test_engine_extra.py）
==================================================
只测 build_config 产出的 JSON 结构与字段——红线：不联网、不连节点、
不起真实引擎跑流量。覆盖五块的典型 + 边界：
1. vmess-from-extra：clash 直导入条目造 vmess 出站（tcp/ws/grpc 传输、
   tls/reality、缺可选字段不写键、链接路径回归）
2. hysteria2 出站：password/obfs/up/down/alpn/sni/skip-cert-verify
3. tuic 出站：uuid/password/congestion_control/udp_relay_mode
4. wireguard 出站：endpoint 形态（sing-box 1.13 起无 wireguard outbound）
5. ss 插件：obfs-local / v2ray-plugin 映射、不支持插件显式报错

外加：配置帧不变量（单节点、无 urltest/selector）、与 clash_import 的
端到端同构（本机缺 yaml 则跳过）、sing-box check -c 校验（本机无
sing-box 二进制则跳过——任务书允许，不许因此红）。

被测对象是本目录的替换版 singbox_engine.py；client-dev 只读材料，
以 client.engines 子模块名加载本文件，让相对导入解析到真包
（core.model / engines.base 用 client-dev 的真实现，同构不是"长得像"）。
样本全部自构造：假 uuid / 假密钥（随机格式但非真实凭证）/ example.com。
"""
import base64
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

CLIENT_DEV = os.environ.get(
    "GATE_CLIENT_DEV", "/home/hatch/workspace/vpn-project/client-dev")
if CLIENT_DEV not in sys.path:
    sys.path.insert(0, CLIENT_DEV)

from client.core.model import Node                       # noqa: E402
from client.engines import base as engines_base          # noqa: E402

# 集成后的引擎本体：以 client.engines 子模块名加载（与 work4 原型同一份代码，
# 集成时仅改了加载路径；client-dev 只读不动）
_HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "client.engines.singbox_engine_work4",
    os.path.join(_HERE, "..", "engines", "singbox_engine.py"))
singbox_engine = importlib.util.module_from_spec(_spec)
sys.modules["client.engines.singbox_engine_work4"] = singbox_engine
_spec.loader.exec_module(singbox_engine)

SingBoxEngine = singbox_engine.SingBoxEngine
SINGBOX_PROTOCOLS = singbox_engine.SINGBOX_PROTOCOLS
EngineError = engines_base.EngineError

SOCKS_PORT = 18093          # 冷门端口，避免和真服务/真 sing-box 默认端口冲突；
HTTP_PORT = 18094           # 与 test_singbox_engine 同一对，18089 已被本机 ZCode 中继占用

VMESS_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
TUIC_UUID = "77777777-8888-9999-0000-111122223333"


def wg_key(seed: int) -> str:
    """假 wireguard 密钥：确定性生成、格式合法（32 字节 base64），
    不是任何人的真实凭证。格式合法是为了 sing-box check 可用时不因
    key 解码而误报。"""
    return base64.b64encode(bytes((seed + i) % 256 for i in range(32))).decode("ascii")


def clash_node(protocol, entry, direct=None, query=None, host="", port=None,
               config_ref=""):
    """按 clash_import 的 extra 布局手工构造 Node（不经过导入器，
    单测聚焦引擎层；端到端另有 TestEndToEndWithClashImport）。"""
    host = host or str(entry.get("server") or "example.com")
    port = int(port or entry.get("port") or 443)
    extra = {"config_format": "clash_yaml", "clash": dict(entry)}
    if query is not None:
        extra["query"] = dict(query)
    if direct:
        extra.update(direct)
    return Node(id=f"manual:{protocol}:{host}:{port}", name=f"{protocol}-测试",
                protocol=protocol, source="manual", host=host, port=port,
                config_ref=config_ref, extra=extra)


class TestVmessFromClash(unittest.TestCase):
    """块 1：vmess 无链接 JSON、extra["clash"] 在场时按 clash 字段造出站。"""

    def setUp(self):
        self.eng = SingBoxEngine(socks_port=SOCKS_PORT, http_port=HTTP_PORT)

    def outbound(self, node) -> dict:
        cfg = self.eng.build_config(node)
        self.assertEqual(len(cfg["outbounds"]), 1)
        return cfg["outbounds"][0]

    def test_tcp_plain_and_defaults(self):
        node = clash_node("vmess", {"server": "vm.example.com", "port": 8443,
                                    "uuid": VMESS_UUID})
        out = self.outbound(node)
        self.assertEqual(out["type"], "vmess")
        self.assertEqual(out["tag"], "node-out")
        self.assertEqual(out["server"], "vm.example.com")
        self.assertEqual(out["server_port"], 8443)
        self.assertEqual(out["uuid"], VMESS_UUID)
        self.assertEqual(out["security"], "auto")       # cipher 缺省 → auto
        self.assertEqual(out["alter_id"], 0)            # alterId 缺省 → 0
        self.assertNotIn("transport", out)
        self.assertNotIn("tls", out)

    def test_cipher_and_alterid_and_altid(self):
        node = clash_node("vmess", {"server": "vm.example.com", "port": 8443,
                                    "uuid": VMESS_UUID,
                                    "cipher": "chacha20-poly1305",
                                    "alterId": 7})
        out = self.outbound(node)
        self.assertEqual(out["security"], "chacha20-poly1305")
        self.assertEqual(out["alter_id"], 7)
        node2 = clash_node("vmess", {"server": "vm.example.com", "port": 8443,
                                     "uuid": VMESS_UUID, "altId": 3})
        self.assertEqual(self.outbound(node2)["alter_id"], 3)

    def test_ws_with_headers_passthrough_and_tls(self):
        node = clash_node("vmess", {
            "server": "vm.example.com", "port": 8443, "uuid": VMESS_UUID,
            "network": "ws", "tls": True, "servername": "tls.example.com",
            "skip-cert-verify": True, "alpn": ["h2", "http/1.1"],
            "ws-opts": {"path": "/vmws", "headers": {
                "Host": "cdn.example.net", "X-Custom": "v1"}}})
        out = self.outbound(node)
        self.assertEqual(out["transport"], {
            "type": "ws", "path": "/vmws",
            "headers": {"Host": "cdn.example.net", "X-Custom": "v1"}})
        self.assertEqual(out["tls"]["server_name"], "tls.example.com")
        self.assertTrue(out["tls"]["insecure"])
        self.assertEqual(out["tls"]["alpn"], ["h2", "http/1.1"])
        self.assertEqual(out["tls"]["utls"]["fingerprint"], "chrome")

    def test_ws_host_header_fallback_for_sni(self):
        # servername/sni 缺省 → server_name 回退 ws-opts.headers.Host
        node = clash_node("vmess", {
            "server": "vm.example.com", "port": 8443, "uuid": VMESS_UUID,
            "network": "ws", "tls": True,
            "ws-opts": {"path": "/vmws",
                        "headers": {"Host": "cdn.example.net"}}})
        out = self.outbound(node)
        self.assertEqual(out["tls"]["server_name"], "cdn.example.net")
        self.assertNotIn("insecure", out["tls"])        # 缺可选字段不写键

    def test_unsafe_fingerprint_omits_utls(self):
        # 口径同 _vless_tls 现行实现：mihomo "unsafe"/未知指纹不写 utls 块
        node = clash_node("vmess", {
            "server": "vm.example.com", "port": 8443, "uuid": VMESS_UUID,
            "tls": True, "client-fingerprint": "unsafe"})
        out = self.outbound(node)
        self.assertNotIn("utls", out["tls"])

    def test_grpc_transport(self):
        node = clash_node("vmess", {
            "server": "vm.example.com", "port": 8443, "uuid": VMESS_UUID,
            "network": "grpc", "tls": True,
            "grpc-opts": {"grpc-service-name": "VmSvc"}})
        out = self.outbound(node)
        self.assertEqual(out["transport"],
                         {"type": "grpc", "service_name": "VmSvc"})

    def test_h2_transport_uses_first_host(self):
        node = clash_node("vmess", {
            "server": "vm.example.com", "port": 8443, "uuid": VMESS_UUID,
            "network": "h2", "tls": True,
            "h2-opts": {"host": ["h2a.example.com", "h2b.example.com"],
                        "path": "/h2path"}})
        out = self.outbound(node)
        self.assertEqual(out["transport"], {
            "type": "http", "path": "/h2path",
            "headers": {"Host": "h2a.example.com"}})

    def test_reality(self):
        node = clash_node("vmess", {
            "server": "vm.example.com", "port": 8443, "uuid": VMESS_UUID,
            "network": "tcp", "client-fingerprint": "firefox",
            "reality-opts": {"public-key": "pbk-sample-value",
                             "short-id": "0123abcd"}})
        out = self.outbound(node)
        self.assertEqual(out["tls"]["reality"], {
            "enabled": True, "public_key": "pbk-sample-value",
            "short_id": "0123abcd"})
        self.assertEqual(out["tls"]["server_name"], "vm.example.com")
        self.assertEqual(out["tls"]["utls"]["fingerprint"], "firefox")

    def test_reality_missing_public_key(self):
        node = clash_node("vmess", {
            "server": "vm.example.com", "port": 8443, "uuid": VMESS_UUID,
            "reality-opts": {"short-id": "ab"}})
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(node)
        self.assertIn("public-key", str(ctx.exception))

    def test_missing_uuid(self):
        node = clash_node("vmess", {"server": "vm.example.com", "port": 8443})
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(node)
        self.assertIn("uuid", str(ctx.exception))

    def test_bad_alterid(self):
        node = clash_node("vmess", {"server": "vm.example.com", "port": 8443,
                                    "uuid": VMESS_UUID, "alterId": "many"})
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(node)
        self.assertIn("alterId", str(ctx.exception))

    def test_unknown_network(self):
        node = clash_node("vmess", {"server": "vm.example.com", "port": 8443,
                                    "uuid": VMESS_UUID, "network": "kcp"})
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(node)
        self.assertIn("kcp", str(ctx.exception))

    def test_link_path_regression(self):
        # 回归：vmess:// 链接路径（老口径）不受新分派影响
        fields = {"v": "2", "add": "vl.example.com", "port": "8443",
                  "id": VMESS_UUID, "aid": "0", "net": "ws", "tls": "tls",
                  "host": "vmh.example.com", "path": "/vmpath",
                  "sni": "vmsni.example.com", "scy": "auto"}
        payload = base64.b64encode(
            json.dumps(fields, ensure_ascii=False).encode("utf-8")).decode("ascii")
        node = clash_node("vmess", {}, config_ref=f"vmess://{payload}",
                          host="vl.example.com", port=8443)
        out = self.outbound(node)
        self.assertEqual(out["server"], "vl.example.com")
        self.assertEqual(out["transport"],
                         {"type": "ws", "path": "/vmpath",
                          "headers": {"Host": "vmh.example.com"}})
        self.assertEqual(out["tls"]["server_name"], "vmsni.example.com")

    def test_no_link_no_clash(self):
        node = clash_node("vmess", {}, config_ref="")
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(node)
        self.assertIn("无法生成配置", str(ctx.exception))


class TestHysteria2(unittest.TestCase):
    """块 2：hysteria2 出站（clash 条目字段；链接路径今天不存在，见
    extra 直存/query 约定键兜底）。"""

    def setUp(self):
        self.eng = SingBoxEngine(socks_port=SOCKS_PORT, http_port=HTTP_PORT)

    def outbound(self, node) -> dict:
        cfg = self.eng.build_config(node)
        self.assertEqual(len(cfg["outbounds"]), 1)
        return cfg["outbounds"][0]

    def test_full(self):
        node = clash_node("hysteria2", {
            "server": "hy.example.com", "port": 36120, "password": "fake-hy-pw",
            "obfs": "salamander", "obfs-password": "fake-obfs-pw",
            "up": "30 Mbps", "down": 200, "sni": "hy.example.org",
            "skip-cert-verify": True, "alpn": ["h3"]})
        out = self.outbound(node)
        self.assertEqual(out["type"], "hysteria2")
        self.assertEqual(out["server"], "hy.example.com")
        self.assertEqual(out["server_port"], 36120)
        self.assertEqual(out["password"], "fake-hy-pw")
        self.assertEqual(out["obfs"], {"type": "salamander",
                                       "password": "fake-obfs-pw"})
        self.assertEqual(out["up_mbps"], 30)            # "30 Mbps" → 30
        self.assertEqual(out["down_mbps"], 200)
        self.assertEqual(out["tls"], {"enabled": True,
                                      "server_name": "hy.example.org",
                                      "insecure": True, "alpn": ["h3"]})

    def test_auth_fallback(self):
        node = clash_node("hysteria2", {"server": "hy.example.com",
                                        "port": 36120, "auth": "fake-auth"})
        self.assertEqual(self.outbound(node)["password"], "fake-auth")

    def test_minimal_omits_optional_keys(self):
        node = clash_node("hysteria2", {"server": "hy.example.com",
                                        "port": 36120, "password": "fake-hy-pw"})
        out = self.outbound(node)
        for absent in ("obfs", "up_mbps", "down_mbps"):
            self.assertNotIn(absent, out)
        self.assertEqual(out["tls"], {"enabled": True,
                                      "server_name": "hy.example.com"})
        self.assertNotIn("alpn", out["tls"])
        self.assertNotIn("insecure", out["tls"])

    def test_unparseable_bandwidth_not_written(self):
        node = clash_node("hysteria2", {
            "server": "hy.example.com", "port": 36120, "password": "pw",
            "up": "fast", "down": 0})
        out = self.outbound(node)
        self.assertNotIn("up_mbps", out)                # 解析不出正整数不写键
        self.assertNotIn("down_mbps", out)

    def test_obfs_without_password(self):
        # sing-box v1.14.4 源码口径：obfs 在场而密码为空 → 起不来，先行报错
        node = clash_node("hysteria2", {"server": "hy.example.com",
                                        "port": 36120, "password": "pw",
                                        "obfs": "salamander"})
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(node)
        self.assertIn("obfs-password", str(ctx.exception))

    def test_missing_password(self):
        node = clash_node("hysteria2", {"server": "hy.example.com",
                                        "port": 36120})
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(node)
        self.assertIn("password", str(ctx.exception))

    def test_query_convention_fallback(self):
        # 链接导入的 hy2 今天不存在（manual.py 不解析 hy2://），但只要导入器
        # 按现有 extra 约定写字段（password 直存、query 链接口径），引擎即可用
        node = clash_node("hysteria2", {"server": "hy.example.com",
                                        "port": 36120},
                          direct={"password": "fake-hy-pw"},
                          query={"sni": "hy.example.org",
                                 "allowInsecure": "1", "alpn": "h3"})
        out = self.outbound(node)
        self.assertEqual(out["password"], "fake-hy-pw")
        self.assertEqual(out["tls"], {"enabled": True,
                                      "server_name": "hy.example.org",
                                      "insecure": True, "alpn": ["h3"]})
        self.assertNotIn("obfs", out)


class TestTuic(unittest.TestCase):
    """块 3：tuic 出站。"""

    def setUp(self):
        self.eng = SingBoxEngine(socks_port=SOCKS_PORT, http_port=HTTP_PORT)

    def outbound(self, node) -> dict:
        cfg = self.eng.build_config(node)
        self.assertEqual(len(cfg["outbounds"]), 1)
        return cfg["outbounds"][0]

    def test_full(self):
        node = clash_node("tuic", {
            "server": "tu.example.com", "port": 443, "uuid": TUIC_UUID,
            "password": "fake-tu-pw", "congestion-controller": "bbr",
            "udp-relay-mode": "native", "sni": "tu.example.org",
            "skip-cert-verify": True, "alpn": ["h3"]})
        out = self.outbound(node)
        self.assertEqual(out["type"], "tuic")
        self.assertEqual(out["uuid"], TUIC_UUID)
        self.assertEqual(out["password"], "fake-tu-pw")
        self.assertEqual(out["congestion_control"], "bbr")
        self.assertEqual(out["udp_relay_mode"], "native")
        self.assertEqual(out["tls"], {"enabled": True,
                                      "server_name": "tu.example.org",
                                      "insecure": True, "alpn": ["h3"]})

    def test_congestion_control_alt_key(self):
        # congestion-controller 是 mihomo 现行字段名，兼容老写法 congestion-control
        node = clash_node("tuic", {"server": "tu.example.com", "port": 443,
                                   "uuid": TUIC_UUID, "password": "pw",
                                   "congestion-control": "cubic"})
        self.assertEqual(self.outbound(node)["congestion_control"], "cubic")

    def test_minimal_omits_optional_keys(self):
        node = clash_node("tuic", {"server": "tu.example.com", "port": 443,
                                   "uuid": TUIC_UUID, "password": "pw"})
        out = self.outbound(node)
        self.assertNotIn("congestion_control", out)
        self.assertNotIn("udp_relay_mode", out)
        self.assertEqual(out["tls"], {"enabled": True,
                                      "server_name": "tu.example.com"})

    def test_missing_uuid_or_password(self):
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(clash_node(
                "tuic", {"server": "tu.example.com", "port": 443,
                         "password": "pw"}))
        self.assertIn("uuid", str(ctx.exception))
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(clash_node(
                "tuic", {"server": "tu.example.com", "port": 443,
                         "uuid": TUIC_UUID}))
        self.assertIn("password", str(ctx.exception))


class TestWireguard(unittest.TestCase):
    """块 4：wireguard 出站（endpoint 形态）。"""

    def setUp(self):
        self.eng = SingBoxEngine(socks_port=SOCKS_PORT, http_port=HTTP_PORT)

    def endpoint(self, node) -> dict:
        cfg = self.eng.build_config(node)
        self.assertEqual(cfg["outbounds"], [])          # wireguard 无出站条目
        self.assertEqual(len(cfg["endpoints"]), 1)
        return cfg["endpoints"][0]

    def test_full(self):
        node = clash_node("wireguard", {
            "server": "wg.example.com", "port": 51820, "ip": "172.16.0.2",
            "ipv6": "fd01:5ca1:ab1e:80fa:ab85:2ba2:7456:b54d",
            "private-key": wg_key(1), "public-key": wg_key(2),
            "pre-shared-key": wg_key(3), "reserved": [209, 38, 130],
            "mtu": 1408, "allowed-ips": ["10.0.0.0/8"]})
        ep = self.endpoint(node)
        self.assertEqual(ep["type"], "wireguard")
        self.assertEqual(ep["tag"], "node-out")
        self.assertEqual(ep["address"], ["172.16.0.2/32",
                                         "fd01:5ca1:ab1e:80fa:ab85:2ba2:7456:b54d/128"])
        self.assertEqual(ep["private_key"], wg_key(1))
        self.assertEqual(ep["mtu"], 1408)
        self.assertEqual(ep["peers"], [{
            "address": "wg.example.com", "port": 51820,
            "public_key": wg_key(2), "allowed_ips": ["10.0.0.0/8"],
            "pre_shared_key": wg_key(3), "reserved": [209, 38, 130]}])

    def test_minimal_defaults(self):
        node = clash_node("wireguard", {"server": "wg.example.com",
                                        "port": 51820, "ip": "172.16.0.2",
                                        "private-key": wg_key(1),
                                        "public-key": wg_key(2)})
        ep = self.endpoint(node)
        self.assertEqual(ep["address"], ["172.16.0.2/32"])   # v4 自动补 /32
        self.assertNotIn("mtu", ep)                     # 缺省走 sing-box 1408
        peer = ep["peers"][0]
        self.assertEqual(peer["allowed_ips"], ["0.0.0.0/0"])  # 任务书默认口径
        self.assertNotIn("pre_shared_key", peer)        # 缺可选字段不写键
        self.assertNotIn("reserved", peer)

    def test_address_with_existing_mask_kept(self):
        node = clash_node("wireguard", {"server": "wg.example.com",
                                        "port": 51820, "ip": "10.0.0.5/24",
                                        "private-key": wg_key(1),
                                        "public-key": wg_key(2)})
        self.assertEqual(self.endpoint(node)["address"], ["10.0.0.5/24"])

    def test_frame_routes_through_endpoint_tag(self):
        # route.final 与 remote-dns 的 detour 指向 node-out；标签查找回落
        # endpoint 管理器（v1.14.4 adapter/outbound/manager.go）
        node = clash_node("wireguard", {"server": "wg.example.com",
                                        "port": 51820, "ip": "172.16.0.2",
                                        "private-key": wg_key(1),
                                        "public-key": wg_key(2)})
        cfg = self.eng.build_config(node)
        self.assertEqual(cfg["route"]["final"], "node-out")
        self.assertEqual(cfg["dns"]["servers"][0]["detour"], "node-out")
        self.assertNotIn("urltest", json.dumps(cfg))
        self.assertNotIn("selector", json.dumps(cfg))

    def test_missing_keys(self):
        base = {"server": "wg.example.com", "port": 51820, "ip": "172.16.0.2",
                "public-key": wg_key(2)}
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(clash_node("wireguard", dict(base)))
        self.assertIn("private-key", str(ctx.exception))
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(clash_node("wireguard", {
                "server": "wg.example.com", "port": 51820, "ip": "172.16.0.2",
                "private-key": wg_key(1)}))
        self.assertIn("public-key", str(ctx.exception))

    def test_missing_local_address(self):
        # 有意收紧（任务书"有才写"）：sing-box endpoint 必填 address，
        # ip/ipv6 全缺时报错，不产出必挂配置
        node = clash_node("wireguard", {"server": "wg.example.com",
                                        "port": 51820,
                                        "private-key": wg_key(1),
                                        "public-key": wg_key(2)})
        with self.assertRaises(EngineError) as ctx:
            self.eng.build_config(node)
        self.assertIn("address", str(ctx.exception))

    def test_extra_direct_fields_fallback(self):
        # clash 条目缺字段时读 clash_import 直存进 extra 的同值字段
        node = clash_node("wireguard", {"server": "wg.example.com",
                                        "port": 51820},
                          direct={"private_key": wg_key(1),
                                  "public_key": wg_key(2),
                                  "local_address": "172.16.0.9"})
        ep = self.endpoint(node)
        self.assertEqual(ep["private_key"], wg_key(1))
        self.assertEqual(ep["address"], ["172.16.0.9/32"])
        self.assertEqual(ep["peers"][0]["public_key"], wg_key(2))


class TestSSPlugin(unittest.TestCase):
    """块 5：ss 插件（extra["clash"] 的 plugin/plugin-opts → sing-box
    plugin/plugin_opts）。"""

    def setUp(self):
        self.eng = SingBoxEngine(socks_port=SOCKS_PORT, http_port=HTTP_PORT)

    def outbound(self, entry) -> dict:
        node = clash_node("ss", entry, direct={"method": "aes-256-gcm",
                                               "password": "ss-pass-123"})
        cfg = self.eng.build_config(node)
        self.assertEqual(len(cfg["outbounds"]), 1)
        return cfg["outbounds"][0]

    def test_obfs_plugin(self):
        out = self.outbound({"server": "ss.example.com", "port": 8388,
                             "cipher": "aes-256-gcm", "password": "ss-pass-123",
                             "plugin": "obfs",
                             "plugin-opts": {"mode": "http",
                                             "host": "x.example.com"}})
        self.assertEqual(out["plugin"], "obfs-local")
        self.assertEqual(out["plugin_opts"], "obfs=http;obfs-host=x.example.com")

    def test_simple_obfs_alias(self):
        out = self.outbound({"server": "ss.example.com", "port": 8388,
                             "plugin": "simple-obfs",
                             "plugin-opts": {"mode": "tls"}})
        self.assertEqual(out["plugin"], "obfs-local")
        self.assertEqual(out["plugin_opts"], "obfs=tls")

    def test_v2ray_plugin(self):
        out = self.outbound({"server": "ss.example.com", "port": 8388,
                             "plugin": "v2ray-plugin",
                             "plugin-opts": {"mode": "websocket",
                                             "host": "v.example.com",
                                             "path": "/ws", "tls": True}})
        self.assertEqual(out["plugin"], "v2ray-plugin")
        self.assertEqual(out["plugin_opts"],
                         "mode=websocket;host=v.example.com;path=/ws;tls")

    def test_unsupported_plugin_explicit_error(self):
        # 红线：sing-box 不支持的插件名显式报错，不许静默丢弃
        with self.assertRaises(EngineError) as ctx:
            self.outbound({"server": "ss.example.com", "port": 8388,
                           "plugin": "shadow-tls",
                           "plugin-opts": {"version": 3}})
        self.assertIn("shadow-tls", str(ctx.exception))
        self.assertIn("不支持", str(ctx.exception))

    def test_no_plugin_no_keys(self):
        out = self.outbound({"server": "ss.example.com", "port": 8388})
        self.assertNotIn("plugin", out)
        self.assertNotIn("plugin_opts", out)

    def test_obfs_partial_opts(self):
        # 只有 host 没有 mode：写有的部分，不猜缺省值
        out = self.outbound({"server": "ss.example.com", "port": 8388,
                             "plugin": "obfs",
                             "plugin-opts": {"host": "x.example.com"}})
        self.assertEqual(out["plugin_opts"], "obfs-host=x.example.com")


class TestFrameInvariants(unittest.TestCase):
    """P0 帧不变量跨新协议全查：单节点单出站、无 urltest/selector、
    入站只绑本机、wireguard 走 endpoints。"""

    def setUp(self):
        self.eng = SingBoxEngine(socks_port=SOCKS_PORT, http_port=HTTP_PORT)

    def _nodes(self):
        return [
            clash_node("vmess", {"server": "vm.example.com", "port": 8443,
                                 "uuid": VMESS_UUID, "network": "ws",
                                 "tls": True, "ws-opts": {"path": "/w"}},
                       config_ref=""),
            clash_node("hysteria2", {"server": "hy.example.com",
                                     "port": 36120, "password": "pw"}),
            clash_node("tuic", {"server": "tu.example.com", "port": 443,
                                "uuid": TUIC_UUID, "password": "pw"}),
            clash_node("ss", {"server": "ss.example.com", "port": 8388,
                              "plugin": "obfs",
                              "plugin-opts": {"mode": "http"}},
                       direct={"method": "aes-256-gcm",
                               "password": "ss-pass-123"}),
            clash_node("wireguard", {"server": "wg.example.com",
                                     "port": 51820, "ip": "172.16.0.2",
                                     "private-key": wg_key(1),
                                     "public-key": wg_key(2)}),
        ]

    def test_no_urltest_selector_and_single_node(self):
        for node in self._nodes():
            with self.subTest(protocol=node.protocol):
                text = json.dumps(self.eng.build_config(node), ensure_ascii=False)
                self.assertNotIn("urltest", text)
                self.assertNotIn("selector", text)
                cfg = json.loads(text)
                self.assertEqual(cfg["route"]["final"], "node-out")
                for rule in cfg["route"].get("rules", []):
                    self.assertEqual(rule.get("action"), "resolve")
                if node.protocol == "wireguard":
                    self.assertEqual(cfg["outbounds"], [])
                    self.assertEqual(cfg["endpoints"][0]["tag"], "node-out")
                else:
                    self.assertEqual(len(cfg["outbounds"]), 1)
                    self.assertEqual(cfg["outbounds"][0]["tag"], "node-out")

    def test_top_level_keys(self):
        for node in self._nodes():
            with self.subTest(protocol=node.protocol):
                cfg = self.eng.build_config(node)
                expected = {"log", "dns", "inbounds", "outbounds", "route"}
                if node.protocol == "wireguard":
                    expected.add("endpoints")
                self.assertEqual(set(cfg.keys()), expected)
                self.assertEqual(cfg["log"], {"level": "warn", "timestamp": True})

    def test_inbounds_localhost_only(self):
        for node in self._nodes():
            cfg = self.eng.build_config(node)
            socks_in, http_in = cfg["inbounds"]
            self.assertEqual(socks_in["listen"], "127.0.0.1")
            self.assertEqual(http_in["listen"], "127.0.0.1")
            self.assertEqual(socks_in["listen_port"], SOCKS_PORT)
            self.assertEqual(http_in["listen_port"], HTTP_PORT)

    def test_protocol_tuple_extended(self):
        self.assertEqual(SINGBOX_PROTOCOLS,
                         ("vless", "ss", "trojan", "vmess",
                          "hysteria2", "tuic", "wireguard"))


class TestEndToEndWithClashImport(unittest.TestCase):
    """端到端同构：真实 clash_import 导入的节点直接喂给替换版引擎。
    本机缺 yaml 时跳过（不因此红）。"""

    def setUp(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("本机无 PyYAML，跳过端到端用例")
        from client.sources.clash_import import import_clash_text
        self.import_clash_text = import_clash_text
        self.eng = SingBoxEngine(socks_port=SOCKS_PORT, http_port=HTTP_PORT)

    CLASH_YAML = """\
proxies:
  - name: vm-clash
    type: vmess
    server: vm.example.com
    port: 8443
    uuid: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
    alterId: 0
    cipher: auto
    tls: true
    servername: tls.example.com
    network: ws
    ws-opts:
      path: /vmws
      headers:
        Host: cdn.example.net
  - name: hy2-clash
    type: hysteria2
    server: hy.example.com
    port: 36120
    password: fake-hy-pw
    sni: hy.example.org
    obfs: salamander
    obfs-password: fake-obfs-pw
    up: 30 Mbps
    down: 200 Mbps
  - name: tuic-clash
    type: tuic
    server: tu.example.com
    port: 443
    uuid: 77777777-8888-9999-0000-111122223333
    password: fake-tu-pw
    congestion-controller: bbr
    udp-relay-mode: native
    sni: tu.example.org
    skip-cert-verify: true
  - name: wg-clash
    type: wireguard
    server: wg.example.com
    port: 51820
    ip: 172.16.0.2
    private-key: WGPRIV
    public-key: WGPUB
    mtu: 1408
  - name: ss-plugin-clash
    type: ss
    server: ss.example.com
    port: 8388
    cipher: aes-256-gcm
    password: ss-pass-123
    plugin: obfs
    plugin-opts:
      mode: http
      host: x.example.com
"""

    def _node_by_name(self, nodes, name):
        return next(n for n in nodes if n.name == name)

    def test_all_five_flow_through_engine(self):
        result = self.import_clash_text(self.CLASH_YAML)
        self.assertEqual(result.skipped, 0)
        self.assertEqual(len(result.nodes), 5)
        by_protocol = {n.protocol: n for n in result.nodes}

        # vmess：真实导入节点的 extra["clash"] 直接可用
        out = self.eng.build_config(by_protocol["vmess"])["outbounds"][0]
        self.assertEqual(out["server"], "vm.example.com")
        self.assertEqual(out["transport"]["path"], "/vmws")
        self.assertEqual(out["tls"]["server_name"], "tls.example.com")

        # hysteria2
        out = self.eng.build_config(by_protocol["hysteria2"])["outbounds"][0]
        self.assertEqual(out["obfs"], {"type": "salamander",
                                       "password": "fake-obfs-pw"})
        self.assertEqual(out["up_mbps"], 30)
        self.assertEqual(out["down_mbps"], 200)

        # tuic
        out = self.eng.build_config(by_protocol["tuic"])["outbounds"][0]
        self.assertEqual(out["congestion_control"], "bbr")
        self.assertEqual(out["udp_relay_mode"], "native")
        self.assertTrue(out["tls"]["insecure"])

        # wireguard（endpoint 形态）
        cfg = self.eng.build_config(by_protocol["wireguard"])
        ep = cfg["endpoints"][0]
        self.assertEqual(ep["address"], ["172.16.0.2/32"])
        self.assertEqual(ep["private_key"], "WGPRIV")
        self.assertEqual(ep["peers"][0]["public_key"], "WGPUB")
        self.assertEqual(ep["mtu"], 1408)

        # ss 插件
        out = self.eng.build_config(by_protocol["ss"])["outbounds"][0]
        self.assertEqual(out["plugin"], "obfs-local")
        self.assertEqual(out["plugin_opts"], "obfs=http;obfs-host=x.example.com")

    def test_vmess_clash_node_config_ref_is_yaml_not_link(self):
        # 直导入节点的 config_ref 是原条目 YAML 文本，vmess 分派必须走
        # clash 路径而不是误判成链接
        result = self.import_clash_text(self.CLASH_YAML)
        node = self._node_by_name(result.nodes, "vm-clash")
        self.assertFalse(node.config_ref.lower().startswith("vmess://"))
        out = self.eng.build_config(node)["outbounds"][0]
        self.assertEqual(out["server"], "vm.example.com")


class TestSingBoxCheck(unittest.TestCase):
    """若本机有 sing-box 二进制：对每类节点跑一次 `sing-box check -c`
    结构校验（check 只解析配置、不联网）。找不到二进制就 skip——
    任务书明确不许因此红。"""

    def setUp(self):
        self.eng = SingBoxEngine(socks_port=SOCKS_PORT, http_port=HTTP_PORT)
        self.binary = self.eng.find_binary()
        if not self.binary:
            self.skipTest("本机无 sing-box 二进制（任务书允许 skip）")

    def _nodes(self):
        return [
            clash_node("vmess", {"server": "vm.example.com", "port": 8443,
                                 "uuid": VMESS_UUID, "network": "ws",
                                 "tls": True, "servername": "s.example.com",
                                 "ws-opts": {"path": "/w"}},
                       config_ref=""),
            clash_node("hysteria2", {"server": "hy.example.com",
                                     "port": 36120, "password": "pw",
                                     "obfs": "salamander",
                                     "obfs-password": "obfs-pw",
                                     "up": "30 Mbps", "down": 200}),
            clash_node("tuic", {"server": "tu.example.com", "port": 443,
                                "uuid": TUIC_UUID, "password": "pw",
                                "congestion-controller": "bbr"}),
            clash_node("ss", {"server": "ss.example.com", "port": 8388,
                              "plugin": "obfs",
                              "plugin-opts": {"mode": "http"}},
                       direct={"method": "aes-256-gcm",
                               "password": "ss-pass-123"}),
            clash_node("wireguard", {"server": "wg.example.com",
                                     "port": 51820, "ip": "172.16.0.2",
                                     "private-key": wg_key(1),
                                     "public-key": wg_key(2)}),
        ]

    def test_check_passes_for_all_protocols(self):
        for node in self._nodes():
            with self.subTest(protocol=node.protocol):
                cfg = self.eng.build_config(node)
                fd, path = tempfile.mkstemp(prefix="gate-check-", suffix=".json")
                os.close(fd)
                try:
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, ensure_ascii=False, indent=2)
                    r = subprocess.run([self.binary, "check", "-c", path],
                                       capture_output=True, text=True,
                                       timeout=30)
                    out = ((r.stdout or "") + (r.stderr or "")).strip()
                    self.assertEqual(
                        r.returncode, 0,
                        f"{node.protocol} 配置未通过 sing-box check: {out[:300]}")
                finally:
                    os.remove(path)


if __name__ == "__main__":
    unittest.main()


class _FakeProc:
    """只给 poll() 的假进程：None=还在跑，数字=已退出。"""

    def __init__(self, ret=None):
        self._ret = ret

    def poll(self):
        return self._ret


class TestActiveLogPath(unittest.TestCase):
    """task7 集成：SingBoxEngine.active_log_path() 只读访问器。"""

    def _engine(self):
        return SingBoxEngine(binary="/nonexistent/sing-box",
                             socks_port=SOCKS_PORT, http_port=HTTP_PORT)

    def _handle(self, log_path, ret=None):
        return singbox_engine._Handle(node_id="n1", proc=_FakeProc(ret),
                                      config_dir="/tmp/gate-test",
                                      log_path=log_path)

    def test_no_handle_returns_none(self):
        self.assertIsNone(self._engine().active_log_path())

    def test_live_handle_returns_log_path(self):
        eng = self._engine()
        eng._live_handles.append(self._handle("/tmp/gate-test/sing-box.log"))
        self.assertEqual(eng.active_log_path(), "/tmp/gate-test/sing-box.log")

    def test_exited_proc_returns_none(self):
        eng = self._engine()
        eng._live_handles.append(self._handle("/tmp/gate-test/sing-box.log", ret=1))
        self.assertIsNone(eng.active_log_path())

    def test_latest_live_wins(self):
        eng = self._engine()
        eng._live_handles.append(self._handle("/tmp/old/sing-box.log"))
        eng._live_handles.append(self._handle("/tmp/new/sing-box.log", ret=1))
        self.assertEqual(eng.active_log_path(), "/tmp/old/sing-box.log")
