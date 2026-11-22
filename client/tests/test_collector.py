#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
collector 离线测试（task9）
===========================
假 fetch 构造混合种子：一好（产出候选）、一死（抛异常）、一垃圾（解析
为空/抛错）、好种子产出的候选里混死链接——断言：整轮跑完不抛、好候选
被抽出且去重保序、健康账本按 DESIGN §2.2 规则更新（死种子降权、好种子
记产出）、报告数字与假数据对得上。零联网（fetch/parse 全注入）、零
client 包依赖；所有地址都是 *.example 假域名假数据。

跑法：cd client-dev && python3 -m unittest client.tests.test_collector（-v 看明细）。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.sources import collector as C  # noqa: E402

# 假时间（固定 epoch，账本时间断言全靠它）
NOW = 1_700_000_000.0
DAY = 24 * 3600.0

# ---------------- 假数据：种子页与候选页（全假域名） ----------------
SEED_GOOD_ADDR = "https://agg.example/good.md"
SEED_DEAD_ADDR = "https://dead-seed.example/page"
SEED_GARBAGE_ADDR = "https://messy.example/notes"

GOOD_PAGE = """# 每日免费节点聚合（假站示例）
- [订阅一](https://sub.a.example/free.txt)
- <a href="http://sub.b.example/list.md">订阅二</a>
- 直链: https://sub.c.example/nodes.txt。
- <a href='https://sub.d.example/dupe.txt'>订阅四（单引号 href）</a>
- [重复形态](https://sub.a.example/free.txt)
- 节点分享链接不抽：vless://uid@10.9.8.7:443?security=tls&s=abc
- 死链候选：[坏了](https://dead.example.org/x.txt)
"""

CAND_PAGES = {
    "https://sub.a.example/free.txt":
        "vless://alpha@10.0.0.1:443?x=1\nvless://beta@10.0.0.2:443?x=2\n",
    "http://sub.b.example/list.md":
        "ss://bad-line!!!\n",
    "https://sub.c.example/nodes.txt":
        "garbage-nothing-here",
    "https://sub.d.example/dupe.txt":
        "vless://alpha@10.0.0.1:443?x=1\n",
}

EXPECTED_CANDS = [
    "https://sub.a.example/free.txt",
    "http://sub.b.example/list.md",
    "https://sub.c.example/nodes.txt",
    "https://sub.d.example/dupe.txt",
    "https://dead.example.org/x.txt",
]

SEEDS = [
    {"id": "seed-good", "category": "agg_repo", "address": SEED_GOOD_ADDR},
    {"id": "seed-dead", "category": "list_site", "address": SEED_DEAD_ADDR},
    {"id": "seed-garbage", "category": "agg_repo",
     "address": SEED_GARBAGE_ADDR},
]


def fake_fetch(url):
    """假拉取：好种子有货、死种子抛超时、垃圾种子纯文字、候选表内按表
    供页、其余一律假死链。"""
    if url == SEED_GOOD_ADDR:
        return GOOD_PAGE
    if url == SEED_DEAD_ADDR:
        raise TimeoutError("模拟种子超时")
    if url == SEED_GARBAGE_ADDR:
        return "这页是纯说明文字，没有可抽的地址。"
    if url in CAND_PAGES:
        return CAND_PAGES[url]
    raise ConnectionError(f"假死链（fetch 不到）: {url}")


def fake_parse(text):
    """假格式识别：认得出 → 节点条目（dict 带 id，模仿 Node 身份）；
    坏内容 → ValueError（模仿 import_any_text 显式报错）；纯垃圾 → []
    （模仿解析为空）。"""
    out = []
    if "10.0.0.1" in text:
        out.append({"id": "manual:vless:10.0.0.1:443", "extra": {}})
    if "10.0.0.2" in text:
        out.append({"id": "manual:vless:10.0.0.2:443", "extra": {}})
    if "10.0.0.3" in text:
        out.append({"id": "manual:vless:10.0.0.3:443", "extra": {}})
    if out:
        return out
    if "ss://bad-line" in text:
        raise ValueError("无法识别导入内容（假 parse）")
    if "garbage" in text:
        return []
    raise ValueError("认不出（假 parse）")


def _disable_by_failures(ledger, seed_id, now=NOW):
    """按账本规则连记 3 次失败，构造一个自动停用的种子（R4）。"""
    for _ in range(3):
        C.record_result(ledger, seed_id, ok=False, produced=0, now=now)
    return ledger


