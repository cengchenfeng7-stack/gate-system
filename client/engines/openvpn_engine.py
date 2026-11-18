#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
OpenVPN 引擎（engines/openvpn_engine.py）
===========================================
封装 vendored 的 _vpn_dial.py（gate-system/frontend/vpn_dial.py 原样复制）：
- 配置消毒/解码：decode_config / sanitize_ovpn（原样复用）
- 写节点文件：write_node_files（原样复用，含凭证文件与危险指令剥离日志）
- 命令行：build_cmd；进程守护：supervise（在后台线程跑，连上即返回 handle，
  掉线由 supervise 结论经 status() 反映给 orchestrator 巡检）
- 停进程：stop_process

找不到 openvpn 二进制时 start() 抛 EngineError（明确中文原因，不抛栈）。
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from typing import Optional

from ..core.model import Node
from .base import EngineError, EngineEvent


def _load_vpn_dial():
    """延迟加载 vendored vpn_dial（先装 vpngate 垫片，见 _vpngate_shim）。"""
    from . import _vpngate_shim
    _vpngate_shim.install()
    from . import _vpn_dial
    return _vpn_dial


@dataclass
class _Handle:
    node_id: str
    proc: object = None
    thread: Optional[threading.Thread] = None
    result: Optional[tuple] = None          # supervise 的 (kind, detail, was_connected)
    connected_evt: threading.Event = field(default_factory=threading.Event)
    ovpn_path: str = ""


class OpenVPNEngine:
    name = "openvpn"

    def __init__(self, out_dir: str, binary: Optional[str] = None,
                 connect_timeout: float = 45.0, popen_factory=None):
        self.out_dir = out_dir
        self._binary = binary               # None=启动时用 vendored find_openvpn 找
        self.connect_timeout = connect_timeout
        self._popen = popen_factory or subprocess.Popen
        self._vd = None                     # vendored vpn_dial，延迟加载

    # ---------------- 内部 ----------------
    def _dialect(self):
        if self._vd is None:
            self._vd = _load_vpn_dial()
        return self._vd

    def find_binary(self) -> str:
        if self._binary is not None:
            return self._binary
        return self._dialect().find_openvpn()

    def render_config(self, node: Node) -> str:
        """生成消毒后的 .ovpn 文本（dry-run/测试用，不落盘不拨号）。"""
        vd = self._dialect()
        if node.extra.get("config_format") == "text":
            raw = node.config_ref
        else:
            raw = vd.decode_config(node.config_ref)
        text, _removed = vd.sanitize_ovpn(raw, "vpngate_creds.txt")
        return text

    # ---------------- Engine 接口 ----------------
    def start(self, node: Node, on_event=None):
        vd = self._dialect()
        binary = self.find_binary()
        if not binary:
            raise EngineError(
                "未找到 openvpn 可执行文件——请安装 OpenVPN Community，"
                "或用环境变量 OPENVPN_BIN 指定路径")
        os.makedirs(self.out_dir, exist_ok=True)
        node_dict = {"host": node.host, "port": node.port}
        if node.extra.get("config_format") == "text":
            # 明文配置：落盘前同样过消毒（write_node_files 只吃 b64 路径）
            text = self.render_config(node)
            safe = "".join(c if c.isalnum() or c in ".-" else "_" for c in node.host)
            ovpn_path = os.path.join(self.out_dir, f"node_{safe}_{node.port}.ovpn")
            with open(ovpn_path, "w", encoding="utf-8") as f:
                f.write(text)
            creds_path = os.path.join(self.out_dir, "vpngate_creds.txt")
            if not os.path.exists(creds_path):
                from . import _vpngate_shim  # noqa: F401  (确保常量来源一致)
                import vpngate as _vp
                with open(creds_path, "w", encoding="utf-8") as f:
                    f.write(f"{_vp.SSTP_USER}\n{_vp.SSTP_PASS}\n")
        else:
            try:
                ovpn_path, _creds = vd.write_node_files(
                    node_dict, node.config_ref, self.out_dir)
            except Exception as exc:
                raise EngineError(f"节点配置生成失败: {exc}") from exc

        cmd = vd.build_cmd(binary, os.path.basename(ovpn_path))
        try:
            proc = self._popen(cmd, cwd=self.out_dir, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as exc:
            raise EngineError(f"无法启动 OpenVPN（{cmd[0]}）: {exc}") from exc

        handle = _Handle(node_id=node.id, proc=proc, ovpn_path=ovpn_path)

        def emit(line):
            if ">>> 已连上" in line:
                handle.connected_evt.set()
            if on_event:
                on_event(EngineEvent(kind="log", detail=line))

        def run_supervise():
            try:
                handle.result = vd.supervise(proc, self.connect_timeout, emit)
            except Exception as exc:  # supervise 异常按失败结论处理，不静默
                handle.result = (vd.R_FAILED, f"supervise 异常: {exc}", False)
            handle.connected_evt.set()      # 无论成败都唤醒 start()

        handle.thread = threading.Thread(target=run_supervise, daemon=True)
        handle.thread.start()

        # 等 supervise 给出「连上」或任何失败结论
        while not handle.connected_evt.is_set():
            handle.connected_evt.wait(timeout=0.2)
            if handle.result is not None:
                break
        if handle.result is not None:
            kind, detail, _was = handle.result
            vd.stop_process(proc)
            if kind == vd.R_ENV:
                raise EngineError(f"环境错误（换节点没用）: {detail}")
            raise EngineError(f"连接失败: {detail}")
        return handle

    def stop(self, handle) -> None:
        if handle is None:
            return
        vd = self._dialect()
        if handle.proc is not None:
            vd.stop_process(handle.proc)

    def status(self, handle) -> str:
        if handle is None or handle.proc is None:
            return "stopped"
        if handle.result is not None:
            return "stopped"                # supervise 已有结论（掉线/退出）
        return "running" if handle.proc.poll() is None else "stopped"
