#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
usage_model 离线测试（task10）
================================
覆盖 DESIGN §4 口径：模型校验（合法/非法条目，非法报错不静默吞）、
配置生成（各分流模式、网站黑名单空/非空、拦截开关组合、IPv6、DNS
选择）、未覆盖项显式标注、规则集占位标记。假数据 only：域名一律
RFC 2606（example.com 等）、IP 一律 RFC 5737（192.0.2.0/24），
不发任何网络请求。

跑法：cd client-dev && python3 -m unittest client.tests.test_usage_model（-v 看明细）。
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.usage_model import (
    ADS_RULESET_TAG,
    CN_DIRECT_RULESET_TAG,
    DIRECT_OUT_TAG,
    MALWARE_RULESET_TAG,
    PLACEHOLDER_KEY,
    REMOTE_DNS_TAG,
    SITE_DIRECT,
    SITE_REJECT,
    SPLIT_DIRECT,
    SPLIT_GLOBAL,
    SPLIT_MODES,
    SPLIT_RULE,
    UsageSettings,
    UsageSettingsError,
    build_route_config,
    parse_site_blocklist,
)


# ---------------------------------------------------------------------------
# 设置模型：校验
# ---------------------------------------------------------------------------
class TestSettingsValidation(unittest.TestCase):
    def test_defaults_match_design(self):
        """默认值 = 现行行为：global / 全关 / v4-only / google（DESIGN §4.3）。"""
        m = UsageSettings()
        self.assertEqual(m.split_mode, SPLIT_GLOBAL)
        self.assertFalse(m.ad_block)
        self.assertFalse(m.malware_block)
        self.assertFalse(m.ipv6)
        self.assertEqual(m.dns_server, "google")

    def test_all_modes_accepted(self):
        for mode in SPLIT_MODES:
            UsageSettings(split_mode=mode)          # 不抛即合法

    def test_preset_dns_and_custom_ipv4(self):
        self.assertEqual(UsageSettings(dns_server="cloudflare").resolve_dns_ip(),
                         "1.1.1.1")
        self.assertEqual(UsageSettings(dns_server="192.0.2.10").resolve_dns_ip(),
                         "192.0.2.10")

    def test_bad_mode_rejected(self):
        with self.assertRaises(UsageSettingsError):
            UsageSettings(split_mode="smart")

    def test_switches_must_be_bool(self):
        """0/1 冒充布尔拒绝（口径：不许静默吞类型错误）。"""
        for name in ("ad_block", "malware_block", "ipv6"):
            with self.assertRaises(UsageSettingsError):
                UsageSettings(**{name: 1})
            with self.assertRaises(UsageSettingsError):
                UsageSettings(**{name: "yes"})

    def test_dns_hostname_rejected(self):
        with self.assertRaises(UsageSettingsError):
            UsageSettings(dns_server="dns.example.com")

    def test_dns_malformed_ip_rejected(self):
        for bad in ("999.1.1.1", "192.0.2", "", "  "):
            with self.assertRaises(UsageSettingsError):
                UsageSettings(dns_server=bad)

    def test_v6_dns_without_ipv6_rejected(self):
        """交叉校验：IPv6 关时 v6 DNS 拒收（DESIGN §4.4）。"""
        with self.assertRaises(UsageSettingsError):
            UsageSettings(dns_server="2001:db8::53")
        # IPv6 开则放行
        UsageSettings(dns_server="2001:db8::53", ipv6=True)

    def test_from_dict_roundtrip_and_rejects_unknown(self):
        m = UsageSettings(split_mode=SPLIT_RULE, ad_block=True,
                          dns_server="192.0.2.10")
        m2 = UsageSettings.from_dict(m.to_dict())
        self.assertEqual(m, m2)
        with self.assertRaises(UsageSettingsError):
            UsageSettings.from_dict({"unknown_key": 1})
        with self.assertRaises(UsageSettingsError):
            UsageSettings.from_dict("not-a-dict")
        with self.assertRaises(UsageSettingsError):
            UsageSettings.from_dict({"split_mode": "bad"})


