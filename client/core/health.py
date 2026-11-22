#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
连接健康三件套（core/health.py）—— 检测 + 判读模块
=======================================================
task5（阶段三·连接体验 第一段）落地：只做「检测 + 判读」，零自动行为——
不自动切换节点、不自动重连、不自动断开、不改系统代理设置（客户端铁律，
见 zcode-tasks/work5/DESIGN.md「与铁律的相容性」一节）。

三件套：
1. check_ports_free(ports, bind_host)    引擎端口被别的代理软件占用？
2. parse_gsettings_proxy(text) /         系统代理被别的代理软件占用？
   read_system_proxy(runner) /
   proxy_conflict(sysproxy, our_http_port)
3. connection_health(samples) /          连着的节点是不是已经死了？
   connection_health_detail(samples)
4. check_tun_takeover(...)                TUN 模式：默认路由真的接管到
   （task6/DESIGN §6.4，接管生效性，只读 /proc/net/route）gate-tun 了吗？

只用标准库，不依赖 PySide6、不依赖 client 包其它模块（接线层调用本模块，
本模块绝不反向调用 orchestrator / 引擎）。
不发任何外部网络请求：端口检测只对 bind_host（默认 127.0.0.1）做 bind
探测；占用进程名/pid 通过只读 /proc 查询。Linux 专用行为（/proc）查不到
时自动降级为只报端口，绝不抛错影响主流程。
"""

from __future__ import annotations

import errno
import os
import re
import socket
import subprocess

# ---------------------------------------------------------------------------
# 三件套之一：引擎端口占用检测
# ---------------------------------------------------------------------------
# 探测口径（2026-11-21 本机内核实测，见 test_health.py 同口径用例）：
# - 用 SO_REUSEADDR 的 socket 去 bind：任何**活跃监听**（0.0.0.0 通配或
#   127.0.0.1 精确、对方带不带 REUSEADDR）都会 EADDRINUSE——不漏报；
# - 只有 TIME_WAIT 残留时不挡 REUSEADDR bind——不误报（客户端引擎
#   sing-box/Go 默认带 REUSEADDR，探测结论与引擎实际能否 bind 一致）。
# 不用 connect 探测：bind 已覆盖全部监听场景，connect 反而会真的去碰
# 占用方进程的 socket，没有必要。

def check_ports_free(ports, bind_host: str = "127.0.0.1") -> list:
    """逐个尝试 bind 判断端口占用，返回**被占用**端口清单（空列表=全空闲）。

    每项是 {"port": int, "pid": int|None, "process": str|None}：能查到占用
    进程时附 pid 与进程名（只读 /proc/net/tcp* 反查 inode → /proc/<pid>），
    查不到（权限不足/非 Linux/瞬时竞态）就只报端口，绝不抛错。

    端口必须是 1-65535 的整数，否则 ValueError（集成期抓接线错误，
    不静默吞掉）。
    """
    occupied = []
    for port in ports:
        port = int(port)
        if not 1 <= port <= 65535:
            raise ValueError(f"端口超出合法范围 1-65535: {port}")
        if _can_bind(bind_host, port):
            continue
        pid, name = _find_port_owner(port)
        occupied.append({"port": port, "pid": pid, "process": name})
    return occupied


def _can_bind(host: str, port: int) -> bool:
    """REUSEADDR bind 探测：True=当前可 bind（引擎能起），False=被占。"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        return True
    except OSError as exc:
        # EADDRINUSE=被占；EACCES=没权限绑（<1024 端口等）——对引擎来说
        # 同样起不来，按占用处理。其余 errno（参数错等）也按占用报，
        # 由上层 reason 带出 errno，不在这里猜。
        if exc.errno in (errno.EADDRINUSE, errno.EACCES):
            return False
        return False
    finally:
        sock.close()


def _find_port_owner(port: int):
    """从 /proc 反查占用端口的进程 → (pid, 进程名)；查不到返回 (None, None)。

    只读、纯标准库、失败一律静默降级——占用进程可能属于其他用户
    （/proc/<pid>/fd 不可读）或内核态，查不到名字是常态不是错误。
    """
    inodes = _port_inodes(port)
    if not inodes:
        return None, None
    try:
        entries = sorted((e for e in os.listdir("/proc") if e.isdigit()),
                         key=int)
    except OSError:
        return None, None
    for pid in entries:
        found = _fd_socket_inode_match(pid, inodes)
        if not found:
            continue
        return int(pid), _process_name(pid) or None
    return None, None


