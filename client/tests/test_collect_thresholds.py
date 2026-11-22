#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task31 验收：自动搜集门槛与开关定稿（阶段五·商业级打磨 第五段）
==========================================================================

对「拷入 client-dev 相关文件并打上本段 diff 的副本」跑（本文件 sys.path
指向所在目录，即副本的 client 包根）。覆盖（对齐 work31 DESIGN §9）：

1. settings 新字段五项：缺省默认关/默认数、非法值拒收（bool 非 bool、
   正整数给 0/负数/字符串、非负给负数/字符串/bool）、未知键拒收、
   save→load 回读一致、to_dict 恰含声明字段、usage_dict 不含新键、
   merge 保回新字段；门槛数字解析单一事实源（cli 兜底 = Settings 默认）。
2. 门槛：`_collect_gate_ok` 单参形态 = task27 纯基础口径（零行为变化）；
   连续通过计数跨轮累积、失败清零、污染=清零；未达 `collect_promote_
   min_ok` 只记账不入库、达标才入库；账本候选档案（collect_ledger.json
   种子记录 collect_candidates 子键）经 record_result + save/load 回读
   无损——collector.py 零改动的机械锁定。
3. 节奏：每轮上限截断（池大只验前 N 个，余下记「本轮未验」不记死、
   下轮重验）；候选间隔注假时钟验证（首前末后不睡、delay 0 不睡、
   默认口径 1000ms）。
4. 开关：关时手动 `sources collect` 照跑、报告注明「关」；开时注明
   「开（每 N 分钟…）」；损坏 settings.json → 按默认口径跑完并注明；
   GUI（PySide6 在场才跑，缺席整组 skip）：定时器默认不启、开+间隔
   激活、QTimer 单步触发、守卫不触发、设置页保存按新值重建、失败收尾
   不弹红条不碰状态栏、成功不抢状态栏、跑轮复用 cli._sources_collect。
5. 端到端两轮对账（CLI + 落盘 settings.json）：第一轮暂存+截断、第二轮
   达标入库、账本 streak 累积；输出红线（零地址原文）。

