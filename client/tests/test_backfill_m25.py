import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from client.core.backfill import backfill_exit_names
from client.core.model import ExitProfile, Node
from client.core.store import Store


def _node(nid, name, profile=None):
    n = Node(id=nid, name=name, protocol="ss", source="manual",
             host="1.2.3.4", port=8388, config_ref="ss://x")
    n.exit_profile = profile if profile is not None else ExitProfile()
    return n


class TestBackfill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.db = os.path.join(self.tmp, "t.db")
        self.store = Store(self.db)

    def tearDown(self):
        self.store.close()

    def test_rename_from_stored_profile(self):
        p = ExitProfile(ip="203.0.113.7", country="United States",
                        country_code="US", org="Example ISP", is_clean=True)
        self.store.upsert_node(_node("manual:ss:1.2.3.4:8388", "营销名-美国01", p))
        stats = backfill_exit_names(self.store)
        self.assertEqual(stats["scanned"], 1)
        self.assertEqual(stats["renamed"], 1)
        got = self.store.get_node("manual:ss:1.2.3.4:8388")
        self.assertEqual(got.name, "美国 · 203.0.113.7 · Example ISP")
        self.assertEqual(got.extra.get("orig_name"), "营销名-美国01")

    def test_idempotent_second_run(self):
        p = ExitProfile(ip="203.0.113.7", country_code="US",
                        org="Example ISP", is_clean=True)
        self.store.upsert_node(_node("n1", "原名", p))
        backfill_exit_names(self.store)
        stats2 = backfill_exit_names(self.store)
        self.assertEqual(stats2["renamed"], 0)
        got = self.store.get_node("n1")
        self.assertEqual(got.extra.get("orig_name"), "原名")

    def test_quarantine_from_stored_profile(self):
        p = ExitProfile(ip="198.51.100.9", country_code="NL",
                        is_clean=False, dirty={"dirty_ip"})
        self.store.upsert_node(_node("n2", "荷兰节点", p))
        stats = backfill_exit_names(self.store)
        self.assertEqual(stats["quarantined"], 1)
        got = self.store.get_node("n2")
        self.assertEqual(got.extra.get("quarantined"), "1")

    def test_node_without_profile_skipped(self):
        self.store.upsert_node(_node("n3", "没验过的"))
        stats = backfill_exit_names(self.store)
        self.assertEqual(stats["scanned"], 0)
        self.assertEqual(self.store.get_node("n3").name, "没验过的")


if __name__ == "__main__":
    unittest.main()
