#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""U2 分流与 DNS 接线（task17）离线测试：合并契约 + 名单/设置存储 + ③区。
================================================================简报口径：经 sys.path 只读引用 client-dev 的真实模块（usage_model /
settings_proto / singbox_engine 的现行产物形态），work17 四个交付模块
以包内成员身份挂载（仅测试进程内存生效，不写盘、不改 client-dev）。
各组：
1. 锚点组（无 Qt 依赖）：交付物在位 / DESIGN 必备节在位 / 四禁词扫描 /
   交付模块独立性（不依赖 PySide6、不碰 orchestrator）——任何环境实跑；
2. 合并组：默认片段与现行 build_config 深等价（零行为变化红线，七协议
   × proxy/tun）+ §4.5 五条顺序契约逐条断言 + 占位 rule_set 不进 live
   配置（方案 A，REVIEW_FAIL 修复）+ 报错路径；
3. 名单文件层组：往返无损 / 双处置冲突 / 非法条目 / 损坏与缺失处置 /
   路径从设置存储推导；
4. 设置存储组：task12 §4 逐情形处置 + ③区「merge 单字段」接线机制；
5. ③区 offscreen 组（PySide6 缺席整组 skip 并记 DONE.md，不伪造通过）：
   QT_QPA_PLATFORM=offscreen 真构造控件，值落盘往返 / 单字段写入 /
   交叉校验回退 / 损坏文件形态；
6. sing-box check 组（二进制不在场 skip 并记 DONE.md）：合并后配置跑
   `sing-box check -c`（默认片段 / direct 模式 / global+reject 名单）。

红线自查内嵌：四禁词（work10 §5.5）、铁律结构（orchestrator 零牵连）、
假数据 only（RFC 5737 保留 IP / 伪造 uuid / 虚构域名）。
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
QT_QPA = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _find_client_dev():
    """client-dev 只读引用定位：CLIENT_DEV 环境变量优先，其次仓库相对路径。"""
    env = os.environ.get("CLIENT_DEV")
    if env and os.path.isdir(env):
        return env
    cand = os.path.abspath(os.path.join(HERE, "..", ".."))  # client/tests -> client-dev 根
    if os.path.isdir(cand):
        return cand
    return None


CLIENT_DEV = _find_client_dev()
if CLIENT_DEV and CLIENT_DEV not in sys.path:
    sys.path.insert(0, CLIENT_DEV)

try:
    from PySide6.QtWidgets import QApplication
    _QT = True
except ImportError:
    _QT = False

_HAS_DEV = CLIENT_DEV is not None

if _HAS_DEV:
    import client
    import client.core
    import client.ui
    # 集成后四个交付模块已是 client.core / client.ui 的真包成员，直接 import。
    from client.core import settings_proto, settings_store, site_blocklist
    from client.core import usage_merge, usage_model
    from client.engines.singbox_engine import SINGBOX_PROTOCOLS  # 集成适配 3
    from client.core.model import Node
    from client.engines.base import EngineError
    from client.engines.singbox_engine import SingBoxEngine
else:
    settings_proto = settings_store = site_blocklist = None
    usage_merge = usage_model = None
    Node = EngineError = SingBoxEngine = None

# ---------------------------------------------------------------------------
# 假数据 only（RFC 5737 保留 IP / 伪造凭证 / 虚构域名），零真实节点/订阅/密钥
# ---------------------------------------------------------------------------
_FAKE_UUID = "00000000-0000-4000-8000-000000000000"
_FAKE_KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="   # 全零 32B 伪造
_FAKE_PW = "fake-password-not-real"
_DOC_V4 = "198.51.100.{}"          # RFC 5737 TEST-NET-2
_DOC_DNS = "192.0.2.53"            # RFC 5737 TEST-NET-1（自定义 DNS 用）
_DOC_DOMAIN = "example.com"        # RFC 2606 保留域

# 七协议出站夹具（build_config 全协议覆盖：outbounds 列表与 endpoints 两种形态）
_NODE_EXTRA = {
    "vless": {"uuid": _FAKE_UUID},
    "ss": {"method": "aes-128-gcm", "password": _FAKE_PW},
    "trojan": {"password": _FAKE_PW},
    "vmess": {"clash": {"uuid": _FAKE_UUID, "alterId": 0, "cipher": "auto"}},
    "hysteria2": {"password": _FAKE_PW},
    "tuic": {"uuid": _FAKE_UUID, "password": _FAKE_PW},
    "wireguard": {"private_key": _FAKE_KEY, "public_key": _FAKE_KEY,
                  "local_address": "10.7.0.2"},   # extra 直存键口径（singbox_engine._wireguard_endpoint）
}


def make_node(protocol="vless", node_id=None):
    """协议夹具节点（假数据 only）。"""
    return Node(id=node_id or f"t-{protocol}", name=f"{protocol} 测试节点",
                protocol=protocol, source="manual",
                host=_DOC_V4.format(1), port=443,
                extra=dict(_NODE_EXTRA[protocol]))


def fragment(usage=None, blocklist=()):
    """build_route_config 的直通别名（片段生产者是 client-dev 真实模块）。"""
    return usage_model.build_route_config(usage or usage_model.UsageSettings(),
                                          blocklist)


def resolve_rule(config):
    rules = [r for r in config["route"]["rules"]
             if isinstance(r, dict) and r.get("action") == "resolve"]
    assert rules, "夹具配置缺 resolve 规则（引擎形态被改坏？）"
    return rules[0]


# ---------------------------------------------------------------------------
# 组 1：锚点/静态（无 Qt 依赖，任何环境实跑）
# ---------------------------------------------------------------------------
# 交付物在集成树内的落位（审查方适配 5：TestDeliverables 原按 work17/
# 目录读文件，集成后按 CLIENT_DEV 落位读；DESIGN.md 落
# client/core/DESIGN-usage.md，断言内容一字不动）。
_DELIV = {
    "DESIGN.md": "client/core/DESIGN-usage.md",
    "usage_merge.py": "client/core/usage_merge.py",
    "site_blocklist.py": "client/core/site_blocklist.py",
    "settings_store.py": "client/core/settings_store.py",
    "usage_rules_page.py": "client/ui/usage_rules_page.py",
}


