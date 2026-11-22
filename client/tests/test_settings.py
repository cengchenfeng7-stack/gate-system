#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一设置模型离线测试（test_settings.py，task12）
==================================================
跑法：cd client-dev && python3 -m unittest client.tests.test_settings（-v 看明细）。
假数据 only：端口/路径/IP 全用文档保留值（RFC 2606 域名、RFC 5737/4291
TEST-NET/文档前缀），不联网、不读真实设置文件。

口径一致测试组（TestParityWithUsageSettings）：在 client-dev 可导入的
环境（默认 ~/workspace/vpn-project/client-dev，可用环境变量
GATE_CLIENT_DEV 覆盖）下运行——逐字段比对 Settings 与 UsageSettings 的
同名字段默认值、常量与校验行为（红线：冲突以 UsageSettings 为准）；
client-dev 不在时该组跳过，其余照跑。
"""

import dataclasses
import os
import sys
import unittest

from client.core.settings_proto import (
    AUTO_PROBE_INTERVAL_CHOICES,
    DEFAULT_DATA_DIR,
    DEFAULT_HTTP_PORT,
    DEFAULT_SOCKS_PORT,
    DNS_PRESETS,
    Settings,
    SettingsError,
    SPLIT_MODES,
    USAGE_FIELDS,
    merge,
)


def _load_usage_model():
    """加载 client-dev 的 usage_model（只读参考，缺席返回 None）。"""
    root = os.environ.get("GATE_CLIENT_DEV") or os.path.join(
        os.path.expanduser("~"), "workspace", "vpn-project", "client-dev")
    if not os.path.isfile(os.path.join(root, "client", "core", "usage_model.py")):
        return None
    if root not in sys.path:
        sys.path.insert(0, root)
    try:
        from client.core.usage_model import (  # noqa: E402
            DNS_PRESETS as UM_DNS_PRESETS,
            SPLIT_MODES as UM_SPLIT_MODES,
            UsageSettings,
            UsageSettingsError,
        )
    except Exception:
        return None
    return {
        "UsageSettings": UsageSettings,
        "UsageSettingsError": UsageSettingsError,
        "SPLIT_MODES": UM_SPLIT_MODES,
        "DNS_PRESETS": UM_DNS_PRESETS,
    }


_USAGE = _load_usage_model()


# ---------------------------------------------------------------------------
# 默认值 = 现行行为
# ---------------------------------------------------------------------------
class TestDefaults(unittest.TestCase):

    def test_defaults_match_current_behavior(self):
        s = Settings()
        self.assertEqual(s.socks_port, 7898)      # singbox_engine.py:54
        self.assertEqual(s.http_port, 7897)       # singbox_engine.py:55
        self.assertEqual(s.data_dir, "~/.local/share/gate-client")  # cli.py:46-48
        self.assertIs(s.auto_probe_enabled, True)   # app.py:439 默认勾选
        self.assertEqual(s.auto_probe_interval_min, 10)  # app.py:445 默认第 2 档
        self.assertEqual(s.split_mode, "global")    # usage_model.py:167
        self.assertIs(s.ad_block, False)            # usage_model.py:168
        self.assertIs(s.malware_block, False)       # usage_model.py:169
        self.assertIs(s.ipv6, False)                # usage_model.py:170
        self.assertEqual(s.dns_server, "google")    # usage_model.py:171

    def test_default_constants_match_module_constants(self):
        self.assertEqual(DEFAULT_SOCKS_PORT, 7898)
        self.assertEqual(DEFAULT_HTTP_PORT, 7897)
        self.assertEqual(DEFAULT_DATA_DIR, "~/.local/share/gate-client")
        self.assertEqual(AUTO_PROBE_INTERVAL_CHOICES, (5, 10, 20, 30))
        self.assertEqual(SPLIT_MODES, ("global", "rule", "direct"))
        self.assertEqual(DNS_PRESETS["google"], "8.8.8.8")

    def test_usage_fields_constant(self):
        self.assertEqual(USAGE_FIELDS,
                         ("split_mode", "ad_block", "malware_block",
                          "ipv6", "dns_server"))


# ---------------------------------------------------------------------------
# 非法值逐项报错
# ---------------------------------------------------------------------------
class TestInvalidValues(unittest.TestCase):

    def assert_bad(self, **kwargs):
        with self.assertRaises(SettingsError):
            Settings(**kwargs)

    def test_port_out_of_range(self):
        for bad in (0, 65536, -1, -7898):
            self.assert_bad(socks_port=bad)
            self.assert_bad(http_port=bad)

    def test_port_type_strict(self):
        for bad in (True, False, 7898.0, "7898", None):
            self.assert_bad(socks_port=bad)
            self.assert_bad(http_port=bad)

    def test_ports_equal_rejected(self):
        self.assert_bad(socks_port=7898, http_port=7898)
        self.assert_bad(socks_port=1080, http_port=1080)

    def test_port_boundaries_legal(self):
        Settings(socks_port=1, http_port=65535)      # 不抛即合法
        Settings(socks_port=65535, http_port=1)

    def test_data_dir_empty_or_non_string(self):
        for bad in ("", "   ", "\t\n", None, 123, True, []):
            self.assert_bad(data_dir=bad)

    def test_data_dir_valid_values(self):
        Settings(data_dir="~/.local/share/gate-client")   # 默认
        Settings(data_dir="/tmp/gate-client-test")        # 不存在不算错（首次运行创建）
        Settings(data_dir=" relative/dir ")               # 非空白即可，消费端再展开

    def test_interval_non_positive_or_bad_type(self):
        for bad in (0, -1, -10, True, 10.0, "10", None):
            self.assert_bad(auto_probe_interval_min=bad)

    def test_interval_positive_legal(self):
        for ok in (1, 5, 10, 20, 30):
            Settings(auto_probe_interval_min=ok)          # 不抛即合法

    def test_probe_switch_strict_bool(self):
        for bad in (0, 1, "yes", None):
            self.assert_bad(auto_probe_enabled=bad)

    def test_rule_booleans_strict(self):
        for field in ("ad_block", "malware_block", "ipv6"):
            for bad in (0, 1, "true", None):
                self.assert_bad(**{field: bad})

    def test_split_mode_invalid(self):
        for bad in ("bogus", "GLOBAL", "", None, 123):
            self.assert_bad(split_mode=bad)

    def test_split_modes_legal(self):
        for mode in SPLIT_MODES:
            Settings(split_mode=mode)                     # 不抛即合法

    def test_dns_server_invalid(self):
        for bad in ("dns.example.com", "", "  ", None, True,
                    "999.999.1.1", "8.8.8.8.8"):
            self.assert_bad(dns_server=bad)

    def test_dns_server_domain_rejected_even_with_ipv6_on(self):
        self.assert_bad(ipv6=True, dns_server="dns.example.com")

    def test_ipv6_off_rejects_v6_dns(self):
        self.assert_bad(ipv6=False, dns_server="2001:db8::1")
        self.assert_bad(dns_server="2001:db8::1")         # 默认 ipv6=False

    def test_ipv6_on_accepts_v6_dns(self):
        s = Settings(ipv6=True, dns_server="2001:db8::1")  # 不抛即合法
        self.assertEqual(s.dns_server, "2001:db8::1")

    def test_dns_presets_and_ip_literals_legal(self):
        for preset in DNS_PRESETS:
            Settings(dns_server=preset)                   # 不抛即合法
        Settings(dns_server="192.0.2.10")                 # RFC 5737 TEST-NET-1
        Settings(ipv6=True, dns_server="2001:db8::10")    # 文档前缀

    def test_error_is_valueerror_family(self):
        self.assertTrue(issubclass(SettingsError, ValueError))


# ---------------------------------------------------------------------------
# 不可变（frozen）
# ---------------------------------------------------------------------------
class TestFrozen(unittest.TestCase):

    def test_setattr_rejected(self):
        s = Settings()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            s.http_port = 8080
        with self.assertRaises(dataclasses.FrozenInstanceError):
            s.split_mode = "rule"


# ---------------------------------------------------------------------------
# to_dict / from_dict
# ---------------------------------------------------------------------------
class TestRoundTrip(unittest.TestCase):

    def test_default_round_trip(self):
        s = Settings()
        d = s.to_dict()
        self.assertEqual(Settings.from_dict(d), s)
        self.assertIsNot(Settings.from_dict(d), s)

    def test_custom_round_trip(self):
        s = Settings(split_mode="rule", ad_block=True, ipv6=True,
                     dns_server="cloudflare", socks_port=1080,
                     http_port=8118, data_dir="/tmp/gate-x",
                     auto_probe_enabled=False, auto_probe_interval_min=30)
        self.assertEqual(Settings.from_dict(s.to_dict()), s)

    def test_to_dict_has_exactly_declared_fields(self):
        d = Settings().to_dict()
        self.assertEqual(set(d),
                         set(Settings.__dataclass_fields__))

    def test_from_dict_missing_fields_get_defaults(self):
        s = Settings.from_dict({"socks_port": 7899, "split_mode": "rule"})
        self.assertEqual(s.socks_port, 7899)
        self.assertEqual(s.split_mode, "rule")
        self.assertEqual(s.http_port, 7897)               # 其余补默认
        self.assertEqual(s.data_dir, DEFAULT_DATA_DIR)
        self.assertEqual(s.auto_probe_interval_min, 10)
        self.assertEqual(s.dns_server, "google")

    def test_from_dict_unknown_key_raises(self):
        with self.assertRaises(SettingsError) as ctx:
            Settings.from_dict({"http_port": 7897, "bogus_key": 1})
        self.assertIn("bogus_key", str(ctx.exception))

    def test_from_dict_extra_key_not_silently_dropped(self):
        """多余字段必须如实报告（抛错），不许静默丢弃。"""
        d = Settings().to_dict()
        d["future_field"] = "x"
        with self.assertRaises(SettingsError):
            Settings.from_dict(d)

    def test_from_dict_non_dict_raises(self):
        for bad in ([], "x", 1, None):
            with self.assertRaises(SettingsError):
                Settings.from_dict(bad)

    def test_from_dict_invalid_value_raises(self):
        with self.assertRaises(SettingsError):
            Settings.from_dict({"socks_port": 70000})

    def test_usage_dict_subset(self):
        s = Settings(split_mode="direct", ad_block=True)
        u = s.usage_dict()
        self.assertEqual(tuple(u), USAGE_FIELDS)
        self.assertEqual(u["split_mode"], "direct")
        self.assertEqual(u["ad_block"], True)
        self.assertEqual(u["dns_server"], "google")
        self.assertNotIn("socks_port", u)


# ---------------------------------------------------------------------------
# merge：override 只覆盖显式给出的字段
# ---------------------------------------------------------------------------
class TestMerge(unittest.TestCase):

    def test_merge_overrides_only_explicit_fields(self):
        base = Settings()
        m = merge(base, {"http_port": 8080, "split_mode": "rule"})
        self.assertEqual(m.http_port, 8080)
        self.assertEqual(m.split_mode, "rule")
        self.assertEqual(m.socks_port, 7898)              # 未显式给出 → 继承 base
        self.assertEqual(m.data_dir, base.data_dir)
        self.assertEqual(m.auto_probe_interval_min, 10)

    def test_merge_empty_override_equals_base(self):
        self.assertEqual(merge(Settings(), {}), Settings())

    def test_merge_none_returns_base(self):
        base = Settings()
        self.assertIs(merge(base, None), base)

    def test_merge_does_not_mutate_base(self):
        base = Settings()
        merge(base, {"http_port": 8080})
        self.assertEqual(base.http_port, 7897)

    def test_merge_unknown_key_raises(self):
        with self.assertRaises(SettingsError) as ctx:
            merge(Settings(), {"nope": 1})
        self.assertIn("nope", str(ctx.exception))

    def test_merge_invalid_value_raises(self):
        with self.assertRaises(SettingsError):
            merge(Settings(), {"auto_probe_interval_min": 0})
        with self.assertRaises(SettingsError):
            merge(Settings(), {"dns_server": "dns.example.com"})

    def test_merge_non_dict_override_raises(self):
        for bad in ("http_port=8080", [1], 5):
            with self.assertRaises(SettingsError):
                merge(Settings(), bad)

    def test_merge_non_settings_base_raises(self):
        with self.assertRaises(SettingsError):
            merge({"http_port": 8080}, {})

    def test_merge_chained_partial_updates(self):
        s = merge(Settings(), {"auto_probe_enabled": False})
        s = merge(s, {"auto_probe_interval_min": 20})
        self.assertIs(s.auto_probe_enabled, False)
        self.assertEqual(s.auto_probe_interval_min, 20)
        self.assertEqual(s.http_port, 7897)


# ---------------------------------------------------------------------------
# 与 UsageSettings 字段口径一致（client-dev 可导入时跑，否则跳过）
# ---------------------------------------------------------------------------
@unittest.skipIf(_USAGE is None,
                 "client-dev 参考不可导入（GATE_CLIENT_DEV 可指定路径）")
class TestParityWithUsageSettings(unittest.TestCase):
    """红线机械锁定：同名字段同默认值同校验，冲突以 UsageSettings 为准。"""

    def test_field_names_identical_in_order(self):
        self.assertEqual(tuple(_USAGE["UsageSettings"].__dataclass_fields__),
                         USAGE_FIELDS)
        self.assertEqual(set(Settings.__dataclass_fields__)
                         - set(USAGE_FIELDS),
                         {"socks_port", "http_port", "data_dir",
                          "auto_probe_enabled", "auto_probe_interval_min",
                          # U4/task19 W1 扩展位：测速载荷地址（§13-G 空槽）
                          "speedtest_payload_url"})

    def test_shared_field_defaults_identical(self):
        mine, theirs = Settings(), _USAGE["UsageSettings"]()
        for f in USAGE_FIELDS:
            self.assertEqual(getattr(mine, f), getattr(theirs, f),
                             f"字段 {f} 默认值与 UsageSettings 不一致")

    def test_constants_identical(self):
        self.assertEqual(SPLIT_MODES, _USAGE["SPLIT_MODES"])
        self.assertEqual(DNS_PRESETS, _USAGE["DNS_PRESETS"])

    def test_same_bad_inputs_raise_on_both_models(self):
        bad_inputs = [
            {"split_mode": "bogus"},
            {"ad_block": 1},
            {"malware_block": 0},
            {"ipv6": "yes"},
            {"dns_server": "dns.example.com"},
            {"dns_server": ""},
            {"dns_server": None},
            {"dns_server": "999.999.1.1"},
            {"ipv6": False, "dns_server": "2001:db8::1"},
        ]
        for kwargs in bad_inputs:
            with self.assertRaises(SettingsError):
                Settings(**kwargs)
            with self.assertRaises(_USAGE["UsageSettingsError"]):
                _USAGE["UsageSettings"](**kwargs)

    def test_same_good_inputs_accepted_on_both_models(self):
        good_inputs = [
            {},
            {"split_mode": "rule"},
            {"split_mode": "direct"},
            {"ad_block": True, "malware_block": True},
            {"ipv6": True, "dns_server": "2001:db8::1"},
            {"dns_server": "192.0.2.10"},
            {"dns_server": "quad9"},
        ]
        for kwargs in good_inputs:
            Settings(**kwargs)                             # 不抛即合法
            _USAGE["UsageSettings"](**kwargs)

    def test_error_types_same_family(self):
        self.assertTrue(issubclass(SettingsError, ValueError))
        self.assertTrue(issubclass(_USAGE["UsageSettingsError"], ValueError))


if __name__ == "__main__":
    unittest.main()
