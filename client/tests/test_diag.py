#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""diag.py 离线测试（task7）。

重点（任务书红线）：构造含假 password/uuid/订阅地址的材料，走完整管线
（结构化脱敏 → 文本清扫 → build_bundle 写 zip），解开后断言**任何文件**
都不含这些原文。其余覆盖：掩码基元逐例、run_checks 全过/单项失败/
多项失败/缺 env、build_bundle 清单与文件名校验。

全部假数据：主机用 example.com 系与 RFC 5737 文档段（203.0.113.x /
198.51.100.x），凭证全为虚构字符串。零联网、零真实节点。

被测模块 client.core.diag 只用标准库。
跑法：cd client-dev && python3 -m unittest client.tests.test_diag
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core import diag  # noqa: E402

# ---------------------------------------------------------------------------
# 全套假数据（红线用例的共用 fixture）
# ---------------------------------------------------------------------------
FAKE_PASSWORD = "hunter2-master-pw"
FAKE_UUID = "b831381d-6324-4d53-ad4f-8cda48b30811"
FAKE_USERNAME = "user001"
FAKE_TOKEN = "tok_abc123def456"
FAKE_SUB_URL = "https://user:pw@sub.example.com:8443/api/v1/sub?token=abc"
FAKE_HOST = "vpn123456.opengw.net"
FAKE_SERVER_IP = "203.0.113.45"
FAKE_EXIT_IP = "198.51.100.7"

# 清扫用的 secret 列表（DESIGN §3.5 口径：凭证值 + host 原文 + 地址原文 + 出口 IP）
SECRETS = [
    FAKE_PASSWORD, FAKE_UUID, FAKE_USERNAME, FAKE_TOKEN,
    FAKE_SUB_URL, "sub.example.com", FAKE_HOST,
    FAKE_SERVER_IP, FAKE_EXIT_IP,
]

FAKE_NODE = {
    "id": f"manual:vless:{FAKE_SERVER_IP}:443:ab12cd34",
    "name": f"日本 · {FAKE_EXIT_IP} · NTT",
    "protocol": "vless",
    "source": "manual",
    "host": FAKE_HOST,
    "port": 443,
    "config_ref": f"vless://{FAKE_UUID}@{FAKE_HOST}:443?flow=xtls-rprx-vision",
    "exit_profile": {
        "ip": FAKE_EXIT_IP, "country": "日本", "country_code": "JP",
        "network_type": "datacenter", "org": "NTT", "asn": "AS2497",
        "is_clean": True, "dirty": [], "verified_at": 1730000000.0,
    },
    "scenes": ["ai"],
    "metrics": {"latency_ms": 42.0, "jitter_ms": 3.0, "uptime_hours": 10.0,
                "sessions": 2, "speed_mbps": 5.0},
    "pinned": True,
    "banned": False,
    "extra": {
        "uuid": FAKE_UUID,
        "password": FAKE_PASSWORD,
        "username": FAKE_USERNAME,
        "token": FAKE_TOKEN,
        "query": "flow=xtls-rprx-vision&type=ws&path=%2Fws",
        "clash": {"password": FAKE_PASSWORD, "ws-opts": {"path": "/ws"}},
        "subscription_url": FAKE_SUB_URL,
        "subscription_name": "机场A",
        "method": "aes-256-gcm",
        "flow": "xtls-rprx-vision",
        "alpn": "h2,http/1.1",
        "sni": FAKE_HOST,
        "server": FAKE_HOST,
        "quarantined": "0",
        "quarantine_reason": "",
        "registry_entry": "sub-1a2b3c4d",
        "orig_name": f"vless://{FAKE_UUID}@{FAKE_HOST}:443",
        "mystery_future_field": "whatever-a-future-protocol-adds",
    },
}

CHECK_NAMES = ["database", "singbox_binary", "openvpn_binary", "engine_ports",
               "system_proxy", "connection_alive", "tun_takeover",
               "verify_coverage", "source_updates"]