# ---------------------------------------------------------------------------
# 网站黑名单：条目校验
# ---------------------------------------------------------------------------
class TestSiteBlocklistValidation(unittest.TestCase):
    def test_str_entry_defaults_to_reject(self):
        self.assertEqual(parse_site_blocklist(("ads.example.com",)),
                         (("ads.example.com", SITE_REJECT),))

    def test_dict_entry_explicit_action(self):
        self.assertEqual(
            parse_site_blocklist(({"domain": "cn.example.com",
                                   "action": SITE_DIRECT},)),
            (("cn.example.com", SITE_DIRECT),))

    def test_normalizes_case_and_trailing_dot(self):
        pairs = parse_site_blocklist(("EXAMPLE.com.",))
        self.assertEqual(pairs, (("example.com", SITE_REJECT),))

    def test_duplicates_same_action_deduped(self):
        pairs = parse_site_blocklist(("a.example.com", "a.example.com"))
        self.assertEqual(len(pairs), 1)

    def test_conflicting_actions_rejected(self):
        with self.assertRaises(UsageSettingsError):
            parse_site_blocklist(("a.example.com",
                                  {"domain": "a.example.com",
                                   "action": SITE_DIRECT}))

    def test_illegal_entries_rejected(self):
        bad_entries = [
            "https://ads.example.com",      # scheme
            "ads.example.com/path",         # 路径
            "ads example.com",              # 空格
            "*.example.com",                # 通配符
            "广告.example.com",              # 非 ASCII（IDN 未转）
            "localhost",                    # 单标签
            "",                             # 空串
            42,                             # 非字符串
            {"action": SITE_REJECT},        # dict 缺 domain
            {"domain": "a.example.com", "action": "forward"},   # 非法 action
            {"domain": "a.example.com", "extra": 1},            # 未知键
        ]
        for entry in bad_entries:
            with self.assertRaises(UsageSettingsError, msg=repr(entry)):
                parse_site_blocklist((entry,))


# ---------------------------------------------------------------------------
# 配置生成：分流模式
# ---------------------------------------------------------------------------
class TestBuildRouteConfigModes(unittest.TestCase):
    def test_global_default_minimal_fragment(self):
        """默认 global + 无黑名单 + 开关全关：零规则、零出站补丁、final 不动。"""
        frag = build_route_config(UsageSettings())
        self.assertEqual(frag["mode"], SPLIT_GLOBAL)
        self.assertEqual(frag["route"]["rules"], [])
        self.assertEqual(frag["route"]["rule_set"], [])
        self.assertIsNone(frag["route"]["final_override"])
        self.assertEqual(frag["outbound_patch"], [])
        self.assertEqual(frag["notes"], [])

    def test_direct_mode_final_override_and_outbound(self):
        frag = build_route_config(UsageSettings(split_mode=SPLIT_DIRECT))
        self.assertEqual(frag["route"]["final_override"], DIRECT_OUT_TAG)
        self.assertEqual(frag["outbound_patch"],
                         [{"type": "direct", "tag": DIRECT_OUT_TAG}])

    def test_rule_mode_keeps_engine_final_but_adds_direct_outbound(self):
        """rule 模式 final 留给引擎（未命中走节点），补 direct 出站。"""
        frag = build_route_config(UsageSettings(split_mode=SPLIT_RULE))
        self.assertIsNone(frag["route"]["final_override"])
        self.assertEqual(frag["outbound_patch"],
                         [{"type": "direct", "tag": DIRECT_OUT_TAG}])

    def test_merge_order_hint_before_resolve(self):
        """片段插在既有 resolve 规则之前（DESIGN §4.5-2：先拦截后解析）。"""
        frag = build_route_config(UsageSettings())
        self.assertEqual(frag["route"]["insert_before"], "resolve")