def _port_inodes(port: int) -> set:
    """收集 /proc/net/tcp{,6} 里本地端口==port 的 socket inode。
    优先 LISTEN（0A）；没有 LISTEN 项时退回任意状态（bound 未 listen 等）。"""
    by_state_listen, any_state = set(), set()
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path, "r", encoding="ascii", errors="replace") as f:
                header = True
                for line in f:
                    if header:
                        header = False
                        continue
                    parts = line.split()
                    if len(parts) < 10:
                        continue
                    local, st, inode = parts[1], parts[3], parts[9]
                    try:
                        local_port = int(local.rsplit(":", 1)[1], 16)
                    except (IndexError, ValueError):
                        continue
                    if local_port != port:
                        continue
                    any_state.add(inode)
                    if st == "0A":
                        by_state_listen.add(inode)
        except OSError:
            continue
    return by_state_listen or any_state


def _fd_socket_inode_match(pid: str, inodes: set) -> bool:
    """该进程的 fd 里是否有指向目标 inode 的 socket。"""
    try:
        fds = os.listdir(f"/proc/{pid}/fd")
    except OSError:
        return False
    for fd in fds:
        try:
            link = os.readlink(f"/proc/{pid}/fd/{fd}")
        except OSError:
            continue
        if link.startswith("socket:[") and link[8:-1] in inodes:
            return True
    return False


def _process_name(pid: int) -> str:
    """进程名：/proc/<pid>/comm（无则退 cmdline 首参 basename）。"""
    try:
        with open(f"/proc/{pid}/comm", "r", encoding="utf-8",
                  errors="replace") as f:
            name = f.read().strip()
        if name:
            return name
    except OSError:
        pass
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            first = f.read().split(b"\0")[0]
        return os.path.basename(first.decode("utf-8", "replace")) or ""
    except OSError:
        return ""


def describe_occupied(occupied: list) -> str:
    """占用清单 → 人话一句话（GUI 告警条/CLI 输出直接用）。空列表返回 ""。"""
    parts = []
    for item in occupied:
        who = ""
        if item.get("process"):
            who = f"「{item['process']}（pid {item['pid']}）」"
        elif item.get("pid"):
            who = f"「pid {item['pid']}」"
        parts.append(f"端口 {item['port']}" + (f"（被 {who} 占用）" if who else "（被占用，进程名查不到）"))
    return "；".join(parts)


# ---------------------------------------------------------------------------
# 三件套之二：系统代理指向检测（GNOME gsettings）
# ---------------------------------------------------------------------------
# 真机事故背景：Karing/Clash 类软件开着「系统代理」时，系统流量全走它，
# gate-client 面板显示已连接、实际流量与状态对不上。GNOME 的系统代理
# 存在 gsettings：org.gnome.system.proxy 的 mode（none/manual/auto），
# org.gnome.system.proxy.http 的 host/port。
#
# gsettings get 输出是 GVariant 文本格式（跨版本实测变体已收录进解析器
# 与测试；沙盒无 GNOME，解析规则按多形态防御实现，真机口径见 DONE.md
# 待核实项）：
#   字符串：'manual'   整数：7897 或 uint32 7897（带类型前缀）

_GSETTINGS_MODE_CMD = ["gsettings", "get", "org.gnome.system.proxy", "mode"]
_GSETTINGS_HOST_CMD = ["gsettings", "get", "org.gnome.system.proxy.http", "host"]
_GSETTINGS_PORT_CMD = ["gsettings", "get", "org.gnome.system.proxy.http", "port"]

_UINT_PREFIX = re.compile(
    r"^(?:uint64|uint32|uint16|uint8|int64|int32|int16|int8|byte)\s+(.*)$")


def _unquote(text: str) -> str:
    """GVariant 字符串字面量去引号：'manual' / "manual" → manual。"""
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def parse_gsettings_proxy(text: str) -> dict:
    """解析三条 gsettings get 的合并输出（mode、http host、http port，
    每条一行、按此顺序；多余空行容忍）→ 结构化结果。

    返回 {"mode": str|None, "http_host": str|None, "http_port": int|None}。
    解析不出（缺行/空文本/端口非数字）对应值为 None——上层
    proxy_conflict 对 None 的口径是「判不了，不硬判」，不猜。
    """
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    mode = _unquote(lines[0]).lower() if len(lines) >= 1 else None
    host = _unquote(lines[1]) if len(lines) >= 2 else None
    port = None
    if len(lines) >= 3:
        raw = _UINT_PREFIX.match(_unquote(lines[2]))
        raw = (raw.group(1) if raw else _unquote(lines[2])).strip()
        try:
            port = int(raw)
        except (TypeError, ValueError):
            port = None
    return {"mode": mode or None, "http_host": host or None,
            "http_port": port}