ALL_OK_ENV = {
    "db_ok": True, "db_node_count": 25, "db_sample_count": 120,
    "singbox_found": True, "singbox_version": "sing-box version 1.12.0",
    "openvpn_needed": True, "openvpn_found": True,
    "ports_occupied": [],
    "proxy_conflict": {"conflict": False, "reason": "系统代理未开启（mode=none）"},
    "connection": {"status": "ok", "fail_streak": 0, "sample_count": 12,
                   "last_ok": True},
    "takeover_mode": "proxy", "tun_takeover": None,
    "verify_stats": {"total_nodes": 25, "verified_nodes": 12,
                     "recent_success_24h": 3},
    "registry_failures": [], "registry_count": 2,
}


def build_pipeline_files() -> dict:
    """模拟集成组装器：结构化脱敏 → 组文件 → 文本清扫（DESIGN §2 流程）。"""
    redacted = diag.redact_node_fields(FAKE_NODE)
    entry_text = (
        "来源登记摘要（地址只留掩码域名）\n"
        f"来源 sub-1a2b3c4d：类型 subscription（manual 页），名称 机场A，启用\n"
        f"登记地址: {diag.mask_url(FAKE_SUB_URL)}\n"
        "最近更新: 失败 HTTPError: 403（新增 0 / 更新 0 / 消失 0）\n")
    event_text = (
        "2026-11-21T09:00:00Z connect "
        f"{diag.mask_node_id(f'manual:vless:{FAKE_SERVER_IP}:443:ab12cd34')} "
        "出口 JP\n"
        "2026-11-21T09:05:00Z disconnected 用户断开\n")
    files = {
        "00-version.txt": "gate-client 0.10.0\ndiag 0.1.0\n",
        "02-checks.txt": diag.render_checks(diag.run_checks(ALL_OK_ENV)),
        "10-nodes-summary.txt": (
            "脱敏节点清单（每行一条 JSON）\n"
            + json.dumps(redacted, ensure_ascii=False) + "\n"),
        "12-sources.txt": entry_text,
        "30-events-recent.txt": event_text,
    }
    return diag.sweep_files(files, SECRETS)


def read_zip(path: str) -> dict:
    with zipfile.ZipFile(path) as zf:
        return {name: zf.read(name).decode("utf-8")
                for name in zf.namelist()}


# ---------------------------------------------------------------------------
# 掩码基元（DESIGN §3.1 逐例锁定）
# ---------------------------------------------------------------------------
class TestMaskHost(unittest.TestCase):
    def test_design_examples(self):
        self.assertEqual(diag.mask_host("vpn123456.opengw.net"),
                         "vpn123456.***.net")
        self.assertEqual(diag.mask_host("203.0.113.45"), "203.***.45")
        self.assertEqual(diag.mask_host("opengw.net"), "opengw.***.net")

    def test_single_label_fully_masked(self):
        self.assertEqual(diag.mask_host("localhost"), "***")

    def test_ipv6_fully_masked(self):
        self.assertEqual(diag.mask_host("2001:db8::1"), "***")
        self.assertEqual(diag.mask_host("::1"), "***")

    def test_empty(self):
        self.assertEqual(diag.mask_host(""), "")
        self.assertEqual(diag.mask_host(None), "")

    def test_strict_option_masks_everything(self):
        self.assertEqual(diag.mask_host("vpn123456.opengw.net", strict=True), "***")

    def test_masked_form_keeps_no_middle_segments(self):
        masked = diag.mask_host("a.b.c.d.example.org")
        self.assertEqual(masked, "a.***.org")


class TestMaskUrl(unittest.TestCase):
    def test_design_example_strips_everything_but_masked_domain(self):
        self.assertEqual(diag.mask_url(FAKE_SUB_URL), "sub.***.com")

    def test_no_scheme_path_query_port_userinfo_leak(self):
        masked = diag.mask_url(FAKE_SUB_URL)
        for leak in ("https", "8443", "token", "user", "/api", "example.com"):
            self.assertNotIn(leak, masked)

    def test_simple_url(self):
        self.assertEqual(diag.mask_url("https://feed.example.org/data.csv"),
                         "feed.***.org")

    def test_garbage_and_empty(self):
        self.assertEqual(diag.mask_url("不是URL"), "***")
        self.assertEqual(diag.mask_url(""), "***")
        self.assertEqual(diag.mask_url(None), "***")

    def test_scheme_less_bare_string_fully_masked(self):
        # 无 scheme 的裸串解析不出 hostname → 整体 ***（DESIGN §3.1 口径；
        # 登记制本来就只收 http/https，裸串只会来自脏数据，宁多勿漏）
        self.assertEqual(diag.mask_url("example.com:8443"), "***")