# ---------------------------------------------------------------------------
# 配置生成：网站黑名单消费
# ---------------------------------------------------------------------------
class TestBuildRouteConfigBlocklist(unittest.TestCase):
    def test_reject_rule_in_all_modes(self):
        """reject 条目在所有模式生效（DESIGN §4.2：命中即断不被模式冲掉）。"""
        for mode in SPLIT_MODES:
            frag = build_route_config(UsageSettings(split_mode=mode),
                                      site_blocklist=("tracker.example.com",))
            reject_rules = [r for r in frag["route"]["rules"]
                            if r.get("action") == SITE_REJECT]
            self.assertEqual(len(reject_rules), 1, mode)
            self.assertEqual(reject_rules[0]["domain_suffix"],
                             ["tracker.example.com"])

    def test_direct_entries_only_in_rule_mode(self):
        entries = ({"domain": "cn.example.com", "action": SITE_DIRECT},)
        frag = build_route_config(UsageSettings(split_mode=SPLIT_RULE),
                                  site_blocklist=entries)
        # 黑名单 direct 规则与 rule 模式的国内直连规则集是两回事：
        # 这里只数带 domain_suffix 的黑名单直连规则。
        route_rules = [r for r in frag["route"]["rules"]
                       if r.get("outbound") == DIRECT_OUT_TAG
                       and "domain_suffix" in r]
        self.assertEqual(len(route_rules), 1)
        self.assertEqual(route_rules[0]["domain_suffix"], ["cn.example.com"])

    def test_direct_entries_skipped_with_note_outside_rule_mode(self):
        """global/direct 模式下 direct 条目显式跳过并记 notes（不静默吞）。"""
        for mode in (SPLIT_GLOBAL, SPLIT_DIRECT):
            frag = build_route_config(UsageSettings(split_mode=mode),
                                      site_blocklist=({"domain": "cn.example.com",
                                                       "action": SITE_DIRECT},))
            self.assertFalse([r for r in frag["route"]["rules"]
                              if r.get("outbound") == DIRECT_OUT_TAG], mode)
            self.assertTrue(any("cn.example.com" in n for n in frag["notes"]), mode)

    def test_reject_rule_precedes_route_rules(self):
        """reject 在 direct 规则之前（先拦后路由）。"""
        frag = build_route_config(
            UsageSettings(split_mode=SPLIT_RULE),
            site_blocklist=("bad.example.com",
                            {"domain": "cn.example.com", "action": SITE_DIRECT}))
        actions = [r.get("action") for r in frag["route"]["rules"]]
        self.assertEqual(actions.index(SITE_REJECT), 0)
        self.assertLess(actions.index(SITE_REJECT),
                        actions.index("route"))


# ---------------------------------------------------------------------------
# 配置生成：拦截开关与规则集占位
# ---------------------------------------------------------------------------
class TestBuildRouteConfigBlockers(unittest.TestCase):
    def test_ad_block_generates_ruleset_with_placeholder(self):
        frag = build_route_config(UsageSettings(ad_block=True))
        self.assertEqual(frag["route"]["rules"],
                         [{"rule_set": [ADS_RULESET_TAG], "action": SITE_REJECT}])
        (rs,) = frag["route"]["rule_set"]
        self.assertEqual(rs["tag"], ADS_RULESET_TAG)
        self.assertIs(rs[PLACEHOLDER_KEY], True)      # 占位键必带（DESIGN §8.2）
        self.assertIn("cache/rulesets", rs["path"])

    def test_malware_block_generates_own_tag(self):
        frag = build_route_config(UsageSettings(malware_block=True))
        (rs,) = frag["route"]["rule_set"]
        self.assertEqual(rs["tag"], MALWARE_RULESET_TAG)

    def test_both_blockers_two_rules(self):
        frag = build_route_config(UsageSettings(ad_block=True, malware_block=True))
        self.assertEqual([r["rule_set"] for r in frag["route"]["rules"]],
                         [[ADS_RULESET_TAG], [MALWARE_RULESET_TAG]])
        self.assertEqual(len(frag["route"]["rule_set"]), 2)

    def test_rule_mode_adds_cn_direct_ruleset(self):
        frag = build_route_config(UsageSettings(split_mode=SPLIT_RULE))
        cn_rules = [r for r in frag["route"]["rules"]
                    if CN_DIRECT_RULESET_TAG in r.get("rule_set", ())]
        self.assertEqual(len(cn_rules), 1)
        self.assertEqual(cn_rules[0]["outbound"], DIRECT_OUT_TAG)
        tags = {rs["tag"] for rs in frag["route"]["rule_set"]}
        self.assertIn(CN_DIRECT_RULESET_TAG, tags)
        for rs in frag["route"]["rule_set"]:
            self.assertIs(rs[PLACEHOLDER_KEY], True)  # 全部占位，无真实来源


