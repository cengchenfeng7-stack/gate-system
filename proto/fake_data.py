#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原型假数据与显示口径（纯 Python，不依赖 Qt、不 import client 包）。

显示口径按 DESIGN.md §2 改造项实现：
- format_latency：0.3.2 口径（<1s 毫秒、≥1s 秒），修 G16 裸毫秒残留；
- format_recent：「最近」列带日期（M/D HH:MM），修 G6 只有 HH:MM；
- 空值统一「—」，修 G22 五种空值口径。

节点数据全部虚构：IP 用 RFC 5737 保留段（192.0.2.0/24、198.51.100.0/24、
203.0.113.0/24），域名 example 系，名称虚构，不含任何真实节点/订阅/密钥。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from typing import List, Optional

# —— 显示口径（对应审计 G6 / G16 / G22） ——

def format_latency(ms: Optional[float]) -> str:
    """延迟毫秒 → 人话：<1s 毫秒，≥1s 秒（<10s 一位小数）；无值「—」。"""
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 10000:
        return f"{ms / 1000:.1f} 秒"
    return f"{ms / 1000:.0f} 秒"


def format_recent(ts: Optional[float], now: Optional[float] = None) -> str:
    """「最近」列口径：始终带日期「M/D HH:MM」；从没测过「—」。
    对照现窗 bug（app.py:316 只有 %H:%M）：隔夜样本不再与今天混淆。"""
    if ts is None:
        return "—"
    lt = time.localtime(ts)
    return f"{lt.tm_mon}/{lt.tm_mday} {lt.tm_hour:02d}:{lt.tm_min:02d}"


SCENE_LABELS = {"support": "客服", "ai": "AI", "games": "游戏", "download": "下载"}
NETWORK_LABELS = {"residential": "住宅", "datacenter": "机房", "unknown": "未知"}


@dataclass
class FakeNode:
    """一行假节点（字段形状仿现窗表格要展示的口径，纯呈现用）。"""
    nid: str
    name: str
    protocol: str
    country_code: str
    ip: str                      # RFC 5737 假 IP
    network_type: str            # residential / datacenter / unknown
    clean: Optional[bool]        # True 干净 / False 脏 / None 未验
    dirty: tuple = ()
    latency_ms: Optional[float] = None   # 中位延迟；None=无样本
    success_rate: Optional[float] = None  # 0~1；None=无样本
    stable: bool = False
    last_ts: Optional[float] = None      # 最近一次测的时间戳；None=未测
    last_ok: Optional[bool] = None
    scenes: tuple = ()
    pinned: bool = False
    banned: bool = False
    quarantined: bool = False
    active: bool = False

    def marks(self) -> str:
        """标记列（修 G23：状态符号出名称列，独立成列带语义）。"""
        s = ""
        if self.active:
            s += "●"
        if self.pinned:
            s += "▲"
        if self.banned:
            s += "⛔"
        if self.quarantined:
            s += "☠"
        return s or "—"

    def mark_tip(self) -> str:
        tips = []
        if self.active:
            tips.append("● 当前连接")
        if self.pinned:
            tips.append("▲ 已置顶")
        if self.banned:
            tips.append("⛔ 已拉黑")
        if self.quarantined:
            tips.append("☠ 已隔离（污染）")
        return "；".join(tips) or "无标记"

    def exit_text(self) -> str:
        return f"{self.country_code} {self.ip}" if self.ip else "—"

    def type_text(self) -> str:
        return NETWORK_LABELS.get(self.network_type, self.network_type)

    def clean_text(self) -> str:
        if self.clean is True:
            return "干净"
        if self.clean is False:
            return "脏（" + ",".join(sorted(self.dirty)) + "）" if self.dirty else "脏"
        return "—"          # 修 G22：原「未知」

    def latency_text(self) -> str:
        return format_latency(self.latency_ms)

    def stability_text(self) -> str:
        if self.success_rate is None:
            return "—"       # 修 G22：原「无样本」
        return f"{self.success_rate:.0%}" + (" · 稳定" if self.stable else "")

    def recent_text(self) -> str:
        if self.last_ts is None:
            return "—"       # 修 G22：原「未测」
        ok = "可连" if self.last_ok else "不通"
        return f"{ok} {format_recent(self.last_ts)}"

    def scenes_text(self) -> str:
        return "/".join(SCENE_LABELS.get(s, s) for s in sorted(self.scenes)) or "—"


def _days_ago(days: int, hour: int, minute: int) -> float:
    """造一个「days 天前 hour:minute」的时间戳（演示隔夜/隔日样本）。"""
    import datetime as dt
    t = dt.datetime.now().replace(hour=hour, minute=minute, second=0,
                                  microsecond=0) - dt.timedelta(days=days)
    return t.timestamp()


