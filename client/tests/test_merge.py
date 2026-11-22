#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task8 离线测试（test_merge.py）
==============================
假数据 only：域名用 RFC 2606 保留的 example.net，IP 用 RFC 5737 保留段
（192.0.2.x / 198.51.100.x / 203.0.113.x），配置是伪造的 .ovpn 文本与假
b64 载荷——零真实节点地址、零订阅内容、零密钥、零联网。

断言覆盖（任务验收口径逐项）：
- 迁移后来源只剩合并键（§1）
- 节点数 = 按身份取并集，一台不丢（§4.1）
- 验真出口/验真样本、钉选、封禁、OpenVPN 配置合并后都在（§3 红线）
- 全库通用无损：每条旧记录的完整原样可追溯（§3.9 整记录快照）
- 迁移幂等：连跑两遍结果相同（§4.4）
- 样本表 node_id 重映射不丢行（§4.3）
- sources.json 登记表迁移与幂等（§5.3）

跑法：cd client-dev && python3 -m unittest client.tests.test_merge（-v 看明细）。
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.sources import merge_proto as mp  # noqa: E402

# ---------------------------------------------------------------------------
# 假数据工厂（全部保留段/假域名，红线：不出现真实节点地址/订阅内容/密钥）
# ---------------------------------------------------------------------------
FEED = "vpngate_feed"
OVPN = "openvpn"
CANON = "vpngate"

HOST1 = "node1.example.net"          # RFC 2606 保留域名
HOST2 = "node2.example.net"
HOST3 = "node3.example.net"
HOST_THIRD = "node4.example.net"
IP_A = "192.0.2.11"                  # RFC 5737 保留段
IP_B = "198.51.100.7"
IP_C = "203.0.113.9"

CFG_B64 = "Y2xpZW50CmRldiB0dW4KcmVtb3RlIG5vZGUxLmV4YW1wbGUubmV0IDQ0Mwo="  # 假载荷
CFG_TEXT = "client\ndev tun\nremote node1.example.net 443\n"
CFG_TEXT2 = ("client\ndev tun\nremote node1.example.net 443\n"
             "cipher AES-256-CBC\n")  # 用户改过的假 .ovpn


def metrics(latency_ms=None, jitter_ms=None, sessions=0, speed_mbps=0.0,
            uptime_hours=None):
    """NodeMetrics.to_dict() 形状。"""
    return {"latency_ms": latency_ms, "jitter_ms": jitter_ms,
            "sessions": sessions, "speed_mbps": speed_mbps,
            "uptime_hours": uptime_hours}


def profile(ip=None, verified_at=None, dirty=None, **kw):
    """ExitProfile.to_dict() 形状（只填测试关心的键）。"""
    p = {"ip": ip, "verified_at": verified_at, "dirty": sorted(dirty or [])}
    p.update(kw)
    return p


def rec(source, host, port=443, *, protocol="openvpn", name=None,
        config_ref="", exit_profile=None, scenes=None, metrics_dict=None,
        pinned=False, banned=False, extra=None):
    """Node.to_dict() 形状的假记录工厂。"""
    return {
        "id": f"{source}:{protocol}:{host}:{port}",
        "name": name or host,
        "protocol": protocol,
        "source": source,
        "host": host,
        "port": port,
        "config_ref": config_ref,
        "exit_profile": dict(exit_profile or {}),
        "scenes": sorted(scenes or []),
        "metrics": dict(metrics_dict or metrics()),
        "pinned": pinned,
        "banned": banned,
        "extra": dict(extra or {}),
    }


def stash_of(record):
    return (record.get("extra") or {}).get(mp.MERGED_SUPERSEDED_KEY) or {}


def canon_id(host, port=443, protocol="openvpn", digest=""):
    return f"{CANON}:{protocol}:{host}:{port}" + (f":{digest}" if digest else "")