@unittest.skipUnless(_HAS_DEV, "client-dev 不在场，锚点组跳过")
class TestDeliverables(unittest.TestCase):
    def _read(self, name):
        with open(os.path.join(CLIENT_DEV, _DELIV[name]), "r", encoding="utf-8") as f:
            return f.read()

    def test_deliverable_files_present(self):
        for name in ("DESIGN.md", "usage_merge.py", "site_blocklist.py",
                     "settings_store.py", "usage_rules_page.py"):
            self.assertTrue(os.path.isfile(os.path.join(CLIENT_DEV, _DELIV[name])),
                            f"交付物缺失: {name}")

    def test_design_required_sections(self):
        text = self._read("DESIGN.md")
        for marker in ("合并接线图", "集成改动点清单", "名单文件形态定稿",
                       "生效口径", "唯一模型", "所缺一环", "深等价"):
            self.assertIn(marker, text, f"DESIGN.md 缺必备节标记: {marker}")

    def test_forbidden_words_zero_hits(self):
        """work10 §5.5 四禁词对四个交付模块扫描为零（铁律：永不自动切换）。"""
        forbidden = ("connect_best", "connect_fastest",
                     "按延迟排序备用组", "定时延迟对比换节点")
        for name in ("usage_merge.py", "site_blocklist.py",
                     "settings_store.py", "usage_rules_page.py"):
            text = self._read(name)
            for word in forbidden:
                self.assertNotIn(word, text,
                                 f"{name} 出现禁词 {word!r}（铁律红线）")

    def test_core_modules_qt_free_and_engine_free(self):
        """core 三模块不依赖 PySide6、不 import 引擎层（分层口径：core 出
        纯函数与文件层，进程管理只在 engines；orchestrator 红线另测）。
        扫 import 行而非全文——docstring 里的口径声明不算依赖。"""
        for name in ("usage_merge.py", "site_blocklist.py", "settings_store.py"):
            import_lines = [
                ln.strip() for ln in self._read(name).splitlines()
                if ln.strip().startswith(("import ", "from "))]
            for ln in import_lines:
                self.assertNotIn("PySide6", ln, f"{name} 的 {ln!r} 不得依赖 Qt")
                self.assertNotIn("engines", ln, f"{name} 的 {ln!r} 不得引引擎层")
            self.assertTrue(import_lines, f"{name} 扫不到 import 行（文件坏了？）")

    def test_orchestrator_untouched_by_this_segment(self):
        """orchestrator 一行不改（红线）：本段三处接线（引擎尾部 merge、
        构造点供给器、③区挂载）没有一个落点在 orchestrator。"""
        with open(os.path.join(CLIENT_DEV, "client", "core", "orchestrator.py"),
                  "r", encoding="utf-8") as f:
            text = f.read()
        for marker in ("usage_merge", "usage_provider", "usage_rules_page",
                       "site_blocklist", "settings_store"):
            self.assertNotIn(marker, text,
                             f"orchestrator.py 被本段触碰（红线）: {marker}")

    def test_merge_module_single_source_constants(self):
        """merge 不另造第二套口径常量：mode/tag 常量全部 import 自
        usage_model（单一事实源，work10 §11 精神）。"""
        text = self._read("usage_merge.py")
        self.assertIn("from .usage_model import", text)
        self.assertNotIn('SPLIT_GLOBAL = "', text)
        self.assertNotIn('DIRECT_OUT_TAG = "', text)


# ---------------------------------------------------------------------------
# 组 2：合并契约（真引擎产物形态上断言；无 Qt 依赖）
# ---------------------------------------------------------------------------
@unittest.skipUnless(_HAS_DEV, "client-dev 不在场，合并组跳过")
class MergeTestBase(unittest.TestCase):
    def setUp(self):
        self.engine = SingBoxEngine()                     # proxy 接管（现行）
        self.tun_engine = SingBoxEngine(takeover_mode="tun")
        self.config = self.engine.build_config(make_node("vless"))
        self.tun_config = self.tun_engine.build_config(make_node("vless"))
        self.orig = copy.deepcopy(self.config)


class TestDefaultFragmentDeepEqual(MergeTestBase):
    """回归红线：默认 UsageSettings + 空名单的片段并入后与现行 build_config
    输出深等价（零行为变化）——七协议 × proxy/tun 逐个实证。"""

    def _assert_zero_change(self, engine, protocol):
        node = make_node(protocol)
        config = engine.build_config(node)
        keep = copy.deepcopy(config)
        merged = usage_merge.merge_usage_config(config, fragment())
        self.assertEqual(merged, keep,
                         f"{protocol}/{engine.takeover_mode}: 默认片段改变了行为")
        self.assertEqual(config, keep, "merge 不得改动入参 config")

    def test_default_deep_equal_all_protocols_proxy(self):
        for protocol in usage_model_usage_protocols():
            with self.subTest(protocol=protocol):
                self._assert_zero_change(self.engine, protocol)

    def test_default_deep_equal_all_protocols_tun(self):
        for protocol in usage_model_usage_protocols():
            with self.subTest(protocol=protocol):
                self._assert_zero_change(self.tun_engine, protocol)

    def test_default_tun_front_rules_untouched(self):
        """契约 1 佐证：TUN 场景前置形态在合并前后逐字一致（sniff[0] /
        hijack-dns[1]/resolve[2] 现状不动）。"""
        self.assertEqual(self.tun_config["route"]["rules"][0],
                         {"action": "sniff"})
        self.assertEqual(self.tun_config["route"]["rules"][1],
                         {"protocol": "dns", "action": "hijack-dns"})
        self.assertEqual(self.tun_config["route"]["rules"][2]["action"],
                         "resolve")
        merged = usage_merge.merge_usage_config(self.tun_config, fragment())
        self.assertEqual(merged["route"]["rules"][:3],
                         self.tun_config["route"]["rules"][:3])


def usage_model_usage_protocols():
    """七协议清单（singbox_engine.SINGBOX_PROTOCOLS 真值，不另抄名单）。"""
    return SINGBOX_PROTOCOLS


