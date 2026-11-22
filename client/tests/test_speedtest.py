#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
U4/task19 离线测试（一）：吞吐实测器本体 + 回写 + 展示口径 + 载荷校验。
==========================================================================
假数据 only：opener/时钟/节点全是假件，零真实网络连接、零真实载荷地址。
被测模块从 impl/ 镜像路径独立加载（spec_from_file_location，不建包）。
client-dev 只读参考（settings_proto 锁口径）缺席时自动跳过对应用例。
"""

from __future__ import annotations

import importlib.util
import sys
import time
import types
import unittest
from pathlib import Path

# 集成形态（审查方适配，见文末「集成适配清单」）：本文件落 client/tests/，
# 被测模块与参考模块均取自集成树本身（CLIENT_DEV = 集成树根）。
CLIENT_DEV = Path(__file__).resolve().parent.parent.parent
IMPL_SPEEDTEST = CLIENT_DEV / "client" / "core" / "speedtest.py"

MiB = 1024 * 1024


def _load_module(name: str, path: Path):
    # 先注册 sys.modules 再 exec：被测模块带 from __future__ import
    # annotations，dataclass 处理字符串注解时要按模块名回查 sys.modules。
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


speedtest = _load_module("work19_speedtest", IMPL_SPEEDTEST)


def _settings_proto_ref():
    """集成树内 settings_proto（包导入：W1-b 的相对 import 需包上下文，
    spec 裸加载跑不起来）；缺席返回 None（自动跳过）。"""
    if str(CLIENT_DEV) not in sys.path:
        sys.path.insert(0, str(CLIENT_DEV))
    try:
        from client.core import settings_proto
        return settings_proto
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 假件（opener / 时钟 / 流 / 节点）
# ---------------------------------------------------------------------------
class FakeClock:
    """假时钟：不自动走，由 FakeStream 每次 read 推进固定步长——耗时只
    由读次数决定，与被测模块内部 clock_fn 调用次数解耦（逐档可对账）。"""

    def __init__(self, t0=100.0):
        self.t = float(t0)

    def advance(self, dt):
        self.t += dt

    def __call__(self):
        return self.t


class FakeStream:
    """假流：按预置块序吐字节；可注入每次读推进的假时钟步长与读取报错。"""

    def __init__(self, chunks, clock=None, step=1.0,
                 error_at=None, error=None):
        self.chunks = [bytes(c) for c in chunks]
        self.clock = clock
        self.step = step
        self.error_at = error_at      # 第几次 read 抛 error（1 起）
        self.error = error
        self.reads = 0
        self.closed = False

    def read(self, n):
        self.reads += 1
        if self.clock is not None:
            self.clock.advance(self.step)
        if self.error_at is not None and self.reads == self.error_at:
            raise self.error
        if not self.chunks:
            return b""
        chunk = self.chunks.pop(0)
        return chunk[:n]

    def close(self):
        self.closed = True


class FakeOpener:
    """假 opener：记录调用，按预置行为返回流或抛网络类异常（OSError 族）。"""

    def __init__(self, stream_factory=None, error=None):
        self.stream_factory = stream_factory
        self.error = error
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        if self.error is not None:
            raise self.error
        return self.stream_factory()


def _make_node(speed_mbps=0.0, extra=None):
    """鸭子类型假节点（Node 形：metrics.speed_mbps + extra dict）。"""
    return types.SimpleNamespace(
        metrics=types.SimpleNamespace(speed_mbps=speed_mbps),
        extra=dict(extra or {}),
    )


# ---------------------------------------------------------------------------
# 1. 吞吐实测器：逐档对账 / 中止 / 四类失败
# ---------------------------------------------------------------------------
class TestRunThroughput(unittest.TestCase):
    URL = "http://payload.invalid/speed.bin"

    def _run(self, opener, tier_mb, clock=None, **kw):
        clock = clock or FakeClock()
        return speedtest.run_throughput_test(
            self.URL, opener, tier_mb, clock_fn=clock, **kw), clock

    def test_ok_two_tiers_reconcile(self):
        """5/25 两档逐档对账：字节数=档位、耗时=读次数、Mbps=字节×8/秒。"""
        for tier in speedtest.TIERS_MB:
            clock = FakeClock()
            stream = FakeStream([b"\x01" * MiB] * tier, clock=clock, step=1.0)
            opener = FakeOpener(stream_factory=lambda s=stream: s)
            result, _ = self._run(opener, tier, clock, chunk_size=MiB)
            self.assertEqual(result.status, speedtest.ST_OK)
            self.assertIsNone(result.category)
            self.assertFalse(result.aborted)
            self.assertEqual(result.bytes_downloaded, tier * MiB)
            self.assertAlmostEqual(result.elapsed_s, float(tier), places=6)
            expected = tier * MiB * 8 / (tier * 1e6)   # = 8.388608 Mbps
            self.assertAlmostEqual(result.mbps, expected, places=9)
            self.assertEqual(opener.urls, [self.URL])
            self.assertTrue(stream.closed)             # 测完必关流

    def test_abort_midway_partial_and_write(self):
        """中途置 abort：按已下载部分算 partial，口径正确且可写回。"""
        clock = FakeClock()
        stream = FakeStream([b"\x01" * MiB] * 5, clock=clock, step=1.0)
        opener = FakeOpener(stream_factory=lambda: stream)
        # 读满 2 块后中止（第 3 块前的 abort 检查命中）
        result, _ = self._run(opener, 5, clock, chunk_size=MiB,
                              abort_fn=lambda: stream.reads >= 2)
        self.assertEqual(result.status, speedtest.ST_PARTIAL)
        self.assertEqual(result.category, speedtest.CATEGORY_ABORTED)
        self.assertTrue(result.aborted)
        self.assertEqual(result.bytes_downloaded, 2 * MiB)
        self.assertAlmostEqual(result.elapsed_s, 2.0, places=6)
        expected = 2 * MiB * 8 / (2 * 1e6)
        self.assertAlmostEqual(result.mbps, expected, places=9)
        self.assertTrue(result.measured)
        node = _make_node()
        now = {"t": 1234.5}
        self.assertTrue(speedtest.write_back(node, result, now_fn=lambda: now["t"]))
        self.assertAlmostEqual(node.extra["speed_measured_at"], 1234.5, places=6)

    def test_abort_before_any_byte_is_fail_not_partial(self):
        """零字节中止：无数据不冒充实测 → fail(aborted)，不写回。"""
        result, _ = self._run(FakeOpener(lambda: FakeStream([b"\x01" * MiB] * 5)),
                              5, chunk_size=MiB, abort_fn=lambda: True)
        self.assertEqual(result.status, speedtest.ST_FAIL)
        self.assertEqual(result.category, speedtest.CATEGORY_ABORTED)
        self.assertTrue(result.aborted)
        self.assertIsNone(result.mbps)
        self.assertFalse(result.measured)

    def test_fail_unreachable(self):
        """连不上（opener 阶段 ConnectionRefusedError / 一般 OSError）。"""
        for err in (ConnectionRefusedError("refused"),
                    OSError("no route to host")):
            result, _ = self._run(FakeOpener(error=err), 5)
            self.assertEqual(result.status, speedtest.ST_FAIL)
            self.assertEqual(result.category, speedtest.FAIL_UNREACHABLE)
            self.assertIsNone(result.mbps)
            self.assertEqual(result.bytes_downloaded, 0)

    def test_fail_timeout_connect(self):
        """连接阶段 TimeoutError → timeout。"""
        result, _ = self._run(FakeOpener(error=TimeoutError("t/o")), 5)
        self.assertEqual(result.status, speedtest.ST_FAIL)
        self.assertEqual(result.category, speedtest.FAIL_TIMEOUT)

    def test_fail_timeout_budget(self):
        """超出时间预算 → timeout（失败，不记实测）。"""
        clock = FakeClock()
        stream = FakeStream([b"\x01" * MiB] * 25, clock=clock, step=1.0)
        opener = FakeOpener(stream_factory=lambda: stream)
        result, _ = self._run(opener, 25, clock, chunk_size=MiB, budget_s=2.5)
        self.assertEqual(result.status, speedtest.ST_FAIL)
        self.assertEqual(result.category, speedtest.FAIL_TIMEOUT)
        self.assertEqual(result.bytes_downloaded, 3 * MiB)   # 预算耗尽前下了 3 块
        self.assertFalse(result.measured)

    def test_fail_empty_payload(self):
        """载荷为空：0 字节就干净结束 → empty。"""
        result, _ = self._run(FakeOpener(lambda: FakeStream([])), 5)
        self.assertEqual(result.status, speedtest.ST_FAIL)
        self.assertEqual(result.category, speedtest.FAIL_EMPTY)
        self.assertEqual(result.bytes_downloaded, 0)

    def test_fail_interrupted_premature_eof(self):
        """中途断流（>0 字节后流提前结束）→ interrupted。"""
        result, _ = self._run(
            FakeOpener(lambda: FakeStream([b"\x01" * MiB])), 5, chunk_size=MiB)
        self.assertEqual(result.status, speedtest.ST_FAIL)
        self.assertEqual(result.category, speedtest.FAIL_INTERRUPTED)
        self.assertEqual(result.bytes_downloaded, MiB)

    def test_fail_interrupted_read_error(self):
        """读取阶段 OSError（连接重置）→ 中断（视作中途断流）。"""
        result, _ = self._run(
            FakeOpener(lambda: FakeStream([b"\x01" * MiB] * 5, error_at=2,
                                          error=ConnectionResetError("reset"))),
            5, chunk_size=MiB)
        self.assertEqual(result.status, speedtest.ST_FAIL)
        self.assertEqual(result.category, speedtest.FAIL_INTERRUPTED)
        self.assertEqual(result.bytes_downloaded, MiB)

    def test_config_errors(self):
        """分档不在 TIERS_MB / 载荷地址空或非 http(s)：配置错误如实报。"""
        with self.assertRaises(speedtest.SpeedTestConfigError):
            self._run(FakeOpener(lambda: FakeStream([])), 10)
        for bad in ("", "   ", None, "ftp://x.invalid/b", "payload.invalid",
                    "http:///no-host", "https://"):
            clock = FakeClock()
            with self.assertRaises(speedtest.SpeedTestConfigError):
                speedtest.run_throughput_test(
                    bad, FakeOpener(lambda: FakeStream([])), 5, clock_fn=clock)


# ---------------------------------------------------------------------------
# 2. 回写函数：只有成功 / partial 才写时间戳
# ---------------------------------------------------------------------------
class TestWriteBack(unittest.TestCase):
    def _ok_result(self, mbps=8.388608):
        return speedtest.SpeedTestResult(
            status=speedtest.ST_OK, tier_mb=5, bytes_downloaded=5 * MiB,
            elapsed_s=5.0, mbps=mbps, category=None)

    def _partial_result(self):
        return speedtest.SpeedTestResult(
            status=speedtest.ST_PARTIAL, tier_mb=5, bytes_downloaded=2 * MiB,
            elapsed_s=2.0, mbps=2 * MiB * 8 / 2e6, category=speedtest.CATEGORY_ABORTED,
            aborted=True)

    def _fail_result(self, category):
        return speedtest.SpeedTestResult(
            status=speedtest.ST_FAIL, tier_mb=5, bytes_downloaded=0,
            elapsed_s=1.0, mbps=None, category=category)

    def test_ok_writes_value_and_timestamp(self):
        node = _make_node()
        self.assertTrue(speedtest.write_back(node, self._ok_result(),
                                             now_fn=lambda: 1234.5))
        self.assertEqual(node.metrics.speed_mbps, 8.39)   # round 2
        self.assertEqual(node.extra["speed_measured_at"], 1234.5)

    def test_partial_writes_value_and_timestamp(self):
        node = _make_node()
        self.assertTrue(speedtest.write_back(node, self._partial_result(),
                                             now_fn=lambda: 2000.0))
        self.assertEqual(node.metrics.speed_mbps,
                         round(2 * MiB * 8 / 2e6, 2))
        self.assertEqual(node.extra["speed_measured_at"], 2000.0)

    def test_failures_never_write(self):
        """四类失败 + 零字节中止：不写值、不写时间戳、自报值原样保留。"""
        categories = (speedtest.FAIL_UNREACHABLE, speedtest.FAIL_TIMEOUT,
                      speedtest.FAIL_EMPTY, speedtest.FAIL_INTERRUPTED,
                      speedtest.CATEGORY_ABORTED)
        for cat in categories:
            node = _make_node(speed_mbps=77.0, extra={"keep": "me"})
            result = self._fail_result(cat)
            if cat == speedtest.CATEGORY_ABORTED:
                result = speedtest.SpeedTestResult(
                    status=speedtest.ST_FAIL, tier_mb=5, bytes_downloaded=0,
                    elapsed_s=1.0, mbps=None, category=cat, aborted=True)
            self.assertFalse(speedtest.write_back(node, result,
                                                  now_fn=lambda: 1234.5),
                             msg=f"类别 {cat} 不许写回")
            self.assertEqual(node.metrics.speed_mbps, 77.0)
            self.assertNotIn("speed_measured_at", node.extra)
            self.assertEqual(node.extra.get("keep"), "me")

    def test_zero_elapsed_ok_is_not_writable(self):
        """零耗时测不出可信速率：mbps=None → 不写回（不除零冒充）。"""
        result = speedtest.SpeedTestResult(
            status=speedtest.ST_OK, tier_mb=5, bytes_downloaded=5 * MiB,
            elapsed_s=0.0, mbps=None, category=None)
        node = _make_node()
        self.assertFalse(speedtest.write_back(node, result))


# ---------------------------------------------------------------------------
# 3. 实测/自报三态展示口径
# ---------------------------------------------------------------------------
class TestFormatSpeedDisplay(unittest.TestCase):
    def _node(self, speed_mbps=0.0, measured_at=None):
        extra = {}
        if measured_at is not None:
            extra[speedtest.MEASURED_AT_KEY] = measured_at
        return _make_node(speed_mbps=speed_mbps, extra=extra)

    def test_measured_priority(self):
        """有实测时间戳：显示「实测 X Mbps（时间）」——时间戳在，值即实测。"""
        node = self._node(speed_mbps=55.5, measured_at=1234.5)
        text = speedtest.format_speed_display(node, localtime_fn=time.gmtime)
        self.assertEqual(text, "实测 55.5 Mbps（01-01 00:20）")

    def test_self_report_labeled(self):
        """无实测时间戳但有 feed 自报值：显示「自报」标注，与实测分得清。"""
        node = self._node(speed_mbps=42.5)
        self.assertEqual(speedtest.format_speed_display(node), "自报 42.5 Mbps")

    def test_no_value_dash(self):
        """无值显示「—」（metrics 0 / 缺 metrics / 时间戳配零值都不冒充）。"""
        self.assertEqual(speedtest.format_speed_display(self._node()), "—")
        self.assertEqual(speedtest.format_speed_display(
            self._node(speed_mbps=0.0, measured_at=1234.5)), "—")
        self.assertEqual(speedtest.format_speed_display(
            types.SimpleNamespace(metrics=None, extra={})), "—")

    def test_measured_beats_self_report_value(self):
        """同一节点先有自报值、后被实测覆盖：时间戳在 → 只按实测口径显示。"""
        node = self._node(speed_mbps=99.9, measured_at=1234.5)
        self.assertEqual(speedtest.format_speed_display(node, localtime_fn=time.gmtime),
                         "实测 99.9 Mbps（01-01 00:20）")


# ---------------------------------------------------------------------------
# 4. 载荷地址校验（settings 扩展位与实测器共用的一份口径）
# ---------------------------------------------------------------------------
class TestValidatePayloadUrl(unittest.TestCase):
    def test_valid_http_https(self):
        for url in ("http://payload.invalid/bin",
                    "https://127.0.0.1:9999/payload.bin",
                    "  https://speed.invalid/5mb.bin  "):
            self.assertEqual(speedtest.validate_payload_url(url), url.strip())

    def test_invalid_forms(self):
        for bad in ("", "   ", None, 5, "ftp://x.invalid/b", "payload.invalid",
                    "http:///no-host", "https://"):
            with self.assertRaises(speedtest.SpeedTestConfigError):
                speedtest.validate_payload_url(bad)


# ---------------------------------------------------------------------------
# 5. 监测状态行文案与 task12 Settings 两字段机械锁定（缺席自动跳过）
# ---------------------------------------------------------------------------
class TestMonitorStatusText(unittest.TestCase):
    def test_duck_objects(self):
        """鸭子对象三态：开（含间隔）/ 关 / 读不出。"""
        on = types.SimpleNamespace(auto_probe_enabled=True,
                                   auto_probe_interval_min=5)
        self.assertIn("开", speedtest.monitor_status_text(on))
        self.assertIn("5 分钟", speedtest.monitor_status_text(on))
        off = types.SimpleNamespace(auto_probe_enabled=False,
                                    auto_probe_interval_min=10)
        self.assertIn("关", speedtest.monitor_status_text(off))
        weird = types.SimpleNamespace(auto_probe_enabled=None,
                                      auto_probe_interval_min=0)
        self.assertIn("读不出", speedtest.monitor_status_text(weird))

    def test_lock_to_client_dev_settings(self):
        """与 client-dev settings_proto.Settings 两字段机械锁定（缺席跳过）。"""
        proto = _settings_proto_ref()
        if proto is None:
            self.skipTest("client-dev 参考缺席：settings_proto 锁定用例跳过")
        default = proto.Settings()
        self.assertIn("开", speedtest.monitor_status_text(default))
        self.assertIn("10 分钟", speedtest.monitor_status_text(default))
        off = proto.Settings(auto_probe_enabled=False)
        self.assertIn("关", speedtest.monitor_status_text(off))
        slow = proto.Settings(auto_probe_interval_min=20)
        self.assertIn("20 分钟", speedtest.monitor_status_text(slow))


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# 集成适配清单（审查方，2026-11-22）：
# 1. 本文件落 client/tests/test_speedtest.py；被测模块改为集成树内
#    client/core/speedtest.py（CLIENT_DEV = 集成树根，见文件头）。
# 2. _settings_proto_ref 取集成树内 settings_proto（含 W1 扩展位）。
# 3. 交付形态原件留 work19/（impl 镜像 + work19 相对路径），一字未动。