# ---------------------------------------------------------------------------
# ① 来源收敛与节点并集（验收：来源只剩合并键；节点数=按身份取并集）
# ---------------------------------------------------------------------------
class TestSourceReduction(unittest.TestCase):
    def test_only_canonical_source_remains(self):
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"}),
            rec(FEED, HOST2, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec("manual", HOST3, protocol="vless", name="手导",
                config_ref="vless://u@node3.example.net:443?x=1"),
            rec("pipeline", HOST_THIRD, name="流水线"),
        ]
        out = mp.migrate_library(lib)
        self.assertEqual({r["source"] for r in out},
                         {CANON, "manual", "pipeline"})
        for r in out:
            self.assertNotIn(r["source"], mp.LEGACY_SOURCE_KEYS)

    def test_node_count_is_identity_union(self):
        """3 条 feed + 2 条 openvpn，其中 1 对同端点撞身份 → 并集 4 台，一台不丢。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec(FEED, HOST2, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec(FEED, HOST3, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"}),
            rec(OVPN, HOST_THIRD, config_ref=CFG_TEXT2, extra={"config_format": "text"}),
        ]
        out = mp.migrate_library(lib)
        self.assertEqual(len(out), 4)
        before = {(r["protocol"], r["host"], r["port"]) for r in lib}
        after = {(r["protocol"], r["host"], r["port"]) for r in out}
        self.assertEqual(before, after)                 # 端点并集，一台不丢

    def test_merged_id_matches_canonical_formula(self):
        lib = [rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
               rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"})]
        out = mp.migrate_library(lib)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], canon_id(HOST1))

    def test_digest_carry_over(self):
        """带 digest 的旧 id（防御路径）：digest 沿用，不重算不丢段。"""
        old_id = f"{OVPN}:openvpn:{HOST1}:443:deadbeef"
        r = rec(OVPN, HOST1, extra={"config_format": "text"})
        r["id"] = old_id
        out = mp.migrate_library([r])
        self.assertEqual(out[0]["id"], canon_id(HOST1, digest="deadbeef"))


# ---------------------------------------------------------------------------
# ② 逐字段无损（验收红线：验真出口/样本、钉选、封禁、OpenVPN 配置都在）
# ---------------------------------------------------------------------------
class TestVerifiedExitPreserved(unittest.TestCase):
    def test_one_sided_verification_kept(self):
        """只有 feed 侧验过 → 画像整体沿用（merge_fresh 同口径）。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"},
                exit_profile=profile(ip=IP_A, verified_at=100.0,
                                     dirty=["muse"], is_clean=True)),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"}),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["exit_profile"]["ip"], IP_A)
        self.assertEqual(out["exit_profile"]["verified_at"], 100.0)
        self.assertEqual(out["exit_profile"]["dirty"], ["muse"])

    def test_only_ovpn_side_verification_kept(self):
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"},
                exit_profile=profile(ip=IP_B, verified_at=200.0)),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["exit_profile"]["ip"], IP_B)

    def test_both_verified_newer_wins_dirty_union(self):
        """双侧都验过：新快照为主，dirty 并集（拒绝史不丢），旧 ip 进快照。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"},
                exit_profile=profile(ip=IP_A, verified_at=100.0,
                                     dirty=["muse"])),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"},
                exit_profile=profile(ip=IP_B, verified_at=200.0,
                                     dirty=["dirty_ip"])),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["exit_profile"]["ip"], IP_B)          # 新者为主
        self.assertEqual(out["exit_profile"]["verified_at"], 200.0)
        self.assertEqual(out["exit_profile"]["dirty"], ["dirty_ip", "muse"])  # 并集
        # 出口漂移史：旧画像完整留在快照里
        blocks = list(stash_of(out).values())
        old_ips = {b["exit_profile"]["ip"] for b in blocks
                   if b.get("exit_profile", {}).get("ip")}
        self.assertIn(IP_A, old_ips)


class TestVerifySamplesPreserved(unittest.TestCase):
    def test_single_side_latency_kept(self):
        """验真样本（实测延迟/抖动）：恰一侧非 None → 该侧（None≠0 口径）。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"},
                metrics_dict=metrics()),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"},
                metrics_dict=metrics(latency_ms=88.0, jitter_ms=12.0)),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["metrics"]["latency_ms"], 88.0)
        self.assertEqual(out["metrics"]["jitter_ms"], 12.0)

    def test_latency_conflict_newer_measurement_wins(self):
        """两侧都测过：verified_at 新侧的实测值胜（apply_outcome 同步写入）。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"},
                exit_profile=profile(ip=IP_A, verified_at=100.0),
                metrics_dict=metrics(latency_ms=120.0)),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"},
                exit_profile=profile(ip=IP_B, verified_at=200.0),
                metrics_dict=metrics(latency_ms=88.0)),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["metrics"]["latency_ms"], 88.0)
        # 旧实测值在快照里可追溯
        blocks = list(stash_of(out).values())
        old_lats = {b["metrics"]["latency_ms"] for b in blocks
                    if b.get("metrics", {}).get("latency_ms") is not None}
        self.assertIn(120.0, old_lats)

    def test_zero_never_masquerades_as_measured(self):
        """来源侧 0 不是实测（R6）：一侧实测 88、一侧 None → 取 88，绝不被 0 盖。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"},
                metrics_dict=metrics(latency_ms=None, sessions=5)),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"},
                metrics_dict=metrics(latency_ms=88.0)),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["metrics"]["latency_ms"], 88.0)

    def test_feed_reported_metrics_feed_side_wins(self):
        """sessions/speed/uptime 是 feed 报告值：冲突 → feed 侧。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"},
                metrics_dict=metrics(sessions=42, speed_mbps=9.5,
                                     uptime_hours=120.0)),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"},
                metrics_dict=metrics(sessions=7, speed_mbps=1.0,
                                     uptime_hours=3.0)),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["metrics"]["sessions"], 42)
        self.assertEqual(out["metrics"]["speed_mbps"], 9.5)
        self.assertEqual(out["metrics"]["uptime_hours"], 120.0)


class TestPinBanScenes(unittest.TestCase):
    def test_pin_and_ban_union(self):
        """钉选/封禁是人工意图：并集，钉在哪页都不丢；两标记可同时为真。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"},
                pinned=True),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"},
                banned=True),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertTrue(out["pinned"])
        self.assertTrue(out["banned"])

    def test_single_sided_pin_kept(self):
        lib = [rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
               rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"},
                   pinned=True)]
        self.assertTrue(mp.migrate_library(lib)[0]["pinned"])

    def test_scenes_union(self):
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"},
                scenes={"support"}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"},
                scenes={"ai", "games"}),
        ]
        self.assertEqual(mp.migrate_library(lib)[0]["scenes"],
                         ["ai", "games", "support"])


