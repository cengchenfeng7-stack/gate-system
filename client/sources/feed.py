#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
VPN Gate feed 来源（sources/feed.py）
=======================================
把官方 feed（CSV 或镜像 JSON）翻译成 Node 列表（OpenVPN 协议），
归属「VPN Gate feed 页」（source=vpngate_feed）——底层虽是 OpenVPN，
按来源第一维度独立成页，不与 OpenVPN 页合并（用户定，2026-11-19）。

复用说明（按设计“不重造轮子”，以下函数为复制并注明出处，
因客户端要求纯标准库、且不能依赖流水线目录而 vendored）：
- parse_csv / parse_mirror_json / _safe_int / _field
    复制自 gate-system/frontend/vpngate.py（逻辑未改）
- decode_feed_bytes / parse_feed_text / rows_to_feed_configs /
  decode_config / ovpn_proto_port
    复制自 gate-system/frontend/vpn_dial.py（逻辑未改，仅 parse_feed_text
    改为调用本模块内的 parse_* 而非 vp.*）
本模块只做解析，不发网络请求（在线拉取由调用方负责，离线可测）。
"""

from __future__ import annotations

import base64
import csv
import io
import json
import re

from ..core.model import Node, NodeMetrics, make_node_id

# ---------------------------------------------------------------------------
# 复制自 vpngate.py（_safe_int / _field / parse_csv / parse_mirror_json）
# ---------------------------------------------------------------------------
def _safe_int(val, default=0) -> int:
    try:
        return int(float(str(val).strip()))
    except Exception:
        return default


def _field(fields, idx, default=""):
    """安全取列：缺列/越界返回默认值，绝不因字段数量抛异常"""
    if 0 <= idx < len(fields):
        return fields[idx].strip()
    return default


# R1: 不跳行、不抬门槛；兼容 "*" 前缀数据行与缺列旧格式 feed
def parse_csv(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header_idx = None
    for i, ln in enumerate(lines):
        if ln.lstrip("#*").lower().startswith("hostname"):
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError("找不到 CSV 表头行 (HostName)")

    header = [h.strip().lstrip("*").lower() for h in lines[header_idx].lstrip("#*").split(",")]
    data_lines = lines[header_idx + 1:]

    def col_idx(name, fallback):
        return header.index(name) if name in header else fallback

    pos = {
        "hostname": col_idx("hostname", 0),
        "ip": col_idx("ip", 1),
        "score": col_idx("score", 2),
        "ping": col_idx("ping", 3),
        "speed": col_idx("speed", 4),
        "countrylong": col_idx("countrylong", 5),
        "countryshort": col_idx("countryshort", 6),
        "sessions": col_idx("numvpnsessions", 7),
        "uptime": col_idx("uptime", 8),
        "config_b64": col_idx("openvpn_configdata_base64", len(header) - 1),
    }

    rows = []
    for ln in data_lines:
        ln = ln.lstrip("*")  # 兼容 "*" 前缀行（不再整行跳过）
        try:
            fields = next(csv.reader(io.StringIO(ln)))
        except Exception:
            continue
        if len(fields) < 7:  # 与原版基线一致的最低门槛
            continue
        host = _field(fields, pos["hostname"])
        ip = _field(fields, pos["ip"])
        if not host or not ip:
            continue
        rows.append({
            "host": host,
            "ip": ip,
            "score": _safe_int(_field(fields, pos["score"], "0"), 0),
            "ping": _safe_int(_field(fields, pos["ping"], "999"), 999),
            "speed_bps": _safe_int(_field(fields, pos["speed"], "0"), 0),
            "country_long": _field(fields, pos["countrylong"]),
            "country_short": _field(fields, pos["countryshort"]).upper(),
            "sessions": _safe_int(_field(fields, pos["sessions"], "0"), 0),
            "uptime_ms": _safe_int(_field(fields, pos["uptime"], "0"), 0),
            "config_b64": _field(fields, pos["config_b64"]),
        })
    return rows


def parse_mirror_json(data):
    servers = []
    items = data if isinstance(data, list) else [data]
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("servers"), list):
            servers.extend(item["servers"])
        elif isinstance(item, dict):
            servers.append(item)
    rows = []
    for s in servers:
        host = str(s.get("hostname") or s.get("host") or "").strip()
        ip = str(s.get("ip") or "").strip()
        if not host or not ip:
            continue
        rows.append({
            "host": host,
            "ip": ip,
            "score": _safe_int(s.get("score"), 0),
            "ping": _safe_int(s.get("ping"), 999),
            "speed_bps": _safe_int(s.get("speed"), 0),
            "country_long": str(s.get("countrylong") or s.get("country_long") or s.get("country") or "").strip(),
            "country_short": str(s.get("countryshort") or s.get("country_short") or "").strip().upper(),
            "sessions": _safe_int(s.get("numvpnsessions") or s.get("sessions"), 0),
            "uptime_ms": _safe_int(s.get("uptime"), 0),
            "config_b64": str(s.get("openvpn_configdata_base64") or s.get("config_b64") or "").strip(),
        })
    return rows


# ---------------------------------------------------------------------------
# 复制自 vpn_dial.py（decode_feed_bytes / parse_feed_text /
# rows_to_feed_configs / decode_config / ovpn_proto_port）
# ---------------------------------------------------------------------------
def decode_feed_bytes(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8", "replace")


def parse_feed_text(text: str) -> list:
    """按内容识别：JSON(镜像格式) 或 官方 CSV。失败抛 RuntimeError。"""
    s = text.lstrip()
    if s[:1] in ("[", "{"):
        try:
            return parse_mirror_json(json.loads(s))
        except Exception as exc:
            raise RuntimeError(f"JSON feed 解析失败: {exc}")
    return parse_csv(text)    # 找不到表头会抛 RuntimeError


def rows_to_feed_configs(rows: list) -> dict:
    """{host(.opengw.net): feed行} —— 只保留带官方 OpenVPN 配置(base64)的节点"""
    out = {}
    for r in rows:
        if not r.get("config_b64"):
            continue
        host = r["host"] if r["host"].endswith(".opengw.net") else r["host"] + ".opengw.net"
        out[host] = r
    return out


_PROTO_RE = re.compile(r"^\s*proto\s+(\S+)", re.M | re.I)
_REMOTE_RE = re.compile(r"^\s*remote\s+(\S+)\s+(\d+)", re.M | re.I)


def decode_config(config_b64: str) -> str:
    try:
        text = base64.b64decode(config_b64, validate=False).decode("utf-8", "replace")
    except Exception as exc:
        raise RuntimeError(f"OpenVPN 配置解码失败: {exc}")
    if not _REMOTE_RE.search(text):
        # b64decode 对损坏数据不会报错，只会吐出垃圾；没有 remote 行的肯定不是可用配置
        raise RuntimeError("OpenVPN 配置损坏或不完整（找不到 remote 行）")
    return text


def ovpn_proto_port(cfg_text: str):
    """从官方配置里取 (proto, port)；取不到返回 ('?', 443)"""
    pm = _PROTO_RE.search(cfg_text)
    rm = _REMOTE_RE.search(cfg_text)
    proto = pm.group(1).lower() if pm else "?"
    try:
        port = int(rm.group(2)) if rm else 443
    except ValueError:
        port = 443
    return proto, port


# ---------------------------------------------------------------------------
# feed 行 → Node
# ---------------------------------------------------------------------------
def rows_to_nodes(rows: list, source: str = "vpngate_feed") -> list:
    configs = rows_to_feed_configs(rows)
    nodes = []
    for host, row in configs.items():
        try:
            _proto, port = ovpn_proto_port(decode_config(row["config_b64"]))
        except Exception:
            continue                      # 配置坏的节点不入库（收纳线：坏数据在门口拦下）
        up_ms = row.get("uptime_ms") or 0
        node = Node(
            id=make_node_id(source, "openvpn", host, port),
            name=host,
            protocol="openvpn",
            source=source,
            host=host,
            port=port,
            config_ref=row["config_b64"],
            metrics=NodeMetrics(
                sessions=row.get("sessions") or 0,
                speed_mbps=round((row.get("speed_bps") or 0) / 1000000.0, 1),
                uptime_hours=round(up_ms / 3600000.0, 1) if up_ms else None,
            ),
            extra={
                "config_format": "b64",
                "entry_ip": row.get("ip"),
                "country": row.get("country_long") or "",
                "country_code": (row.get("country_short") or "").upper(),
                "feed_score": row.get("score") or 0,
                "feed_ping": row.get("ping", 999),
            },
        )
        nodes.append(node)
    return nodes


def parse_feed_to_nodes(text: str, source: str = "vpngate_feed") -> list:
    return rows_to_nodes(parse_feed_text(text), source=source)


def load_feed_file(path: str, source: str = "vpngate_feed") -> list:
    with open(path, "rb") as f:
        text = decode_feed_bytes(f.read())
    return parse_feed_to_nodes(text, source=source)
