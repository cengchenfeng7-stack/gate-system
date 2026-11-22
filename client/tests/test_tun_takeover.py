#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检测④接管生效性离线测试（task6 集成，v0.10.0）
================================================
check_tun_takeover 只读解析 /proc/net/route 文本：默认路由走 gate-tun
才算接管生效。测试用注入文本，不读真实路由表（除一项默认路径冒烟）。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.health import check_tun_takeover

HEADER = ("Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\t"
          "MTU\tWindow\tIRTT\n")


def route_text(*rows):
    return HEADER + "".join(rows)


class TestTunTakeover(unittest.TestCase):
    def test_default_route_via_gate_tun_is_effective(self):
        text = route_text(
            "gate-tun\t00000000\t00000000\t0003\t0\t0\t0\t00000000\t0\t0\t0\n",
            "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n")
        result = check_tun_takeover(route_text=text)
        self.assertTrue(result["effective"])
        self.assertIn("gate-tun", result["reason"])

    def test_default_route_elsewhere_is_not_effective(self):
        text = route_text(
            "gate-tun\t001213AC\t00000000\t0003\t0\t0\t0\t00FFFFFF\t0\t0\t0\n",
            "eth0\t00000000\t0101A8C0\t0003\t0\t0\t100\t00000000\t0\t0\t0\n")
        result = check_tun_takeover(route_text=text)
        self.assertFalse(result["effective"])
        self.assertIn("未接管", result["reason"])

    def test_no_default_route_at_all(self):
        text = route_text(
            "eth0\t001213AC\t0101A8C0\t0003\t0\t0\t100\t00FFFFFF\t0\t0\t0\n")
        result = check_tun_takeover(route_text=text)
        self.assertFalse(result["effective"])

    def test_empty_and_garbage_text(self):
        self.assertFalse(check_tun_takeover(route_text="")["effective"])
        self.assertFalse(
            check_tun_takeover(route_text="not a route table\n")["effective"])

    def test_custom_interface_name(self):
        text = route_text(
            "tun9\t00000000\t00000000\t0003\t0\t0\t0\t00000000\t0\t0\t0\n")
        self.assertTrue(
            check_tun_takeover(route_text=text, interface_name="tun9")["effective"])
        self.assertFalse(
            check_tun_takeover(route_text=text)["effective"])

    def test_unreadable_route_file_reports_not_effective(self):
        result = check_tun_takeover(route_path="/proc/net/route-no-such-file")
        self.assertFalse(result["effective"])
        self.assertIn("读不到", result["reason"])

    def test_real_proc_route_parses_without_error(self):
        # 默认路径冒烟：只要求返回 dict、不抛错；本机有无 gate-tun 都合法。
        result = check_tun_takeover()
        self.assertIn("effective", result)
        self.assertIn("reason", result)


if __name__ == "__main__":
    unittest.main()
