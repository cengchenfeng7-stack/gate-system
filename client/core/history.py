#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
养鱼池（core/history.py）
===========================
按节点累积样本，算稳定性指标，只负责排序和展示——不替用户做选择
（设计第三节 7）。波动率口径与 vpngate.calc_volatility 一致（标准差/均值）。
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from .model import Node
from .store import Store, VerifySample
from .verify import STABILITY_THRESHOLD


def record(store: Store, node_id: str, kind: str, success: bool,
           latency_ms: Optional[float] = None, jitter_ms: Optional[float] = None,
           exit_ip: Optional[str] = None, detail: str = "", ts: float = 0.0) -> None:
    store.add_sample(VerifySample(node_id=node_id, kind=kind, success=success, ts=ts,
                                  latency_ms=latency_ms, jitter_ms=jitter_ms,
                                  exit_ip=exit_ip, detail=detail))


@dataclass
class NodeStats:
    total: int = 0
    success_count: int = 0
    success_rate: float = 0.0
    median_latency_ms: Optional[float] = None
    volatility: Optional[float] = None     # 延迟波动率；None=样本不足
    stable: bool = False                   # volatility < STABILITY_THRESHOLD
    active_days: int = 0                   # 有成功样本的自然日数（UTC）
    streak_days: int = 0                   # 以最近样本日结尾的连续成功天数（近似）
    last_ok: Optional[bool] = None         # 最近一条样本成败；None=从未测过

    @property
    def fail_count(self) -> int:
        return self.total - self.success_count


def calc_volatility(latencies) -> Optional[float]:
    """复制自 vpngate.calc_volatility：样本 <2 或均值 0 → None。"""
    if len(latencies) < 2:
        return None
    mean = statistics.mean(latencies)
    if mean == 0:
        return None
    return statistics.stdev(latencies) / mean


def compute_stats(samples) -> NodeStats:
    """samples: VerifySample 列表（任意 kind，只统计 success 语义明确的样本）。"""
    total = len(samples)
    ok = [s for s in samples if s.success]
    lats = [s.latency_ms for s in ok if s.latency_ms is not None]
    vol = calc_volatility(lats)
    days = sorted({datetime.fromtimestamp(s.ts, tz=timezone.utc).date() for s in ok})
    streak = 0
    if days:
        streak = 1
        for i in range(len(days) - 1, 0, -1):
            if (days[i] - days[i - 1]).days == 1:
                streak += 1
            else:
                break
    # 最近成败只看链路探测（connect/verify），与界面「最近」列同口径：
    # disconnect 是用户主动断开、drop 是巡检告警，都不代表「节点能用」。
    link_samples = [s for s in samples if s.kind in ("connect", "verify")]
    last_ok = bool(link_samples[-1].success) if link_samples else None
    return NodeStats(
        total=total,
        success_count=len(ok),
        success_rate=(len(ok) / total) if total else 0.0,
        median_latency_ms=statistics.median(lats) if lats else None,
        volatility=vol if vol is None else round(vol, 3),
        stable=bool(vol is not None and vol < STABILITY_THRESHOLD),
        active_days=len(days),
        streak_days=streak,
        last_ok=last_ok,
    )


def format_latency_ms(ms: Optional[float]) -> str:
    """延迟毫秒数 → 人能一眼读懂的显示（界面「延迟」列专用）。

    原始毫秒直接摆出来会出现 5745ms、23147ms 这种没法读的数：
    小于 1 秒显示毫秒，1 秒以上换算成秒（<10 秒带一位小数，再大取整）。
    """
    if ms is None:
        return "—"
    if ms < 1000:
        return f"{ms:.0f}ms"
    if ms < 10000:
        return f"{ms / 1000:.1f} 秒"
    return f"{ms / 1000:.0f} 秒"


def stats_for(store: Store, node_id: str) -> NodeStats:
    return compute_stats(store.samples_for(node_id))


def last_result(store: Store, node_id: str):
    """最近一次 connect/verify 样本的 (成功与否, 时间戳)；没测过返回 None。
    给界面「最近」列用——一眼看出这个节点上次测到底通没通。"""
    samples = [s for s in store.samples_for(node_id)
               if s.kind in ("connect", "verify")]
    if not samples:
        return None
    s = samples[-1]
    return (bool(s.success), s.ts)


def sort_key(node: Node, stats: NodeStats) -> tuple:
    """
    同场景排序键（升序）：置顶优先 → 最近成功 → 从未测过 → 最近失败，
    组内成功率高的在前、延迟中位数低的在前、活跃天数多的在前、名称字典序。
    「最近成功」的节点永远排在没测过/刚测挂的前面（2026-11-21 用户需求：
    能用的节点一直放前面）；人工置顶仍是最高优先（显式意愿 > 自动排名）。
    只决定展示顺序；连接永远由用户点选（铁律）。
    """
    return (
        0 if node.pinned else 1,
        0 if stats.last_ok else (1 if stats.last_ok is None else 2),
        -stats.success_rate,
        stats.median_latency_ms if stats.median_latency_ms is not None else float("inf"),
        -stats.active_days,
        node.name,
    )


def sorted_nodes(store: Store, nodes) -> list:
    return sorted(nodes, key=lambda n: sort_key(n, stats_for(store, n.id)))