# ---------------------------------------------------------------------------
# 配置生成：DNS 与 IPv6
# ---------------------------------------------------------------------------
class TestBuildRouteConfigDns(unittest.TestCase):
    def test_default_dns_patch_matches_engine(self):
        """默认=引擎现行口径：remote-dns 8.8.8.8 经节点绕行 + resolve ipv4_only。"""
        frag = build_route_config(UsageSettings())
        self.assertEqual(frag["dns_patch"]["remote_dns_server"], "8.8.8.8")
        self.assertEqual(frag["dns_patch"]["resolve_rule_strategy"], "ipv4_only")

    def test_dns_preset_and_custom_ip_applied(self):
        frag = build_route_config(UsageSettings(dns_server="quad9"))
        self.assertEqual(frag["dns_patch"]["remote_dns_server"], "9.9.9.9")
        frag = build_route_config(UsageSettings(dns_server="192.0.2.53"))
        self.assertEqual(frag["dns_patch"]["remote_dns_server"], "192.0.2.53")

    def test_ipv6_on_removes_v4_only_strategy(self):
        frag = build_route_config(UsageSettings(ipv6=True))
        self.assertIsNone(frag["dns_patch"]["resolve_rule_strategy"])
        frag = build_route_config(UsageSettings(ipv6=False))
        self.assertEqual(frag["dns_patch"]["resolve_rule_strategy"], "ipv4_only")

    def test_remote_dns_tag_untouched(self):
        """防泄漏口径不动：remote-dns tag 与 detour 语义由引擎持有，
        片段只改 server 字段（DESIGN §4.4 写死的三件事）。"""
        frag = build_route_config(UsageSettings(dns_server="cloudflare"))
        self.assertIn(REMOTE_DNS_TAG, str(frag["dns_patch"]))
        self.assertNotIn("detour", frag["dns_patch"])


# ---------------------------------------------------------------------------
# 未覆盖项显式标注
# ---------------------------------------------------------------------------
class TestUncovered(unittest.TestCase):
    def test_uncovered_lists_all_non_config_features(self):
        frag = build_route_config(UsageSettings())
        features = {item["feature"] for item in frag["uncovered"]}
        for feature in ("容灾 / 自动替换", "TUN 模式", "测速 / 网络速度展示",
                        "定时监测", "自动更新", "特殊网站测试",
                        "节点白/黑名单", "应用净化", "订阅导入"):
            self.assertIn(feature, features)

    def test_uncovered_items_carry_belongs_and_why(self):
        frag = build_route_config(UsageSettings())
        for item in frag["uncovered"]:
            self.assertIn("belongs", item)
            self.assertIn("why", item)
            self.assertTrue(item["belongs"] and item["why"])

    def test_no_fake_generation_for_uncovered(self):
        """未覆盖功能不得混进配置片段（不许假装生成）。"""
        frag = build_route_config(UsageSettings())
        blob = str(frag["route"]) + str(frag["dns_patch"])
        self.assertNotIn("failover", blob)
        self.assertNotIn("tun", blob.lower())
        self.assertNotIn("process_name", blob)


# ---------------------------------------------------------------------------
# 健壮性
# ---------------------------------------------------------------------------
class TestRobustness(unittest.TestCase):
    def test_model_type_required(self):
        with self.assertRaises(UsageSettingsError):
            build_route_config({"split_mode": SPLIT_GLOBAL})

    def test_input_tuple_not_mutated_and_result_deterministic(self):
        entries = ("a.example.com", {"domain": "b.example.com",
                                     "action": SITE_DIRECT})
        snapshot = tuple(entries)
        frag1 = build_route_config(UsageSettings(split_mode=SPLIT_RULE),
                                   site_blocklist=entries)
        self.assertEqual(entries, snapshot)              # 入参不被改动
        frag2 = build_route_config(UsageSettings(split_mode=SPLIT_RULE),
                                   site_blocklist=entries)
        self.assertEqual(frag1, frag2)                   # 同输入同输出

    def test_rules_lists_are_fresh_objects(self):
        """两次结果不共享可变内层（改一份不串另一份）。"""
        frag1 = build_route_config(UsageSettings(ad_block=True))
        frag2 = build_route_config(UsageSettings(ad_block=True))
        frag1["route"]["rules"].append({"domain_suffix": ["x.example.com"]})
        self.assertEqual(len(frag2["route"]["rules"]), 1)


if __name__ == "__main__":
    unittest.main()
