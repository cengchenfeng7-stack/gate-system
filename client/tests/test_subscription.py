#!/usr/bin/env python3
"""subscription 离线测试：base64/明文订阅解析、坏行计数、批内去重。
按任务书红线：只测 decode/parse/load 纯本地函数，fetch/import 不碰。"""
import base64
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.sources import manual as manual_src
from client.sources import subscription as sub_src


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# 假域名/假 uuid fixture（任务书要求：不许出现真实订阅地址）
VLESS_LINK = ("vless://11111111-2222-3333-4444-555555555555"
              "@example.com:443?security=tls#test1")
SS_LINK = "ss://aes-256-gcm:secret-pw@1.2.3.4:8388#ssnode"
TROJAN_LINK = "trojan://pw123456@t.example.org:443?sni=t.example.org#tj1"
THREE_LINKS = VLESS_LINK + "\n" + SS_LINK + "\n" + TROJAN_LINK + "\n"
# 结尾添 ">"：使整段 base64 含 "+"（标准字母表），便于换成 urlsafe 字母表测容错
THREE_LINKS_ANGLE = THREE_LINKS + ">"
FAKE_SUB_URL = "https://sub.example.com/fake/sub"
FAKE_SUB_NAME = "测试订阅"


def broken_b64(text: str, chunk: int = 48) -> str:
    """缺 padding、中间夹换行的 base64。"""
    enc = b64(text).rstrip("=")
    return "\n".join(enc[i:i + chunk] for i in range(0, len(enc), chunk))


class TestDecodeSubscriptionText(unittest.TestCase):
    def test_plain_links_returned_as_is(self):
        out, fmt = sub_src.decode_subscription_text(THREE_LINKS)
        self.assertEqual(fmt, "plain")
        self.assertEqual(out, THREE_LINKS)

    def test_base64_roundtrip(self):
        out, fmt = sub_src.decode_subscription_text(b64(THREE_LINKS))
        self.assertEqual(fmt, "base64")
        self.assertEqual(out, THREE_LINKS)

    def test_base64_missing_padding_and_newlines(self):
        enc = b64(THREE_LINKS).rstrip("=")
        self.assertNotEqual(len(enc) % 4, 0)   # fixture 确实丢了 padding
        out, fmt = sub_src.decode_subscription_text(broken_b64(THREE_LINKS))
        self.assertEqual(fmt, "base64")
        self.assertEqual(out, THREE_LINKS)

    def test_base64_urlsafe_alphabet(self):
        enc = b64(THREE_LINKS_ANGLE)           # "+" 在标准字母表里
        self.assertIn("+", enc)
        urlsafe = enc.replace("+", "-").replace("/", "_").rstrip("=")
        out, fmt = sub_src.decode_subscription_text(urlsafe)
        self.assertEqual(fmt, "base64")
        self.assertEqual(out, THREE_LINKS_ANGLE)

    def test_base64_without_link_scheme_falls_back_to_plain(self):
        enc = b64("hello world, nothing linky here")
        out, fmt = sub_src.decode_subscription_text(enc)
        self.assertEqual((out, fmt), (enc, "plain"))

    def test_whitespace_inside_base64(self):
        enc = b64(THREE_LINKS)
        spaced = "  ".join(enc[i:i + 40] for i in range(0, len(enc), 40))
        out, fmt = sub_src.decode_subscription_text(spaced)
        self.assertEqual((fmt, out), ("base64", THREE_LINKS))


