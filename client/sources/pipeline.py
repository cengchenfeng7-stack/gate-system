#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
自家流水线来源（sources/pipeline.py）
=======================================
解析 vpngate.py 的产物：
- data.json：build_outputs() 的结构（available[] 为主，字段见 vpngate.py）
- nodes_*.txt：build_nodes_text() 的行格式
    入口#国家-标签-序号$sstp://user:pass@host:port
  以及 subscription.txt 的行格式
    sstp://user:pass@host:port#备注

场景映射（vpngate *_ready → 客户端场景名）：
  support_ready→support / game_ready→games / speed_ready→download / ai_ready→ai
评级只作初始标签；入库后以本地复验为准（设计二·五：可信度由验真决定）。
"""

from __future__ import annotations

import json

from ..core.model import ExitProfile, Node, NodeMetrics, make_node_id
from .manual import parse_link, parse_links_text

_READY_TO_SCENE = (
    ("support_ready", "support"),
    ("game_ready", "games"),
    ("speed_ready", "download"),
    ("ai_ready", "ai"),
)


def node_from_pipeline_dict(n: dict) -> Node:
    host = n.get("host") or ""
    port = int(n.get("port") or 0)
    scenes = {scene for flag, scene in _READY_TO_SCENE if n.get(flag)}

    exit_d = n.get("exit") or {}
    dirty = set()
    if exit_d:
        if exit_d.get("is_abuser"):
            dirty.add("abuser")
        if exit_d.get("is_tor"):
            dirty.add("tor")
        if exit_d.get("is_bogon"):
            dirty.add("bogon")
        if exit_d.get("is_clean") is False:
            dirty.add("dirty_ip")
    profile = ExitProfile(
        ip=exit_d.get("ip"),
        country=exit_d.get("country"),
        country_code=(exit_d.get("country_code") or None),
        network_type=n.get("residential") or "unknown",
        org=exit_d.get("org"),
        asn=str(exit_d["asn"]) if exit_d.get("asn") is not None else None,
        is_clean=n.get("is_clean") if n.get("is_clean") is not None
                 else exit_d.get("is_clean"),
        dirty=dirty,
    )
    country = n.get("country") or ""
    cc = (n.get("country_code") or "").upper()
    name = f"{host}:{port}" + (f" ({cc})" if cc else "")
    return Node(
        id=make_node_id("pipeline", n.get("protocol") or "sstp", host, port),
        name=name,
        protocol=n.get("protocol") or "sstp",
        source="pipeline",
        host=host,
        port=port,
        config_ref=n.get("link") or f"sstp://vpn:vpn@{host}:{port}",
        exit_profile=profile,
        scenes=scenes,
        metrics=NodeMetrics(
            latency_ms=n.get("latency_ms"),
            jitter_ms=n.get("jitter_ms"),
            uptime_hours=n.get("uptime_hours"),
            sessions=n.get("sessions") or 0,
            speed_mbps=n.get("speed_mbps") or 0.0,
        ),
        pinned=bool(n.get("pinned")),
        extra={
            "country": country,
            "country_code": cc,
            "entry_ip": n.get("ip"),
            "tier": n.get("tier"),
            "composite_score": n.get("composite_score"),
            "checked_at": n.get("checked_at"),
        },
    )


def parse_data_json(text: str) -> list:
    """data.json 文本 → Node 列表（以 available[] 为准）。"""
    data = json.loads(text)
    nodes = [node_from_pipeline_dict(n) for n in (data.get("available") or [])]
    return nodes


def load_data_json_file(path: str) -> list:
    with open(path, "r", encoding="utf-8") as f:
        return parse_data_json(f.read())


def parse_nodes_text(text: str) -> list:
    """nodes_*.txt / subscription.txt 文本 → Node 列表（source=pipeline）。"""
    return parse_links_text(text, source="pipeline")


def load_nodes_text_file(path: str) -> list:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse_nodes_text(f.read())
