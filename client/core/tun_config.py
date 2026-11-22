#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TUN 模式配置生成（core/tun_config.py）
========================================
task6 · 阶段三·连接体验 第二段，v0.10.0 集成。纯标准库、可独立 import，
不依赖 PySide6 / client 包；不读文件、不碰网络、不碰系统路由——只产出 dict。

设计依据见 task6 DESIGN.md（~/workspace/zcode-tasks/work6/DESIGN.md：
参数选型、DNS 防泄漏三层口径、权限模型）。
sing-box 内核钉版 1.14.4，字段口径以官方文档
（configuration/inbound/tun、configuration/route、configuration/dns/rule、
configuration/route/rule_action，2026-11-21 核对）为准：

- tun inbound 1.14 用 ``address`` 列表（1.10 起合并 inet4/inet6_address，
  旧字段 1.12 移除，不写旧字段）；``stack`` 在 1.15 才废弃，钉版内合法。
- ``route.auto_detect_interface`` 文档原文「prevent routing loops under
  tun」——TUN 模式必须显式 true，否则引擎自身出站（node-out / local-dns）
  会被自己的默认路由吸回 tun，形成回环。
- DNS 防泄漏靠 route 规则 ``{"action": "sniff"}`` + ``{"protocol": "dns",
  "action": "hijack-dns"}``（1.11+ 规则动作口径），把进 tun 的 DNS 查询
  送进 DNS 模块；再配 dns 规则按 inbound 指到 remote-dns（经 node-out
  绕行，出口侧解析）并压 strategy=ipv4_only（tun 地址只配 IPv4，放行
  真实 AAAA 会让应用拿到 v6 后绕过隧道直连——泄漏）。

三个入口：
- build_tun_inbound(**overrides) → sing-box tun inbound dict
- build_tun_config(base_config, inbound, dns_server_tag=None) → 合并后的完整 config
- mode_plan(mode) → 该模式下引擎 inbound 种类 + 系统侧动作清单（纯数据）

接线见 DESIGN.md §10：引擎 takeover_mode="tun" 时 build_config 尾部并入；
TUN 激活（pkexec + root wrapper）未实现，start() 会显式拒绝，见
engines/singbox_engine.py 的 _check_tun_privilege。

