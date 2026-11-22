#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打包自检 IP 白名单守卫测试（test_whitelist.py）—— task15 跟进修复
====================================================================
REVIEW_FAIL.md（2026-11-22）裁决·方案 A 的守卫侧验证：work15/build_deb.py
（client-dev/packaging/build_deb.py 的修复副本）的 `_IP_WHITELIST` 由
逐个枚举改为「RFC 5737 三段文档地址按类放行 + 非文档段逐条豁免」。

本测试断言两件事，缺一不可：
1. **按类放行**：三个文档段（192.0.2.0/24、198.51.100.0/24、203.0.113.0/24）
   内任何地址都放行——包括撞墙实录里的 203.0.113.7 / 198.51.100.7，
   以及旧白名单从未枚举过的新文档地址（以后不再撞墙）；
2. **防线本意不变**：白名单之外的**一切真实 IP 仍一律拦截**——真实公网
   地址、文档段紧邻段、越界段、解析不出的形态全部判白名单外；既有
   逐条豁免（DNS 预设/回环/TUN 网段/UA 版本号误配）不放大。

两层消费点同扫（deb_selfcheck 第 5 项与 test_packaging 独立复扫的
写法 `m.group(0) not in build_deb._IP_WHITELIST`，读同一份对象）：
模拟 staging 树里放 task15 撞墙原件内容 → 零命中；植入真实 IP /
订阅字样 → 命中。纯标准库、不 import client 包、全程离线。
"""

from __future__ import annotations

import os
import re
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "..", "packaging"))
import build_deb  # noqa: E402  packaging/build_deb.py，标准库 only

# REVIEW_FAIL 实录的撞墙地址（task15 假数据默认值，RFC 5737 文档段）
COLLISION_IPS = ("203.0.113.7", "198.51.100.7")

# 三个文档段的代表值：网络/广播边界、旧白名单从未枚举过的新地址
DOC_RANGE_ALLOWED = (
    # TEST-NET-1（旧白名单只枚举过 203.0.113.45 一个文档段值）
    "192.0.2.0", "192.0.2.9", "192.0.2.45", "192.0.2.128", "192.0.2.255",
    # TEST-NET-2
    "198.51.100.0", "198.51.100.7", "198.51.100.100", "198.51.100.255",
    # TEST-NET-3
    "203.0.113.0", "203.0.113.7", "203.0.113.45", "203.0.113.255",
)

# 既有逐条豁免（非文档段；不按类放开——放开=防线放水）
EXPLICIT_ALLOWED = (
    "0.0.0.0", "127.0.0.1", "1.1.1.1", "8.8.8.8", "9.9.9.9",
    "172.19.0.0", "172.19.0.1", "172.19.0.5",
    "126.0.0.0", "140.0.0.0",          # Chrome UA 版本号误配，不是地址
)

# 一切真实 IP 必须拦：公网各段采样 + 文档段紧邻段 + 边界越界 + 解析不出
REAL_IPS_BLOCKED = (
    # 真实公网地址采样（示例值，非节点数据）
    "1.2.3.4", "8.8.4.4", "93.184.216.34", "104.16.132.229",
    "151.101.1.140", "223.5.5.5", "36.152.44.96",
    # 文档段紧邻段（差一点就是文档段的都不是文档段）
    "192.0.1.255", "192.0.3.0", "192.0.3.1",
    "198.51.99.255", "198.51.101.0", "198.51.101.7",
    "203.0.112.255", "203.0.114.0", "203.0.114.7",
    # 解析不出 / 非 IPv4：一律不放行（不猜）
    "not-an-ip", "999.999.999.999", "::1", "2001:db8::1", "",
)


class TestDocRangesClassAllowed(unittest.TestCase):
    """方案 A 核心：文档段按类放行，新文档地址不再撞墙。"""

    def test_collision_ips_now_allowed(self):
        """撞墙实录的两个地址（task15 假数据默认值）必须放行。"""
        for ip in COLLISION_IPS:
            self.assertTrue(build_deb.ip_allowed(ip), ip)
            self.assertIn(ip, build_deb._IP_WHITELIST, ip)

    def test_whole_doc_ranges_allowed(self):
        """三段 /24 全段放行（含网络/广播地址与旧白名单没枚举过的值）。"""
        for ip in DOC_RANGE_ALLOWED:
            self.assertTrue(build_deb.ip_allowed(ip), ip)
            self.assertIn(ip, build_deb._IP_WHITELIST, ip)

    def test_explicit_defaults_still_allowed(self):
        """既有逐条豁免保留（DNS 预设/回环/TUN 网段/UA 版本号误配）。"""
        for ip in EXPLICIT_ALLOWED:
            self.assertTrue(build_deb.ip_allowed(ip), ip)

    def test_consumer_expression_unchanged(self):
        """两个消费点的写法 `m.group(0) not in _IP_WHITELIST` 直接可用
        （读同一份对象，白名单改一处即同过）。白名单成员=放行：
        文档段 `in` 为真（消费点不报），真实 IP `not in` 为真（报）。"""
        for ip in COLLISION_IPS + DOC_RANGE_ALLOWED[:3]:
            self.assertIn(ip, build_deb._IP_WHITELIST, ip)
            self.assertFalse(ip not in build_deb._IP_WHITELIST, ip)
        self.assertTrue("93.184.216.34" not in build_deb._IP_WHITELIST)


class TestRealIpsStillBlocked(unittest.TestCase):
    """防线本意不变：白名单外一切真实 IP 一律拦截。"""

    def test_real_public_ips_blocked(self):
        for ip in REAL_IPS_BLOCKED:
            self.assertFalse(build_deb.ip_allowed(ip), f"必须拦: {ip}")
            self.assertNotIn(ip, build_deb._IP_WHITELIST,
                             f"消费点写法必须判白名单外: {ip}")

    def test_doc_range_boundary_exact(self):
        """边界逐值对账：/24 的最后一字节 0-255 只在段内放行。"""
        self.assertTrue(build_deb.ip_allowed("192.0.2.0"))
        self.assertTrue(build_deb.ip_allowed("192.0.2.255"))
        self.assertFalse(build_deb.ip_allowed("192.0.1.255"))
        self.assertFalse(build_deb.ip_allowed("192.0.3.0"))

    def test_ipv6_never_allowed(self):
        """IPv6 不放行（IP 正则只扫点分 IPv4；真混入 v6 也宁可误拦）。"""
        for ip in ("::1", "2001:db8::1", "fe80::1"):
            self.assertFalse(build_deb.ip_allowed(ip), ip)


class TestScanLayersSimulated(unittest.TestCase):
    """两层消费点同扫：模拟 staging 树——task15 撞墙内容零命中，
    植入真实 IP/订阅字样必须命中（守卫仍拦真实 IP）。"""

    # task15 撞墙原件的行内容（REVIEW_FAIL 实录）+ 既有豁免来源行
    TASK15_LINES = (
        '    exit_ip="203.0.113.7"):',                 # faults.py 假 probe 默认
        'host="198.51.100.7", port=443,',              # faults.py make_node 默认
        'soak_db.upsert_node(faults.make_node(node_id, "fake-proto",',
        '        probe = faults.FakeProbe(behavior="normal")',
        'dns_servers = ["1.1.1.1", "8.8.8.8", "9.9.9.9"]',
        '"(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")',
        '"(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")',
        '"gateway": "172.19.0.1", "address": "172.19.0.5/30",',
        # 真源码形态是 f-string（大括号断开载荷，_SCHEME_RE 不命中——
        # build_deb.py 注释口径：格式示例落不进链接正则）
        'proxy = f"socks5://127.0.0.1:{port}"',
    )

    def _scan(self, files: dict) -> list:
        """复刻两层消费点的扫描写法（同一正则、同一 in 判断）。"""
        hits = []
        with tempfile.TemporaryDirectory(prefix="work15-wl-") as td:
            for name, text in files.items():
                with open(os.path.join(td, name), "w",
                          encoding="utf-8") as f:
                    f.write(text)
            for name in sorted(files):
                path = os.path.join(td, name)
                with open(path, encoding="utf-8", errors="replace") as f:
                    text = f.read()
                for m in build_deb._SCHEME_RE.finditer(text):
                    hits.append(f"{name}: 链接字样 {m.group(0)[:48]!r}")
                for m in build_deb._IP_RE.finditer(text):
                    if m.group(0) not in build_deb._IP_WHITELIST:
                        hits.append(f"{name}: 白名单外 IP {m.group(0)}")
                for marker in build_deb._SECRET_MARKERS:
                    if marker in text:
                        hits.append(f"{name}: 密钥字样 {marker!r}")
        return hits

    def test_task15_content_zero_hits(self):
        """撞墙原件内容（文档段假数据 + 既有豁免）→ 两层扫描零命中。"""
        text = "\n".join(self.TASK15_LINES) + "\n"
        self.assertEqual(self._scan({"faults.py": text, "soak.py": text}), [])

    def test_planted_real_ip_is_caught(self):
        """守卫必须拦真实 IP：植入公网地址 → 两层扫描都报白名单外。"""
        base = "\n".join(self.TASK15_LINES) + "\n"
        for real_ip in ("93.184.216.34", "223.5.5.5", "1.2.3.4"):
            hits = self._scan({"node.py": base + f'server = "{real_ip}"\n'})
            self.assertEqual(len(hits), 1, f"{real_ip}: {hits}")
            self.assertIn("白名单外 IP", hits[0])
            self.assertIn(real_ip, hits[0])

    def test_planted_subscription_string_is_caught(self):
        """守卫必须拦订阅链接字样（scheme + ≥12 字符真载荷）。"""
        hits = self._scan({"sub.py": "ref = 'vmess://eyJhZGQiOiIxOTIuMC4yLj"
                                       "EwIiwiaWQiOiJhIn0='\n"})
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("链接字样", hits[0])

    def test_doc_range_node_string_not_flagged_by_ip_scan(self):
        """文档段地址即使写成『像节点服务器』的形态也不触发 IP 命中
        （文档段永不可能是真实节点——方案 A 的口径本身）。"""
        hits = self._scan({"ok.py": 'server = "203.0.113.7"\n'
                                    'port = 443\n'})
        self.assertEqual(hits, [])

    def test_secret_marker_still_caught(self):
        """密钥字样防线原样有效（本次只动 IP 白名单，其他不放宽）。"""
        # 只含一个标记形态的样本（"BEGIN RSA PRIVATE KEY" 会同时命中
        # "PRIVATE KEY" 与 "BEGIN RSA" 两个标记，非单命中样本）
        hits = self._scan({"k.py": 'key = "ssh-rsa AAAAB3NzaC1yc2Etest"\n'})
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("密钥字样", hits[0])

    def test_docstring_banner_marks_repair_copy(self):
        """修复标记在场：集成版 build_deb.py 含方案 A 标记（work15 原件里的
        「修复副本」banner 只存在于原型副本，不套回原件；集成版以源码内
        方案 A 注释块 + _DOC_NETS 为标记）。"""
        src = open(build_deb.__file__, encoding="utf-8").read()
        self.assertIn("REVIEW_FAIL 2026-11-22", src)
        self.assertIn("方案 A", src)
        self.assertIn("import ipaddress", src)
        self.assertIn("_DOC_NETS", src)
        self.assertIn("_IP_WHITELIST", src)


if __name__ == "__main__":
    unittest.main()


# 集成适配（work15 原件 → client-dev 集成版的全部改动，审查方留痕）：
# 1. import build_deb 前插入 packaging/ 到 sys.path（与 test_packaging.py 同口径）。
# 2. test_docstring_banner_marks_repair_copy 改为查集成版源码标记：work15 原件的
#    「修复副本」banner 只存在于原型副本、不套回原件；集成版以方案 A 注释块 +
#    _DOC_NETS 为标记。
# 其余与原件逐行一致。