# ---------------------------------------------------------------------------
# 文本清扫（DESIGN §3.3）
# ---------------------------------------------------------------------------
class TestRedactText(unittest.TestCase):
    def test_empty_secrets_returns_text_asis(self):
        text = "包含 hunter2-master-pw 的原文"
        self.assertIs(diag.redact_text(text, []), text)
        self.assertIs(diag.redact_text(text, None), text)
        self.assertEqual(diag.redact_text(text, ()), text)

    def test_single_secret_all_occurrences(self):
        out = diag.redact_text("密码 hunter2-master-pw 写了两遍 hunter2-master-pw。",
                               [FAKE_PASSWORD])
        self.assertEqual(out, "密码 *** 写了两遍 ***。")

    def test_long_secret_before_short_prefix(self):
        # 先短后长会把 password123 拆成 ***word123 半截泄露；实现按长度降序
        out = diag.redact_text("password123 and pass", ["pass", "password123"])
        self.assertEqual(out, "*** and ***")
        self.assertNotIn("word123", out)

    def test_case_insensitive(self):
        out = diag.redact_text("token 是 TOK_ABC123DEF456。", [FAKE_TOKEN])
        self.assertNotIn("TOK_", out)
        self.assertIn("***", out)

    def test_short_secrets_skipped(self):
        # <4 的 secret 跳过（DESIGN §3.3 唯一例外），文本不被打花
        self.assertEqual(diag.redact_text("ab in ab", ["ab"]), "ab in ab")
        self.assertEqual(diag.redact_text("abc in abc", ["abc"]), "abc in abc")

    def test_non_string_secret_coerced(self):
        self.assertEqual(diag.redact_text("端口 8388 被占", [8388]), "端口 *** 被占")

    def test_regex_special_chars_literal(self):
        out = diag.redact_text("密码 a.b*c( 有效", ["a.b*c("])
        self.assertEqual(out, "密码 *** 有效")

    def test_non_string_text_passthrough(self):
        self.assertEqual(diag.redact_text(42, [FAKE_PASSWORD]), 42)
        self.assertIsNone(diag.redact_text(None, [FAKE_PASSWORD]))

    def test_string_as_single_secret_not_char_iterated(self):
        self.assertEqual(diag.redact_text("abc", "abcdef"), "abc")
        self.assertEqual(diag.redact_text("abcdef", "abcdef"), "***")

    def test_sweep_files(self):
        files = {"a.txt": "pw=hunter2-master-pw", "b.txt": "干净"}
        out = diag.sweep_files(files, [FAKE_PASSWORD])
        self.assertEqual(out["a.txt"], "pw=***")
        self.assertEqual(out["b.txt"], "干净")
        self.assertEqual(files["a.txt"], "pw=hunter2-master-pw")  # 不改输入


