#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
work18 离线测试（test_rulesets.py）—— U3 拦截规则集
========================================================================
集成形态（work18/DESIGN.md §10 集成适配）：两个交付模块已是 client.core
真包成员，直接 import；内存挂载/内存接线补丁段已删。client-dev 缺席时
基础组照跑（impl 模块 stdlib-only 独立可导入），口径一致/片段收口/
合并兼容组自动跳过（task12 口径一致组先例）。

组清单（对应简报交付物 2）：
1. 交付物在位 + 静态扫描（impl 模块零 "://" 字样——全仓无真实 URL 红线；
   四禁词/节点优选词零出现——铁律红线）；
2. 来源校验组（逐项报错不静默吞；空槽语义）；
3. 来源文件层组（缺文件=空槽 / 损坏如实报错 / version 拒读 / 原子写往返）；
4. 缓存组（假 fetcher 假 .srs 字节：原子落盘、失败保旧字节不变、
   空内容拒写、tmp 无残留；状态报告字段齐全）；
5. plan/resolve 组（四种开关/缓存/来源组合的产出形状——验收口径；
   uncovered 显式报错、绝不假装生效）；
6. 口径一致组（client-dev 在场）：tag 常量与 usage_model 逐字机械锁定、
   DEFAULT_DATA_DIR 同族锁定、占位键不复制；
7. 片段收口组（client-dev 在场）：直调 client-dev 真 build_route_config
   （内存接线补丁已删，见 §10），四种组合跑真实函数 + state=None 时
   占位语义断言（0.14.0 占位语义保持红线）；
8. 合并兼容组（client-dev 在场）：wired 片段过 usage_merge——占位路径
   照旧滤除（U2 方案 A 零改动）、真条目路径照旧并入（先拦截后解析）；
9. sing-box check 组（二进制不在场 skip 并记 DONE.md）：内核自编译 .srs
   夹具，local 真条目 / remote 字段形状 / 默认片段三种配置过 check；
10. ③区 offscreen 组（PySide6 缺席整组 skip 并记 DONE.md）：直调真
    UsageRulesPanel，按钮→缓存落盘→状态行逐 tag。

