#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sing-box 引擎（engines/singbox_engine.py）
============================================
把 vless / ss(shadowsocks) / trojan / vmess 单节点翻译成 sing-box 1.14 配置，
拉起本地代理进程（socks+http 双入站，只绑 127.0.0.1）：
- build_config：纯函数式生成配置（dry-run/测试用，不落盘不起进程）
- start：临时目录写配置 → 子进程日志重定向到临时文件 → 等本地端口就绪 → handle
- stop：terminate → wait 5s → 还活着就 kill，然后删临时目录
  （临时配置里有 uuid/密码，停机必须删——红线）
- status：诚实汇报 "running"/"stopped"，不假报

铁律：手动固定节点、严禁自动切换——配置恒为单节点单出站（唯一出站
tag=node-out，route.final 指向它），绝不生成 urltest / selector。
找不到 sing-box 二进制时 start() 抛 EngineError（中文安装指引，不抛栈）。
本期不做 TUN、不做多节点/负载/分流（route 无 rules），验出口不归本引擎管。
"""

from __future__ import annotations

import atexit
import base64
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from dataclasses import dataclass
from typing import Optional

from ..core.model import Node
from .base import EngineError, EngineEvent

DEFAULT_SOCKS_PORT = 7898
DEFAULT_HTTP_PORT = 7897
SINGBOX_PROTOCOLS = ("vless", "ss", "trojan", "vmess")

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


class SingBoxEngine:
    name = "sing-box"

    def __init__(self, binary: Optional[str] = None,
                 socks_port: int = DEFAULT_SOCKS_PORT,
                 http_port: int = DEFAULT_HTTP_PORT,
                 ready_timeout: float = 15.0, popen_factory=None,
                 lx_binary: Optional[str] = None):
        # 构造器不碰文件系统、不找二进制——二进制解析懒到 find_binary/start
        self._binary = binary
        self._lx_binary = lx_binary
        self.socks_port = int(socks_port)
        self.http_port = int(http_port)
        self.ready_timeout = float(ready_timeout)
        self._popen = popen_factory or subprocess.Popen
        self._live_handles = []
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

    # ---------------- 配置生成 ----------------
    def build_config(self, node: Node) -> dict:
        """纯函数式生成 sing-box 1.14 配置（不落盘不起进程）。

        单节点单出站：outbounds 只有 node-out 一条，route.final 指向它；
        缺必填参数直接抛 EngineError 点名缺哪个字段，不生成半成品配置。
        """
        protocol = str(getattr(node, "protocol", "") or "").strip().lower()
        if protocol not in SINGBOX_PROTOCOLS:
            raise EngineError(
                f"不支持节点协议 {protocol or '未知'}——sing-box 引擎只支持 "
                + "/".join(SINGBOX_PROTOCOLS))
        try:
            if protocol == "vless":
                outbound = self._vless_outbound(node)
            elif protocol == "ss":
                outbound = self._ss_outbound(node)
            elif protocol == "trojan":
                outbound = self._trojan_outbound(node)
            else:
                outbound = self._vmess_outbound(node)
        except EngineError:
            raise
        except Exception as exc:
            raise EngineError(f"sing-box 配置生成失败: {exc}") from exc
        return {
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
            "outbounds": [outbound],
            "route": {
                "rules": [{"action": "resolve", "server": "remote-dns",
                           "strategy": "ipv4_only"}],
                "final": _OUT_TAG,
                # 出站服务器域名一律走本地解析（sing-box 1.14 硬性要求
                # 显式指定 domain_resolver，否则 check 直接 FATAL）
                "default_domain_resolver": "local-dns",
            },
        }

    @staticmethod
    def _need_endpoint(label: str, host, port) -> None:
        if not str(host or "").strip():
            raise EngineError(f"{label} 节点缺 server 地址")
        if not port:
            raise EngineError(f"{label} 节点缺端口")

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
    def _vless_tls(query: dict, transport_host: str, server: str):
        """security=tls/reality → tls 块；缺省/none → None。server_name 回退链：
        sni → 传输 host 参数 → server 地址。"""
        security = str(query.get("security") or "").strip().lower()
        if security not in ("tls", "reality"):
            return None
        sni = str(query.get("sni") or "").strip() or transport_host or server
        fp = str(query.get("fp") or "").strip() or "chrome"
        tls = {"enabled": True, "server_name": sni,
               "utls": {"enabled": True, "fingerprint": fp}}
        if security == "reality":
            pbk = str(query.get("pbk") or "").strip()
            if not pbk:
                raise EngineError("vless 节点缺 reality 公钥（query pbk）")
            tls["reality"] = {"enabled": True, "public_key": pbk,
                              "short_id": str(query.get("sid") or "").strip()}
        return tls

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
            out["flow"] = flow
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
        return {"type": "shadowsocks", "tag": _OUT_TAG, "server": node.host,
                "server_port": int(node.port),
                "method": method, "password": password}

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