# ---------------------------------------------------------------------------
# 节点行逐字段脱敏（DESIGN §3.2 逐字段锁定）
# ---------------------------------------------------------------------------
class TestRedactNodeFields(unittest.TestCase):
    def setUp(self):
        self.snapshot = copy.deepcopy(FAKE_NODE)
        self.out = diag.redact_node_fields(FAKE_NODE)

    def tearDown(self):
        self.assertEqual(FAKE_NODE, self.snapshot, "输入 record 被修改了")

    def test_returns_copy_not_same_object(self):
        self.assertIsNot(self.out, FAKE_NODE)
        self.assertIsNot(self.out["extra"], FAKE_NODE["extra"])

    def test_credential_fields_removed(self):
        extra = self.out["extra"]
        for key in ("uuid", "password", "username", "token", "query", "clash",
                    "orig_name"):
            self.assertNotIn(key, extra)
        self.assertNotIn("config_ref", self.out)

    def test_redacted_keys_recorded(self):
        dropped = self.out["redacted_keys"]
        self.assertIn("config_ref", dropped)
        for key in ("uuid", "password", "username", "token", "query", "clash",
                    "orig_name", "mystery_future_field"):
            self.assertIn(f"extra.{key}", dropped)
        self.assertEqual(dropped, sorted(dropped))

    def test_host_masked_keeping_head_tail(self):
        self.assertEqual(self.out["host"], "vpn123456.***.net")
        self.assertEqual(self.out["extra"]["server"], "vpn123456.***.net")
        self.assertEqual(self.out["extra"]["sni"], "vpn123456.***.net")

    def test_subscription_url_masked_to_domain_only(self):
        self.assertEqual(self.out["extra"]["subscription_url"], "sub.***.com")
        self.assertNotIn("token", self.out["extra"]["subscription_url"])

    def test_subscription_name_and_registry_stamp_kept(self):
        extra = self.out["extra"]
        self.assertEqual(extra["subscription_name"], "机场A")
        self.assertEqual(extra["registry_entry"], "sub-1a2b3c4d")
        self.assertEqual(extra["quarantined"], "0")
        self.assertEqual(extra["quarantine_reason"], "")

    def test_protocol_params_kept(self):
        extra = self.out["extra"]
        self.assertEqual(extra["method"], "aes-256-gcm")
        self.assertEqual(extra["flow"], "xtls-rprx-vision")
        self.assertEqual(extra["alpn"], "h2,http/1.1")

    def test_node_id_host_segment_masked_digest_kept(self):
        self.assertEqual(self.out["id"], "manual:vless:203.***.45:443:ab12cd34")

    def test_node_id_ipv6_over_masked(self):
        masked = diag.mask_node_id("openvpn:openvpn:2001:db8::1:1194")
        self.assertEqual(masked, "openvpn:openvpn:***:1194")
        self.assertNotIn("2001", masked)
        self.assertNotIn("db8", masked)

    def test_node_id_without_digest(self):
        masked = diag.mask_node_id("pipeline:ss:example.com:8388")
        self.assertEqual(masked, "pipeline:ss:example.***.com:8388")

    def test_exit_ip_masked_country_kept(self):
        profile = self.out["exit_profile"]
        self.assertEqual(profile["ip"], "198.***.7")
        self.assertEqual(profile["country_code"], "JP")
        self.assertEqual(profile["org"], "NTT")
        self.assertEqual(profile["network_type"], "datacenter")

    def test_name_exit_ip_masked(self):
        self.assertEqual(self.out["name"], "日本 · 198.***.7 · NTT")

    def test_name_untouched_without_exit_ip(self):
        node = dict(FAKE_NODE, name="原名没改过", exit_profile={"ip": None})
        out = diag.redact_node_fields(node)
        self.assertEqual(out["name"], "原名没改过")

    def test_port_metrics_flags_kept(self):
        self.assertEqual(self.out["port"], 443)
        self.assertEqual(self.out["metrics"]["latency_ms"], 42.0)
        self.assertTrue(self.out["pinned"])
        self.assertFalse(self.out["banned"])
        self.assertEqual(self.out["scenes"], ["ai"])

    def test_no_secret_value_survives_in_copy(self):
        text = json.dumps(self.out, ensure_ascii=False)
        for secret in (FAKE_PASSWORD, FAKE_UUID, FAKE_USERNAME, FAKE_TOKEN,
                       FAKE_SUB_URL, FAKE_HOST, FAKE_SERVER_IP, FAKE_EXIT_IP):
            self.assertNotIn(secret, text)

    def test_non_dict_rejected(self):
        with self.assertRaises(TypeError):
            diag.redact_node_fields("not a dict")


