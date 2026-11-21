#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""clash_import 离线测试：Clash YAML 直导入的七协议逐字段映射、保真、
同构与坏条目处理。按任务书红线：全部用自构造 YAML 样本（example.com /
假 uuid / 假密钥），不读真实订阅文件、不联网、不碰真实节点。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))

import yaml  # noqa: E402

from client.sources import clash_import  # noqa: E402
from client.core.model import Node, make_node_id  # noqa: E402
from client.sources import manual as manual_src   # noqa: E402


# ---------------- 自构造样本（全部假数据） ----------------

SS_YAML = """\
proxies:
  - name: ss-obfs-sample
    type: ss
    server: 203.0.113.10
    port: 8388
    cipher: aes-256-gcm
    password: fake-ss-pw
    udp: true
    plugin: obfs
    plugin-opts:
      mode: http
      host: example.cloudflare.website
"""

VMESS_YAML = """\
proxies:
  - name: vmess-ws-sample
    type: vmess
    server: vm.example.com
    port: 443
    uuid: 11111111-2222-3333-4444-555555555555
    alterId: 64
    cipher: aes-128-gcm
    udp: true
    tls: true
    servername: vm.example.org
    client-fingerprint: chrome
    skip-cert-verify: true
    alpn:
      - h2
      - http/1.1
    network: ws
    ws-opts:
      path: /vmws
      headers:
        Host: ws.example.net
      max-early-data: 2048
      early-data-header-name: Sec-WebSocket-Protocol
"""

VLESS_YAML = """\
proxies:
  - name: vless-reality-sample
    type: vless
    server: vl.example.com
    port: 443
    uuid: aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee
    flow: xtls-rprx-vision
    tls: true
    servername: real.example.com
    client-fingerprint: firefox
    skip-cert-verify: true
    udp: true
    alpn:
      - h3
    reality-opts:
      public-key: pbk-sample-value
      short-id: "0123abcd"
    network: ws
    ws-opts:
      path: /vlws
      headers:
        Host: cdn.example.net
"""

TROJAN_YAML = """\
proxies:
  - name: trojan-grpc-sample
    type: trojan
    server: tj.example.com
    port: 443
    password: fake-tj-pw
    sni: tj.example.org
    skip-cert-verify: true
    client-fingerprint: ios
    alpn:
      - h2
      - http/1.1
    network: grpc
    grpc-opts:
      grpc-service-name: TjGrpcSvc
"""

HY2_YAML = """\
proxies:
  - name: hy2-sample
    type: hysteria2
    server: hy.example.com
    port: 36120
    password: fake-hy-pw
    sni: hy.example.org
    skip-cert-verify: false
    fingerprint: chrome
    alpn:
      - h3
    up: 30 Mbps
    down: 200 Mbps
    obfs: salamander
    obfs-password: fake-obfs-pw
    ports: 20000-30000
"""

TUIC_YAML = """\
proxies:
  - name: tuic-sample
    type: tuic
    server: tu.example.com
    port: 443
    uuid: 77777777-8888-9999-0000-111122223333
    password: fake-tu-pw
    congestion-controller: bbr
    udp-relay-mode: native
    reduce-rtt: true
    sni: tu.example.org
    skip-cert-verify: true
    fingerprint: safari
    alpn:
      - h3
"""

WG_YAML = """\
proxies:
  - name: wg-sample
    type: wireguard
    server: wg.example.com
    port: 51820
    ip: 172.16.0.2
    ipv6: fd01:5ca1:ab1e:80fa:ab85:2ba2:7456:b54d
    private-key: fake-private-key-b64=
    public-key: fake-peer-pubkey-b64=
    pre-shared-key: fake-psk-b64=
    reserved: [209, 38, 130]
    udp: true
    mtu: 1408
    dns: 1.1.1.1
    persistent-keepalive: 25
"""

def load_yaml(text: str) -> dict:
    return yaml.safe_load(text)


