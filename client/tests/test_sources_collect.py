#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task27 验收：`sources collect` 手动搜集接线（阶段五·商业级打磨 第一段）
==========================================================================

对「拷入 client-dev 相关文件并打上本段 diff 的副本」跑（本文件 sys.path
指向所在目录，即副本的 client 包根）。覆盖（对齐 task27 验收口径）：

1. 假种子文件（*.example 地址 only）+ 注假 fetch_fn 跑通一整轮：
   报告数字逐行对账、账本 R1/R2/R3/R8 记账与跳过不建档、溯源四键、
   账本 save/load 回读一致且与种子配置同目录。
2. 未配置（not_configured）/ 空清单 / 全停用 → 打印「未启用」退出 0，
   不是故障、不告警；停用种子跳过（配置停用不进轮、账本停用由
   run_collection 自己跳过，双闸各管各的）；损坏 JSON / 非法 schema /
   损坏账本 → 显式报错退出 2（seed_config / collector 既有口径，不静默吞）。
3. 候选 → 验真 → 入库管道：验真失败（链路死 / 出口污染 / 引擎起不来）
   不入库、库内节点数不变，只进 dead_candidates 记账（stage="verify"）；
   验真通过才入库（Store 既有口径），同身份旧档按 §5 字段口径合并
   （pin/ban 保住、orig_name 保最早、场景并集、新鲜验真画像为准）。
4. CLI 输出断言：只报数字——不含任何节点名称/地址、种子或候选地址
   原文、出口 IP。
5. 零真实外网：socket 全程站岗（fetch_fn 注入点之外无 socket 调用），
   fetch 只许落在 *.example 假域名；验真经假引擎 + 假探针，临时引擎
   起停配平（测完即停）。