class ExtractCandidatesTest(unittest.TestCase):
    """DESIGN §3②：三形态、只收 http(s)、去重保序。"""

    def test_three_forms_dedup_preserve_order(self):
        """好种子页：markdown/href/纯文本三形态全抽出，重复与 vless 不算，
        尾部中文句号剥掉，顺序按首次出现位置。"""
        self.assertEqual(C.extract_candidates(GOOD_PAGE), EXPECTED_CANDS)

    def test_punctuation_scheme_and_empty(self):
        """尾随标点剥、非 http(s) 不收、句子括号残尾剥、空/非 str → []。"""
        text = ("地址 http://x.example/a.txt, 与 ftp://files.example/nope "
                "括号 (https://w.example/d.txt) 与 "
                "[t](https://z.example/c.txt)")
        self.assertEqual(C.extract_candidates(text), [
            "http://x.example/a.txt",
            "https://w.example/d.txt",
            "https://z.example/c.txt",
        ])
        self.assertEqual(C.extract_candidates("没有任何地址"), [])
        self.assertEqual(C.extract_candidates(""), [])
        self.assertEqual(C.extract_candidates(None), [])


class LedgerTest(unittest.TestCase):
    """DESIGN §2.2 记账规则 R0–R8 逐条。"""

    def setUp(self):
        self.ledger = C.new_ledger()

    def test_fail_three_times_disables(self):
        """R3/R4：失败逐次降权，连续 3 次停用；第 4 次不复活也不改口。"""
        for i in range(3):
            rec = C.record_result(self.ledger, "s", ok=False, produced=0,
                                  now=NOW)
        self.assertFalse(rec["enabled"])
        self.assertEqual(rec["disabled_reason"], "连续失败 3 次")
        self.assertEqual(rec["weight"], 0.25)
        self.assertEqual(rec["consecutive_failures"], 3)
        self.assertEqual(rec["last_fail_at"], NOW)
        rec = C.record_result(self.ledger, "s", ok=False, produced=0,
                              now=NOW + DAY)
        self.assertFalse(rec["enabled"])            # 停用维持
        self.assertEqual(rec["weight"], 0.0)        # 继续降权
        self.assertEqual(rec["disabled_reason"], "连续失败 3 次")

    def test_success_recovers_weight_and_reuses_disabled(self):
        """R5/R6：成功权重回升；停用的自动种子「复测成功且产出>0」即复用。"""
        _disable_by_failures(self.ledger, "s")
        rec = C.record_result(self.ledger, "s", ok=True, produced=4,
                              now=NOW + 8 * DAY)
        self.assertTrue(rec["enabled"])
        self.assertEqual(rec["disabled_reason"], "")
        self.assertEqual(rec["recovered_at"], NOW + 8 * DAY)
        self.assertEqual(rec["weight"], 0.45)       # 0.25 + 0.2
        self.assertEqual(rec["produced_total"], 4)
        self.assertEqual(rec["consecutive_failures"], 0)

    def test_manual_disable_never_auto_reused(self):
        """R7：手动停用的种子，成功也不自动复用（只有人能救活它）。"""
        C.record_result(self.ledger, "s", ok=True, produced=1, now=NOW)
        self.ledger["seeds"]["s"]["enabled"] = False
        self.ledger["seeds"]["s"]["disabled_reason"] = C.MANUAL_DISABLE_REASON
        rec = C.record_result(self.ledger, "s", ok=True, produced=4, now=NOW)
        self.assertFalse(rec["enabled"])
        self.assertEqual(rec["disabled_reason"], C.MANUAL_DISABLE_REASON)

    def test_zero_yield_penalizes_and_eventually_disables(self):
        """R2：活而空不进连续失败；零产出计满 3 降权一档并清零重计；
        权重耗尽（15 轮零产出 = 5 档）后停用。"""
        rec = C.record_result(self.ledger, "s", ok=True, produced=0, now=NOW)
        self.assertEqual(rec["zero_streak"], 1)
        self.assertEqual(rec["consecutive_failures"], 0)
        self.assertEqual(rec["weight"], 1.0)
        for _ in range(2):                          # 加上面一次共 3 轮零产出
            rec = C.record_result(self.ledger, "s", ok=True, produced=0,
                                  now=NOW)
        self.assertEqual(rec["zero_streak"], 0)     # 计满清零重计
        self.assertEqual(rec["weight"], 0.8)        # 降权一档
        self.assertTrue(rec["enabled"])
        for _ in range(12):
            rec = C.record_result(self.ledger, "s", ok=True, produced=0,
                                  now=NOW)
        self.assertFalse(rec["enabled"])            # 权重耗尽
        self.assertEqual(rec["disabled_reason"], "权重耗尽（连续零产出）")
        self.assertEqual(rec["weight"], 0.0)

    def test_save_load_roundtrip_missing_and_corrupt(self):
        """落盘/回读一致；缺文件→空账本；损坏→显式 ValueError。"""
        tmp = tempfile.mkdtemp(dir=os.path.dirname(os.path.abspath(__file__)))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        path = os.path.join(tmp, "collect_ledger.json")
        self.assertEqual(C.load_ledger(path), C.new_ledger())   # 缺文件
        C.record_result(self.ledger, "seed-x", ok=True, produced=3, now=NOW)
        C.save_ledger(path, self.ledger)
        loaded = C.load_ledger(path)
        self.assertEqual(loaded, self.ledger)
        with open(path, "w", encoding="utf-8") as f:
            f.write("不是 json {{{")
        with self.assertRaises(ValueError):
            C.load_ledger(path)

    def test_pick_probation(self):
        """R6：只挑冷却满 7 天的自动停用种子，取最久未试者；手动停用与
        未冷却的不挑。"""
        _disable_by_failures(self.ledger, "seed-a", now=NOW)
        _disable_by_failures(self.ledger, "seed-b", now=NOW - DAY)
        self.assertIsNone(C.pick_probation(self.ledger, now=NOW))  # 全未冷却
        self.assertEqual(C.pick_probation(self.ledger, now=NOW + 8 * DAY),
                         "seed-b")          # 更久没试（last_fail_at 更旧）
        self.ledger["seeds"]["seed-c"] = {
            "weight": 1.0, "enabled": False,
            "disabled_reason": C.MANUAL_DISABLE_REASON,
            "ok_count": 0, "fail_count": 0, "produced_total": 0,
            "consecutive_failures": 0, "zero_streak": 0,
            "last_ok_at": None, "last_fail_at": NOW - 30 * DAY,
            "recovered_at": None, "created_at": NOW, "updated_at": NOW}
        self.assertEqual(C.pick_probation(self.ledger, now=NOW + 8 * DAY),
                         "seed-b")          # 手动停用的 seed-c 不参与