跑法：cd work31 && python3 -m unittest（或 -v 看明细）。
红线：本文件出现的全部 http(s) 地址都是 RFC 2606 *.example（假数据沿用
task27 验收测试的同一套假种子/假候选/假探针），节点与出口 IP 用
RFC 1918/5737 保留段，零真实外网（socket 全程站岗）。
"""
import base64
import contextlib
import io
import os
import re
import shutil
import socket
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from client.core.model import ExitProfile                # noqa: E402
from client.core.settings_proto import (Settings,        # noqa: E402
                                        SettingsError, SettingsStore, merge)
from client.core.store import Store                      # noqa: E402
from client.core.verify import VerifyOutcome             # noqa: E402
from client.sources import collector as C                # noqa: E402
from client.sources import manual as manual_src          # noqa: E402
from client.sources import seed_config as SC             # noqa: E402
from client.tests.test_sources_collect import (          # noqa: E402
    BASE_SEEDS, CAND_A, CollectCase, FAKE_EXIT_IP, FakeSingBoxEngine,
    FetchLog, FORBIDDEN_IN_OUTPUT, PAGES, SEED_AGG_ADDR, SocketGuard,
    VLESS_ONE, VLESS_TWO, probe_result)
from client.ui import cli                                # noqa: E402

VLESS_THREE = ("vless://33333333-4444-5555-6666-777777777777"
               "@10.0.0.7:443?security=tls#collect-three")

# 设置落点（与库同目录，task24 §4.1 口径——main() 解析 settings.json 用
# settings_file_path(explicit_db)，即本目录）
SETTINGS_FILE = "settings.json"


def _ok_outcome(exit_ip=FAKE_EXIT_IP):
    """基础口径通过：验真活 + 出口干净（假画像，RFC 5737 测试段）。"""
    return VerifyOutcome(ok=True, exit_profile=ExitProfile(
        ip=exit_ip, country="Japan", country_code="JP",
        network_type="residential", is_clean=True, dirty=set()))


def _polluted_outcome(exit_ip="203.0.113.8"):
    """基础口径不过：出口污染（is_clean=False）。"""
    return VerifyOutcome(ok=True, exit_profile=ExitProfile(
        ip=exit_ip, country="Japan", country_code="JP",
        network_type="datacenter", is_clean=False, dirty=set()))


def _dead_outcome(reason="假探针：链路不通"):
    return VerifyOutcome(ok=False, reason=reason)


def _pool_item(vless=VLESS_ONE, seed_id="seed-x", cand=CAND_A):
    n = manual_src.parse_link(vless)
    n.extra.update({"collect_seed_id": seed_id,
                    "collect_seed_category": "agg_repo",
                    "collect_round": "R-test",
                    "collected_from": cand})
    return n


def _write_settings(db_path, **overrides):
    """在库同目录落一张 settings.json（main() 解析落点 = 库目录）。"""
    spath = os.path.join(os.path.dirname(os.path.abspath(db_path)),
                         SETTINGS_FILE)
    SettingsStore(spath).save(merge(Settings(), overrides))
    return spath


# ---------------------------------------------------------------------------
# settings 新字段（task31 五项）
# ---------------------------------------------------------------------------
class TestSettingsCollectFields(unittest.TestCase):

    def test_defaults_conservative(self):
        """默认值 = DESIGN §2 定稿：开关关 + 30 分钟 + 每轮 10 + 间隔
        1000ms + 门槛 1（task27「过即进」口径不变）。"""
        s = Settings()
        self.assertIs(s.auto_collect_enabled, False)     # 铁律：默认关
        self.assertEqual(s.collect_interval_min, 30)
        self.assertEqual(s.collect_verify_per_round, 10)
        self.assertEqual(s.collect_verify_delay_ms, 1000)
        self.assertEqual(s.collect_promote_min_ok, 1)

    def test_switch_requires_strict_bool(self):
        s = Settings(auto_collect_enabled=True)
        self.assertIs(s.auto_collect_enabled, True)
        for bad in (0, 1, "true", None, 1.0):
            with self.assertRaises(SettingsError):
                Settings(auto_collect_enabled=bad)

    def test_positive_int_fields_reject_bad(self):
        for name in ("collect_interval_min", "collect_verify_per_round",
                     "collect_promote_min_ok"):
            for bad in (0, -1, "10", True, 1.5):
                with self.assertRaises(SettingsError, msg=name):
                    Settings(**{name: bad})
            s = Settings(**{name: 1})
            self.assertEqual(getattr(s, name), 1)

    def test_delay_allows_zero_rejects_negative(self):
        self.assertEqual(Settings(collect_verify_delay_ms=0)
                         .collect_verify_delay_ms, 0)    # 0 = 显式关闭
        for bad in (-1, "500", False, 2.5):
            with self.assertRaises(SettingsError):
                Settings(collect_verify_delay_ms=bad)

    def test_unknown_key_rejected(self):
        d = Settings().to_dict()
        with self.assertRaises(SettingsError):
            Settings.from_dict({**d, "collect_interval_minutes": 5})
        with self.assertRaises(SettingsError):
            merge(Settings(), {"collect_verify_limit": 5})

    def test_save_load_roundtrip(self):
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        spath = os.path.join(tmp, SETTINGS_FILE)
        s = Settings(auto_collect_enabled=True, collect_interval_min=45,
                     collect_verify_per_round=3, collect_verify_delay_ms=0,
                     collect_promote_min_ok=2)
        SettingsStore(spath).save(s)
        loaded = SettingsStore(spath).load()
        self.assertEqual(loaded, s)
        self.assertIs(loaded.auto_collect_enabled, True)

    def test_missing_fields_get_defaults(self):
        """旧版 settings.json（缺新键）照读补默认——字段层前向兼容。"""
        d = Settings().to_dict()
        for k in ("auto_collect_enabled", "collect_interval_min",
                  "collect_verify_per_round", "collect_verify_delay_ms",
                  "collect_promote_min_ok"):
            d.pop(k)
        s = Settings.from_dict(d)
        self.assertIs(s.auto_collect_enabled, False)
        self.assertEqual(s.collect_interval_min, 30)

    def test_to_dict_usage_dict_merge_shapes_unchanged(self):
        s = Settings()
        d = s.to_dict()
        self.assertEqual(set(d), set(Settings.__dataclass_fields__))
        for k in ("auto_collect_enabled", "collect_interval_min",
                  "collect_verify_per_round", "collect_verify_delay_ms",
                  "collect_promote_min_ok"):
            self.assertIn(k, d)                      # 尾部追加五键
            self.assertNotIn(k, s.usage_dict())      # usage_dict 仍只五项规则
        m = merge(s, {"socks_port": 7899})
        self.assertEqual(m.collect_verify_per_round, 10)   # 未编辑字段保回
        self.assertEqual(m.socks_port, 7899)

    def test_gate_values_single_source(self):
        """门槛数字解析单一事实源：cli 兜底（None）= Settings() 默认，
        cli 层不另立常数（work31 DESIGN §6.1/§8-1）。"""
        self.assertEqual(cli._collect_gate_values(None),
                         cli._collect_gate_values(Settings()))
        gate = cli._collect_gate_values(
            Settings(auto_collect_enabled=True, collect_interval_min=45,
                     collect_verify_per_round=3, collect_verify_delay_ms=0,
                     collect_promote_min_ok=2))
        self.assertEqual(gate, {"enabled": True, "interval_min": 45,
                                "per_round": 3, "delay_ms": 0,
                                "promote_min": 2})


# ---------------------------------------------------------------------------
# 门槛：连续通过计数（_collect_gate_ok 具名空槽收口）
# ---------------------------------------------------------------------------
class TestGateStreak(unittest.TestCase):

    def _ledger_with(self, node, seed_id="seed-x"):
        ledger = C.new_ledger()
        return ledger

    def test_single_arg_is_pure_task27_base(self):
        """单参形态 = task27 基础口径，零行为变化（既有测试零改动）。"""
        self.assertTrue(cli._collect_gate_ok(_ok_outcome()))
        self.assertFalse(cli._collect_gate_ok(_polluted_outcome()))
        self.assertFalse(cli._collect_gate_ok(_dead_outcome()))

    def test_streak_accumulates_across_rounds(self):
        """连续计数跨轮累积：3 连过才放行（promote_min=3），前两轮只记账。"""
        ledger = C.new_ledger()
        node = _pool_item()
        for i, expect in enumerate((False, False, True), start=1):
            ok = cli._collect_gate_ok(_ok_outcome(), ledger=ledger,
                                      item=node, promote_min=3, now=1000.0 + i)
            self.assertIs(ok, expect)
        prof = ledger["seeds"]["seed-x"]["collect_candidates"][node.id]
        self.assertEqual(prof["streak"], 3)
        self.assertEqual(prof["last_status"], "passed")

    def test_fail_resets_streak(self):
        """失败清零：两连过后一败，streak 归 0、last_status=failed。"""
        ledger = C.new_ledger()
        node = _pool_item()
        for ok_out in (_ok_outcome(), _ok_outcome(), _dead_outcome()):
            cli._collect_gate_ok(ok_out, ledger=ledger, item=node,
                                 promote_min=3, now=1000.0)
        prof = ledger["seeds"]["seed-x"]["collect_candidates"][node.id]
        self.assertEqual((prof["streak"], prof["last_status"]), (0, "failed"))

    def test_polluted_counts_as_fail(self):
        """污染 = 基础口径不过 = 清零（不是「通过但暂存」）。"""
        ledger = C.new_ledger()
        node = _pool_item()
        cli._collect_gate_ok(_ok_outcome(), ledger=ledger, item=node,
                             promote_min=2, now=1000.0)
        cli._collect_gate_ok(_polluted_outcome(), ledger=ledger, item=node,
                             promote_min=2, now=1001.0)
        prof = ledger["seeds"]["seed-x"]["collect_candidates"][node.id]
        self.assertEqual((prof["streak"], prof["last_status"]), (0, "failed"))

    def test_ledger_subkey_survives_record_result_and_roundtrip(self):
        """collector 零改动的机械锁定：候选档案子键经 record_result 与
        save/load 回读原样存活（R0–R8 与 13 字段 schema 零触碰）。"""
        tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "collect_ledger.json")
        ledger = C.new_ledger()
        node = _pool_item()
        cli._collect_gate_ok(_ok_outcome(), ledger=ledger, item=node,
                             promote_min=1, now=1000.0)
        before = dict(ledger["seeds"]["seed-x"]["collect_candidates"][node.id])
        C.record_result(ledger, "seed-x", ok=True, produced=1, now=2000.0)
        self.assertEqual(ledger["seeds"]["seed-x"]["collect_candidates"][node.id],
                         before)                       # record_result 透明
        C.save_ledger(path, ledger)
        loaded = C.load_ledger(path)
        self.assertEqual(
            loaded["seeds"]["seed-x"]["collect_candidates"][node.id], before)
        # 既有 13 字段照常记账（R1 正常成功）
        rec = loaded["seeds"]["seed-x"]
        self.assertEqual((rec["ok_count"], rec["produced_total"]), (1, 1))

    def test_no_seed_id_falls_back_to_base(self):
        """无溯源键的池条目（正常轮不会出现）：退基础口径不记账。"""
        ledger = C.new_ledger()
        node = manual_src.parse_link(VLESS_ONE)
        self.assertTrue(cli._collect_gate_ok(_ok_outcome(), ledger=ledger,
                                             item=node, promote_min=5,
                                             now=1000.0))
        self.assertEqual(ledger["seeds"], {})


# ---------------------------------------------------------------------------
# 节奏：每轮上限截断 + 候选间隔（_collect_enter_store 具名空槽收口）
# ---------------------------------------------------------------------------
class TestRoundCapAndDelay(unittest.TestCase):

    def _run(self, pool, settings=None, ledger=None, sleeper=None,
             probe=None, promote=None):
        report = {"pool": list(pool), "dead_candidates": [],
                  "candidates_dead": 0, "round_id": "R-test"}
        engine = FakeSingBoxEngine()
        probe = probe if probe is not None else mock.MagicMock(
            side_effect=lambda node: probe_result())
        with mock.patch.object(cli, "make_real_probe",
                               lambda *a, **k: probe):
            passed, failed, entered = cli._collect_enter_store(
                Store(self.db), report, engine_factory=lambda: engine,
                settings=settings, ledger=ledger, sleeper=sleeper)
        return report, engine, (passed, failed, entered)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(
            dir=os.path.dirname(os.path.abspath(__file__)))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = os.path.join(self.tmp, "client.db")

    def _settings(self, **kw):
        return merge(Settings(), kw)

    def test_truncation_cap_and_overflow_booking(self):
        """池 3 上限 2：只验前 2 个，余 1 记「本轮未验」（overflow）不进
        死账、streak 不动，下轮自然重验。"""
        ledger = C.new_ledger()
        pool = [_pool_item(VLESS_ONE), _pool_item(VLESS_TWO),
                _pool_item(VLESS_THREE)]
        report, engine, counts = self._run(
            pool, settings=self._settings(collect_verify_per_round=2,
                                          collect_verify_delay_ms=0),
            ledger=ledger)
        self.assertEqual(counts, (2, 0, 2))
        self.assertEqual(engine.started, 2)              # 只起 2 次引擎
        self.assertEqual(report["collect_truncated"], 1)
        self.assertEqual(report["dead_candidates"], [])  # 截断不记死
        third = ledger["seeds"]["seed-x"]["collect_candidates"][pool[2].id]
        self.assertEqual((third["streak"], third["last_status"]),
                         (0, "overflow"))
        store = Store(self.db)
        try:
            self.assertEqual(store.count_nodes(), 2)
        finally:
            store.close()

    def test_overflow_reverified_next_round(self):
        """下轮：上轮截断的候选自然重验（同池序跑第二轮，上限放开）。"""
        ledger = C.new_ledger()
        pool = [_pool_item(VLESS_ONE), _pool_item(VLESS_TWO),
                _pool_item(VLESS_THREE)]
        self._run(pool, settings=self._settings(collect_verify_per_round=2,
                                                collect_verify_delay_ms=0),
                  ledger=ledger)
        report, engine, counts = self._run(
            pool, settings=self._settings(collect_verify_per_round=3,
                                          collect_verify_delay_ms=0),
            ledger=ledger)
        self.assertEqual(counts, (3, 0, 3))
        self.assertEqual(engine.started, 3)              # 第二轮验 3 个
        third = ledger["seeds"]["seed-x"]["collect_candidates"][pool[2].id]
        self.assertEqual((third["streak"], third["last_status"]),
                         (1, "passed"))

    def test_delay_between_candidates_only(self):
        """间隔注假时钟：候选之间睡、首前末后不睡；delay 0 不睡；默认
        口径 1000ms。全程零真实 wall sleep（注入点可注假）。"""
        pool = [_pool_item(VLESS_ONE), _pool_item(VLESS_TWO),
                _pool_item(VLESS_THREE)]
        sleeps = []
        self._run(pool, settings=self._settings(collect_verify_delay_ms=500,
                                                collect_verify_per_round=10),
                  sleeper=sleeps.append)
        self.assertEqual(sleeps, [0.5, 0.5])             # n-1 次、参数=ms/1000
        sleeps = []
        self._run(pool, settings=self._settings(collect_verify_delay_ms=0),
                  sleeper=sleeps.append)
        self.assertEqual(sleeps, [])                     # 0 = 显式关闭
        sleeps = []
        self._run(pool, settings=None, sleeper=sleeps.append)  # 默认口径
        self.assertEqual(sleeps, [1.0, 1.0])             # Settings 默认 1000ms

    def test_held_booked_not_entered(self):
        """连续次数未达：验真通过但只记账不入库（库零字节、不进死账）。"""
        ledger = C.new_ledger()
        pool = [_pool_item(VLESS_ONE)]
        report, engine, counts = self._run(
            pool, settings=self._settings(collect_promote_min_ok=2,
                                          collect_verify_delay_ms=0),
            ledger=ledger)
        self.assertEqual(counts, (1, 0, 0))              # passed 含暂存
        self.assertEqual(engine.started, 1)
        self.assertEqual(report["collect_held"], 1)
        self.assertEqual(report["dead_candidates"], [])
        prof = ledger["seeds"]["seed-x"]["collect_candidates"][pool[0].id]
        self.assertEqual((prof["streak"], prof["last_status"]),
                         (1, "passed"))
        store = Store(self.db)
        try:
            self.assertEqual(store.count_nodes(), 0)     # 只记账不入库
        finally:
            store.close()

    def test_second_round_reaches_threshold_enters(self):
        """达标才入库：第二轮连过（streak 2 ≥ promote 2）→ 入库，
        计数跨轮累积。"""
        ledger = C.new_ledger()
        pool = [_pool_item(VLESS_ONE)]
        st = self._settings(collect_promote_min_ok=2, collect_verify_delay_ms=0)
        self._run(pool, settings=st, ledger=ledger)
        report, _engine, counts = self._run(pool, settings=st, ledger=ledger)
        self.assertEqual(counts, (1, 0, 1))
        self.assertEqual(report["collect_held"], 0)
        prof = ledger["seeds"]["seed-x"]["collect_candidates"][pool[0].id]
        self.assertEqual(prof["streak"], 2)
        store = Store(self.db)
        try:
            self.assertEqual(store.count_nodes(), 1)
        finally:
            store.close()

    def test_fail_in_between_resets_accumulation(self):
        """连过一败再过：清零重计（promote 2 时第二轮进不了库）。"""
        ledger = C.new_ledger()
        pool = [_pool_item(VLESS_ONE)]
        st = self._settings(collect_promote_min_ok=2, collect_verify_delay_ms=0)
        self._run(pool, settings=st, ledger=ledger,
                  probe=mock.MagicMock(side_effect=lambda node: probe_result()))
        mid_report, _engine, mid_counts = self._run(
            pool, settings=st, ledger=ledger,
            probe=mock.MagicMock(
                side_effect=lambda node: probe_result(alive=False)))
        self.assertEqual(mid_counts, (0, 1, 0))          # 中间轮：验真未过
        self.assertEqual(mid_report["candidates_dead"], 1)
        prof = ledger["seeds"]["seed-x"]["collect_candidates"][pool[0].id]
        self.assertEqual((prof["streak"], prof["last_status"]), (0, "failed"))
        report, _engine, counts = self._run(pool, settings=st, ledger=ledger)
        self.assertEqual(counts, (1, 0, 0))              # streak 只回到 1
        self.assertEqual(report["collect_held"], 1)


# ---------------------------------------------------------------------------
# 开关语义 + 端到端（CLI + 落盘 settings.json）
# ---------------------------------------------------------------------------
class TestSwitchAndE2E(CollectCase):
    """开关只管定时器不管手动；门槛来源注明；账本候选档案落盘。"""

    PAGES_TWO = None     # 类级懒构造：候选页 = 两个 vless 的 b64 订阅正文

    @classmethod
    def _two_cand_page(cls):
        if cls.PAGES_TWO is None:
            cls.PAGES_TWO = base64.b64encode(
                (VLESS_ONE + "\n" + VLESS_TWO + "\n").encode()).decode()
        return cls.PAGES_TWO

    def test_disabled_switch_manual_still_runs(self):
        """开关关：手动 collect 照跑入库；报告注明「关」+ 落盘设置来源。"""
        self._write_config([BASE_SEEDS[0]])
        _write_settings(self.db, auto_collect_enabled=False)
        rc, out, err, engine, _ = self._collect(FetchLog())
        self.assertEqual(rc, 0, err)
        self.assertIn("入库 1（库内共 1 个）", out)
        self.assertIn("门槛：转登记须连续通过 1 次 · 每轮验真上限 10 个 · "
                      "候选间隔 1000ms（落盘设置）", out)
        self.assertIn("本轮截断 0 个", out)
        self.assertIn("自动搜集开关：关（定时器不跑；手动 sources collect "
                      "不受影响）", out)

    def test_enabled_switch_reports_on(self):
        _write_settings(self.db, auto_collect_enabled=True,
                        collect_interval_min=45)
        self._write_config([BASE_SEEDS[0]])
        rc, out, err, _e, _p = self._collect(FetchLog())
        self.assertEqual(rc, 0, err)
        self.assertIn("自动搜集开关：开（每 45 分钟定时一轮；只加库，"
                      "不碰连接）", out)

    def test_gate_source_default_without_settings(self):
        """无 settings.json：按默认口径跑完并在门槛行注明。"""
        self._write_config([BASE_SEEDS[0]])
        rc, out, err, _e, _p = self._collect(FetchLog())
        self.assertEqual(rc, 0, err)
        self.assertIn("候选间隔 1000ms（默认口径）", out)
        self.assertIn("自动搜集开关：关", out)

    def test_two_round_promotion_e2e(self):
        """端到端两轮对账：门槛 2 + 每轮上限 1 + 池 2 → 第一轮暂存+截断、
        第二轮达标入库、账本 streak 累积、输出红线零地址。"""
        cand_addr = "https://sub.two.example/daily.txt"
        pages = dict(PAGES)
        pages[cand_addr] = self._two_cand_page()
        seeds = [{"id": "seed-two-01", "category": "agg_repo",
                  "address": SEED_AGG_ADDR}]
        pages[SEED_AGG_ADDR] = (
            f"- [订阅二]({cand_addr})\n")
        _write_settings(self.db, auto_collect_enabled=True,
                        collect_interval_min=45, collect_promote_min_ok=2,
                        collect_verify_per_round=1, collect_verify_delay_ms=0)
        self._write_config(seeds)
        fetch = FetchLog(pages=pages)

        # —— 第一轮：验 1 个（streak 1 暂存）、截断 1 个（本轮未验） ——
        rc, out, err, engine, _ = self._collect(fetch)
        self.assertEqual(rc, 0, err)
        self.assertIn("候选池 2：验真通过 1", out)
        self.assertIn("入库 0（库内共 0 个）", out)
        self.assertIn("本轮暂存 1 个（验真通过、连续次数未达）", out)
        self.assertIn("本轮截断 1 个（超每轮上限，余下留待下轮验真，"
                      "不记死账）", out)
        self.assertIn("门槛：转登记须连续通过 2 次 · 每轮验真上限 1 个 · "
                      "候选间隔 0ms（落盘设置）", out)
        self.assertIn("自动搜集开关：开（每 45 分钟定时一轮；只加库，"
                      "不碰连接）", out)
        self.assertEqual(engine.started, 1)              # 上限 1：只验一个
        ledger = C.load_ledger(self.ledger_path)
        cands = ledger["seeds"]["seed-two-01"]["collect_candidates"]
        self.assertEqual(len(cands), 2)
        by_streak = sorted(cands.values(), key=lambda p: p["streak"])
        self.assertEqual([p["streak"] for p in by_streak], [0, 1])
        self.assertEqual(by_streak[0]["last_status"], "overflow")
        self.assertEqual(by_streak[1]["last_status"], "passed")
        self.assertEqual(self._db_count(), 0)

        # —— 第二轮：首候选连过 → streak 2 达标入库；次候选仍未验 ——
        rc, out, err, engine, _ = self._collect(fetch)
        self.assertEqual(rc, 0, err)
        self.assertIn("入库 1（库内共 1 个）", out)
        self.assertEqual(engine.started, 1)
        ledger = C.load_ledger(self.ledger_path)
        cands = ledger["seeds"]["seed-two-01"]["collect_candidates"]
        self.assertEqual(sorted(p["streak"] for p in cands.values()), [0, 2])
        self.assertEqual(self._db_count(), 1)
        node = self._store_nodes()[0]
        self.assertEqual(node.exit_profile.ip, FAKE_EXIT_IP)  # 新鲜验真画像

        # —— 输出红线：零地址原文（沿用 task27 同一禁止清单） ——
        blob = out + err
        for forbidden in FORBIDDEN_IN_OUTPUT:
            self.assertNotIn(forbidden, blob, f"输出泄漏: {forbidden}")

    def test_ledger_candidate_file_roundtrip(self):
        """候选档案真落盘（第二轮 save_ledger 回写）：文件里 streak 可查。"""
        _write_settings(self.db, collect_promote_min_ok=2,
                        collect_verify_delay_ms=0)
        self._write_config([BASE_SEEDS[0]])
        rc, out, err, _e, _p = self._collect(FetchLog())
        self.assertEqual(rc, 0, err)
        self.assertIn("本轮暂存 1 个", out)
        ledger = C.load_ledger(self.ledger_path)         # 从文件读回
        cands = ledger["seeds"]["seed-agg-01"]["collect_candidates"]
        self.assertEqual(len(cands), 1)
        prof = next(iter(cands.values()))
        self.assertEqual((prof["streak"], prof["last_status"]),
                         (1, "passed"))
        self.assertFalse(os.path.exists(self.ledger_path + ".tmp"))

    def test_corrupt_settings_falls_back_with_note(self):
        """settings.json 损坏（运行中撞上）：按默认口径跑完并注明，不是
        故障（致命处置归 main 启动路径既有口径）。直调形态（绕过 main）。"""
        self._write_config([BASE_SEEDS[0]])
        with open(os.path.join(self.tmp, SETTINGS_FILE), "w",
                  encoding="utf-8") as f:
            f.write("不是 json {{{")
        guard = SocketGuard()
        engine = FakeSingBoxEngine()
        fetch = FetchLog()
        probes = mock.MagicMock(side_effect=lambda node: probe_result())
        store = Store(self.db)
        self.addCleanup(store.close)
        with guard, mock.patch.object(cli, "make_real_probe",
                                      lambda *a, **k: probes):
            buf, ebuf = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(buf), \
                    contextlib.redirect_stderr(ebuf):
                rc = cli._sources_collect(store, fetcher=fetch,
                                          engine_factory=lambda: engine)
        self.assertEqual(rc, 0)
        out = buf.getvalue()
        self.assertIn("入库 1（库内共 1 个）", out)
        self.assertIn("候选间隔 1000ms（默认口径）", out)
        self.assertEqual(guard.calls, [])
        for url in fetch.fetched:
            self.assertTrue(
                url.split("://", 1)[1].split("/", 1)[0].endswith(".example"))


# ---------------------------------------------------------------------------
# GUI：定时器接线（PySide6 在场才跑；缺席整组 skip，不伪造通过）
# ---------------------------------------------------------------------------
try:
    from PySide6.QtWidgets import QApplication            # noqa: E402
    _QT = True
except ImportError:
    _QT = False

if _QT:
    from PySide6.QtCore import Qt                          # noqa: E402
    from client.core.orchestrator import Orchestrator      # noqa: E402
    from client.ui import app as app_mod                   # noqa: E402

    class _FakeEngine:
        """openvpn 引擎替身（同 test_gui_app 手法：只记启动/停止）。"""
        name = "openvpn"

        def __init__(self):
            self.started = []
            self.alive = True

        def start(self, node, on_event=None):
            self.started.append(node.id)
            self.alive = True
            return {"node_id": node.id}

        def stop(self, handle):
            self.alive = False

        def status(self, handle):
            return "running" if self.alive else "stopped"

    def _ok_verifier(node):
        return VerifyOutcome(ok=True, exit_profile=ExitProfile(
            ip="203.0.113.9", country="日本", country_code="JP",
            network_type="residential", is_clean=True), scenes={"ai"})

    class GuiCollectCase(unittest.TestCase):
        """GUI 底座：offscreen Qt + 假引擎 + 注入 settings 的 MainWindow。"""

        @classmethod
        def setUpClass(cls):
            cls.app = QApplication.instance() or QApplication([])

        def setUp(self):
            from client.core.model import Node
            self.store = Store(":memory:")
            self.store.upsert_node(Node(id="ov1", name="隧道节点",
                                        protocol="openvpn", source="vpngate",
                                        host="h1", port=1194))
            self.eng = _FakeEngine()
            self.orch = Orchestrator(self.store, {"openvpn": self.eng},
                                     _ok_verifier)

        def make_window(self, settings=None):
            win = app_mod.MainWindow(self.store, orchestrator=self.orch,
                                     engines={"openvpn": self.eng},
                                     settings=settings)
            self.addCleanup(win.close)
            return win

        def pump(self):
            self.app.processEvents()

        def select(self, win, source, row=0):
            win.tabs.setCurrentWidget(win.pages[source])
            win.pages[source].table.setCurrentCell(row, 0)

    class TestGuiCollectTimer(GuiCollectCase):

        def test_timer_off_by_default(self):
            """默认（开关关）：collect_timer 不启动——铁律的机械锁定。"""
            win = self.make_window()
            self.assertFalse(win.collect_timer.isActive())
            self.assertEqual(win.collect_timer.interval(), 30 * 60_000)

        def test_timer_on_with_interval(self):
            win = self.make_window(Settings(
                auto_collect_enabled=True, collect_interval_min=5))
            self.assertTrue(win.collect_timer.isActive())
            self.assertEqual(win.collect_timer.interval(), 5 * 60_000)

        def test_qtimer_single_step_triggers_round(self):
            """QTimer 单步：timeout 触发一轮（复用 _launch_auto 通道，
            tag=auto_collect）；收尾复位旗标。"""
            win = self.make_window(Settings(auto_collect_enabled=True))
            tags = []
            win._launch_auto = lambda fn, tag="auto_probe": tags.append(tag)
            win.collect_timer.timeout.emit()
            self.assertEqual(tags, ["auto_collect"])
            self.assertTrue(win._collect_active)
            win._on_task_finished("auto_collect", (True, 0))
            self.assertFalse(win._collect_active)

        def test_guards_skip_round(self):
            """守卫：开关关 / 上轮未完 / 用户任务占用 / 批量测速中 /
            连接中，都不开新一轮（与 _maybe_auto_probe 同款纪律）。"""
            win = self.make_window(Settings(auto_collect_enabled=True))
            tags = []
            win._launch_auto = lambda fn, tag="auto_probe": tags.append(tag)

            win.settings = merge(win.settings,
                                 {"auto_collect_enabled": False})
            win._apply_collect_timer()
            win.collect_timer.timeout.emit()
            self.assertEqual(tags, [])                   # 开关关
            self.assertFalse(win.collect_timer.isActive())

            win.settings = merge(win.settings,
                                 {"auto_collect_enabled": True})
            win._apply_collect_timer()
            win._collect_active = True
            win.collect_timer.timeout.emit()
            self.assertEqual(tags, [])                   # 上轮未完
            win._collect_active = False

            win._busy = True
            win.collect_timer.timeout.emit()
            self.assertEqual(tags, [])                   # 用户任务占用
            win._busy = False

            win._batch_active = True
            win.collect_timer.timeout.emit()
            self.assertEqual(tags, [])                   # 批量测速中
            win._batch_active = False

            # 会话连接中：经真连接流程进入 CONNECTED（假引擎）
            self.select(win, "vpngate")
            win._run_task = lambda tag, fn: win._on_task_finished(
                tag, (True, fn()))
            win.connect_selected()
            self.pump()
            self.assertEqual(win.orchestrator.state, Orchestrator.CONNECTED)
            win.collect_timer.timeout.emit()
            self.assertEqual(tags, [])                   # 连接期间不跑

        def test_settings_save_rebuilds_timer(self):
            """设置页保存后定时器按新值重建（与自动探测定时器同款刷新）。"""
            win = self.make_window()
            self.assertFalse(win.collect_timer.isActive())
            win._apply_saved_settings(Settings(
                auto_collect_enabled=True, collect_interval_min=20))
            self.assertTrue(win.collect_timer.isActive())
            self.assertEqual(win.collect_timer.interval(), 20 * 60_000)
            win._apply_saved_settings(Settings())        # 改回默认关
            self.assertFalse(win.collect_timer.isActive())

        def test_finish_failure_logs_only(self):
            """失败收尾：只记 stderr 日志——不弹红条、不碰连接状态栏。"""
            win = self.make_window()
            win.status_label.setText("已连接：假节点")
            win._collect_active = True
            buf = io.StringIO()
            with contextlib.redirect_stderr(buf):
                win._on_task_finished("auto_collect", (False,
                                                       RuntimeError("假失败")))
            self.assertFalse(win.alert_bar.isVisible())  # 不弹红条
            self.assertEqual(win.status_label.text(), "已连接：假节点")
            self.assertFalse(win._collect_active)
            self.assertIn("[自动搜集]", buf.getvalue())

        def test_finish_success_keeps_status(self):
            """成功收尾：不抢状态栏（新节点经 refresh_pages 出现在库页）。"""
            win = self.make_window()
            win.status_label.setText("已连接：假节点")
            win._collect_active = True
            win._on_task_finished("auto_collect", (True, 0))
            self.assertEqual(win.status_label.text(), "已连接：假节点")
            self.assertFalse(win.alert_bar.isVisible())
            self.assertFalse(win._collect_active)

        def test_run_auto_collect_reuses_cli_body(self):
            """跑轮复用 CLI 本体 _sources_collect（不抄第二份跑轮逻辑）；
            settings_file 用 settings_store 落点（替身形态 None → 兜底）。"""
            win = self.make_window()
            from client.ui import cli as cli_mod
            with mock.patch.object(cli_mod, "_sources_collect",
                                   return_value=0) as fake_collect:
                rc = win._run_auto_collect()
            self.assertEqual(rc, 0)
            fake_collect.assert_called_once_with(win.store, settings_file=None)


# ---------------------------------------------------------------------------
# 红线证明：本文件出现的 http(s) 地址全部 RFC 2606 *.example / 回环
# ---------------------------------------------------------------------------
class TestZeroRealAddresses(unittest.TestCase):

    def test_all_http_addresses_in_this_test_are_example(self):
        with open(os.path.abspath(__file__), encoding="utf-8") as f:
            src = f.read()
        hits = re.findall(r"https?://[A-Za-z0-9.\-]+", src)
        for url in hits:
            host = url.split("://", 1)[1]
            if host == "127.0.0.1":
                continue    # 假引擎本地回环代理口（http_proxy_url），非外网
            self.assertTrue(host.endswith(".example"),
                            f"非保留域名: {url}")


if __name__ == "__main__":
    unittest.main()
