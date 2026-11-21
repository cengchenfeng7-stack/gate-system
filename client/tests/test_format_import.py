#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""format_import 离线测试：四格式识别、五类容错（混合 / BOM / CRLF /
夹注释行 / 部分坏行）、报错与计数口径、task1 衔接。
按任务书红线：全部自构造样本（example.com / 假 uuid / 假密钥 / RFC 5737
文档段），不读真实订阅文件、不联网、不碰真实节点、无自动选择逻辑。"""
import base64
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))

from client.sources import format_import as fi  # noqa: E402


# ---------------- 自构造样本（全部假数据） ----------------

VLESS = ("vless://11111111-2222-3333-4444-555555555555"
         "@a.example.com:443?sni=b.example.com&security=tls#节点一")
TROJAN = "trojan://fake-pw@c.example.com:443#节点二"
SS = "ss://aes-256-gcm:fake-ss-pw@e.example.com:8388#节点三"
SSTP = "sstp://user1:fakepw@f.example.com:443#节点四"
LINKS = "\n".join([VLESS, TROJAN, SS, SSTP])
LINK_NAMES = ("节点一", "节点二", "节点三", "节点四")

# 无自动测速/自动选择的极简 Clash 样本：2 条可导入 + 1 坏条目 + 1 不支持协议
CLASH_YAML = """\
port: 7890
proxies:
  - name: vless-sample
    type: vless
    server: 203.0.113.10
    port: 443
    uuid: 11111111-2222-3333-4444-555555555555
    tls: true
  - name: ss-sample
    type: ss
    server: 203.0.113.11
    port: 8388
    cipher: aes-256-gcm
    password: fake-ss-pw
  - name: socks5-unsupported
    type: socks5
    server: 203.0.113.12
    port: 1080
  - name: bad-entry
    type: trojan
    server: ""
    port: 443
    password: fake-pw
