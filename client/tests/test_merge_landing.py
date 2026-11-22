#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task23 离线验收测试（test_merge_landing.py）
============================================
跑在「拷入 client-dev 相关文件并打上本段接线 diff + 测试重写包」的副本
（work23/client-copy，由 build_copy.sh 重建）上。构造四页时代假存量
（source=openvpn / vpngate_feed 的节点 + 含 feed_mirror 的 sources.json）
→ 启动触发（sources/landing.run_startup_migration）→ 逐项断言简报的
验收口径：

- 来源只剩 canonical 键；节点数 = 身份并集一台不丢；
- 验真出口/样本/钉选/封禁/OpenVPN 配置都在；样本行数不丢；
- 触发幂等：连跑两次启动迁移，第二次零变更（JSON 深等价）；
- 手导保护：手导 .ovpn 在迁移后 + 一轮 feed 刷新模拟后仍不被标消失
  （owned_nodes 戳归属生效）；
- 快照存活：merged_superseded 在一轮 feed 刷新模拟后仍在
  （_LOCAL_EXTRA_KEYS 增补生效）；
- GUI 三页：offscreen 下构造主窗断言页签只剩三页且合并页只有一个
  登记入口（PySide6 不在场则该组 skip 并记原因）；
- §7 重写后的既有测试 + 既有 test_merge 在副本上全绿（子进程实跑）。

假数据红线：域名用 RFC 2606 保留的 example.net / opengw 假主机名，IP 用
RFC 5737 保留段（192.0.2.x / 198.51.100.x / 203.0.113.x），配置为伪造
.ovpn 文本与假 b64——零真实节点地址、零订阅内容、零密钥、零联网；
迁移全程不自动切换任何节点（只搬数据）。

跑法：cd work23 && python3 -m unittest test_merge_landing（-v 看明细）
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

# ---------------------------------------------------------------------------
# 定位打好 diff 的副本（work23/ 布局或装进 client-copy/client/tests/ 布局）
# ---------------------------------------------------------------------------
def _find_copy_root():
    here = os.path.dirname(os.path.abspath(__file__))
    cand = os.path.join(here, "client-copy")            # work23/ 布局
    if os.path.isdir(os.path.join(cand, "client")):
        return cand
    cand = os.path.dirname(os.path.dirname(here))       # client-copy/client/tests/ 布局
    if os.path.isdir(os.path.join(cand, "client")):
        return cand
    return None


COPY_ROOT = _find_copy_root()
if COPY_ROOT is None:
    raise RuntimeError("找不到打好 diff 的副本（work23/client-copy）；"
                       "先跑 work23/build_copy.sh 重建副本再测")
if COPY_ROOT not in sys.path:
    sys.path.insert(0, COPY_ROOT)

from client.core.model import ExitProfile, Node, NodeMetrics, make_node_id  # noqa: E402
from client.core.store import Store, VerifySample  # noqa: E402
from client.sources import merge_proto as mp  # noqa: E402
from client.sources import source_registry as sr  # noqa: E402
from client.sources.landing import (needs_startup_migration,  # noqa: E402
                                    run_startup_migration)

# ---------------------------------------------------------------------------
# 假数据工厂（全部保留段/假域名；零真实地址、零订阅内容、零密钥）
# ---------------------------------------------------------------------------
FEED_HEADER = ("HostName,IP,Score,Ping,Speed,CountryLong,CountryShort,"
               "NumVpnSessions,Uptime,OpenVPN_ConfigData_Base64")

H_F1 = "fake1.opengw.net"        # feed 侧节点（与手导 H1 同端点 → 合并目标）
H_F2 = "fake2.opengw.net"        # feed 侧节点（刷新模拟轮里消失 → 标 gone）
H_THIRD = "third.example.net"    # 手导第三方服务器（feed 里永远不会出现）

IP_A = "192.0.2.11"
IP_B = "192.0.2.12"
IP_EXIT = "203.0.113.9"          # H1 实测出口（RFC 5737）

