#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sing-box 引擎（engines/singbox_engine.py）
============================================
把 vless / ss(shadowsocks) / trojan / vmess / hysteria2 / tuic / wireguard
单节点翻译成 sing-box 1.14 配置，拉起本地代理进程（socks+http 双入站，只绑
127.0.0.1）：
- build_config：纯函数式生成配置（dry-run/测试用，不落盘不起进程）
- start：临时目录写配置 → 子进程日志重定向到临时文件 → 等本地端口就绪 → handle
- stop：terminate → wait 5s → 还活着就 kill，然后删临时目录
  （临时配置里有 uuid/密码，停机必须删——红线）
- status：诚实汇报 "running"/"stopped"，不假报

铁律：手动固定节点、严禁自动切换——配置恒为单节点单出站（唯一出站
tag=node-out，route.final 指向它），绝不生成 urltest / selector。
wireguard 例外说明：sing-box 1.13 起已移除 wireguard outbound，出站行为
由 endpoints[0] 承担（tag 同为 node-out，outbounds 为空列表）；route.final
与 DNS detour 的标签查找会回落到 endpoint 管理器（v1.14.4
adapter/outbound/manager.go Outbound() 尾部口径），单节点单出站语义不变。
找不到 sing-box 二进制时 start() 抛 EngineError（中文安装指引，不抛栈）。
TUN 模式（v0.10.0，task6）：build_config 在 takeover_mode="tun" 时尾部并入
tun inbound + DNS 劫持规则（core/tun_config.py，设计见 task6 DESIGN.md §10）；
激活（pkexec + root wrapper）未实现，start() 遇 tun 模式显式拒绝——配置
生成可用、提权启动器待真机实现，绝不静默半接管。

字段来源说明：Clash 直导入节点把原始条目逐字段保真在 extra["clash"]
（sources/clash_import.py），本引擎按那份保真映射消费；vmess 没有链接
JSON 时走 clash 条目造出站，hysteria2/tuic/wireguard 的协议字段只存在
于 clash 条目里，链接导入节点沿用 extra 直存字段/query 约定键。
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from ..core.health import check_ports_free, describe_occupied
from ..core.model import Node
from ..core import tun_config as _tun
from .base import EngineError, EngineEvent

DEFAULT_SOCKS_PORT = 7898
DEFAULT_HTTP_PORT = 7897
SINGBOX_PROTOCOLS = ("vless", "ss", "trojan", "vmess",
                     "hysteria2", "tuic", "wireguard")

_ENV_BIN = "GATE_CLIENT_SINGBOX_BIN"
_HOME_BIN = "~/.local/bin/sing-box"
_ENV_LX_BIN = "GATE_CLIENT_SINGBOX_LX_BIN"
_HOME_LX_BIN = "~/.local/bin/sing-box-lx"
_OUT_TAG = "node-out"
_TEMP_PREFIX = "gate-singbox-"
_ENGINE_META = "engine.json"


@dataclass
class _Handle:
    node_id: str
    proc: object = None
    config_dir: str = ""       # 会话临时目录（config.json + sing-box.log），stop 时删
    log_path: str = ""


# sing-box utls 只认这组指纹；mihomo 的 "unsafe" 本义是不伪造指纹，
# 直译就是不写 utls 块。未知值同样处理——写个内核不认的值只会启动失败。
_UTLS_FINGERPRINTS = frozenset({
    "chrome", "firefox", "safari", "ios", "android", "edge", "360", "qq",
    "random", "randomized"})


def _normalize_utls_fingerprint(raw) -> str:
    fp = str(raw or "").strip().lower()
    if not fp:
        return "chrome"
    return fp if fp in _UTLS_FINGERPRINTS else ""


def _normalize_vless_flow(flow: str) -> str:
    """Xray 的 xtls-rprx-vision-udp443 等变体在 sing-box 里就是
    xtls-rprx-vision；其余值原样透传，未知值让内核显式报错。"""
    if flow == "xtls-rprx-vision" or flow.startswith("xtls-rprx-vision-"):
        return "xtls-rprx-vision"
    return flow


