#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
引擎统一接口（engines/base.py）
=================================
所有协议引擎（OpenVPN、未来的 sing-box）都实现同一组方法，
orchestrator 只认这个接口。引擎只管“把某个节点连上/断开”，
不管选哪个节点——选择权在用户（铁律）。

status(handle) 返回值约定：
  "running"  隧道已建立且进程存活
  "stopped"  进程已退出/从未启动
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol


class EngineError(Exception):
    """引擎层可预期失败（缺二进制、配置坏、握手失败…）。消息可直接展示给用户。"""


@dataclass
class EngineEvent:
    kind: str                    # connected / dropped / failed / env_error / log
    detail: str = ""


EventCallback = Callable[[EngineEvent], None]


class Engine(Protocol):
    name: str

    def start(self, node, on_event: Optional[EventCallback] = None):
        """启动并等到隧道建立，返回 handle。失败抛 EngineError。"""
        ...

    def stop(self, handle) -> None:
        """断开 handle 对应的隧道。重复调用无害。"""
        ...

    def status(self, handle) -> str:
        """返回 "running" / "stopped"。"""
        ...