class TestParseSubscriptionText(unittest.TestCase):
    def test_base64_input_three_nodes(self):
        res = sub_src.parse_subscription_text(b64(THREE_LINKS))
        self.assertEqual(res.source_format, "base64")
        self.assertEqual(len(res.nodes), 3)
        self.assertEqual({n.protocol for n in res.nodes}, {"vless", "ss", "trojan"})
        self.assertTrue(all(n.source == "manual" for n in res.nodes))
        self.assertEqual(res.skipped, 0)

    def test_plain_input_three_nodes(self):
        res = sub_src.parse_subscription_text(THREE_LINKS)
        self.assertEqual(res.source_format, "plain")
        self.assertEqual(len(res.nodes), 3)
        self.assertEqual(res.nodes[0].id, manual_src.parse_link(VLESS_LINK).id)

    def test_bad_line_skipped(self):
        res = sub_src.parse_subscription_text(THREE_LINKS + "not-a-link://x\n")
        self.assertEqual(len(res.nodes), 3)    # 节点数不变
        self.assertEqual(res.skipped, 1)       # 坏行计数，不抛异常

    def test_duplicate_link_dedup_and_stable_id(self):
        n1 = manual_src.parse_link(VLESS_LINK)
        n2 = manual_src.parse_link(VLESS_LINK)
        self.assertEqual(n1.id, n2.id)         # ID 稳定：同链接两次 parse 完全一致
        res = sub_src.parse_subscription_text(
            VLESS_LINK + "\n" + SS_LINK + "\n" + VLESS_LINK + "\n")
        self.assertEqual(len(res.nodes), 2)    # 批内去重：只留一个
        self.assertEqual([n.id for n in res.nodes], [n1.id,
                        manual_src.parse_link(SS_LINK).id])   # 先出现者胜，原顺序

    def test_subscription_meta_written_to_extra(self):
        res = sub_src.parse_subscription_text(THREE_LINKS,
                                              subscription_url=FAKE_SUB_URL,
                                              subscription_name=FAKE_SUB_NAME)
        self.assertEqual(len(res.nodes), 3)
        for n in res.nodes:
            self.assertEqual(n.extra["subscription_url"], FAKE_SUB_URL)
            self.assertEqual(n.extra["subscription_name"], FAKE_SUB_NAME)

    def test_meta_keys_absent_when_not_given(self):
        res = sub_src.parse_subscription_text(THREE_LINKS)
        for n in res.nodes:
            self.assertNotIn("subscription_url", n.extra)
            self.assertNotIn("subscription_name", n.extra)

    def test_broken_base64_still_parses(self):
        res = sub_src.parse_subscription_text(broken_b64(THREE_LINKS))
        self.assertEqual(res.source_format, "base64")
        self.assertEqual(len(res.nodes), 3)

    def test_empty_and_comment_only(self):
        for text in ("", "\n\n   \n"):
            res = sub_src.parse_subscription_text(text)
            self.assertEqual(len(res.nodes), 0)
            self.assertEqual(res.skipped, 0)
            self.assertEqual(res.source_format, "plain")
        res = sub_src.parse_subscription_text("# 注释行一\n# another comment\n\n")
        self.assertEqual(len(res.nodes), 0)
        self.assertEqual(res.skipped, 0)       # 注释行不计 skipped

    def test_singbox_json_gives_empty_not_exception(self):
        j = ('{\n'
             '  "log": {"level": "info"},\n'
             '  "outbounds": [{"type": "vless", "server": "203.0.113.9", '
             '"server_port": 443}]\n'
             '}\n')
        res = sub_src.parse_subscription_text(j)
        self.assertEqual(res.nodes, [])        # 暂不支持 → 空列表，不抛异常
        self.assertEqual(res.source_format, "plain")


class TestLoadSubscriptionFile(unittest.TestCase):
    def _write_temp(self, content: str) -> str:
        fd, path = tempfile.mkstemp(suffix=".txt", prefix="sub_test_")
        os.close(fd)
        self.addCleanup(os.remove, path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def test_load_base64_file_with_kwargs(self):
        path = self._write_temp(b64(THREE_LINKS))
        res = sub_src.load_subscription_file(path,
                                             subscription_url=FAKE_SUB_URL,
                                             subscription_name=FAKE_SUB_NAME)
        self.assertEqual(res.source_format, "base64")
        self.assertEqual(len(res.nodes), 3)
        self.assertEqual(res.nodes[0].extra["subscription_name"], FAKE_SUB_NAME)

    def test_load_plain_file(self):
        path = self._write_temp(THREE_LINKS)
        res = sub_src.load_subscription_file(path)
        self.assertEqual((res.source_format, len(res.nodes)), ("plain", 3))
        self.assertEqual(res.nodes[0].source, "manual")


if __name__ == "__main__":
    unittest.main()


CLASH_YAML = """\
proxies:
  - name: clash-vless
    type: vless
    server: example.com
    port: 443
    uuid: 11111111-2222-3333-4444-555555555555
    tls: true
  - name: clash-hy2
    type: hysteria2
    server: hy2.example.com
    port: 443
    password: fake-hy2-pw
"""


class TestClashYamlRoute(unittest.TestCase):
    """Clash YAML 经 parse_subscription_text 自动转直导入（阶段二）。"""

    def test_clash_yaml_routed(self):
        res = sub_src.parse_subscription_text(CLASH_YAML)
        self.assertEqual(res.source_format, "clash-yaml")
        self.assertEqual(len(res.nodes), 2)
        self.assertEqual(res.skipped, 0)
        protos = {n.protocol for n in res.nodes}
        self.assertEqual(protos, {"vless", "hysteria2"})

    def test_non_clash_yaml_falls_through(self):
        res = sub_src.parse_subscription_text("foo: bar\n")
        self.assertEqual(res.source_format, "plain")
        self.assertEqual(res.nodes, [])

    def test_plain_links_unaffected(self):
        res = sub_src.parse_subscription_text(THREE_LINKS)
        self.assertEqual(res.source_format, "plain")
        self.assertEqual(len(res.nodes), 3)