class SingBoxEngine:
    name = "sing-box"

    def __init__(self, binary: Optional[str] = None,
                 socks_port: int = DEFAULT_SOCKS_PORT,
                 http_port: int = DEFAULT_HTTP_PORT,
                 ready_timeout: float = 15.0, popen_factory=None,
                 lx_binary: Optional[str] = None,
                 takeover_mode: str = "proxy"):
        # 构造器不碰文件系统、不找二进制——二进制解析懒到 find_binary/start
        self._binary = binary
        self._lx_binary = lx_binary
        self.socks_port = int(socks_port)
        self.http_port = int(http_port)
        self.ready_timeout = float(ready_timeout)
        self._popen = popen_factory or subprocess.Popen
        self._live_handles = []
        # 接管方式（task6/DESIGN §2）：会话属性，与节点无关；默认 proxy
        # （现行系统代理）。只在 IDLE 时改（GUI 在 CONNECTED 时禁用控件，
        # CLI 每次连接显式给）；非法值当场报错，不静默按 proxy 处理。
        mode = str(takeover_mode or "proxy").strip().lower()
        if mode not in _tun.MODES:
            raise EngineError(
                f"未知接管方式 {takeover_mode!r}——支持 {'/'.join(_tun.MODES)}")
        self.takeover_mode = mode
        atexit.register(self._stop_all_live)

    # ---------------- 二进制解析 ----------------
    def find_binary(self) -> str:
        """构造参数 binary → 环境变量 GATE_CLIENT_SINGBOX_BIN →
        ~/.local/bin/sing-box（存在才算）→ shutil.which("sing-box")。

        明确给出的路径（构造参数/环境变量）必须真实存在，否则直接判未找到、
        不回落自动探测——否则显式传错路径会在装了 sing-box 的机器上
        悄悄用上真二进制。找不到返回 ""。
        """
        if self._binary:
            return self._binary if os.path.isfile(self._binary) else ""
        env_bin = os.environ.get(_ENV_BIN) or ""
        if env_bin:
            return env_bin if os.path.isfile(env_bin) else ""
        home_bin = os.path.expanduser(_HOME_BIN)
        if os.path.isfile(home_bin):
            return home_bin
        return shutil.which("sing-box") or ""

    @staticmethod
    def _missing_binary_error(explicit: str) -> EngineError:
        if explicit:
            return EngineError(
                f"指定的 sing-box 路径不存在: {explicit}——请从官方 GitHub release "
                f"安装 sing-box v1.14.4（钉版），放到 {_HOME_BIN}，"
                f"或用环境变量 {_ENV_BIN} 指定有效路径")
        return EngineError(
            f"未找到 sing-box 可执行文件——请从官方 GitHub release 安装 "
            f"sing-box v1.14.4（钉版），放到 {_HOME_BIN}，"
            f"或用环境变量 {_ENV_BIN} 指定路径")

    @staticmethod
    def _check_tun_privilege() -> None:
        """TUN 提权前提检查（task6/DESIGN §5）：v1 方案是 pkexec + root
        wrapper（wrapper 监听 stdin-EOF 联动停机，解用户态 stop() 对 root
        进程 SIGTERM 会 EPERM 的坑）。

        wrapper 尚未实现，当前版本一律显式报错——TUN 配置已生成，但没有
        提权路径可安全启动 sing-box。主方案：失败即显式报错、不静默回退
        到系统代理模式（审查方裁决，见 task6 DESIGN §2.4；CLI 打印本错
        误并提示手动改用系统代理模式，退出码 2）。
        """
        if shutil.which("pkexec") is None:
            raise EngineError(
                "TUN 模式尚未就绪：本机没有 pkexec（polkit），且提权启动器"
                "（pkexec + root wrapper）未实现——TUN 配置已生成，但没有"
                "提权路径可安全启动 sing-box。请先用系统代理模式连接；"
                "提权启动器待真机实现后再开 TUN。客户端不会自动回退，"
                "也不会自动重试。")
        raise EngineError(
            "TUN 模式尚未就绪：提权启动器（pkexec + root wrapper）未实现——"
            "TUN 配置已生成，但没有提权路径可安全启动 sing-box。请先用"
            "系统代理模式连接；提权启动器待真机实现后再开 TUN。客户端"
            "不会自动回退，也不会自动重试。")

    # ---------------- 配置生成 ----------------
    def build_config(self, node: Node) -> dict:
        """纯函数式生成 sing-box 1.14 配置（不落盘不起进程）。

        单节点单出站：outbounds 只有 node-out 一条（wireguard 例外：
        outbound 已被 sing-box 移除，出站由 endpoints[0] 承担，tag 仍为
        node-out，见 _wireguard_endpoint）；缺必填参数直接抛 EngineError
        点名缺哪个字段，不生成半成品配置。
        """
        protocol = str(getattr(node, "protocol", "") or "").strip().lower()
        if protocol not in SINGBOX_PROTOCOLS:
            raise EngineError(
                f"不支持节点协议 {protocol or '未知'}——sing-box 引擎只支持 "
                + "/".join(SINGBOX_PROTOCOLS))
        outbound = None
        endpoint = None
        try:
            if protocol == "vless":
                outbound = self._vless_outbound(node)
            elif protocol == "ss":
                outbound = self._ss_outbound(node)
            elif protocol == "trojan":
                outbound = self._trojan_outbound(node)
            elif protocol == "vmess":
                outbound = self._vmess_outbound(node)
            elif protocol == "hysteria2":
                outbound = self._hysteria2_outbound(node)
            elif protocol == "tuic":
                outbound = self._tuic_outbound(node)
            else:
                endpoint = self._wireguard_endpoint(node)
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(f"sing-box 配置生成失败: {exc}") from exc
        config = {
            "log": {"level": "warn", "timestamp": True},
            # 目标域名必须在出口侧解析（remote-dns 经 node-out 绕行）：本机
            # 解析器可能被其他代理的 fake-ip 劫持，解析出的假 IP 送到出口
            # 服务器毫无意义、连接必死（2026-11-20 真机验收实测教训）；
            # 同时避免 DNS 泄漏。节点服务器自身的域名走 local-dns 本地
            # 解析（默认 final），否则绕行解析会先有鸡还是先有蛋。
            "dns": {
                "servers": [
                    {"type": "udp", "tag": "remote-dns", "server": "8.8.8.8",
                     "server_port": 53, "detour": _OUT_TAG},
                    {"type": "local", "tag": "local-dns"},
                ],
                "final": "local-dns",
            },
            "inbounds": [
                {"type": "socks", "tag": "socks-in",
                 "listen": "127.0.0.1", "listen_port": self.socks_port},
                {"type": "http", "tag": "http-in",
                 "listen": "127.0.0.1", "listen_port": self.http_port},
            ],
            "outbounds": [outbound] if outbound is not None else [],
            "route": {
                "rules": [{"action": "resolve", "server": "remote-dns",
                           "strategy": "ipv4_only"}],
                "final": _OUT_TAG,
                # 出站服务器域名一律走本地解析（sing-box 1.14 硬性要求
                # 显式指定 domain_resolver，否则 check 直接 FATAL）
                "default_domain_resolver": "local-dns",
            },
        }
        if endpoint is not None:
            # wireguard：endpoint 的 tag 沿用 node-out，route.final 与
            # remote-dns 的 detour 照旧指向它（标签查找回落 endpoint 管理器）
            config["endpoints"] = [endpoint]
        if self.takeover_mode == "tun":
            # TUN 模式（task6/DESIGN §2.3）：尾部并入 tun inbound + DNS
            # 劫持规则；proxy 模式原样。合并失败显式报错，不产半成品配置。
            try:
                config = _tun.build_tun_config(
                    config, _tun.build_tun_inbound())
            except ValueError as exc:
                raise EngineError(f"TUN 配置合并失败: {exc}") from exc
        return config

    @staticmethod
    def _need_endpoint(label: str, host, port) -> None:
        if not str(host or "").strip():
            raise EngineError(f"{label} 节点缺 server 地址")
        if not port:
            raise EngineError(f"{label} 节点缺端口")

    @staticmethod
    def _extra_maps(node: Node):
        """(extra["clash"], extra["query"]) 的 dict 视图——非 dict 一律当
        缺省处理（节点库里可能有旧版本/手工数据，字段保真来源不在场）。"""
        extra = node.extra or {}
        clash = extra.get("clash")
        query = extra.get("query")
        return (clash if isinstance(clash, dict) else {},
                query if isinstance(query, dict) else {})

    _XHTTP_MODES = ("auto", "packet-up", "stream-up", "stream-one")

    @staticmethod
    def _transport(qtype, host, path, service, label: str, mode=None):
        """vless query / vmess JSON 字段 → (sing-box transport 或 None, host 参数)。

        host 参数返回给调用方做 tls server_name 回退；tcp/缺省 → 不写 transport。
        """
        qtype = str(qtype or "").strip().lower()
        host = str(host or "").strip()
        if qtype in ("", "tcp"):
            return None, host
        if qtype in ("http", "h2"):                 # h2 是 http 传输的老别名
            t = {"type": "http", "path": str(path or "").strip() or "/"}
            if host:
                t["headers"] = {"Host": host}
            return t, host
        if qtype == "ws":
            t = {"type": "ws", "path": str(path or "").strip() or "/"}
            if host:
                t["headers"] = {"Host": host}
            return t, host
        if qtype == "grpc":
            return {"type": "grpc",
                    "service_name": str(service or "").strip()}, host
        if qtype == "xhttp":
            # XHTTP（Xray splithttp）：host 是顶层字段（不是 Host 头），
            # mode 只收四种合法值，非法/缺省就不写、让内核用 auto。
            t = {"type": "xhttp", "path": str(path or "").strip() or "/"}
            if host:
                t["host"] = host
            m = str(mode or "").strip().lower()
            if m in SingBoxEngine._XHTTP_MODES:
                t["mode"] = m
            return t, host
        raise EngineError(f"{label} 节点暂不支持传输类型 {qtype}")

    @staticmethod
    def _clash_transport(clash: dict, label: str):
        """clash 原始条目 → (sing-box transport 或 None, 传输 host 参数)。

        字段口径对齐 clash_import._transport_q（network / ws-opts /
        grpc-opts / h2-opts / http-opts / xhttp-opts）。ws 的 headers
        整体透传（Host 只是其中之一，自定义头也是配置的一部分，丢了就
        连不上）；h2/http/xhttp 与链接路径共用 _transport 的产出形状。
        host 参数返回给调用方做 tls server_name 回退；tcp/缺省不写 transport。
        """
        net = str(clash.get("network") or "").strip().lower()
        if net in ("", "tcp"):
            return None, ""
        if net == "ws":
            ws = clash.get("ws-opts") or {}
            t = {"type": "ws", "path": str(ws.get("path") or "").strip() or "/"}
            host = ""
            headers = ws.get("headers")
            if isinstance(headers, dict) and headers:
                t["headers"] = {str(k): str(v) for k, v in headers.items()
                                if v is not None}
                host = str(headers.get("Host") or headers.get("host")
                           or "").strip()
            return t, host
        if net == "grpc":
            grpc = clash.get("grpc-opts") or {}
            return ({"type": "grpc",
                     "service_name": str(grpc.get("grpc-service-name")
                                         or "").strip()}, "")
        if net == "h2":                             # h2-opts.host 惯例是列表
            h2 = clash.get("h2-opts") or {}
            hosts = h2.get("host")
            return SingBoxEngine._transport(
                qtype="http", path=h2.get("path"), service=None, label=label,
                host=hosts[0] if isinstance(hosts, list) and hosts else hosts)
        if net == "http":
            http = clash.get("http-opts") or {}
            headers = http.get("headers") or {}
            paths = http.get("path")                # http-opts.path 惯例是列表
            return SingBoxEngine._transport(
                qtype="http",
                host=headers.get("Host") or headers.get("host"),
                service=None, label=label,
                path=paths[0] if isinstance(paths, list) and paths else paths)
        if net == "xhttp":
            xx = clash.get("xhttp-opts") or {}
            return SingBoxEngine._transport(
                qtype="xhttp", host=xx.get("host"), path=xx.get("path"),
                service=None, label=label, mode=xx.get("mode"))
        raise EngineError(f"{label} 节点暂不支持传输类型 {net}")

    @staticmethod
    def _vless_tls(query: dict, transport_host: str, server: str):
        """security=tls/reality → tls 块；缺省/none → None。server_name 回退链：
        sni → 传输 host 参数 → server 地址。"""
        security = str(query.get("security") or "").strip().lower()
        if security not in ("tls", "reality"):
            return None
        sni = str(query.get("sni") or "").strip() or transport_host or server
        fp = _normalize_utls_fingerprint(query.get("fp"))
        tls = {"enabled": True, "server_name": sni}
        if fp:
            tls["utls"] = {"enabled": True, "fingerprint": fp}
        if security == "reality":
            pbk = str(query.get("pbk") or "").strip()
            if not pbk:
                raise EngineError("vless 节点缺 reality 公钥（query pbk）")
            tls["reality"] = {"enabled": True, "public_key": pbk,
                              "short_id": str(query.get("sid") or "").strip()}
        return tls

    @staticmethod
    def _quic_tls(clash: dict, query: dict, server: str) -> dict:
        """hysteria2 / tuic 共用的 TLS 块。两个协议本身强制 TLS（sing-box
        v1.14.4 protocol/{hysteria2,tuic}/outbound.go 开头即校验
        TLS.Enabled，否则 ErrTLSRequired），所以恒写 enabled=true。
        server_name 回退链 servername/sni → query sni → server 地址；
        skip-cert-verify → insecure（clash 布尔或 query allowInsecure=1
        链接口径都认）；alpn 透传、缺省不写键（QUIC 层默认 h3）。
        fingerprint 有意不映射：uTLS 是 TCP-TLS 概念，QUIC 握手用不上。"""
        sni = (str(clash.get("servername") or "").strip()
               or str(clash.get("sni") or "").strip()
               or str(query.get("sni") or "").strip() or server)
        tls = {"enabled": True, "server_name": sni}
        if clash.get("skip-cert-verify") or \
                str(query.get("allowInsecure") or "") == "1":
            tls["insecure"] = True
        alpn = clash.get("alpn")
        if isinstance(alpn, (list, tuple)) and alpn:
            tls["alpn"] = [str(a) for a in alpn]
        elif isinstance(alpn, str) and alpn.strip():
            tls["alpn"] = [alpn.strip()]
        elif isinstance(query.get("alpn"), str) and query["alpn"].strip():
            # 链接口径：alpn 存逗号串（clash_import._tls_q 的写法）
            tls["alpn"] = [a.strip() for a in str(query["alpn"]).split(",")
                           if a.strip()]
        return tls

    @staticmethod
    def _clash_bandwidth(value) -> Optional[int]:
        """clash 的 up/down（"30 Mbps" / 30）→ Mbps 整数；解析不出正整数
        就返回 None 不写键——sing-box 里这两个字段留空走 BBR，写坏值反而
        起不来，静默不写是有意的（见 DESIGN.md）。"""
        if value is None or isinstance(value, bool):
            return None
        if isinstance(value, (int, float)):
            return int(value) if value > 0 else None
        m = re.search(r"\d+(?:\.\d+)?", str(value))
        if not m:
            return None
        n = int(float(m.group()))
        return n if n > 0 else None

    def _vless_outbound(self, node: Node) -> dict:
        extra = node.extra or {}
        query = dict(extra.get("query") or {})
        uuid = str(extra.get("uuid") or "").strip()
        if not uuid:
            raise EngineError("vless 节点缺 uuid")
        self._need_endpoint("vless", node.host, node.port)
        out = {"type": "vless", "tag": _OUT_TAG, "server": node.host,
               "server_port": int(node.port), "uuid": uuid}
        flow = str(query.get("flow") or "").strip()
        if flow:
            out["flow"] = _normalize_vless_flow(flow)
        transport, transport_host = self._transport(
            qtype=query.get("type"), host=query.get("host"),
            path=query.get("path"), service=query.get("serviceName"),
            label="vless", mode=query.get("mode"))
        if transport:
            out["transport"] = transport
        tls = self._vless_tls(query, transport_host, node.host)
        if tls:
            out["tls"] = tls
        return out

    def _ss_outbound(self, node: Node) -> dict:
        extra = node.extra or {}
        method = str(extra.get("method") or "").strip()
        password = str(extra.get("password") or "")    # 密码原样用，不 strip
        if not method:
            raise EngineError("ss 节点缺 method（加密方式）")
        if not password:
            raise EngineError("ss 节点缺 password")
        self._need_endpoint("ss", node.host, node.port)
        out = {"type": "shadowsocks", "tag": _OUT_TAG, "server": node.host,
               "server_port": int(node.port),
               "method": method, "password": password}
        # ss 插件参数只存在于 clash 直导入条目（SIP002 链接的 plugin 参数
        # manual.py 今天不解析），保真来源是 extra["clash"]
        clash = extra.get("clash")
        if isinstance(clash, dict):
            plugin = str(clash.get("plugin") or "").strip()
            if plugin:
                plugin_name, plugin_opts = self._ss_plugin(
                    plugin, clash.get("plugin-opts"))
                out["plugin"] = plugin_name
                if plugin_opts:
                    out["plugin_opts"] = plugin_opts
        return out

    # clash 插件名 → sing-box plugin 字段。依据（sing-box 文档
    # configuration/outbound/shadowsocks）：plugin 字段只支持 obfs-local 与
    # v2ray-plugin 两种内置 SIP003 插件；clash 的 obfs 插件就是
    # simple-obfs（其 SIP003 标准名/可执行名是 obfs-local），v2ray-plugin 同名。
    _SS_PLUGIN_NAMES = {
        "obfs": "obfs-local",
        "obfs-local": "obfs-local",
        "simple-obfs": "obfs-local",
        "v2ray-plugin": "v2ray-plugin",
    }

    @classmethod
    def _ss_plugin(cls, plugin: str, opts):
        """clash plugin/plugin-opts → (sing-box plugin, plugin_opts)。

        不支持的插件名显式 EngineError——插件参与握手，静默丢弃插件名会让
        节点"导得进、连不上"且查不出原因（红线：不许静默丢弃）。
        plugin_opts 是 SIP003 选项串（分号分隔的 key=value；布尔项裸名）。
        """
        name = cls._SS_PLUGIN_NAMES.get(str(plugin or "").strip().lower())
        if not name:
            raise EngineError(
                f"不支持 ss 插件 {plugin}——sing-box 只内置 obfs-local / "
                "v2ray-plugin 两种 SIP003 插件，该节点无法在本引擎拉起"
                "（显式报错，不静默丢弃插件参数）")
        opts = opts if isinstance(opts, dict) else {}
        parts = []
        if name == "obfs-local":
            # simple-obfs 的 SIP003 参数：obfs=<mode>（http/tls）、obfs-host=<host>
            mode = str(opts.get("mode") or "").strip()
            host = str(opts.get("host") or "").strip()
            if mode:
                parts.append(f"obfs={mode}")
            if host:
                parts.append(f"obfs-host={host}")
        else:
            # v2ray-plugin SIP003 参数：mode/host/path/tls 是文档明确的连接
            # 子集；headers/mux/skip-cert-verify 等参数格式口径未核实，有意
            # 不映射（见 DESIGN.md），不猜格式
            for key in ("mode", "host", "path"):
                value = str(opts.get(key) or "").strip()
                if value:
                    parts.append(f"{key}={value}")
            if opts.get("tls"):
                parts.append("tls")
        return name, ";".join(parts)

    def _trojan_outbound(self, node: Node) -> dict:
        extra = node.extra or {}
        password = str(extra.get("password") or "")    # 密码原样用，不 strip
        if not password:
            raise EngineError("trojan 节点缺 password")
        self._need_endpoint("trojan", node.host, node.port)
        query = dict(extra.get("query") or {})
        sni = str(query.get("sni") or "").strip() or node.host
        return {"type": "trojan", "tag": _OUT_TAG, "server": node.host,
                "server_port": int(node.port), "password": password,
                "tls": {"enabled": True, "server_name": sni}}

    def _vmess_outbound(self, node: Node) -> dict:
        """vmess 双来源分派：config_ref 是 vmess:// 链接 → 链接 JSON 路径
        （老口径不变）；否则 clash 直导入节点（配置在 extra["clash"]）→
        clash 字段路径。两条路都没有 → 显式报错。"""
        if str(node.config_ref or "").strip().lower().startswith("vmess://"):
            return self._vmess_outbound_from_link(node)
        clash, _ = self._extra_maps(node)
        if clash:
            return self._vmess_outbound_from_clash(node, clash)
        raise EngineError("vmess 节点既无 vmess:// 链接载荷，也无 clash "
                          "原始条目（extra[\"clash\"]），无法生成配置")

    def _vmess_outbound_from_link(self, node: Node) -> dict:
        # vmess 链接 manual 解析拿不到字段——以 config_ref 里的 base64 JSON 为准
        fields = self._decode_vmess(node.config_ref)
        server = str(fields.get("add") or "").strip()
        uuid = str(fields.get("id") or "").strip()     # add/id 已在 _decode_vmess 拦下
        try:
            server_port = int(fields.get("port"))
            alter_id = int(fields.get("aid") or 0)
        except (TypeError, ValueError):
            raise EngineError("vmess 链接解析失败: port/aid 字段不是整数")
        out = {"type": "vmess", "tag": _OUT_TAG, "server": server,
               "server_port": server_port, "uuid": uuid,
               "security": str(fields.get("scy") or "").strip() or "auto",
               "alter_id": alter_id}
        # grpc 的服务名在 vmess JSON 里按惯例存 path 字段
        transport, transport_host = self._transport(
            qtype=fields.get("net"), host=fields.get("host"),
            path=fields.get("path"), service=fields.get("path"),
            label="vmess", mode=fields.get("mode"))
        if transport:
            out["transport"] = transport
        if str(fields.get("tls") or "").strip().lower() == "tls":
            sni = str(fields.get("sni") or "").strip() or transport_host or server
            out["tls"] = {"enabled": True, "server_name": sni,
                          "utls": {"enabled": True, "fingerprint": "chrome"}}
        return out

    def _vmess_outbound_from_clash(self, node: Node, clash: dict) -> dict:
        """clash 直导入的 vmess：按 extra["clash"] 原始条目造出站。

        字段口径对齐 clash_import._extra_vmess（uuid 直存；alterId/altId →
        aid 缺省 0、cipher → scy 缺省 auto）；tls/reality 与传输层的映射
        见 _vmess_clash_tls / _clash_transport。server/port 以 Node 为准
        （clash_import 已校验必填并写进 Node）。"""
        uuid = str(clash.get("uuid") or "").strip()
        if not uuid:
            raise EngineError("vmess 节点缺 uuid")
        self._need_endpoint("vmess", node.host, node.port)
        try:
            alter_id = int(clash.get("alterId", clash.get("altId", 0)) or 0)
        except (TypeError, ValueError):
            raise EngineError("vmess 节点 alterId 字段不是整数")
        out = {"type": "vmess", "tag": _OUT_TAG, "server": node.host,
               "server_port": int(node.port), "uuid": uuid,
               "security": str(clash.get("cipher") or "").strip() or "auto",
               "alter_id": alter_id}
        transport, transport_host = self._clash_transport(clash, label="vmess")
        if transport:
            out["transport"] = transport
        tls = self._vmess_clash_tls(clash, transport_host, node.host)
        if tls:
            out["tls"] = tls
        return out

    @staticmethod
    def _vmess_clash_tls(clash: dict, transport_host: str, server: str):
        """clash vmess 的 TLS 层。reality-opts 在场即 reality（对齐
        clash_import._tls_q 口径：reality-opts 优先于 tls 布尔），其次
        tls: true。server_name 回退链 servername/sni → 传输 host 参数 →
        server 地址；skip-cert-verify → insecure；alpn 列表透传。
        utls 指纹沿用本文件 _vless_tls 的现行口径
        （_normalize_utls_fingerprint：client-fingerprint/fingerprint 在场
        用之、"unsafe"/未知值不写 utls、缺省 chrome）。"""
        ro = clash.get("reality-opts")
        reality = isinstance(ro, dict) and bool(ro)
        if not reality and not clash.get("tls"):
            return None
        sni = (str(clash.get("servername") or "").strip()
               or str(clash.get("sni") or "").strip()
               or transport_host or server)
        fp = _normalize_utls_fingerprint(
            clash.get("client-fingerprint") or clash.get("fingerprint"))
        tls = {"enabled": True, "server_name": sni}
        if fp:
            tls["utls"] = {"enabled": True, "fingerprint": fp}
        if reality:
            pbk = str(ro.get("public-key") or "").strip()
            if not pbk:
                raise EngineError(
                    "vmess 节点缺 reality 公钥（reality-opts.public-key）")
            tls["reality"] = {"enabled": True, "public_key": pbk,
                              "short_id": str(ro.get("short-id") or "").strip()}
        if clash.get("skip-cert-verify"):
            tls["insecure"] = True
        alpn = clash.get("alpn")
        if isinstance(alpn, (list, tuple)) and alpn:
            tls["alpn"] = [str(a) for a in alpn]
        elif isinstance(alpn, str) and alpn.strip():
            tls["alpn"] = [alpn.strip()]
        return tls

    def _hysteria2_outbound(self, node: Node) -> dict:
        """hysteria2 出站。凭证字段双来源：clash 条目（password|auth，口径
        同 clash_import._extra_hysteria2）优先，extra["password"] 直存字段
        兜底——manual.py 今天不解析 hy2:// 链接（_LINK_SCHEMES 里没有），
        但任何按现有 extra 约定写凭证的导入器即刻可用。obfs 的 type/password
        来自 clash 条目；up/down 有才写（解析不出正整数也不写，留空走
        sing-box 的 BBR，写坏值反而起不来）。"""
        clash, query = self._extra_maps(node)
        extra = node.extra or {}
        password = str(clash.get("password") or clash.get("auth")
                       or extra.get("password") or "")
        if not password:
            raise EngineError("hysteria2 节点缺 password")
        self._need_endpoint("hysteria2", node.host, node.port)
        out = {"type": "hysteria2", "tag": _OUT_TAG, "server": node.host,
               "server_port": int(node.port), "password": password}
        obfs = str(clash.get("obfs") or "").strip()
        if obfs:
            obfs_password = clash.get("obfs-password")
            if obfs_password is None or str(obfs_password) == "":
                # sing-box v1.14.4 protocol/hysteria2/outbound.go：obfs 在场
                # 而 password 为空 → "missing obfs password" 起不来，先行报错
                raise EngineError(
                    "hysteria2 节点 obfs 缺 obfs-password（sing-box 要求 "
                    "obfs 混淆密码必填）")
            out["obfs"] = {"type": obfs, "password": str(obfs_password)}
        up = self._clash_bandwidth(clash.get("up"))
        if up:
            out["up_mbps"] = up
        down = self._clash_bandwidth(clash.get("down"))
        if down:
            out["down_mbps"] = down
        out["tls"] = self._quic_tls(clash, query, node.host)
        return out

    def _tuic_outbound(self, node: Node) -> dict:
        """tuic 出站。uuid/password 双来源（clash 条目优先、extra 直存字段
        兜底，口径同 clash_import._extra_tuic）；congestion-controller 与
        udp-relay-mode 是 mihomo 条目字段名，对应 sing-box 的
        congestion_control / udp_relay_mode，有才写、原样透传（sing-box
        v1.14.4 对非法 udp_relay_mode 静默忽略、congestion_control 交给
        tuic 库，坏值不会静默变成别的语义）。"""
        clash, query = self._extra_maps(node)
        extra = node.extra or {}
        uuid = str(clash.get("uuid") or extra.get("uuid") or "").strip()
        if not uuid:
            raise EngineError("tuic 节点缺 uuid")
        password = str(clash.get("password") or extra.get("password") or "")
        if not password:
            raise EngineError("tuic 节点缺 password")
        self._need_endpoint("tuic", node.host, node.port)
        out = {"type": "tuic", "tag": _OUT_TAG, "server": node.host,
               "server_port": int(node.port), "uuid": uuid,
               "password": password}
        congestion = str(clash.get("congestion-controller")
                         or clash.get("congestion-control") or "").strip()
        if congestion:
            out["congestion_control"] = congestion
        udp_relay = str(clash.get("udp-relay-mode") or "").strip()
        if udp_relay:
            out["udp_relay_mode"] = udp_relay
        out["tls"] = self._quic_tls(clash, query, node.host)
        return out

    def _wireguard_endpoint(self, node: Node) -> dict:
        """wireguard：sing-box 1.11 起 wireguard outbound 弃用、1.13 起移除
        （v1.14.4 无 protocol/wireguard/outbound.go、outbound 注册表无此类型），
        出站行为由 endpoint 承担。endpoint 的 tag 沿用 node-out：route.final
        与 remote-dns 的 detour 标签查找会回落到 endpoint 管理器
        （v1.14.4 adapter/outbound/manager.go Outbound() 尾部），单节点
        单出站语义不变。字段口径：clash 条目的 private-key/public-key/
        pre-shared-key/ip/ipv6/reserved/mtu（对齐 clash_import
        ._extra_wireguard 的保真映射）。"""
        clash, _ = self._extra_maps(node)
        extra = node.extra or {}
        private_key = str(clash.get("private-key") or
                          extra.get("private_key") or "").strip()
        if not private_key:
            raise EngineError("wireguard 节点缺 private-key")
        public_key = str(clash.get("public-key") or
                         extra.get("public_key") or "").strip()
        if not public_key:
            raise EngineError("wireguard 节点缺 public-key（对端公钥）——"
                              "没有对端身份无法握手，不许静默生成残缺配置")
        self._need_endpoint("wireguard", node.host, node.port)
        # 本地接口地址：sing-box endpoint 必填 address（v1.14.4 文档
        # Required）。这里对任务书"有才写"有意收紧：ip/ipv6 都缺时不产出
        # 必挂配置，而是显式报错（见 DESIGN.md）
        address = []
        for value, prefix in ((clash.get("ip") or extra.get("local_address"), 32),
                              (clash.get("ipv6") or extra.get("local_address_v6"),
                               128)):
            local = str(value or "").strip()
            if local:
                address.append(local if "/" in local else f"{local}/{prefix}")
        if not address:
            raise EngineError("wireguard 节点缺本地地址（clash 的 ip/ipv6）"
                              "——sing-box wireguard endpoint 必填 address")
        allowed_ips = clash.get("allowed-ips")
        if isinstance(allowed_ips, (list, tuple)) and allowed_ips:
            allowed_ips = [str(a) for a in allowed_ips]
        else:
            allowed_ips = ["0.0.0.0/0"]            # 任务书默认口径
        peer = {"address": node.host, "port": int(node.port),
                "public_key": public_key, "allowed_ips": allowed_ips}
        psk = clash.get("pre-shared-key")
        if psk is None:
            psk = extra.get("pre_shared_key")
        if psk is not None and str(psk) != "":
            peer["pre_shared_key"] = str(psk)
        reserved = clash.get("reserved")
        if reserved is None:
            reserved = extra.get("reserved")
        if reserved is not None and reserved != "":
            # 不在任务字段清单里的保真补充：WARP 类节点必须带 reserved 才能
            # 握手，clash_import 原样保存了它（列表或字符串，sing-box 两种
            # 都收——[]uint8 支持 JSON 数组与 base64 串）
            peer["reserved"] = reserved
        endpoint = {"type": "wireguard", "tag": _OUT_TAG,
                    "address": address, "private_key": private_key,
                    "peers": [peer]}
        mtu = clash.get("mtu")
        if mtu:
            endpoint["mtu"] = int(mtu)
        return endpoint

    @staticmethod
    def _decode_vmess(config_ref) -> dict:
        """vmess://<base64 JSON> → dict。解码失败或缺 id/add 一律 EngineError
        （消息只说解析失败，不回显链接原文——P3）。"""
        ref = str(config_ref or "").strip()
        if not ref.lower().startswith("vmess://"):
            raise EngineError("vmess 链接解析失败: 缺少 vmess:// 前缀")
        payload = ref[len("vmess://"):].strip()
        padded = payload + "=" * (-len(payload) % 4)
        data = None
        for decode in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                data = json.loads(decode(padded).decode("utf-8"))
                break
            except ValueError:                     # 含 binascii/JSON/Unicode 错误
                continue
        if not isinstance(data, dict):
            raise EngineError("vmess 链接解析失败: base64/JSON 解不出来")
        if not str(data.get("id") or "").strip():
            raise EngineError("vmess 链接解析失败: 缺 id 字段")
        if not str(data.get("add") or "").strip():
            raise EngineError("vmess 链接解析失败: 缺 add 字段")
        return data

    # ---------------- 进程管理 ----------------
    @property
    def proxy_url(self) -> str:
        return f"socks5://127.0.0.1:{self.socks_port}"

    @property
    def http_proxy_url(self) -> str:
        """HTTP 入站的代理地址。验出口探针（urllib）只会说 HTTP 代理、
        不会 SOCKS5，所以探针走这条；流量仍经同一个 node-out 出站。"""
        return f"http://127.0.0.1:{self.http_port}"

    def find_lx_binary(self) -> str:
        """xhttp 专用第二内核（sing-box-lx，Leadaxe fork，带 with_xhttp）：
        构造参数 lx_binary → 环境变量 GATE_CLIENT_SINGBOX_LX_BIN →
        ~/.local/bin/sing-box-lx → shutil.which("sing-box-lx")。
        显式路径不存在 → 判未找到、不回落（同主内核口径）。找不到返回 ""。"""
        if self._lx_binary:
            return self._lx_binary if os.path.isfile(self._lx_binary) else ""
        env_bin = os.environ.get(_ENV_LX_BIN) or ""
        if env_bin:
            return env_bin if os.path.isfile(env_bin) else ""
        home_bin = os.path.expanduser(_HOME_LX_BIN)
        if os.path.isfile(home_bin):
            return home_bin
        return shutil.which("sing-box-lx") or ""

    def start(self, node: Node, on_event=None):
        try:
            config = self.build_config(node)
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(f"sing-box 配置生成失败: {exc}") from exc
        if self.takeover_mode == "tun":
            # TUN 提权前提检查（task6/DESIGN §10）：v1=pkexec+root wrapper。
            # wrapper 未实现，当前版本一律显式拒绝——配置生成可用，激活
            # 未就绪；绝不以普通用户态悄悄起一个不接管的半 TUN。
            self._check_tun_privilege()
        binary = self.find_binary()
        transport = (config.get("outbounds") or [{}])[0].get("transport") or {}
        if transport.get("type") == "xhttp":
            lx = self.find_lx_binary()
            if lx:
                binary = lx          # xhttp 只走第二内核，主内核保持官方版
        if not binary:
            explicit = self._binary or os.environ.get(_ENV_BIN) or ""
            raise self._missing_binary_error(explicit)

        # 启动前先清扫孤儿引擎（兜底，失败绝不影响本次启动）
        try:
            self.reap_stale_engines()
        except Exception:
            pass
        # 健康预检①（task5 兜底）：孤儿清扫后端口仍被占，说明是别的程序
        # （其他代理软件/残留引擎）在听——直接报人话错误，不发起注定失败
        # 的启动。任何调用方（GUI/CLI/托盘）都过这一关。
        occupied = check_ports_free((self.socks_port, self.http_port))
        if occupied:
            raise EngineError(
                f"引擎端口被占用，启动被挡：{describe_occupied(occupied)}。"
                "关掉占用它的程序（其他代理软件？）或改它的端口，再点一次连接。")
        config_dir = tempfile.mkdtemp(prefix="gate-singbox-")
        config_path = os.path.join(config_dir, "config.json")
        log_path = os.path.join(config_dir, "sing-box.log")
        log_file = None
        try:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
            transport = (config.get("outbounds") or [{}])[0].get("transport") or {}
            if transport.get("type") == "xhttp":
                self._check_xhttp_capable(binary, config_path, config_dir)
            # stdout/stderr 落日志文件而非 PIPE（长跑会堵满管道），失败还能拿尾巴
            log_file = open(log_path, "wb")
            proc = self._popen([binary, "run", "-c", config_path],
                               stdin=subprocess.DEVNULL,
                               stdout=log_file, stderr=log_file)
        except OSError as exc:
            if log_file is not None:
                log_file.close()
            shutil.rmtree(config_dir, ignore_errors=True)
            raise EngineError(f"无法启动 sing-box（{binary}）: {exc}") from exc
        log_file.close()                           # 子进程持有自己的 fd，父侧及时关

        handle = _Handle(node_id=node.id, proc=proc,
                         config_dir=config_dir, log_path=log_path)
        self._live_handles.append(handle)
        self._write_engine_meta(handle)

        # 就绪等待：每 0.1 秒试连本地 socks 端口（单次 0.3s 超时），
        # 每轮顺带 poll——进程早退=失败；超时=失败（顺带停掉还活着的进程）
        deadline = time.monotonic() + self.ready_timeout
        while True:
            if proc.poll() is not None:
                self._fail_start(handle, "进程提前退出")
            if time.monotonic() >= deadline:
                self._fail_start(handle,
                                 f"等待本地端口 {self.socks_port} 就绪超时")
            try:
                sock = socket.create_connection(
                    ("127.0.0.1", self.socks_port), timeout=0.3)
                sock.close()
                break
            except OSError:
                time.sleep(0.1)

        if on_event:
            try:
                on_event(EngineEvent(kind="connected", detail=self.proxy_url))
            except Exception:
                pass                               # 回调异常不能弄丢已就绪的隧道
        return handle

    @staticmethod
    def _check_xhttp_capable(binary: str, config_path: str, config_dir: str) -> None:
        """xhttp 先验：官方 sing-box（v1.14.4 真机实测）根本不认这种传输，
        run 起来只会立刻 FATAL 退出、报错难懂。先跑一次 check 把话说清：
        内核不支持就明说——配置没问题，是内核缺这块。check 本身跑不起来
        （超时/异常）则不拦，交给后面的 run 路径按老规矩报错。"""
        try:
            r = subprocess.run([binary, "check", "-c", config_path],
                               capture_output=True, text=True, timeout=15)
        except (OSError, subprocess.SubprocessError):
            return
        if r.returncode == 0:
            return
        out = ((r.stdout or "") + (r.stderr or "")).strip()
        shutil.rmtree(config_dir, ignore_errors=True)
        if "unknown transport type" in out and "xhttp" in out:
            raise EngineError(
                f"当前内核不支持 xhttp 传输（{binary} 的 check 报 "
                "unknown transport type: xhttp）——官方 sing-box v1.14.4 "
                "实测不带 xhttp；把 sing-box-lx 放到 ~/.local/bin/sing-box-lx "
                "后 xhttp 节点会自动改用它，节点配置本身没问题")
        raise EngineError(f"sing-box 配置校验未通过: {out[:300]}")

    def _fail_start(self, handle: _Handle, reason: str) -> None:
        """启动失败收尾：读日志尾巴 → 停进程 → 删临时目录（含 uuid/密码的配置）
        → 带尾巴抛错。尾巴必须先读——目录马上就删了。"""
        self._untrack(handle)
        tail = self._log_tail(handle.log_path)
        self._terminate(handle.proc)
        shutil.rmtree(handle.config_dir, ignore_errors=True)
        if tail:
            raise EngineError(f"sing-box 启动失败: {tail}")
        raise EngineError(f"sing-box 启动失败: {reason}")

    @staticmethod
    def _log_tail(log_path: str, limit: int = 20) -> str:
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
        except OSError:
            return ""
        lines = [ln for ln in lines if ln.strip()]
        return " | ".join(lines[-limit:])[:2000]

    def stop(self, handle) -> None:
        if handle is None:
            return
        self._untrack(handle)
        proc = getattr(handle, "proc", None)
        if proc is not None:
            self._terminate(proc)
        config_dir = getattr(handle, "config_dir", "")
        if config_dir:
            shutil.rmtree(config_dir, ignore_errors=True)
            handle.config_dir = ""

    def active_log_path(self):
        """只读访问器（task7 集成，见 work7/DESIGN §5.1）：当前活动会话的
        sing-box 日志路径（config_dir/sing-box.log），供诊断导出
        （diag_collect，集成期）取引擎日志尾部。

        无活动会话（未连接 / 已停止 / 进程已退出）返回 None。
        只读：不暴露句柄、不改引擎状态、不碰文件系统。
        """
        for handle in reversed(self._live_handles):
            proc = getattr(handle, "proc", None)
            if proc is not None and proc.poll() is None:
                return getattr(handle, "log_path", "") or None
        return None

    # ---------------- 生命周期兜底（孤儿引擎清扫） ----------------
    # 背景（2026-11-21 真机）：探测引擎的所有者进程异常消失后，stop()
    # 无人调用，sing-box 子进程被 init 收养、残留一下午（占着 10818）。
    # 三层兜底：①实例登记 live 句柄 + atexit 退出必停；②每次 start 前
    # 清扫孤儿；③引擎目录里写 engine.json 登记所有者，供跨进程识别。

    def _write_engine_meta(self, handle) -> None:
        """把所有者/进程信息写进引擎自己的临时目录。写失败绝不影响
        启动——清扫只是兜底，不是主流程。"""
        try:
            meta = {
                "owner_pid": os.getpid(),
                "proc_pid": getattr(handle.proc, "pid", None),
                "socks_port": self.socks_port,
                "http_port": self.http_port,
                "started_at": time.time(),
            }
            with open(os.path.join(handle.config_dir, _ENGINE_META),
                      "w", encoding="utf-8") as f:
                json.dump(meta, f)
        except Exception:
            pass

    def _untrack(self, handle) -> None:
        self._live_handles = [h for h in self._live_handles
                              if h is not handle]

    def _stop_all_live(self) -> None:
        """atexit 兜底：本实例还没停的引擎全部停掉。"""
        for handle in list(self._live_handles):
            try:
                self.stop(handle)
            except Exception:
                pass

    @staticmethod
    def _pid_alive(pid) -> bool:
        if not pid:
            return False
        try:
            pid = int(pid)
        except (TypeError, ValueError):
            return False
        try:
            os.kill(pid, 0)
        except PermissionError:
            return True
        except OSError:
            return False
        try:  # 僵尸已死、只是还没被收尸，别当活人
            with open(f"/proc/{pid}/stat", "r", encoding="utf-8") as f:
                if f.read().rsplit(")", 1)[1].split()[0] == "Z":
                    return False
        except (OSError, IndexError):
            pass
        return True

    @staticmethod
    def _pid_ppid(pid):
        try:
            with open(f"/proc/{int(pid)}/stat", "r", encoding="utf-8") as f:
                return int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _find_pid_by_config(config_dir: str):
        """无登记的老引擎：按 config.json 路径反查还活着的进程。"""
        target = os.path.join(config_dir, "config.json")
        try:
            entries = os.listdir("/proc")
        except OSError:
            return None
        for entry in entries:
            if not entry.isdigit():
                continue
            try:
                with open(f"/proc/{entry}/cmdline", "rb") as f:
                    if target.encode() in f.read().split(b"\0"):
                        return int(entry)
            except OSError:
                continue
        return None

    @staticmethod
    def _kill_pid(pid) -> None:
        """给非子进程的孤儿发 SIGTERM，短暂等待，还活着就 SIGKILL。"""
        try:
            os.kill(int(pid), signal.SIGTERM)
        except (OSError, ValueError):
            return
        for _ in range(20):
            if not SingBoxEngine._pid_alive(pid):
                return
            time.sleep(0.1)
        try:
            os.kill(int(pid), signal.SIGKILL)
        except (OSError, ValueError):
            pass

    @classmethod
    def reap_stale_engines(cls, root: str = None) -> int:
        """清扫孤儿引擎，返回清扫数量。所有者还活着的一律不碰；
        清扫自身出错不外抛。判定孤儿的条件（任一）：
        ①有 engine.json 且登记的所有者进程已死；
        ②无登记（旧版本）：进程已被 init 收养（ppid==1）且目录
          超过 1 小时，或进程已不存在且目录超过 24 小时（纯垃圾
          目录，内含配置残留，一并删掉）。"""
        root = root or tempfile.gettempdir()
        reaped = 0
        try:
            names = os.listdir(root)
        except OSError:
            return 0
        for name in names:
            if not name.startswith(_TEMP_PREFIX):
                continue
            config_dir = os.path.join(root, name)
            if not os.path.isdir(config_dir):
                continue
            try:
                if cls._reap_one(config_dir):
                    reaped += 1
            except Exception:
                continue
        return reaped

    @classmethod
    def _reap_one(cls, config_dir: str) -> bool:
        meta = {}
        try:
            with open(os.path.join(config_dir, _ENGINE_META),
                      "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (OSError, ValueError):
            meta = {}
        owner_pid = meta.get("owner_pid")
        proc_pid = meta.get("proc_pid")
        if owner_pid:
            if cls._pid_alive(owner_pid):
                return False            # 所有者活着，不是孤儿
        else:
            if not proc_pid:
                proc_pid = cls._find_pid_by_config(config_dir)
            try:
                age = time.time() - os.path.getmtime(config_dir)
            except OSError:
                return False
            if proc_pid and cls._pid_alive(proc_pid):
                if cls._pid_ppid(proc_pid) != 1 or age < 3600:
                    return False        # 还挂在活进程树上，或还太新
            elif age < 86400:
                return False            # 进程已不在，但目录还不够老
        if proc_pid and cls._pid_alive(proc_pid):
            cls._kill_pid(proc_pid)
        shutil.rmtree(config_dir, ignore_errors=True)
        return True

    def _terminate(self, proc) -> None:
        """terminate → wait 5 秒 → 还活着就 kill → wait。进程已死则直接返回。"""
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            pass

    def status(self, handle) -> str:
        proc = getattr(handle, "proc", None) if handle is not None else None
        if proc is None:
            return "stopped"
        return "running" if proc.poll() is None else "stopped"