class TestOpenVpnConfigPreserved(unittest.TestCase):
    def test_identical_configs_single_primary(self):
        """feed 与镜像解析同一份 feed：b64 逐字节相同 → 一份主配置。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec(OVPN, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["config_ref"], CFG_B64)
        self.assertEqual(out["extra"]["config_format"], "b64")

    def test_b64_primary_text_kept_in_snapshot(self):
        """配置冲突：官方 b64 为主（登记刷新稳态），手导明文整份进快照不丢。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT2, extra={"config_format": "text"}),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["config_ref"], CFG_B64)
        self.assertEqual(out["extra"]["config_format"], "b64")
        blocks = list(stash_of(out).values())
        texts = {b["config_ref"] for b in blocks
                 if (b.get("extra") or {}).get("config_format") == "text"}
        self.assertEqual(texts, {CFG_TEXT2})            # 手导原文完整可追溯

    def test_hand_ovpn_without_twin_keeps_text_config(self):
        """手导 .ovpn 无孪生（第三方服务器）：只改键重算 id，配置原样。"""
        lib = [rec(OVPN, HOST2, config_ref=CFG_TEXT2,
                   extra={"config_format": "text"}, name="第三方.ovpn")]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["source"], CANON)
        self.assertEqual(out["id"], canon_id(HOST2))
        self.assertEqual(out["config_ref"], CFG_TEXT2)
        self.assertEqual(out["extra"]["config_format"], "text")
        self.assertNotIn(mp.MERGED_SUPERSEDED_KEY, out["extra"])   # 没合并就没快照