def compose(*docs: str) -> str:
    """把若干单协议样本文档合并成一份 proxies 文档（各样本都有自己的
    proxies: 根，直接字符串拼接不是合法 YAML，必须按条目重组）。"""
    entries = []
    for d in docs:
        entries.extend(yaml.safe_load(d)["proxies"])
    return yaml.safe_dump({"proxies": entries}, allow_unicode=True,
                          sort_keys=False, default_flow_style=False)


FULL_YAML = compose(SS_YAML, VMESS_YAML, VLESS_YAML, TROJAN_YAML,
                    HY2_YAML, TUIC_YAML, WG_YAML)


class Base(unittest.TestCase):
    def import_one(self, text: str) -> Node:
        result = clash_import.import_clash_text(text)
        self.assertEqual(result.skipped, 0, result.skipped_reasons)
        self.assertEqual(len(result.nodes), 1)
        return result.nodes[0]


class TestProtocolMapping(Base):
    """七协议逐字段映射：关键字段不丢（老转换器的已知丢字段逐个补回）。"""

    def test_ss_plugin_fields_survive(self):
        """ss：老转换器整个丢掉 plugin/plugin-opts，直导入必须保真。"""
        node = self.import_one(SS_YAML)
        entry = load_yaml(SS_YAML)["proxies"][0]
        self.assertEqual(node.protocol, "ss")
        self.assertEqual(node.host, "203.0.113.10")
        self.assertEqual(node.port, 8388)
        self.assertEqual(node.name, "ss-obfs-sample")
        self.assertEqual(node.extra["method"], "aes-256-gcm")
        self.assertEqual(node.extra["password"], "fake-ss-pw")
        self.assertEqual(node.extra["clash"]["plugin"], "obfs")
        self.assertEqual(node.extra["clash"]["plugin-opts"]["mode"], "http")
        self.assertEqual(node.extra["clash"]["plugin-opts"]["host"],
                         "example.cloudflare.website")
        self.assertEqual(node.extra["clash"], entry)   # 原条目零丢弃

    def test_vmess_fields(self):
        """vmess：aid/scy/tls/sni/ws 全进 query，early-data 进 clash 保真。"""
        node = self.import_one(VMESS_YAML)
        entry = load_yaml(VMESS_YAML)["proxies"][0]
        q = node.extra["query"]
        self.assertEqual(node.protocol, "vmess")
        self.assertEqual(node.extra["uuid"],
                         "11111111-2222-3333-4444-555555555555")
        self.assertEqual(q["aid"], "64")
        self.assertEqual(q["scy"], "aes-128-gcm")
        self.assertEqual(q["security"], "tls")
        self.assertEqual(q["sni"], "vm.example.org")
        self.assertEqual(q["fp"], "chrome")
        self.assertEqual(q["allowInsecure"], "1")
        self.assertEqual(q["alpn"], "h2,http/1.1")
        self.assertEqual(q["type"], "ws")
        self.assertEqual(q["path"], "/vmws")
        self.assertEqual(q["host"], "ws.example.net")
        # 老转换器丢 ws early-data：这里保真
        self.assertEqual(node.extra["clash"]["ws-opts"]["max-early-data"], 2048)
        self.assertEqual(node.extra["clash"]["ws-opts"]["early-data-header-name"],
                         "Sec-WebSocket-Protocol")
        self.assertEqual(node.extra["clash"], entry)

    def test_vless_reality_alpn_survive(self):
        """vless：老转换器丢 alpn 与 skip-cert-verify；reality/flow/ws 必须全在。"""
        node = self.import_one(VLESS_YAML)
        q = node.extra["query"]
        self.assertEqual(node.protocol, "vless")
        self.assertEqual(node.extra["uuid"],
                         "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
        self.assertEqual(q["security"], "reality")
        self.assertEqual(q["pbk"], "pbk-sample-value")
        self.assertEqual(q["sid"], "0123abcd")
        self.assertEqual(q["flow"], "xtls-rprx-vision")
        self.assertEqual(q["sni"], "real.example.com")
        self.assertEqual(q["fp"], "firefox")
        self.assertEqual(q["allowInsecure"], "1")   # 老转换器 vless 丢的
        self.assertEqual(q["alpn"], "h3")           # 老转换器 vless 丢的
        self.assertEqual(q["type"], "ws")
        self.assertEqual(q["path"], "/vlws")
        self.assertEqual(q["host"], "cdn.example.net")
        self.assertEqual(q["encryption"], "none")

    def test_trojan_grpc_fp_alpn_survive(self):
        """trojan：老转换器丢 grpc serviceName 与 fp/alpn，直导入补回。"""
        node = self.import_one(TROJAN_YAML)
        q = node.extra["query"]
        self.assertEqual(node.protocol, "trojan")
        self.assertEqual(node.extra["password"], "fake-tj-pw")
        self.assertEqual(q["sni"], "tj.example.org")
        self.assertEqual(q["serviceName"], "TjGrpcSvc")   # 老转换器丢的
        self.assertEqual(q["type"], "grpc")
        self.assertEqual(q["fp"], "ios")                  # 老转换器丢的
        self.assertEqual(q["alpn"], "h2,http/1.1")        # 老转换器丢的
        self.assertEqual(q["allowInsecure"], "1")

    def test_hysteria2(self):
        """hysteria2：老转换器直接跳过，直导入产出完整节点。"""
        node = self.import_one(HY2_YAML)
        entry = load_yaml(HY2_YAML)["proxies"][0]
        q = node.extra["query"]
        self.assertEqual(node.protocol, "hysteria2")
        self.assertEqual(node.host, "hy.example.com")
        self.assertEqual(node.port, 36120)
        self.assertEqual(node.extra["password"], "fake-hy-pw")
        self.assertEqual(q["sni"], "hy.example.org")
        self.assertEqual(q["fp"], "chrome")
        self.assertEqual(q["alpn"], "h3")
        self.assertNotIn("allowInsecure", q)      # skip-cert-verify: false 不写
        self.assertEqual(node.extra["clash"]["obfs"], "salamander")
        self.assertEqual(node.extra["clash"]["obfs-password"], "fake-obfs-pw")
        self.assertEqual(node.extra["clash"]["up"], "30 Mbps")
        self.assertEqual(node.extra["clash"]["ports"], "20000-30000")
        self.assertEqual(node.extra["clash"], entry)

    def test_tuic(self):
        """tuic：老转换器直接跳过，直导入产出完整节点。"""
        node = self.import_one(TUIC_YAML)
        entry = load_yaml(TUIC_YAML)["proxies"][0]
        q = node.extra["query"]
        self.assertEqual(node.protocol, "tuic")
        self.assertEqual(node.extra["uuid"],
                         "77777777-8888-9999-0000-111122223333")
        self.assertEqual(node.extra["password"], "fake-tu-pw")
        self.assertEqual(q["sni"], "tu.example.org")
        self.assertEqual(q["alpn"], "h3")
        self.assertEqual(q["fp"], "safari")
        self.assertEqual(q["allowInsecure"], "1")
        self.assertEqual(node.extra["clash"]["congestion-controller"], "bbr")
        self.assertEqual(node.extra["clash"]["udp-relay-mode"], "native")
        self.assertEqual(node.extra["clash"], entry)

    def test_wireguard(self):
        """wireguard：老转换器直接跳过，直导入产出完整节点。"""
        node = self.import_one(WG_YAML)
        entry = load_yaml(WG_YAML)["proxies"][0]
        self.assertEqual(node.protocol, "wireguard")
        self.assertEqual(node.host, "wg.example.com")
        self.assertEqual(node.port, 51820)
        self.assertEqual(node.extra["private_key"], "fake-private-key-b64=")
        self.assertEqual(node.extra["public_key"], "fake-peer-pubkey-b64=")
        self.assertEqual(node.extra["pre_shared_key"], "fake-psk-b64=")
        self.assertEqual(node.extra["local_address"], "172.16.0.2")
        self.assertEqual(node.extra["local_address_v6"],
                         "fd01:5ca1:ab1e:80fa:ab85:2ba2:7456:b54d")
        self.assertEqual(node.extra["reserved"], [209, 38, 130])
        self.assertEqual(node.extra["clash"]["mtu"], 1408)
        self.assertEqual(node.extra["clash"]["persistent-keepalive"], 25)
        self.assertEqual(node.extra["clash"], entry)


class TestStructureAndIso(Base):
    """与现有导入同构：Node 结构、id 口径、query 链接口径逐项对齐。"""

    def test_node_structure_matches_subscription_path(self):
        """同构对照：同一个 vless 端点，直导入节点与
        clash2links 风格链接 → manual.parse_link 的现有导入节点，
        id/host/port/name/uuid 相同，query 的可映射子集一致。"""
        node = self.import_one(VLESS_YAML)
        entry = load_yaml(VLESS_YAML)["proxies"][0]
        # 老 pipeline 产出的等价链接（conv_vless 口径：丢 alpn/allowInsecure）
        link = ("vless://aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee@vl.example.com:443"
                "?encryption=none&security=reality&pbk=pbk-sample-value"
                "&sid=0123abcd&sni=real.example.com&fp=firefox"
                "&flow=xtls-rprx-vision&type=ws&path=%2Fvlws"
                "&host=cdn.example.net#vless-reality-sample")
        ref = manual_src.parse_link(link)
        self.assertEqual(node.id, ref.id)
        self.assertEqual(node.host, ref.host)
        self.assertEqual(node.port, ref.port)
        self.assertEqual(node.name, ref.name)
        self.assertEqual(node.source, ref.source)
        self.assertEqual(node.protocol, ref.protocol)
        self.assertEqual(node.extra["uuid"], ref.extra["uuid"])
        for key in ("encryption", "security", "pbk", "sid", "sni", "fp",
                    "flow", "type", "path", "host"):
            self.assertEqual(node.extra["query"][key], ref.extra["query"][key],
                             f"query[{key}] 与链接口径不一致")
        # 直导入多出来的：alpn / allowInsecure（老链路丢的）
        self.assertEqual(node.extra["query"]["alpn"], "h3")
        self.assertEqual(node.extra["query"]["allowInsecure"], "1")

    def test_node_class_and_defaults(self):
        node = self.import_one(SS_YAML)
        self.assertIsInstance(node, Node)
        self.assertEqual(node.source, "manual")          # D1：归手动导入页
        self.assertEqual(sorted(node.scenes), [])
        self.assertFalse(node.pinned and node.banned)
        self.assertIsNone(node.metrics.latency_ms)       # 不测速，None 不是 0

    def test_id_matches_make_node_id(self):
        node = self.import_one(VLESS_YAML)
        self.assertEqual(node.id, make_node_id(
            "manual", "vless", "vl.example.com", 443,
            "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"))
        wg = self.import_one(WG_YAML)
        self.assertEqual(wg.id, make_node_id(
            "manual", "wireguard", "wg.example.com", 51820,
            "fake-peer-pubkey-b64="))
        ss = self.import_one(SS_YAML)
        self.assertEqual(ss.id, make_node_id(
            "manual", "ss", "203.0.113.10", 8388))       # ss 无混淆，同 manual

    def test_config_ref_roundtrip_full_fidelity(self):
        """config_ref = 原条目 YAML 文本；safe_load 回来与原条目逐字段相等。"""
        result = clash_import.import_clash_text(FULL_YAML)
        entries = load_yaml(FULL_YAML)["proxies"]
        self.assertEqual(len(result.nodes), 7)
        for node, entry in zip(result.nodes, entries):
            self.assertEqual(node.extra["config_format"], "clash_yaml")
            self.assertEqual(yaml.safe_load(node.config_ref), entry)
            self.assertEqual(node.extra["clash"], entry)  # dict 保真零丢弃

    def test_to_json_roundtrip_with_date_field(self):
        """YAML 里的裸日期（date 类型）必须被安全化，to_json 不炸。"""
        text = (SS_YAML.rstrip("\n") + "\n    created: 2026-01-02\n"
                "    blob: !!binary aGVsbG8=\n")
        node = self.import_one(text)
        clone = Node.from_json(node.to_json())           # 不抛即通过
        self.assertEqual(clone.extra["clash"]["created"], "2026-01-02")
        self.assertEqual(clone.extra["method"], "aes-256-gcm")


class TestBatchBehavior(unittest.TestCase):
    """批量行为：跳过计数、去重、溯源、空配置、坏 YAML、不导入分组。"""

    def test_skipped_and_unsupported_counted(self):
        doc = compose(SS_YAML)
        entries = load_yaml(doc)["proxies"] + [
            {"name": "bad-trojan", "type": "trojan",
             "server": "tj2.example.com", "port": 443},        # 缺 password
            {"name": "bad-port", "type": "ss",
             "server": "203.0.113.11", "port": "not-a-port",
             "cipher": "aes-128-gcm", "password": "x"},
            {"name": "socks-entry", "type": "socks5",
             "server": "203.0.113.12", "port": 1080},
            {"name": "ssr-entry", "type": "ssr",
             "server": "203.0.113.13", "port": 7000},
        ]
        result = clash_import.import_clash_text(
            yaml.safe_dump({"proxies": entries}, allow_unicode=True,
                           sort_keys=False))
        self.assertEqual(len(result.nodes), 1)
        self.assertEqual(result.total, 5)
        self.assertEqual(result.skipped, 2)
        self.assertEqual(result.skipped_types, {"socks5": 1, "ssr": 1})
        self.assertTrue(result.skipped_reasons)

    def test_dedupe_by_id_first_wins(self):
        text = compose(SS_YAML, SS_YAML)
        result = clash_import.import_clash_text(text)
        self.assertEqual(len(result.nodes), 1)
        self.assertEqual(result.total, 2)

    def test_subscription_url_and_name_propagated(self):
        result = clash_import.import_clash_text(
            SS_YAML, subscription_url="https://sub.example.com/fake",
            subscription_name="测试订阅")
        node = result.nodes[0]
        self.assertEqual(node.extra["subscription_url"],
                         "https://sub.example.com/fake")
        self.assertEqual(node.extra["subscription_name"], "测试订阅")

    def test_empty_and_non_clash_yaml(self):
        for text in ("", "   \n", "just-a-string",
                     "mixed: [1, 2]", "proxies: []",
                     "port: 7890\nmode: rule"):
            result = clash_import.import_clash_text(text)
            self.assertEqual(len(result.nodes), 0, repr(text))
            self.assertEqual(result.total, 0, repr(text))

    def test_broken_yaml_raises_valueerror(self):
        with self.assertRaises(ValueError):
            clash_import.import_clash_text("proxies: [ {type: ss, ")

    def test_proxy_groups_not_imported(self):
        """url-test / fallback / select 分组一律不导入——本项目严禁
        自动测速切换逻辑，直导入只认 proxies 条目。"""
        text = (SS_YAML.rstrip("\n") + "\nproxy-groups:\n"
                "  - name: auto\n"
                "    type: url-test\n"
                "    proxies: [ss-obfs-sample]\n"
                "    url: http://www.gstatic.com/generate_204\n"
                "  - name: pick\n"
                "    type: select\n"
                "    proxies: [auto, ss-obfs-sample]\n")
        result = clash_import.import_clash_text(text)
        self.assertEqual(len(result.nodes), 1)
        self.assertEqual({n.protocol for n in result.nodes}, {"ss"})
        self.assertNotIn("urltest", {n.protocol for n in result.nodes})

    def test_file_entry_point(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sample.yaml")
            with open(path, "w", encoding="utf-8") as f:
                f.write(FULL_YAML)
            by_file = clash_import.import_clash_file(path)
            by_text = clash_import.import_clash_text(FULL_YAML)
        self.assertEqual([n.id for n in by_file.nodes],
                         [n.id for n in by_text.nodes])
        self.assertEqual(by_file.total, 7)
        self.assertEqual(by_file.skipped, 0)


if __name__ == "__main__":
    unittest.main()