class RunCollectionTest(unittest.TestCase):
    """整轮编排：报告数字对得上假数据、账本联动、失效耐受、缓刑复测。"""

    def setUp(self):
        self.ledger = C.new_ledger()
        # 整轮跑完不抛（brief 红线）：run_collection 自吞单点失败
        self.report = C.run_collection(SEEDS, fake_fetch, fake_parse,
                                       ledger=self.ledger, now=NOW)

    def test_round_report_matches_fixture(self):
        """报告数字与假数据一一对应（§4.2）。"""
        r = self.report
        self.assertEqual(r["seeds_total"], 3)
        self.assertEqual(r["seeds_ok"], 2)
        self.assertEqual(r["seeds_failed"], 1)
        self.assertEqual(r["seeds_skipped"], 0)
        self.assertEqual(r["ts"], NOW)
        self.assertTrue(r["round_id"].startswith("R"))
        # 逐种子：一好（产出候选）、一死（异常进报告）、一垃圾（零候选）
        rows = {row["seed_id"]: row for row in r["seeds"]}
        self.assertTrue(rows["seed-good"]["ok"])
        self.assertEqual(rows["seed-good"]["candidates"], 5)
        self.assertEqual(rows["seed-good"]["nodes"], 2)
        self.assertFalse(rows["seed-dead"]["ok"])
        self.assertEqual(rows["seed-dead"]["error"],
                         "TimeoutError: 模拟种子超时")
        self.assertTrue(rows["seed-garbage"]["ok"])
        self.assertEqual(rows["seed-garbage"]["candidates"], 0)
        # 候选：好种子页内去重后 5（extract 已做种子内去重）、死 3
        self.assertEqual(r["candidates_total"], 5)
        self.assertEqual(r["candidates_unique"], 5)
        self.assertEqual(r["candidates_dead"], 3)
        dead = {(d["url"], d["stage"]) for d in r["dead_candidates"]}
        self.assertIn(("https://dead.example.org/x.txt", "fetch"), dead)
        self.assertIn(("http://sub.b.example/list.md", "parse"), dead)
        self.assertIn(("https://sub.c.example/nodes.txt", "parse"), dead)
        fetch_err = [d for d in r["dead_candidates"]
                     if d["url"] == "https://dead.example.org/x.txt"][0]
        self.assertTrue(fetch_err["error"].startswith("ConnectionError"))
        parse_err = [d for d in r["dead_candidates"]
                     if d["url"] == "http://sub.b.example/list.md"][0]
        self.assertEqual(parse_err["error"],
                         "ValueError: 无法识别导入内容（假 parse）")
        # 节点池：解析条数 3（sub.d 与 sub.a 撞身份）、身份去重后 2
        self.assertEqual(r["nodes_total"], 3)
        self.assertEqual(r["nodes_unique"], 2)
        self.assertEqual([it["id"] for it in r["pool"]],
                         ["manual:vless:10.0.0.1:443",
                          "manual:vless:10.0.0.2:443"])
        self.assertFalse(r["zero_yield"])
        self.assertIn("节点池 2", r["summary"])

    def test_pool_provenance_stamped(self):
        """§3.3：池内条目带四个溯源键，轮次号与报告一致。"""
        it = self.report["pool"][0]
        self.assertEqual(it["extra"]["collect_seed_id"], "seed-good")
        self.assertEqual(it["extra"]["collect_seed_category"], "agg_repo")
        self.assertEqual(it["extra"]["collect_round"],
                         self.report["round_id"])
        self.assertEqual(it["extra"]["collected_from"],
                         "https://sub.a.example/free.txt")

    def test_ledger_updated_by_round(self):
        """账本按规则联动：好种子记产出（R1）、死种子降权（R3）、
        垃圾种子记活而空（R2）。"""
        seeds = self.ledger["seeds"]
        self.assertEqual(seeds["seed-good"]["produced_total"], 5)  # 首见 5 条
        self.assertEqual(seeds["seed-good"]["ok_count"], 1)
        self.assertEqual(seeds["seed-good"]["last_ok_at"], NOW)
        self.assertEqual(seeds["seed-good"]["weight"], 1.0)  # 封顶
        self.assertEqual(seeds["seed-dead"]["fail_count"], 1)
        self.assertEqual(seeds["seed-dead"]["consecutive_failures"], 1)
        self.assertEqual(seeds["seed-dead"]["weight"], 0.75)
        self.assertTrue(seeds["seed-dead"]["enabled"])  # 一次失败不停用
        self.assertEqual(seeds["seed-garbage"]["zero_streak"], 1)
        self.assertEqual(seeds["seed-garbage"]["produced_total"], 0)
        self.assertEqual(seeds["seed-garbage"]["weight"], 1.0)

    def test_duplicate_candidates_attribute_to_first_seer(self):
        """R8：跨种子重复的候选只归首个发现者计功，地址只拉一次。"""
        seeds = [
            {"id": "seed-good", "category": "agg_repo",
             "address": SEED_GOOD_ADDR},
            {"id": "seed-second", "category": "list_site",
             "address": "https://second.example/again.md"},
        ]
        pages = dict(CAND_PAGES)
        pages["https://second.example/again.md"] = (
            "- [又是订阅一](https://sub.a.example/free.txt)\n"
            "- [新的](https://sub.e.example/f.txt)\n")
        pages["https://sub.e.example/f.txt"] = "vless://gamma@10.0.0.3:443\n"

        def fetch2(url):
            if url == SEED_GOOD_ADDR:
                return GOOD_PAGE
            if url in pages:
                return pages[url]
            raise ConnectionError(f"假死链: {url}")

        ledger = C.new_ledger()
        r = C.run_collection(seeds, fetch2, fake_parse, ledger=ledger,
                             now=NOW)
        self.assertEqual(r["candidates_total"], 7)   # 5 + 2（各种子页内去重后）
        self.assertEqual(r["candidates_unique"], 6)  # 新增只有 sub.e
        self.assertEqual(r["nodes_unique"], 3)       # 池里多了 gamma
        rows = {row["seed_id"]: row for row in r["seeds"]}
        self.assertEqual(rows["seed-second"]["candidates"], 2)  # raw
        self.assertEqual(rows["seed-second"]["nodes"], 1)
        self.assertEqual(ledger["seeds"]["seed-second"]["produced_total"], 1)
        self.assertEqual(ledger["seeds"]["seed-good"]["produced_total"], 5)

    def test_all_dead_zero_yield_honest(self):
        """§4.2：全死轮零产出——不抛、zero_yield、summary 如实报告。"""
        seeds = [
            {"id": "seed-dead", "category": "list_site",
             "address": SEED_DEAD_ADDR},
            {"id": "seed-dead2", "category": "list_site",
             "address": "https://another-dead.example/x"},
        ]
        ledger = C.new_ledger()
        r = C.run_collection(seeds, fake_fetch, fake_parse, ledger=ledger,
                             now=NOW)
        self.assertTrue(r["zero_yield"])
        self.assertEqual(r["pool"], [])
        self.assertEqual(r["candidates_unique"], 0)
        self.assertEqual(r["seeds_failed"], 2)
        self.assertEqual(r["nodes_unique"], 0)
        self.assertIn("本轮产出 0", r["summary"])
        self.assertEqual(ledger["seeds"]["seed-dead"]["fail_count"], 1)
        self.assertEqual(ledger["seeds"]["seed-dead2"]["fail_count"], 1)

    def test_disabled_seeds_skipped_not_failed(self):
        """账本 enabled=false 的种子本轮跳过（不计失败）；无账本记录时
        尊重 seed 描述里的 enabled=False。"""
        ledger = C.new_ledger()
        C.record_result(ledger, "seed-manual", ok=True, produced=1, now=NOW)
        ledger["seeds"]["seed-manual"]["enabled"] = False
        ledger["seeds"]["seed-manual"]["disabled_reason"] = \
            C.MANUAL_DISABLE_REASON
        seeds = [
            {"id": "seed-good", "category": "agg_repo",
             "address": SEED_GOOD_ADDR},
            {"id": "seed-manual", "category": "list_site",
             "address": SEED_DEAD_ADDR},          # 账本停用 → 跳过
            {"id": "seed-off", "category": "agg_repo",
             "address": SEED_GARBAGE_ADDR, "enabled": False},
        ]
        r = C.run_collection(seeds, fake_fetch, fake_parse, ledger=ledger,
                             now=NOW)
        rows = {row["seed_id"]: row for row in r["seeds"]}
        self.assertTrue(rows["seed-manual"]["skipped_disabled"])
        self.assertTrue(rows["seed-off"]["skipped_disabled"])
        self.assertEqual(r["seeds_skipped"], 2)
        self.assertEqual(r["seeds_failed"], 0)
        self.assertEqual(ledger["seeds"]["seed-manual"]["ok_count"], 1)
        self.assertNotIn("seed-off", ledger["seeds"])   # 跳过不建档

    def test_probation_retest_failure_keeps_disabled(self):
        """R6：冷却满 7 天的停用种子被复测，仍失败 → 维持停用，
        失败照记（冷却从新时间重算）。"""
        ledger = _disable_by_failures(C.new_ledger(), "seed-dead")
        seeds = [{"id": "seed-dead", "category": "list_site",
                  "address": SEED_DEAD_ADDR}]
        r = C.run_collection(seeds, fake_fetch, fake_parse, ledger=ledger,
                             now=NOW + 8 * DAY)
        row = r["seeds"][0]
        self.assertTrue(row["probation"])           # 本轮唯一复测对象
        self.assertFalse(row["ok"])
        rec = ledger["seeds"]["seed-dead"]
        self.assertFalse(rec["enabled"])            # 复测仍失败 → 停用维持
        self.assertEqual(rec["fail_count"], 4)
        self.assertEqual(rec["last_fail_at"], NOW + 8 * DAY)
        self.assertEqual(rec["weight"], 0.0)

    def test_probation_retest_success_reuses(self):
        """R6：复测成功且产出>0 → 复用；候选死活不影响复用（R8：
        种子只对拉取+抽取负责，本例候选全是死链，种子照样记成功）。"""
        ledger = _disable_by_failures(C.new_ledger(), "seed-dead")

        def fetch(url):
            if url == SEED_DEAD_ADDR:
                return GOOD_PAGE            # 种子复活了
            raise ConnectionError(f"假死链: {url}")

        r = C.run_collection(
            [{"id": "seed-dead", "category": "list_site",
              "address": SEED_DEAD_ADDR}],
            fetch, fake_parse, ledger=ledger, now=NOW + 8 * DAY)
        row = r["seeds"][0]
        self.assertTrue(row["probation"])
        self.assertTrue(row["ok"])
        self.assertEqual(row["candidates"], 5)
        rec = ledger["seeds"]["seed-dead"]
        self.assertTrue(rec["enabled"])             # 复用
        self.assertEqual(rec["recovered_at"], NOW + 8 * DAY)
        self.assertEqual(rec["produced_total"], 5)  # 死候选不计入成败
        self.assertEqual(rec["weight"], 0.45)

    def test_malformed_seed_raises_upfront(self):
        """§4.1：结构错误当场 ValueError——调用方 bug 不伪装成源失败。"""
        with self.assertRaises(ValueError):
            C.run_collection([{"id": "x"}], fake_fetch, fake_parse)
        with self.assertRaises(ValueError):
            C.run_collection("not-a-list", fake_fetch, fake_parse)


if __name__ == "__main__":
    unittest.main()