# ---------------------------------------------------------------------------
# 一键自检（DESIGN §4.1）
# ---------------------------------------------------------------------------
class TestRunChecks(unittest.TestCase):
    def test_all_pass(self):
        results = diag.run_checks(ALL_OK_ENV)
        self.assertEqual([r.name for r in results], CHECK_NAMES)
        self.assertTrue(all(r.ok for r in results), [r.detail for r in results])
        self.assertEqual(len(results), 9)

    def test_single_failure_ports_occupied(self):
        env = dict(ALL_OK_ENV,
                   ports_occupied=[{"port": 7897, "pid": 4242, "process": "karing"}])
        results = diag.run_checks(env)
        failed = [r for r in results if not r.ok]
        self.assertEqual([r.name for r in failed], ["engine_ports"])
        self.assertIn("7897", failed[0].detail)
        self.assertIn("karing", failed[0].detail)
        self.assertIn("关掉", failed[0].detail)

    def test_process_name_unknown_still_readably_described(self):
        env = dict(ALL_OK_ENV,
                   ports_occupied=[{"port": 7898, "pid": None, "process": None}])
        results = diag.run_checks(env)
        detail = [r for r in results if r.name == "engine_ports"][0].detail
        self.assertIn("7898", detail)
        self.assertIn("进程名查不到", detail)

    def test_multiple_failures(self):
        env = dict(ALL_OK_ENV,
                   db_ok=False, db_error="database disk image is malformed",
                   ports_occupied=[{"port": 7897, "pid": 1, "process": "clash"}],
                   proxy_conflict={"conflict": True,
                                   "reason": "系统代理为 manual 模式，指向 127.0.0.1:7899"},
                   connection={"status": "dead", "fail_streak": 6,
                               "sample_count": 10, "last_ok": False},
                   verify_stats={"total_nodes": 30, "verified_nodes": 0},
                   registry_failures=["HTTPError: HTTP Error 403"])
        results = diag.run_checks(env)
        failed = {r.name: r for r in results if not r.ok}
        self.assertEqual(set(failed), {"database", "engine_ports", "system_proxy",
                                       "connection_alive", "verify_coverage",
                                       "source_updates"})
        self.assertIn("malformed", failed["database"].detail)
        self.assertIn("7897", failed["engine_ports"].detail)
        self.assertIn("manual", failed["system_proxy"].detail)
        self.assertIn("6", failed["connection_alive"].detail)
        self.assertIn("自动切换", failed["connection_alive"].detail)
        self.assertIn("30", failed["verify_coverage"].detail)
        self.assertIn("403", failed["source_updates"].detail)

    def test_missing_env_all_ok_with_skip_notes(self):
        results = diag.run_checks({})
        self.assertEqual([r.name for r in results], CHECK_NAMES)
        self.assertTrue(all(r.ok for r in results))
        for r in results:
            self.assertTrue(r.detail.strip())

    def test_object_env_supported(self):
        from types import SimpleNamespace
        obj = SimpleNamespace(**ALL_OK_ENV)
        results = diag.run_checks(obj)
        self.assertTrue(all(r.ok for r in results))

    def test_tun_takeover_failed_only_in_tun_mode(self):
        env = dict(ALL_OK_ENV, takeover_mode="tun",
                   tun_takeover={"effective": False,
                                 "reason": "TUN 网卡已建立，但默认路由未接管到 gate-tun"})
        results = diag.run_checks(env)
        tun = [r for r in results if r.name == "tun_takeover"][0]
        self.assertFalse(tun.ok)
        self.assertIn("gate-tun", tun.detail)

    def test_tun_takeover_effective(self):
        env = dict(ALL_OK_ENV, takeover_mode="tun",
                   tun_takeover={"effective": True, "reason": "默认路由已接管到 gate-tun"})
        results = diag.run_checks(env)
        tun = [r for r in results if r.name == "tun_takeover"][0]
        self.assertTrue(tun.ok)

    def test_tun_skipped_in_proxy_mode(self):
        results = diag.run_checks(ALL_OK_ENV)  # takeover_mode=proxy
        tun = [r for r in results if r.name == "tun_takeover"][0]
        self.assertTrue(tun.ok)
        self.assertIn("非 TUN", tun.detail)

    def test_connection_states(self):
        for status, ok, marker in (
                ("ok", True, "出口探测正常"),
                ("unknown", True, "还没有探测样本"),
                ("suspect", False, "连续失败"),
                ("dead", False, "自动切换")):
            env = dict(ALL_OK_ENV,
                       connection={"status": status, "fail_streak": 3,
                                   "sample_count": 5, "last_ok": False})
            r = [x for x in diag.run_checks(env) if x.name == "connection_alive"][0]
            self.assertEqual(r.ok, ok, status)
            self.assertIn(marker, r.detail, status)

    def test_no_active_connection_is_ok(self):
        env = dict(ALL_OK_ENV, connection=None)
        r = [x for x in diag.run_checks(env) if x.name == "connection_alive"][0]
        self.assertTrue(r.ok)
        self.assertIn("没有活动连接", r.detail)

    def test_verify_coverage_empty_db(self):
        env = dict(ALL_OK_ENV, verify_stats={"total_nodes": 0, "verified_nodes": 0})
        r = [x for x in diag.run_checks(env) if x.name == "verify_coverage"][0]
        self.assertTrue(r.ok)
        self.assertIn("节点库为空", r.detail)

    def test_singbox_missing(self):
        env = dict(ALL_OK_ENV, singbox_found=False, singbox_version="")
        r = [x for x in diag.run_checks(env) if x.name == "singbox_binary"][0]
        self.assertFalse(r.ok)
        self.assertIn("GATE_CLIENT_SINGBOX_BIN", r.detail)

    def test_openvpn_not_needed_skipped(self):
        env = dict(ALL_OK_ENV, openvpn_needed=False, openvpn_found=False)
        r = [x for x in diag.run_checks(env) if x.name == "openvpn_binary"][0]
        self.assertTrue(r.ok)
        self.assertIn("不要求 openvpn", r.detail)

    def test_checkresult_shape(self):
        r = diag.run_checks(ALL_OK_ENV)[0]
        self.assertEqual(set(r.to_dict()), {"name", "ok", "detail"})

    def test_render_checks_all_pass_summary(self):
        text = diag.render_checks(diag.run_checks(ALL_OK_ENV))
        self.assertIn("[ok] database：", text)
        self.assertIn("总结：9 项检查全部通过", text)

    def test_render_checks_fail_summary(self):
        env = dict(ALL_OK_ENV, ports_occupied=[{"port": 7897, "pid": 1,
                                                "process": "clash"}],
                   db_ok=False)
        text = diag.render_checks(diag.run_checks(env))
        self.assertIn("[fail] engine_ports：", text)
        self.assertIn("总结：2/9 项未通过", text)