跑法：cd work27 && python3 -m unittest（或 -v 看明细）。
红线：本文件出现的全部 http(s) 地址都是 RFC 2606 *.example，节点与
出口 IP 用 RFC 5730/5737 保留段（10.0.0.x / 203.0.113.x），零真实外网。
"""
import base64
import json
import os
import re
import shutil
import socket
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from client.core.store import Store                       # noqa: E402
from client.core.verify import ProbeResult                # noqa: E402
from client.engines.base import EngineError               # noqa: E402
from client.engines.singbox_engine import SingBoxEngine   # noqa: E402
from client.sources import collector as C                 # noqa: E402
from client.sources import manual as manual_src           # noqa: E402
from client.sources import seed_config as SC              # noqa: E402
from client.tests.test_cli import run_cli                 # noqa: E402
from client.ui import cli                                 # noqa: E402

# ---------------- 假数据：种子配置 / 种子页 / 候选页（全假域名假段） ----------------
SEED_AGG_ADDR = "https://agg.example/free/list.md"
SEED_DEAD_ADDR = "https://dead-seed.example/page"
SEED_RAW_ADDR = "https://raw.example/daily/nodes.txt"
SEED_OFF_ADDR = "https://off.example/p.html"          # 配置停用
SEED_LD_DEAD_ADDR = "https://ledger-dead.example/p.html"   # 账本停用
CAND_A = "https://sub.a.example/free.txt"             # 候选：b64 订阅正文
CAND_B = "http://sub.b.example/junk.md"               # 候选：假死链

VLESS_ONE = ("vless://11111111-2222-3333-4444-555555555555"
             "@10.0.0.9:443?security=tls#collect-one")
VLESS_TWO = ("vless://22222222-3333-4444-5555-666666666666"
             "@10.0.0.8:443?security=tls#collect-two")

AGG_PAGE = (
    "# 每日免费节点聚合（假站示例）\n"
    f"- [订阅一]({CAND_A})\n"
    f"<a href='{CAND_B}'>坏订阅</a>\n"
)
RAW_PAGE = "这页是原始订阅正文（假数据），没有可再抽的地址。"
PAGES = {
    SEED_AGG_ADDR: AGG_PAGE,
    CAND_A: base64.b64encode((VLESS_ONE + "\n").encode()).decode(),
    SEED_RAW_ADDR: RAW_PAGE,
}

BASE_SEEDS = [
    {"id": "seed-agg-01", "category": "agg_repo", "address": SEED_AGG_ADDR},
    {"id": "seed-dead-01", "category": "list_site", "address": SEED_DEAD_ADDR},
    {"id": "seed-raw-01", "category": "raw_file", "address": SEED_RAW_ADDR},
]

# 假探针的出口（RFC 5737 TEST-NET-3）与画像
FAKE_EXIT_IP = "203.0.113.7"
FAKE_EXIT = dict(exit_ip=FAKE_EXIT_IP, country="Japan", country_code="JP",
                 org="NTT EAST", is_clean=True, latency_ms=300.0,
                 jitter_ms=50.0, muse_code=307)

# CLI 输出红线检查清单：不许出现在 stdout/stderr 的任何原文
FORBIDDEN_IN_OUTPUT = (
    SEED_AGG_ADDR, SEED_DEAD_ADDR, SEED_RAW_ADDR, SEED_OFF_ADDR,
    SEED_LD_DEAD_ADDR, CAND_A, CAND_B,
    "vless://", "10.0.0.", FAKE_EXIT_IP,
    "collect-one", "collect-two",
)


class FetchLog:
    """假拉取：按表供页，其余一律假死链；全程记录被拉过的地址。"""

    def __init__(self, pages=None):
        self.pages = dict(PAGES)
        if pages:
            self.pages.update(pages)
        self.fetched = []

    def __call__(self, url):
        self.fetched.append(url)
        if url == SEED_DEAD_ADDR:
            raise TimeoutError("模拟种子超时")
        if url == CAND_B:
            raise ConnectionError("假死链（fetch 不到）")
        page = self.pages.get(url)
        if page is None:
            raise ConnectionError(f"假死链（fetch 不到）: {url}")
        return page


class FakeSingBoxEngine(SingBoxEngine):
    """isinstance(SingBoxEngine) 成立，start/stop 全假——绝不真拉进程；
    起停计数用于断言「测完即停、不漏停」。"""

    def __init__(self, http_port=18097, start_raises=None):
        super().__init__(http_port=http_port)
        self.started = 0
        self.stopped = 0
        self._start_raises = start_raises

    def start(self, node, on_event=None):
        self.started += 1
        if self._start_raises is not None:
            raise self._start_raises
        return {"node_id": node.id}

    def stop(self, handle):
        self.stopped += 1

    def status(self, handle):
        return "running"


class SocketGuard:
    """零真实外网哨兵：搜集全程任何 socket 实例化都记账并炸断。"""

    def __init__(self):
        self.calls = []

    def __enter__(self):
        self._orig = socket.socket

        def _recorder(*args, **kwargs):
            self.calls.append((args, kwargs))
            raise AssertionError("搜集全程出现 socket 调用（零真实外网红线）")

        socket.socket = _recorder
        return self

    def __exit__(self, *exc):
        socket.socket = self._orig


def probe_result(alive=True, **overrides):
    fields = dict(FAKE_EXIT) if alive else {}
    fields.update(overrides)
    if not alive:
        fields.setdefault("error", "假探针：链路不通")
    return ProbeResult(alive=alive, **fields)


class CollectCase(unittest.TestCase):
    """公共底座：临时目录 + 库路径 + 一轮 collect 的跑法。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(
            dir=os.path.dirname(os.path.abspath(__file__)))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.db = os.path.join(self.tmp, "client.db")
        self.config_path = os.path.join(self.tmp, "collect_seeds.json")
        self.ledger_path = os.path.join(self.tmp, "collect_ledger.json")

    def _write_config(self, seeds):
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "seeds": seeds}, f, ensure_ascii=False,
                      indent=1)

    def _write_ledger_raw(self, text):
        with open(self.ledger_path, "w", encoding="utf-8") as f:
            f.write(text)

    def _collect(self, fetch, engine=None, probe=None):
        """跑 `sources collect`。fetch 为必注假拉取；engine 缺省给假
        sing-box 引擎；probe 为假探针工厂的返回值（默认验真通过）。
        全程 socket 站岗 + make_real_probe 替换，断言零联网。"""
        guard = SocketGuard()
        engine = engine if engine is not None else FakeSingBoxEngine()
        probes = []
        proxy_seen = []

        def fake_make_real_probe(*args, **kwargs):
            proxy_seen.append(kwargs.get("proxy"))
            return lambda node: probe if probe is not None else probe_result()

        with guard, mock.patch.object(cli, "make_real_probe",
                                      fake_make_real_probe):
            rc, out, err = run_cli(["--db", self.db, "sources", "collect"],
                                   fetcher=fetch,
                                   engine_factory=lambda: engine)
        # 零真实外网：注入点之外无 socket；注入点内全是 *.example。
        self.assertEqual(guard.calls, [], "出现 socket 调用")
        for url in fetch.fetched:
            host = url.split("://", 1)[1].split("/", 1)[0]
            self.assertTrue(host.endswith(".example"),
                            f"拉取了非保留域名: {url}")
        return rc, out, err, engine, proxy_seen

    def _db_count(self):
        store = Store(self.db)
        try:
            return store.count_nodes()
        finally:
            store.close()

    def _store_nodes(self, include_banned=True):
        store = Store(self.db)
        try:
            return store.list_nodes(include_banned=include_banned)
        finally:
            store.close()


