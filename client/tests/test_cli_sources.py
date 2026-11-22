#!/usr/bin/env python3
"""ui/cli sources 离线测试：登记/更新/删除 + 订阅导入自动登记。
假 fetcher 注入，绝不触网；地址只用假域名；登记表地址不回显全址。"""
import base64
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.store import Store
from client.tests.test_cli import run_cli
from client.tests.test_subscription import FAKE_SUB_URL, THREE_LINKS

FEED_HEADER = ("HostName,IP,Score,Ping,Speed,CountryLong,CountryShort,"
               "NumVpnSessions,Uptime,OpenVPN_ConfigData_Base64")


def _ovpn_b64(host, port=443, proto="udp"):
    cfg = (f"client\ndev tun\nproto {proto}\nremote {host} {port}\n"
           "resolv-retry infinite\n")
    return base64.b64encode(cfg.encode()).decode()


def _feed_row(host, ip, sessions=10, speed=8000000):
    return ",".join([host, ip, "100", "50", str(speed), "Fake Country", "FC",
                     str(sessions), "86400000", _ovpn_b64(host)])


def _feed_csv(*rows):
    return FEED_HEADER + "\n" + "\n".join(rows) + "\n"


def _mixed_fetch(entry):
    if entry["type"] == "subscription":
        return THREE_LINKS
    return _feed_csv(_feed_row("f1.opengw.net", "192.0.2.11"))


class TestCliSources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "client.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _cli(self, *argv, fetcher=None):
        return run_cli(["--db", self.db, "sources", *argv], fetcher=fetcher)

    def _registry_json(self):
        """task30 起登记表事实源是 Store 的 sources 表（sources.json 只在
        表空首用时被只读迁移一次，不再被写入）。本 peephole 改读表、方法
        名留旧；下方各断言的口径一行不动。"""
        store = Store(self.db)
        try:
            return {"entries": store.entries()}
        finally:
            store.close()

    def _db_count(self):
        store = Store(self.db)
        try:
            return store.count_nodes()
        finally:
            store.close()

    # 1. 登记 → 列表（只显域名）→ 更新（新增 3）→ 再更新（全是更新）→ 删除
    def test_add_list_update_remove(self):
        rc, out, err = self._cli("add", "--type", "subscription",
                                 "--address", FAKE_SUB_URL, "--name", "测试订阅")
        self.assertEqual(rc, 0, err)
        self.assertIn("已登记 sub-", out)
        entry_id = self._registry_json()["entries"][0]["id"]

        rc, out, err = self._cli("list")
        self.assertEqual(rc, 0, err)
        self.assertIn(entry_id, out)
        self.assertIn("sub.example.com", out)
        self.assertNotIn("/fake/sub", out)  # 红线：不回显订阅地址全址

        rc, out, err = self._cli("update", entry_id,
                                 fetcher=lambda e: THREE_LINKS)
        self.assertEqual(rc, 0, err)
        self.assertIn("新增 3", out)
        self.assertEqual(self._db_count(), 3)

        rc, out, err = self._cli("update", entry_id,
                                 fetcher=lambda e: THREE_LINKS)
        self.assertEqual(rc, 0, err)
        self.assertIn("新增 0", out)
        self.assertIn("更新 3", out)

        rc, out, err = self._cli("remove", entry_id)
        self.assertEqual(rc, 0, err)
        self.assertEqual(self._registry_json()["entries"], [])
        self.assertEqual(self._db_count(), 3)  # 删登记不删节点

    # 2. 重复登记 / 坏 scheme / 未知条目均显式报错
    def test_add_duplicate_rejected(self):
        self._cli("add", "--type", "subscription", "--address", FAKE_SUB_URL)
        rc, out, err = self._cli("add", "--type", "subscription",
                                 "--address", FAKE_SUB_URL)
        self.assertEqual(rc, 2)
        self.assertIn("已登记过", err)

    def test_add_bad_scheme_rejected(self):
        rc, out, err = self._cli("add", "--type", "subscription",
                                 "--address", "ftp://x.example.com/s")
        self.assertEqual(rc, 2)

    def test_update_unknown_rejected(self):
        rc, out, err = self._cli("update", "sub-deadbeef")
        self.assertEqual(rc, 2)
        self.assertIn("未登记", err)

    # 3. update-all：订阅 + feed 各一条，条间独立
    def test_update_all_mixed(self):
        self._cli("add", "--type", "subscription", "--address", FAKE_SUB_URL)
        self._cli("add", "--type", "feed")
        rc, out, err = self._cli("update-all", fetcher=_mixed_fetch)
        self.assertEqual(rc, 0, err)
        self.assertIn("新增 3", out)  # 订阅
        self.assertIn("新增 1", out)  # feed
        self.assertEqual(self._db_count(), 4)

    # 4. enable/disable：停用条目 update-all 跳过（fetcher 不被调）
    def test_disable_skips_update_all(self):
        self._cli("add", "--type", "subscription", "--address", FAKE_SUB_URL)
        entry_id = self._registry_json()["entries"][0]["id"]
        rc, out, err = self._cli("disable", entry_id)
        self.assertEqual(rc, 0, err)
        calls = []
        rc, out, err = self._cli("update-all",
                                 fetcher=lambda e: calls.append(e) or THREE_LINKS)
        self.assertEqual(rc, 0, err)
        self.assertEqual(calls, [])
        self.assertEqual(self._db_count(), 0)

    # 5. 订阅地址导入自动登记（下次可一键更新）
    def test_import_subscription_auto_registers(self):
        rc, out, err = run_cli(
            ["--db", self.db, "import-subscription", "--url", FAKE_SUB_URL],
            fetcher=lambda url: THREE_LINKS)
        self.assertEqual(rc, 0, err)
        self.assertIn("已收纳 3 个节点", out)
        entries = self._registry_json()["entries"]
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["type"], "subscription")
        self.assertEqual(entries[0]["address"], FAKE_SUB_URL)
        # 再导一次不重复登记、不报错
        rc, out, err = run_cli(
            ["--db", self.db, "import-subscription", "--url", FAKE_SUB_URL],
            fetcher=lambda url: THREE_LINKS)
        self.assertEqual(rc, 0, err)
        self.assertEqual(len(self._registry_json()["entries"]), 1)
