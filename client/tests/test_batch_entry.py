#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
U4/task19 离线测试（二）：按板块批量测的分组口径 + 入口决策 + 交付物红线
自检（无真实载荷 URL、core 不碰网络）。
==========================================================================
分组口径直接注入 client-dev 的 classify_proto（只读参考，机械锁定——
不许自造第二套归类）；缺席自动跳过。假节点全部是 classify 口径假件。
"""

from __future__ import annotations

import re
import sys
import types
import unittest
from pathlib import Path

try:
    from test_speedtest import CLIENT_DEV, speedtest, _load_module
except ImportError:  # 以 client.tests 包形态运行时
    from client.tests.test_speedtest import CLIENT_DEV, speedtest, _load_module

# 集成形态（审查方适配）：交付物扫描改为集成树内 DESIGN-speedtest.md；
# DONE.md 不落集成树（只留 work19/ 备查）。
DESIGN_DOC = CLIENT_DEV / "client" / "core" / "DESIGN-speedtest.md"


class FakeNode:
    """假节点：只带分组需要的 id / to_dict()（dict 形态同 Node.to_dict）。"""

    def __init__(self, nid, d):
        self.id = nid
        self._d = d

    def to_dict(self):
        return self._d


def _classify_ref():
    """集成树内 classify_proto（包导入：task25 起 classify_proto 用相对
    import 取同包 exit_keywords，spec 裸加载跑不起来；与 test_speedtest
    的 _settings_proto_ref 同口径，task19 W1-b 既定判例）；缺席返回 None
    （自动跳过）。"""
    if str(CLIENT_DEV) not in sys.path:
        sys.path.insert(0, str(CLIENT_DEV))
    try:
        from client.core import classify_proto
        return classify_proto
    except Exception:
        return None


def _sample_nodes():
    """六个板块键 + 未分类各一：全部走 classify_proto 的真实判定口径。"""
    return [
        FakeNode("n1", {"source": "vpngate", "protocol": "vless",
                        "exit_profile": {"org": "NTT EAST", "asn": ""}}),
        FakeNode("n2", {"source": "vpngate", "protocol": "trojan",
                        "exit_profile": {"org": "DIGITALOCEAN", "asn": "AS123"}}),
        FakeNode("n3", {"source": "manual", "protocol": "ss",
                        "extra": {"nature": "paid"},
                        "exit_profile": {"org": "COMCAST", "asn": ""}}),
        FakeNode("n4", {"source": "pipeline", "protocol": "vless",
                        "exit_profile": {}}),
        FakeNode("n5", {"source": "manual", "protocol": "openvpn",
                        "extra": {"nature": "paid"}, "exit_profile": {}}),
        FakeNode("n6", {"source": "manual", "protocol": "vless",
                        "exit_profile": {}}),   # 未标性质 → 未分类
    ]


class TestBoardGrouping(unittest.TestCase):
    def setUp(self):
        self.classify = _classify_ref()
        if self.classify is None:
            self.skipTest("client-dev 参考缺席：classify_proto 锁定用例跳过")

    def test_grouping_by_board_keys(self):
        """board_of 六键 + unclassified 各归各位，组内保序。"""
        nodes = _sample_nodes()
        groups = speedtest.group_nodes_by_board(nodes, classify=self.classify)
        self.assertEqual(groups["free_residential"], [nodes[0]])
        self.assertEqual(groups["free_datacenter"], [nodes[1]])
        self.assertEqual(groups["paid_residential"], [nodes[2]])
        self.assertEqual(groups["pipeline"], [nodes[3]])
        self.assertEqual(groups["openvpn"], [nodes[4]])
        self.assertEqual(groups[self.classify.UNCLASSIFIED], [nodes[5]])
        self.assertEqual(groups["paid_datacenter"], [])
        self.assertEqual(
            sorted(groups), sorted(list(self.classify.BOARDS)
                                   + [self.classify.UNCLASSIFIED]))

    def test_board_node_ids(self):
        ids = speedtest.board_node_ids(_sample_nodes(), "free_residential",
                                       classify=self.classify)
        self.assertEqual(ids, ["n1"])
        self.assertEqual(speedtest.board_node_ids(
            _sample_nodes(), "paid_datacenter", classify=self.classify), [])


class TestBoardBatchPlan(unittest.TestCase):
    """②区批量测入口决策：分组 + 载荷门卫（空槽显式提示，不报错不静默）。"""

    def setUp(self):
        self.classify = _classify_ref()
        if self.classify is None:
            self.skipTest("client-dev 参考缺席：classify_proto 锁定用例跳过")

    def test_empty_slot_notices_unconfigured(self):
        """载荷空槽：给「未配置测速载荷」提示 + 只测延迟；不抛错不静默。"""
        plan = speedtest.board_batch_plan(_sample_nodes(), "free_residential",
                                          "", classify=self.classify)
        self.assertEqual(plan["ids"], ["n1"])
        self.assertFalse(plan["measure_speed"])
        self.assertIn("未配置测速载荷", plan["notice"])
        plan_none = speedtest.board_batch_plan(
            _sample_nodes(), "free_residential", None, classify=self.classify)
        self.assertIn("未配置测速载荷", plan_none["notice"])
        self.assertFalse(plan_none["measure_speed"])

    def test_configured_payload_measures(self):
        plan = speedtest.board_batch_plan(
            _sample_nodes(), "pipeline", "http://payload.invalid/bin",
            classify=self.classify)
        self.assertEqual(plan["ids"], ["n4"])
        self.assertTrue(plan["measure_speed"])
        self.assertIsNone(plan["notice"])

    def test_invalid_payload_notices_not_raises(self):
        """地址形态非法：入口提示不可用 + 只测延迟，不当场炸、不静默跑。"""
        plan = speedtest.board_batch_plan(
            _sample_nodes(), "pipeline", "ftp://not-http.invalid/bin",
            classify=self.classify)
        self.assertFalse(plan["measure_speed"])
        self.assertIn("不可用", plan["notice"])

    def test_empty_board(self):
        plan = speedtest.board_batch_plan(_sample_nodes(), "paid_datacenter",
                                          "http://payload.invalid/bin",
                                          classify=self.classify)
        self.assertEqual(plan["ids"], [])
        self.assertFalse(plan["measure_speed"])
        self.assertIn("没有可测的节点", plan["notice"])


class TestDeliverableHygiene(unittest.TestCase):
    """交付物红线自检：全仓无真实载荷 URL；core 不碰真实网络。"""

    def test_no_real_url_literals_in_deliverables(self):
        """impl 代码与 DESIGN/DONE 里不许出现任何 http(s) 域名形态字面量
        （占位写法 http(s)://主机/路径 不会命中本正则）。"""
        pattern = re.compile(r"https?://[A-Za-z0-9][A-Za-z0-9.-]*\.[A-Za-z]{2,}")
        targets = [Path(speedtest.__file__), DESIGN_DOC]
        for path in targets:
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            matches = pattern.findall(text)
            self.assertEqual(
                matches, [],
                msg=f"{path.name} 出现 URL 字面量（红线：不许内置载荷地址）: {matches}")

    def test_core_touches_no_real_network(self):
        """core 模块不碰真实网络（AST 级检查）：不 import socket /
        urllib.request / http.client / ssl（urllib.parse 解析除外），
        不调用 urlopen / build_opener / 建连接函数。"""
        import ast
        src = Path(speedtest.__file__).read_text(encoding="utf-8")
        tree = ast.parse(src)
        banned_modules = {"socket", "ssl", "http.client", "requests", "urllib3"}
        banned_calls = {"urlopen", "build_opener", "create_connection",
                        "getaddrinfo", "socket"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    name = alias.name
                    ok = name == "urllib.parse" or not name.startswith("urllib")
                    self.assertTrue(
                        ok and name not in banned_modules,
                        msg=f"core 模块 import 了真实网络构件: {name}")
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                ok = module == "urllib.parse" or not module.startswith("urllib")
                self.assertTrue(
                    ok and module not in banned_modules,
                    msg=f"core 模块 import 了真实网络构件: {module}")
            elif isinstance(node, ast.Call):
                func = node.func
                called = getattr(func, "attr", None) or getattr(func, "id", None)
                self.assertNotIn(
                    called, banned_calls,
                    msg=f"core 模块调用了真实网络函数: {called}")

    def test_contracts_constants(self):
        """数据层与提示口径的键名锁定（接线清单引用的常量不许漂移）。"""
        self.assertEqual(speedtest.MEASURED_AT_KEY, "speed_measured_at")
        self.assertIn("未配置测速载荷", speedtest.UNCONFIGURED_HINT)
        self.assertEqual(speedtest.TIERS_MB, (5, 25))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# 集成适配清单（审查方，2026-11-22）：
# 1. 本文件落 client/tests/test_batch_entry.py；test_speedtest 导入加包形态回退。
# 2. 红线扫描的 DESIGN/DONE 改为集成树内 DESIGN-speedtest.md（DONE 不落树）。
# 3. _classify_ref 取集成树内 classify_proto（CLIENT_DEV = 集成树根）。
# 5. task25 集成适配：_classify_ref 改包导入（classify_proto 自 task25
#    起相对 import 同包 exit_keywords，spec 裸加载跑不起来；task19 W1-b
#    既定判例，见 test_speedtest._settings_proto_ref）。
# 4. 交付形态原件留 work19/，一字未动。
