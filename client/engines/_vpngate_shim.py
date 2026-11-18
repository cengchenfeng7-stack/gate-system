#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vpngate 最小垫片（engines/_vpngate_shim.py）
==============================================
vendored 的 _vpn_dial.py 在模块头 `import vpngate as vp`，导入失败会
直接 sys.exit。客户端要求独立可运行（不依赖流水线目录），因此引擎在
导入 _vpn_dial 前先尝试真实 vpngate；找不到才装这个垫片。

垫片只提供引擎实际用到的东西：
- SSTP_USER / SSTP_PASS：write_node_files 写凭证文件用
  （默认 vpn/vpn，与 vpngate.py 的默认值一致；可用环境变量覆盖）
- die()：垫片下被调用即抛 RuntimeError（引擎路径不会走到）
- parse_csv / parse_mirror_json：转调 sources/feed.py 的同名复制实现
- BAN_HOSTS / PIN_HOSTS / _host_list_match：引擎不走候选挑选，给空实现

注意：垫片不改变 _vpn_dial.py 的任何逻辑，只是补上它 import 的名字。
"""

from __future__ import annotations

import os
import sys
import types


def install() -> bool:
    """真实 vpngate 可导入 → 返回 False（不用垫片）；否则装垫片 → True。"""
    try:
        import vpngate  # noqa: F401
        return False
    except Exception:
        pass
    if "vpngate" in sys.modules and hasattr(sys.modules["vpngate"], "SSTP_USER"):
        return True
    shim = types.ModuleType("vpngate")
    shim.SSTP_USER = os.environ.get("GATE_SSTP_USER", "vpn")
    shim.SSTP_PASS = os.environ.get("GATE_SSTP_PASS", "vpn")
    shim.BAN_HOSTS = set()
    shim.PIN_HOSTS = set()

    def die(msg):
        raise RuntimeError(str(msg))

    def _host_list_match(node, host_set):
        return False

    def parse_csv(text):  # 延迟导入，避免循环依赖
        from ..sources.feed import parse_csv as _impl
        return _impl(text)

    def parse_mirror_json(data):
        from ..sources.feed import parse_mirror_json as _impl
        return _impl(data)

    shim.die = die
    shim._host_list_match = _host_list_match
    shim.parse_csv = parse_csv
    shim.parse_mirror_json = parse_mirror_json
    sys.modules["vpngate"] = shim
    return True