假数据 only：RFC 2606 保留域（example.invalid）+ RFC 5737 保留 IP +
127.0.0.1:9（loopback 拒收端口——远端条目 URL 用它，即便内核 check 去
取也零外联、瞬时拒绝）。零真实规则集 URL / 节点地址 / 订阅 / 密钥。
"""

from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import types
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

_HAS_DEV = CLIENT_DEV is not None

if _HAS_DEV:
    import client
    import client.core
    # 集成后两个交付模块已是 client.core 的真包成员，直接 import。
    from client.core import (ruleset_cache, ruleset_source, settings_proto,
                             usage_merge, usage_model)
    from client.core.usage_model import UsageSettings
    from client.engines.singbox_engine import SingBoxEngine
    from client.core.model import Node
else:
    ruleset_cache = ruleset_source = None
    settings_proto = usage_merge = usage_model = None
    UsageSettings = SingBoxEngine = Node = None

try:
    from PySide6.QtWidgets import QApplication
    _QT = True
except ImportError:
    _QT = False

# ---------------------------------------------------------------------------
# 假数据 only（RFC 2606 / RFC 5737 保留值 + loopback 拒收端口），零真实 URL
# ---------------------------------------------------------------------------
_FAKE_DOMAIN = "ads.example.invalid"          # RFC 2606 保留域
_FAKE_V4 = "198.51.100.7"                     # RFC 5737 TEST-NET-2
# 远端条目夹具 URL：loopback + discard 端口（9）——形态合法（http + 主机），
# 绝非真实规则集地址，即便被内核取也是瞬时本机拒绝、零外联
_FAKE_REMOTE_URL = "http://127.0.0.1:9/rulesets/ads-blockset.srs"
_FAKE_SRS_A = b"FAKE-SRS-BYTES-ads-v1-\x00\x01"      # 假 .srs 字节（非真格式）
_FAKE_SRS_B = b"FAKE-SRS-BYTES-ads-v2-\x00\x02"


def make_usage(**kwargs):
    """UsageSettings 夹具（client-dev 在场时为真模型；缺席 None）。"""
    if not _HAS_DEV:
        return None
    fields = {"split_mode": "global", "ad_block": False, "malware_block": False,
              "ipv6": False, "dns_server": "google"}
    fields.update(kwargs)
    return UsageSettings(**fields)


def _write_file(path, data: bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# 组 1：交付物在位 + 静态扫描（无外部依赖）
# ---------------------------------------------------------------------------
class TestDeliverablesPresent(unittest.TestCase):
    """impl 镜像布局与静态红线扫描（全仓无真实 URL / 无节点优选词）。"""

    def test_impl_layout_mirrors_client_dev(self):
        for rel in ("client/core/ruleset_source.py", "client/core/ruleset_cache.py"):
            self.assertTrue(
                os.path.isfile(os.path.join(CLIENT_DEV, rel)),
                f"{rel} 缺失（集成落位口径）")

    def test_impl_sources_contain_no_scheme_urls(self):
        """红线：impl 全部源码零 "://"——客户端不内置来源（work10 §8.2），
        连「知名公开规则集」地址都不许出现；url 校验只留 scheme 名。"""
        for rel in ("client/core/ruleset_source.py",
                    "client/core/ruleset_cache.py"):
            with open(os.path.join(CLIENT_DEV, rel), encoding="utf-8") as f:
                src = f.read()
            self.assertNotIn("://", src,
                             f"{rel} 出现 scheme:// 字样（真实/伪 URL 红线）")

    def test_impl_sources_free_of_node_selection_words(self):
        """铁律红线扫描：拦截只走 reject 语义，不夹带节点优选/替换行为。"""
        forbidden = ("urltest", "selector", "connect_best", "connect_fastest",
                     "按延迟排序", "定时延迟对比换节点")
        for rel in ("client/core/ruleset_source.py",
                    "client/core/ruleset_cache.py"):
            with open(os.path.join(CLIENT_DEV, rel), encoding="utf-8") as f:
                src = f.read().lower()
            for word in forbidden:
                self.assertNotIn(word, src, f"{rel} 出现禁词 {word!r}")

    def test_tag_constants_literal_values(self):
        """tag 名字面量锁定（work10 §8.1 固定名——集成时不得改名）。"""
        self.assertEqual(ruleset_source.ADS_RULESET_TAG, "ads-blockset")
        self.assertEqual(ruleset_source.MALWARE_RULESET_TAG, "malware-blockset")
        self.assertEqual(ruleset_source.CN_DIRECT_RULESET_TAG, "cn-directset")
        self.assertEqual(ruleset_source.SOURCE_TYPES, ("local", "remote"))


# ---------------------------------------------------------------------------
# 组 2：来源校验组（逐项报错，不静默吞）
# ---------------------------------------------------------------------------
class TestSourceValidation(unittest.TestCase):
    def _entry(self, **kw):
        base = {"tag": "ads-blockset", "type": "local", "path": "/tmp/x.srs",
                "url": None, "update_interval": None}
        base.update(kw)
        return base

    def test_unknown_tag_rejected(self):
        for tag in ("my-ads", "", None, "ads-blockset2"):
            with self.assertRaisesRegex(
                    ruleset_source.RulesetSourceError, "tag 只认"):
                ruleset_source.validate_source_entry(self._entry(tag=tag))
            with self.assertRaises(ruleset_source.RulesetSourceError):
                ruleset_source.empty_source("my-tag")
            with self.assertRaises(ruleset_source.RulesetSourceError):
                ruleset_source.approve_source(
                    "my-tag", source_type="local", path="/tmp/x.srs")

    def test_type_must_be_local_or_remote(self):
        for t in ("", "Local", "http", None, 1, True):
            with self.assertRaisesRegex(
                    ruleset_source.RulesetSourceError, "type 只允许"):
                ruleset_source.validate_source_entry(self._entry(type=t))

    def test_local_requires_path_without_url(self):
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError,
                                    "path 必须是非空字符串"):
            ruleset_source.validate_source_entry(self._entry(path=None))
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError,
                                    "path 必须是非空字符串"):
            ruleset_source.validate_source_entry(self._entry(path="   "))
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError,
                                    "不允许 url"):
            ruleset_source.validate_source_entry(
                self._entry(path="/tmp/x.srs", url=_FAKE_REMOTE_URL))

    def test_remote_requires_url_without_path(self):
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError,
                                    "url 必须是非空字符串"):
            ruleset_source.validate_source_entry(
                self._entry(type="remote", path=None, url=None))
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError,
                                    "不允许 path"):
            ruleset_source.validate_source_entry(
                self._entry(type="remote", url=_FAKE_REMOTE_URL,
                            path="/tmp/x.srs"))

    def test_url_shape_validation(self):
        bad = ("ftp://h/x.srs", "file:///tmp/x.srs", "hxxp://h/x.srs",
               "not-a-url", "http://", "https:// host/x.srs",
               "http://h/x.srs?next= http://y", "")
        for url in bad:
            with self.assertRaisesRegex(
                    ruleset_source.RulesetSourceError,
                    "(只允许 http/https scheme|缺主机|不允许空白|"
                    "必须是非空字符串)"):
                ruleset_source.validate_source_entry(
                    self._entry(type="remote", path=None, url=url))

    def test_local_path_must_be_absolute(self):
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError,
                                    "必须是绝对路径"):
            ruleset_source.validate_source_entry(
                self._entry(path="cache/rulesets/ads.srs"))

    def test_local_path_normalizes_home(self):
        entry = ruleset_source.validate_source_entry(self._entry(path="~/s.srs"))
        self.assertTrue(entry["path"].startswith(os.path.expanduser("~")))
        self.assertNotIn("~", entry["path"])

    def test_update_interval_validation(self):
        for good in ("1d", "12h", "30m", "1h30m", "90s", "500ms", None):
            entry = ruleset_source.validate_source_entry(
                self._entry(update_interval=good))
            self.assertEqual(
                entry["update_interval"],
                good.strip() if isinstance(good, str) else None)
        for bad in ("banana", "1w", "1", "d", "1d 2h", "", 86400, True):
            with self.assertRaisesRegex(ruleset_source.RulesetSourceError,
                                        "update_interval"):
                ruleset_source.validate_source_entry(
                    self._entry(update_interval=bad))

    def test_unknown_keys_rejected(self):
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError,
                                    "未知键"):
            ruleset_source.validate_source_entry(
                self._entry(name="extra"))

    def test_empty_slot_semantics(self):
        """默认状态 = 空槽：type=None、无 url、无路径指向真实文件
        （work10 §8.2——语义上仍是占位）。"""
        slot = ruleset_source.empty_source("ads-blockset")
        self.assertEqual(slot, {"tag": "ads-blockset", "type": None,
                                "path": None, "url": None,
                                "update_interval": None})
        self.assertEqual(
            sorted(ruleset_source.empty_sources()),
            sorted(ruleset_source.KNOWN_RULESET_TAGS))
        # 空槽仅允许以 filled_only=False 校验（不落盘形态）
        normalized = ruleset_source.validate_source_entry(
            dict(slot), filled_only=False)
        self.assertEqual(normalized, slot)
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError,
                                    "type 只允许"):
            ruleset_source.validate_source_entry(dict(slot))  # 文件层拒收空槽

    def test_unknown_tags_in_active_and_state(self):
        with self.assertRaises(ruleset_source.RulesetSourceError):
            ruleset_source.plan_ruleset_parts({"my-tag": True}, None)
        # resolve_ruleset_state 的产物恒含全部已知 tag；只有手造的残缺
        # state（接线层契约破坏）才触发缺状态报错
        partial = {"ads-blockset": {"tag": "ads-blockset", "approved": False}}
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError,
                                    "缺 malware-blockset"):
            ruleset_source.plan_ruleset_parts(
                {"malware-blockset": True}, partial)


# ---------------------------------------------------------------------------
# 组 3：来源文件层组（缺文件=空槽 / 损坏如实报错 / 原子写）
# ---------------------------------------------------------------------------
class TestSourceFileLayer(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="u3-src-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.path = ruleset_source.sources_path(self.dir)

    def test_missing_file_is_all_empty_slots(self):
        sources = ruleset_source.load_sources(self.path)
        self.assertEqual(sources, ruleset_source.empty_sources())
        for entry in sources.values():
            self.assertIsNone(entry["type"])
            self.assertIsNone(entry["url"])
            self.assertIsNone(entry["path"])

    def test_corrupt_file_reported_not_swallowed(self):
        _write_file(self.path, b"{not json")
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError,
                                    "损坏"):
            ruleset_source.load_sources(self.path)

    def test_wrapper_shape_and_version(self):
        _write_file(self.path, json.dumps({"version": 1, "sources": {}}).encode())
        self.assertEqual(ruleset_source.load_sources(self.path),
                         ruleset_source.empty_sources())
        _write_file(self.path, json.dumps({"version": 2, "sources": {}}).encode())
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError, "version"):
            ruleset_source.load_sources(self.path)
        _write_file(self.path, json.dumps(
            {"version": 1, "sources": {}, "extra": 1}).encode())
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError, "未知键"):
            ruleset_source.load_sources(self.path)
        _write_file(self.path, json.dumps({"version": 1}).encode())
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError, "sources"):
            ruleset_source.load_sources(self.path)

    def test_unknown_tag_in_file_rejected(self):
        _write_file(self.path, json.dumps({
            "version": 1,
            "sources": {"my-ads": {"tag": "my-ads", "type": "local",
                                   "path": "/tmp/x.srs"}}}).encode())
        with self.assertRaisesRegex(ruleset_source.RulesetSourceError, "未知 tag"):
            ruleset_source.load_sources(self.path)

    def test_approve_roundtrip_and_atomic_write(self):
        entry = ruleset_source.approve_source(
            "ads-blockset", source_type="local", path="/tmp/stage/ads.srs",
            data_dir=self.dir)
        self.assertEqual(entry["type"], "local")
        self.assertEqual(entry["path"], "/tmp/stage/ads.srs")
        self.assertIsNone(entry["url"])
        sources = ruleset_source.load_sources(self.path)
        self.assertEqual(sources["ads-blockset"], entry)
        self.assertEqual(sources["malware-blockset"]["type"], None)  # 仍空槽
        # 只存已核定条目；tmp 无残留（原子写口径）
        with open(self.path, encoding="utf-8") as f:
            wrapper = json.load(f)
        self.assertEqual(sorted(wrapper["sources"]), ["ads-blockset"])
        self.assertEqual(wrapper["version"], 1)
        self.assertEqual([f for f in os.listdir(self.dir)
                          if f.endswith(".tmp")], [])

    def test_approve_remote_shape(self):
        entry = ruleset_source.approve_source(
            "malware-blockset", source_type="remote", url=_FAKE_REMOTE_URL,
            update_interval="1d", data_dir=self.dir)
        self.assertEqual(entry, {"tag": "malware-blockset", "type": "remote",
                                 "path": None, "url": _FAKE_REMOTE_URL,
                                 "update_interval": "1d"})
        # 重新核定覆盖旧值（登记表唯一事实源）
        entry2 = ruleset_source.approve_source(
            "malware-blockset", source_type="remote", url=_FAKE_REMOTE_URL,
            data_dir=self.dir)
        self.assertIsNone(entry2["update_interval"])
        self.assertEqual(
            len(ruleset_source.load_sources(self.path)["malware-blockset"]), 5)

    def test_save_rejects_bad_entries(self):
        sources = ruleset_source.empty_sources()
        sources["ads-blockset"] = dict(sources["ads-blockset"], type="bogus")
        with self.assertRaises(ruleset_source.RulesetSourceError):
            ruleset_source.save_sources(sources, self.path)
        sources["ads-blockset"] = {"tag": "ads-blockset", "type": "local",
                                   "path": "rel.srs"}
        with self.assertRaises(ruleset_source.RulesetSourceError):
            ruleset_source.save_sources(sources, self.path)


# ---------------------------------------------------------------------------
# 组 4：缓存组（假 fetcher 假 .srs 字节；原子替换 / 失败保旧 / 状态报告）
# ---------------------------------------------------------------------------
class TestRulesetCache(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="u3-cache-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.stage = os.path.join(self.dir, "stage", "ads.srs")
        _write_file(self.stage, _FAKE_SRS_A)
        self.local_entry = {"tag": "ads-blockset", "type": "local",
                            "path": self.stage, "url": None,
                            "update_interval": None}
        self.remote_entry = {"tag": "ads-blockset", "type": "remote",
                             "path": None, "url": _FAKE_REMOTE_URL,
                             "update_interval": "1d"}

    # ---- 状态报告 ----
    def test_cache_status_fields_complete(self):
        empty = ruleset_cache.cache_status(self.dir)
        self.assertEqual(sorted(empty), sorted(ruleset_source.KNOWN_RULESET_TAGS))
        item = empty["ads-blockset"]
        for key in ("tag", "path", "present", "size_bytes", "last_updated"):
            self.assertIn(key, item)
        self.assertFalse(item["present"])
        self.assertIsNone(item["size_bytes"])
        self.assertIsNone(item["last_updated"])
        self.assertTrue(item["path"].endswith("rulesets/ads-blockset.srs"))

        _write_file(ruleset_cache.tag_cache_path("ads-blockset", self.dir),
                    _FAKE_SRS_A)
        filled = ruleset_cache.cache_status(self.dir)["ads-blockset"]
        self.assertTrue(filled["present"])
        self.assertEqual(filled["size_bytes"], len(_FAKE_SRS_A))
        self.assertIsInstance(filled["last_updated"], float)

    def test_cache_status_absent_dir_is_smooth_start(self):
        missing = os.path.join(self.dir, "no-such-dir")
        report = ruleset_cache.cache_status(missing)
        self.assertTrue(all(not item["present"] for item in report.values()))

    def test_cache_status_rejects_unknown_tag(self):
        with self.assertRaises(ruleset_cache.RulesetCacheError):
            ruleset_cache.cache_status(self.dir, tags=("my-tag",))

    def test_tag_cache_path_rejects_unknown_tag(self):
        with self.assertRaises(ruleset_cache.RulesetCacheError):
            ruleset_cache.tag_cache_path("my-tag", self.dir)

    # ---- 原子替换与失败保旧（宁旧勿缺红线） ----
    def test_update_success_atomic_replace(self):
        cache_file = ruleset_cache.tag_cache_path("ads-blockset", self.dir)
        # 旧文件在场 → 更新后字节整体替换、tmp 无残留
        _write_file(cache_file, _FAKE_SRS_A)
        result = ruleset_cache.update_tag(
            "ads-blockset", fetcher=lambda t, e: _FAKE_SRS_B, data_dir=self.dir,
            source_entry=self.local_entry)
        self.assertTrue(result["ok"])
        self.assertEqual(result["kind"], "updated")
        self.assertEqual(result["size_bytes"], len(_FAKE_SRS_B))
        with open(cache_file, "rb") as f:
            self.assertEqual(f.read(), _FAKE_SRS_B)
        self.assertEqual(result["last_updated"],
                         os.path.getmtime(cache_file))
        leftovers = [f for f in os.listdir(os.path.dirname(cache_file))
                     if f.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_update_first_write_creates_dir(self):
        result = ruleset_cache.update_tag(
            "ads-blockset", fetcher=lambda t, e: _FAKE_SRS_A, data_dir=self.dir,
            source_entry=self.local_entry)
        self.assertTrue(result["ok"])
        self.assertTrue(
            os.path.isfile(ruleset_cache.tag_cache_path("ads-blockset", self.dir)))

    def test_fetch_failure_keeps_old_bytes(self):
        cache_file = ruleset_cache.tag_cache_path("ads-blockset", self.dir)
        _write_file(cache_file, _FAKE_SRS_A)
        with open(cache_file, "rb") as f:
            before = f.read()

        def boom(tag, entry):
            raise RuntimeError("network down (simulated)")

        result = ruleset_cache.update_tag(
            "ads-blockset", fetcher=boom, data_dir=self.dir,
            source_entry=self.local_entry)
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "fetch_failed")
        self.assertIn("旧缓存保持不动", result["message"])
        with open(cache_file, "rb") as f:
            self.assertEqual(f.read(), before)          # 字节级不变

    def test_empty_payload_rejected_keeps_old(self):
        cache_file = ruleset_cache.tag_cache_path("ads-blockset", self.dir)
        _write_file(cache_file, _FAKE_SRS_A)
        with open(cache_file, "rb") as f:
            before = f.read()
        result = ruleset_cache.update_tag(
            "ads-blockset", fetcher=lambda t, e: b"", data_dir=self.dir,
            source_entry=self.local_entry)
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "fetch_failed")
        with open(cache_file, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_write_failure_keeps_old_and_no_tmp(self):
        cache_file = ruleset_cache.tag_cache_path("ads-blockset", self.dir)
        _write_file(cache_file, _FAKE_SRS_A)
        with open(cache_file, "rb") as f:
            before = f.read()
        # 缓存目录位置被一个普通文件占据 → mkdir/落盘必然失败
        blocked = os.path.join(self.dir, "blocked", "rulesets")
        _write_file(blocked, b"not a dir")
        entry = dict(self.local_entry)
        result = ruleset_cache.update_tag(
            "ads-blockset", fetcher=lambda t, e: _FAKE_SRS_B, data_dir=blocked,
            source_entry=entry)
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "write_failed")
        with open(cache_file, "rb") as f:
            self.assertEqual(f.read(), before)

    def test_no_source_slot_reported(self):
        slot = ruleset_source.empty_source("ads-blockset")
        result = ruleset_cache.update_tag(
            "ads-blockset", fetcher=lambda t, e: _FAKE_SRS_A, data_dir=self.dir,
            source_entry=slot)
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "no_source")
        self.assertIsNone(result["path"])           # 无文件动作
        self.assertFalse(
            os.path.exists(ruleset_cache.tag_cache_path("ads-blockset", self.dir)))

    def test_remote_entry_is_kernel_managed_no_file_action(self):
        result = ruleset_cache.update_tag(
            "ads-blockset", fetcher=lambda t, e: _FAKE_SRS_A, data_dir=self.dir,
            source_entry=self.remote_entry)
        self.assertFalse(result["ok"])
        self.assertEqual(result["kind"], "remote_kernel_managed")
        self.assertIn("内核", result["message"])
        self.assertIsNone(result["path"])           # 远端条目无客户端缓存语义
        self.assertFalse(
            os.path.exists(ruleset_cache.tag_cache_path("ads-blockset", self.dir)))

    def test_default_fetcher_reads_source_file(self):
        result = ruleset_cache.update_tag(
            "ads-blockset", data_dir=self.dir, source_entry=self.local_entry)
        self.assertTrue(result["ok"])
        with open(ruleset_cache.tag_cache_path("ads-blockset", self.dir),
                  "rb") as f:
            self.assertEqual(f.read(), _FAKE_SRS_A)

    def test_fetcher_receives_tag_and_entry(self):
        seen = {}

        def spy(tag, entry):
            seen.update(tag=tag, entry=entry)
            return _FAKE_SRS_A

        ruleset_cache.update_tag("ads-blockset", fetcher=spy,
                                 data_dir=self.dir,
                                 source_entry=self.local_entry)
        self.assertEqual(seen["tag"], "ads-blockset")
        self.assertEqual(seen["entry"], self.local_entry)

    # ---- 立即更新（逐 tag 批量） ----
    def test_update_rulesets_mixed_kinds(self):
        sources = ruleset_source.empty_sources()
        sources["ads-blockset"] = self.local_entry
        sources["malware-blockset"] = self.remote_entry
        # cn-directset 留空槽
        results = ruleset_cache.update_rulesets(
            fetcher=lambda t, e: _FAKE_SRS_A, data_dir=self.dir,
            sources=sources)
        self.assertEqual(results["ads-blockset"]["kind"], "updated")
        self.assertEqual(results["malware-blockset"]["kind"],
                         "remote_kernel_managed")
        self.assertEqual(results["cn-directset"]["kind"], "no_source")

    def test_update_rulesets_per_tag_independence(self):
        sources = ruleset_source.empty_sources()
        sources["ads-blockset"] = self.local_entry
        malware_path = os.path.join(self.dir, "stage", "malware.srs")
        _write_file(malware_path, _FAKE_SRS_B)
        sources["malware-blockset"] = {
            "tag": "malware-blockset", "type": "local", "path": malware_path,
            "url": None, "update_interval": None}

        calls = {"n": 0}

        def flaky(tag, entry):
            calls["n"] += 1
            if tag == "ads-blockset":
                raise RuntimeError("simulated failure")
            return _FAKE_SRS_B

        results = ruleset_cache.update_rulesets(
            fetcher=flaky, data_dir=self.dir, sources=sources,
            tags=("ads-blockset", "malware-blockset"))
        self.assertEqual(results["ads-blockset"]["kind"], "fetch_failed")
        self.assertEqual(results["malware-blockset"]["kind"], "updated")
        self.assertEqual(calls["n"], 2)             # 失败不中断其余 tag


# ---------------------------------------------------------------------------
# 组 5：plan/resolve 组（四种开关/缓存/来源组合——验收口径）
# ---------------------------------------------------------------------------
class TestPlanRulesetParts(unittest.TestCase):
    ALL_ON = {"ads-blockset": True, "malware-blockset": True,
              "cn-directset": False}

    def _state(self, sources=None, cache_dir=None):
        return ruleset_source.resolve_ruleset_state(
            sources or ruleset_source.empty_sources(), cache_dir or {})

    def test_switch_off_produces_nothing(self):
        """开关关 → 什么都不产出（现状）。"""
        parts = ruleset_source.plan_ruleset_parts(
            {"ads-blockset": False, "malware-blockset": False,
             "cn-directset": False}, self._state())
        self.assertEqual(parts, {"rules": [], "rule_set": [],
                                 "uncovered": [], "notes": []})

    def test_state_none_marks_placeholder(self):
        """未接线/原型口径（ruleset_state=None）→ 占位声明 + 原规则语义。"""
        parts = ruleset_source.plan_ruleset_parts(self.ALL_ON, None)
        self.assertEqual(parts["rules"], [
            {"action": "reject", "rule_set": ["ads-blockset"]},
            {"action": "reject", "rule_set": ["malware-blockset"]},
        ])
        self.assertEqual(parts["rule_set"], [
            {"__placeholder__": "ads-blockset"},
            {"__placeholder__": "malware-blockset"}])
        self.assertEqual(parts["uncovered"], [])
        self.assertEqual(parts["notes"], [])

    def test_no_cache_no_source_is_explicit_uncovered(self):
        """开关开 + 既无缓存文件又无核定来源 → 零条目零规则 + uncovered
        显式记录（绝不假装拦截已生效）。"""
        parts = ruleset_source.plan_ruleset_parts(
            self.ALL_ON, self._state())               # 全空槽 + 无缓存
        self.assertEqual(parts["rules"], [])
        self.assertEqual(parts["rule_set"], [])
        self.assertEqual(len(parts["uncovered"]), 2)
        for item in parts["uncovered"]:
            self.assertIn("feature", item)
            self.assertIn("belongs", item)
            self.assertIn("why", item)
            self.assertIn("来源未核定", item["why"])
        self.assertEqual(len(parts["notes"]), 2)

    def test_local_approved_with_cache_is_real_entry(self):
        """开关开 + 来源已核定 local + 缓存文件在 → local 真条目（无占位键，
        指向真实缓存路径）。"""
        sources = ruleset_source.empty_sources()
        sources["ads-blockset"] = {
            "tag": "ads-blockset", "type": "local", "path": "/tmp/stage.srs",
            "url": None, "update_interval": None}
        status = {"ads-blockset": {
            "tag": "ads-blockset", "path": "/data/rulesets/ads-blockset.srs",
            "present": True, "size_bytes": 10, "last_updated": 1.0}}
        state = self._state(sources, status)
        self.assertTrue(state["ads-blockset"]["approved"])
        self.assertEqual(state["ads-blockset"]["cache_file"],
                         "/data/rulesets/ads-blockset.srs")
        parts = ruleset_source.plan_ruleset_parts(self.ALL_ON, state)
        self.assertEqual(parts["rule_set"], [{
            "type": "local", "tag": "ads-blockset", "format": "binary",
            "path": "/data/rulesets/ads-blockset.srs"}])
        self.assertEqual(parts["rules"], [
            {"action": "reject", "rule_set": ["ads-blockset"]}])
        # 条目键集里没有占位键（占位键归 usage_model，真条目绝不携带）
        self.assertNotIn("prototype_placeholder", parts["rule_set"][0])
        # malware 仍空槽 → uncovered 恰 1 条（ad 已落真条目）
        self.assertEqual([u["feature"] for u in parts["uncovered"]],
                         ["恶意拦截（malware-blockset）"])

    def test_local_approved_without_cache_is_uncovered(self):
        """来源已核定 local 但缓存文件缺失 → 零条目 + uncovered（宁缺勿假）。"""
        sources = ruleset_source.empty_sources()
        sources["malware-blockset"] = {
            "tag": "malware-blockset", "type": "local",
            "path": "/tmp/stage.srs", "url": None, "update_interval": None}
        state = self._state(sources, {})              # 缓存报告为空
        parts = ruleset_source.plan_ruleset_parts(self.ALL_ON, state)
        self.assertEqual(parts["rules"], [])
        self.assertEqual(parts["rule_set"], [])
        self.assertEqual(len(parts["uncovered"]), 2)
        malware_item = [u for u in parts["uncovered"]
                        if "malware" in u["feature"]][0]
        self.assertIn("缓存文件缺失", malware_item["why"])
        ads_item = [u for u in parts["uncovered"]
                    if "ads" in u["feature"]][0]
        self.assertIn("来源未核定", ads_item["why"])

    def test_remote_approved_shape_per_13e(self):
        """开关开 + 来源已核定 remote → remote 条目（字段形状按 §13-E 核实
        结果：format binary + url + download_detour node-out；缺省
        update_interval = 内核默认 1d，不给字段）。"""
        sources = ruleset_source.empty_sources()
        sources["ads-blockset"] = {
            "tag": "ads-blockset", "type": "remote", "path": None,
            "url": _FAKE_REMOTE_URL, "update_interval": None}
        state = self._state(sources, {})
        parts = ruleset_source.plan_ruleset_parts(self.ALL_ON, state)
        self.assertEqual(parts["rule_set"][0], {
            "type": "remote", "tag": "ads-blockset", "format": "binary",
            "url": _FAKE_REMOTE_URL,
            "download_detour": ruleset_source.DOWNLOAD_DETOUR_TAG})
        self.assertNotIn("update_interval", parts["rule_set"][0])
        self.assertNotIn("prototype_placeholder", parts["rule_set"][0])

        sources["ads-blockset"]["update_interval"] = "12h"
        state = self._state(sources, {})
        parts = ruleset_source.plan_ruleset_parts(self.ALL_ON, state)
        self.assertEqual(parts["rule_set"][0]["update_interval"], "12h")

    def test_cn_direct_rule_mode_semantics(self):
        """cn-directset（rule 模式）语义原样：命中 route→direct-out 直连
        分流（work10 §4.1 既有口径，无节点动作）。"""
        sources = ruleset_source.empty_sources()
        sources["cn-directset"] = {
            "tag": "cn-directset", "type": "local", "path": "/tmp/cn.srs",
            "url": None, "update_interval": None}
        status = {"cn-directset": {
            "tag": "cn-directset", "path": "/data/rulesets/cn-directset.srs",
            "present": True, "size_bytes": 3, "last_updated": 1.0}}
        state = self._state(sources, status)
        parts = ruleset_source.plan_ruleset_parts(
            {"ads-blockset": False, "malware-blockset": False,
             "cn-directset": True}, state)
        self.assertEqual(parts["rules"], [
            {"action": "route", "outbound": "direct-out",
             "rule_set": ["cn-directset"]}])
        self.assertEqual(parts["rule_set"][0]["tag"], "cn-directset")


# ---------------------------------------------------------------------------
# 组 6：口径一致组（client-dev 只读参考机械锁定；缺席整组跳过，task12 先例）
# ---------------------------------------------------------------------------
@unittest.skipUnless(_HAS_DEV, "client-dev 不在场，口径一致组跳过")
class TestParityWithUsageModel(unittest.TestCase):
    """红线机械锁定：work18 口径与 usage_model 既有常量逐字一致，冲突以
    usage_model 为准（简报交付物 2：tag 名逐字一致）。"""

    def test_ruleset_tags_match_usage_model_verbatim(self):
        self.assertEqual(ruleset_source.ADS_RULESET_TAG,
                         usage_model.ADS_RULESET_TAG)
        self.assertEqual(ruleset_source.MALWARE_RULESET_TAG,
                         usage_model.MALWARE_RULESET_TAG)
        self.assertEqual(ruleset_source.CN_DIRECT_RULESET_TAG,
                         usage_model.CN_DIRECT_RULESET_TAG)
        self.assertEqual(
            ruleset_source.KNOWN_RULESET_TAGS,
            (usage_model.ADS_RULESET_TAG, usage_model.MALWARE_RULESET_TAG,
             usage_model.CN_DIRECT_RULESET_TAG))

    def test_outbound_tags_match_usage_model(self):
        self.assertEqual(ruleset_source.DOWNLOAD_DETOUR_TAG,
                         usage_model.OUT_TAG)
        self.assertEqual(ruleset_source.DIRECT_OUT_TAG,
                         usage_model.DIRECT_OUT_TAG)

    def test_placeholder_key_not_duplicated(self):
        """占位键唯一事实源 = usage_model（work17 方案 A 口径不动）；
        ruleset_source 不复制第二份。"""
        self.assertEqual(usage_model.PLACEHOLDER_KEY, "prototype_placeholder")
        self.assertFalse(hasattr(ruleset_source, "PLACEHOLDER_KEY"))

    def test_data_dir_family_lock(self):
        """缓存/登记落点与 DEFAULT_DB 同族口径（ui/cli.py DEFAULT_DB 的
        目录字面量 = settings_proto.DEFAULT_DATA_DIR）。"""
        self.assertEqual(ruleset_source.DEFAULT_DATA_DIR,
                         settings_proto.DEFAULT_DATA_DIR)
        default_dir = os.path.expanduser(settings_proto.DEFAULT_DATA_DIR)
        self.assertEqual(os.path.dirname(str(ruleset_cache.cache_dir())),
                         default_dir)
        self.assertEqual(os.path.dirname(str(ruleset_source.sources_path())),
                         default_dir)
        self.assertEqual(ruleset_source.sources_path().name,
                         "ruleset_sources.json")
        self.assertEqual(ruleset_cache.CACHE_DIRNAME, "rulesets")

    def test_reject_semantics_only_for_block_tags(self):
        """拦截只走 reject（铁律红线）；cn-directset 的 route→direct-out 是
        work10 §4.1 既有直连分流语义。"""
        self.assertEqual(
            ruleset_source.TAG_RULE_SEMANTICS[usage_model.ADS_RULESET_TAG],
            {"action": usage_model.SITE_REJECT})
        self.assertEqual(
            ruleset_source.TAG_RULE_SEMANTICS[usage_model.MALWARE_RULESET_TAG],
            {"action": usage_model.SITE_REJECT})
        self.assertEqual(
            ruleset_source.TAG_RULE_SEMANTICS[usage_model.CN_DIRECT_RULESET_TAG],
            {"action": "route", "outbound": usage_model.DIRECT_OUT_TAG})


# ---------------------------------------------------------------------------
# 组 7：片段收口组（集成形态：直调真 build_route_config；内存接线补丁已删，
# 见 work18/DESIGN.md §10 集成适配清单）
# ---------------------------------------------------------------------------


@unittest.skipUnless(_HAS_DEV, "client-dev 不在场，片段收口组跳过")
class TestWiredFragmentCombos(unittest.TestCase):
    """四种开关/缓存/来源组合跑真实 build_route_config（集成真身直调）。"""

    @classmethod
    def setUpClass(cls):
        # 集成形态：build_route_config 已是接线后真身，直调（DESIGN §10
        # 集成适配：内存补丁段删除）。
        cls.wired = staticmethod(usage_model.build_route_config)

    def setUp(self):
        # 逐用例独立数据目录（来源登记/缓存互不串场）
        self.dir = tempfile.mkdtemp(prefix="u3-frag-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    # ---- 红线：占位语义保持（wired(state=None) 与原函数深等价） ----
    def test_state_none_equals_original_verbatim(self):
        """ruleset_state=None（未接线/原型口径）→ 输出与 client-dev 现行
        build_route_config 深等价——0.14.0 片段层占位口径原样（work17
        「0.14.0「占位」口径在片段层原样保留」红线的 U3 延续）。"""
        cases = [
            make_usage(),
            make_usage(ad_block=True, malware_block=True),
            make_usage(split_mode="rule", ad_block=True),
            make_usage(split_mode="direct"),
            make_usage(split_mode="rule", ad_block=True, malware_block=True),
        ]
        for model in cases:
            fragment = self.wired(model, (), ruleset_state=None)
            baseline = usage_model.build_route_config(model, ())
            self.assertEqual(fragment, baseline,
                             f"占位语义漂移：{model.split_mode}/"
                             f"ad={model.ad_block}/mal={model.malware_block}")

    def test_state_none_placeholder_entries_intact(self):
        fragment = self.wired(make_usage(ad_block=True), (), ruleset_state=None)
        entry = fragment["route"]["rule_set"][0]
        self.assertEqual(entry["tag"], "ads-blockset")
        self.assertTrue(entry[usage_model.PLACEHOLDER_KEY])
        self.assertEqual(entry["path"], "cache/rulesets/ads.srs")

    # ---- 组合 1：开关关 → 无条目 ----
    def test_combo_switches_off(self):
        state = ruleset_source.resolve_ruleset_state(
            ruleset_source.empty_sources(),
            ruleset_cache.cache_status(self.dir))
        fragment = self.wired(make_usage(), (), ruleset_state=state)
        self.assertEqual(fragment["route"]["rule_set"], [])
        self.assertEqual(fragment["route"]["rules"], [])
        self.assertEqual(len(fragment["uncovered"]),
                         len(usage_model._uncovered()))  # 仅静态清单

    # ---- 组合 2：开关开 + 无缓存无来源 → uncovered 显式 ----
    def test_combo_on_without_cache_or_source(self):
        state = ruleset_source.resolve_ruleset_state(
            ruleset_source.empty_sources(),
            ruleset_cache.cache_status(self.dir))
        fragment = self.wired(make_usage(ad_block=True, malware_block=True),
                              (), ruleset_state=state)
        self.assertEqual(fragment["route"]["rule_set"], [])
        self.assertEqual(fragment["route"]["rules"], [])
        features = [item["feature"] for item in fragment["uncovered"]]
        self.assertEqual(len(features), len(usage_model._uncovered()) + 2)
        self.assertTrue(any("ads-blockset" in f for f in features))
        self.assertTrue(any("malware-blockset" in f for f in features))
        # 默认空槽状态下：片段零 URL 键、零 scheme 字样、零占位键
        text = json.dumps(fragment, ensure_ascii=False)
        self.assertNotIn("://", text)
        self.assertNotIn(usage_model.PLACEHOLDER_KEY, fragment["route"])
        self.assertNotIn("prototype_placeholder", text)

    # ---- 组合 3：开关开 + local 来源核定 + 缓存在 → local 真条目 ----
    def test_combo_on_with_local_cache(self):
        stage = os.path.join(self.dir, "stage", "ads.srs")
        _write_file(stage, _FAKE_SRS_A)
        ruleset_source.approve_source("ads-blockset", source_type="local",
                                      path=stage, data_dir=self.dir)
        ruleset_cache.update_tag("ads-blockset", data_dir=self.dir)
        state = ruleset_source.resolve_ruleset_state(
            ruleset_source.load_sources(
                ruleset_source.sources_path(self.dir)),
            ruleset_cache.cache_status(self.dir))
        fragment = self.wired(make_usage(ad_block=True), (),
                              ruleset_state=state)
        entries = fragment["route"]["rule_set"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["type"], "local")
        self.assertEqual(entry["format"], "binary")
        self.assertEqual(
            entry["path"],
            str(ruleset_cache.tag_cache_path("ads-blockset", self.dir)))
        self.assertNotIn("prototype_placeholder", entry)
        self.assertIn({"rule_set": ["ads-blockset"], "action": "reject"},
                      fragment["route"]["rules"])

    # ---- 组合 4：开关开 + remote 来源核定 → remote 条目（§13-E 形状） ----
    def test_combo_on_with_remote_source(self):
        ruleset_source.approve_source(
            "malware-blockset", source_type="remote", url=_FAKE_REMOTE_URL,
            update_interval="12h", data_dir=self.dir)
        state = ruleset_source.resolve_ruleset_state(
            ruleset_source.load_sources(
                ruleset_source.sources_path(self.dir)),
            ruleset_cache.cache_status(self.dir))
        fragment = self.wired(make_usage(malware_block=True), (),
                              ruleset_state=state)
        entries = fragment["route"]["rule_set"]
        self.assertEqual(len(entries), 1)
        entry = entries[0]
        self.assertEqual(entry["type"], "remote")
        self.assertEqual(entry["format"], "binary")
        self.assertEqual(entry["url"], _FAKE_REMOTE_URL)
        self.assertEqual(entry["download_detour"],
                         usage_model.OUT_TAG)
        self.assertEqual(entry["update_interval"], "12h")
        self.assertNotIn("prototype_placeholder", entry)
        self.assertIn({"rule_set": ["malware-blockset"], "action": "reject"},
                      fragment["route"]["rules"])

    # ---- ⑤ cn-directset（rule 模式）与出站补丁共存 ----
    def test_combo_rule_mode_cn_direct_with_cache(self):
        stage = os.path.join(self.dir, "stage", "cn.srs")
        _write_file(stage, _FAKE_SRS_A)
        ruleset_source.approve_source("cn-directset", source_type="local",
                                      path=stage, data_dir=self.dir)
        ruleset_cache.update_tag("cn-directset", data_dir=self.dir)
        state = ruleset_source.resolve_ruleset_state(
            ruleset_source.load_sources(
                ruleset_source.sources_path(self.dir)),
            ruleset_cache.cache_status(self.dir))
        fragment = self.wired(make_usage(split_mode="rule"), (),
                              ruleset_state=state)
        entries = {e["tag"]: e for e in fragment["route"]["rule_set"]}
        self.assertIn("cn-directset", entries)
        self.assertEqual(entries["cn-directset"]["type"], "local")
        self.assertNotIn("prototype_placeholder", entries["cn-directset"])
        self.assertIn({"rule_set": ["cn-directset"], "action": "route",
                       "outbound": "direct-out"},
                      fragment["route"]["rules"])
        self.assertEqual(fragment["outbound_patch"],
                         [{"type": "direct", "tag": "direct-out"}])
        self.assertIsNone(fragment["route"]["final_override"])

    def test_rule_mode_cn_direct_without_source_is_uncovered(self):
        state = ruleset_source.resolve_ruleset_state(
            ruleset_source.empty_sources(),
            ruleset_cache.cache_status(self.dir))
        fragment = self.wired(make_usage(split_mode="rule"), (),
                              ruleset_state=state)
        self.assertEqual(fragment["route"]["rule_set"], [])
        features = [item["feature"] for item in fragment["uncovered"]]
        self.assertTrue(any("cn-directset" in f for f in features))
        # 出站补丁与 final 与规则集状态无关（⑤续语义原样）
        self.assertEqual(fragment["outbound_patch"],
                         [{"type": "direct", "tag": "direct-out"}])
        self.assertIsNone(fragment["route"]["final_override"])

    # ---- 局部核定：一个 tag 有缓存、另一个空槽 → 混合形态 ----
    def test_mixed_one_cached_one_unapproved(self):
        stage = os.path.join(self.dir, "stage", "ads2.srs")
        _write_file(stage, _FAKE_SRS_A)
        ruleset_source.approve_source("ads-blockset", source_type="local",
                                      path=stage, data_dir=self.dir)
        ruleset_cache.update_tag("ads-blockset", data_dir=self.dir)
        state = ruleset_source.resolve_ruleset_state(
            ruleset_source.load_sources(
                ruleset_source.sources_path(self.dir)),
            ruleset_cache.cache_status(self.dir))
        fragment = self.wired(make_usage(ad_block=True, malware_block=True),
                              (), ruleset_state=state)
        entries = fragment["route"]["rule_set"]
        self.assertEqual([e["tag"] for e in entries], ["ads-blockset"])
        features = [item["feature"] for item in fragment["uncovered"]]
        self.assertTrue(any("malware-blockset" in f for f in features))


# ---------------------------------------------------------------------------
# 组 8：合并兼容组（U2 usage_merge 零改动；§4.5 顺序契约相容性实证）
# ---------------------------------------------------------------------------
@unittest.skipUnless(_HAS_DEV, "client-dev 不在场，合并兼容组跳过")
class TestMergeCompatibility(unittest.TestCase):
    """wired 片段过 U2 的 merge_usage_config：占位路径照旧滤除（方案 A
    零改动）、真条目路径照旧并入（先拦截后解析，插在 resolve 之前）。"""

    @classmethod
    def setUpClass(cls):
        # 集成形态：build_route_config 已是接线后真身，直调（DESIGN §10
        # 集成适配：内存补丁段删除）。
        cls.wired = staticmethod(usage_model.build_route_config)

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="u3-merge-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)

    def _engine_config(self):
        node = Node(id="t-vless", name="vless 测试节点", protocol="vless",
                    source="manual", host=_FAKE_V4, port=443,
                    extra={"uuid": "00000000-0000-4000-8000-000000000000"})
        engine = SingBoxEngine(takeover_mode="proxy")
        return engine.build_config(node)

    def test_placeholder_fragment_still_filtered(self):
        """state=None（占位口径）→ merge 照旧滤除占位条目与其规则
        （U2 方案 A 行为零变化，usage_merge 零改动红线）。"""
        fragment = self.wired(make_usage(ad_block=True, malware_block=True),
                              (), ruleset_state=None)
        notes = []
        merged = usage_merge.merge_usage_config(self._engine_config(),
                                                fragment, notes)
        self.assertNotIn("rule_set", merged["route"])
        for rule in merged["route"]["rules"]:
            self.assertNotIn("ads-blockset", rule.get("rule_set", []))
            self.assertNotIn("malware-blockset", rule.get("rule_set", []))
        self.assertTrue(any("占位规则集被拦" in n for n in notes))

    def test_real_local_entry_merged_before_resolve(self):
        stage = os.path.join(self.dir, "stage", "ads.srs")
        _write_file(stage, _FAKE_SRS_A)
        ruleset_source.approve_source("ads-blockset", source_type="local",
                                      path=stage, data_dir=self.dir)
        ruleset_cache.update_tag("ads-blockset", data_dir=self.dir)
        state = ruleset_source.resolve_ruleset_state(
            ruleset_source.load_sources(
                ruleset_source.sources_path(self.dir)),
            ruleset_cache.cache_status(self.dir))
        fragment = self.wired(make_usage(ad_block=True), (),
                              ruleset_state=state)
        merged = usage_merge.merge_usage_config(self._engine_config(), fragment)
        rule_set = merged["route"]["rule_set"]
        self.assertEqual(len(rule_set), 1)
        self.assertEqual(rule_set[0]["type"], "local")
        self.assertNotIn("prototype_placeholder", rule_set[0])
        # §4.5-2：先拦截后解析——usage reject 规则插在 resolve 之前
        rules = merged["route"]["rules"]
        resolve_idx = next(i for i, r in enumerate(rules)
                           if r.get("action") == "resolve")
        reject_idx = next(i for i, r in enumerate(rules)
                          if r.get("rule_set") == ["ads-blockset"])
        self.assertLess(reject_idx, resolve_idx)
        self.assertEqual(rules[reject_idx]["action"], "reject")

    def test_uncovered_fragment_merges_to_no_extra_rules(self):
        state = ruleset_source.resolve_ruleset_state(
            ruleset_source.empty_sources(),
            ruleset_cache.cache_status(self.dir))
        fragment = self.wired(make_usage(ad_block=True), (),
                              ruleset_state=state)
        baseline = usage_model.build_route_config(make_usage(), ())
        merged = usage_merge.merge_usage_config(self._engine_config(), fragment)
        baseline_merged = usage_merge.merge_usage_config(
            self._engine_config(), baseline)
        # 开关开但无缓存无来源 → live 配置与「开关关」深等价（无操作）
        self.assertEqual(merged["route"]["rules"],
                         baseline_merged["route"]["rules"])
        self.assertEqual(merged["route"].get("rule_set"),
                         baseline_merged["route"].get("rule_set"))


# ---------------------------------------------------------------------------
# 组 9：sing-box check 组（二进制不在场 skip 并记 DONE.md）
# ---------------------------------------------------------------------------
def _find_singbox_binary():
    """构造链同引擎口径：环境变量 → which → ~/.local/bin/sing-box。"""
    env = os.environ.get("GATE_CLIENT_SINGBOX_BIN")
    if env and os.path.isfile(env):
        return env
    home = os.path.expanduser("~/.local/bin/sing-box")
    if os.path.isfile(home):
        return home
    return shutil.which("sing-box")


def _compile_srs(binary):
    """内核自编译 .srs 夹具（离线：source JSON → rule-set compile）。
    返回 (srs 字节, 说明) 或 (None, 原因)。source JSON 用 RFC 2606 保留域。"""
    workdir = tempfile.mkdtemp(prefix="u3-srs-")
    source_path = os.path.join(workdir, "src.json")
    out_path = os.path.join(workdir, "out.srs")
    source = {"version": 1,
              "rules": [{"domain_suffix": [_FAKE_DOMAIN,
                                           "cn.example.invalid"]}]}
    with open(source_path, "w", encoding="utf-8") as f:
        json.dump(source, f)
    proc = subprocess.run(
        [binary, "rule-set", "compile", "--output", out_path, source_path],
        capture_output=True, timeout=60)
    if proc.returncode != 0 or not os.path.isfile(out_path):
        shutil.rmtree(workdir, ignore_errors=True)
        return None, (f"rule-set compile 失败 rc={proc.returncode} "
                      f"{(proc.stdout + proc.stderr)[:200]!r}")
    with open(out_path, "rb") as f:
        data = f.read()
    shutil.rmtree(workdir, ignore_errors=True)
    return data, "ok"


@unittest.skipUnless(_HAS_DEV and _find_singbox_binary(),
                     "sing-box 二进制不在场（沿用既有引擎测试口径："
                     "内核实测复核归审查方 VM，原因记 DONE.md）")
class TestSingBoxCheck(unittest.TestCase):
    """接线后配置过 sing-box check：默认片段 / local 真条目 / remote 形状。
    本机无二进制时整组 skip（work17 先例）；内核 1.14.4 实测由审查方 VM
    复跑，结果回填 DONE.md。"""

    @classmethod
    def setUpClass(cls):
        cls.binary = _find_singbox_binary()
        # 集成形态：build_route_config 已是接线后真身，直调（DESIGN §10
        # 集成适配：内存补丁段删除）。
        cls.wired = staticmethod(usage_model.build_route_config)
        cls.dir = tempfile.mkdtemp(prefix="u3-check-")
        data, why = _compile_srs(cls.binary)
        cls.srs = data
        cls.srs_why = why

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _check(self, config):
        cfg_path = os.path.join(self.dir, "check-config.json")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False)
        proc = subprocess.run([self.binary, "check", "-c", cfg_path],
                              capture_output=True, timeout=60)
        self.assertEqual(
            proc.returncode, 0,
            f"sing-box check 未通过: {(proc.stdout + proc.stderr)[:400]}")

    def _engine_config(self):
        node = Node(id="t-vless", name="vless 测试节点", protocol="vless",
                    source="manual", host=_FAKE_V4, port=443,
                    extra={"uuid": "00000000-0000-4000-8000-000000000000"})
        return SingBoxEngine(takeover_mode="proxy").build_config(node)

    def _local_state(self):
        stage = os.path.join(self.dir, "stage", "ads.srs")
        _write_file(stage, self.srs)
        ruleset_source.approve_source("ads-blockset", source_type="local",
                                      path=stage, data_dir=self.dir)
        ruleset_cache.update_tag("ads-blockset", data_dir=self.dir)
        return ruleset_source.resolve_ruleset_state(
            ruleset_source.load_sources(
                ruleset_source.sources_path(self.dir)),
            ruleset_cache.cache_status(self.dir))

    def test_default_fragment_passes_check(self):
        self._check(usage_merge.merge_usage_config(
            self._engine_config(),
            self.wired(make_usage(), (), ruleset_state=None)))

    def test_wired_local_entry_passes_check(self):
        """local 真条目（缓存文件 = 内核自编译 .srs）过 check。
        compile 不可用时 skip（内核变体差异，如实记录不硬造夹具）。"""
        if self.srs is None:
            self.skipTest(f"内核 rule-set compile 不可用：{self.srs_why}")
        fragment = self.wired(make_usage(ad_block=True), (),
                              ruleset_state=self._local_state())
        self._check(usage_merge.merge_usage_config(
            self._engine_config(), fragment))

    def test_wired_remote_entry_shape_passes_check(self):
        """remote 条目字段形状过 check（§13-E 文档口径的内核实证）。
        URL 用 loopback:9（形态合法、零外联；若内核 check 实际发起取材
        则本用例失败——如实记录内核行为并回填 DESIGN.md）。"""
        ruleset_source.approve_source(
            "ads-blockset", source_type="remote", url=_FAKE_REMOTE_URL,
            data_dir=self.dir)
        state = ruleset_source.resolve_ruleset_state(
            ruleset_source.load_sources(
                ruleset_source.sources_path(self.dir)),
            ruleset_cache.cache_status(self.dir))
        fragment = self.wired(make_usage(ad_block=True), (),
                              ruleset_state=state)
        self._check(usage_merge.merge_usage_config(
            self._engine_config(), fragment))


# ---------------------------------------------------------------------------
# 组 10：③区 offscreen 组（PySide6 缺席整组 skip 并记 DONE.md）
# ---------------------------------------------------------------------------
# （集成形态：③区内存接线补丁已删，直调真 UsageRulesPanel，见 §10。）


@unittest.skipUnless(_HAS_DEV and _QT,
                     "PySide6 或 client-dev 不在场，③区 offscreen 组跳过")
class TestUsageRulesPanelUpdateButton(unittest.TestCase):
    """③区「立即更新」按钮 offscreen 实跑：按钮 → 缓存落盘 → 状态行逐 tag
    （mtime 即 last_updated，数据来自缓存状态报告）。"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        from PySide6.QtWidgets import QApplication
        from client.ui.usage_rules_page import UsageRulesPanel
        cls.panel_cls = UsageRulesPanel
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="u3-ui-")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.settings_path = os.path.join(self.dir, "settings.json")
        self.panel = self.panel_cls(settings_path=self.settings_path)

    def test_button_and_status_present(self):
        self.assertTrue(self.panel.update_btn.text(), "立即更新")
        self.assertIn("ads-blockset", self.panel.ruleset_status.text())
        self.assertIn("无缓存", self.panel.ruleset_status.text())

    def test_update_button_downloads_local_source(self):
        stage = os.path.join(self.dir, "stage", "ads.srs")
        _write_file(stage, _FAKE_SRS_A)
        ruleset_source.approve_source("ads-blockset", source_type="local",
                                      path=stage, data_dir=self.dir)
        self.panel._on_update_now()
        cache_file = ruleset_cache.tag_cache_path("ads-blockset", self.dir)
        self.assertTrue(os.path.isfile(cache_file))
        with open(cache_file, "rb") as f:
            self.assertEqual(f.read(), _FAKE_SRS_A)
        # 状态行展示逐 tag last_updated（mtime 格式化）
        self.assertIn("ads-blockset", self.panel.ruleset_status.text())
        self.assertIn("更新于", self.panel.ruleset_status.text())
        self.assertIn("malware-blockset", self.panel.ruleset_status.text())

    def test_update_reports_kernel_managed_for_remote(self):
        ruleset_source.approve_source(
            "malware-blockset", source_type="remote", url=_FAKE_REMOTE_URL,
            data_dir=self.dir)
        self.panel._on_update_now()
        self.assertIn("内核", self.panel.ruleset_status.text())
        self.assertFalse(
            os.path.exists(
                ruleset_cache.tag_cache_path("malware-blockset", self.dir)))


if __name__ == "__main__":
    unittest.main()