class TestCollectFullRound(CollectCase):
    """假种子文件 + 假 fetch 跑通一整轮：数字对账、记账、溯源、账本回读。"""

    def test_full_round_numbers_ledger_provenance(self):
        self._write_config(BASE_SEEDS)
        fetch = FetchLog()
        rc, out, err, engine, proxy_seen = self._collect(fetch)
        self.assertEqual(rc, 0, err)
        # —— 报告数字逐行对账（种子 3：一好一死一活而空；候选 2 去 2 死 1；
        #    池 1 验真过 1；入库 1）——
        m = re.search(r"搜集轮 (\S+)：种子 3（成功 2、失败 1、停用跳过 0）",
                      out)
        self.assertIsNotNone(m, out)
        round_id = m.group(1)
        self.assertIn("候选 2（去重 2、死 1，其中验真未过 0）", out)
        self.assertIn("候选池 1：验真通过 1", out)
        self.assertIn("入库 1（库内共 1 个）", out)
        # 临时引擎纪律：起停配平、测完即停；探针经引擎自己的代理口
        self.assertEqual((engine.started, engine.stopped), (1, 1))
        self.assertEqual(proxy_seen, ["http://127.0.0.1:18097"])
        # 拉取足迹：种子逐个处理——agg 页 → 其两个候选；死种子；raw 页
        self.assertEqual(fetch.fetched,
                         [SEED_AGG_ADDR, CAND_A, CAND_B,
                          SEED_DEAD_ADDR, SEED_RAW_ADDR])
        # —— 账本 R1/R3/R2 记账 + 落位（与种子配置同目录）+ 回读一致 ——
        self.assertEqual(self.ledger_path, SC.collect_ledger_path(self.db))
        self.assertEqual(os.path.dirname(self.ledger_path),
                         os.path.dirname(self.config_path))
        self.assertFalse(os.path.exists(self.ledger_path + ".tmp"))
        ledger = C.load_ledger(self.ledger_path)
        self.assertEqual(ledger["version"], C.LEDGER_VERSION)
        self.assertEqual(sorted(ledger["seeds"]),
                         ["seed-agg-01", "seed-dead-01", "seed-raw-01"])
        agg = ledger["seeds"]["seed-agg-01"]
        self.assertEqual((agg["ok_count"], agg["produced_total"]), (1, 2))
        self.assertEqual(agg["weight"], 1.0)              # R1：封顶
        dead = ledger["seeds"]["seed-dead-01"]
        self.assertEqual((dead["fail_count"], dead["consecutive_failures"]),
                         (1, 1))                          # R3：降权一档
        self.assertEqual(dead["weight"], 0.75)
        raw = ledger["seeds"]["seed-raw-01"]
        self.assertEqual((raw["ok_count"], raw["produced_total"],
                          raw["zero_streak"]), (1, 0, 1))  # R2：活而空
        self.assertEqual(raw["weight"], 1.0)
        # —— 入库节点：Store 既有口径 + 溯源四键（§3.3）——
        nodes = self._store_nodes()
        self.assertEqual(len(nodes), 1)
        n = nodes[0]
        self.assertEqual(n.source, "manual")               # D1 落页
        self.assertEqual(n.extra["collect_seed_id"], "seed-agg-01")
        self.assertEqual(n.extra["collect_seed_category"], "agg_repo")
        self.assertEqual(n.extra["collect_round"], round_id)
        self.assertEqual(n.extra["collected_from"], CAND_A)
        self.assertEqual(n.exit_profile.ip, FAKE_EXIT_IP)  # 新鲜验真画像
        self.assertEqual(sorted(n.scenes), ["games"])

    def test_second_round_ledger_save_load_roundtrip(self):
        """连跑两轮：账本累计（save/load 回读一致），入库不产生副本。"""
        self._write_config(BASE_SEEDS)
        fetch = FetchLog()
        rc, out, err, _, _ = self._collect(fetch)
        self.assertEqual(rc, 0, err)
        ledger = C.load_ledger(self.ledger_path)
        self.assertEqual(ledger["seeds"]["seed-agg-01"]["ok_count"], 1)
        rc, out, err, _, _ = self._collect(fetch)
        self.assertEqual(rc, 0, err)
        self.assertIn("入库 1（库内共 1 个）", out)   # 同身份合并，不新建副本
        ledger2 = C.load_ledger(self.ledger_path)
        self.assertEqual(ledger2["seeds"]["seed-agg-01"]["ok_count"], 2)
        self.assertEqual(ledger2["seeds"]["seed-agg-01"]["produced_total"], 4)
        self.assertEqual(ledger2["seeds"]["seed-dead-01"]["fail_count"], 2)
        self.assertEqual(ledger2["seeds"]["seed-dead-01"]["weight"], 0.5)
        self.assertEqual(self._db_count(), 1)

    def test_cli_output_numbers_only(self):
        """输出红线：不含任何节点/种子/候选地址原文与出口 IP，只报数字。"""
        self._write_config(BASE_SEEDS)
        fetch = FetchLog()
        rc, out, err, _, _ = self._collect(fetch)
        self.assertEqual(rc, 0)
        blob = out + err
        for forbidden in FORBIDDEN_IN_OUTPUT:
            self.assertNotIn(forbidden, blob, f"输出泄漏: {forbidden}")
        for number_bit in ("种子 3", "候选 2", "候选池 1", "入库 1"):
            self.assertIn(number_bit, blob)