class TestContract45(MergeTestBase):
    """§4.5 五条顺序契约逐条断言（简报交付物 2 / 验收口径）。"""

    # ---- 契约 1：TUN 前置不动 ----
    def test_c1_tun_front_rules_stay_first_with_usage_rules(self):
        """广告/恶意拦截的占位 rule_set 引用规则已被滤除（方案 A，见
        test_placeholder_blocked_from_live_config 专测）；名单 reject 规则
        仍插在 tun 前置之后、resolve 之前（位置语义不变）。"""
        frag = fragment(usage_model.UsageSettings(ad_block=True),
                        ["ads.example.com"])
        merged = usage_merge.merge_usage_config(self.tun_config, frag)
        rules = merged["route"]["rules"]
        self.assertEqual(rules[0], {"action": "sniff"})
        self.assertEqual(rules[1], {"protocol": "dns", "action": "hijack-dns"})
        self.assertEqual(rules[2],
                         {"domain_suffix": ["ads.example.com"],
                          "action": "reject"})
        self.assertEqual(rules[3]["action"], "resolve")
        self.assertEqual(len(rules), 4)

    # ---- 契约 2：插在既有 resolve 规则之前 ----
    def test_c2_fragment_rules_precede_resolve(self):
        """片段 live 规则顺序保真（usage_model 产出语义序不重排）：reject
        在前、direct route 在后，都插在 resolve 之前。占位 rule_set 引用
        规则已被滤除（方案 A），不再参与位置断言。"""
        frag = fragment(usage_model.UsageSettings(split_mode="rule"),
                        ["ads.example.com",
                         {"domain": "direct.example.com", "action": "direct"}])
        merged = usage_merge.merge_usage_config(self.config, frag)
        rules = merged["route"]["rules"]
        self.assertEqual(rules[0]["action"], "reject")       # 先拦截
        self.assertEqual(rules[1], {"domain_suffix": ["direct.example.com"],
                                    "action": "route",
                                    "outbound": "direct-out"})
        self.assertEqual(rules[-1]["action"], "resolve")     # 后解析
        self.assertEqual(len(rules), 3)

    def test_c2_blocklist_reject_rule_is_first_inserted(self):
        frag = fragment(usage_model.UsageSettings(),
                        ["b1.example.com", "b2.example.com"])
        merged = usage_merge.merge_usage_config(self.config, frag)
        self.assertEqual(merged["route"]["rules"][0],
                         {"domain_suffix": ["b1.example.com", "b2.example.com"],
                          "action": "reject"})
        self.assertEqual(merged["route"]["rules"][1]["action"], "resolve")

    # ---- 契约 3：dns_patch 只动两字段 ----
    def test_c3_remote_dns_server_only_field_changed(self):
        merged = usage_merge.merge_usage_config(
            self.config, fragment(usage_model.UsageSettings(dns_server="quad9")))
        before = self.orig["dns"]
        self.assertEqual(merged["dns"]["servers"][0],
                         {**before["servers"][0], "server": "9.9.9.9"})
        # detour/server_port/type/tag 与 local-dns/final 全部不动
        self.assertEqual(merged["dns"]["servers"][0]["detour"], "node-out")
        self.assertEqual(merged["dns"]["servers"][0]["server_port"], 53)
        self.assertEqual(merged["dns"]["servers"][1], before["servers"][1])
        self.assertEqual(merged["dns"]["final"], before["final"])
        self.assertEqual(merged["dns"]["servers"][1], {"type": "local",
                                                       "tag": "local-dns"})

    def test_c3_resolve_strategy_write_same_and_remove(self):
        # 默认（ipv6 关）→ strategy 写回同值 ipv4_only，其余键不动
        merged = usage_merge.merge_usage_config(self.config, fragment())
        self.assertEqual(resolve_rule(merged), resolve_rule(self.orig))
        # IPv6 开 → strategy=None=移除该字段，resolve 其余键不动
        merged6 = usage_merge.merge_usage_config(
            self.config, fragment(usage_model.UsageSettings(ipv6=True)))
        rr = resolve_rule(merged6)
        self.assertEqual(rr, {"action": "resolve", "server": "remote-dns"})
        self.assertNotIn("strategy", rr)
        # 自定义 IP → server 写回该 IP 字面量
        merged_ip = usage_merge.merge_usage_config(
            self.config, fragment(usage_model.UsageSettings(dns_server=_DOC_DNS)))
        self.assertEqual(merged_ip["dns"]["servers"][0]["server"], _DOC_DNS)

    # ---- 契约 4：mode≠global 才追加 direct-out ----
    def test_c4_direct_out_only_for_non_global(self):
        direct_out = {"type": "direct", "tag": "direct-out"}
        for mode in ("rule", "direct"):
            merged = usage_merge.merge_usage_config(
                self.config, fragment(usage_model.UsageSettings(split_mode=mode)))
            self.assertEqual(merged["outbounds"], self.orig["outbounds"] + [direct_out],
                             f"{mode} 模式应追加 direct-out")
            self.assertEqual(merged["outbounds"][0]["tag"], "node-out",
                             "outbounds[0] 恒为 node-out（单节点单出站语义）")
        merged_g = usage_merge.merge_usage_config(self.config, fragment())
        self.assertEqual(merged_g["outbounds"], self.orig["outbounds"],
                         "global 模式不得追加出站")

    def test_c4_wireguard_endpoint_form_direct(self):
        """wireguard（outbounds=[] 的 endpoints 形态）direct 模式：direct-out
        追加后 outbounds=[direct-out]，route.final 指向它（单节点语义不变：
        出站承担者 endpoints[0] tag 仍 node-out）。"""
        wg = self.engine.build_config(make_node("wireguard"))
        self.assertEqual(wg["outbounds"], [])               # 形态前提
        merged = usage_merge.merge_usage_config(
            wg, fragment(usage_model.UsageSettings(split_mode="direct")))
        self.assertEqual(merged["outbounds"], [{"type": "direct",
                                                "tag": "direct-out"}])
        self.assertEqual(merged["route"]["final"], "direct-out")
        self.assertEqual(merged["endpoints"][0]["tag"], "node-out")

    # ---- 契约 5：route.final 仅 direct 改写 ----
    def test_c5_final_only_direct_mode(self):
        for mode, expect in (("global", "node-out"), ("rule", "node-out"),
                             ("direct", "direct-out")):
            merged = usage_merge.merge_usage_config(
                self.config, fragment(usage_model.UsageSettings(split_mode=mode)))
            self.assertEqual(merged["route"]["final"], expect, f"mode={mode}")

    # ---- 名单处置与模式的合并语义（0.14.0 口径经引擎落地的端到端） ----
    def test_blocklist_reject_all_modes_direct_entries_rule_only(self):
        bl = ["reject.example.com",
              {"domain": "direct.example.com", "action": "direct"}]
        for mode in ("global", "rule", "direct"):
            usage = usage_model.UsageSettings(split_mode=mode)
            frag = fragment(usage, bl)
            merged = usage_merge.merge_usage_config(self.config, frag)
            rejects = [r for r in merged["route"]["rules"]
                       if r.get("action") == "reject" and "domain_suffix" in r]
            self.assertTrue(
                any("reject.example.com" in r["domain_suffix"] for r in rejects),
                f"{mode}: reject 条目全模式生效")
            routes = [r for r in merged["route"]["rules"]
                      if r.get("outbound") == "direct-out"]
            if mode == "rule":
                self.assertTrue(
                    any("direct.example.com" in r.get("domain_suffix", [])
                        for r in routes), "rule: direct 条目生成 route 规则")
            else:
                self.assertFalse(
                    any("direct.example.com" in r.get("domain_suffix", [])
                        for r in routes),
                    f"{mode}: direct 条目不得生成（与模式语义冲突）")
                # 显式跳过写在片段 notes（不静默吞；merge 不吞 notes——
                # notes 属片段报告，不入路由配置）
                self.assertTrue(
                    any("direct.example.com" in n for n in frag["notes"]),
                    f"{mode}: direct 条目跳过须记 notes")

    def test_rule_mode_carries_placeholder_ruleset(self):
        """0.14.0「占位」口径在片段层保留（方案 A 未拆它，usage_model 不动）：
        占位键在 build_route_config 的**片段**里必须在场（真条目来源待主人
        核定，绝不假装生成）。live 配置层由 merge 滤除——见
        test_placeholder_blocked_from_live_config（REVIEW_FAIL 根因：
        占位条目进 live 配置必被真 sing-box 严格 JSON 解码整段拒绝）。"""
        frag = fragment(usage_model.UsageSettings(split_mode="rule"))
        rs = frag["route"]["rule_set"]
        self.assertEqual(len(rs), 1)
        self.assertEqual(rs[0]["tag"], usage_model.CN_DIRECT_RULESET_TAG)
        self.assertTrue(rs[0].get("prototype_placeholder"),
                        "占位键必须在场（片段层口径，绝不假装生成）")
        self.assertEqual(rs[0]["path"], "cache/rulesets/cn-direct.srs")

    def test_placeholder_blocked_from_live_config(self):
        """方案 A（主会话 2026-11-22 裁决）：占位 rule_set 条目与引用其 tag
        的规则不进 live 配置——三种触发形态全覆盖（广告/恶意开关、rule 模式）。
        断言三件事：live 配置无占位键、无引用占位 tag 的 rules、滤除经
        notes 通道说明（不静默）；片段本身不被改动；不传 notes（引擎现行
        两参调用形态）滤除行为不变。"""
        placeholder_tags = {usage_model.ADS_RULESET_TAG,
                            usage_model.MALWARE_RULESET_TAG,
                            usage_model.CN_DIRECT_RULESET_TAG}
        scenarios = (
            usage_model.UsageSettings(ad_block=True),
            usage_model.UsageSettings(malware_block=True),
            usage_model.UsageSettings(split_mode="rule"),
        )
        for usage in scenarios:
            with self.subTest(mode=usage.split_mode, ad=usage.ad_block,
                              mal=usage.malware_block):
                frag = fragment(usage)
                # 前提：片段确实带占位（0.14.0 口径在片段层保留）
                self.assertTrue(
                    any(rs.get("prototype_placeholder")
                        for rs in frag["route"]["rule_set"]),
                    "夹具前提不成立：片段应带占位 rule_set")
                frag_keep = copy.deepcopy(frag)
                # 带 notes 通道：live 配置干净 + 说明在案
                notes = []
                merged = usage_merge.merge_usage_config(self.config, frag,
                                                        notes=notes)
                self.assertEqual(frag, frag_keep, "滤除不得改动片段入参")
                for rs in merged["route"].get("rule_set", []):
                    self.assertNotIn("prototype_placeholder", rs,
                                     f"占位键进了 live 配置: {rs}")
                    self.assertNotIn(rs.get("tag"), placeholder_tags,
                                     f"占位 rule_set 进了 live 配置: {rs}")
                for rule in merged["route"]["rules"]:
                    refs = set(rule.get("rule_set") or ())
                    self.assertFalse(refs & placeholder_tags,
                                     f"引用占位 tag 的规则进了 live 配置: {rule}")
                self.assertTrue(notes and "占位规则集被拦" in notes[0],
                                f"滤除须经 notes 说明（不静默）: {notes}")
                self.assertIn("自然生效", notes[0])
                # 不传 notes（引擎集成点现行两参调用）：滤除行为不变
                merged_plain = usage_merge.merge_usage_config(self.config, frag)
                self.assertEqual(
                    merged_plain["route"].get("rule_set", []),
                    merged["route"].get("rule_set", []),
                    "notes 通道不得影响滤除行为（集成点兼容）")
                self.assertEqual(merged_plain["route"]["rules"],
                                 merged["route"]["rules"])


