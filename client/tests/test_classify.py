#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task11 分类原型离线测试。假数据 only：主机名一律 *.example，无真实
节点地址/订阅内容/密钥；ISP 名称全部用尽人皆知厂商名（简报红线）。

跑法：cd client-dev && python3 -m unittest client.tests.test_classify
"""

import copy
import json
import unittest

from client.core import classify_proto as cp  # noqa: E402


def _node(nid, source, protocol, org=None, asn=None, extra=None, name=None):
    """构造假节点记录（字段口径对齐 client/core/model.py 的 Node.to_dict）。"""
    node = {"id": nid, "name": name or nid, "protocol": protocol,
            "source": source}
    if org or asn:
        node["exit_profile"] = {"org": org, "asn": asn,
                                "network_type": "unknown"}
    if extra:
        node["extra"] = dict(extra)
    return node


# ---------------------------------------------------------------------------
# 1. classify_exit：三态判定
# ---------------------------------------------------------------------------
class TestClassifyExit(unittest.TestCase):

    def test_residential_hit(self):
        self.assertEqual(cp.classify_exit("NTT Communications", ""),
                         "residential")
        self.assertEqual(cp.classify_exit("", "AS7922 COMCAST"),
                         "residential")
        self.assertEqual(cp.classify_exit("Comcast Cable", None),
                         "residential")
        self.assertEqual(cp.classify_exit("China Telecom Shanghai", ""),
                         "residential")

    def test_datacenter_hit(self):
        self.assertEqual(cp.classify_exit("DigitalOcean LLC", ""),
                         "datacenter")
        self.assertEqual(cp.classify_exit(None, "AS16509 AMAZON-EC2"),
                         "datacenter")
        self.assertEqual(cp.classify_exit("Hetzner Online GmbH", ""),
                         "datacenter")

    def test_unknown_no_hit(self):
        # 表外 ISP → unknown；无数据 → unknown。三态里 unknown 不许被上层猜掉。
        self.assertEqual(cp.classify_exit("Acme Broadband Co",
                                          "AS64512 ACME"), "unknown")
        self.assertEqual(cp.classify_exit("", ""), "unknown")
        self.assertEqual(cp.classify_exit(None, None), "unknown")

    def test_case_insensitive(self):
        self.assertEqual(cp.classify_exit("digitalocean llc", ""),
                         "datacenter")
        self.assertEqual(cp.classify_exit("ntt east", ""), "residential")

    def test_datacenter_first_matches_client_order(self):
        # 两参数合并匹配、机房关键词先查——与 verify.classify_network 同序。
        self.assertEqual(cp.classify_exit(
            "NTT Communications / AWS Direct Connect", ""), "datacenter")

    def test_exception_table(self):
        # GOOGLE FIBER 是住宅宽带，例外表先于 GOOGLE 机房关键词。
        self.assertEqual(cp.classify_exit("Google Fiber", ""), "residential")


# ---------------------------------------------------------------------------
# 2. dims_of：原始记录 → 三维
# ---------------------------------------------------------------------------
class TestDimsOf(unittest.TestCase):

    def test_vpngate_always_free(self):
        self.assertEqual(cp.dims_of(_node("a", "vpngate", "vless"))
                         ["source_nature"], "free")

    def test_legacy_source_keys_map_to_vpngate(self):
        # task8 合并前的旧库（openvpn / vpngate_feed 两键）同样恒为免费。
        self.assertEqual(cp.dims_of(_node("b", "openvpn", "openvpn"))
                         ["source_nature"], "free")
        self.assertEqual(cp.dims_of(_node("c", "vpngate_feed", "vless"))
                         ["source_nature"], "free")

    def test_pipeline_always_pipeline(self):
        self.assertEqual(cp.dims_of(_node("d", "pipeline", "trojan"))
                         ["source_nature"], "pipeline")

    def test_manual_collected_always_free(self):
        # task9 搜集入库：manual 页 + extra.collected_from → 恒为免费。
        n = _node("e", "manual", "vless",
                  extra={"collected_from": "https://collect.example/list"})
        self.assertEqual(cp.dims_of(n)["source_nature"], "free")

    def test_manual_nature_mark(self):
        self.assertEqual(cp.dims_of(_node("f", "manual", "vless",
                                          extra={"nature": "paid"}))
                         ["source_nature"], "paid")
        self.assertEqual(cp.dims_of(_node("g", "manual", "vless",
                                          extra={"nature": "free"}))
                         ["source_nature"], "free")

    def test_manual_unmarked_is_none(self):
        self.assertIsNone(cp.dims_of(_node("h", "manual", "vless"))
                          ["source_nature"])

    def test_collected_beats_nature_mark(self):
        # 搜集来的恒为免费：性质标注不覆盖硬判定。
        n = _node("i", "manual", "vless", extra={"collected_from": "x",
                                                 "nature": "paid"})
        self.assertEqual(cp.dims_of(n)["source_nature"], "free")

    def test_nature_mark_ignored_for_vpngate(self):
        # VPNGate 恒为免费：extra.nature 对非 manual 来源无效。
        n = _node("j", "vpngate", "vless", extra={"nature": "paid"})
        self.assertEqual(cp.dims_of(n)["source_nature"], "free")

    def test_explicit_dims_win(self):
        # 调用方已算好三维时直接采信（显式键优先于推导）。
        n = {"id": "k", "source": "vpngate", "protocol": "vless",
             "source_nature": "paid", "exit_kind": "datacenter",
             "form": "proxy"}
        dims = cp.dims_of(n)
        self.assertEqual(dims["source_nature"], "paid")
        self.assertEqual(dims["exit_kind"], "datacenter")

    def test_invalid_explicit_dim_falls_back(self):
        # 非法显式值当作没给：manual 未标 → None。
        n = {"id": "l", "source": "manual", "protocol": "vless",
             "source_nature": "vip"}
        self.assertIsNone(cp.dims_of(n)["source_nature"])

    def test_exit_kind_from_verified_profile(self):
        n = _node("m", "vpngate", "vless", org="DigitalOcean LLC",
                  asn="AS14061 DIGITALOCEAN")
        self.assertEqual(cp.dims_of(n)["exit_kind"], "datacenter")

    def test_exit_kind_no_data_is_unknown(self):
        n = _node("n", "vpngate", "vless")   # 未验真，无画像
        self.assertEqual(cp.dims_of(n)["exit_kind"], "unknown")

    def test_exit_override_beats_auto(self):
        # 人工改判优先于自动判定（复查不覆盖改判，见 DESIGN §6）。
        n = _node("o", "manual", "vless", org="DigitalOcean LLC",
                  extra={"exit_kind_override": "residential"})
        self.assertEqual(cp.dims_of(n)["exit_kind"], "residential")

    def test_form_from_protocol(self):
        self.assertEqual(cp.dims_of(_node("p", "vpngate", "openvpn"))["form"],
                         "openvpn")
        self.assertEqual(cp.dims_of(_node("q", "vpngate", "trojan"))["form"],
                         "proxy")

    def test_form_from_config_format_when_protocol_missing(self):
        # task8：extra.config_format 只在 OpenVPN 载荷节点上存在。
        n = {"id": "r", "source": "manual", "extra": {"config_format": "b64"}}
        self.assertEqual(cp.dims_of(n)["form"], "openvpn")

    def test_form_missing(self):
        self.assertIsNone(cp.dims_of({"id": "s", "source": "manual"})["form"])

    def test_dims_of_does_not_mutate(self):
        n = _node("t", "manual", "vless", org="NTT WEST",
                  extra={"nature": "paid"})
        snapshot = copy.deepcopy(n)
        cp.dims_of(n)
        self.assertEqual(n, snapshot)

    def test_non_dict_raises(self):
        with self.assertRaises(TypeError):
            cp.dims_of(["not", "a", "dict"])


# ---------------------------------------------------------------------------
# 3. board_of：三维组合规则（六板块 + 未分类兜底）
# ---------------------------------------------------------------------------
class TestBoardOf(unittest.TestCase):

    def test_six_boards_hit(self):
        cases = [
            ({"source_nature": "free", "exit_kind": "residential",
              "form": "proxy"}, "free_residential"),
            ({"source_nature": "free", "exit_kind": "datacenter",
              "form": "proxy"}, "free_datacenter"),
            ({"source_nature": "paid", "exit_kind": "residential",
              "form": "proxy"}, "paid_residential"),
            ({"source_nature": "paid", "exit_kind": "datacenter",
              "form": "proxy"}, "paid_datacenter"),
            ({"source_nature": "free", "exit_kind": "datacenter",
              "form": "openvpn"}, "openvpn"),
            ({"source_nature": "pipeline", "exit_kind": "datacenter",
              "form": "proxy"}, "pipeline"),
        ]
        for dims, expect in cases:
            self.assertEqual(cp.board_of(dict(dims)), expect, dims)

    def test_pipeline_precedence_over_openvpn(self):
        # P1 > P2：自家流水线的 OpenVPN 形态产物归私人流水线板块。
        self.assertEqual(cp.board_of({"source_nature": "pipeline",
                                      "exit_kind": "residential",
                                      "form": "openvpn"}), "pipeline")

    def test_openvpn_needs_only_form(self):
        # OpenVPN 板块是形态定义：来源性质、出口性质未知也照归（不堆未分类）。
        self.assertEqual(cp.board_of({"form": "openvpn"}), "openvpn")

    def test_pipeline_needs_only_nature(self):
        self.assertEqual(cp.board_of({"source_nature": "pipeline"}),
                         "pipeline")

    def test_missing_exit_kind_is_unclassified(self):
        self.assertEqual(cp.board_of({"source_nature": "free",
                                      "form": "proxy"}), "unclassified")
        self.assertEqual(cp.board_of({"source_nature": "paid",
                                      "exit_kind": "unknown",
                                      "form": "proxy"}), "unclassified")

    def test_missing_form_is_unclassified(self):
        self.assertEqual(cp.board_of({"source_nature": "free",
                                      "exit_kind": "residential"}),
                         "unclassified")

    def test_missing_nature_is_unclassified(self):
        self.assertEqual(cp.board_of({"exit_kind": "residential",
                                      "form": "proxy"}), "unclassified")

    def test_invalid_values_are_unclassified(self):
        self.assertEqual(cp.board_of({"source_nature": "vip",
                                      "exit_kind": "residential",
                                      "form": "proxy"}), "unclassified")
        self.assertEqual(cp.board_of({"source_nature": "free",
                                      "exit_kind": "villa",
                                      "form": "proxy"}), "unclassified")
        self.assertEqual(cp.board_of({}), "unclassified")

    def test_raw_node_dict_accepted(self):
        # 缺三维的原始记录先推导再组合。
        self.assertEqual(cp.board_of(_node("u", "vpngate", "vless",
                                           org="NTT EAST")),
                         "free_residential")

    def test_board_of_does_not_mutate(self):
        n = _node("v", "manual", "vless", org="NTT WEST",
                  extra={"nature": "paid"})
        snapshot = copy.deepcopy(n)
        cp.board_of(n)
        self.assertEqual(n, snapshot)


# ---------------------------------------------------------------------------
# 4. classify_library：整库批量分类
# ---------------------------------------------------------------------------
class TestClassifyLibrary(unittest.TestCase):

    def _library(self):
        return [
            _node("n1", "vpngate", "vless", org="NTT Communications"),
            _node("n2", "vpngate", "ss", org="DigitalOcean LLC"),
            _node("n3", "vpngate", "openvpn", org="Hetzner Online"),
            _node("n4", "pipeline", "trojan", org="OVH SAS"),
            _node("n5", "manual", "vless", org="China Telecom",
                  extra={"nature": "paid"}),
            _node("n6", "manual", "ss", org="Amazon AWS",
                  extra={"nature": "paid"}),
            _node("n7", "vpngate", "openvpn"),                 # 未验真 → 仍归 OpenVPN
            _node("n8", "manual", "vless", org="Comcast Cable",
                  extra={"collected_from": "https://collect.example/list"}),
            _node("n9", "manual", "vless", org="NTT WEST"),    # 存量未标性质
            _node("n10", "vpngate", "vless"),                  # 无出口数据
            _node("n11", "vpngate", "vless",
                  org="Unknown Regional ISP Co"),              # 表外 ISP
            _node("n12", "pipeline", "vless"),                 # 无出口 → 仍归流水线
            _node("n13", "manual", "openvpn", extra={"nature": "paid"}),
            _node("n14", "manual", "vless", org="NTT Communications",
                  extra={"nature": "paid",
                         "exit_kind_override": "datacenter"}),
        ]

    def test_counts_match_hand_expectation(self):
        report = cp.classify_library(self._library())
        self.assertEqual(report["total"], 14)
        self.assertEqual(report["counts"], {
            "free_residential": 2,   # n1, n8
            "free_datacenter": 1,    # n2
            "openvpn": 3,            # n3, n7, n13（形态单列，出口未知不堆未分类）
            "pipeline": 2,           # n4, n12
            "paid_residential": 1,   # n5
            "paid_datacenter": 2,    # n6, n14（n14 人工改判生效）
            "unclassified": 3,       # n9, n10, n11
        })

    def test_board_memberships(self):
        report = cp.classify_library(self._library())
        self.assertEqual(report["boards"]["free_residential"], ["n1", "n8"])
        self.assertEqual(report["boards"]["openvpn"], ["n3", "n7", "n13"])
        self.assertEqual(report["boards"]["paid_datacenter"], ["n6", "n14"])

    def test_unclassified_list_with_reasons(self):
        report = cp.classify_library(self._library())
        items = {it["id"]: it for it in report["unclassified"]}
        self.assertEqual(set(items), {"n9", "n10", "n11"})
        self.assertIn("未标性质", " ".join(items["n9"]["missing"]))
        self.assertIn("无验真出口", " ".join(items["n10"]["missing"]))
        self.assertIn("未命中归类表", " ".join(items["n11"]["missing"]))

    def test_library_not_mutated(self):
        nodes = self._library()
        snapshot = json.dumps(nodes, sort_keys=True, ensure_ascii=False)
        cp.classify_library(nodes)
        self.assertEqual(json.dumps(nodes, sort_keys=True,
                                    ensure_ascii=False), snapshot)

    def test_pre_annotated_dims_tolerated(self):
        # 已带三维的记录（后台批量补标产物）直接进组合规则。
        report = cp.classify_library([
            {"id": "x1", "source_nature": "free", "exit_kind": "residential",
             "form": "proxy"},
            {"id": "x2", "source_nature": "pipeline"},
        ])
        self.assertEqual(report["counts"]["free_residential"], 1)
        self.assertEqual(report["counts"]["pipeline"], 1)

    def test_empty_library(self):
        report = cp.classify_library([])
        self.assertEqual(report["total"], 0)
        self.assertEqual(report["counts"]["unclassified"], 0)
        self.assertEqual(report["unclassified"], [])

    def test_non_dict_entry_raises(self):
        with self.assertRaises(TypeError):
            cp.classify_library(["nope"])


# ---------------------------------------------------------------------------
# 5. 归类表扩充：旧判定不回退
# ---------------------------------------------------------------------------
class TestTableExtension(unittest.TestCase):

    def test_extend_does_not_regress(self):
        before = {
            "digitalocean": cp.classify_exit("DigitalOcean LLC", ""),
            "ntt": cp.classify_exit("NTT EAST", ""),
            "fastweb": cp.classify_exit("Fastweb SpA", ""),   # 住宅表既有条目
            "acme": cp.classify_exit("Acme Broadband", ""),   # 表外 → unknown
        }
        orig_dc, orig_res = cp.DATACENTER_KEYWORDS, cp.RESIDENTIAL_KEYWORDS
        # 扩充：新增尽人皆知厂商（SCALEWAY 机房 / TELEFONICA 住宅）
        cp.DATACENTER_KEYWORDS = orig_dc + ("SCALEWAY",)
        cp.RESIDENTIAL_KEYWORDS = orig_res + ("TELEFONICA",)
        try:
            # 旧判定不回退：已判的样本一个不许变
            self.assertEqual(cp.classify_exit("DigitalOcean LLC", ""),
                             before["digitalocean"])
            self.assertEqual(cp.classify_exit("NTT EAST", ""), before["ntt"])
            self.assertEqual(cp.classify_exit("Fastweb SpA", ""),
                             before["fastweb"])
            # 不与新条目碰撞的 unknown 仍 unknown
            self.assertEqual(cp.classify_exit("Acme Broadband", ""),
                             before["acme"])
            # 新条目按预期生效
            self.assertEqual(cp.classify_exit("Scaleway SAS", ""),
                             "datacenter")
            self.assertEqual(cp.classify_exit("Telefonica de Espana", ""),
                             "residential")
        finally:
            cp.DATACENTER_KEYWORDS, cp.RESIDENTIAL_KEYWORDS = orig_dc, orig_res
        # 恢复后回到原判定（表是纯数据，可回滚）
        self.assertEqual(cp.classify_exit("Scaleway SAS", ""), "unknown")
        self.assertEqual(cp.classify_exit("DigitalOcean LLC", ""),
                         "datacenter")


# ---------------------------------------------------------------------------
# 6. 常量与铁律防线
# ---------------------------------------------------------------------------
class TestConstantsAndIronLaw(unittest.TestCase):

    def test_six_board_labels(self):
        # 主人定的六个板块名一字不差，键-标签字典齐全。
        self.assertEqual(len(cp.BOARDS), 6)
        self.assertEqual(sorted(cp.BOARD_LABELS.keys()), sorted(cp.BOARDS))
        self.assertEqual(sorted(cp.BOARD_LABELS.values()),
                         sorted(["免费住宅", "免费机房", "OpenVPN",
                                 "私人流水线", "付费住宅", "付费机房"]))
        self.assertNotIn(cp.UNCLASSIFIED, cp.BOARDS)

    def test_name_host_latency_never_decide_exit(self):
        # 铁律：只认验真出口 ISP 数据。名字/主机名/延迟一个都不许参与判定。
        n = _node("home", "vpngate", "vless", name="住宅家宽超快节点")
        n["host"] = "home-residential.example"
        n["metrics"] = {"latency_ms": 15.0, "speed_mbps": 88.0}
        self.assertEqual(cp.board_of(n), "unclassified")
        report = cp.classify_library([n])
        self.assertIn("无验真出口",
                      " ".join(report["unclassified"][0]["missing"]))

    def test_deceptive_name_does_not_flip(self):
        # 名字喊「机房」也改变不了 NTT 住宅判定（classify_exit 只看 ISP 串）。
        self.assertEqual(cp.classify_exit("机房专用 NTT WEST", ""),
                         "residential")

    def test_board_not_in_persisted_model(self):
        # 板块是派生值不落库：classify_proto 全模块不得出现写库/写文件语义。
        import inspect
        src = inspect.getsource(cp)
        for forbidden in ("INSERT", "UPDATE ", "DELETE ", "open(", "write("):
            self.assertNotIn(forbidden, src)


if __name__ == "__main__":
    unittest.main()