class TestCollectGate(CollectCase):
    """候选 → 验真 → 入库门槛：验真失败（死/污染/引擎炸）不入库。"""

    def _pre_intake_one(self):
        rc, out, err = run_cli(["--db", self.db, "intake", "--link", VLESS_ONE])
        self.assertEqual(rc, 0, err)

    def test_verify_fail_not_entered_store_untouched(self):
        """验真失败不入库：库内节点数不变、旧档一字节不动；种子记账不受
        候选死活影响（R8：候选后续死活不算种子成败）。"""
        self._pre_intake_one()
        self._write_config([BASE_SEEDS[0]])     # 只有 agg 一个种子
        old_nodes = self._store_nodes()
        self.assertEqual(len(old_nodes), 1)
        old_name, old_profile = old_nodes[0].name, old_nodes[0].exit_profile
        fetch = FetchLog()
        rc, out, err, _, _ = self._collect(
            fetch, probe=probe_result(alive=False))
        self.assertEqual(rc, 0, err)
        self.assertIn("候选池 1：验真通过 0", out)
        self.assertIn("其中验真未过 1", out)
        self.assertIn("入库 0（库内共 1 个）", out)
        self.assertEqual(self._db_count(), 1)
        node = self._store_nodes()[0]
        self.assertEqual(node.name, old_name)              # 库一字节不动
        self.assertEqual(node.exit_profile.to_dict(), old_profile.to_dict())
        ledger = C.load_ledger(self.ledger_path)
        self.assertEqual(ledger["seeds"]["seed-agg-01"]["produced_total"], 2)
        self.assertEqual(ledger["seeds"]["seed-agg-01"]["ok_count"], 1)

    def test_verify_polluted_not_entered(self):
        """出口污染（is_clean=False）＝门槛不过：不进库。"""
        self._write_config([BASE_SEEDS[0]])
        rc, out, err, _, _ = self._collect(
            fetch=FetchLog(),
            probe=probe_result(exit_ip="203.0.113.8", is_clean=False))
        self.assertEqual(rc, 0, err)
        self.assertIn("其中验真未过 1", out)
        self.assertIn("入库 0（库内共 0 个）", out)
        self.assertEqual(self._db_count(), 0)

    def test_engine_start_failure_not_entered(self):
        """临时引擎起不来（缺二进制等）→ 如实判失败，绝不降级直连探针。"""
        self._write_config([BASE_SEEDS[0]])
        engine = FakeSingBoxEngine(
            start_raises=EngineError("找不到 sing-box 二进制（假）"))
        rc, out, err, _, proxy_seen = self._collect(fetch=FetchLog(),
                                                    engine=engine)
        self.assertEqual(rc, 0, err)
        self.assertIn("其中验真未过 1", out)
        self.assertIn("入库 0（库内共 0 个）", out)
        self.assertEqual(self._db_count(), 0)
        self.assertEqual(proxy_seen, [])     # 引擎没起来，探针根本没造

    def test_verify_pass_merges_existing_identity(self):
        """验真通过才入库；同身份旧档按 §5 字段口径合并：pin/ban 保住、
        orig_name 保最早、场景并集、画像以新鲜验真为准。"""
        self._pre_intake_one()
        node_id = self._store_nodes()[0].id
        store = Store(self.db)
        try:
            n = store.get_node(node_id)
            n.banned = True
            n.scenes = {"support"}
            n.extra["orig_name"] = "最早的原名"
            store.upsert_node(n)
        finally:
            store.close()
        rc, _ = run_cli(["--db", self.db, "ban", node_id])[:2]
        self._write_config([BASE_SEEDS[0]])
        fetch = FetchLog()
        rc, out, err, _, _ = self._collect(fetch)
        self.assertEqual(rc, 0, err)
        self.assertIn("入库 1（库内共 1 个）", out)
        n = self._store_nodes()[0]
        self.assertTrue(n.banned)                    # 人工标记不被冲掉
        self.assertEqual(sorted(n.scenes), ["games", "support"])  # 并集
        self.assertEqual(n.extra["orig_name"], "最早的原名")      # 保最早
        self.assertEqual(n.exit_profile.ip, FAKE_EXIT_IP)         # 新鲜验真
        self.assertEqual(n.name, f"日本 · {FAKE_EXIT_IP} · NTT EAST")
        self.assertEqual(n.extra["collect_seed_id"], "seed-agg-01")


