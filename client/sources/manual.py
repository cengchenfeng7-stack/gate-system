#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手动导入（sources/manual.py）
================================
- 分享链接：vless:// / ss:// / trojan:// / sstp://（基本字段 + 查询参数进 extra）
  → 归属「手动导入页」（source=manual）
- .ovpn 文本：提取 remote 主机/端口，配置原文存 config_ref
  → 归属「OpenVPN 节点页」（source=openvpn）：.ovpn 进的是 OpenVPN 引擎
  节点池，按用户定的四页结构（来源第一维度）不进手动页。
只解析不联网。订阅批量导入（base64 订阅等）在 M2 的 subscription.py。
"""

from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, unquote, urlsplit

from ..core.model import Node, make_node_id

_LINK_SCHEMES = ("vless", "ss", "trojan", "sstp", "vmess")
_REMOTE_RE = re.compile(r"^\s*remote\s+(\S+)\s+(\d+)", re.M | re.I)


def _b64_decode_maybe(s: str) -> str:
    """ss 的 userinfo 可能是 base64(method:password)，也可能是明文。"""
    try:
        padded = s + "=" * (-len(s) % 4)
        return base64.urlsafe_b64decode(padded.encode()).decode("utf-8", "replace")
    except Exception:
        return s


def parse_link(link: str, source: str = "manual") -> Node:
    link = link.strip()
    if "://" not in link:
        raise ValueError(f"不是分享链接: {link[:40]}")
    scheme = link.split("://", 1)[0].lower()
    if scheme not in _LINK_SCHEMES:
        raise ValueError(f"暂不支持的链接类型: {scheme}://")
    parts = urlsplit(link)
    host = unquote(parts.hostname or "")
    port = parts.port or 0
    userinfo = unquote(parts.username or "")
    password = unquote(parts.password or "")
    if scheme == "ss" and userinfo and not password:
        decoded = _b64_decode_maybe(userinfo)
        if ":" in decoded:
            userinfo, password = decoded.split(":", 1)
    name = unquote(parts.fragment or "") or f"{host}:{port}"
    query = {k: v[0] for k, v in parse_qs(parts.query).items()}

    extra = {"query": query}
    if scheme in ("vless", "vmess"):
        extra["uuid"] = userinfo
    elif scheme in ("ss",):
        extra["method"] = userinfo
        extra["password"] = password
    elif scheme == "trojan":
        extra["password"] = password or userinfo   # trojan 的密码在 userinfo 位
    elif scheme == "sstp":
        extra["username"] = userinfo
        extra["password"] = password
    discriminator = userinfo if scheme in ("vless", "vmess", "trojan") else ""
    return Node(
        id=make_node_id(source, scheme, host, port, discriminator),
        name=name,
        protocol=scheme,
        source=source,
        host=host,
        port=port,
        config_ref=link,
        extra=extra,
    )


def parse_links_text(text: str, source: str = "manual") -> list:
    """多行文本逐行解析分享链接；注释行（# 开头）和坏行跳过。"""
    nodes = []
    for ln in text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        # nodes_*.txt 格式：入口#标签$sstp://... —— 取 $ 之后的真链接
        if "$" in ln and "://" in ln:
            ln = ln.rsplit("$", 1)[1]
        try:
            nodes.append(parse_link(ln, source=source))
        except ValueError:
            continue
    return nodes


def parse_ovpn_text(text: str, name: str = "", source: str = "openvpn") -> Node:
    m = _REMOTE_RE.search(text)
    if not m:
        raise ValueError("OpenVPN 配置里找不到 remote 行，不是可用的 .ovpn")
    host = m.group(1)
    port = int(m.group(2))
    return Node(
        id=make_node_id(source, "openvpn", host, port),
        name=name or f"{host}:{port}",
        protocol="openvpn",
        source=source,
        host=host,
        port=port,
        config_ref=text,
        extra={"config_format": "text"},
    )


def load_ovpn_file(path: str, source: str = "openvpn") -> Node:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    import os
    return parse_ovpn_text(text, name=os.path.basename(path), source=source)