def make_fake_nodes() -> List[FakeNode]:
    """12 个虚构节点，覆盖各种呈现分支（活动/置顶/拉黑/隔离/脏/无样本/
    高延迟/隔夜样本）。IP 全部 RFC 5737 保留段，名称虚构。"""
    return [
        FakeNode("f01", "示例节点 · 甲", "vless", "JP", "192.0.2.11",
                 "residential", True, latency_ms=182, success_rate=0.98,
                 stable=True, last_ts=_days_ago(0, 9, 41), last_ok=True,
                 scenes=("support", "ai"), pinned=True),
        FakeNode("f02", "示例节点 · 乙（活动）", "ss", "SG", "192.0.2.12",
                 "datacenter", True, latency_ms=340, success_rate=0.95,
                 stable=True, last_ts=_days_ago(0, 10, 2), last_ok=True,
                 scenes=("games",), active=True),
        FakeNode("f03", "示例节点 · 丙", "trojan", "US", "198.51.100.21",
                 "datacenter", False, dirty=("muse",), latency_ms=1260,
                 success_rate=0.61, last_ts=_days_ago(0, 8, 15), last_ok=True,
                 scenes=("download",)),
        FakeNode("f04", "示例节点 · 丁（高延迟）", "ss", "DE", "198.51.100.22",
                 "residential", True, latency_ms=5745, success_rate=0.72,
                 last_ts=_days_ago(1, 22, 3), last_ok=True, scenes=("ai",)),
        FakeNode("f05", "示例节点 · 戊（隔离）", "vmess", "NL", "198.51.100.23",
                 "datacenter", False, dirty=("google", "facebook"),
                 success_rate=0.44, last_ts=_days_ago(1, 14, 27),
                 last_ok=False, quarantined=True),
        FakeNode("f06", "示例节点 · 己（已拉黑）", "vless", "KR",
                 "198.51.100.24", "unknown", None, last_ts=_days_ago(2, 11, 5),
                 last_ok=False, banned=True),
        FakeNode("f07", "示例节点 · 庚（无样本）", "ss", "GB", "203.0.113.31",
                 "unknown", None),
        FakeNode("f08", "示例节点 · 辛", "vless", "JP", "203.0.113.32",
                 "residential", True, latency_ms=890, success_rate=0.90,
                 stable=True, last_ts=_days_ago(3, 19, 48), last_ok=True,
                 scenes=("support",)),
        FakeNode("f09", "示例节点 · 壬（不通）", "trojan", "US",
                 "203.0.113.33", "datacenter", True, latency_ms=None,
                 success_rate=0.10, last_ts=_days_ago(0, 7, 30), last_ok=False),
        FakeNode("f10", "示例节点 · 癸", "ss", "SG", "192.0.2.13",
                 "residential", True, latency_ms=230, success_rate=0.88,
                 last_ts=_days_ago(0, 9, 12), last_ok=True, scenes=("ai",)),
        FakeNode("f11", "示例节点 · 子（脏）", "vmess", "DE",
                 "203.0.113.34", "datacenter", False, dirty=("muse",),
                 latency_ms=410, success_rate=0.83, last_ts=_days_ago(1, 6, 55),
                 last_ok=True, scenes=("download",)),
        FakeNode("f12", "示例节点 · 丑（超 10 秒延迟）", "vless", "NL",
                 "192.0.2.14", "residential", True, latency_ms=23147,
                 success_rate=0.55, last_ts=_days_ago(1, 3, 8), last_ok=True),
    ]


def recommend_key(n: FakeNode):
    """「推荐序」（默认档）：置顶优先 → 最近成功 → 未测 → 最近失败，
    组内成功率高的在前、延迟低的在前。口径与 history.sorted_nodes 同形
    （原型内自造同形键，不 import client）。"""
    return (
        0 if n.pinned else 1,
        0 if n.last_ok else (1 if n.last_ok is None else 2),
        -(n.success_rate if n.success_rate is not None else -1.0),
        n.latency_ms if n.latency_ms is not None else float("inf"),
        n.name,
    )


def latency_key(n: FakeNode):
    return (n.latency_ms if n.latency_ms is not None else float("inf"),
            n.name)


def recent_ok_key(n: FakeNode):
    return (-(n.last_ts or 0), n.name)


SORT_MODES = (("推荐序", recommend_key), ("延迟 ↑", latency_key),
              ("最近成功 ↓", recent_ok_key))