class TestCollectEnterStoreUnit(CollectCase):
    """_collect_enter_store 直测：验真未过的候选进 dead_candidates 记账，
    stage="verify" 与 fetch/parse 死账同一本账。"""

    def test_verify_dead_booked_into_dead_candidates(self):
        report = {"pool": [], "dead_candidates": [], "candidates_dead": 0}
        ok_node = manual_src.parse_link(VLESS_ONE)
        ok_node.extra.update({"collect_seed_id": "seed-x",
                              "collect_seed_category": "agg_repo",
                              "collect_round": "R-fake",
                              "collected_from": CAND_A})
        bad_node = manual_src.parse_link(VLESS_TWO)
        bad_node.extra.update({"collect_seed_id": "seed-y",
                               "collect_seed_category": "list_site",
                               "collect_round": "R-fake",
                               "collected_from": CAND_B})
        report["pool"] = [ok_node, bad_node]
        engine = FakeSingBoxEngine()
        probe = mock.MagicMock(side_effect=lambda node: (
            probe_result() if node.id == ok_node.id
            else probe_result(alive=False)))
        with mock.patch.object(cli, "make_real_probe", lambda *a, **k: probe):
            passed, failed, entered = cli._collect_enter_store(
                Store(self.db), report, engine_factory=lambda: engine)
        self.assertEqual((passed, failed, entered), (1, 1, 1))
        self.assertEqual(report["candidates_dead"], 1)
        self.assertEqual(len(report["dead_candidates"]), 1)
        dead = report["dead_candidates"][0]
        self.assertEqual(dead["stage"], "verify")
        self.assertEqual(dead["seed_id"], "seed-y")
        self.assertEqual(dead["url"], CAND_B)
        self.assertEqual(dead["error"], "假探针：链路不通")


