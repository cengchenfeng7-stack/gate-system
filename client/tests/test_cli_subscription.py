#!/usr/bin/env python3
"""ui/cli import-subscription 离线测试：临时库 + 假 fetcher 注入，
绝不触网；订阅地址/链接只许用假域名 fixture（红线见 TASKS/T5）。"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.store import Store
from client.tests.test_cli import run_cli
from client.tests.test_subscription import (FAKE_SUB_NAME, FAKE_SUB_URL,
                                            THREE_LINKS, VLESS_LINK, b64)


def make_fake_fetcher(text: str, exc: Exception = None):
    """假 fetcher：返回 text 或抛 exc，并记录被调的 url。"""
    calls = []

    def fetcher(url):
        calls.append(url)
        if exc is not None:
            raise exc
        return text

    return fetcher, calls


class TestCliImportSubscription(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "client.db")

    def tearDown(self):
        self.tmp.cleanup()

    def _write_sub_file(self, content: str) -> str:
        path = os.path.join(self.tmp.name, "sub.txt")
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        return path

    def _db_nodes(self):
        store = Store(self.db)
        try:
            return store.list_nodes()
        finally:
            store.close()

    def _import(self, *extra, fetcher=None):
        return run_cli(["--db", self.db, "import-subscription", *extra],
                       fetcher=fetcher)

    # 1. --file base64 fixture + --name → 入库成功且盖章
    def test_file_import_base64_with_name(self):
        path = self._write_sub_file(b64(THREE_LINKS))
        rc, out, err = self._import("--file", path, "--name", FAKE_SUB_NAME)
        self.assertEqual(rc, 0, err)
        self.assertIn("已收纳 3 个节点", out)
        self.assertIn("跳过坏行 0", out)
        self.assertIn("格式 base64", out)
        nodes = self._db_nodes()
        self.assertEqual(len(nodes), 3)
        self.assertTrue(all(n.source == "manual" for n in nodes))
        self.assertTrue(all(n.extra.get("subscription_name") == FAKE_SUB_NAME
                            for n in nodes))

    # 2. --file 含 1 条坏行 → 跳过计数，其余入库
    def test_file_import_bad_line_counted(self):
        path = self._write_sub_file(THREE_LINKS + "not-a-link://x\n")
        rc, out, err = self._import("--file", path)
        self.assertEqual(rc, 0, err)
        self.assertIn("跳过坏行 1", out)
        self.assertEqual(len(self._db_nodes()), 3)

    # 3. --url + 假 fetcher → fetcher 恰好一次、参数对、subscription_url 盖章
    def test_url_import_with_fake_fetcher(self):
        fetcher, calls = make_fake_fetcher(THREE_LINKS)
        rc, out, err = self._import("--url", FAKE_SUB_URL, fetcher=fetcher)
        self.assertEqual(rc, 0, err)
        self.assertEqual(calls, [FAKE_SUB_URL])   # 恰好一次且参数对
        self.assertIn("已收纳 3 个节点", out)
        self.assertIn("格式 plain", out)
        nodes = self._db_nodes()
        self.assertEqual(len(nodes), 3)
        self.assertTrue(all(n.extra.get("subscription_url") == FAKE_SUB_URL
                            for n in nodes))
        # 红线：不回显订阅地址全文
        self.assertNotIn(FAKE_SUB_URL, out)
        self.assertNotIn(FAKE_SUB_URL, err)

    # 4. --url + fetcher 抛 OSError → rc==2，库空
    def test_url_fetch_oserror(self):
        fetcher, calls = make_fake_fetcher("", exc=OSError("boom"))
        rc, _out, err = self._import("--url", FAKE_SUB_URL, fetcher=fetcher)
        self.assertEqual(rc, 2)
        self.assertEqual(calls, [FAKE_SUB_URL])
        self.assertIn("拉取订阅失败", err)
        self.assertIn("boom", err)
        self.assertEqual(len(self._db_nodes()), 0)

    # 5. --file 纯垃圾文本 → rc==2，库空（阶段二 task2：识别不了显式报错，
    # 不再是旧的「没有解析出任何节点」静默空结果口径）
    def test_file_garbage_returns_2(self):
        path = self._write_sub_file("完全不是链接的垃圾文本\n随便写的第二行\n")
        rc, _out, err = self._import("--file", path)
        self.assertEqual(rc, 2)
        self.assertIn("导入失败", err)
        self.assertIn("无法识别", err)
        self.assertEqual(len(self._db_nodes()), 0)

    # 6. --file 路径不存在 → rc==2
    def test_file_missing_returns_2(self):
        missing = os.path.join(self.tmp.name, "nope.txt")
        rc, _out, err = self._import("--file", missing)
        self.assertEqual(rc, 2)
        self.assertIn("文件不存在", err)

    # 7. --file 与 --url 都不给 / 都给 → rc==2
    def test_file_and_url_xor(self):
        rc, _out, err = self._import()
        self.assertEqual(rc, 2)
        self.assertIn("二选一", err)
        rc, _out, err = self._import("--file", "sub.txt", "--url", FAKE_SUB_URL)
        self.assertEqual(rc, 2)
        self.assertIn("二选一", err)
        self.assertEqual(len(self._db_nodes()), 0)

    # 8. 红线：成功输出不回显节点链接原文
    def test_stdout_no_link_echo(self):
        path = self._write_sub_file(b64(THREE_LINKS))
        rc, out, err = self._import("--file", path, "--name", FAKE_SUB_NAME)
        self.assertEqual(rc, 0, err)
        self.assertIn("已收纳 3 个节点", out)
        self.assertNotIn(VLESS_LINK, out)
        self.assertNotIn("vless://", out)   # 任何 vless 链接原文都不许出现
        self.assertNotIn("config_ref", out)


if __name__ == "__main__":
    unittest.main()
