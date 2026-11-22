#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""source_registry 离线测试：登记增删查、首次登记导入、二次更新合并
（新增/消失/保留样本）、一键更新失败库不变、feed 戳归属范围、回归清理。
（task23 落页改写：feed_mirror 类型废除，feed 落 VPNGate 合并页——
自 task23 起行为反转、依据 work8/DESIGN §7；「手导永不标消失」「同端点
合并保验真」两个语义原样保留，work8/DESIGN §5.2。）
按任务书红线：拉取动作全部注入假拉取函数，样本全部自构造
（example.com / RFC 5737 文档段 / 假 uuid / 假密钥），零联网、不读用户
真实订阅地址或文件、无自动测速/自动选择逻辑。"""
import base64
import hashlib
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")))

from client.sources import source_registry as sr  # noqa: E402
from client.core.model import ExitProfile, make_node_id  # noqa: E402
from client.core.store import Store, VerifySample  # noqa: E402
from client.sources import manual  # noqa: E402

# ---------------- 自构造样本（全部假数据） ----------------
SUB_URL = "https://sub.example.com/fake-sub"

VLESS1 = ("vless://11111111-2222-3333-4444-555555555555"
          "@a.example.com:443?sni=b.example.com&security=tls#节点一")
SS1 = "ss://aes-256-gcm:fake-ss-pw@e.example.com:8388#节点三"
SSTP1 = "sstp://user1:fakepw@f.example.com:443#节点四"
TROJAN1 = "trojan://fake-pw@c.example.com:443#节点二"
LINKS_B1 = "\n".join([VLESS1, SS1, SSTP1])      # 首次导入
LINKS_B2 = "\n".join([VLESS1, TROJAN1])         # 二次：SS/SSTP 消失
LINKS_B3 = "\n".join([VLESS1, SSTP1])           # 三次：SSTP 回归

VLESS_ID = make_node_id("manual", "vless", "a.example.com", 443,
                        "11111111-2222-3333-4444-555555555555")
SS_ID = make_node_id("manual", "ss", "e.example.com", 8388)
SSTP_ID = make_node_id("manual", "sstp", "f.example.com", 443)
TROJAN_ID = make_node_id("manual", "trojan", "c.example.com", 443, "fake-pw")

FEED_HEADER = ("HostName,IP,Score,Ping,Speed,CountryLong,CountryShort,"
               "NumVpnSessions,Uptime,OpenVPN_ConfigData_Base64")


def ovpn_b64(host, port=443, proto="udp"):
    cfg = f"client\ndev tun\nproto {proto}\nremote {host} {port}\nresolv-retry infinite\n"
    return base64.b64encode(cfg.encode()).decode()


def feed_row(host, ip, sessions=10, speed=8000000):
    return ",".join([host, ip, "100", "50", str(speed), "Fake Country", "FC",
                     str(sessions), "86400000", ovpn_b64(host)])


def feed_csv(*rows):
    return FEED_HEADER + "\n" + "\n".join(rows) + "\n"


def mirror_json(*hosts_ips):
    servers = [{"hostname": h, "ip": ip, "score": 100, "ping": 50,
                "speed": 8000000, "countrylong": "Fake Country",
                "countryshort": "FC", "numvpnsessions": 10,
                "uptime": 86400000, "openvpn_configdata_base64": ovpn_b64(h)}
               for h, ip in hosts_ips]
    return json.dumps({"servers": servers})


F1, F2, F3, F4 = ("fake1.opengw.net", "fake2.opengw.net",
                  "fake3.opengw.net", "fake4.opengw.net")
# task23 落页：feed 解析默认 source=vpngate（旧 vpngate_feed 键退役）
F1_ID = make_node_id("vpngate", "openvpn", F1, 443)
F2_ID = make_node_id("vpngate", "openvpn", F2, 443)
F3_ID = make_node_id("vpngate", "openvpn", F3, 443)


def snap(store):
    """整库快照（含拉黑），逐字节可比——失败库不变的判定基准。"""
    return sorted(n.to_json() for n in store.list_nodes(include_banned=True))


def sub_id_of(reg):
    return reg.entries()[0]["id"]


def mark_verified(store, node_id):
    """模拟 verify.py 写在节点行上的本地验真状态（exit_profile、实测延迟、
    出口改名 + orig_name、隔离标记），口径对齐 core/verify.py。"""
    n = store.get_node(node_id)
    n.exit_profile = ExitProfile(ip="203.0.113.9", country="Fake Country",
                                 country_code="FC", verified_at=1000.0)
    n.metrics.latency_ms = 123.0
    n.metrics.jitter_ms = 5.0
    n.extra["orig_name"] = n.name
    n.name = "FC-实测出口改名"
    n.extra["quarantined"] = "1"
    n.extra["quarantine_reason"] = "abuser"
    store.upsert_node(n)


def sub_scenario(store, texts):
    """登记假订阅 + 依次喂多批内容，返回 (registry, entry_id, reports)。"""
    reg = sr.SourceRegistry()
    entry = reg.register("subscription", SUB_URL, name="假订阅", now=1000.0)
    batches = iter(texts)

    def fetch(e):
        assert e["address"] == SUB_URL       # 拉取拿到的是登记的地址
        return next(batches)

    reports = [sr.update_entry(reg, store, entry["id"], fetch_fn=fetch,
                               now=1000.0)]
    return reg, entry["id"], reports


class TestRegistryCrud(unittest.TestCase):
    """登记的增删查与约束（DESIGN §3）。"""

    def setUp(self):
        self.reg = sr.SourceRegistry()

    def test_register_subscription_entry(self):
        e = self.reg.register("subscription", SUB_URL, name="假订阅")
        self.assertEqual(e["type"], "subscription")
        self.assertEqual(e["page"], "manual")          # 来源页归属由 type 定死
        self.assertTrue(e["id"].startswith("sub-"))
        self.assertEqual(e["id"], "sub-" + hashlib.sha1(
            SUB_URL.encode()).hexdigest()[:8])         # 同地址永远同一 id
        self.assertEqual(e["address"], SUB_URL)
        self.assertTrue(e["enabled"])

    def test_register_subscription_rejects_bad_address(self):
        for bad in ("", "ftp://x.example.com/s", "not-a-url", "gopher://y"):
            with self.assertRaises(ValueError):
                self.reg.register("subscription", bad)

    def test_register_feed_singleton_default_address(self):
        e = self.reg.register("feed")
        self.assertEqual(e["address"], sr.FEED_DEFAULT_ADDRESS)  # 自动填官方
        self.assertEqual(e["page"], "vpngate")   # 落 VPNGate 合并页（task23）
        with self.assertRaises(ValueError):              # feed 单例
            self.reg.register("feed")

    def test_feed_mirror_type_abolished(self):
        """自 task23 起行为反转、依据 work8/DESIGN §7（§5.1）：feed_mirror
        登记类型废除，两入口合一为 feed（同一份 feed 落 VPNGate 合并页，
        不再落「OpenVPN 页」——该页键已随合并退役）。"""
        reg = sr.SourceRegistry()
        with self.assertRaises(ValueError):              # 类型已从 ENTRY_TYPES 删除
            reg.register("feed_mirror")
        self.assertEqual(sr.ENTRY_TYPES, ("subscription", "feed"))

    def test_register_duplicate_subscription_url(self):
        self.reg.register("subscription", SUB_URL)
        with self.assertRaises(ValueError):
            self.reg.register("subscription", SUB_URL)

    def test_remove_entry_keeps_nodes(self):
        store = Store(":memory:")
        reg, eid, _ = sub_scenario(store, [LINKS_B1])
        self.assertEqual(store.count_nodes(), 3)
        self.assertTrue(reg.remove(eid))
        self.assertFalse(reg.remove(eid))                # 再删返回 False
        self.assertIsNone(reg.get(eid))
        self.assertEqual(store.count_nodes(), 3)         # 节点原地不动
        n = store.get_node(VLESS_ID)
        self.assertNotIn("source_gone", n.extra)         # 也不顺手标消失
        self.assertEqual(reg.last_update(eid), None)     # 记账随条目清掉

    def test_registry_persistence_roundtrip(self):
        store = Store(":memory:")
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "registry.json")
            reg = sr.SourceRegistry(path)
            eid = reg.register("subscription", SUB_URL, name="假订阅")["id"]
            sr.update_entry(reg, store, eid,
                            fetch_fn=lambda e: LINKS_B1, now=2000.0)
            reg2 = sr.SourceRegistry(path)               # 重开
            e = reg2.get(eid)
            self.assertEqual(e["address"], SUB_URL)
            lu = reg2.last_update(eid)
            self.assertTrue(lu["ok"])
            self.assertEqual((lu["added"], lu["fetched"]), (3, 3))

    def test_disabled_entry_blocks_update(self):
        store = Store(":memory:")
        reg = sr.SourceRegistry()
        eid = reg.register("feed")["id"]
        reg.set_enabled(eid, False)
        with self.assertRaises(ValueError):
            sr.update_entry(reg, store, eid, fetch_fn=lambda e: feed_csv(
                feed_row(F1, "192.0.2.11")))
        self.assertEqual(store.count_nodes(), 0)


class TestFirstImport(unittest.TestCase):
    """首次登记导入（DESIGN §4.1 ①②③④ 全链路）。"""

    def test_subscription_first_import_plain(self):
        store = Store(":memory:")
        reg, eid, reports = sub_scenario(store, [LINKS_B1])
        rep = reports[0]
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual((rep.added, rep.updated, rep.gone, rep.skipped),
                         (3, 0, 0, 0))
        self.assertEqual(rep.parser, "format_import.import_any_text")
        nodes = store.list_nodes(source="manual")
        self.assertEqual(len(nodes), 3)
        n = store.get_node(VLESS_ID)
        self.assertEqual(n.extra["registry_entry"], eid)           # 登记戳
        self.assertEqual(n.extra["subscription_url"], SUB_URL)     # 溯源
        self.assertEqual(n.extra["subscription_name"], "假订阅")
        lu = reg.last_update(eid)
        self.assertEqual((lu["ok"], lu["added"], lu["ts"]), (True, 3, 1000.0))

    def test_subscription_first_import_b64(self):
        blob = base64.b64encode(LINKS_B1.encode()).decode()
        text = "# 订阅说明头（注释行不该挡住 base64）\n" + \
               "\n".join(blob[i:i + 60] for i in range(0, len(blob), 60))
        store = Store(":memory:")
        reg, eid, reports = sub_scenario(store, [text])
        rep = reports[0]
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual((rep.added, rep.skipped), (3, 0))  # task2 消除 F2 的口径
        self.assertEqual(store.count_nodes(), 3)

    def test_feed_first_import_csv(self):
        store = Store(":memory:")
        reg = sr.SourceRegistry()
        eid = reg.register("feed", now=1000.0)["id"]
        csv_text = feed_csv(feed_row(F1, "192.0.2.11"),
                            feed_row(F2, "192.0.2.12"),
                            feed_row(F3, "192.0.2.13"))
        rep = sr.update_entry(reg, store, eid,
                              fetch_fn=lambda e: csv_text.encode("utf-16"),
                              now=1000.0)
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual((rep.added, rep.feed_from), (3, "登记地址"))
        self.assertEqual(rep.parser, "feed.parse_feed_to_nodes")
        nodes = store.list_nodes(source="vpngate")
        self.assertEqual({n.id for n in nodes},
                         {F1_ID, F2_ID, F3_ID})
        n = store.get_node(F1_ID)
        self.assertEqual(n.protocol, "openvpn")
        self.assertEqual(n.extra["registry_entry"], "feed")
        self.assertEqual(n.metrics.sessions, 10)
        self.assertEqual(n.metrics.speed_mbps, 8.0)
        self.assertEqual(n.extra["country"], "Fake Country")
        self.assertEqual(n.extra["country_code"], "FC")
        self.assertTrue(n.config_ref.startswith("Y2xpZW50"))  # b64 配置原文

class TestSecondUpdate(unittest.TestCase):
    """二次更新：新增 / 消失 / 更新计数与本地状态保住（DESIGN §5、§8）。"""

    def setUp(self):
        self.store = Store(":memory:")
        self.reg, self.eid, _ = sub_scenario(self.store, [LINKS_B1])
        # 第一批之后模拟本地验真：VLESS 与 SSTP 各挂验真状态与样本
        self.store.set_pinned(VLESS_ID)
        mark_verified(self.store, VLESS_ID)
        mark_verified(self.store, SSTP_ID)
        self.store.add_sample(VerifySample(node_id=VLESS_ID, kind="verify",
                                           success=True, ts=1100.0,
                                           latency_ms=120.0))
        self.store.add_sample(VerifySample(node_id=SSTP_ID, kind="connect",
                                           success=True, ts=1200.0,
                                           latency_ms=200.0))
        self.store.add_sample(VerifySample(node_id=SSTP_ID, kind="connect",
                                           success=False, ts=1300.0))

    def test_second_update_counts(self):
        rep2 = sr.update_entry(self.reg, self.store, self.eid,
                               fetch_fn=lambda e: LINKS_B2, now=2000.0)
        self.assertTrue(rep2.ok, rep2.error)
        self.assertEqual((rep2.added, rep2.updated, rep2.gone, rep2.returned),
                         (1, 1, 2, 0))            # TROJAN 新增 / VLESS 更新 / SS+SSTP 消失
        rep3 = sr.update_entry(self.reg, self.store, self.eid,
                               fetch_fn=lambda e: LINKS_B3, now=3000.0)
        # SSTP 回归；TROJAN 只在第二批出现过，第三批没拉到 → 照样标消失
        self.assertEqual((rep3.added, rep3.updated, rep3.gone, rep3.returned),
                         (0, 2, 2, 1))
        lu = self.reg.last_update(self.eid)
        self.assertEqual((lu["ok"], lu["gone"], lu["returned"]), (True, 2, 1))

    def test_verified_state_survives_update(self):
        """核心要求：已验出的出口/延迟样本在更新（updated 分支）后不丢。"""
        sr.update_entry(self.reg, self.store, self.eid,
                        fetch_fn=lambda e: LINKS_B2, now=2000.0)
        n = self.store.get_node(VLESS_ID)        # updated：合并刷新
        self.assertTrue(n.pinned)
        self.assertEqual(n.name, "FC-实测出口改名")       # 出口改名不被打回
        self.assertEqual(n.extra["orig_name"], "节点一")
        self.assertEqual(n.exit_profile.ip, "203.0.113.9")
        self.assertEqual(n.metrics.latency_ms, 123.0)
        self.assertEqual(n.extra["quarantined"], "1")
        self.assertEqual(len(self.store.samples_for(VLESS_ID)), 1)
        self.assertNotIn("source_gone", n.extra)

    def test_gone_node_keeps_samples_and_flags(self):
        """消失分支：只打标记，样本与人工标志原样（DESIGN §8）。"""
        sr.update_entry(self.reg, self.store, self.eid,
                        fetch_fn=lambda e: LINKS_B2, now=2000.0)
        n = self.store.get_node(SSTP_ID)
        self.assertEqual(n.extra["source_gone"], "1")
        self.assertEqual(n.extra["source_gone_at"], 2000.0)
        self.assertEqual(n.extra["source_gone_from"], self.eid)
        self.assertEqual(n.exit_profile.ip, "203.0.113.9")   # 验出的出口还在
        self.assertEqual(n.metrics.latency_ms, 123.0)
        self.assertEqual(n.name, "FC-实测出口改名")
        self.assertEqual(n.extra["quarantined"], "1")
        self.assertEqual(len(self.store.samples_for(SSTP_ID)), 2)  # 样本没动
        self.assertEqual(store := self.store.count_nodes(), 4)     # 没删（3+TROJAN）
        gone = self.reg.gone_nodes(self.store, self.eid)
        self.assertEqual({g.id for g in gone}, {SS_ID, SSTP_ID})

    def test_returned_node_restored_with_history(self):
        sr.update_entry(self.reg, self.store, self.eid,
                        fetch_fn=lambda e: LINKS_B2, now=2000.0)
        rep3 = sr.update_entry(self.reg, self.store, self.eid,
                               fetch_fn=lambda e: LINKS_B3, now=3000.0)
        self.assertEqual(rep3.returned, 1)
        n = self.store.get_node(SSTP_ID)
        for key in ("source_gone", "source_gone_at", "source_gone_from"):
            self.assertNotIn(key, n.extra)               # 标记清除
        self.assertEqual(n.extra["registry_entry"], self.eid)
        self.assertEqual(len(self.store.samples_for(SSTP_ID)), 2)  # 历史连续
        self.assertEqual(n.exit_profile.ip, "203.0.113.9")

    def test_pasted_links_not_owned_by_subscription(self):
        """手动页的无关节点（粘贴链接，无 subscription_url/登记戳）绝不被
        订阅刷新标消失；登记制之前的老订阅节点（仅 subscription_url 溯源）
        会被收编进归属范围（DESIGN §4.2）。"""
        paste = manual.parse_link(
            "vless://99999999-8888-7777-6666-555555555555"
            "@z.example.com:443#手贴的")
        self.store.upsert_node(paste)                    # 模拟粘贴导入
        legacy = manual.parse_link(
            "sstp://user2:fakepw@g.example.com:443#老订阅节点")
        legacy.extra["subscription_url"] = SUB_URL       # 登记制之前 URL 导入的
        self.store.upsert_node(legacy)
        rep2 = sr.update_entry(self.reg, self.store, self.eid,
                               fetch_fn=lambda e: LINKS_B2, now=2000.0)
        self.assertTrue(rep2.ok, rep2.error)
        self.assertEqual(rep2.gone, 3)                   # SS + SSTP + 老订阅节点
        self.assertNotIn("source_gone",
                         self.store.get_node(paste.id).extra)  # 手贴的不动
        self.assertEqual(self.store.get_node(legacy.id).extra["source_gone"], "1")

    def test_banned_node_marked_gone_when_missing(self):
        self.store.set_banned(SS_ID)
        rep2 = sr.update_entry(self.reg, self.store, self.eid,
                               fetch_fn=lambda e: LINKS_B2, now=2000.0)
        self.assertTrue(rep2.ok, rep2.error)
        n = self.store.get_node(SS_ID)                   # 含拉黑节点的全量查询
        self.assertEqual(n.extra["source_gone"], "1")    # 消失照样标记
        self.assertTrue(n.banned)                        # 拉黑标志保住

    def test_feed_metrics_refresh_on_update(self):
        """来源侧指标随刷新更新，本地实测延迟不被来源空值冲掉。"""
        reg = sr.SourceRegistry()
        eid = reg.register("feed", now=1.0)["id"]
        sr.update_entry(reg, self.store, eid, fetch_fn=lambda e: feed_csv(
            feed_row(F1, "192.0.2.11", sessions=10)), now=1.0)
        mark_verified(self.store, F1_ID)
        rep = sr.update_entry(reg, self.store, eid, fetch_fn=lambda e: feed_csv(
            feed_row(F1, "192.0.2.11", sessions=33, speed=9000000)), now=2.0)
        self.assertEqual((rep.added, rep.updated, rep.gone), (0, 1, 0))
        n = self.store.get_node(F1_ID)
        self.assertEqual(n.metrics.sessions, 33)         # 来源侧：新值
        self.assertEqual(n.metrics.speed_mbps, 9.0)
        self.assertEqual(n.metrics.latency_ms, 123.0)    # 本地侧：实测保住


class TestFeedScope(unittest.TestCase):
    """feed 的 stamp 归属规则（work8/DESIGN §5.2；自 task23 起 feed 从
    「整页归属」改为「只认登记戳」——合并页混着手导 .ovpn，没戳的绝不碰；
    迁移已给 feed 系存量补戳，归属集合与合并前等价，见 work23/test_merge_landing）。"""

    def test_feed_never_marks_hand_imported(self):
        """「手导永不标消失」保护原样保留（§7 语义不变）：手导 .ovpn 没有
        登记戳，不在 feed 归属范围，feed 拉不到它也绝不标消失。"""
        store = Store(":memory:")
        hand = manual.parse_ovpn_text(
            "client\ndev tun\nproto tcp\nremote fake5.opengw.net 443\n",
            name="手导.ovpn")                    # 默认 source=vpngate（task23）
        store.upsert_node(hand)
        store.add_sample(VerifySample(node_id=hand.id, kind="verify",
                                      success=True, ts=500.0, latency_ms=88.0))
        reg = sr.SourceRegistry()
        eid = reg.register("feed")["id"]
        rep1 = sr.update_entry(reg, store, eid, fetch_fn=lambda e: feed_csv(
            feed_row(F1, "192.0.2.11"), feed_row(F2, "192.0.2.12")), now=1.0)
        self.assertEqual(rep1.added, 2)                  # 只管 feed 喂的
        rep2 = sr.update_entry(reg, store, eid, fetch_fn=lambda e: feed_csv(
            feed_row(F1, "192.0.2.11")), now=2.0)
        self.assertEqual(rep2.gone, 1)                   # fake2 消失
        hand_after = store.get_node(hand.id)
        self.assertNotIn("source_gone", hand_after.extra)   # 手导的永不标消失
        self.assertNotIn("registry_entry", hand_after.extra)
        self.assertEqual(len(store.samples_for(hand.id)), 1)
        self.assertEqual({g.id for g in reg.gone_nodes(store, eid)}, {F2_ID})

    def test_feed_merges_with_same_endpoint_hand_ovpn(self):
        """「同端点合并保验真」语义原样保留（§7；work8/DESIGN §5.2）：手导
        .ovpn 与 feed 节点同 host:port → 同 id（合并后同一 canonical 键）：
        按 §5 合并（验真状态保住、配置换成官方 b64、此后归 feed 管），
        不是当新节点盖掉（§4.2 全库同身份合并）。"""
        store = Store(":memory:")
        hand = manual.parse_ovpn_text(
            "client\ndev tun\nproto tcp\nremote fake1.opengw.net 443\n",
            name="手导.ovpn")
        store.upsert_node(hand)
        mark_verified(store, hand.id)
        store.add_sample(VerifySample(node_id=hand.id, kind="verify",
                                      success=True, ts=500.0, latency_ms=88.0))
        reg = sr.SourceRegistry()
        eid = reg.register("feed")["id"]
        rep = sr.update_entry(reg, store, eid, fetch_fn=lambda e: feed_csv(
            feed_row(F1, "192.0.2.11"), feed_row(F2, "192.0.2.12")), now=1.0)
        self.assertEqual((rep.added, rep.updated), (1, 1))   # fake2 新 / fake1 顺同一记录更新
        n = store.get_node(hand.id)
        self.assertEqual(n.extra["registry_entry"], eid)     # 此后归 feed 管
        self.assertEqual(n.extra["config_format"], "b64")    # 来源侧：官方配置
        self.assertEqual(n.exit_profile.ip, "203.0.113.9")   # 本地侧：验真保住
        self.assertEqual(n.metrics.latency_ms, 123.0)
        self.assertEqual(len(store.samples_for(hand.id)), 1)


class TestUpdateFailure(unittest.TestCase):
    """一键更新失败时库不变（DESIGN §4.3 全量或不动）。"""

    def test_fetch_failure_store_untouched(self):
        store = Store(":memory:")
        reg = sr.SourceRegistry()
        eid = reg.register("feed")["id"]
        sr.update_entry(reg, store, eid, fetch_fn=lambda e: feed_csv(
            feed_row(F1, "192.0.2.11")), now=1.0)
        before = snap(store)

        def boom(e):
            raise OSError("网络断了（假拉取模拟）")

        rep = sr.update_entry(reg, store, eid, fetch_fn=boom, now=2.0)
        self.assertFalse(rep.ok)
        self.assertIn("OSError", rep.error)
        self.assertIn("网络断了", rep.error)
        self.assertEqual(snap(store), before)            # 整库逐字节不变
        self.assertEqual(reg.gone_nodes(store, eid), []) # 没有顺手标消失
        lu = reg.last_update(eid)
        self.assertEqual((lu["ok"], lu["ts"]), (False, 2.0))
        self.assertEqual(lu["error"], rep.error)

    def test_parse_failure_store_untouched(self):
        store = Store(":memory:")
        reg = sr.SourceRegistry()
        feed_eid = reg.register("feed")["id"]
        sub_eid = reg.register("subscription", SUB_URL)["id"]
        sr.update_entry(reg, store, feed_eid, fetch_fn=lambda e: feed_csv(
            feed_row(F1, "192.0.2.11")), now=1.0)
        before = snap(store)
        rep1 = sr.update_entry(reg, store, feed_eid,
                               fetch_fn=lambda e: b"this is not a feed",
                               now=2.0)
        self.assertFalse(rep1.ok)
        self.assertIn("RuntimeError", rep1.error)        # feed 解析的报错原文
        rep2 = sr.update_entry(reg, store, sub_eid,
                               fetch_fn=lambda e: "!!!garbage!!!",
                               now=2.0)
        self.assertFalse(rep2.ok)
        self.assertIn("ValueError", rep2.error)          # format_import 显式报错
        self.assertEqual(snap(store), before)
        self.assertEqual(reg.gone_nodes(store, feed_eid), [])

    def test_empty_parse_result_is_failure(self):
        """解析"成功"但 0 节点：保守判失败，库不动（§4.3）。"""
        store = Store(":memory:")
        reg = sr.SourceRegistry()
        eid = reg.register("feed")["id"]
        sr.update_entry(reg, store, eid, fetch_fn=lambda e: feed_csv(
            feed_row(F1, "192.0.2.11")), now=1.0)
        before = snap(store)
        rep = sr.update_entry(reg, store, eid,
                              fetch_fn=lambda e: feed_csv(), now=2.0)  # 只有表头
        self.assertFalse(rep.ok)
        self.assertIn("0 个节点", rep.error)
        self.assertEqual(snap(store), before)
        self.assertNotIn("source_gone",
                         store.get_node(F1_ID).extra)

    def test_feed_fallback_to_mirror(self):
        """官方失败回退镜像（口径同流水线 vpngate.py:267），镜像 JSON 走
        同一条解析路径，来源记进 report.feed_from。"""
        store = Store(":memory:")
        reg = sr.SourceRegistry()
        eid = reg.register("feed")["id"]

        def official_down(e):
            if e["address"] == sr.FEED_DEFAULT_ADDRESS:
                raise OSError("官方暂时不可达（假拉取模拟）")
            self.assertEqual(e["address"], sr.FEED_FALLBACK_ADDRESS)
            return mirror_json((F1, "192.0.2.11")).encode("utf-8")

        rep = sr.update_entry(reg, store, eid, fetch_fn=official_down, now=1.0)
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual(rep.feed_from, "镜像")
        self.assertEqual(store.count_nodes(), 1)
        self.assertEqual(store.get_node(F1_ID).metrics.sessions, 10)

    def test_feed_both_fail_reports_official_error(self):
        """镜像也挂：报主源（官方）那次的错误，库不动。"""
        store = Store(":memory:")
        reg = sr.SourceRegistry()
        eid = reg.register("feed")["id"]

        def all_down(e):
            raise OSError(f"挂了: {e['address']}")

        rep = sr.update_entry(reg, store, eid, fetch_fn=all_down, now=1.0)
        self.assertFalse(rep.ok)
        self.assertIn(str(sr.FEED_DEFAULT_ADDRESS), rep.error)  # 官方的错
        self.assertNotIn(sr.FEED_FALLBACK_ADDRESS, rep.error)
        self.assertEqual(store.count_nodes(), 0)

    def test_partial_bad_lines_counted(self):
        """部分坏行只计数不致命（format_import 既有口径）。"""
        store = Store(":memory:")
        reg = sr.SourceRegistry()
        eid = reg.register("subscription", SUB_URL)["id"]
        text = VLESS1 + "\nftp://bad.example.com:1#坏行"
        rep = sr.update_entry(reg, store, eid, fetch_fn=lambda e: text, now=1.0)
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual((rep.added, rep.skipped), (1, 1))
        self.assertEqual(store.count_nodes(), 1)


class TestHelpers(unittest.TestCase):
    """merge_fresh 单元与 update_all / gone_nodes 助手。"""

    def test_merge_fresh_new_and_existing(self):
        from client.core.model import Node, NodeMetrics
        fresh = Node(id="manual:sstp:h.example.com:443", name="新节点",
                     protocol="sstp", source="manual",
                     metrics=NodeMetrics(sessions=3))
        merged = sr.merge_fresh(None, fresh, "sub-abc")
        self.assertEqual(merged.extra["registry_entry"], "sub-abc")

        old = Node(id="manual:sstp:h.example.com:443", name="实测名",
                   protocol="sstp", source="manual", pinned=True,
                   metrics=NodeMetrics(latency_ms=50.0))
        old.exit_profile = ExitProfile(ip="203.0.113.9", verified_at=1.0)
        old.extra = {"orig_name": "原名", "quarantined": "1",
                     "source_gone": "1", "source_gone_from": "sub-abc"}
        merged = sr.merge_fresh(old, fresh, "sub-abc")
        self.assertTrue(merged.pinned)
        self.assertEqual(merged.exit_profile.ip, "203.0.113.9")
        self.assertEqual(merged.metrics.latency_ms, 50.0)   # None 时保本地
        self.assertEqual(merged.name, "实测名")             # 出口改名保住
        self.assertEqual(merged.extra["quarantined"], "1")
        self.assertNotIn("source_gone", merged.extra)       # 回归清标记
        self.assertEqual(merged.metrics.sessions, 3)        # 来源侧照常刷新

    def test_update_all_independent_and_gone_helper(self):
        store = Store(":memory:")
        reg = sr.SourceRegistry()
        feed_eid = reg.register("feed", now=1.0)["id"]
        sub_eid = reg.register("subscription", SUB_URL)["id"]
        reg.set_enabled(sub_eid, False)                  # 停用：全更跳过

        def fetch(e):
            if e["type"] == "feed":
                return feed_csv(feed_row(F1, "192.0.2.11"),
                                feed_row(F3, "192.0.2.13"))
            raise OSError("订阅断了（假拉取模拟）")

        reports = sr.update_all(reg, store, fetch_fn=fetch, now=1.0)
        self.assertEqual([r.entry_id for r in reports], [feed_eid])  # 停用被跳过
        # 第二轮：feed 掉了一个节点 + 订阅仍然断——各条独立
        def fetch2(e):
            if e["type"] == "feed":
                return feed_csv(feed_row(F1, "192.0.2.11"))
            raise OSError("订阅断了（假拉取模拟）")

        reg.set_enabled(sub_eid, True)
        reports2 = sr.update_all(reg, store, fetch_fn=fetch2, now=2.0)
        by_id = {r.entry_id: r for r in reports2}
        self.assertFalse(by_id[sub_eid].ok)              # 订阅条失败
        self.assertTrue(by_id[feed_eid].ok)              # 不影响 feed 条
        self.assertEqual(by_id[feed_eid].gone, 1)        # fake3 消失
        gone = reg.gone_nodes(store, feed_eid)
        self.assertEqual([g.id for g in gone], [F3_ID])
        self.assertEqual(len(store.samples_for(F3_ID)), 0)


if __name__ == "__main__":
    unittest.main()
