#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tun_config.py 的离线测试（task6）
==================================
覆盖四块（验收口径）：
1. 参数校验：mtu 越界/非整数、stack 不在枚举、address 非法 CIDR/缺前缀/
   网络地址/空、布尔字段、网卡名约束、未知参数名——全部 ValueError 且
   带原因；auto_route=False 显式拒绝。
2. 默认值与 DESIGN.md 推荐选型一致（字段逐一断言）。
3. build_tun_config 合并后结构完整：inbounds/route/dns 关键字段断言，
   含规则顺序（sniff→hijack-dns 在既有 resolve 规则之前）、dns 规则
   指向经节点绕行的解析器、auto_detect_interface、原 config 不被改动、
   缺构件/重复 tag/多个绕行 DNS 服务器等错误路径。
4. mode_plan 两模式：inbound 种类清单与系统侧动作清单（含 privileged
   标记、铁律相关 notes），未知模式报错。

全程零外网、零文件、零系统调用——只断言生成的 dict 结构。
"""

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core import tun_config as tc  # noqa: E402


def make_base_config():
    """构造与 client-dev 引擎 build_config 同构的假 base config
    （inbounds/route/dns 形状一致，outbound 用占位 node-out）。
    铁律：假数据 only，不含任何真实节点地址。"""
    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": {
            "servers": [
                {"type": "udp", "tag": "remote-dns", "server": "203.0.113.53",
                 "server_port": 53, "detour": "node-out"},
                {"type": "local", "tag": "local-dns"},
            ],
            "final": "local-dns",
        },
        "inbounds": [
            {"type": "socks", "tag": "socks-in",
             "listen": "127.0.0.1", "listen_port": 7898},
            {"type": "http", "tag": "http-in",
             "listen": "127.0.0.1", "listen_port": 7897},
        ],
        "outbounds": [{"type": "vless", "tag": "node-out",
                       "server": "example.test", "server_port": 443,
                       "uuid": "00000000-0000-0000-0000-000000000000"}],
        "route": {
            "rules": [{"action": "resolve", "server": "remote-dns",
                       "strategy": "ipv4_only"}],
            "final": "node-out",
            "default_domain_resolver": "local-dns",
        },
    }


class BuildTunInboundDefaultsTest(unittest.TestCase):
    """默认值必须与 DESIGN.md 推荐选型逐字段一致。"""

    def setUp(self):
        self.inbound = tc.build_tun_inbound()

    def test_defaults_design_selected_values(self):
        self.assertEqual(self.inbound["type"], "tun")
        self.assertEqual(self.inbound["tag"], tc.TUN_TAG)
        self.assertEqual(self.inbound["interface_name"], "gate-tun")
        self.assertEqual(self.inbound["address"], ["172.19.0.1/30"])
        self.assertEqual(self.inbound["mtu"], 1500)
        self.assertEqual(self.inbound["stack"], "system")
        self.assertEqual(self.inbound["dns_mode"], "hijack")

    def test_defaults_takeover_semantics(self):
        # auto_route 是接管的本体；strict_route=true 是防泄漏选择
        self.assertIs(self.inbound["auto_route"], True)
        self.assertIs(self.inbound["strict_route"], True)

    def test_constants_consistent_with_defaults(self):
        # 模块常量与默认值同源（单一事实来源），防止改一处漏一处
        self.assertEqual(tc.TUN_TAG, "tun-in")
        self.assertEqual(tc.TUN_INTERFACE_NAME, "gate-tun")
        self.assertEqual(tc.TUN_ADDRESS, ["172.19.0.1/30"])
        self.assertEqual(tc.TUN_MTU, 1500)
        self.assertEqual(tc.TUN_STACK, "system")
        self.assertEqual(tc.TUN_DNS_MODE, "hijack")

    def test_all_values_are_json_serializable(self):
        # 最终要 json.dump 给 sing-box，先保证纯 JSON 类型
        import json
        roundtrip = json.loads(json.dumps(self.inbound))
        self.assertEqual(roundtrip, self.inbound)


class BuildTunInboundOverrideTest(unittest.TestCase):
    """合法 overrides 生效；非法 overrides 一律 ValueError 带原因。"""

    def test_override_each_param(self):
        inbound = tc.build_tun_inbound(
            tag="my-tun", interface_name="gc0", address="10.111.0.1/24",
            mtu=1280, auto_route=True, strict_route=False, stack="gvisor",
            dns_mode="native")
        self.assertEqual(inbound["tag"], "my-tun")
        self.assertEqual(inbound["interface_name"], "gc0")
        self.assertEqual(inbound["address"], ["10.111.0.1/24"])
        self.assertEqual(inbound["mtu"], 1280)
        self.assertIs(inbound["strict_route"], False)
        self.assertEqual(inbound["stack"], "gvisor")
        self.assertEqual(inbound["dns_mode"], "native")

    def test_address_list_and_ipv6(self):
        inbound = tc.build_tun_inbound(
            address=["172.19.0.1/30", "fdfe:dcba:9876::1/126"])
        self.assertEqual(inbound["address"],
                         ["172.19.0.1/30", "fdfe:dcba:9876::1/126"])

    def test_address_host_route_32_accepted(self):
        # /32 整段主机地址合法（点对点 tun 常用），不许被当网络地址误杀
        inbound = tc.build_tun_inbound(address="10.7.0.1/32")
        self.assertEqual(inbound["address"], ["10.7.0.1/32"])

    def test_mtu_out_of_range_low(self):
        with self.assertRaises(ValueError) as ctx:
            tc.build_tun_inbound(mtu=tc.MTU_MIN - 1)
        self.assertIn("越界", str(ctx.exception))

    def test_mtu_out_of_range_high(self):
        with self.assertRaises(ValueError) as ctx:
            tc.build_tun_inbound(mtu=tc.MTU_MAX + 1)
        self.assertIn("越界", str(ctx.exception))

    def test_mtu_boundary_accepted(self):
        self.assertEqual(tc.build_tun_inbound(mtu=tc.MTU_MIN)["mtu"],
                         tc.MTU_MIN)
        self.assertEqual(tc.build_tun_inbound(mtu=tc.MTU_MAX)["mtu"],
                         tc.MTU_MAX)

    def test_mtu_not_integer(self):
        for bad in ("1500", 1500.0, None, True):
            with self.assertRaises(ValueError):
                tc.build_tun_inbound(mtu=bad)

    def test_stack_not_in_enum(self):
        for bad in ("lwip", "SYSTEM", "gVisor", "", None, 3, True):
            with self.assertRaises(ValueError):
                tc.build_tun_inbound(stack=bad)

    def test_address_invalid_cidr(self):
        for bad in ("172.19.0.1",            # 缺前缀
                    "172.19.0.1/33",         # 前缀越界
                    "172.19.0.256/30",       # 地址非法
                    "not-an-address/30",     # 完全不是地址
                    "example.com/30",        # 域名混入
                    12345,                   # 非字符串
                    None,                    # None
                    ["172.19.0.1/30", "172.19.0.2/33"]):  # 列表混入坏项
            with self.assertRaises(ValueError):
                tc.build_tun_inbound(address=bad)

    def test_address_missing_prefix_explicit_reason(self):
        with self.assertRaises(ValueError) as ctx:
            tc.build_tun_inbound(address="172.19.0.1")
        self.assertIn("缺前缀", str(ctx.exception))

    def test_address_network_address_rejected(self):
        # 网络地址（主机位全 0）不是 tun 网卡该占的本地地址
        with self.assertRaises(ValueError) as ctx:
            tc.build_tun_inbound(address="172.19.0.0/30")
        self.assertIn("网络地址", str(ctx.exception))

    def test_address_empty_list_rejected(self):
        for bad in ([], ""):
            with self.assertRaises(ValueError):
                tc.build_tun_inbound(address=bad)

    def test_auto_route_false_rejected_with_reason(self):
        # auto_route=False 的 tun 不接管任何流量——不产出这种假 TUN
        with self.assertRaises(ValueError) as ctx:
            tc.build_tun_inbound(auto_route=False)
        self.assertIn("auto_route", str(ctx.exception))

    def test_auto_route_non_bool_rejected(self):
        for bad in (1, "true", None):
            with self.assertRaises(ValueError):
                tc.build_tun_inbound(auto_route=bad)

    def test_strict_route_non_bool_rejected(self):
        for bad in (0, "yes", None):
            with self.assertRaises(ValueError):
                tc.build_tun_inbound(strict_route=bad)

    def test_interface_name_invalid(self):
        for bad in ("", "   ", None,
                    "a" * 16,               # 超 IFNAMSIZ-1
                    "gate tun",             # 空格
                    "gate/tun",             # 斜杠
                    "gate\ntun"):           # 控制字符
            with self.assertRaises(ValueError):
                tc.build_tun_inbound(interface_name=bad)

    def test_unknown_param_rejected(self):
        # 拼错参数名必须当场暴露，不许静默吞（如 mtu→mtu_ 之类手误）
        with self.assertRaises(ValueError) as ctx:
            tc.build_tun_inbound(mtu_=1500)
        self.assertIn("未知 TUN 参数", str(ctx.exception))
        with self.assertRaises(ValueError):
            tc.build_tun_inbound(auto_router=True)

    def test_error_messages_are_chinese_and_reasoned(self):
        # 报错要能直接给人看：非空、含中文说明
        cases = [
            lambda: tc.build_tun_inbound(mtu=1),
            lambda: tc.build_tun_inbound(stack="lwip"),
            lambda: tc.build_tun_inbound(address="bad"),
            lambda: tc.build_tun_inbound(auto_route=False),
            lambda: tc.build_tun_inbound(mtu_bad=1),
        ]
        for case in cases:
            with self.assertRaises(ValueError) as ctx:
                case()
            self.assertTrue(str(ctx.exception).strip())
            self.assertTrue(any("\u4e00" <= ch <= "\u9fff"
                                for ch in str(ctx.exception)),
                            f"报错缺中文说明: {ctx.exception!r}")


class BuildTunConfigMergeTest(unittest.TestCase):
    """合并后结构完整：inbounds/route/dns 关键字段与规则顺序。"""

    def setUp(self):
        self.base = make_base_config()
        self.inbound = tc.build_tun_inbound()
        self.merged = tc.build_tun_config(self.base, self.inbound)

    def test_returns_new_dict_and_inputs_untouched(self):
        # 调用方的 base/入站 dict 绝不能被改动（副作用红线）
        self.assertNotIn("gate-tun", [i.get("tag") for i in
                                      self.base["inbounds"]])
        self.assertEqual(self.base["route"]["rules"],
                         [{"action": "resolve", "server": "remote-dns",
                           "strategy": "ipv4_only"}])
        self.assertNotIn("rules", self.base["dns"])
        self.assertNotIn("auto_detect_interface", self.base["route"])
        # 两次合并互不串扰（inbound 也被拷贝）
        merged2 = tc.build_tun_config(self.base, self.inbound)
        self.assertEqual(self.merged, merged2)
        merged2["inbounds"][-1]["mtu"] = 1
        self.assertEqual(self.inbound["mtu"], 1500)

    def test_inbounds_complete(self):
        tags = [i["tag"] for i in self.merged["inbounds"]]
        # socks/http 保留（健康探测与本地工具依赖）+ tun 追加
        self.assertEqual(tags, ["socks-in", "http-in", tc.TUN_TAG])
        tun = self.merged["inbounds"][-1]
        self.assertEqual(tun["type"], "tun")
        self.assertEqual(tun["interface_name"], "gate-tun")
        self.assertEqual(tun["address"], ["172.19.0.1/30"])
        self.assertIs(tun["auto_route"], True)
        self.assertIs(tun["strict_route"], True)
        self.assertEqual(tun["stack"], "system")

    def test_route_rules_order_and_existing_preserved(self):
        rules = self.merged["route"]["rules"]
        # 前置 sniff → hijack-dns，既有的 resolve 规则原样跟在后面
        self.assertEqual(rules[0], {"action": "sniff"})
        self.assertEqual(rules[1], {"protocol": "dns",
                                    "action": "hijack-dns"})
        self.assertIn({"action": "resolve", "server": "remote-dns",
                       "strategy": "ipv4_only"}, rules)
        # 单节点单出站语义原样保留（铁律：TUN 与选节点无关）
        self.assertEqual(self.merged["route"]["final"], "node-out")
        self.assertEqual(self.merged["route"]["default_domain_resolver"],
                         "local-dns")

    def test_route_auto_detect_interface_true(self):
        # 防回环：引擎自身出站（node-out/local-dns）必须绑物理网卡
        self.assertIs(self.merged["route"]["auto_detect_interface"], True)

    def test_dns_rule_targets_detoured_server(self):
        first = self.merged["dns"]["rules"][0]
        self.assertEqual(first, {"inbound": [tc.TUN_TAG],
                                 "server": "remote-dns",
                                 "strategy": "ipv4_only"})
        # 既有 dns 段（servers/final）不动：remote-dns 仍经 node-out 绕行
        servers = {s["tag"]: s for s in self.merged["dns"]["servers"]}
        self.assertEqual(servers["remote-dns"]["detour"], "node-out")
        self.assertEqual(self.merged["dns"]["final"], "local-dns")

    def test_other_sections_untouched(self):
        self.assertEqual(self.merged["log"], self.base["log"])
        self.assertEqual(self.merged["outbounds"], self.base["outbounds"])

    def test_merged_config_is_valid_json(self):
        import json
        self.assertEqual(json.loads(json.dumps(self.merged)), self.merged)

    def test_explicit_dns_server_tag(self):
        merged = tc.build_tun_config(self.base, self.inbound,
                                     dns_server_tag="remote-dns")
        self.assertEqual(merged["dns"]["rules"][0]["server"], "remote-dns")

    def test_explicit_dns_server_tag_unknown_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            tc.build_tun_config(self.base, self.inbound,
                                dns_server_tag="no-such-dns")
        self.assertIn("不在 dns.servers", str(ctx.exception))

    def test_auto_detected_server_when_tag_missing(self):
        # 定义性特征识别：detour == route.final 的唯一 server
        base = copy.deepcopy(self.base)
        base["dns"]["servers"][0]["tag"] = "exit-dns"
        merged = tc.build_tun_config(base, self.inbound)
        self.assertEqual(merged["dns"]["rules"][0]["server"], "exit-dns")

    def test_ambiguous_detoured_servers_rejected(self):
        # 两个绕行服务器：识别不了哪个是出口侧解析器，拒绝猜测
        base = copy.deepcopy(self.base)
        base["dns"]["servers"].append(
            {"type": "udp", "tag": "remote-dns-2", "server": "203.0.113.54",
             "server_port": 53, "detour": "node-out"})
        with self.assertRaises(ValueError) as ctx:
            tc.build_tun_config(base, self.inbound)
        self.assertIn("dns_server_tag", str(ctx.exception))

    def test_no_detoured_server_rejected(self):
        # 只有 local-dns 的 base config：TUN 的 DNS 防泄漏不成立，拒绝合并
        base = copy.deepcopy(self.base)
        base["dns"]["servers"] = [{"type": "local", "tag": "local-dns"}]
        with self.assertRaises(ValueError) as ctx:
            tc.build_tun_config(base, self.inbound)
        self.assertIn("绕行", str(ctx.exception))

    def test_detour_not_matching_final_rejected(self):
        base = copy.deepcopy(self.base)
        base["dns"]["servers"][0]["detour"] = "somewhere-else"
        with self.assertRaises(ValueError):
            tc.build_tun_config(base, self.inbound)

    def test_malformed_base_config_rejected(self):
        inbound = tc.build_tun_inbound()
        cases = [
            None, [], "config", 42,                    # 非 dict
            {},                                        # 空配置
            {**self.base, "inbounds": None},           # inbounds 缺失
            {**self.base, "route": {"final": "node-out"}},  # rules 缺失
            {**self.base, "route": {"rules": []}},     # final 缺失
            {**self.base, "dns": {}},                  # servers 缺失
        ]
        for bad in cases:
            with self.assertRaises(ValueError):
                tc.build_tun_config(bad, inbound)

    def test_duplicate_inbound_tag_rejected(self):
        base = copy.deepcopy(self.base)
        base["inbounds"].append(tc.build_tun_inbound())
        with self.assertRaises(ValueError) as ctx:
            tc.build_tun_config(base, tc.build_tun_inbound())
        self.assertIn("同名入站", str(ctx.exception))

    def test_handmade_inbound_rejected(self):
        # 不是 build_tun_inbound 产物（缺 auto_route/address）不许合并
        with self.assertRaises(ValueError):
            tc.build_tun_config(self.base, {"type": "tun", "tag": "tun-in"})
        with self.assertRaises(ValueError):
            tc.build_tun_config(self.base, None)
        with self.assertRaises(ValueError):
            tc.build_tun_config(self.base, {"type": "socks", "tag": "x",
                                            "address": ["172.19.0.1/30"],
                                            "auto_route": True})

    def test_base_auto_detect_interface_false_conflict(self):
        # base 显式关掉防回环：与 TUN 冲突，拒绝静默改写
        base = copy.deepcopy(self.base)
        base["route"]["auto_detect_interface"] = False
        with self.assertRaises(ValueError) as ctx:
            tc.build_tun_config(base, self.inbound)
        self.assertIn("auto_detect_interface", str(ctx.exception))

    def test_base_auto_detect_interface_true_kept(self):
        base = copy.deepcopy(self.base)
        base["route"]["auto_detect_interface"] = True
        merged = tc.build_tun_config(base, self.inbound)
        self.assertIs(merged["route"]["auto_detect_interface"], True)


class ModePlanTest(unittest.TestCase):
    """两模式的 inbound 种类与系统侧动作清单（纯数据）。"""

    def test_proxy_mode(self):
        plan = tc.mode_plan("proxy")
        self.assertEqual(plan["mode"], "proxy")
        self.assertEqual(plan["engine_inbound_kinds"], ["socks", "http"])
        self.assertEqual(len(plan["system_actions"]), 1)
        action = plan["system_actions"][0]
        self.assertEqual(action["actor"], "user")
        self.assertIs(action["privileged"], False)
        self.assertIs(action["required"], True)
        # 不含任何提权项
        self.assertTrue(all(not a.get("privileged")
                            for a in plan["system_actions"]))

    def test_proxy_mode_uppercase_and_spaces(self):
        self.assertEqual(tc.mode_plan("  PROXY ")["mode"], "proxy")

    def test_tun_mode(self):
        plan = tc.mode_plan("tun")
        self.assertEqual(plan["mode"], "tun")
        # socks/http 保留 + tun 追加
        self.assertEqual(plan["engine_inbound_kinds"],
                         ["socks", "http", "tun"])
        actions = plan["system_actions"]
        privileged = [a for a in actions if a.get("privileged")]
        self.assertEqual(len(privileged), 1)
        self.assertEqual(privileged[0]["actor"], "system")
        self.assertEqual(privileged[0]["needs"], "CAP_NET_ADMIN")
        self.assertIn(tc.TUN_INTERFACE_NAME, privileged[0]["action"])
        self.assertIn("auto_route", privileged[0]["action"])
        # 用户侧动作存在但非必需（授权对话框取消=启动失败，不是静默降级）
        user_actions = [a for a in actions if a["actor"] == "user"]
        self.assertTrue(user_actions)
        self.assertTrue(all(not a.get("privileged")
                            for a in user_actions))

    def test_no_node_switching_paths_in_plan(self):
        # 铁律对照：动作清单里不存在切换节点/自动重连/自动断开类动作
        banned = ("切换节点", "自动连", "自动重连", "换节点", "选节点",
                  "自动断开", "disconnect(", "connect(")
        for mode in ("proxy", "tun"):
            text = str(tc.mode_plan(mode))
            for word in banned:
                self.assertNotIn(word, text,
                                 f"{mode} 模式计划出现违禁词 {word!r}")

    def test_tun_notes_state_no_silent_fallback(self):
        plan = tc.mode_plan("tun")
        self.assertTrue(any("不静默回退" in n for n in plan["notes"]))

    def test_unknown_mode_rejected(self):
        for bad in ("tunnel", "", None, "TUNNEL", "proxy/tun", 1):
            with self.assertRaises(ValueError):
                tc.mode_plan(bad)

    def test_plan_is_pure_json_data(self):
        import json
        for mode in ("proxy", "tun"):
            plan = tc.mode_plan(mode)
            self.assertEqual(json.loads(json.dumps(plan)), plan)
        # 返回的是拷贝，改返回值不影响后续调用
        plan = tc.mode_plan("tun")
        plan["system_actions"][0]["action"] = "tampered"
        self.assertNotEqual(
            tc.mode_plan("tun")["system_actions"][0]["action"], "tampered")


class IronLawInvariantsTest(unittest.TestCase):
    """铁律对照：产出物里不存在节点选择/切换路径。"""

    def test_no_node_fields_in_generated_dicts(self):
        # 生成的 config 里不得出现自动选择类字段（urltest/selector 等）
        merged = tc.build_tun_config(make_base_config(),
                                     tc.build_tun_inbound())
        text = str(merged)
        for banned in ("urltest", "selector", "auto_switch", "connect_best"):
            self.assertNotIn(banned, text)
        self.assertEqual(merged["route"]["final"], "node-out")

    def test_module_has_no_io_or_network_surface(self):
        # 原型不许碰文件/网络/系统：import 面只许标准库三件
        import inspect
        src = inspect.getsource(tc)
        for banned_import in ("socket", "subprocess", "requests", "urllib",
                              "PySide6", "client"):
            self.assertNotIn(f"import {banned_import}", src)
            self.assertNotIn(f"from {banned_import}", src)
        imports = [line.strip() for line in src.splitlines()
                   if line.strip().startswith(("import ", "from "))
                   and "annotations" not in line]
        self.assertEqual(
            imports, ["import copy", "import ipaddress", "import re"])


if __name__ == "__main__":
    unittest.main()
