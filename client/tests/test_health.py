#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
health.py 离线测试（test_health.py）
====================================
全部离线：端口占用用**真实 bind 本地临时端口**制造（任务红线允许，
绝不触网）；gsettings 用注入 runner 的假输出文本；health 分类用构造
样本行。被测模块 client.core.health 只用标准库。

跑法：cd client-dev/client && python3 -m unittest tests.test_health
"""

from __future__ import annotations

import os
import socket
import sys
import time
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core import health  # noqa: E402


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------
@contextmanager
def listener(host: str, port: int, reuse: bool = False):
    """在本机临时端口起一个真实 TCP 监听（制造占用场景）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if reuse:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.listen(5)
    try:
        yield sock
    finally:
        sock.close()


def free_port() -> int:
    """拿一个当前空闲的本地端口（bind(0) 后立刻关，未发生过连接、
    无 TIME_WAIT 残留，探测应判空闲）。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def rows(*oks) -> list:
    """构造样本行（每行至少含 ok: bool）。"""
    return [{"ok": ok} for ok in oks]


def sysproxy(mode="manual", host="127.0.0.1", port=7890,
             available=True, error="") -> dict:
    base = {"available": available, "mode": mode,
            "http_host": host, "http_port": port, "error": error}
    return base


# ---------------------------------------------------------------------------
# 三件套之一：引擎端口占用检测
# ---------------------------------------------------------------------------
class TestCheckPortsFree(unittest.TestCase):

    def test_free_port_reports_empty(self):
        port = free_port()
        self.assertEqual(health.check_ports_free([port]), [])

    def test_occupied_by_exact_listener(self):
        port = free_port()
        with listener("127.0.0.1", port):
            occupied = health.check_ports_free([port])
        self.assertEqual(len(occupied), 1)
        entry = occupied[0]
        self.assertEqual(entry["port"], port)
        # 占用方是测试进程自己：pid 与进程名都能查到（本用户 /proc 可读）
        self.assertEqual(entry["pid"], os.getpid())
        self.assertTrue(entry["process"])

    def test_occupied_by_wildcard_listener_with_reuseaddr(self):
        # Go 风格监听：0.0.0.0 + SO_REUSEADDR（实测本内核：探测 bind
        # 无论带不带 REUSEADDR 都 EADDRINUSE，必须能查出来）
        port = free_port()
        with listener("0.0.0.0", port, reuse=True):
            occupied = health.check_ports_free([port])
        self.assertEqual([e["port"] for e in occupied], [port])

    def test_occupied_by_exact_listener_with_reuseaddr(self):
        # 占用方自己也带 REUSEADDR（Karing/Clash 类常见）：照样要查出来
        port = free_port()
        with listener("127.0.0.1", port, reuse=True):
            occupied = health.check_ports_free([port])
        self.assertEqual([e["port"] for e in occupied], [port])

    @staticmethod
    def _make_time_wait_residue(port: int, reuse: bool):
        """制造 TIME_WAIT 残留：真实连接后服务端先主动关，accepted
        socket 进 TIME_WAIT（本地端口=port）。状态机由内核瞬时完成，
        sleep 只是等 FIN 收尾（本机 /proc/net/tcp 实测 0.2s 后即 06 态）。"""
        with listener("127.0.0.1", port, reuse=reuse) as sock:
            client = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            client.connect(("127.0.0.1", port))
            accepted, _ = sock.accept()
            accepted.close()            # 服务端先关 → TIME_WAIT 残留
            client.close()
        time.sleep(0.2)

    def test_time_wait_residue_from_reuse_listener_still_free(self):
        # 内核口径实测（2026-11-21，/proc/net/tcp 确认 06=TIME_WAIT 在场）：
        # REUSEADDR 监听者留下的 TIME_WAIT 不挡 REUSEADDR bind——即
        # sing-box/Go（默认 REUSEADDR）这类软件的残留不会让我们误报，
        # 探测结论与引擎实际可 bind 一致。
        port = free_port()
        self._make_time_wait_residue(port, reuse=True)
        self.assertEqual(health.check_ports_free([port]), [])

    def test_time_wait_residue_from_plain_listener_blocks_reuse_bind(self):
        # 对照组：不带 REUSEADDR 的监听者留下的 TIME_WAIT，连 REUSEADDR
        # 的 bind 都进不去（内核要求两侧都带）——此时引擎同样起不来，
        # 探测如实报占用，不算误报。
        port = free_port()
        self._make_time_wait_residue(port, reuse=False)
        occupied = health.check_ports_free([port])
        self.assertEqual([e["port"] for e in occupied], [port])
        # 残留没有归属进程（socket 已关），查不到 pid/name 是正常降级
        self.assertIsNone(occupied[0]["pid"])

    def test_mixed_free_and_occupied_reports_only_occupied(self):
        free1, busy, free2 = free_port(), free_port(), free_port()
        with listener("127.0.0.1", busy):
            occupied = health.check_ports_free([free1, busy, free2])
        self.assertEqual([e["port"] for e in occupied], [busy])

    def test_invalid_port_raises(self):
        with self.assertRaises(ValueError):
            health.check_ports_free([0])
        with self.assertRaises(ValueError):
            health.check_ports_free([70000])

    def test_describe_occupied_text(self):
        self.assertEqual(health.describe_occupied([]), "")
        with_name = health.describe_occupied(
            [{"port": 7897, "pid": 4321, "process": "karing"}])
        self.assertIn("7897", with_name)
        self.assertIn("karing", with_name)
        self.assertIn("4321", with_name)
        no_name = health.describe_occupied(
            [{"port": 7898, "pid": None, "process": None}])
        self.assertIn("7898", no_name)
        self.assertIn("查不到", no_name)


# ---------------------------------------------------------------------------
# 三件套之二：系统代理解析与冲突判读
# ---------------------------------------------------------------------------
class TestParseGsettingsProxy(unittest.TestCase):

    def test_typical_manual(self):
        res = health.parse_gsettings_proxy(
            "'manual'\n'127.0.0.1'\n7897\n")
        self.assertEqual(res, {"mode": "manual", "http_host": "127.0.0.1",
                               "http_port": 7897})

    def test_none_mode(self):
        res = health.parse_gsettings_proxy("'none'\n''\n0\n")
        self.assertEqual(res["mode"], "none")
        self.assertIsNone(res["http_host"])
        self.assertEqual(res["http_port"], 0)

    def test_port_with_gvariant_uint_prefix(self):
        res = health.parse_gsettings_proxy(
            "'manual'\n'127.0.0.1'\nuint32 7890\n")
        self.assertEqual(res["http_port"], 7890)

    def test_port_quoted(self):
        res = health.parse_gsettings_proxy(
            "'manual'\n'192.168.1.5'\n'7890'\n")
        self.assertEqual(res, {"mode": "manual", "http_host": "192.168.1.5",
                               "http_port": 7890})

    def test_blank_lines_tolerated(self):
        res = health.parse_gsettings_proxy(
            "\n'manual'\n\n  \n'127.0.0.1'\n\n7897\n\n")
        self.assertEqual(res["http_port"], 7897)

    def test_empty_text_all_none(self):
        self.assertEqual(health.parse_gsettings_proxy(""),
                         {"mode": None, "http_host": None, "http_port": None})

    def test_garbage_port_is_none(self):
        res = health.parse_gsettings_proxy("'manual'\n'127.0.0.1'\nabc\n")
        self.assertIsNone(res["http_port"])
        self.assertEqual(res["mode"], "manual")

    def test_mode_only(self):
        res = health.parse_gsettings_proxy("'none'\n")
        self.assertEqual(res["mode"], "none")
        self.assertIsNone(res["http_host"])
        self.assertIsNone(res["http_port"])


class TestReadSystemProxy(unittest.TestCase):

    def test_fake_runner_outputs(self):
        outputs = {
            "mode": "'manual'\n",
            "host": "'127.0.0.1'\n",
            "port": "7890\n",
        }
        seen = []

        def fake_runner(cmd):
            seen.append(list(cmd))
            key = cmd[-1]
            return outputs[key]

        res = health.read_system_proxy(fake_runner)
        self.assertTrue(res["available"])
        self.assertEqual(res["mode"], "manual")
        self.assertEqual(res["http_host"], "127.0.0.1")
        self.assertEqual(res["http_port"], 7890)
        self.assertEqual(res["error"], "")
        # 三条命令都是 gsettings get，schema 路径正确
        self.assertEqual(len(seen), 3)
        self.assertEqual(seen[0], ["gsettings", "get",
                                   "org.gnome.system.proxy", "mode"])
        self.assertEqual(seen[1][:3], ["gsettings", "get",
                                       "org.gnome.system.proxy.http"])
        self.assertEqual(seen[2][:3], ["gsettings", "get",
                                       "org.gnome.system.proxy.http"])

    def test_runner_missing_gsettings_degrades(self):
        def missing(cmd):
            raise FileNotFoundError("No such file or directory: 'gsettings'")

        res = health.read_system_proxy(missing)
        self.assertFalse(res["available"])
        self.assertIsNone(res["mode"])
        self.assertIsNone(res["http_port"])
        self.assertIn("FileNotFoundError", res["error"])

    def test_runner_nonzero_exit_degrades(self):
        def failing(cmd):
            raise RuntimeError("gsettings 退出码 1: no schema")

        res = health.read_system_proxy(failing)
        self.assertFalse(res["available"])
        self.assertIn("RuntimeError", res["error"])


class TestProxyConflict(unittest.TestCase):

    def test_manual_pointing_elsewhere_is_conflict(self):
        res = health.proxy_conflict(sysproxy(port=7890), our_http_port=7897)
        self.assertTrue(res["conflict"])
        self.assertIn("7890", res["reason"])
        self.assertIn("7897", res["reason"])

    def test_manual_pointing_at_our_port_not_conflict(self):
        res = health.proxy_conflict(
            sysproxy(port=7897, host="127.0.0.1"), our_http_port=7897)
        self.assertFalse(res["conflict"])
        self.assertIn("一致", res["reason"])

    def test_manual_localhost_alias_not_conflict(self):
        res = health.proxy_conflict(
            sysproxy(port=7897, host="localhost"), our_http_port=7897)
        self.assertFalse(res["conflict"])

    def test_manual_our_port_but_foreign_host_is_conflict(self):
        # 端口撞对了但 host 指别处：引擎只听 127.0.0.1，流量到不了我们
        res = health.proxy_conflict(
            sysproxy(port=7897, host="192.168.1.5"), our_http_port=7897)
        self.assertTrue(res["conflict"])

    def test_manual_other_host_and_port_is_conflict(self):
        res = health.proxy_conflict(
            sysproxy(port=8080, host="192.168.1.5"), our_http_port=7897)
        self.assertTrue(res["conflict"])

    def test_manual_without_host_but_other_port_is_conflict(self):
        # manual + 端口不是引擎端口即冲突（任务口径原文）；host 未设照报
        res = health.proxy_conflict(
            sysproxy(port=7890, host=""), our_http_port=7897)
        self.assertTrue(res["conflict"])
        self.assertIn("未设 host", res["reason"])

    def test_manual_unreadable_port_is_not_conflict(self):
        # 判不了 ≠ 冲突：manual 但端口读不出，不硬判（口径：宁缺勿猜）
        res = health.proxy_conflict(
            sysproxy(port=None), our_http_port=7897)
        self.assertFalse(res["conflict"])
        self.assertIn("读不出", res["reason"])

    def test_mode_none_not_conflict(self):
        res = health.proxy_conflict(sysproxy(mode="none"), our_http_port=7897)
        self.assertFalse(res["conflict"])
        self.assertIn("未开启", res["reason"])

    def test_mode_auto_not_conflict(self):
        res = health.proxy_conflict(sysproxy(mode="auto"), our_http_port=7897)
        self.assertFalse(res["conflict"])

    def test_unavailable_not_conflict(self):
        res = health.proxy_conflict(
            sysproxy(available=False, error="FileNotFoundError"),
            our_http_port=7897)
        self.assertFalse(res["conflict"])
        self.assertIn("读不到", res["reason"])

    def test_none_input_not_conflict(self):
        res = health.proxy_conflict(None, our_http_port=7897)
        self.assertFalse(res["conflict"])

    def test_missing_our_port_raises(self):
        # 引擎端口都没给 = 集成接线错误，当场暴露
        with self.assertRaises(ValueError):
            health.proxy_conflict(sysproxy(), None)


# ---------------------------------------------------------------------------
# 三件套之三：连接健康分级
# ---------------------------------------------------------------------------
class TestConnectionHealth(unittest.TestCase):

    def test_threshold_constants(self):
        self.assertEqual(health.SUSPECT_FAILS, 3)
        self.assertEqual(health.DEAD_FAILS, 5)
        self.assertGreater(health.DEAD_FAILS, health.SUSPECT_FAILS)
        for state in ("ok", "suspect", "dead", "unknown"):
            self.assertIn(state, health.HEALTH_STATES)

    def test_empty_is_unknown(self):
        self.assertEqual(health.connection_health([]), "unknown")

    def test_all_ok(self):
        self.assertEqual(health.connection_health(rows(True, True, True)),
                         "ok")

    def test_single_and_double_failures_still_ok(self):
        self.assertEqual(health.connection_health(rows(False)), "ok")
        self.assertEqual(health.connection_health(rows(True, False)), "ok")
        self.assertEqual(health.connection_health(rows(False, False)), "ok")

    def test_three_failures_suspect(self):
        self.assertEqual(
            health.connection_health(rows(True, False, False, False)),
            "suspect")

    def test_four_failures_still_suspect(self):
        self.assertEqual(
            health.connection_health(rows(False, False, False, False)),
            "suspect")

    def test_five_failures_dead(self):
        self.assertEqual(
            health.connection_health(rows(True, True, False, False, False,
                                          False, False)),
            "dead")

    def test_six_failures_dead(self):
        self.assertEqual(
            health.connection_health(rows(False) * 6), "dead")

    def test_recovery_after_failures_is_ok(self):
        self.assertEqual(
            health.connection_health(rows(False, False, False, True)),
            "ok")

    def test_trailing_streak_after_recovery(self):
        # 恢复后又连败：只看末尾连败
        self.assertEqual(
            health.connection_health(rows(False, False, False, True,
                                          False, False, False)),
            "suspect")

    def test_recovery_resets_streak_before_dead(self):
        # 4 连败后恢复一次，再连败 3 次：连败清零重来，只数末尾 3 次
        self.assertEqual(
            health.connection_health(rows(False, False, False, False,
                                          True, False, False, False)),
            "suspect")

    def test_object_rows_with_ok_attribute(self):
        class Sample:
            def __init__(self, ok):
                self.ok = ok
        self.assertEqual(
            health.connection_health([Sample(False), Sample(False),
                                      Sample(False)]),
            "suspect")

    def test_detail_counts(self):
        detail = health.connection_health_detail(rows(True, False, False))
        self.assertEqual(detail["status"], "ok")
        self.assertEqual(detail["fail_streak"], 2)
        self.assertEqual(detail["sample_count"], 3)
        self.assertIs(detail["last_ok"], False)
        empty = health.connection_health_detail([])
        self.assertEqual(empty["status"], "unknown")
        self.assertIsNone(empty["last_ok"])

    def test_bad_rows_raise(self):
        with self.assertRaises(ValueError):
            health.connection_health([{}])                # 缺 ok
        with self.assertRaises(ValueError):
            health.connection_health([{"ok": 1}])         # 非 bool
        with self.assertRaises(ValueError):
            health.connection_health([{"ok": None}])
        with self.assertRaises(ValueError):
            health.connection_health([object()])          # 无 ok 属性

    def test_bad_input_type_raises(self):
        with self.assertRaises(TypeError):
            health.connection_health(None)
        with self.assertRaises(TypeError):
            health.connection_health("ok,not,a,sample")


if __name__ == "__main__":
    unittest.main()