class TestMergeErrors(MergeTestBase):
    """报错路径：锚点/目标缺失、契约破坏、形状非法——报错不静默吞。"""

    def _merged(self, config, fragment_dict):
        return usage_merge.merge_usage_config(config, fragment_dict)

    def test_missing_resolve_rule_errors(self):
        bad = copy.deepcopy(self.config)
        bad["route"]["rules"] = [r for r in bad["route"]["rules"]
                                 if r.get("action") != "resolve"]
        with self.assertRaises(usage_merge.UsageMergeError):
            # 名单 reject 规则是 live 规则（占位 rule_set 引用规则已被
            # 滤除、不再触发锚点查找——用名单片段保持锚点防线覆盖）
            self._merged(bad, fragment(usage_model.UsageSettings(),
                                       ["b.example.com"]))

    def test_missing_remote_dns_server_errors(self):
        bad = copy.deepcopy(self.config)
        bad["dns"]["servers"] = [s for s in bad["dns"]["servers"]
                                 if s.get("tag") != "remote-dns"]
        with self.assertRaises(usage_merge.UsageMergeError):
            self._merged(bad, fragment())

    def test_ambiguous_remote_dns_errors(self):
        bad = copy.deepcopy(self.config)
        bad["dns"]["servers"].append(copy.deepcopy(bad["dns"]["servers"][0]))
        with self.assertRaises(usage_merge.UsageMergeError):
            self._merged(bad, fragment())

    def test_incomplete_config_shapes_errors(self):
        frag = fragment()
        for key, mutation in (
                ("route", lambda c: c.pop("route")),
                ("dns", lambda c: c.pop("dns")),
                ("outbounds", lambda c: c.pop("outbounds")),
                ("final", lambda c: c["route"].pop("final"))):
            bad = copy.deepcopy(self.config)
            mutation(bad)
            with self.assertRaises(usage_merge.UsageMergeError, msg=key):
                self._merged(bad, frag)

    def test_fragment_contract_violations_errors(self):
        with self.assertRaises(usage_merge.UsageMergeError):
            # global 片段带 outbound_patch（§4.5-4 破坏）
            frag = fragment()
            frag["outbound_patch"] = [{"type": "direct", "tag": "direct-out"}]
            self._merged(self.config, frag)
        with self.assertRaises(usage_merge.UsageMergeError):
            # rule 片段改 final（§4.5-5 破坏）
            frag = fragment(usage_model.UsageSettings(split_mode="rule"))
            frag["route"]["final_override"] = "direct-out"
            self._merged(self.config, frag)
        with self.assertRaises(usage_merge.UsageMergeError):
            # direct 片段 final_override 不是 direct-out
            frag = fragment(usage_model.UsageSettings(split_mode="direct"))
            frag["route"]["final_override"] = "node-out"
            self._merged(self.config, frag)

    def test_fragment_shape_errors(self):
        frag = fragment()
        for key in ("mode", "route", "dns_patch", "outbound_patch"):
            bad = copy.deepcopy(frag)
            bad.pop(key)
            with self.assertRaises(usage_merge.UsageMergeError, msg=key):
                self._merged(self.config, bad)
        with self.assertRaises(usage_merge.UsageMergeError):
            self._merged(self.config, "not-a-dict")
        bad = copy.deepcopy(frag)
        bad["mode"] = "banana"
        with self.assertRaises(usage_merge.UsageMergeError):
            self._merged(self.config, bad)
        bad = copy.deepcopy(frag)
        bad["route"]["insert_before"] = "tail"     # 锚点漂移拒猜
        with self.assertRaises(usage_merge.UsageMergeError):
            self._merged(self.config, bad)

    def test_duplicate_rule_set_tag_errors(self):
        """tag 重复防线仍在（占位滤除不绕过它）：把片段条目的占位键删掉
        （= U3 落真规则后的形态）才参与重复检测；带占位键的条目在滤除后
        不与既有 tag 冲突（这正是方案 A 的预期行为）。"""
        frag = fragment(usage_model.UsageSettings(ad_block=True))
        for rs in frag["route"]["rule_set"]:
            rs.pop("prototype_placeholder", None)      # 模拟 U3 真条目形态
        bad = copy.deepcopy(self.config)
        bad["route"]["rule_set"] = [{"type": "local", "tag": "ads-blockset",
                                     "path": "x.srs"}]
        with self.assertRaises(usage_merge.UsageMergeError):
            self._merged(bad, frag)

    def test_duplicate_outbound_tag_errors(self):
        bad = copy.deepcopy(self.config)
        bad["outbounds"].append({"type": "direct", "tag": "direct-out"})
        with self.assertRaises(usage_merge.UsageMergeError):
            self._merged(bad,
                         fragment(usage_model.UsageSettings(split_mode="direct")))

    def test_inputs_never_mutated(self):
        config_keep = copy.deepcopy(self.config)
        frag = fragment(usage_model.UsageSettings(split_mode="direct"),
                        ["reject.example.com"])
        frag_keep = copy.deepcopy(frag)
        usage_merge.merge_usage_config(self.config, frag)
        self.assertEqual(self.config, config_keep)
        self.assertEqual(frag, frag_keep)