铁律对齐：本模块没有任何节点选择/切换逻辑——TUN 只是接管方式，节点恒由
调用方显式指定；生成的配置仍是单节点单出站（route.final 原样保留）。
"""

from __future__ import annotations

import copy
import ipaddress
import re

# ---------------- 常量（DESIGN.md 推荐选型的单一事实来源） ----------------

TUN_TAG = "tun-in"              # tun 入站 tag；dns 规则与呈现文案引用它
TUN_INTERFACE_NAME = "gate-tun"  # 固定网卡名：健康检测/排障可按名查找
TUN_ADDRESS = ["172.19.0.1/30"]  # 见 DESIGN：避开内核默认 172.18/30 与常见 198.18/30
TUN_MTU = 1500                   # 见 DESIGN：对齐以太网/真实 WAN，链路行为可预期
TUN_STACK = "system"             # 见 DESIGN：Linux 桌面首选，性能与成熟度
TUN_DNS_MODE = "hijack"          # 1.14 显式写死：不随内核版本默认值漂移

# mtu 合法区间：下限 576 = IPv4 端到端不分片的最小保证（RFC 791），
# 再小的 tun 没有实际用途；上限 65521 是 Linux tun 驱动的硬上限
# （ippkt 最大长度 65535 减 IP 头等开销），9000/1500 都在区间内。
MTU_MIN = 576
MTU_MAX = 65521

STACKS = ("system", "gvisor", "mixed")

# Linux 网卡名硬约束：IFNAMSIZ=16（含结尾 NUL → 最长 15 字符），
# 可见字符集取 iproute2 实际接受的字母数字与 ._-（/、空格等必挂）。
_IFNAME_MAX_LEN = 15
_IFNAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")

_PROXY_SYSTEM_ACTIONS = (
    {
        "actor": "user",
        "action": "把系统代理（HTTP/HTTPS）手动指到引擎 HTTP 入站"
                  "（默认 127.0.0.1:7897）；SOCKS 入站（默认 7898）供命令行工具选配",
        "required": True,
        "privileged": False,
    },
)

_TUN_SYSTEM_ACTIONS = (
    {
        "actor": "system",
        "action": "获取 TUN 权限（pkexec 提权或独立 helper），由内核创建 "
                  + TUN_INTERFACE_NAME + " 网卡并接管默认路由（auto_route）",
        "required": True,
        "privileged": True,
        "needs": "CAP_NET_ADMIN",
    },
    {
        "actor": "user",
        "action": "无需配置应用（TUN 已接管全机流量）；首次开启会弹出系统"
                  "授权对话框，取消授权即启动失败",
        "required": False,
        "privileged": False,
    },
)


# ---------------- 入口一：tun inbound ----------------

def build_tun_inbound(**overrides) -> dict:
    """生成 sing-box tun inbound dict；默认值 = DESIGN.md 推荐选型。

    非法参数抛 ValueError 并说明原因（不静默纠正、不产出半成品配置）：
    - mtu：非整数 / 越界（576..65521）；
    - stack：不在 {system, gvisor, mixed}；
    - address：不是「地址/前缀」形式的合法 CIDR（列表或单个字符串均可，
      允许 IPv4 与 IPv6，但必须是精确主机位——网络地址形如 172.19.0.0/30
      会被拒，tun 网卡要的是本机地址）；
    - auto_route / strict_route：非布尔；
    - interface_name：空、超 15 字符（IFNAMSIZ）或含非法字符；
    - tag / dns_mode：非空字符串；
    - 未知参数名：一律拒绝（拼错参数名必须当场暴露，不许静默吞）。
    """
    unknown = set(overrides) - {"tag", "interface_name", "address", "mtu",
                                "auto_route", "strict_route", "stack",
                                "dns_mode"}
    if unknown:
        raise ValueError(
            "未知 TUN 参数: " + ", ".join(sorted(unknown))
            + "——支持 tag/interface_name/address/mtu/auto_route/"
              "strict_route/stack/dns_mode")

    tag = overrides.get("tag", TUN_TAG)
    interface_name = overrides.get("interface_name", TUN_INTERFACE_NAME)
    address = overrides.get("address", list(TUN_ADDRESS))
    mtu = overrides.get("mtu", TUN_MTU)
    auto_route = overrides.get("auto_route", True)
    strict_route = overrides.get("strict_route", True)
    stack = overrides.get("stack", TUN_STACK)
    dns_mode = overrides.get("dns_mode", TUN_DNS_MODE)

    if not isinstance(tag, str) or not tag.strip():
        raise ValueError(f"tag 必须是非空字符串，得到 {tag!r}")
    if not isinstance(interface_name, str) or not interface_name.strip():
        raise ValueError(
            f"interface_name 必须是非空字符串，得到 {interface_name!r}——"
            "sing-box 虽可自动分配 tun0/tun1…，但固定网卡名是健康检测与"
            "排障的锚点（DESIGN.md）")
    if len(interface_name) > _IFNAME_MAX_LEN:
        raise ValueError(
            f"interface_name 长度 {len(interface_name)} 超过 Linux IFNAMSIZ "
            f"上限 {_IFNAME_MAX_LEN}: {interface_name!r}")
    if not _IFNAME_RE.match(interface_name):
        raise ValueError(
            f"interface_name 含非法字符（只允许字母数字与 . _ -）: "
            f"{interface_name!r}")
    if isinstance(auto_route, bool):
        if not auto_route:
            raise ValueError(
                "auto_route=False 不能用于 TUN 接管——没有默认路由接管就"
                "不存在全机流量接管（DESIGN.md）；要只在本地代理模式请用 "
                "mode_plan('proxy')")
    else:
        raise ValueError(f"auto_route 必须是布尔值，得到 {auto_route!r}")
    if not isinstance(strict_route, bool):
        raise ValueError(f"strict_route 必须是布尔值，得到 {strict_route!r}")

    address = _normalize_address(address)
    if isinstance(stack, bool) or not isinstance(stack, str) \
            or stack not in STACKS:
        raise ValueError(
            f"stack 必须是 {'/'.join(STACKS)} 之一，得到 {stack!r}")
    if isinstance(mtu, bool) or not isinstance(mtu, int):
        raise ValueError(f"mtu 必须是整数，得到 {mtu!r}")
    if not MTU_MIN <= mtu <= MTU_MAX:
        raise ValueError(
            f"mtu {mtu} 越界（合法区间 {MTU_MIN}..{MTU_MAX}）——"
            "低于 576 违背 IPv4 最小不分片保证，高于 65521 超出 Linux "
            "tun 驱动上限")
    if not isinstance(dns_mode, str) or not dns_mode.strip():
        raise ValueError(
            f"dns_mode 必须是非空字符串，得到 {dns_mode!r}（推荐写死 "
            f"{TUN_DNS_MODE}，理由见 DESIGN.md DNS 三层口径）")

    return {
        "type": "tun",
        "tag": tag,
        "interface_name": interface_name,
        "address": address,
        "mtu": mtu,
        "dns_mode": dns_mode,
        "auto_route": auto_route,
        "strict_route": strict_route,
        "stack": stack,
    }


def _normalize_address(address) -> list:
    """address → 字符串列表，逐项校验「主机地址/前缀」形式。

    用 ipaddress.ip_interface（保主机位）而非 ip_network：172.19.0.5/30
    这种网络地址形态对 tun 网卡没有意义，必须拦截；缺前缀（裸 IP）同样
    拒绝——sing-box 对缺前缀的行为不明确，宁可显式报错。"""
    if isinstance(address, str):
        address = [address]
    if not isinstance(address, (list, tuple)) or not address:
        raise ValueError(
            f"address 必须是非空 CIDR 列表（或单个字符串），得到 {address!r}")
    normalized = []
    for item in address:
        if isinstance(item, bool) or not isinstance(item, str):
            raise ValueError(
                f"address 项必须是字符串 CIDR，得到 {item!r}")
        text = item.strip()
        try:
            iface = ipaddress.ip_interface(text)
        except ValueError as exc:
            raise ValueError(
                f"address 项不是合法 CIDR（地址/前缀形式）: {item!r}（{exc}）"
            ) from exc
        if "/" not in text:
            raise ValueError(
                f"address 项缺前缀长度，必须写完整的 地址/前缀 形式: "
                f"{text!r}（如 172.19.0.1/30）")
        if iface.ip == iface.network.network_address \
                and iface.network.prefixlen < iface.network.max_prefixlen:
            raise ValueError(
                f"address 项是网络地址而不是主机地址（{text!r}）——tun "
                "网卡的本地地址不能占网段网络位，例如应写 172.19.0.1/30 "
                "而不是 172.19.0.0/30（/32、/128 整段主机地址合法）")
        normalized.append(str(iface))
    return normalized


# ---------------- 入口二：并入完整 config ----------------

def build_tun_config(base_config: dict, inbound: dict,
                     dns_server_tag=None) -> dict:
    """把 tun inbound 并入一份完整引擎 config（dict），返回新 dict。

    按 DESIGN.md 的 DNS 三层口径补齐：
    1. route 规则前置 ``{"action": "sniff"}`` 与 ``{"protocol": "dns",
       "action": "hijack-dns"}``——进 tun 的 DNS 查询送进 DNS 模块；
    2. dns 规则新增 ``{"inbound": [tun tag], "server": remote-dns}``——
       被劫持的查询经节点绕行解析（现行 remote-dns 口径，防泄漏+防本机
       解析器被其他代理 fake-ip 劫持）；
    3. route.auto_detect_interface=True——引擎自身出站绑物理网卡防回环。

    base_config 由调用方传入（完整深拷贝，绝不改动调用方的原 dict）；
    dns_server_tag 省略时自动识别「detour 指向 route.final 的 DNS 服务器」
    （即现行 remote-dns 的定义性特征），识别不到就显式报错——没有经节点
    绕行的 DNS 服务器，TUN 模式的 DNS 防泄漏不成立，拒绝凭空猜测。
    """
    if not isinstance(base_config, dict):
        raise ValueError(f"base_config 必须是 dict，得到 {type(base_config).__name__}")
    if not isinstance(inbound, dict) or inbound.get("type") != "tun":
        raise ValueError(
            f"inbound 必须是 type=tun 的 dict（用 build_tun_inbound 生成），"
            f"得到 {inbound!r}")
    tag = str(inbound.get("tag") or "").strip()
    if not tag:
        raise ValueError("inbound 缺 tag——DNS 规则要按 inbound tag 匹配")

    config = copy.deepcopy(base_config)
    inbounds = config.get("inbounds")
    if not isinstance(inbounds, list):
        raise ValueError("base_config 缺 inbounds 列表——不是完整的引擎配置")
    if any(isinstance(x, dict) and x.get("tag") == tag for x in inbounds):
        raise ValueError(
            f"base_config 已有同名入站 tag {tag!r}——拒绝静默覆盖/重复")

    # 先核 route 形状（route.final 是 DNS 服务器识别的依据），再定位
    # 绕行 DNS 服务器；两处都过不了就不动 base_config 任何内容
    _require_route_shape(config)
    dns_server_tag = _resolve_dns_server_tag(config, dns_server_tag)

    if "auto_route" not in inbound or "address" not in inbound:
        raise ValueError(
            "inbound 缺 auto_route/address——必须是 build_tun_inbound 的"
            "完整产物；手工拼的半成品 inbound 会产出「起得来但不接管」的"
            "假 TUN 配置，拒绝合并")

    inbounds.append(copy.deepcopy(inbound))

    rules = config["route"]["rules"]
    # 前置两条规则（顺序有语义）：sniff 必须在 protocol=dns 匹配之前
    # 完成，劫持规则必须在既有 resolve 规则之前吃掉 DNS 流量，否则 DNS
    # 查询会被 resolve 当成普通连接经出口解析后再绕一圈。
    rules.insert(0, {"protocol": "dns", "action": "hijack-dns"})
    rules.insert(0, {"action": "sniff"})

    dns = config["dns"]
    if not isinstance(dns.get("rules"), list):
        dns["rules"] = []
    # strategy=ipv4_only 是防 v6 泄漏的关键（DESIGN.md DNS 三层口径第 2 层
    # 的补强）：tun 地址只配 IPv4（v6 流量进不了隧道），若放行真实 AAAA
    # 应答，拿到 v6 地址的应用会绕过 tun 走物理网卡直连——泄漏。压掉
    # AAAA 让应用回落 IPv4，与现行 resolve 规则的 ipv4_only 口径一致。
    # （dns 规则的 strategy 是 1.14 文档标注 legacy 的动作选项，钉版
    # 1.14.4 内有效；内核升级时的替代写法见 DESIGN.md 待核实项。）
    dns["rules"].insert(0, {"inbound": [tag], "server": dns_server_tag,
                            "strategy": "ipv4_only"})

    auto_detect = config["route"].get("auto_detect_interface")
    if auto_detect is False:
        raise ValueError(
            "base_config 显式 auto_detect_interface=False 与 TUN 模式冲突"
            "（引擎自身出站会被吸回 tun 形成回环）——拒绝静默改写，请先"
            "修正 base_config")
    config["route"]["auto_detect_interface"] = True
    return config


def _resolve_dns_server_tag(config: dict, dns_server_tag) -> str:
    """确定被劫持 DNS 应使用的 DNS 服务器 tag。

    显式传入时校验其存在于 dns.servers；省略时按定义性特征识别现行
    remote-dns：server 条目带 detour 且 detour == route.final（经节点
    出站绕行的解析器）。识别不到显式报错，不猜。"""
    dns = config.get("dns")
    if not isinstance(dns, dict) or not isinstance(dns.get("servers"), list):
        raise ValueError("base_config 缺 dns.servers——不是完整的引擎配置")
    final = (config.get("route") or {}).get("final") \
        if isinstance(config.get("route"), dict) else None
    if dns_server_tag is not None:
        if not isinstance(dns_server_tag, str) or not dns_server_tag.strip():
            raise ValueError(f"dns_server_tag 必须是非空字符串，得到 {dns_server_tag!r}")
        tags = {s.get("tag") for s in dns["servers"] if isinstance(s, dict)}
        if dns_server_tag not in tags:
            raise ValueError(
                f"dns_server_tag {dns_server_tag!r} 不在 dns.servers 中"
                f"（现有: {sorted(t for t in tags if t)}）")
        return dns_server_tag
    detoured = [s for s in dns["servers"]
                if isinstance(s, dict) and s.get("detour")]
    if final and len(detoured) == 1 and detoured[0].get("detour") == final:
        return str(detoured[0].get("tag"))
    raise ValueError(
        "无法识别经节点绕行的 DNS 服务器（detour 指向 route.final 的唯一"
        " server 条目）——TUN 模式的 DNS 防泄漏以此为基础，拒绝猜测；"
        "请显式传 dns_server_tag")


def _require_route_shape(config: dict) -> None:
    route = config.get("route")
    if not isinstance(route, dict) or not isinstance(route.get("rules"), list):
        raise ValueError("base_config 缺 route.rules——不是完整的引擎配置")
    if not route.get("final"):
        raise ValueError("base_config 缺 route.final——TUN 合并后必须仍是"
                         "单节点单出站语义，拒绝无出口的半成品")


# ---------------- 入口三：模式计划（纯数据，供 GUI/CLI 消费） ----------------

MODES = ("proxy", "tun")

PROXY_INBOUND_KINDS = ("socks", "http")
TUN_INBOUND_KINDS = ("socks", "http", "tun")


def mode_plan(mode: str) -> dict:
    """按接管方式返回引擎应生成的 inbound 种类清单与系统侧动作清单。

    纯数据、无副作用：GUI/CLI 据此展示「开 TUN 需要什么」（如提权提示）、
    校验当前模式可行性；清单里没有任何节点相关内容——接管方式与选哪个
    节点无关（铁律）。未知模式抛 ValueError。
    """
    mode = str(mode or "").strip().lower()
    if mode == "proxy":
        return {
            "mode": "proxy",
            "engine_inbound_kinds": list(PROXY_INBOUND_KINDS),
            "system_actions": [dict(x) for x in _PROXY_SYSTEM_ACTIONS],
            "notes": [
                "引擎只监听 127.0.0.1，无需任何权限",
                "不走系统代理的应用（游戏/部分桌面程序/命令行工具）不会被接管",
            ],
        }
    if mode == "tun":
        return {
            "mode": "tun",
            "engine_inbound_kinds": list(TUN_INBOUND_KINDS),
            "system_actions": [dict(x) for x in _TUN_SYSTEM_ACTIONS],
            "notes": [
                "socks/http 入站保留：健康探测（经 HTTP 入站验出口）与本地"
                "工具继续可用",
                "TUN 启动失败时显式报错，绝不静默回退到系统代理模式",
            ],
        }
    raise ValueError(
        f"未知接管方式 {mode!r}——支持 {'/'.join(MODES)}")