# ---------------------------------------------------------------------------
# 导出包（DESIGN §2）+ 脱敏红线自证（§3.6）
# ---------------------------------------------------------------------------
class TestBuildBundle(unittest.TestCase):
    def test_writes_zip_with_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bundle.zip")
            written = diag.build_bundle(
                {"02-checks.txt": "一行\n两行\n三行",
                 "00-version.txt": "gate-client 0.10.0"},
                path, client_version="0.10.0", now=1730000000.0)
            with zipfile.ZipFile(path) as zf:
                members = zf.namelist()
            self.assertEqual(members, ["00-version.txt", "02-checks.txt",
                                       "MANIFEST.txt"])
            self.assertEqual(written, ["00-version.txt", "02-checks.txt",
                                       "MANIFEST.txt"])

    def test_manifest_counts_and_purposes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bundle.zip")
            content = "一行\n两行\n三行"
            diag.build_bundle({"02-checks.txt": content}, path,
                              client_version="0.10.0", now=1730000000.0)
            manifest = read_zip(path)["MANIFEST.txt"]
            self.assertIn("客户端版本: 0.10.0", manifest)
            self.assertIn("UTC", manifest)
            self.assertIn("一键自检报告", manifest)          # 已知文件用途
            self.assertIn("02-checks.txt\t", manifest)
            # 字节数按 UTF-8 实算
            self.assertIn(str(len(content.encode("utf-8"))), manifest)
            self.assertIn("本清单", manifest)

    def test_utf8_chinese_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "bundle.zip")
            diag.build_bundle({"10-nodes-summary.txt": "日本 · 出口 · 运营商\n"},
                              path)
            self.assertEqual(read_zip(path)["10-nodes-summary.txt"],
                             "日本 · 出口 · 运营商\n")

    def test_rejects_path_traversal_names(self):
        for bad in ("sub/dir.txt", "a\\b.txt", "..", ".hidden", ""):
            with self.assertRaises(ValueError):
                diag.build_bundle({bad: "x"}, "/tmp/never-written.zip")

    def test_rejects_pre_supplied_manifest(self):
        with self.assertRaises(ValueError):
            diag.build_bundle({"MANIFEST.txt": "x"}, "/tmp/never-written.zip")