# ---------------------------------------------------------------------------
# ③ extra 状态保全（orig_name/隔离/消失标记/登记戳/feed 溯源/未知键）
# ---------------------------------------------------------------------------
class TestExtraStatePreserved(unittest.TestCase):
    def test_name_and_orig_name_paired(self):
        """实测改名侧胜出；name 与 orig_name 同侧配对（restore-names 依赖）。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text",
                                                        "orig_name": "手导.ovpn"},
                name="日本 · 198.51.100.7 · NTT"),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["name"], "日本 · 198.51.100.7 · NTT")
        self.assertEqual(out["extra"]["orig_name"], "手导.ovpn")

    def test_name_default_feed_side(self):
        """无实测改名：feed 侧名（hostname）为准，对齐登记刷新稳态。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"},
                name="手导.ovpn"),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["name"], HOST1)
        blocks = list(stash_of(out).values())
        self.assertIn("手导.ovpn", {b["name"] for b in blocks})

    def test_quarantine_kept(self):
        """隔离标记迁移不解除；两侧都有 → reason 去重拼接。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64,
                extra={"config_format": "b64", "quarantined": "1",
                       "quarantine_reason": "muse"}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT,
                extra={"config_format": "text", "quarantined": "1",
                       "quarantine_reason": "muse,dirty_ip"}),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["extra"]["quarantined"], "1")
        self.assertEqual(out["extra"]["quarantine_reason"], "dirty_ip,muse")

    def test_gone_mark_kept(self):
        """消失标记三键一组保留（误标由下次更新的回归机制自愈）。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT,
                extra={"config_format": "text", "source_gone": "1",
                       "source_gone_at": 500.0,
                       "source_gone_from": "feed-mirror"}),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["extra"]["source_gone"], "1")
        self.assertEqual(out["extra"]["source_gone_at"], 500.0)
        self.assertEqual(out["extra"]["source_gone_from"], "feed-mirror")

    def test_registry_stamp_rewrite(self):
        """镜像戳 → 合并后唯一条目 feed；feed 页无戳存量补戳；手导无戳不补。"""
        mirrored = rec(OVPN, HOST1, config_ref=CFG_B64,
                       extra={"config_format": "b64",
                              "registry_entry": "feed-mirror"})
        hand = rec(OVPN, HOST2, config_ref=CFG_TEXT, extra={"config_format": "text"})
        feed_unstamped = rec(FEED, HOST3, config_ref=CFG_B64,
                             extra={"config_format": "b64"})
        out = mp.migrate_library([mirrored, hand, feed_unstamped])
        by_host = {r["host"]: r for r in out}
        self.assertEqual(by_host[HOST1]["extra"]["registry_entry"], "feed")
        self.assertNotIn("registry_entry", by_host[HOST2]["extra"])   # 手导保护
        self.assertEqual(by_host[HOST3]["extra"]["registry_entry"], "feed")

    def test_feed_provenance_extras_union(self):
        """feed 溯源键并集；单侧有的进主位。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64,
                extra={"config_format": "b64", "entry_ip": IP_A,
                       "country": "Japan", "country_code": "JP",
                       "feed_score": 12345, "feed_ping": 120}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"}),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["extra"]["entry_ip"], IP_A)
        self.assertEqual(out["extra"]["country_code"], "JP")
        self.assertEqual(out["extra"]["feed_score"], 12345)

    def test_unknown_extra_keys_both_retrievable(self):
        """未知 extra 键双侧不等：主位放 side-priority 胜者，败值在快照里。"""
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64,
                extra={"config_format": "b64", "note": "from-feed"}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT,
                extra={"config_format": "text", "note": "from-hand"}),
        ]
        out = mp.migrate_library(lib)[0]
        self.assertEqual(out["extra"]["note"], "from-feed")     # feed 侧主位
        blocks = list(stash_of(out).values())
        self.assertIn("from-hand", {b.get("extra", {}).get("note") for b in blocks})


# ---------------------------------------------------------------------------
# ④ 通用无损：每条旧记录的完整原样可追溯（红线兜底的结构证明）
# ---------------------------------------------------------------------------
class TestWholeRecordLosslessness(unittest.TestCase):
    LIB = [
        rec(FEED, HOST1, config_ref=CFG_B64,
            extra={"config_format": "b64", "feed_score": 100},
            exit_profile=profile(ip=IP_A, verified_at=100.0, dirty=["muse"]),
            metrics_dict=metrics(latency_ms=120.0, sessions=9),
            scenes={"ai"}, pinned=True),
        rec(OVPN, HOST1, config_ref=CFG_TEXT2,
            extra={"config_format": "text", "orig_name": "手导.ovpn",
                   "quarantined": "1", "quarantine_reason": "muse"},
            name="改名后的出口名", metrics_dict=metrics(latency_ms=88.0),
            banned=True),
        rec(FEED, HOST2, config_ref=CFG_B64, extra={"config_format": "b64"}),
        rec(OVPN, HOST3, config_ref=CFG_TEXT2, extra={"config_format": "text"}),
        rec("manual", HOST2, protocol="vless", name="手导链接",
            config_ref="vless://uuid@example.com:443"),
        rec("pipeline", HOST_THIRD, name="流水线"),
    ]

    def test_every_original_record_retrievable(self):
        """每条旧记录可追溯，三选一：原样存活（范围外来源）／整份在快照里
        （撞身份被合并）／单条改写路径下数据字段逐项相等（只有身份键与登记
        戳按设计变换）。"""
        before = copy.deepcopy(self.LIB)
        after = mp.migrate_library(before)
        blocks = []
        for r in after:
            blocks.extend(stash_of(r).values())
        for old in before:
            if old["source"] not in mp.LEGACY_SOURCE_KEYS:
                self.assertTrue(any(r == old for r in after),
                                f"范围外记录被改动: {old['id']}")
                continue
            matches = [r for r in after
                       if mp.identity_of(r) == mp.identity_of(old)]
            self.assertEqual(len(matches), 1, old["id"])
            r = matches[0]
            if r == old or any(b == old for b in blocks):
                continue                      # 原样存活 / 整份在快照
            self.assertEqual(r["source"], CANON)                       # 改写路径
            self.assertEqual(r["id"], mp.canonical_id(old))
            for key in ("name", "protocol", "host", "port", "config_ref",
                        "exit_profile", "scenes", "metrics", "pinned",
                        "banned"):
                self.assertEqual(r[key], old[key], f"{old['id']}.{key}")
            for key, value in old["extra"].items():
                self.assertEqual(r["extra"].get(key), value,
                                 f"{old['id']}.extra.{key}")

    def test_snapshot_blocks_are_complete_originals(self):
        """快照块＝合并前原始记录的深拷贝（含旧 source/id 与全部字段）。"""
        out = mp.migrate_library(self.LIB)
        merged = next(r for r in out if r["host"] == HOST1)
        blocks = stash_of(merged)
        self.assertEqual(set(blocks),
                         {f"{FEED}:openvpn:{HOST1}:443",
                          f"{OVPN}:openvpn:{HOST1}:443"})
        for block in blocks.values():
            original = next(o for o in self.LIB if o["id"] == block["id"])
            self.assertEqual(block, original)           # 逐字段原样
            self.assertIsNot(block, original)           # 且是副本，不改输入

    def test_inputs_not_mutated(self):
        """merge_records / migrate_library 都不改输入（纯函数）。"""
        before = copy.deepcopy(self.LIB)
        after = mp.migrate_library(self.LIB)
        self.assertEqual(self.LIB, before)
        a = self.LIB[0]
        b = self.LIB[1]
        mp.merge_records(copy.deepcopy(a), copy.deepcopy(b))
        self.assertEqual(self.LIB, before)

    def test_merge_records_symmetric(self):
        """merge_records 内容驱动：交换参数结果一致（§3.0）。"""
        a, b = self.LIB[0], self.LIB[1]
        self.assertEqual(mp.merge_records(copy.deepcopy(a), copy.deepcopy(b)),
                         mp.merge_records(copy.deepcopy(b), copy.deepcopy(a)))


# ---------------------------------------------------------------------------
# ⑤ 幂等（验收：连跑两遍结果相同）
# ---------------------------------------------------------------------------
class TestIdempotency(unittest.TestCase):
    LIB = TestWholeRecordLosslessness.LIB

    @staticmethod
    def _frozen(records):
        return json.dumps(records, ensure_ascii=False, sort_keys=True)

    def test_migrate_twice_identical(self):
        once = mp.migrate_library(self.LIB)
        twice = mp.migrate_library(once)
        self.assertEqual(self._frozen(once), self._frozen(twice))

    def test_second_pass_touches_nothing_semantically(self):
        """第二遍每身份恰一条、无旧键可改：记录数与 id 完全不变。"""
        once = mp.migrate_library(self.LIB)
        twice = mp.migrate_library(once)
        self.assertEqual(len(once), len(twice))
        self.assertEqual([r["id"] for r in once], [r["id"] for r in twice])

    def test_merge_records_self_noop(self):
        r = self.LIB[0]
        self.assertEqual(mp.merge_records(copy.deepcopy(r), copy.deepcopy(r)),
                         copy.deepcopy(r))

    def test_migrate_single_record_is_rewrite_only(self):
        """单条记录（无撞身份）：只改键重算 id 与戳，其余字段逐项不变。"""
        r = rec(FEED, HOST1, config_ref=CFG_B64,
                extra={"config_format": "b64", "feed_ping": 99},
                exit_profile=profile(ip=IP_A, verified_at=1.0),
                metrics_dict=metrics(latency_ms=50.0), pinned=True)
        out = mp.migrate_library([r])[0]
        self.assertEqual(out["source"], CANON)
        self.assertEqual(out["id"], canon_id(HOST1))
        for key in ("name", "protocol", "host", "port", "config_ref",
                    "exit_profile", "scenes", "metrics", "pinned", "banned"):
            self.assertEqual(out[key], r[key], key)
        self.assertEqual(out["extra"]["feed_ping"], 99)


# ---------------------------------------------------------------------------
# ⑥ 验真样本表重映射（§4.3）
# ---------------------------------------------------------------------------
class TestSampleRemap(unittest.TestCase):
    def make_lib(self):
        lib = [
            rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"}),
            rec(OVPN, HOST1, config_ref=CFG_TEXT, extra={"config_format": "text"}),
        ]
        samples = [
            {"node_id": f"{FEED}:openvpn:{HOST1}:443", "ts": 1.0,
             "kind": "verify", "success": 1, "latency_ms": 100.0},
            {"node_id": f"{FEED}:openvpn:{HOST1}:443", "ts": 2.0,
             "kind": "verify", "success": 1, "latency_ms": 110.0},
            {"node_id": f"{OVPN}:openvpn:{HOST1}:443", "ts": 3.0,
             "kind": "connect", "success": 0, "latency_ms": None},
        ]
        return lib, samples

    def test_all_samples_land_on_merged_id(self):
        """两条旧记录的样本全部落到合并后同一 id：一台不丢、一行不丢。"""
        lib, samples = self.make_lib()
        before = copy.deepcopy(lib)
        after = mp.migrate_library(lib)
        mapping = mp.sample_id_map(before, after)
        self.assertEqual(len(mapping), 2)
        remapped = mp.remap_samples(samples, mapping)
        self.assertEqual(len(remapped), len(samples))            # 行数不变
        self.assertEqual({s["node_id"] for s in remapped},
                         {canon_id(HOST1)})                      # 全部落到合并 id
        self.assertEqual(sum(1 for s in remapped if s["kind"] == "verify"), 2)

    def test_samples_content_untouched(self):
        lib, samples = self.make_lib()
        after = mp.migrate_library(lib)
        remapped = mp.remap_samples(samples, mp.sample_id_map(lib, after))
        for old, new in zip(samples, remapped):
            self.assertEqual(
                {k: v for k, v in old.items() if k != "node_id"},
                {k: v for k, v in new.items() if k != "node_id"})

    def test_foreign_samples_pass_through(self):
        lib = [rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"})]
        after = mp.migrate_library(lib)
        foreign = [{"node_id": "manual:vless:elsewhere.example.net:443",
                    "ts": 9.0, "kind": "verify", "success": 1}]
        out = mp.remap_samples(foreign, mp.sample_id_map(lib, after))
        self.assertEqual(out, foreign)                  # 映射外原样


# ---------------------------------------------------------------------------
# ⑦ sources.json 登记表迁移（§5.3）
# ---------------------------------------------------------------------------
class TestRegistryMigration(unittest.TestCase):
    def reg(self, entries, last_updates=None):
        return {"version": 1, "entries": entries,
                "last_updates": last_updates or {}}

    def test_mirror_removed_and_page_rewritten(self):
        data = self.reg(
            [{"id": "feed", "type": "feed", "page": "vpngate_feed",
              "address": mp.FEED_DEFAULT_ADDRESS, "name": "官方", "enabled": True},
             {"id": "feed-mirror", "type": "feed_mirror", "page": "openvpn",
              "address": "", "name": "镜像", "enabled": True},
             {"id": "sub-abcd1234", "type": "subscription", "page": "manual",
              "address": "https://sub.example.net/s", "name": "", "enabled": True}],
            {"feed": {"ts": 1.0, "ok": True},
             "feed-mirror": {"ts": 2.0, "ok": False, "error": "x"}})
        out = mp.migrate_registry_json(data)
        ids = {e["id"] for e in out["entries"]}
        self.assertIn("feed", ids)
        self.assertNotIn("feed-mirror", ids)
        feed = next(e for e in out["entries"] if e["id"] == "feed")
        self.assertEqual(feed["page"], CANON)
        self.assertEqual(feed["address"], mp.FEED_DEFAULT_ADDRESS)  # 未被同址镜像改写
        self.assertIn("sub-abcd1234", ids)                          # 订阅条目不动
        self.assertNotIn("feed-mirror", out["last_updates"])        # 镜像记账删除
        self.assertIn("feed", out["last_updates"])

    def test_mirror_promoted_when_feed_absent(self):
        """只登记过镜像：就地转正，自定义地址与记账都保住。"""
        data = self.reg(
            [{"id": "feed-mirror", "type": "feed_mirror", "page": "openvpn",
              "address": "https://mirror.example.net/json/data.json",
              "name": "镜像", "enabled": False, "created_at": 42.0}],
            {"feed-mirror": {"ts": 9.0, "ok": True}})
        out = mp.migrate_registry_json(data)
        self.assertEqual(len(out["entries"]), 1)
        e = out["entries"][0]
        self.assertEqual(e["id"], "feed")
        self.assertEqual(e["type"], "feed")
        self.assertEqual(e["page"], CANON)
        self.assertEqual(e["address"], "https://mirror.example.net/json/data.json")
        self.assertFalse(e["enabled"])
        self.assertEqual(out["last_updates"].get("feed"), {"ts": 9.0, "ok": True})

    def test_custom_mirror_address_adopted(self):
        """镜像登记了自定义地址而 feed 是官方默认：显式登记优先。"""
        data = self.reg(
            [{"id": "feed", "type": "feed", "page": "vpngate_feed",
              "address": mp.FEED_DEFAULT_ADDRESS, "name": "", "enabled": True},
             {"id": "feed-mirror", "type": "feed_mirror", "page": "openvpn",
              "address": "https://mirror.example.net/json/data.json",
              "name": "", "enabled": True}])
        out = mp.migrate_registry_json(data)
        feed = next(e for e in out["entries"] if e["id"] == "feed")
        self.assertEqual(feed["address"], "https://mirror.example.net/json/data.json")
        self.assertEqual(len(out["entries"]), 1)

    def test_registry_migration_idempotent(self):
        data = self.reg(
            [{"id": "feed", "type": "feed", "page": "vpngate_feed",
              "address": mp.FEED_DEFAULT_ADDRESS, "name": "", "enabled": True},
             {"id": "feed-mirror", "type": "feed_mirror", "page": "openvpn",
              "address": "", "name": "", "enabled": True}],
            {"feed-mirror": {"ts": 1.0, "ok": True}})
        once = mp.migrate_registry_json(data)
        twice = mp.migrate_registry_json(once)
        self.assertEqual(json.dumps(once, sort_keys=True),
                         json.dumps(twice, sort_keys=True))

    def test_already_migrated_registry_untouched(self):
        data = self.reg(
            [{"id": "feed", "type": "feed", "page": CANON,
              "address": mp.FEED_DEFAULT_ADDRESS, "name": "", "enabled": True}])
        self.assertEqual(mp.migrate_registry_json(data),
                         copy.deepcopy(data))


# ---------------------------------------------------------------------------
# ⑧ 防御与边界
# ---------------------------------------------------------------------------
class TestDefensive(unittest.TestCase):
    def test_identity_mismatch_rejected(self):
        a = rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"})
        b = rec(OVPN, HOST2, config_ref=CFG_TEXT, extra={"config_format": "text"})
        with self.assertRaises(ValueError):
            mp.merge_records(a, b)

    def test_non_dict_record_rejected(self):
        with self.assertRaises(TypeError):
            mp.migrate_library([rec(FEED, HOST1), "not-a-dict"])
        with self.assertRaises(TypeError):
            mp.migrate_library(None)

    def test_unknown_source_passes_through(self):
        """未知来源记录原样保留（不丢也不越权改写）。"""
        r = rec("mystery", HOST3, name="未知来源")
        out = mp.migrate_library([r])
        self.assertEqual(out, [r])

    def test_duplicate_input_dedupes(self):
        """调用方喂了重复记录（store 不可能出现的防御路径）：并成一条不炸。"""
        r = rec(FEED, HOST1, config_ref=CFG_B64, extra={"config_format": "b64"})
        out = mp.migrate_library([copy.deepcopy(r), copy.deepcopy(r)])
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["id"], canon_id(HOST1))


if __name__ == "__main__":
    unittest.main()