# ---------------------------------------------------------------------------
# 组 3：名单文件层（临时目录）
# ---------------------------------------------------------------------------
class BlocklistTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bl-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, site_blocklist.BLOCKLIST_FILENAME)

    def write_raw(self, payload):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(payload)


@unittest.skipUnless(_HAS_DEV, "client-dev 不在场，名单组跳过")
class TestBlocklistFile(BlocklistTestBase):

    def test_missing_file_returns_empty(self):
        self.assertEqual(site_blocklist.load_blocklist(self.path), ())

    def test_str_entry_defaults_reject_wrapper_shape(self):
        site_blocklist.save_blocklist(["ads.example.com"], self.path)
        with open(self.path, "r", encoding="utf-8") as f:
            wrapper = json.load(f)
        self.assertEqual(wrapper["version"], 1)
        self.assertEqual(wrapper["entries"],
                         [{"domain": "ads.example.com", "action": "reject"}])
        self.assertEqual(site_blocklist.load_blocklist(self.path),
                         ({"domain": "ads.example.com", "action": "reject"},))

    def test_roundtrip_lossless_bytes(self):
        entries = ["a.example.com",
                   {"domain": "b.example.com", "action": "direct"},
                   "a.example.com"]                  # 同条目重复 → 去重保序
        site_blocklist.save_blocklist(entries, self.path)
        with open(self.path, "r", encoding="utf-8") as f:
            first = f.read()
        loaded = site_blocklist.load_blocklist(self.path)
        self.assertEqual(loaded, ({"domain": "a.example.com", "action": "reject"},
                                  {"domain": "b.example.com", "action": "direct"}))
        site_blocklist.save_blocklist(loaded, self.path)
        with open(self.path, "r", encoding="utf-8") as f:
            second = f.read()
        self.assertEqual(first, second, "save(load(p)) 必须字节级一致（往返无损）")

    def test_loaded_entries_feed_build_route_config(self):
        """端到端：文件层 → 片段（reject 全模式 / direct 条目进 rule 路由）。"""
        bl = site_blocklist.load_blocklist(
            _prepare(self.path, ["reject.example.com",
                                 {"domain": "direct.example.com",
                                  "action": "direct"}]))
        frag = usage_model.build_route_config(
            usage_model.UsageSettings(split_mode="rule"), bl)
        rules = frag["route"]["rules"]
        self.assertTrue(any(r.get("action") == "reject" for r in rules))
        self.assertTrue(any(r.get("outbound") == "direct-out" for r in rules))

    def test_double_action_conflict_errors_on_save_and_load(self):
        conflicting = [{"domain": "x.example.com", "action": "reject"},
                       {"domain": "x.example.com", "action": "direct"}]
        with self.assertRaises(usage_model.UsageSettingsError):
            site_blocklist.save_blocklist(conflicting, self.path)
        self.write_raw(json.dumps({"version": 1, "entries": conflicting}))
        with self.assertRaises(usage_model.UsageSettingsError):
            site_blocklist.load_blocklist(self.path)

    def test_illegal_entries_errors(self):
        bad_sets = (
            ["https://ads.example.com"],           # scheme
            ["*.ads.example.com"],                 # 通配符
            ["例え.example.com"],                  # IDN 未转 punycode
            ["localhost"],                         # 单标签
            [{"domain": "a.example.com", "action": "drop"}],   # 未知处置
            [{"domain": "a.example.com", "extra": 1}],         # dict 多键
        )
        for bad in bad_sets:
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    site_blocklist.save_blocklist(bad, self.path)
                self.write_raw(json.dumps({"version": 1, "entries": bad}))
                with self.assertRaises(ValueError):
                    site_blocklist.load_blocklist(self.path)

    def test_corrupt_json_raises_not_silent_empty(self):
        self.write_raw("{ this is not json")
        with self.assertRaises(site_blocklist.SiteBlocklistError) as ctx:
            site_blocklist.load_blocklist(self.path)
        self.assertIn(self.path, str(ctx.exception), "报错须点名文件路径")

    def test_wrapper_structure_errors(self):
        cases = (
            "[]",                                             # 顶层非 dict
            json.dumps({"version": 1, "entries": [], "extra": 1}),  # 未知键
            json.dumps({"version": 1, "entries": "nope"}),    # entries 非列表
            json.dumps({"version": 2, "entries": []}),        # version 超前
            json.dumps({"version": 0, "entries": []}),        # version 落后
            json.dumps({"version": "1", "entries": []}),      # version 非整数
            json.dumps({"entries": []}),                      # 缺 version
        )
        for payload in cases:
            with self.subTest(payload=payload[:40]):
                self.write_raw(payload)
                with self.assertRaises(site_blocklist.SiteBlocklistError):
                    site_blocklist.load_blocklist(self.path)

    def test_atomic_write_no_tmp_leftover(self):
        site_blocklist.save_blocklist(["a.example.com"], self.path)
        self.assertEqual(sorted(os.listdir(self.tmp)),
                         [site_blocklist.BLOCKLIST_FILENAME])

    def test_path_follows_settings_store_dir(self):
        """路径从设置存储取，不写死：显式 data_dir > 内置默认目录
        （与 settings.json 同目录口径）。"""
        from pathlib import Path
        self.assertEqual(site_blocklist.blocklist_path("some/dir"),
                         Path("some/dir") / site_blocklist.BLOCKLIST_FILENAME)
        default = site_blocklist.blocklist_path()
        self.assertEqual(default.name, site_blocklist.BLOCKLIST_FILENAME)
        self.assertEqual(default.parent,
                         settings_store.settings_path().parent)