class TestRedLineBundle(unittest.TestCase):
    """红线：导出 zip 解开后任何文件都不含假 secret 原文。"""

    def assert_no_secrets_in_bundle(self, path, extra_forbidden=()):
        members = read_zip(path)
        forbidden = list(SECRETS) + list(extra_forbidden)
        for name, text in members.items():
            lowered = text.lower()
            for secret in forbidden:
                self.assertNotIn(secret.lower(), lowered,
                                 f"{name} 泄漏了 {secret!r}")
        return members

    def test_full_pipeline_bundle_carries_no_secret(self):
        files = build_pipeline_files()
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "diag.zip")
            diag.build_bundle(files, path, secrets=SECRETS,
                              client_version="0.10.0")
            members = self.assert_no_secrets_in_bundle(
                path, extra_forbidden=("example.com", "opengw", "user:pw"))
            # 阳性对照：材料不是被清空，掩码形态确实在包里
            nodes = members["10-nodes-summary.txt"]
            self.assertIn("203.***.45", nodes)
            self.assertIn("198.***.7", nodes)
            self.assertIn("日本 · 198.***.7 · NTT", nodes)
            self.assertIn("sub.***.com", members["12-sources.txt"])
            self.assertIn("manual:vless:203.***.45:443:ab12cd34",
                          members["30-events-recent.txt"])
            self.assertIn("机场A", members["12-sources.txt"])

    def test_case_variant_of_secret_also_caught(self):
        files = {"30-events-recent.txt":
                 f"verify 失败：{FAKE_HOST.upper()} 不可达"}
        swept = diag.sweep_files(files, [FAKE_HOST])
        self.assertNotIn(FAKE_HOST.upper(), swept["30-events-recent.txt"])

    def test_last_line_of_defense_catches_missed_secret(self):
        # 材料故意漏脱一处明文 secret——build_bundle 的 secrets 兜底拦下
        files = {"30-events-recent.txt":
                 f"connect 失败：密码 {FAKE_PASSWORD} 不对"}
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "diag.zip")
            diag.build_bundle(files, path, secrets=SECRETS)
            text = read_zip(path)["30-events-recent.txt"]
            self.assertNotIn(FAKE_PASSWORD, text)
            self.assertIn("***", text)

    def test_build_bundle_without_secrets_writes_files_asis(self):
        # 不传 secrets = 信任调用方已脱敏（任务签名口径），内容逐字写入
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "diag.zip")
            diag.build_bundle({"01-environment.txt": "原样\n"}, path)
            self.assertEqual(read_zip(path)["01-environment.txt"], "原样\n")


class TestDependencyHygiene(unittest.TestCase):
    """红线：diag.py 只依赖标准库（不依赖 PySide6 / client 包）。"""

    def test_source_has_no_forbidden_imports(self):
        # 只查真正的 import 语句（docstring/注释里提到 PySide6 不算依赖）
        with open(diag.__file__, "r", encoding="utf-8") as f:
            source = f.read()
        for bad in ("PySide6", "client", "PyQt"):
            self.assertIsNone(
                re.search(rf"^\s*(?:import|from)\s+{bad}", source, re.M),
                f"diag.py 出现了对 {bad} 的 import")
        self.assertIsNone(re.search(r"^\s*from\s+\.", source, re.M),
                          "diag.py 出现了包内相对 import")


if __name__ == "__main__":
    unittest.main()