"""


def b64_wrap(text: str, width: int = 64, eol: str = "\n") -> str:
    """把链接文本编成换行包装的整段 base64 订阅（模拟服务端真实排版）。"""
    raw = base64.b64encode(text.encode("utf-8")).decode("ascii")
    return eol.join(raw[i:i + width] for i in range(0, len(raw), width))


BLOB4 = b64_wrap(LINKS)          # 四条链接的整段订阅，\n 换行包装
BLOB2 = b64_wrap("\n".join([VLESS, TROJAN]))


def names(result):
    return tuple(n.name for n in result.nodes)


# ---------------- 识别：detect_format ----------------

class DetectTests(unittest.TestCase):
    def test_base64_wrapped(self):
        self.assertEqual(fi.detect_format(BLOB4), "base64")

    def test_plain_list(self):
        self.assertEqual(fi.detect_format(LINKS), "plain")

    def test_clash_yaml(self):
        self.assertEqual(fi.detect_format(CLASH_YAML), "clash_yaml")

    def test_mixed_b64_and_plain(self):
        self.assertEqual(fi.detect_format(BLOB2 + "\n" + SS), "mixed")

    def test_unknown_and_empty(self):
        self.assertEqual(fi.detect_format("随便写点说明文字，不是节点。"), "unknown")
        self.assertEqual(fi.detect_format(""), "unknown")
        self.assertEqual(fi.detect_format("# 只有注释\n"), "unknown")

    def test_clash_sig_no_prefix_false_positive(self):
        # proxy-providers:/proxy-groups: 不是签名（regex 锚定 proxies 接冒号）
        self.assertEqual(fi.detect_format("proxy-providers:\n  sub1:\n    url: x"),
                         "unknown")
        # 链接行即使行中出现 proxies: 字样也不触发（签名锚定行首）
        self.assertEqual(fi.detect_format("ss://aes-256-gcm:pw@e.example.com:8388"
                                          "?x=proxies:1#n"), "plain")


# ---------------- 四格式导入 ----------------

class ImportFormatTests(unittest.TestCase):
    def test_import_base64(self):
        r = fi.import_any_text(BLOB4)
        self.assertEqual(fi.detect_format(BLOB4), "base64")
        self.assertEqual(names(r), LINK_NAMES)
        self.assertEqual(r.format, "base64")
        self.assertEqual(r.skipped, 0)
        self.assertEqual(r.total, len(BLOB4.splitlines()))  # 实质行 = 包装行数
        n1 = r.nodes[0]
        self.assertEqual((n1.protocol, n1.source, n1.host, n1.port),
                         ("vless", "manual", "a.example.com", 443))
        self.assertEqual(n1.extra["uuid"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(n1.extra["query"]["sni"], "b.example.com")

    def test_import_plain_list(self):
        r = fi.import_any_text(LINKS)
        self.assertEqual(names(r), LINK_NAMES)
        self.assertEqual((r.format, r.skipped, r.total), ("plain", 0, 4))
        trojan = r.nodes[1]
        self.assertEqual(trojan.extra["password"], "fake-pw")
        ss = r.nodes[2]
        self.assertEqual((ss.extra["method"], ss.extra["password"]),
                         ("aes-256-gcm", "fake-ss-pw"))
        sstp = r.nodes[3]
        self.assertEqual(sstp.extra["username"], "user1")

    def test_import_txt_file_with_dollar_lines(self):
        # nodes_*.txt 惯例：入口#标签$链接（manual.parse_links_text 同款），CRLF + 注释
        txt = ("\r\n".join([
            "入口A#香港-01$sstp://user1:fakepw@g.example.com:443#s1",
            "# 每日 06:00 更新",
            "入口B#日本-02$trojan://fake-pw@h.example.com:443#s2",
            "",
        ]) + "\r\n")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "nodes_20260101.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(txt)
            r = fi.import_any_file(path)
        self.assertEqual(names(r), ("s1", "s2"))
        self.assertEqual((r.format, r.skipped, r.total), ("plain", 0, 2))
        self.assertEqual(r.nodes[0].protocol, "sstp")
        self.assertEqual(r.nodes[1].extra["password"], "fake-pw")

    def test_import_clash_yaml(self):
        r = fi.import_any_text(CLASH_YAML)
        self.assertEqual((r.format, r.total), ("clash_yaml", 4))
        self.assertEqual(len(r.nodes), 2)
        self.assertEqual([n.protocol for n in r.nodes], ["vless", "ss"])
        self.assertEqual(r.skipped, 1)                      # bad-entry 缺 server
        self.assertEqual(r.skipped_types, {"socks5": 1})    # 不支持协议计数透传
        self.assertTrue(r.skipped_reasons)

    def test_clash_invalid_yaml_raises(self):
        with self.assertRaises(ValueError):
            fi.import_any_text("proxies:\n  - [断裂的 yaml")


# ---------------- 容错五类 ----------------

class ToleranceTests(unittest.TestCase):
    def test_f1_mixed_b64_and_plain(self):
        # 旧行为（实测）：blob 整块被当坏行丢掉。现在 b64 组解出 + 明文行照常导入
        text = BLOB2 + "\n" + SS + "\n这是说明文字，不是节点。"
        r = fi.import_any_text(text)
        self.assertEqual(r.format, "mixed")
        self.assertEqual(names(r), ("节点一", "节点二", "节点三"))
        self.assertEqual(r.skipped, 1)                       # 只有说明行
        self.assertEqual(r.skipped_reasons, {"无法识别的行": 1})

    def test_f2_comment_header_before_b64(self):
        # 旧行为（实测）：注释头的 # 污染字符集判定 → 整段订阅全丢
        text = "# 每日 06:00 更新\n订阅到期请及时刷新\n" + BLOB2
        r = fi.import_any_text(text)
        self.assertEqual(r.format, "base64")
        self.assertEqual(names(r), ("节点一", "节点二"))
        self.assertEqual(r.skipped, 1)                       # 无 # 的说明行计入
        self.assertEqual(r.skipped_reasons, {"无法识别的行": 1})

    def test_f3_dollar_lines_in_text_entry(self):
        r = fi.import_any_text("入口A#香港-01$sstp://user1:fakepw@g.example.com:443#s1")
        self.assertEqual(names(r), ("s1",))
        self.assertEqual(r.skipped, 0)

    def test_dollar_guard_keeps_credentials(self):
        # $ 归一化带 :// 守卫：凭证里恰好含 $ 的链接不被误切
        # （老惯例 manual.parse_links_text 对这种行会切坏并丢弃）
        r = fi.import_any_text("trojan://pa$ss@c.example.com:443#带美元密码")
        self.assertEqual(len(r.nodes), 1)
        self.assertEqual(r.nodes[0].extra["password"], "pa$ss")

    def test_bom(self):
        self.assertEqual(names(fi.import_any_text("\ufeff" + LINKS)), LINK_NAMES)
        self.assertEqual(names(fi.import_any_text("\ufeff" + BLOB4)), LINK_NAMES)

    def test_crlf(self):
        self.assertEqual(names(fi.import_any_text(LINKS.replace("\n", "\r\n"))),
                         LINK_NAMES)
        self.assertEqual(names(fi.import_any_text(b64_wrap(LINKS, eol="\r\n"))),
                         LINK_NAMES)

    def test_comment_lines_between_links(self):
        text = "# 列表说明\n" + "\n# ---\n".join(LINKS.split("\n")) + "\n# 尾注\n"
        r = fi.import_any_text(text)
        self.assertEqual((names(r), r.skipped, r.total), (LINK_NAMES, 0, 4))

    def test_partial_bad_lines_counted(self):
        text = "\n".join([
            VLESS, TROJAN, SS,
            "垃圾行，带中文标点。",                            # other 行
            "hysteria2://fake-pw@i.example.com:443#h2",        # 暂不支持协议
            "https://sub.example.com/token",                   # 疑似订阅地址
        ])
        r = fi.import_any_text(text)
        self.assertEqual((r.format, r.total), ("plain", 6))
        self.assertEqual(len(r.nodes), 3)
        self.assertEqual(r.skipped, 3)
        self.assertEqual(r.skipped_reasons, {
            "无法识别的行": 1,
            "暂不支持的链接类型: hysteria2://": 1,
            "http(s) 行是订阅地址不是节点链接"
            "（本入口只解析内容；拉取订阅走 import_subscription_url）": 1,
        })

    def test_b64_subscription_with_inner_comments(self):
        # 解码后文本里的注释/空行同样静默跳过，不计坏行
        blob = b64_wrap("# 说明\n" + VLESS + "\n\n" + TROJAN)
        r = fi.import_any_text(blob)
        self.assertEqual(names(r), ("节点一", "节点二"))
        self.assertEqual((r.format, r.skipped), ("base64", 0))


# ---------------- 报错口径：识别不了显式 ValueError ----------------

class ErrorTests(unittest.TestCase):
    def test_empty_and_comments_only(self):
        for bad in ("", "   \n  ", "# 只有注释\n"):
            with self.assertRaisesRegex(ValueError, "导入内容"):
                fi.import_any_text(bad)

    def test_unrecognized_junk_raises(self):
        with self.assertRaises(ValueError) as cm:
            fi.import_any_text("随便一段说明\n第二行也不是节点")
        self.assertIn("无法识别导入内容", str(cm.exception))

    def test_json_gets_singbox_hint(self):
        with self.assertRaises(ValueError) as cm:
            fi.import_any_text('{"outbounds":[{"type":"vless","server":"x"}]}')
        self.assertIn("sing-box", str(cm.exception))

    def test_utf16_like_gets_encoding_hint(self):
        with self.assertRaises(ValueError) as cm:
            fi.import_any_text("v\x00e\x00s\x00s\x00")   # UTF-16 误读的形状
        self.assertIn("UTF-8", str(cm.exception))

    def test_b64_only_but_not_a_link_subscription(self):
        # 解码成功但不含 ://（如 base64 的普通文本/JSON）
        with self.assertRaises(ValueError) as cm:
            fi.import_any_text(base64.b64encode(b"hello world").decode())
        self.assertIn("base64", str(cm.exception))
        # 截断残缺的 blob（解码失败）
        with self.assertRaises(ValueError):
            fi.import_any_text(BLOB4.split("\n")[0][:-3])

    def test_b64_group_failure_in_mixed_is_counted_not_fatal(self):
        # 混合输入里 b64 组报废：计数跳过，明文行照常导入（不整单报错）
        text = base64.b64encode(b"hello world").decode() + "\n" + VLESS
        r = fi.import_any_text(text)
        self.assertEqual(r.format, "mixed")
        self.assertEqual(names(r), ("节点一",))
        self.assertEqual(r.skipped, 1)
        self.assertEqual(r.skipped_reasons,
                         {"base64 解码失败或解码后不含链接": 1})


# ---------------- 去重 / 溯源 / 文件入口 ----------------

class BehaviorTests(unittest.TestCase):
    def test_dedup_and_subscription_metadata(self):
        text = VLESS + "\n" + VLESS + "\n" + TROJAN
        r = fi.import_any_text(text, subscription_url="https://sub.example.com/s",
                               subscription_name="测试订阅")
        self.assertEqual(names(r), ("节点一", "节点二"))   # 先出现者胜
        n = r.nodes[0]
        self.assertEqual(n.extra["subscription_url"], "https://sub.example.com/s")
        self.assertEqual(n.extra["subscription_name"], "测试订阅")

    def test_file_entry_equals_text_entry(self):
        content = "\ufeff" + BLOB2.replace("\n", "\r\n") + "\r\n"
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "sub.txt")
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            r_file = fi.import_any_file(path, subscription_name="文件订阅")
        r_text = fi.import_any_text(content, subscription_name="文件订阅")
        self.assertEqual(names(r_file), names(r_text))
        self.assertEqual((r_file.format, r_file.skipped),
                         (r_text.format, r_text.skipped))
        self.assertEqual(len(r_file.nodes), 2)

    def test_plain_counts_bad_lines_where_parse_links_text_silent(self):
        # 对照：manual.parse_links_text 对坏行静默；统一入口必须计数
        r = fi.import_any_text(VLESS + "\n坏行，不是链接。")
        self.assertEqual((len(r.nodes), r.skipped), (1, 1))


# ---------------- task1 衔接：Clash 依赖加载 ----------------

class ClashDependencyTests(unittest.TestCase):
    def test_get_clash_importer_loads(self):
        importer = fi._get_clash_importer()
        self.assertTrue(callable(importer))
        self.assertEqual(importer.__name__, "import_clash_text")

    def test_dependency_failure_is_explicit_not_silent(self):
        saved = (fi._CLASH_IMPORTER, fi._CLASH_IMPORT_ERROR)
        fi._CLASH_IMPORTER = None
        fi._CLASH_IMPORT_ERROR = ImportError("加载失败（模拟）")
        try:
            with self.assertRaisesRegex(ImportError, "加载失败（模拟）"):
                fi.import_any_text(CLASH_YAML)
        finally:
            fi._CLASH_IMPORTER, fi._CLASH_IMPORT_ERROR = saved


if __name__ == "__main__":
    unittest.main()