def _prepare(path, entries):
    site_blocklist.save_blocklist(entries, path)
    return path


# ---------------------------------------------------------------------------
# 组 4：设置存储（task12 §4 逐情形处置 + ③区接线机制）
# ---------------------------------------------------------------------------
class SettingsStoreTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="st-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "settings.json")

    def write_raw(self, payload):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(payload)

    def read_raw(self):
        with open(self.path, "r", encoding="utf-8") as f:
            return f.read()


@unittest.skipUnless(_HAS_DEV, "client-dev 不在场，存储组跳过")
class TestSettingsStore(SettingsStoreTestBase):

    def test_missing_file_returns_defaults(self):
        s = settings_store.load_settings(self.path)
        self.assertEqual(s, settings_proto.Settings())
        self.assertFalse(os.path.exists(self.path), "读缺失不得写回文件")

    def test_roundtrip_all_fields(self):
        s = settings_proto.Settings(
            split_mode="rule", ad_block=True, malware_block=True,
            dns_server="quad9", socks_port=7901, http_port=7902,
            data_dir="/tmp/other-dir", auto_probe_enabled=False,
            auto_probe_interval_min=20)
        settings_store.save_settings(s, self.path)
        self.assertEqual(settings_store.load_settings(self.path), s)
        wrapper = json.loads(self.read_raw())
        self.assertEqual(wrapper["version"], 1)
        self.assertEqual(wrapper["settings"], s.to_dict())

    def test_usage_dict_wiring_to_usage_settings(self):
        """task12 §6-2 消费路径：Settings → UsageSettings(**usage_dict())。"""
        s = settings_proto.Settings(split_mode="direct", ad_block=True,
                                    dns_server="quad9")
        u = usage_model.UsageSettings(**s.usage_dict())
        self.assertEqual(u.split_mode, "direct")
        self.assertTrue(u.ad_block)
        self.assertEqual(u.resolve_dns_ip(), "9.9.9.9")

    def test_merge_single_field_preserves_rest(self):
        """③区接线机制：merge 只覆盖显式字段——五项之外的六项原样保留。"""
        base = settings_proto.Settings(socks_port=7901, http_port=7902,
                                       data_dir="/tmp/other-dir",
                                       auto_probe_interval_min=20)
        merged = settings_proto.merge(base, {"ad_block": True})
        self.assertTrue(merged.ad_block)
        self.assertEqual(merged.socks_port, 7901)
        self.assertEqual(merged.http_port, 7902)
        self.assertEqual(merged.data_dir, "/tmp/other-dir")
        self.assertEqual(merged.auto_probe_interval_min, 20)
        self.assertEqual(merged.split_mode, "global")
        with self.assertRaises(settings_proto.SettingsError):
            settings_proto.merge(base, {"unknown_key": 1})

    def test_unknown_key_and_illegal_value_raise(self):
        self.write_raw(json.dumps({"version": 1, "settings": {"nope": 1}}))
        with self.assertRaises(settings_proto.SettingsError):
            settings_store.load_settings(self.path)
        self.write_raw(json.dumps({"version": 1,
                                   "settings": {"split_mode": "banana"}}))
        with self.assertRaises(settings_proto.SettingsError):
            settings_store.load_settings(self.path)

    def test_missing_fields_filled_with_defaults(self):
        self.write_raw(json.dumps({"version": 1, "settings": {"ad_block": True}}))
        s = settings_store.load_settings(self.path)
        self.assertTrue(s.ad_block)
        self.assertEqual(s.split_mode, "global")       # 其余补默认
        self.assertEqual(s.socks_port, 7898)

    def test_corrupt_raises_not_overwritten(self):
        raw = "{ corrupted"
        self.write_raw(raw)
        with self.assertRaises(settings_store.SettingsStoreError) as ctx:
            settings_store.load_settings(self.path)
        self.assertIn(self.path, str(ctx.exception))
        self.assertEqual(self.read_raw(), raw, "损坏文件不得被默认值盖掉")

    def test_wrapper_errors(self):
        cases = (
            "[]",
            json.dumps({"version": 1, "settings": {}, "extra": 1}),
            json.dumps({"version": 99, "settings": {}}),
            json.dumps({"version": "1", "settings": {}}),
            json.dumps({"settings": {}}),
            json.dumps({"version": 1, "settings": []}),
        )
        for payload in cases:
            with self.subTest(payload=payload[:40]):
                self.write_raw(payload)
                with self.assertRaises(settings_store.SettingsStoreError):
                    settings_store.load_settings(self.path)

    def test_save_rejects_non_settings(self):
        with self.assertRaises(settings_store.SettingsStoreError):
            settings_store.save_settings({"split_mode": "rule"}, self.path)

    def test_reset_keeps_corrupt_evidence(self):
        raw = "{ corrupted"
        self.write_raw(raw)
        fresh = settings_store.reset_settings(self.path)
        self.assertEqual(fresh, settings_proto.Settings())
        self.assertEqual(settings_store.load_settings(self.path),
                         settings_proto.Settings())
        backups = [n for n in os.listdir(self.tmp) if ".corrupt-" in n]
        self.assertEqual(len(backups), 1, "损坏原文件须改名留档")
        with open(os.path.join(self.tmp, backups[0]), "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), raw, "留档须是原始证据原文")

    def test_atomic_write_no_tmp_leftover(self):
        settings_store.save_settings(settings_proto.Settings(), self.path)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["settings.json"])

    def test_path_default_and_explicit(self):
        self.assertEqual(str(settings_store.settings_path()),
                         os.path.expanduser("~/.local/share/gate-client/settings.json"))
        self.assertEqual(str(settings_store.settings_path("db/覆盖目录")),
                         os.path.join("db", "覆盖目录", "settings.json"))