F1_ID = make_node_id("vpngate_feed", "openvpn", H_F1, 443)   # 四页时代旧键
F2_ID = make_node_id("vpngate_feed", "openvpn", H_F2, 443)
H1_ID = make_node_id("openvpn", "openvpn", H_F1, 443)
H3_ID = make_node_id("openvpn", "openvpn", H_THIRD, 443)
M1_ID = make_node_id("manual", "vless", "node.manual.example.net", 443,
                     "11111111-2222-3333-4444-555555555555")
P1_ID = make_node_id("pipeline", "sstp", "node.pipeline.example.net", 443)
MERGED_ID = make_node_id("vpngate", "openvpn", H_F1, 443)    # 迁移后合并 id

CFG_B64_F1 = "Y2xpZW50CmRldiB0dW4KcmVtb3RlIGZha2UxLm9wZW5ndy5uZXQgNDQzCg=="  # 假 b64
CFG_TEXT_H1 = "client\ndev tun\nproto tcp\nremote fake1.opengw.net 443\n"
CFG_TEXT_H3 = ("client\ndev tun\nproto udp\n"
               "remote third.example.net 443\ncipher AES-256-CBC\n")
FEED_OFFICIAL = "https://www.vpngate.net/api/iphone/"
FEED_CUSTOM = "https://mirror.example.invalid/data.json"     # 假镜像地址


def feed_row(host, ip, sessions=10, speed=8000000):
    cfg = "client\ndev tun\nproto udp\nremote %s 443\n" % host
    b64 = __import__("base64").b64encode(cfg.encode()).decode()
    return ",".join([host, ip, "100", "50", str(speed), "Fake Country", "FC",
                     str(sessions), "86400000", b64])


def feed_csv(*rows):
    return FEED_HEADER + "\n" + "\n".join(rows) + "\n"


def four_page_store(tmpdir):
    """四页时代假存量库：6 条节点（含同端点孪生 F1+H1）+ 4 行样本。
    样本行数与内容在迁移后逐行可追（验收：一台不丢、一行不丢）。"""
    store = Store(os.path.join(tmpdir, "client.db"))

    f1 = Node(id=F1_ID, name=H_F1, protocol="openvpn", source="vpngate_feed",
              host=H_F1, port=443, config_ref=CFG_B64_F1,
              metrics=NodeMetrics(sessions=10, speed_mbps=8.0),
              pinned=True,
              extra={"config_format": "b64", "entry_ip": IP_A,
                     "country": "Fake Country", "country_code": "FC",
                     "feed_score": 100, "feed_ping": 50})
    h1 = Node(id=H1_ID, name="FC · 实测出口改名", protocol="openvpn",
              source="openvpn", host=H_F1, port=443, config_ref=CFG_TEXT_H1,
              metrics=NodeMetrics(latency_ms=88.0, jitter_ms=3.0),
              banned=True,
              extra={"config_format": "text", "orig_name": "手导.ovpn",
                     "quarantined": "1", "quarantine_reason": "dirty_ip"})
    h1.exit_profile = ExitProfile(ip=IP_EXIT, country="Fake Country",
                                  country_code="FC", verified_at=1000.0,
                                  dirty={"muse"})
    h3 = Node(id=H3_ID, name="第三方.ovpn", protocol="openvpn", source="openvpn",
              host=H_THIRD, port=443, config_ref=CFG_TEXT_H3,
              extra={"config_format": "text"})
    f2 = Node(id=F2_ID, name=H_F2, protocol="openvpn", source="vpngate_feed",
              host=H_F2, port=443, config_ref="Q0ZHMg==",
              extra={"config_format": "b64", "registry_entry": "feed",
                     "entry_ip": IP_B, "country": "Fake Country",
                     "country_code": "FC"})
    m1 = Node(id=M1_ID, name="手动节点", protocol="vless", source="manual",
              host="node.manual.example.net", port=443, config_ref="vless://x")
    p1 = Node(id=P1_ID, name="流水线节点", protocol="sstp", source="pipeline",
              host="node.pipeline.example.net", port=443, config_ref="sstp://x")
    store.upsert_nodes([f1, h1, h3, f2, m1, p1])
    for sample in (VerifySample(node_id=F1_ID, kind="verify", success=True,
                                ts=900.0, latency_ms=111.0),
                   VerifySample(node_id=H1_ID, kind="verify", success=True,
                                ts=1000.0, latency_ms=88.0, jitter_ms=3.0,
                                exit_ip=IP_EXIT),
                   VerifySample(node_id=H3_ID, kind="connect", success=True,
                                ts=1100.0, latency_ms=200.0),
                   VerifySample(node_id=M1_ID, kind="verify", success=False,
                                ts=1200.0)):
        store.add_sample(sample)
    return store