def read_system_proxy(runner=None) -> dict:
    """真实读取系统代理：依次跑三条 gsettings get，合并输出交给
    parse_gsettings_proxy。runner(cmd: list[str]) -> str 可注入以便测试。

    返回 {"available": bool, "mode": ..., "http_host": ..., "http_port": ...,
    "error": str}。gsettings 不存在/调用失败 → available=False，error 带原因
    （非 GNOME 桌面是常态，上层按「判不了」处理，不算冲突）。
    """
    if runner is None:
        runner = _gsettings_runner
    chunks = []
    error = ""
    try:
        for cmd in (_GSETTINGS_MODE_CMD, _GSETTINGS_HOST_CMD,
                    _GSETTINGS_PORT_CMD):
            chunks.append(runner(cmd))
    except Exception as exc:                       # gsettings 缺失/超时等
        error = f"{type(exc).__name__}: {exc}"
    if chunks:
        result = parse_gsettings_proxy("\n".join(chunks))
        result.update(available=True, error="")
        return result
    return {"available": False, "mode": None, "http_host": None,
            "http_port": None, "error": error or "runner 未返回任何输出"}


def _gsettings_runner(cmd) -> str:
    """默认 runner：subprocess 跑 gsettings，返回 stdout。失败抛 OSError/
    subprocess 异常，由 read_system_proxy 统一兜住。"""
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
    if proc.returncode != 0:
        raise RuntimeError(
            f"gsettings 退出码 {proc.returncode}: {(proc.stderr or '').strip()}")
    return proc.stdout or ""


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost", ""})


def proxy_conflict(sysproxy, our_http_port: int) -> dict:
    """判读系统代理是否与我们的引擎打架 → {"conflict": bool, "reason": str}。

    口径（任务书原文）：系统代理为 manual 且指向**不是**我们引擎端口
    → conflict=True 并给人话原因。判不了的情况（gsettings 缺失、mode
    解析不出、manual 但端口读不出）一律 conflict=False + 说清判不了
    的原因——宁可"判不了"，不硬判误报。

    our_http_port 缺失按集成错误处理，直接 ValueError（引擎端口是集成层
    必给的参数，None 说明接线错了，当场暴露）。
    """
    try:
        our_http_port = int(our_http_port)
    except (TypeError, ValueError):
        raise ValueError(f"our_http_port 必须是整数端口，得到: {our_http_port!r}")
    if sysproxy is None:
        return {"conflict": False,
                "reason": "系统代理状态读不到（非 GNOME 或 gsettings 缺失），"
                          "跳过冲突判读"}
    if not sysproxy.get("available", True):
        return {"conflict": False,
                "reason": f"系统代理状态读不到（{sysproxy.get('error') or '未知原因'}），"
                          "跳过冲突判读"}
    mode = sysproxy.get("mode")
    if mode is None:
        return {"conflict": False,
                "reason": "系统代理 mode 解析不出，跳过冲突判读"}
    if mode != "manual":
        if mode == "none":
            return {"conflict": False, "reason": "系统代理未开启（mode=none）"}
        return {"conflict": False,
                "reason": f"系统代理为 {mode} 模式（自动配置），不直接指向"
                          "固定端口，不做冲突判读"}
    host = sysproxy.get("http_host") or ""
    port = sysproxy.get("http_port")
    if port is None:
        return {"conflict": False,
                "reason": "系统代理为 manual 模式但 HTTP 端口读不出，"
                          "无法判读是否指向本客户端"}
    if port == our_http_port and host.lower() in _LOOPBACK_HOSTS:
        return {"conflict": False,
                "reason": f"系统代理指向本客户端引擎端口（{host or '127.0.0.1'}:{port}），"
                          "与当前连接一致"}
    where = f"{host or '（未设 host）'}:{port}"
    return {"conflict": True,
            "reason": f"系统代理为 manual 模式，指向 {where}，不是本客户端"
                      f"引擎端口 {our_http_port}——疑似其他代理软件"
                      "（Karing/Clash 类）占着系统代理，流量不走本客户端"}


# ---------------------------------------------------------------------------
# 三件套之三：连接健康（节点死活）判定 —— 纯函数
# ---------------------------------------------------------------------------
# 输入样本由集成层周期探测产生（GUI 里经活动引擎的 http 入站探测，
# 见 DESIGN.md「触发时机」），这里只按「最近连续失败次数」分级。
# 阈值写成常量：suspect 提醒、dead 报死。死活只反映**当前连接**，
# 样本留内存即可，不落库（落库会混进养鱼池口径，见 DESIGN.md）。

HEALTH_OK = "ok"                # 连接正常（或失败未达阈值）
HEALTH_SUSPECT = "suspect"      # 连续失败 ≥ SUSPECT_FAILS：可疑，提醒
HEALTH_DEAD = "dead"            # 连续失败 ≥ DEAD_FAILS：基本确定节点死了
HEALTH_UNKNOWN = "unknown"      # 没有样本（还没开始探测）
HEALTH_STATES = (HEALTH_OK, HEALTH_SUSPECT, HEALTH_DEAD, HEALTH_UNKNOWN)

