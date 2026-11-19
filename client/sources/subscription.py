#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
订阅导入（sources/subscription.py）
====================================
M2 第一个模块：把订阅内容（分享链接列表，整段 base64 或明文）批量解析成
统一 Node。只解析，不测速、不验真——拿到节点列表即止。

- 归属（已定决定 D1，2026-11-19）：订阅导入的节点一律归「手动导入页」
  （source=manual），不新增第五个 source；订阅地址/名称只写进
  node.extra（subscription_url / subscription_name）做溯源。
- 支持的输入：逐行 vless:// ss:// trojan:// sstp:// vmess:// 分享链接；
  空行与 # 注释行跳过；坏行计入 skipped，不中断整批。
- 暂不支持：sing-box JSON、Clash YAML 等客户端配置格式——喂进来只会
  得到空列表（不抛异常，也不假装支持），需要时另行专项实现。
- 联网边界：decode_subscription_text / parse_subscription_text /
  load_subscription_file 纯本地，不发任何网络请求；只有
  fetch_subscription_text / import_subscription_url 会拉取订阅地址。
"""

from __future__ import annotations

import base64
import binascii
import re
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from . import manual


@dataclass
class SubscriptionResult:
    nodes: list          # list[Node]，按 id 批内去重（先出现者胜），保持原顺序
    skipped: int         # 非空、非注释、但解析失败的行数
    source_format: str   # "base64" 或 "plain"：输入实际被当作哪种处理


# base64 正文允许的字符（标准 + urlsafe 两种字母表的并集）；= 只允许作结尾 padding
_B64_CORE_RE = re.compile(r"[A-Za-z0-9+/\-_]+")

# 常见浏览器 UA：订阅服务端常按 UA 区分客户端，裸 urllib UA 容易被拒
_BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


def decode_subscription_text(text: str) -> tuple[str, str]:
    """整段 base64 容错解码，返回 (链接文本, "base64")；解不开或解出文本
    不含 "://" 时返回 (原文, "plain")。容错点：标准/urlsafe 两种字母表、
    缺失 padding、内容中夹空白和换行。纯本地，不发网络请求。"""
    if text.startswith("\ufeff"):    # BOM 不是内容（utf-8 非 utf-8-sig 读带 BOM 文件会残留，污染首行）
        text = text[1:]
    if not text.strip():
        return text, "plain"
    compact = "".join(text.split())          # 去掉内容中夹的所有空白（含换行）
    core = compact.rstrip("=")               # 结尾 padding（缺失/残缺）一律重补
    if not core or not _B64_CORE_RE.fullmatch(core):
        return text, "plain"
    padded = core + "=" * (-len(core) % 4)
    try:
        raw = base64.b64decode(padded, validate=True)
    except (binascii.Error, ValueError):     # 含 -/_ 时标准字母表解不开 → 试 urlsafe
        try:
            raw = base64.urlsafe_b64decode(padded)
        except (binascii.Error, ValueError):
            return text, "plain"
    decoded = raw.decode("utf-8", "replace")
    if "://" not in decoded:
        return text, "plain"
    return decoded, "base64"


def parse_subscription_text(text: str, source: str = "manual",
                            subscription_url: str = "",
                            subscription_name: str = "") -> SubscriptionResult:
    """订阅内容 → Node 列表。流程：
    1. decode_subscription_text 得到链接文本；
    2. 逐行处理：空行和 # 注释行跳过（不计 skipped）；其余行调
       manual.parse_link(line, source=source)，ValueError → skipped += 1；
    3. subscription_url / subscription_name 非空时，写进每个 node.extra；
    4. 按 node.id 批内去重（先出现者胜，保持原顺序）。
    纯本地，不发网络请求。sing-box JSON / Clash YAML 暂不支持：结果为空列表。"""
    link_text, source_format = decode_subscription_text(text)
    nodes = []
    skipped = 0
    for ln in link_text.splitlines():
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        try:
            nodes.append(manual.parse_link(ln, source=source))
        except ValueError:
            skipped += 1
    if subscription_url:
        for n in nodes:
            n.extra["subscription_url"] = subscription_url
    if subscription_name:
        for n in nodes:
            n.extra["subscription_name"] = subscription_name
    seen = set()
    deduped = []
    for n in nodes:
        if n.id not in seen:
            seen.add(n.id)
            deduped.append(n)
    return SubscriptionResult(nodes=deduped, skipped=skipped,
                              source_format=source_format)


def load_subscription_file(path: str, **kwargs) -> SubscriptionResult:
    """读本地文件（utf-8, errors='replace'）后调 parse_subscription_text。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return parse_subscription_text(text, **kwargs)


def fetch_subscription_text(url: str, timeout: float = 15.0) -> str:
    """urllib 拉取订阅原文。只允许 http/https（其他 scheme 抛 ValueError）；
    带常见浏览器 User-Agent；只读文本返回，不解析。"""
    scheme = urlsplit(url).scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError(f"订阅地址只允许 http/https，收到 scheme: {scheme or '(空)'}")
    req = urllib.request.Request(url, headers={"User-Agent": _BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        charset = (resp.headers and resp.headers.get_content_charset()) or "utf-8"
        data = resp.read()
    return data.decode(charset, "replace")


def import_subscription_url(url: str, **kwargs) -> SubscriptionResult:
    """fetch + parse，subscription_url 自动填 url（kwargs 已传则以 kwargs 为准）。"""
    text = fetch_subscription_text(url)
    kwargs.setdefault("subscription_url", url)
    return parse_subscription_text(text, **kwargs)