# ---------------------------------------------------------------------------
# 组 5：③区 offscreen（PySide6 缺席整组 skip，不伪造通过）
# ---------------------------------------------------------------------------
@unittest.skipUnless(_QT, "无 PySide6，③区 offscreen 组整组跳过"
                          "（原因记 DONE.md），不伪造通过")
class PanelTestBase(unittest.TestCase):
    def setUp(self):
        from client.ui.usage_rules_page import UsageRulesPanel
        self.UsageRulesPanel = UsageRulesPanel
        self.tmp = tempfile.mkdtemp(prefix="panel-test-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.path = os.path.join(self.tmp, "settings.json")
        settings_store.save_settings(settings_proto.Settings(), self.path)


class TestUsageRulesPanel(PanelTestBase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_panel(self, path=None):
        return self.UsageRulesPanel(settings_path=path or self.path)

    def stored(self, path=None):
        return settings_store.load_settings(path or self.path)

    # ---- 构造与初值 ----
    def test_construct_defaults_offscreen(self):
        p = self.make_panel()
        self.assertEqual(p.mode_combo.currentData(), "global")
        self.assertFalse(p.ad_chk.isChecked())
        self.assertFalse(p.mal_chk.isChecked())
        self.assertFalse(p.ipv6_chk.isChecked())
        self.assertEqual(p.dns_combo.currentData(), "google")
        self.assertIn("下次连接生效", p.note_label.text())
        self.assertIn("不做热重载", p.note_label.text())

    def test_controls_only_five_fields(self):
        """控件面恰为③区五项（work10 §2.2）：一个不多（端口/数据目录/
        自动探测/名单编辑一个不放）。"""
        p = self.make_panel()
        self.assertEqual(p.mode_combo.count(), 3)
        self.assertEqual(p.dns_combo.count(), 4)       # 三预设 + 自定义
        for name in ("socks", "http", "端口", "数据目录", "自动探测",
                     "名单", "黑名单编辑"):
            self.assertNotIn(name, p.note_label.text())

    # ---- 值落盘往返 ----
    def test_ad_toggle_persists(self):
        p = self.make_panel()
        p.ad_chk.setChecked(True)
        self.assertTrue(self.stored().ad_block)
        self.assertIn("已保存", p.status_label.text())
        self.assertIn("下次连接生效", p.status_label.text())

    def test_mode_combo_persists_all_three(self):
        p = self.make_panel()
        for i, mode in enumerate(("rule", "direct", "global")):
            idx = p.mode_combo.findData(mode)
            p.mode_combo.setCurrentIndex(idx)
            self.assertEqual(self.stored().split_mode, mode, f"档位 {mode}")

    def test_ipv6_toggle_persists(self):
        p = self.make_panel()
        p.ipv6_chk.setChecked(True)
        self.assertTrue(self.stored().ipv6)
        p.ipv6_chk.setChecked(False)
        self.assertFalse(self.stored().ipv6)

    def test_custom_dns_ip_persists(self):
        p = self.make_panel()
        p.dns_combo.setCurrentIndex(p.dns_combo.count() - 1)   # 自定义档
        p.dns_edit.setText(_DOC_DNS)
        p.dns_edit.editingFinished.emit()
        self.assertEqual(self.stored().dns_server, _DOC_DNS)
        # 预设档切回 → combo data 直写
        idx = p.dns_combo.findData("quad9")
        p.dns_combo.setCurrentIndex(idx)
        self.assertEqual(self.stored().dns_server, "quad9")

    # ---- 校验失败：红字 + 控件回退，不静默保存 ----
    def test_domain_dns_rejected_widget_reverts(self):
        p = self.make_panel()
        p.dns_combo.setCurrentIndex(p.dns_combo.count() - 1)
        p.dns_edit.setText("dns.example.com")          # 域名形态拒收
        p.dns_edit.editingFinished.emit()
        self.assertEqual(self.stored().dns_server, "google", "非法值不得落盘")
        self.assertIn("未保存", p.status_label.text())
        self.assertEqual(p.dns_combo.currentData(), "google", "控件回退存储值")

    def test_ipv6_off_with_v6_dns_reverts(self):
        p = self.make_panel()
        p.ipv6_chk.setChecked(True)                    # 先开 IPv6
        self.assertTrue(self.stored().ipv6)
        p.dns_combo.setCurrentIndex(p.dns_combo.count() - 1)
        p.dns_edit.setText("2001:db8::53")             # RFC 3849 文档段 v6
        p.dns_edit.editingFinished.emit()
        self.assertEqual(self.stored().dns_server, "2001:db8::53")
        p.ipv6_chk.setChecked(False)                   # 再关 IPv6 → 交叉校验拒
        self.assertTrue(self.stored().ipv6, "存储值不得被非法改动")
        self.assertTrue(p.ipv6_chk.isChecked(), "控件回退存储值")
        self.assertIn("未保存", p.status_label.text())

    # ---- 单字段写入：五项之外的原样保留 ----
    def test_single_field_write_preserves_other_nine(self):
        base = settings_proto.Settings(socks_port=7901, http_port=7902,
                                       data_dir="/tmp/other-dir",
                                       auto_probe_enabled=False,
                                       auto_probe_interval_min=20,
                                       malware_block=True)
        settings_store.save_settings(base, self.path)
        p = self.make_panel()
        self.assertTrue(p.mal_chk.isChecked())          # 初值来自存储
        p.ad_chk.setChecked(True)
        s = self.stored()
        self.assertTrue(s.ad_block)                     # 只改了这一项
        self.assertEqual(s.socks_port, 7901)
        self.assertEqual(s.http_port, 7902)
        self.assertEqual(s.data_dir, "/tmp/other-dir")
        self.assertFalse(s.auto_probe_enabled)
        self.assertEqual(s.auto_probe_interval_min, 20)
        self.assertTrue(s.malware_block)
        self.assertEqual(s.split_mode, "global")
        self.assertEqual(s.dns_server, "google")
        self.assertFalse(s.ipv6)

    def test_panel_is_no_second_copy_reload_from_storage(self):
        """唯一存储：另一面板实例从同一存储取值（不许出现两个可编辑副本）。"""
        p1 = self.make_panel()
        p1.mode_combo.setCurrentIndex(p1.mode_combo.findData("rule"))
        p2 = self.make_panel()
        self.assertEqual(p2.mode_combo.currentData(), "rule")
        self.assertTrue(p2.current_usage()["split_mode"] == "rule")

    # ---- 存储异常形态（task12 §4.2：不用默认值盖掉） ----
    def test_corrupt_storage_disables_panel_and_blocks_save(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ corrupted")
        raw_before = open(self.path, "r", encoding="utf-8").read()
        p = self.make_panel()
        self.assertIn("读取失败", p.status_label.text())
        self.assertFalse(p.mode_combo.isEnabled())
        self.assertFalse(p.ad_chk.isEnabled())
        p.ad_chk.setChecked(True)                       # 禁用态程序性触发仍会走管道
        self.assertEqual(open(self.path, "r", encoding="utf-8").read(),
                         raw_before, "损坏文件不得被任何保存覆盖")

    def test_reload_after_external_fix(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ corrupted")
        p = self.make_panel()
        self.assertFalse(p.mode_combo.isEnabled())
        settings_store.save_settings(
            settings_proto.Settings(split_mode="rule"), self.path)
        p.reload()
        self.assertTrue(p.mode_combo.isEnabled())
        self.assertEqual(p.mode_combo.currentData(), "rule")


# ---------------------------------------------------------------------------
# 组 6：sing-box check（二进制不在场 skip 并记 DONE.md）
# ---------------------------------------------------------------------------
def _find_singbox_binary():
    """沿用引擎 find_binary 的探测链（env → ~/.local/bin → which）。"""
    if not _HAS_DEV:
        return ""
    return SingBoxEngine().find_binary()


@unittest.skipUnless(_HAS_DEV and _find_singbox_binary(),
                     "sing-box 二进制不在场（沿用既有引擎测试口径："
                     "skip 并在 DONE.md 记原因，不伪造通过）")
class TestSingBoxCheck(unittest.TestCase):
    """合并后配置过 sing-box check：默认片段 / direct 模式 / global+reject 名单。
    （rule 模式含占位 rule_set 条目——路径系 U3 真条目核定前的占位文件，
    不在本组范围；占位键口径见 DONE.md 未解决项。）"""

    @classmethod
    def setUpClass(cls):
        cls.binary = _find_singbox_binary()
        cls.engine = SingBoxEngine()

    def _check(self, config):
        tmp = tempfile.mkdtemp(prefix="check-test-")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "config.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        import subprocess
        proc = subprocess.run([self.binary, "check", "-c", path],
                              capture_output=True, text=True, timeout=30)
        self.assertEqual(
            proc.returncode, 0,
            f"sing-box check 未通过: {(proc.stdout + proc.stderr)[:400]}")

    def test_merged_default_fragment_passes_check(self):
        config = self.engine.build_config(make_node("vless"))
        self._check(usage_merge.merge_usage_config(config, fragment()))

    def test_merged_direct_mode_passes_check(self):
        config = self.engine.build_config(make_node("vless"))
        merged = usage_merge.merge_usage_config(
            config, fragment(usage_model.UsageSettings(split_mode="direct"),
                             ["reject.example.com"]))
        self._check(merged)

    def test_merged_global_blocklist_custom_dns_passes_check(self):
        config = self.engine.build_config(make_node("vless"))
        merged = usage_merge.merge_usage_config(
            config,
            fragment(usage_model.UsageSettings(dns_server=_DOC_DNS),
                     ["reject.example.com"]))
        self._check(merged)

    def test_merged_tun_with_usage_rules_passes_check(self):
        engine = SingBoxEngine(takeover_mode="tun")
        config = engine.build_config(make_node("vless"))
        merged = usage_merge.merge_usage_config(
            config, fragment(usage_model.UsageSettings(ad_block=True)))
        self._check(merged)


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# 集成适配清单（审查方落位 client-dev 时对测试做以下 N 处适配，其余用例
# 与原件逐行一致——沿用 work13–work16 既有口径）：
# 1. 删 client.core / client.ui 的 __path__ 内存挂载（«仅测试进程内存生效»
#    块）：四个交付模块落真包后直接可见；
# 2. `_find_client_dev` 的兜底路径改 client-dev 树内口径（与 work16 同款），
#    CLIENT_DEV 环境变量口径保留；
# 3. `usage_model_usage_protocols()` 里 SINGBOX_PROTOCOLS 直接顶层 import；
# 4. 若 task12 设置页集成段已实现持久化，settings_store 用例改指向集成版
#    （口径以 task12 DESIGN §4 为准，差异报 DONE）。——审查注记：task24 设置页
#    集成段尚未落地，本项不适用；settings_store.py 本身即 task12 §4 的落位。
# 5. TestDeliverables 按 _DELIV 映射读集成树落位（审查方适配，见 _DELIV 注释）；
#    DESIGN.md 落 client/core/DESIGN-usage.md（client/core/DESIGN.md 已是 task15 的）。
# ---------------------------------------------------------------------------
