#!/usr/bin/env python3
"""core/orchestrator 铁律测试：假引擎 + 假 verifier，全离线。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.model import ExitProfile, Node
from client.core.orchestrator import Orchestrator, OrchestratorError
from client.core.store import Store
from client.core.verify import VerifyOutcome
from client.engines.base import EngineError


class FakeEngine:
    name = "openvpn"

    def __init__(self, fail_start=False):
        self.fail_start = fail_start
        self.started = []
        self.stopped = []
        self.alive = True

    def start(self, node, on_event=None):
        if self.fail_start:
            raise EngineError("假引擎启动失败")
        self.started.append(node.id)
        self.alive = True
        return {"node_id": node.id}

    def stop(self, handle):
        self.stopped.append(handle)
        self.alive = False

    def status(self, handle):
        return "running" if self.alive else "stopped"


def ok_verifier(node):
    return VerifyOutcome(ok=True, exit_profile=ExitProfile(
        ip="5.6.7.8", country_code="JP", network_type="residential", is_clean=True),
        scenes={"support"})


def bad_verifier(node):
    return VerifyOutcome(ok=False, reason="出口 IP 没变（假连）")


def make_orch(verifier=ok_verifier, engine=None):
    store = Store(":memory:")
    # 自 task23 起行为反转、依据 work8/DESIGN §7：旧键 vpngate_feed 退役，
    # fixture 改用 canonical 键 vpngate（编排器按 protocol 选引擎，不读 source）。
    store.upsert_node(Node(id="n1", name="n1", protocol="openvpn", source="vpngate",
                           host="h1", port=443))
    store.upsert_node(Node(id="n2", name="n2", protocol="openvpn", source="vpngate",
                           host="h2", port=443))
    eng = engine or FakeEngine()
    return Orchestrator(store, {"openvpn": eng}, verifier), eng, store


class TestIronRules(unittest.TestCase):
    def test_connect_happy_path(self):
        orch, eng, _store = make_orch()
        ev = orch.connect("n1")
        self.assertEqual(ev.kind, "connected")
        self.assertEqual(orch.status(), {"state": "connected", "active_node_id": "n1"})
        self.assertEqual(eng.started, ["n1"])

    def test_single_active_only(self):
        orch, _eng, _s = make_orch()
        orch.connect("n1")
        with self.assertRaises(OrchestratorError):
            orch.connect("n2")

    def test_no_auto_switch_api_exists(self):
        orch, _eng, _s = make_orch()
        for forbidden in ("connect_best", "connect_next", "auto_switch",
                          "switch", "failover", "reconnect"):
            self.assertFalse(hasattr(orch, forbidden),
                             f"铁律：不应存在自动选择/切换 API {forbidden}")

    def test_connect_requires_explicit_node(self):
        orch, _eng, _s = make_orch()
        with self.assertRaises(TypeError):
            orch.connect()  # 必须显式给 node_id

    def test_verify_failure_disconnects(self):
        orch, eng, store = make_orch(verifier=bad_verifier)
        ev = orch.connect("n1")
        self.assertEqual(ev.kind, "verify_failed")
        self.assertEqual(orch.status()["state"], "idle")
        self.assertEqual(len(eng.stopped), 1)          # 引擎被拆掉，防假连
        samples = store.samples_for("n1", kind="connect")
        self.assertEqual(len(samples), 1)
        self.assertFalse(samples[0].success)

    def test_engine_start_failure_is_event_not_stack(self):
        orch, _eng, _s = make_orch(engine=FakeEngine(fail_start=True))
        ev = orch.connect("n1")
        self.assertEqual(ev.kind, "error")
        self.assertEqual(orch.status()["state"], "idle")

    def test_banned_node_refused(self):
        orch, _eng, store = make_orch()
        store.set_banned("n1", True)
        with self.assertRaises(OrchestratorError):
            orch.connect("n1")

    def test_unknown_node_and_protocol(self):
        orch, _eng, store = make_orch()
        with self.assertRaises(OrchestratorError):
            orch.connect("ghost")
        store.upsert_node(Node(id="v1", name="v1", protocol="vless", source="manual",
                               host="h", port=443))
        with self.assertRaises(OrchestratorError):
            orch.connect("v1")   # M1 无 vless 引擎 → 明确拒绝而非乱连

    def test_drop_reported_never_auto_reconnects(self):
        orch, eng, _s = make_orch()
        orch.connect("n1")
        eng.alive = False                              # 模拟掉线
        ev = orch.poll()
        self.assertEqual(ev.kind, "dropped")
        self.assertEqual(orch.status()["state"], "idle")
        self.assertEqual(eng.started, ["n1"])          # 只启动过一次：没有自动连 n2
        self.assertIsNone(orch.poll())                 # 巡检无事返回 None

    def test_disconnect(self):
        orch, eng, _s = make_orch()
        orch.connect("n1")
        ev = orch.disconnect()
        self.assertEqual(ev.kind, "disconnected")
        self.assertEqual(orch.status()["state"], "idle")
        self.assertEqual(len(eng.stopped), 1)


if __name__ == "__main__":
    unittest.main()
