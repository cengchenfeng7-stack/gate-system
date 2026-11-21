#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Clash YAML 直导入原型（阶段二前置）
====================================
解析 Clash 配置的 proxies 条目，逐字段映射进现有 Node 结构（core/model.py），
不再绕分享链接（clash2links.py → manual.parse_link 的老路会丢字段：
ss 的 plugin/plugin-opts、trojan 的 grpc serviceName 与 fp/alpn、
vless/vmess 的 alpn / allowInsecure / ws early-data，且 hysteria2 / tuic /
wireguard 被直接跳过——直导入全部补上）。

设计要点（详见 DESIGN.md）：
- 每条 proxies 原样保真：extra["clash"] = 原条目 dict（JSON 安全化），
  config_ref = 原条目 YAML 文本。任何字段都不丢弃，引擎层日后可用。
- 可映射字段写进现有约定位置：extra["uuid"/"method"/"password"/...] 与
  extra["query"]（键名与分享链接 query 参数一致，sing-box 引擎的
  vless/trojan 出站今天就能直接消费）。
- 归属沿用订阅导入的既定决定（D1，2026-11-19）：默认 source="manual"
  归「手动导入页」，不新增来源；subscription_url / subscription_name
  写进 extra 做溯源。
- 按 node.id 批内去重（先出现者胜），坏条目/不支持协议计数跳过，
  不中断整批——口径与 subscription.py 一致。
- proxy-groups 里的 url-test/fallback/select 等分组一律不导入：
  本项目只允许用户手动固定节点，绝无自动测速/自动选择逻辑。