SUSPECT_FAILS = 3               # 连续失败 3 次 → suspect
DEAD_FAILS = 5                  # 连续失败 5 次 → dead


def _sample_ok(row) -> bool:
    """取一行样本的 ok 布尔值。dict 取 "ok" 键，其余对象取 .ok 属性。
    缺键/非 bool 一律抛错——探测样本是集成层自己造的，坏样本必须当场
    暴露，不许静默当失败或当成功（口径：不许静默丢弃）。"""
    if isinstance(row, dict):
        if "ok" not in row:
            raise ValueError(f"样本缺 ok 字段: {row!r}")
        value = row["ok"]
    else:
        value = getattr(row, "ok", None)
        if value is None and not hasattr(row, "ok"):
            raise ValueError(f"样本缺 ok 字段: {row!r}")
    if type(value) is not bool:
        raise ValueError(f"样本 ok 字段必须是 bool，得到 {value!r}")
    return value


def connection_health_detail(samples) -> dict:
    """connection_health 的明细版：状态 + 连败次数 + 样本数 + 最后样本。

    返回 {"status": 枚举, "fail_streak": int, "sample_count": int,
    "last_ok": bool|None}。fail_streak = 末尾连续失败次数（最后一样本
    为 ok 时为 0）。last_ok = 最后一条样本的 ok；无样本为 None。
    """
    if samples is None or isinstance(samples, (str, bytes)):
        raise TypeError("samples 必须是按时间升序的样本序列（list/tuple）")
    rows = list(samples)
    count = len(rows)
    if count == 0:
        return {"status": HEALTH_UNKNOWN, "fail_streak": 0,
                "sample_count": 0, "last_ok": None}
    oks = [_sample_ok(r) for r in rows]
    streak = 0
    for ok in reversed(oks):
        if ok:
            break
        streak += 1
    if streak >= DEAD_FAILS:
        status = HEALTH_DEAD
    elif streak >= SUSPECT_FAILS:
        status = HEALTH_SUSPECT
    else:
        status = HEALTH_OK
    return {"status": status, "fail_streak": streak,
            "sample_count": count, "last_ok": oks[-1]}


def connection_health(samples) -> str:
    """纯函数：按时间升序的样本行（每行至少含 ok: bool）→ 状态枚举之一。

    ok        —— 正常（含偶发失败 < SUSPECT_FAILS、失败后恢复等）
    suspect   —— 末尾连续失败 ≥ SUSPECT_FAILS(3)：提醒用户，连接还挂着
    dead      —— 末尾连续失败 ≥ DEAD_FAILS(5)：节点基本确定死了
    unknown   —— 样本为空（还没开始探测）
    只看末尾连败：任何一次成功都把连败清零（恢复即 ok）。
    """
    return connection_health_detail(samples)["status"]


# ---------------------------------------------------------------------------
# 检测④：TUN 接管生效性（task6/DESIGN §6.4，只读）
# ---------------------------------------------------------------------------
# 动机（「假接管」场景）：tun 设备建起来了、端口也开了、进程活着，但
# auto_route 因 netlink 写入失败只生效了一半——检测③照常 ok（探测走回环
# 入站，根本不经过路由），用户实际流量却没走隧道。
# 只读检查：解析 /proc/net/route，确认存在 Destination=00000000 且
# Iface=<tun 网卡名> 的默认路由条目。缺失 → 告警，不做任何修复动作
# （红线：只提示）。route_text 注入供测试；默认读真实 /proc/net/route。

def check_tun_takeover(route_path: str = "/proc/net/route",
                       interface_name: str = "gate-tun",
                       route_text: str = None) -> dict:
    """TUN 模式接管生效性（只读）→ {"effective": bool, "reason": str}。

    effective=True：默认路由（Destination 00000000）走 gate-tun，接管生效。
    effective=False：网卡已建但默认路由未接管（或路由表读不到）——流量
    没有走隧道。调用方只告警、不修路由。
    """
    if route_text is None:
        try:
            with open(route_path, "r", encoding="utf-8", errors="replace") as f:
                route_text = f.read()
        except OSError as exc:
            return {"effective": False,
                    "reason": f"TUN 接管生效性查不到：路由表读不到"
                              f"（{route_path}：{exc}）"}
    for line in (route_text or "").splitlines():
        fields = line.split()
        if len(fields) < 2:
            continue
        if fields[0] == "Iface":      # 表头行
            continue
        if fields[0] == interface_name and fields[1] == "00000000":
            return {"effective": True,
                    "reason": f"默认路由已接管到 {interface_name}，"
                              "流量走隧道"}
    return {"effective": False,
            "reason": f"TUN 网卡已建立，但默认路由未接管到 {interface_name}"
                      "——流量没有走隧道"}