class TestCollectStates(CollectCase):
    """未配置/空清单/全停用 → 未启用退出 0；停用种子跳过；坏文件显式报错。"""

    def test_not_configured_exit_zero(self):
        fetch = FetchLog()
        rc, out, err, _, _ = self._collect(fetch)
        self.assertEqual(rc, 0, err)
        self.assertIn("未启用", out)
        self.assertNotIn("错误", out + err)              # 不是故障、不告警
        self.assertEqual(fetch.fetched, [])              # 一处都没拉
        self.assertFalse(os.path.exists(self.ledger_path))

    def test_empty_list_exit_zero(self):
        self._write_config([])
        fetch = FetchLog()
        rc, out, err, _, _ = self._collect(fetch)
        self.assertEqual(rc, 0, err)
        self.assertIn("未启用", out)
        self.assertIn("种子清单为空", out)
        self.assertEqual(fetch.fetched, [])

    def test_all_disabled_exit_zero(self):
        self._write_config([dict(s, enabled=False) for s in BASE_SEEDS])
        fetch = FetchLog()
        rc, out, err, _, _ = self._collect(fetch)
        self.assertEqual(rc, 0, err)
        self.assertIn("未启用", out)
        self.assertEqual(fetch.fetched, [])
        self.assertFalse(os.path.exists(self.ledger_path))

    def test_disabled_seed_skipped(self):
        """三道闸各管各的：配置停用不进轮（不建档）；账本停用由
        run_collection 跳过（seeds_skipped 计数，档案不动）。"""
        ledger = C.new_ledger()
        for _ in range(3):                     # 连败 3 次 → 账本停用
            C.record_result(ledger, "seed-ld-dead", ok=False, produced=0,
                            now=time.time() - 3600)   # 冷却未满 → 不复测
        C.save_ledger(self.ledger_path, ledger)
        self._write_config([
            BASE_SEEDS[0],
            {"id": "seed-off-01", "category": "list_site",
             "address": SEED_OFF_ADDR, "enabled": False},
            {"id": "seed-ld-dead", "category": "list_site",
             "address": SEED_LD_DEAD_ADDR},
        ])
        fetch = FetchLog()
        rc, out, err, _, _ = self._collect(fetch)
        self.assertEqual(rc, 0, err)
        self.assertIn("种子 2（成功 1、失败 0、停用跳过 1）", out)
        # 两类停用种子的地址都没被拉过
        self.assertNotIn(SEED_OFF_ADDR, fetch.fetched)
        self.assertNotIn(SEED_LD_DEAD_ADDR, fetch.fetched)
        ledger = C.load_ledger(self.ledger_path)
        self.assertNotIn("seed-off-01", ledger["seeds"])   # 跳过不建档
        ld = ledger["seeds"]["seed-ld-dead"]               # 跳过不改账
        self.assertEqual((ld["ok_count"], ld["fail_count"]), (0, 3))
        self.assertFalse(ld["enabled"])
        self.assertEqual(ld["disabled_reason"], "连续失败 3 次")

    def test_corrupt_config_explicit_error(self):
        with open(self.config_path, "w", encoding="utf-8") as f:
            f.write("不是 json {{{")
        rc, out, err, _, _ = self._collect(FetchLog())
        self.assertEqual(rc, 2)
        self.assertIn("[错误]", err)
        self.assertIn("种子配置损坏", err)
        self.assertIn(self.config_path, err)

    def test_invalid_schema_explicit_error(self):
        bad = dict(BASE_SEEDS[0], address="ftp://files.example/list.txt")
        self._write_config([bad])
        rc, out, err, _, _ = self._collect(FetchLog())
        self.assertEqual(rc, 2)
        self.assertIn("种子配置结构不符", err)
        self.assertIn("http/https", err)

    def test_corrupt_ledger_explicit_error(self):
        """账本损坏显式报错——绝不静默当空账本用（那会把停用状态洗掉）。"""
        self._write_config([BASE_SEEDS[0]])
        self._write_ledger_raw("不是 json {{{")
        rc, out, err, _, _ = self._collect(FetchLog())
        self.assertEqual(rc, 2)
        self.assertIn("健康账本损坏", err)