- 纯本地解析，不发任何网络请求；测试全部用自构造 YAML 样本。
"""
from __future__ import annotations

import base64
import datetime
import os
import sys
from dataclasses import dataclass, field

import yaml

# Node 复用现有模型：集成进 client 包后走相对导入；原型期（本目录单独跑）
# 从 client-dev 树导入真实的 Node / make_node_id，保证输出同构不是"长得像"。
try:
    from ..core.model import Node, make_node_id
except ImportError:
    _CLIENT_DEV = os.environ.get(
        "GATE_CLIENT_DEV", "/home/hatch/workspace/vpn-project/client-dev")
    if os.path.isdir(_CLIENT_DEV):
        sys.path.insert(0, _CLIENT_DEV)
    from client.core.model import Node, make_node_id


# 直导入支持的协议（任务划定的七种）；其余 type 计入 skipped_types，不中断
SUPPORTED_TYPES = ("ss", "vmess", "vless", "trojan",
                   "hysteria2", "tuic", "wireguard")

# 每种协议的主凭证（缺失则该条按坏条目跳过，不计入节点）
_REQUIRED = {
    "ss": ("cipher",),                       # ss 密码允许为空（同老转换器口径）
    "vmess": ("uuid",),
    "vless": ("uuid",),
    "trojan": ("password",),
    "hysteria2": ("password",),
    "tuic": ("uuid", "password"),
    "wireguard": ("private-key",),
}

# id 混淆子：同端点不同凭证 = 不同节点（与 manual.parse_link 的凭证口径一致：
# vless/vmess 用 uuid、trojan 用 password；新协议按主凭证同理指定；ss 沿用无混淆）
_DISCRIMINATOR = {
    "vmess": "uuid",
    "vless": "uuid",
    "trojan": "password",
    "hysteria2": "password",
    "tuic": "uuid",
    "wireguard": "public-key",               # 远端身份，同端点换对端密钥即不同节点
}


@dataclass
class ClashImportResult:
    nodes: list            # list[Node]，按 id 批内去重（先出现者胜），保持原顺序
    skipped: int           # 坏条目数（缺必填字段/端口不是整数等），不中断整批
    skipped_types: dict    # 不支持的 type -> 条数（如 ssr / socks5 / http）
    total: int             # proxies 条目总数
    skipped_reasons: dict = field(default_factory=dict)  # 跳过原因 -> 条数（诊断用）


def _json_safe(value):
    """YAML 出来的值转成 JSON 可序列化（Node.to_json 要求）：
    date/datetime → ISO 字符串，bytes → base64 字符串，其余递归。"""
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)                        # 兜底：不认识的类型转字符串保真


def _q(query: dict, key: str, value) -> None:
    """非空值才写进 query 并统一转字符串——与链接 query 参数口径一致
    （链接里只有真实出现的参数，parse_qs 也只产字符串）。"""
    if value is None or value is False:
        return
    if isinstance(value, str) and not value.strip():
        return
    query[key] = str(value)


def _transport_q(query: dict, p: dict, net: str) -> None:
    """传输层公共映射：network / ws-opts / grpc-opts / h2-opts / http-opts /
    xhttp-opts → query 的 type / path / host / serviceName / mode
    （链接约定键名，sing-box 引擎 _transport 现成消费）。
    net 里 h2 按链接惯例改叫 http（引擎对 http/h2 一视同仁）。"""
    net_raw = str(net or "tcp").strip().lower()
    net = "http" if net_raw == "h2" else net_raw
    _q(query, "type", net)
    if net_raw == "ws":
        ws = p.get("ws-opts") or {}
        _q(query, "path", ws.get("path"))
        headers = ws.get("headers") or {}
        _q(query, "host", headers.get("Host") or headers.get("host"))
    elif net_raw == "grpc":
        grpc = p.get("grpc-opts") or {}
        _q(query, "serviceName", grpc.get("grpc-service-name"))
    elif net_raw == "h2":
        h2 = p.get("h2-opts") or {}
        _q(query, "path", h2.get("path"))
        hosts = h2.get("host")
        if isinstance(hosts, list):
            _q(query, "host", hosts[0] if hosts else None)
        else:
            _q(query, "host", hosts)
    elif net_raw == "http":
        http = p.get("http-opts") or {}
        paths = http.get("path")
        if isinstance(paths, list):
            _q(query, "path", paths[0] if paths else None)
        else:
            _q(query, "path", paths)
        headers = http.get("headers") or {}
        _q(query, "host", headers.get("Host") or headers.get("host"))
    elif net_raw == "xhttp":
        xx = p.get("xhttp-opts") or {}
        _q(query, "path", xx.get("path"))
        _q(query, "host", xx.get("host"))
        _q(query, "mode", xx.get("mode"))


def _tls_q(query: dict, p: dict, *, reality: bool = True) -> None:
    """TLS 公共映射：servername/sni → sni，skip-cert-verify → allowInsecure，
    client-fingerprint / fingerprint → fp，alpn → alpn（逗号串，链接口径），
    reality-opts → security=reality + pbk/sid，tls: true → security=tls。"""
    _q(query, "sni", p.get("servername") or p.get("sni"))
    if p.get("skip-cert-verify"):
        query["allowInsecure"] = "1"
    _q(query, "fp", p.get("client-fingerprint") or p.get("fingerprint"))
    alpn = p.get("alpn")
    if isinstance(alpn, (list, tuple)) and alpn:
        _q(query, "alpn", ",".join(str(a) for a in alpn))
    else:
        _q(query, "alpn", alpn)
    if reality:
        ro = p.get("reality-opts") or {}
        if ro:
            query["security"] = "reality"
            _q(query, "pbk", ro.get("public-key"))
            _q(query, "sid", ro.get("short-id"))
        elif p.get("tls"):
            query["security"] = "tls"


# ---------------- 各协议处理器：条目 dict → (直存 extra 字段, query) ----------------

def _extra_ss(p):
    query = {}
    direct = {"method": str(p.get("cipher") or "")}
    pw = p.get("password")
    if pw is not None:
        direct["password"] = str(pw)         # 空密码也原样保留（引擎层自会拒）
    return direct, query


def _extra_vmess(p):
    query = {}
    direct = {"uuid": str(p.get("uuid") or "")}
    aid = p.get("alterId", p.get("altId", 0))
    _q(query, "aid", aid)
    _q(query, "scy", p.get("cipher") or "auto")
    _tls_q(query, p)
    _transport_q(query, p, p.get("network"))
    return direct, query


def _extra_vless(p):
    query = {}
    direct = {"uuid": str(p.get("uuid") or "")}
    query["encryption"] = "none"             # 链接惯例恒带 encryption=none
    _q(query, "flow", p.get("flow"))
    _tls_q(query, p)
    _transport_q(query, p, p.get("network"))
    return direct, query


def _extra_trojan(p):
    query = {}
    direct = {"password": str(p.get("password") or "")}
    _tls_q(query, p)                         # trojan 恒为 TLS，security 不另写
    net = p.get("network")
    if net:
        _transport_q(query, p, net)
    return direct, query


def _extra_hysteria2(p):
    query = {}
    direct = {"password": str(p.get("password") or p.get("auth") or "")}
    _tls_q(query, p)                         # sni / allowInsecure / fp / alpn(h3)
    return direct, query


def _extra_tuic(p):
    query = {}
    direct = {"uuid": str(p.get("uuid") or ""),
              "password": str(p.get("password") or "")}
    _tls_q(query, p)                         # sni / fp / alpn / allowInsecure
    return direct, query


def _extra_wireguard(p):
    query = {}
    direct = {}
    if p.get("private-key") is not None:
        direct["private_key"] = str(p["private-key"])
    if p.get("public-key") is not None:
        direct["public_key"] = str(p["public-key"])
    if p.get("pre-shared-key") is not None:
        direct["pre_shared_key"] = str(p["pre-shared-key"])
    if p.get("ip") is not None:
        direct["local_address"] = str(p["ip"])
    if p.get("ipv6") is not None:
        direct["local_address_v6"] = str(p["ipv6"])
    if p.get("reserved") is not None:
        direct["reserved"] = _json_safe(p["reserved"])
    return direct, query


_HANDLERS = {"ss": _extra_ss, "vmess": _extra_vmess, "vless": _extra_vless,
             "trojan": _extra_trojan, "hysteria2": _extra_hysteria2,
             "tuic": _extra_tuic, "wireguard": _extra_wireguard}


# ---------------- 主入口 ----------------

def _skip(result: dict, reason: str) -> None:
    result["skipped"] += 1
    result["skipped_reasons"][reason] = \
        result["skipped_reasons"].get(reason, 0) + 1


def import_clash_text(text: str, source: str = "manual",
                      subscription_url: str = "",
                      subscription_name: str = "") -> ClashImportResult:
    """Clash 配置文本 → Node 列表（直导入，不绕分享链接）。

    - yaml 解不出来 → ValueError（与 manual.parse_link 的坏输入口径一致）；
    - 解得动但顶层不是映射 / 没有 proxies 列表 → 空结果（不抛异常，
      同 subscription.py 对不支持格式"空列表、不假装支持"的口径）；
    - 坏条目（缺 server/port/主凭证）与不支持协议只计数跳过，不中断整批；
    - 纯本地解析，不发任何网络请求；不导入 proxy-groups（本项目严禁
      url-test/fallback 等自动切换逻辑）。
    """
    result = {"skipped": 0, "skipped_types": {}, "skipped_reasons": {}}
    try:
        cfg = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValueError(f"YAML 解析失败: {exc}") from exc
    proxies = cfg.get("proxies") if isinstance(cfg, dict) else None
    if not isinstance(proxies, list):
        # 不是 Clash 配置（或空配置）：空结果，不假装支持
        return ClashImportResult(nodes=[], skipped=0, skipped_types={},
                                 total=0, skipped_reasons={})
    nodes = []
    for p in proxies:
        if not isinstance(p, dict):
            _skip(result, "条目不是映射")
            continue
        ptype = str(p.get("type") or "").strip().lower()
        if ptype not in _HANDLERS:
            result["skipped_types"][ptype] = \
                result["skipped_types"].get(ptype, 0) + 1
            continue
        missing = [k for k in _REQUIRED[ptype] if not str(p.get(k) or "").strip()]
        host = str(p.get("server") or "").strip()
        try:
            port = int(p.get("port"))
        except (TypeError, ValueError):
            port = 0
        if not host or not port or missing:
            _skip(result, f"{ptype or '未知'} 条目缺必填字段"
                          + (f"（缺 {'/'.join(missing)}）" if missing else ""))
            continue
        direct, query = _HANDLERS[ptype](p)
        name = str(p.get("name") or "").strip() or f"{host}:{port}"
        discriminator_key = _DISCRIMINATOR.get(ptype)
        discriminator = ""
        if discriminator_key:
            discriminator = str(p.get(discriminator_key) or "").strip()
        node = Node(
            id=make_node_id(source, ptype, host, port, discriminator),
            name=name,
            protocol=ptype,
            source=source,
            host=host,
            port=port,
            config_ref=yaml.safe_dump(p, allow_unicode=True,
                                      sort_keys=False, default_flow_style=False),
            extra={
                "config_format": "clash_yaml",
                "clash": _json_safe(p),      # 原条目逐字段保真，零丢弃
                "query": query,
                **direct,
            },
        )
        nodes.append(node)
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
    return ClashImportResult(nodes=deduped, skipped=result["skipped"],
                             skipped_types=result["skipped_types"],
                             total=len(proxies),
                             skipped_reasons=result["skipped_reasons"])


def import_clash_file(path: str, **kwargs) -> ClashImportResult:
    """读本地 Clash YAML 文件（utf-8, errors='replace'）后调
    import_clash_text。只读文件，不发网络请求。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return import_clash_text(text, **kwargs)