def four_page_registry_json():
    """四页时代 sources.json：feed（旧 page 键）+ 自定义地址的 feed-mirror
    + 两边都有更新记账（work8/DESIGN §5.3 三种情形里的「两者都在」）。"""
    return {"version": 1,
            "entries": [
                {"id": "feed", "type": "feed", "page": "vpngate_feed",
                 "address": FEED_OFFICIAL, "name": "", "enabled": True,
                 "created_at": 1.0},
                {"id": "feed-mirror", "type": "feed_mirror", "page": "openvpn",
                 "address": FEED_CUSTOM, "name": "旧镜像", "enabled": True,
                 "created_at": 2.0},
            ],
            "last_updates": {
                "feed": {"ts": 3.0, "ok": True, "added": 2},
                "feed-mirror": {"ts": 4.0, "ok": True, "added": 1},
            }}


def write_registry(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def db_json_dump(store):
    """整库 JSON 形状（节点 + 样本），幂等深等价的判定基准。"""
    nodes = sorted(n.to_json() for n in store.list_nodes(include_banned=True))
    rows = store._conn.execute(
        "SELECT node_id, ts, kind, success, latency_ms, jitter_ms, exit_ip, detail"
        " FROM verify_samples ORDER BY ts, id").fetchall()
    samples = [list(r) for r in rows]
    return {"nodes": nodes, "samples": samples}


def registry_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 1. 启动触发 → 迁移无损
# ---------------------------------------------------------------------------
class TestStartupMigrationLossless(unittest.TestCase):
    """假四页时代库 + 含 feed_mirror 的 sources.json → 启动触发 →
    来源只剩 canonical 键、一台不丢、一行不丢、状态全在。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = four_page_store(self.tmp.name)
        self.reg_path = os.path.join(self.tmp.name, "sources.json")
        write_registry(self.reg_path, four_page_registry_json())
        self.report = run_startup_migration(self.store, self.reg_path)

    def test_triggered_with_report(self):
        self.assertTrue(self.report["triggered"])
        self.assertTrue(self.report["registry_migrated"])
        self.assertEqual(self.report["nodes_rewritten"], 3)   # 合并1(F1+H1) + 改写2(F2/H3)
        self.assertEqual(self.report["merged_away"], 1)       # F1+H1 → 1
        self.assertEqual(self.report["samples_remapped"], 3)  # F1+H1 两行并
                                                              # 到合并 id + H3 改写
                                                              # （F2 无样本行）

    def test_only_canonical_sources_remain(self):
        sources = {n.source for n in self.store.list_nodes(include_banned=True)}
        self.assertEqual(sources, {"vpngate", "manual", "pipeline"})
        self.assertNotIn("openvpn", sources)
        self.assertNotIn("vpngate_feed", sources)

    def test_node_count_is_identity_union(self):
        """节点数 = 身份并集一台不丢：F1+H1 同端点并成一条，其余各留一条。"""
        nodes = self.store.list_nodes(include_banned=True)
        self.assertEqual(len(nodes), 5)
        self.assertEqual({n.id for n in nodes},
                         {MERGED_ID, F2_ID.replace("vpngate_feed", "vpngate"),
                          H3_ID.replace("openvpn", "vpngate", 1),
                          M1_ID, P1_ID})
        merged = self.store.get_node(MERGED_ID)
        self.assertIsNotNone(merged)
        self.assertEqual(merged.source, "vpngate")

    def test_verified_exit_and_flags_survive(self):
        """验真出口/钉选/封禁/隔离/实测指标合并后都在（work8/DESIGN §3）。"""
        merged = self.store.get_node(MERGED_ID)
        self.assertEqual(merged.exit_profile.ip, IP_EXIT)        # 验过侧沿用
        self.assertEqual(merged.exit_profile.dirty, {"muse"})    # 拒绝史保留
        self.assertTrue(merged.pinned)                           # F1 的钉选
        self.assertTrue(merged.banned)                           # H1 的封禁（或）
        self.assertEqual(merged.extra.get("quarantined"), "1")   # 隔离警示保留
        self.assertEqual(merged.extra.get("orig_name"), "手导.ovpn")
        self.assertEqual(merged.name, "FC · 实测出口改名")        # 实测出口名
        self.assertEqual(merged.metrics.latency_ms, 88.0)        # 实测延迟（None≠0）
        self.assertEqual(merged.extra.get("registry_entry"), "feed")  # §5.2 补戳

    def test_openvpn_config_both_survive(self):
        """OpenVPN 配置：feed b64 为主，手导原文整份进 merged_superseded
        快照——非主方一份不少（work8/DESIGN §3.3/§3.9）。"""
        merged = self.store.get_node(MERGED_ID)
        self.assertEqual(merged.extra.get("config_format"), "b64")
        self.assertEqual(merged.config_ref, CFG_B64_F1)
        snap = merged.extra.get("merged_superseded") or {}
        self.assertIn(H1_ID, snap)
        self.assertIn(F1_ID, snap)
        self.assertEqual(snap[H1_ID]["config_ref"], CFG_TEXT_H1)   # 手导原文
        self.assertEqual(snap[H1_ID]["extra"]["config_format"], "text")
        self.assertEqual(snap[H1_ID]["exit_profile"]["ip"], IP_EXIT)
        self.assertEqual(snap[F1_ID]["config_ref"], CFG_B64_F1)
        self.assertEqual(snap[F1_ID]["source"], "vpngate_feed")    # 旧键原样留档
        # 无戳手导（feed 无孪生）：只改键重算 id，其余原样、不获登记戳
        h3 = self.store.get_node(H3_ID.replace("openvpn", "vpngate", 1))
        self.assertEqual(h3.config_ref, CFG_TEXT_H3)
        self.assertNotIn("registry_entry", h3.extra)

    def test_samples_rows_preserved_and_remapped(self):
        """样本行数不丢；F1/H1 两行并到合并 id 上，内容逐字段不变。"""
        all_rows = self.store._conn.execute(
            "SELECT COUNT(*) FROM verify_samples").fetchone()[0]
        self.assertEqual(all_rows, 4)                     # 迁移前 4 行
        merged_rows = self.store.samples_for(MERGED_ID)
        self.assertEqual(len(merged_rows), 2)             # F1+H1 的样本都跟上
        self.assertEqual({s.latency_ms for s in merged_rows}, {111.0, 88.0})
        self.assertEqual({s.exit_ip for s in merged_rows if s.exit_ip},
                         {IP_EXIT})
        # 无关节点的样本原样（id 没变的映射到自身，行不动）
        h3_rows = self.store.samples_for(H3_ID.replace("openvpn", "vpngate", 1))
        self.assertEqual(len(h3_rows), 1)
        self.assertEqual(h3_rows[0].latency_ms, 200.0)

    def test_registry_migrated(self):
        """登记表：feed_mirror 条目删除、page 归一、自定义镜像地址显式优先
        （work8/DESIGN §5.3；副作用：采纳后不再有官方回退——已知边界继承）。"""
        data = registry_json(self.reg_path)
        ids = {e["id"] for e in data["entries"]}
        self.assertEqual(ids, {"feed"})
        feed = data["entries"][0]
        self.assertEqual(feed["type"], "feed")
        self.assertEqual(feed["page"], "vpngate")
        self.assertEqual(feed["address"], FEED_CUSTOM)    # 显式登记 > 兜底默认
        self.assertNotIn("feed-mirror", data["last_updates"])  # 镜像记账删除
        self.assertIn("feed", data["last_updates"])            # feed 记账保留


# ---------------------------------------------------------------------------
# 2. 触发幂等
# ---------------------------------------------------------------------------
class TestStartupMigrationIdempotent(unittest.TestCase):
    """连跑两次启动迁移，第二次零变更（JSON 深等价，work8/DESIGN §4.4）。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = four_page_store(self.tmp.name)
        self.reg_path = os.path.join(self.tmp.name, "sources.json")
        write_registry(self.reg_path, four_page_registry_json())
        run_startup_migration(self.store, self.reg_path)
        self.before = db_json_dump(self.store)
        self.reg_before = registry_json(self.reg_path)

    def test_second_run_is_zero_change(self):
        rep = run_startup_migration(self.store, self.reg_path)
        self.assertFalse(rep["triggered"])               # 检测不命中
        self.assertFalse(rep["registry_migrated"])
        self.assertEqual(db_json_dump(self.store), self.before)   # 库深等价
        self.assertEqual(registry_json(self.reg_path), self.reg_before)

    def test_detection_off_after_migration(self):
        self.assertFalse(needs_startup_migration(self.store, self.reg_path))

    def test_merge_proto_idempotent_on_migrated_records(self):
        """执行层兜底：就算误触发（强制喂迁移后记录），merge_proto 仍零变更
        （migrate_library 幂等，work8 已证；这里对真库形状再证一遍）。"""
        records = [n.to_dict() for n in self.store.list_nodes(include_banned=True)]
        again = mp.migrate_library(records)
        self.assertEqual(json.dumps(again, sort_keys=True, ensure_ascii=False),
                         json.dumps(records, sort_keys=True, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 3. 手导保护 + 戳归属等价（work8/DESIGN §5.2、§7 新增）
# ---------------------------------------------------------------------------
class TestHandImportProtection(unittest.TestCase):
    """迁移后跑一轮 feed 刷新模拟：owned_nodes 戳归属生效——手导 .ovpn
    （含第三方服务器）绝不被标消失；feed 系存量（已补戳）消失照样标记。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = four_page_store(self.tmp.name)
        self.reg_path = os.path.join(self.tmp.name, "sources.json")
        write_registry(self.reg_path, four_page_registry_json())
        run_startup_migration(self.store, self.reg_path)

    def _feed_round(self):
        reg = sr.SourceRegistry(self.reg_path)
        return sr.update_entry(
            reg, self.store, "feed",
            # 刷新轮只拉到 fake1：fake2 消失；third/手导不在 feed 里（永远）
            fetch_fn=lambda e: feed_csv(feed_row(H_F1, IP_A, sessions=33)),
            now=2000.0)

    def test_owned_nodes_stamp_scope_after_migration(self):
        """§7 新增：owned_nodes 戳归属等价用例——归属 = 带 feed 戳的
        合并页节点（合并节点 + feed 系存量），无戳手导/手动/流水线不在。"""
        reg = sr.SourceRegistry(self.reg_path)
        entry = reg.get("feed")
        owned = {n.id for n in reg.owned_nodes(self.store, entry)}
        self.assertEqual(owned, {MERGED_ID,
                                 F2_ID.replace("vpngate_feed", "vpngate")})
        self.assertNotIn(H3_ID.replace("openvpn", "vpngate", 1), owned)
        self.assertNotIn(M1_ID, owned)
        self.assertNotIn(P1_ID, owned)

    def test_hand_imported_never_marked_gone(self):
        rep = self._feed_round()
        self.assertTrue(rep.ok, rep.error)
        self.assertEqual((rep.updated, rep.gone), (1, 1))  # fake1 更新 / fake2 消失
        # 手导保护：第三方手导节点不在 feed 归属范围，绝不标消失
        h3 = self.store.get_node(H3_ID.replace("openvpn", "vpngate", 1))
        self.assertNotIn("source_gone", h3.extra)
        self.assertNotIn("registry_entry", h3.extra)
        self.assertEqual(len(self.store.samples_for(h3.id)), 1)
        # 手动/流水线节点同样不碰
        self.assertNotIn("source_gone",
                         self.store.get_node(M1_ID).extra)
        self.assertNotIn("source_gone",
                         self.store.get_node(P1_ID).extra)
        # feed 系存量（补了戳的）消失照样标记——归属集合与合并前等价
        gone = self.store.get_node(F2_ID.replace("vpngate_feed", "vpngate"))
        self.assertEqual(gone.extra.get("source_gone"), "1")
        self.assertEqual(gone.extra.get("source_gone_from"), "feed")

    def test_merged_snapshot_survives_refresh(self):
        """快照存活：合并节点经 merge_fresh 刷新后 merged_superseded 仍在
        （_LOCAL_EXTRA_KEYS 增补生效——不加则第一次刷新就洗掉快照）。"""
        rep = self._feed_round()
        self.assertTrue(rep.ok, rep.error)
        merged = self.store.get_node(MERGED_ID)
        snap = merged.extra.get("merged_superseded")
        self.assertIsNotNone(snap)
        self.assertIn(H1_ID, snap)
        self.assertEqual(snap[H1_ID]["config_ref"], CFG_TEXT_H1)  # 手导原文仍在
        # 刷新同时不洗掉其他本地侧状态
        self.assertEqual(merged.exit_profile.ip, IP_EXIT)
        self.assertEqual(merged.extra.get("quarantined"), "1")
        self.assertEqual(merged.metrics.latency_ms, 88.0)
        self.assertEqual(merged.metrics.sessions, 33)    # 来源侧照常刷新


# ---------------------------------------------------------------------------
# 4. 检测口径（不触发零写入 / 仅登记表残留也触发）
# ---------------------------------------------------------------------------
class TestStartupDetection(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_fresh_library_not_triggered(self):
        store = Store(os.path.join(self.tmp.name, "client.db"))
        reg_path = os.path.join(self.tmp.name, "sources.json")
        self.assertFalse(needs_startup_migration(store, reg_path))
        before = db_json_dump(store)
        rep = run_startup_migration(store, reg_path)
        self.assertFalse(rep["triggered"])
        self.assertEqual(db_json_dump(store), before)    # 零写入
        self.assertFalse(os.path.exists(reg_path))       # 不顺手创建登记表

    def test_registry_only_marks_trigger(self):
        """登记过 feed/mirror 却没喂过节点的库：page 旧键残留也算触发
        （否则旧 page 键永远洗不掉，「更新已登记来源」按页匹配会失配）。
        幂等口径下误触发无害。"""
        store = Store(os.path.join(self.tmp.name, "client.db"))
        reg_path = os.path.join(self.tmp.name, "sources.json")
        write_registry(reg_path, {"version": 1, "entries": [
            {"id": "feed", "type": "feed", "page": "vpngate_feed",
             "address": FEED_OFFICIAL, "name": "", "enabled": True,
             "created_at": 1.0}]})
        self.assertTrue(needs_startup_migration(store, reg_path))
        rep = run_startup_migration(store, reg_path)
        self.assertTrue(rep["triggered"])
        self.assertTrue(rep["registry_migrated"])
        self.assertEqual(registry_json(reg_path)["entries"][0]["page"],
                         "vpngate")
        self.assertEqual(store.count_nodes(), 0)         # 库本来就空

    def test_mirror_only_promotion(self):
        """只登记过镜像（§5.3 转正情形）：镜像就地转正为 feed 条目，
        更新记账挂靠保留（work8/DESIGN §5.3 表第 2 行）。"""
        store = Store(os.path.join(self.tmp.name, "client.db"))
        reg_path = os.path.join(self.tmp.name, "sources.json")
        write_registry(reg_path, {"version": 1, "entries": [
            {"id": "feed-mirror", "type": "feed_mirror", "page": "openvpn",
             "address": FEED_CUSTOM, "name": "", "enabled": True,
             "created_at": 2.0}],
            "last_updates": {"feed-mirror": {"ts": 4.0, "ok": True}}})
        rep = run_startup_migration(store, reg_path)
        self.assertTrue(rep["triggered"] and rep["registry_migrated"])
        data = registry_json(reg_path)
        self.assertEqual([e["id"] for e in data["entries"]], ["feed"])
        self.assertEqual(data["entries"][0]["type"], "feed")
        self.assertEqual(data["entries"][0]["page"], "vpngate")
        self.assertEqual(data["last_updates"].get("feed"), {"ts": 4.0, "ok": True})

    def test_corrupt_registry_does_not_block_startup(self):
        """登记表读不动：迁移静默跳过登记表侧，不挡启动（库侧照常检测）。"""
        store = Store(os.path.join(self.tmp.name, "client.db"))
        reg_path = os.path.join(self.tmp.name, "sources.json")
        with open(reg_path, "w", encoding="utf-8") as f:
            f.write("{broken json!!!")
        self.assertFalse(needs_startup_migration(store, reg_path))
        rep = run_startup_migration(store, reg_path)
        self.assertFalse(rep["triggered"])


# ---------------------------------------------------------------------------
# 5. GUI 三页（PySide6 不在场整组 skip 并记原因）
# ---------------------------------------------------------------------------
try:
    from PySide6.QtWidgets import QApplication  # noqa: F401
    _QT = True
except ImportError:
    _QT = False

if _QT:
    from client.core.orchestrator import Orchestrator
    from client.ui import app as app_mod


@unittest.skipUnless(_QT, "无 PySide6（本机/VM 未安装），GUI 三页组整组跳过；"
                         "PySide6 环境下实跑断言三页与单登记入口")
class TestGuiThreePages(unittest.TestCase):
    """offscreen 构造主窗：页签只剩三页、合并页只有一个登记入口、
    迁移后的节点落在合并页（work8/DESIGN §6）。"""

    @classmethod
    def setUpClass(cls):
        if _QT:
            cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = four_page_store(self.tmp.name)
        reg_path = os.path.join(self.tmp.name, "sources.json")
        write_registry(reg_path, four_page_registry_json())
        run_startup_migration(self.store, reg_path)
        eng = _FakeEngine()
        orch = Orchestrator(self.store, {"openvpn": eng}, _ok_verifier)
        self.win = app_mod.MainWindow(self.store, orchestrator=orch,
                                      engines={"openvpn": eng})
        self.addCleanup(self.win.close)

    def test_tabs_are_three_pages(self):
        self.assertEqual(self.win.tabs.count(), 3)
        self.assertEqual(set(self.win.pages),
                         {"vpngate", "manual", "pipeline"})

    def test_merged_page_has_single_register_entry(self):
        actions = [t for t, _ in self.win.pages["vpngate"]._import_actions()]
        self.assertIn("登记并更新 feed", actions)
        self.assertNotIn("登记 feed 镜像", actions)
        register_buttons = [t for t in actions if t.startswith("登记")]
        self.assertEqual(register_buttons, ["登记并更新 feed"])   # 单入口
        # 其余两页没有登记 feed 类入口
        for source in ("manual", "pipeline"):
            acts = [t for t, _ in self.win.pages[source]._import_actions()]
            self.assertNotIn("登记并更新 feed", acts)
            self.assertNotIn("登记 feed 镜像", acts)

    def test_migrated_nodes_landed_on_merged_page(self):
        """迁移后节点落在合并页。断言分两步（task23 修复·方案 A，
        REVIEW_FAIL 2026-11-22 裁决）：合并节点 banned=True 且
        extra["quarantined"]="1" 是 work8 合并口径的既定语义（封=或、
        隔离警示保留，H1 固件有意带上），页面默认视图按既有 UX 过滤
        已拉黑/已隔离节点——先断言默认视图里合并节点确实被过滤（过滤
        行为本身仍在工作），再勾上「显示已拉黑」「显示隔离节点」展开
        全部，断言合并节点在展开后的合并页视图里。"""
        page = self.win.pages["vpngate"]
        # ① 默认视图（两复选框未勾）：合并节点被页面既有过滤挡住
        self.assertFalse(page.show_banned_chk.isChecked())
        self.assertFalse(page.show_quarantined_chk.isChecked())
        self.win.refresh_pages()
        self.assertNotIn(MERGED_ID, page.visible_node_ids())
        # ② 展开全部：合并节点就在合并页视图里（迁移落页断言本体）
        page.show_banned_chk.setChecked(True)
        page.show_quarantined_chk.setChecked(True)
        self.win.refresh_pages()
        ids = page.visible_node_ids()
        self.assertIn(MERGED_ID, ids)
        self.assertIn(H3_ID.replace("openvpn", "vpngate", 1), ids)
        self.assertIn(F2_ID.replace("vpngate_feed", "vpngate"), ids)
        self.assertNotIn(M1_ID, ids)                     # 手动节点仍在手动页
        self.assertEqual(self.win.pages["manual"].visible_node_ids(), [M1_ID])


class _FakeEngine:
    """离线 GUI 构造用的最小引擎替身（不启动、不连网）。"""
    name = "openvpn"

    def __init__(self):
        self.alive = True

    def start(self, node, on_event=None):
        self.alive = True
        return {"node_id": node.id}

    def stop(self, handle):
        self.alive = False

    def status(self, handle):
        return "running" if self.alive else "stopped"


def _ok_verifier(node):
    from client.core.verify import VerifyOutcome
    return VerifyOutcome(ok=True, exit_profile=ExitProfile(
        ip="203.0.113.9", country="Fake Country", country_code="FC",
        network_type="residential", is_clean=True), scenes=set())


# ---------------------------------------------------------------------------
# 6. §7 重写后的既有测试 + 既有 test_merge 在副本上全绿（子进程实跑）
# ---------------------------------------------------------------------------
class TestRewrittenSuitesGreen(unittest.TestCase):
    """在副本上子进程实跑：既有 test_merge（task8 44 例）+ §7 重写后的
    既有测试模块。无 PySide6 的环境 GUI 模块整文件 skip（OK + skip 记因）。"""

    MODULES = [
        "client.tests.test_merge",            # task8 既有迁移测试
        "client.tests.test_source_pages",     # §7 行为反转重写
        "client.tests.test_source_registry",  # §7 镜像→feed 重写
        "client.tests.test_model",            # §7 id/枚举重写
        "client.tests.test_sources",          # §7 解析默认值重写
        "client.tests.test_cli",              # §7 id 字面量
        "client.tests.test_cli_sources",
        "client.tests.test_cli_verify_proxy",  # §7 id 字面量
        "client.tests.test_orchestrator",     # §7 fixture 键
        "client.tests.test_health_wiring",    # §7 id 字面量
        "client.tests.test_gui_app",          # §7 三页重写（无 Qt 时 skip）
        "client.tests.test_gui_sources",      # §7 按钮重写（无 Qt 时 skip）
    ]

    def test_suites_all_green(self):
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                   PYTHONPATH=COPY_ROOT)
        proc = subprocess.run(
            [sys.executable, "-m", "unittest", "-q", *self.MODULES],
            cwd=COPY_ROOT, env=env, capture_output=True, timeout=600)
        tail = (proc.stderr or b"")[-2000:].decode("utf-8", "replace")
        self.assertEqual(
            proc.returncode, 0,
            "§7 重写后的测试在副本上没有全绿：\n" + tail)
        self.assertIn("OK", tail)


if __name__ == "__main__":
    unittest.main()