class TestVerifyCandidateUnit(unittest.TestCase):
    """_verify_candidate 单元口径：无引擎可验的协议如实判失败。"""

    def _node(self, protocol):
        n = manual_src.parse_link(VLESS_ONE)
        n.protocol = protocol
        return n

    def test_openvpn_honest_fail(self):
        outcome = cli._verify_candidate(self._node("openvpn"))
        self.assertFalse(outcome.ok)
        self.assertIn("隧道", outcome.reason)

    def test_unsupported_protocol_honest_fail(self):
        outcome = cli._verify_candidate(self._node("sstp"))
        self.assertFalse(outcome.ok)
        self.assertIn("暂无本机引擎", outcome.reason)


class TestZeroRealAddresses(unittest.TestCase):
    """红线证明：本文件出现的 http(s) 地址全部 RFC 2606 *.example。"""

    def test_all_http_addresses_in_this_test_are_example(self):
        with open(os.path.abspath(__file__), encoding="utf-8") as f:
            src = f.read()
        hits = re.findall(r"https?://[A-Za-z0-9.\-]+", src)
        self.assertTrue(hits)                      # 用例里确实有地址可查
        for url in hits:
            host = url.split("://", 1)[1]
            if host == "127.0.0.1":
                continue    # 假引擎的本地回环代理口（http_proxy_url），非外网地址
            self.assertTrue(host.endswith(".example"),
                            f"非保留域名: {url}")


if __name__ == "__main__":
    unittest.main()


# 集成适配（work27 原件 → client-dev 集成版，审查方留痕）：
# 本文件按原件一字未动落位 client/tests/test_sources_collect.py：原件
# sys.path 插入自身所在目录（work27/ 时指向副本 client 包根）；落位后
# 该插入指向 client/tests/，client 包仍由全套发现时的顶层目录
# （client-dev 根）解析，其余 import（client.core.store /
# client.sources.collector / client.tests.test_cli 等）零改动。原件
# docstring 所述跑法（cd work27 && python3 -m unittest）在 work27/ 内
# 仍有效；集成树内随全套 `python3 -m unittest discover -s client/tests
# -t .` 跑，18 项全绿。改动只在简报范围内（cli.py collect 接线 + 新测试
# + 版本/日志/待推清单），collector.py / seed_config.py / source_registry.py
# / store.py / verify.py 零改动。
