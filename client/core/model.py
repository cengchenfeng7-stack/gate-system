#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一节点模型（core/model.py）
==============================
所有来源（pipeline / feed / manual / 未来的 subscription）进门先翻译成
同一个 Node 结构，核心层只认它。设计见《客户端设计-v1》第三节 1。

场景命名：客户端统一用 support / ai / games / download；
与 vpngate.py 的 *_ready 标志映射为 support_ready / ai_ready /
game_ready→games / speed_ready→download（见 sources/pipeline.py）。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from typing import Optional

SCENES = ("support", "ai", "games", "download")

# 来源 = 第一组织维度（用户定，2026-11-19）：独立“页面”，一个节点
# 只归属它进来的那一页，协议重叠也不合并。四场景只是页内徽标/过滤。
# task8/task23 落页（work8/DESIGN §1）：OpenVPN 页与 VPNGate feed 页本
# 是同一份 feed，合并为「VPNGate 来源页」（canonical 键 vpngate，旧两键
# openvpn / vpngate_feed 退役，存量由 sources/landing.py 启动迁移）。
SOURCES = ("vpngate", "manual", "pipeline")
SOURCE_LABELS = {
    "vpngate": "VPNGate 来源页",
    "manual": "手动导入页",
    "pipeline": "自家流水线产物页",
}

NETWORK_TYPES = ("residential", "datacenter", "unknown")


def make_node_id(source: str, protocol: str, host: str, port: int,
                 discriminator: str = "") -> str:
    """
    稳定节点 ID，来源是第一段：同一端点从不同来源进来 = 两条独立记录
    （页面不合并）；同来源同端点同凭证重复导入 → 同 ID（upsert 不产生副本）。
    """
    base = f"{source}:{protocol}:{host}:{port}"
    if discriminator:
        digest = hashlib.sha1(discriminator.encode("utf-8")).hexdigest()[:8]
        return f"{base}:{digest}"
    return base


@dataclass
class ExitProfile:
    """实测出口画像。全部来自本地验真，不采信节点自报。"""
    ip: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    network_type: str = "unknown"          # residential / datacenter / unknown
    org: Optional[str] = None
    asn: Optional[str] = None
    is_clean: Optional[bool] = None        # None=未知；False=脏（滥用/Tor/虚假段等）
    dirty: set = field(default_factory=set)  # 拒绝过此出口的目标名，如 {"muse"}
    verified_at: Optional[float] = None    # epoch 秒

    def to_dict(self) -> dict:
        d = asdict(self)
        d["dirty"] = sorted(self.dirty)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ExitProfile":
        d = dict(d or {})
        d["dirty"] = set(d.get("dirty") or [])
        if d.get("network_type") not in NETWORK_TYPES:
            d["network_type"] = "unknown"
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class NodeMetrics:
    """最近一次测得的性能指标快照（None = 未知，绝不当 0 用，同 vpngate R6）。"""
    latency_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    uptime_hours: Optional[float] = None
    sessions: int = 0
    speed_mbps: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "NodeMetrics":
        d = dict(d or {})
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class Node:
    id: str
    name: str
    protocol: str                          # openvpn / sstp / vless / ss / trojan ...
    source: str                            # SOURCES 之一：vpngate/manual/pipeline
    host: str = ""
    port: int = 0
    # 原始配置载荷：openvpn=b64 或明文（见 extra["config_format"]）、
    # sstp/vless/ss/trojan=分享链接原文
    config_ref: str = ""
    exit_profile: ExitProfile = field(default_factory=ExitProfile)
    scenes: set = field(default_factory=set)   # 子集 of SCENES
    metrics: NodeMetrics = field(default_factory=NodeMetrics)
    pinned: bool = False                   # 人工置顶（吸收 pin.txt 语义）
    banned: bool = False                   # 人工拉黑（吸收 ban.txt 语义）
    extra: dict = field(default_factory=dict)  # 协议特有字段（uuid/密码/传输参数等）

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "protocol": self.protocol,
            "source": self.source,
            "host": self.host,
            "port": self.port,
            "config_ref": self.config_ref,
            "exit_profile": self.exit_profile.to_dict(),
            "scenes": sorted(self.scenes),
            "metrics": self.metrics.to_dict(),
            "pinned": self.pinned,
            "banned": self.banned,
            "extra": self.extra,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Node":
        return cls(
            id=d["id"],
            name=d.get("name") or d["id"],
            protocol=d.get("protocol") or "unknown",
            source=d.get("source") or "manual",
            host=d.get("host") or "",
            port=int(d.get("port") or 0),
            config_ref=d.get("config_ref") or "",
            exit_profile=ExitProfile.from_dict(d.get("exit_profile") or {}),
            scenes={s for s in (d.get("scenes") or []) if s in SCENES},
            metrics=NodeMetrics.from_dict(d.get("metrics") or {}),
            pinned=bool(d.get("pinned")),
            banned=bool(d.get("banned")),
            extra=dict(d.get("extra") or {}),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)

    @classmethod
    def from_json(cls, s: str) -> "Node":
        return cls.from_dict(json.loads(s))
