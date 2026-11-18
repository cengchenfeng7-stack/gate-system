#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
会话管理（core/orchestrator.py）—— 铁律执行者
================================================
写死的规矩（设计第三节 6，不给自动行为留口子）：
1. 同一时刻只允许一个活动节点；已有活动连接时再 connect 直接拒绝。
2. 连接必须由显式 node_id 发起。本类**故意不提供**任何
   connect_best / connect_next / auto_switch 之类的方法。
3. 引擎启动后必须调 verifier 复核真实出口；复核失败 → 立刻断开
   并产出 verify_failed 事件（防假连：面板显示连上、实际没出隧道）。
4. 巡检发现节点掉线 → 只上报 dropped 事件并回到空闲，**绝不自动连
   其他节点**，等用户自己选下一个。

引擎与 verifier 都由外部注入（接口见 engines/base.py 与
core/verify.py 的 verify_exit），因此本模块可完全离线测试。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..engines.base import Engine, EngineError
from . import history
from .model import Node
from .store import Store
from .verify import VerifyOutcome

# verifier 签名：verifier(node) -> VerifyOutcome
VerifierFn = Callable[[Node], VerifyOutcome]


@dataclass
class SessionEvent:
    kind: str        # connect_started/engine_started/connected/verify_failed/
                     # disconnected/dropped/error
    node_id: str = ""
    detail: str = ""
    ts: float = field(default_factory=time.time)


class OrchestratorError(Exception):
    """会话层可预期失败（已有活动连接、节点被 ban、无对应引擎…）。"""


class Orchestrator:
    IDLE = "idle"
    CONNECTED = "connected"

    def __init__(self, store: Store, engines: dict, verifier: VerifierFn,
                 on_event: Optional[Callable[[SessionEvent], None]] = None):
        self.store = store
        self.engines = dict(engines)       # {protocol: Engine}
        self.verifier = verifier
        self.on_event = on_event
        self.state = self.IDLE
        self.active_node_id: Optional[str] = None
        self._engine: Optional[Engine] = None
        self._handle = None
        self.events: list = []             # 本进程内事件流水（同时落 history 样本）

    # ---------------- 内部 ----------------
    def _emit(self, kind: str, node_id: str = "", detail: str = "") -> SessionEvent:
        ev = SessionEvent(kind=kind, node_id=node_id, detail=detail)
        self.events.append(ev)
        if self.on_event:
            self.on_event(ev)
        return ev

    def _teardown_engine(self):
        if self._engine is not None and self._handle is not None:
            try:
                self._engine.stop(self._handle)
            except Exception:
                pass
        self._engine = None
        self._handle = None

    # ---------------- 对外 ----------------
    def status(self) -> dict:
        return {"state": self.state, "active_node_id": self.active_node_id}

    def connect(self, node_id: str) -> SessionEvent:
        """
        连接指定节点（只能由调用方显式给出 node_id）。
        成功返回 connected 事件；任何失败都返回/抛出带明确原因的结果，
        且保证失败后回到 IDLE、不留半开的隧道。
        """
        if self.state != self.IDLE:
            raise OrchestratorError(
                f"已有活动连接（{self.active_node_id}），先断开再连别的节点")
        node = self.store.get_node(node_id)
        if node is None:
            raise OrchestratorError(f"节点不存在: {node_id}")
        if node.banned:
            raise OrchestratorError(f"节点已被拉黑（ban），拒绝连接: {node_id}")
        engine = self.engines.get(node.protocol)
        if engine is None:
            raise OrchestratorError(
                f"协议 {node.protocol} 暂无引擎（M1 仅 OpenVPN，sing-box 在 M2）: {node_id}")

        self._emit("connect_started", node_id)
        try:
            handle = engine.start(node)
        except EngineError as exc:
            history.record(self.store, node_id, "connect", False, detail=str(exc))
            return self._emit("error", node_id, f"引擎启动失败: {exc}")
        self._engine, self._handle = engine, handle
        self._emit("engine_started", node_id)

        # 铁律 3：连接后强制验出口，验不过 = 假连，立刻断开
        outcome = self.verifier(node)
        if not outcome.ok:
            self._teardown_engine()
            self.state = self.IDLE
            self.active_node_id = None
            history.record(self.store, node_id, "connect", False,
                           detail=f"验出口失败: {outcome.reason}")
            return self._emit("verify_failed", node_id,
                              f"已断开（防假连）：{outcome.reason}")

        from .verify import apply_outcome
        apply_outcome(node, outcome)
        self.store.upsert_node(node)
        self.state = self.CONNECTED
        self.active_node_id = node_id
        history.record(self.store, node_id, "connect", True,
                       latency_ms=node.metrics.latency_ms,
                       exit_ip=node.exit_profile.ip, detail="验出口通过")
        return self._emit("connected", node_id,
                          f"出口 {node.exit_profile.ip} / {node.exit_profile.country_code}")

    def disconnect(self) -> SessionEvent:
        if self.state == self.IDLE:
            return self._emit("disconnected", "", "本来就没有活动连接")
        node_id = self.active_node_id or ""
        self._teardown_engine()
        self.state = self.IDLE
        self.active_node_id = None
        history.record(self.store, node_id, "disconnect", True, detail="用户断开")
        return self._emit("disconnected", node_id)

    def poll(self) -> Optional[SessionEvent]:
        """
        巡检：活动引擎若已掉线，只上报 dropped 并回到 IDLE。
        **不做任何自动重连/自动切换**——选下一个节点是用户的动作。
        无事发生返回 None。
        """
        if self.state != self.CONNECTED or self._engine is None:
            return None
        if self._engine.status(self._handle) == "running":
            return None
        node_id = self.active_node_id or ""
        self._teardown_engine()
        self.state = self.IDLE
        self.active_node_id = None
        history.record(self.store, node_id, "drop", False, detail="引擎进程已退出")
        return self._emit("dropped", node_id, "节点掉线，已停在原地（不会自动切换）")
