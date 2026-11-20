#!/usr/bin/env python3
"""M2.5 离线测试：验后按实测出口改名 + 污染节点隔离。

- 改名：成功验后改名且 orig_name 只存第一次；重复验不覆盖 orig_name；
  验失败不改名；restore-names 能换回。
- 隔离：dirty 画像落隔离标记；干净画像清除标记；list 默认隐藏 +
  汇总行计数正确；--show-quarantined 显示且带 ☠；connect 隔离节点先警告。
全程假 probe / 假引擎，绝不触网。
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.model import Node
from client.core.verify import (ProbeResult, apply_outcome, exit_display_name,
                                pollution_of, verify_exit)
from client.ui import cli


def _node(name="机场假名 JP-01"):
    return Node(id="manual:ss:1.2.3.4:8388:abcd1234", name=name,
                protocol="ss", source="manual", host="1.2.3.4", port=8388)


def _probe(**kw):
    base = dict(alive=True, exit_ip="176.97.192.53", country="Nigeria",
                country_code="NG", org="Example ISP", is_clean=True,
                latency_ms=120.0, jitter_ms=30.0, muse_code=307)
    base.update(kw)
    return lambda node: ProbeResult(**base)


class TestRenameByExit(unittest.TestCase):
    def test_clean_verify_renames_and_preserves_orig(self):
        n = _node()
        apply_outcome(n, verify_exit(n, _probe()))
        self.assertEqual(n.name, "尼日利亚 · 176.97.192.53 · Example ISP")
        self.assertEqual(n.extra.get("orig_name"), "机场假名 JP-01")
        self.assertNotEqual(n.extra.get("quarantined"), "1")

    def test_reverify_updates_name_but_not_orig_name(self):
        n = _node()
        apply_outcome(n, verify_exit(n, _probe()))
        apply_outcome(n, verify_exit(n, _probe(
            exit_ip="10.0.0.9", country="Japan", country_code="JP", org="NTT EAST")))
        self.assertEqual(n.name, "日本 · 10.0.0.9 · NTT EAST")
        self.assertEqual(n.extra.get("orig_name"), "机场假名 JP-01")

    def test_failed_verify_keeps_name(self):
        n = _node()
        apply_outcome(n, verify_exit(n, lambda node: ProbeResult(alive=False, error="死")))
        self.assertEqual(n.name, "机场假名 JP-01")
        self.assertNotIn("orig_name", n.extra)

    def test_org_omitted_two_segments(self):
        p = verify_exit(_node(), _probe(org=None)).exit_profile
        self.assertEqual(exit_display_name(p), "尼日利亚 · 176.97.192.53")

    def test_unknown_country_code_falls_back_to_code(self):
        p = verify_exit(_node(), _probe(country=None, country_code="XX")).exit_profile
        self.assertTrue(exit_display_name(p).startswith("XX · "))


class TestQuarantine(unittest.TestCase):
    def test_muse_reject_quarantines(self):
        n = _node()
        apply_outcome(n, verify_exit(n, _probe(muse_code=403)))
        self.assertEqual(n.extra.get("quarantined"), "1")
        self.assertIn("muse", n.extra.get("quarantine_reason", ""))

    def test_dirty_ip_quarantines(self):
        n = _node()
        apply_outcome(n, verify_exit(n, _probe(is_clean=False)))
        self.assertEqual(n.extra.get("quarantined"), "1")
        self.assertIn("dirty_ip", n.extra.get("quarantine_reason", ""))

    def test_clean_reverify_clears_quarantine(self):
        n = _node()
        apply_outcome(n, verify_exit(n, _probe(muse_code=403)))
        self.assertEqual(n.extra.get("quarantined"), "1")
        apply_outcome(n, verify_exit(n, _probe()))
        self.assertNotIn("quarantined", n.extra)
        self.assertNotIn("quarantine_reason", n.extra)

    def test_pollution_of_clean_profile(self):
        p = verify_exit(_node(), _probe()).exit_profile
        self.assertEqual(pollution_of(p), (False, ""))


def run_cli(argv, **kw):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        rc = cli.main(argv, **kw)
    return rc, out.getvalue(), err.getvalue()


def polluting_factory():
    return _probe(muse_code=403)


def clean_factory():
    return _probe()


class FakeEngine:
    name = "ss"

    def start(self, node, on_event=None):
        return {"node_id": node.id}

    def stop(self, handle):
        pass

    def status(self, handle):
        return "running"


class TestCliM25(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "client.db")
        link = ("ss://YWVzLTI1Ni1nY206cGFzc3dvcmQ@1.2.3.4:8388"
                "#%E6%9C%BA%E5%9C%BA%E5%81%87%E5%90%8D")
        rc, _o, _e = run_cli(["--db", self.db, "intake", "--link", link])
        self.assertEqual(rc, 0)
        from client.core.store import Store
        st = Store(self.db)
        self.node_id = st.list_nodes()[0].id
        st.close()

    def tearDown(self):
        self.tmp.cleanup()

    def test_list_hides_quarantined_with_summary(self):
        rc, out, _ = run_cli(["--db", self.db, "verify", self.node_id],
                             probe_factory=polluting_factory)
        self.assertEqual(rc, 0)
        rc, out, _ = run_cli(["--db", self.db, "list"])
        self.assertEqual(rc, 0)
        self.assertIn("另有 1 个污染节点已隔离", out)
        self.assertNotIn("☠", out)
        rc, out, _ = run_cli(["--db", self.db, "list", "--show-quarantined"])
        self.assertEqual(rc, 0)
        self.assertIn("☠", out)
        self.assertIn("尼日利亚 · 176.97.192.53", out)

    def test_restore_names_roundtrip(self):
        run_cli(["--db", self.db, "verify", self.node_id],
                probe_factory=polluting_factory)
        rc, out, _ = run_cli(["--db", self.db, "restore-names"])
        self.assertEqual(rc, 0)
        self.assertIn("已恢复 1 个", out)
        from client.core.store import Store
        st = Store(self.db)
        n = st.get_node(self.node_id)
        st.close()
        self.assertNotIn("orig_name", n.extra)
        self.assertNotEqual(n.name, "尼日利亚 · 176.97.192.53 · Example ISP")
        # 隔离标记不被 restore-names 动
        self.assertEqual(n.extra.get("quarantined"), "1")

    def test_connect_quarantined_warns_but_connects(self):
        run_cli(["--db", self.db, "verify", self.node_id],
                probe_factory=polluting_factory)
        rc, out, err = run_cli(
            ["--db", self.db, "connect", self.node_id],
            probe_factory=clean_factory,
            engine_factory=lambda: {"ss": FakeEngine()})
        self.assertEqual(rc, 0, err)
        self.assertIn("污染节点", out)
        self.assertIn("connected", out)


if __name__ == "__main__":
    unittest.main()
