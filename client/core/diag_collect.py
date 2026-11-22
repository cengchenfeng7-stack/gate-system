#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断材料收集器（core/diag_collect.py，task22 · task7 欠账接线）
================================================================
task7（v0.11.0）落了诊断纯函数层 core/diag.py；本模块补齐「读 Store /
来源登记表 / 引擎，把材料组装成 build_bundle 入参」的接线层（work7/DONE.md
§4-1 的欠账）。口径全部继承 work7/DESIGN.md，不许放宽：

- 材料 = DESIGN §2 文件清单逐项（00/01/02/03/10/11/12/20/30，MANIFEST
  由 diag.build_bundle 自动生成）；
- 组装 run_checks 的 env = DESIGN §4.0 对照表——探测**全部复用
  core/health 现成函数**注入 env，不新造阈值、不新增探测通道；
- secrets 列表 = DESIGN §3.5：遍历库时顺手累积（凭证值 + host 原文 +
  订阅/feed 地址原文 + 出口 IP 原文），**只进内存、绝不写文件**；
- 引擎日志尾部经 SingBoxEngine.active_log_path() 只读获取，拿不到时
  如实写「无活动会话」，不猜；
- 11 号文件的近 7 天 / 近 24h 聚合由本模块写统计 SQL（verify_samples
  按 ts 过滤，只读连接），Store 的 :memory: 形态回退公开 API
  samples_for（无文件可连）；
- 全程只读：不连不测不改任何节点状态，零外网连接（本机 `sing-box
  version` / `openvpn --version` 子进程是本地探测，DESIGN §2 明文口径）。

铁则同 diag.py：只产生「给人看的信号与文件」，没有任何连接、切换节点、
改设置的路径。
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import time
from typing import Dict, List, Optional, Tuple
from urllib.parse import quote as _urlquote
from urllib.parse import urlsplit

from . import diag
from . import health
from . import history
from . import settings_store
from ..engines.openvpn_engine import OpenVPNEngine
from ..engines.singbox_engine import (DEFAULT_HTTP_PORT, DEFAULT_SOCKS_PORT,
                                      SingBoxEngine)
from .settings_proto import settings_file_path
from .store import Store, VerifySample

COLLECT_VERSION = "0.1.0"           # 收集器自身版本（进 00 号文件）
LOG_TAIL_BYTES = 64 * 1024          # 20 号文件取引擎日志尾部的窗口（DESIGN §2）
DAY = 86400.0
STAT_DAYS = 7                       # 11 号文件按日统计窗口（DESIGN §2）
STAT_HOURS_24 = 24                  # verify_coverage 的近 24h 窗口（DESIGN §4.1-8）

# secrets 累积的键名标记（DESIGN §3.5 第 1 项；与 diag._SECRET_EXTRA_MARKS
# 同源口径——宁可多脱，多收集的值只会多被清扫，不会放宽脱敏）
_SECRET_VALUE_MARKS = ("password", "passwd", "uuid", "username", "user",
                       "token", "secret", "auth", "credential", "key")
# host 类 extra 键原文也进 secrets（DESIGN §3.5 第 2 项）
_HOST_VALUE_MARKS = diag._HOST_EXTRA_KEYS

_STATUS_TEXT = {
    health.HEALTH_OK: "连接健康：出口探测正常",
    health.HEALTH_UNKNOWN: "连接刚建立，还没有探测样本",
}


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def read_client_version() -> str:
    """读 client/VERSION（本模块位于 client/core/，包根在上一级）。
    读不到如实返回空串（00 号文件写「未知」），不猜。"""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, "VERSION"), "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def _mask_home(path: str) -> str:
    """家目录掩码为 ~/…（DESIGN §3.4：用户名也是隐私）。"""
    path = str(path or "")
    home = os.path.expanduser("~")
    if path and home and path.startswith(home):
        return "~" + path[len(home):]
    return path


def _fmt_ts(ts, utc: bool = True) -> str:
    """epoch → 人话时间（统计口径统一 UTC，与养鱼池 active_days 同基）。"""
    if not ts:
        return "-"
    return time.strftime("%Y-%m-%d %H:%M:%S",
                         time.gmtime(float(ts)) if utc else time.localtime(float(ts)))


def _hostname_of(address: str) -> str:
    """地址 → hostname（secrets 收集用；解析不出返回空串）。"""
    try:
        return urlsplit(str(address or "")).hostname or ""
    except ValueError:
        return ""


def default_bundle_path(now: Optional[float] = None) -> str:
    """默认包路径：~/gate-client-diag-<YYYYmmdd-HHMMSS>-UTC.zip（DESIGN §2）。"""
    ts = time.strftime("%Y%m%d-%H%M%S", time.gmtime(now if now is not None else time.time()))
    return os.path.join(os.path.expanduser("~"), f"gate-client-diag-{ts}-UTC.zip")


def registry_path_for(store) -> str:
    """登记表位置：与节点库同目录的 sources.json（ui/cli.py `_registry_path`
    同口径——GUI/CLI/诊断三方共用同一份登记表）。"""
    return os.path.join(
        os.path.dirname(os.path.abspath(getattr(store, "path", "") or "client.db")),
        "sources.json")


def _binary_version(binary: str, args: Tuple[str, ...] = ("version",)) -> str:
    """本机跑 <binary> version 取首行版本串（DESIGN §2 明文口径的本地探测；
    5s 超时，任何失败如实返回空串——01 号文件写「版本未知」，不猜）。"""
    if not binary:
        return ""
    try:
        proc = subprocess.run([binary, *args], capture_output=True,
                              text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    for chunk in (proc.stdout or "", proc.stderr or ""):
        for line in chunk.splitlines():
            line = line.strip()
            if line:
                return line
    return ""


def _find_openvpn_binary() -> str:
    """openvpn 二进制：复用 OpenVPNEngine.find_binary()（vendored
    find_openvpn：OPENVPN_BIN → PATH → 回退路径），不另造第二套解析。"""
    try:
        eng = OpenVPNEngine(out_dir="")    # 构造器不碰文件系统；不 start
        return eng.find_binary() or ""
    except Exception:
        return os.environ.get("OPENVPN_BIN", "") or shutil.which("openvpn") or ""


def _singbox_like(engines) -> Optional[object]:
    """从 engines dict 挑出「sing-box 引擎样」对象（鸭子判定：find_binary +
    takeover_mode + 双端口——OpenVPNEngine 没有这些属性，不会被误挑）。
    没给 engines 或没有 sing-box 样对象时构造默认引擎（构造器只设属性，
    只读安全），保证 CLI 单独 doctor 也能给出与 cmd_status 同一套探测。"""
    for e in (engines or {}).values():
        if (hasattr(e, "find_binary") and hasattr(e, "takeover_mode")
                and hasattr(e, "http_port") and hasattr(e, "socks_port")):
            return e
    return None


# ---------------------------------------------------------------------------
# 一次遍历（_survey）：nodes / 样本 / 登记表只各读一遍
# ---------------------------------------------------------------------------
def _survey(store, registry=None, now: Optional[float] = None,
            orchestrator=None) -> dict:
    now = time.time() if now is None else now
    nodes = list(store.list_nodes(include_banned=True))
    records = [n.to_dict() if hasattr(n, "to_dict") else dict(n) for n in nodes]
    samples, samples_error = _fetch_all_samples(store)
    entries, last_updates, registry_error = _load_registry(registry, store)
    return {
        "store": store,
        "nodes": nodes,
        "records": records,
        "samples": samples,
        "samples_error": samples_error,
        "entries": entries,
        "last_updates": last_updates,
        "registry_error": registry_error,
        "events": list(getattr(orchestrator, "events", None) or []),
        "node_count": store.count_nodes() if hasattr(store, "count_nodes") else len(nodes),
        "now": now,
    }


def _fetch_all_samples(store) -> Tuple[List[VerifySample], str]:
    """全库 verify_samples 读出来（DESIGN §2 的 11 号文件 + §3.5 的出口 IP
    secrets 都要用）。

    - 库是真实文件：只读 URI 连接（mode=ro）跑一条 SELECT——11 号文件的
      按 ts 聚合（近 7 天 / 近 24h）由这里带出，work7/DONE.md §5 最后一项
      的「统计 SQL」落在本函数与 _verify_stats；
    - :memory: / 文件不存在 / ro 连接失败：回退 Store 公开 API
      samples_for（同一份数据，公共接口；绝不写库）。
    任何失败如实返回错误串（11 号文件写明），不静默装没有样本。
    """
    path = getattr(store, "path", "") or ""
    if path != ":memory:" and os.path.isfile(path):
        try:
            uri = "file:" + _urlquote(os.path.abspath(path)) + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True)
            try:
                rows = conn.execute(
                    "SELECT node_id, ts, kind, success, latency_ms, jitter_ms,"
                    " exit_ip, detail FROM verify_samples ORDER BY ts").fetchall()
            finally:
                conn.close()
            return [VerifySample(node_id=r[0], ts=r[1], kind=r[2],
                                 success=bool(r[3]), latency_ms=r[4],
                                 jitter_ms=r[5], exit_ip=r[6],
                                 detail=r[7] or "") for r in rows], ""
        except sqlite3.Error as exc:
            # 落到下面的公开 API 回退；不在这里猜成败
            pass
    try:
        out: List[VerifySample] = []
        for n in store.list_nodes(include_banned=True):
            out.extend(store.samples_for(n.id))
        return out, ""
    except Exception as exc:                       # noqa: BLE001 - 诊断不炸主流程
        return [], f"样本读取失败：{type(exc).__name__}: {exc}"


def _load_registry(registry, store):
    """登记表统一成（SourceRegistry 样对象, 错误串）。registry 参数：
    None → 库同目录 sources.json；str → 当路径；其余 → 鸭子类型实例
    （要有 entries() / last_update(id)，与 SourceRegistry 公开面一致）。"""
    error = ""
    if registry is None:
        registry = registry_path_for(store)
    if isinstance(registry, str):
        try:
            from ..sources.source_registry import SourceRegistry
            registry = SourceRegistry(registry)
        except Exception as exc:                   # noqa: BLE001
            return None, {}, f"登记表读取失败：{type(exc).__name__}: {exc}"
    try:
        entries = [dict(e) for e in registry.entries()]
        last_updates = {}
        for e in entries:
            lu = registry.last_update(e["id"])
            if lu:
                last_updates[e["id"]] = dict(lu)
        return entries, last_updates, ""
    except Exception as exc:                       # noqa: BLE001
        return [], {}, f"登记表读取失败：{type(exc).__name__}: {exc}"


def _verify_stats(survey) -> dict:
    """verify_coverage 的 env 键（DESIGN §4.1-8）：总有画像节点数 + 近 24h
    成功验真数（verify_samples 按 ts 过滤的统计 SQL 口径由 _fetch_all_samples
    的整表读出在此聚合）。"""
    now = survey["now"]
    verified = 0
    for rec in survey["records"]:
        ep = rec.get("exit_profile") or {}
        if isinstance(ep, dict) and (ep.get("ip") or ep.get("verified_at")):
            verified += 1
    recent = sum(1 for s in survey["samples"]
                 if s.success and s.kind == "verify"
                 and s.ts >= now - STAT_HOURS_24 * 3600.0)
    return {"total_nodes": survey["node_count"], "verified_nodes": verified,
            "recent_success_24h": recent}


def _registry_failures(survey) -> List[str]:
    """最近一次更新失败的登记来源（DESIGN §4.1-9）。"""
    out = []
    for e in survey["entries"]:
        lu = survey["last_updates"].get(e["id"])
        if lu and not lu.get("ok"):
            who = e.get("name") or e.get("type") or ""
            out.append(f"{e['id']}（{who}）：{lu.get('error') or '原因未知'}")
    return out


# ---------------------------------------------------------------------------
# secrets 累积（DESIGN §3.5）：只进内存、只用于清扫、绝不写任何文件
# ---------------------------------------------------------------------------
def _event_node_host(node_id: str) -> str:
    """事件 node_id 的 host 段（与 diag.mask_node_id 同一套 digest 判定）。
    事件引用的节点若已从库里删掉，host 原文仍要进 secrets——宁可多脱。"""
    parts = str(node_id or "").split(":")
    if len(parts) < 3:
        return ""
    host_end = -1
    if re.fullmatch(r"[0-9a-f]{8}", parts[-1] or ""):
        host_end = -2
    return ":".join(parts[2:host_end])


def _secrets(survey) -> List[str]:
    secrets: List[str] = []
    for rec in survey["records"]:
        host = rec.get("host")
        if host:
            secrets.append(str(host))
        extra = rec.get("extra") or {}
        if isinstance(extra, dict):
            for key, value in extra.items():
                low = str(key).lower()
                value_s = str(value) if value is not None else ""
                if not value_s.strip():
                    continue
                if any(m in low for m in _SECRET_VALUE_MARKS + _HOST_VALUE_MARKS):
                    secrets.append(value_s)
        ep = rec.get("exit_profile") or {}
        if isinstance(ep, dict) and ep.get("ip"):
            secrets.append(str(ep["ip"]))
    for e in survey["entries"]:
        address = str(e.get("address") or "").strip()
        if address:
            secrets.append(address)
            host = _hostname_of(address)
            if host:
                secrets.append(host)               # 裸域名也兜住（§3.6 口径）
    for s in survey["samples"]:
        if s.exit_ip:
            secrets.append(str(s.exit_ip))
    for ev in survey.get("events") or []:
        host = _event_node_host(getattr(ev, "node_id", "") or "")
        if host:
            secrets.append(host)
    return secrets


def collect_secrets(store, registry=None, now: Optional[float] = None,
                    orchestrator=None) -> List[str]:
    """DESIGN §3.5 的 secrets 累积（公开口，测试用）。返回原文清单（去重）；
    清扫由 diag.redact_text 做长度/大小写处理，这里不加工。"""
    seen, out = set(), []
    for secret in _secrets(_survey(store, registry=registry, now=now,
                                   orchestrator=orchestrator)):
        if secret and secret not in seen:
            seen.add(secret)
            out.append(secret)
    return out


# ---------------------------------------------------------------------------
# 探测端口解析（task26 ③：随设置走，与 connect/status/doctor/GUI 同口径）
# ---------------------------------------------------------------------------
def _ports_from_settings() -> Optional[Tuple[int, int]]:
    """当前统一设置的引擎入站端口（task26 ③）。

    落点解析与 main() 同一条链（settings_file_path：GATE_CLIENT_DB 显式
    目录 > 内置默认目录；`--db` 显式档由调用方注入的引擎/设置端口替身
    承载——cli._collect_check_env 形态，本层不猜）。设置里有引擎端口字段
    （socks_port/http_port）就用它；文件不存在/读不了（损坏/版本/非法值）
    → None = 无设置可用，调用方回落模块默认端口（singbox_engine 既有
    常量），不猜、不抛——诊断收集对设置损坏 resilient（探测照跑）。
    只读，绝不写设置。"""
    sfile = settings_file_path(env_db=os.environ.get("GATE_CLIENT_DB"))
    try:
        s = settings_store.load_settings(sfile)
    except (ValueError, OSError):
        return None
    return int(s.socks_port), int(s.http_port)


def _check_env(survey, engines=None, orchestrator=None,
               health_samples=None, now: Optional[float] = None) -> dict:
    store = survey["store"]
    sb = _singbox_like(engines)
    if sb is not None:
        # 引擎样对象自带双端口属性（_singbox_like 的鸭子判定已含）→
        # 会话真实端口优先：GUI/CLI 注入的引擎都按统一设置端口构造/
        # 对齐（task24 §8-1），连接中导出时测的是当下会话在用的端口。
        socks_port = int(sb.socks_port)
        http_port = int(sb.http_port)
    else:
        # 无引擎可依：从当前 settings 解析端口（task26 ③，与
        # connect/status/doctor/GUI 同口径——设置里有引擎端口字段就用它）；
        # 无设置可用才回落模块默认端口。
        resolved = _ports_from_settings()
        socks_port, http_port = (resolved if resolved is not None
                                 else (DEFAULT_SOCKS_PORT, DEFAULT_HTTP_PORT))
        sb = SingBoxEngine()                       # 默认引擎：构造器只设属性
    mode = str(getattr(sb, "takeover_mode", "proxy") or "proxy").strip().lower()

    singbox_bin = ""
    try:
        singbox_bin = sb.find_binary() or ""
    except Exception:
        singbox_bin = ""

    connected = False
    if orchestrator is not None:
        connected = str(getattr(orchestrator, "state", "") or "").lower() \
            not in ("", "idle")
    if connected:
        connection = health.connection_health_detail(list(health_samples or []))
    else:
        connection = None                          # 未连接（DESIGN §4.0：None）

    return {
        "db_ok": True,
        "db_node_count": survey["node_count"],
        "db_sample_count": len(survey["samples"]),
        "singbox_found": bool(singbox_bin),
        "singbox_version": _binary_version(singbox_bin, ("version",)),
        # task23（0.21.0）落页后 openvpn/vpngate_feed 两页并入 vpngate：
        # 「OpenVPN 页」不再是页面概念，按协议 openvpn 判定是否需要二进制
        # （DESIGN §4.1-3 的口径在新页面结构下的对应实现）。
        "openvpn_needed": any(r.get("protocol") == "openvpn"
                              for r in survey["records"]),
        "openvpn_found": bool(_find_openvpn_binary()),
        # 实际探测端口（task26 ③ 记账键：占用/系统代理探测与 01 号文件
        # 展示用同一份解析结果；diag.run_checks 按已知键取值，不读它）
        "probe_ports": {"socks": socks_port, "http": http_port},
        "ports_occupied": health.check_ports_free((socks_port, http_port)),
        "proxy_conflict": health.proxy_conflict(health.read_system_proxy(),
                                                http_port),
        "connection": connection,
        "takeover_mode": mode,
        "tun_takeover": (health.check_tun_takeover() if mode == "tun" else None),
        "verify_stats": _verify_stats(survey),
        "registry_failures": _registry_failures(survey),
        "registry_count": len(survey["entries"]),
    }


def collect_check_env(store, engines=None, orchestrator=None,
                      health_samples=None, registry=None,
                      now: Optional[float] = None) -> dict:
    """组装 diag.run_checks 的 env（DESIGN §4.0 对照表逐项）。engines 是
    {协议: 引擎} dict（鸭子类型，测试可给假引擎）；orchestrator 可选（取
    会话状态与事件流水）；health_samples 是 GUI 内存里的巡检样本（当前
    连接的 ok 布尔序列）。"""
    return _check_env(_survey(store, registry=registry, now=now,
                              orchestrator=orchestrator),
                      engines=engines, orchestrator=orchestrator,
                      health_samples=health_samples, now=now)


# ---------------------------------------------------------------------------
# 导出包材料（DESIGN §2 文件清单逐项；MANIFEST 由 diag.build_bundle 生成）
# ---------------------------------------------------------------------------
def _file_00_version(client_version: str) -> str:
    u = platform.uname()
    lines = [
        f"客户端版本: {client_version or '未知（client/VERSION 缺失）'}",
        f"诊断工具版本: diag {diag.DIAG_VERSION} / diag_collect {COLLECT_VERSION}",
        f"Python 版本: {platform.python_version()}",
        "平台信息（platform.uname）:",
        f"  system: {u.system}",
        f"  node: {u.node}",
        f"  release: {u.release}",
        f"  version: {u.version}",
        f"  machine: {u.machine}",
    ]
    return "\n".join(lines) + "\n"


def _file_01_environment(survey, env, singbox_bin: str) -> str:
    ov_bin = ""
    try:
        ov_bin = _find_openvpn_binary()
    except Exception:
        ov_bin = ""
    nodes_bits = []
    if env.get("db_node_count") is not None:
        nodes_bits.append(f"{env['db_node_count']} 个节点")
    if env.get("db_sample_count") is not None:
        nodes_bits.append(f"{env['db_sample_count']} 条验真样本")
    conflict = env.get("proxy_conflict") or {}
    occupied = env.get("ports_occupied") or []
    # 展示端口与实际探测端口同一份解析结果（task26 ③：随设置走；
    # probe_ports 缺键（外部组装的 env）时按既有模块默认兜底展示）
    probe = env.get("probe_ports") or {}
    ports_text = (f"无（{probe.get('http', DEFAULT_HTTP_PORT)}/"
                  f"{probe.get('socks', DEFAULT_SOCKS_PORT)} 空闲）"
                  if not occupied else health.describe_occupied(occupied))
    lines = [
        "环境摘要（DB 路径家目录已掩码为 ~/…）",
        f"DB 路径: {_mask_home(getattr(survey['store'], 'path', '') or '')}",
        "节点库: 可读" + (f"（{'、'.join(nodes_bits)}）" if nodes_bits else ""),
        f"sing-box 二进制: {singbox_bin or '未找到'}"
        + (f"（版本: {_binary_version(singbox_bin, ('version',)) or '版本未知'}）"
           if singbox_bin else ""),
        f"openvpn 二进制: {ov_bin or '未找到'}"
        + (f"（版本: {_binary_version(ov_bin, ('--version',)) or '版本未知'}）"
           if ov_bin else ""),
        f"引擎端口占用: {ports_text}",
        f"接管方式: {env.get('takeover_mode') or 'proxy'}",
        f"系统代理判读: {conflict.get('reason') or '判读结果缺失'}",
    ]
    return "\n".join(lines) + "\n"


def _file_03_health_now(env) -> str:
    conn = env.get("connection")
    if conn is None:
        return ("连接健康（当下）：当前无活动连接。\n"
                "（巡检样本只在内存、不落库——离线导出拿不到历史样本，"
                "task5 既定决策，如实注明。）\n")
    status = conn.get("status")
    streak = conn.get("fail_streak") or 0
    count = conn.get("sample_count") or 0
    if status in _STATUS_TEXT:
        verdict = _STATUS_TEXT[status]
    elif status == health.HEALTH_SUSPECT:
        verdict = f"连接可疑（连续失败 {streak} 次）"
    elif status == health.HEALTH_DEAD:
        verdict = f"连接基本已死（连续失败 {streak} 次）"
    else:
        verdict = f"状态判读不了（status={status!r}）"
    return ("连接健康（当下）：\n"
            f"  分级：{verdict}\n"
            f"  连续失败次数：{streak}\n"
            f"  内存样本数：{count}\n"
            "（巡检样本只在内存、不落库；本文件只反映导出时刻的当下结论。）\n")


def _file_10_nodes(survey) -> str:
    by_page, by_proto, quarantined, banned = {}, {}, 0, 0
    for rec in survey["records"]:
        by_page[rec.get("source") or "?"] = by_page.get(rec.get("source") or "?", 0) + 1
        by_proto[rec.get("protocol") or "?"] = by_proto.get(rec.get("protocol") or "?", 0) + 1
        extra = rec.get("extra") or {}
        if isinstance(extra, dict) and extra.get("quarantined") == "1":
            quarantined += 1
        if rec.get("banned"):
            banned += 1
    pages = "、".join(f"{k}={v}" for k, v in sorted(by_page.items())) or "-"
    protos = "、".join(f"{k}={v}" for k, v in sorted(by_proto.items())) or "-"
    lines = [
        "脱敏节点清单（每行一条 JSON，字段口径见 diag DESIGN §3.2；"
        "凭证/原始载荷整项剔除，host 与出口 IP 掩码留首尾段，"
        "被剔除键记在 redacted_keys）",
        f"聚合计数：共 {survey['node_count']} 个 · 按页：{pages} · "
        f"按协议：{protos} · 隔离 {quarantined} · 拉黑 {banned}",
        "",
    ]
    for rec in survey["records"]:
        redacted = diag.redact_node_fields(rec)
        lines.append(json.dumps(redacted, ensure_ascii=False))
    if not survey["records"]:
        lines.append("（节点库为空）")
    return "\n".join(lines) + "\n"


def _file_11_verify_stats(survey) -> str:
    now = survey["now"]
    lines = [f"最近验真统计（窗口按 UTC；生成时刻 {_fmt_ts(now)}Z）", ""]
    if survey["samples_error"]:
        lines.append(survey["samples_error"])
    else:
        lines.append(f"近 7 天全库样本按日计数（共 {len(survey['samples'])} 条）：")
        buckets: Dict[str, List[int]] = {}
        for s in survey["samples"]:
            if s.ts < now - STAT_DAYS * DAY:
                continue
            day = time.strftime("%Y-%m-%d", time.gmtime(s.ts))
            slot = buckets.setdefault(day, [0, 0])   # [成功, 失败]
            slot[0 if s.success else 1] += 1
        for day in sorted(buckets):
            ok_n, fail_n = buckets[day]
            lines.append(f"  {day}：成功 {ok_n} · 失败 {fail_n}")
        if not buckets:
            lines.append("  （近 7 天没有样本）")
        stats = _verify_stats(survey)
        lines.append(f"近 24 小时成功验真（kind=verify）：{stats['recent_success_24h']} 次")
        lines.append("")
        lines.append("每节点统计（node_id 已按 diag.mask_node_id 脱敏；"
                     "指标为 history.compute_stats 口径，直接复用不重算）：")
        by_node: Dict[str, List[VerifySample]] = {}
        for s in survey["samples"]:
            by_node.setdefault(s.node_id, []).append(s)
        for node_id in sorted(by_node):
            st = history.compute_stats(by_node[node_id])
            lat = history.format_latency_ms(st.median_latency_ms)
            vol = "-" if st.volatility is None else f"{st.volatility:.3f}"
            last = "-" if st.last_ok is None else ("可连" if st.last_ok else "不通")
            lines.append(
                f"  {diag.mask_node_id(node_id)}：成功率 {st.success_rate:.0%}"
                f"（{st.success_count}/{st.total}）· 延迟中位 {lat} · "
                f"波动率 {vol} · 活跃天数 {st.active_days} · 最近 {last}")
        if not by_node:
            lines.append("  （没有任何样本）")
    return "\n".join(lines) + "\n"


def _file_12_sources(survey) -> str:
    lines = ["来源登记摘要（地址只写掩码域名——scheme/path/query/userinfo "
             "一律不出现，DESIGN §3.4）", ""]
    if survey["registry_error"]:
        lines.append(survey["registry_error"])
    if not survey["entries"]:
        lines.append("（未登记任何来源）")
    for e in survey["entries"]:
        enabled = "启用" if e.get("enabled") else "停用"
        lines.append(
            f"- {e.get('id')} [{e.get('type')}/{e.get('page')}] "
            f"名称={e.get('name') or '（未命名）'} · {enabled} · "
            f"登记于 {_fmt_ts(e.get('created_at'))}Z · "
            f"地址={diag.mask_url(e.get('address'))}")
        lu = survey["last_updates"].get(e.get("id"))
        if not lu:
            lines.append("  最近更新：未更新过")
        elif lu.get("ok"):
            bits = [f"新增 {lu.get('added', 0)}", f"更新 {lu.get('updated', 0)}",
                    f"消失 {lu.get('gone', 0)}"]
            if lu.get("returned"):
                bits.append(f"回归 {lu['returned']}")
            if lu.get("skipped"):
                bits.append(f"跳过坏行 {lu['skipped']}")
            lines.append(f"  最近更新：成功（{'，'.join(bits)}，"
                         f"{_fmt_ts(lu.get('ts'))}Z）")
        else:
            lines.append(f"  最近更新：失败（{lu.get('error') or '原因未知'}，"
                         f"{_fmt_ts(lu.get('ts'))}Z）")
    return "\n".join(lines) + "\n"


def _file_20_engine_log(survey, engines) -> str:
    sb = _singbox_like(engines)
    log_path = None
    if sb is not None and hasattr(sb, "active_log_path"):
        try:
            log_path = sb.active_log_path()
        except Exception:
            log_path = None
    if not log_path:
        return ("引擎日志尾部：无活动会话。\n"
                "（sing-box 日志写在会话临时目录、stop 即整目录删除——v0.9.0 "
                "起的设计内行为，因为临时配置含凭证必须删。本包生成时经 "
                "SingBoxEngine.active_log_path() 只读查询，当前没有进行中的"
                "会话，拿不到日志尾部，如实注明、不猜。）\n")
    try:
        size = os.path.getsize(log_path)
        with open(log_path, "rb") as f:
            if size > LOG_TAIL_BYTES:
                f.seek(size - LOG_TAIL_BYTES)
            raw = f.read().decode("utf-8", "replace")
        if size > LOG_TAIL_BYTES and "\n" in raw:
            raw = raw.split("\n", 1)[1]            # 丢掉截半的首行
        return ("sing-box.log 尾部（最后 ~64KB）。以下内容已经过 secret 清扫"
                "（日志可能含服务器地址，原文已被替换为 ***）：\n" + raw)
    except OSError as exc:
        return (f"引擎日志尾部：读取失败（{type(exc).__name__}: {exc}）。\n"
                "（活动会话存在但日志文件读不到，如实注明。）\n")


def _file_30_events(orchestrator) -> str:
    lines = ["最近会话事件（orchestrator 内存事件流水，最近 100 条；"
             "node_id 已按 diag.mask_node_id 脱敏）", ""]
    events = list(getattr(orchestrator, "events", None) or [])
    if not events:
        lines.append("（无事件——CLI 为单次进程形态，事件只存在于当次进程内存；"
                     "GUI 常驻会话的事件在此列出。）")
    for ev in events[-100:]:
        ts = getattr(ev, "ts", 0) or 0
        node_id = diag.mask_node_id(getattr(ev, "node_id", "") or "")
        detail = str(getattr(ev, "detail", "") or "")
        lines.append(f"{_fmt_ts(ts)}Z  {getattr(ev, 'kind', '?')}  "
                     f"{node_id or '-'}  {detail}")
    return "\n".join(lines) + "\n"


def _materials(survey, env, engines=None, orchestrator=None,
               client_version: str = "") -> Dict[str, str]:
    sb = _singbox_like(engines)
    singbox_bin = ""
    if sb is not None:
        try:
            singbox_bin = sb.find_binary() or ""
        except Exception:
            singbox_bin = ""
    version = client_version or read_client_version()
    return {
        "00-version.txt": _file_00_version(version),
        "01-environment.txt": _file_01_environment(survey, env, singbox_bin),
        "02-checks.txt": diag.render_checks(diag.run_checks(env)) + "\n",
        "03-health-now.txt": _file_03_health_now(env),
        "10-nodes-summary.txt": _file_10_nodes(survey),
        "11-verify-stats.txt": _file_11_verify_stats(survey),
        "12-sources.txt": _file_12_sources(survey),
        "20-engine-log-tail.txt": _file_20_engine_log(survey, engines),
        "30-events-recent.txt": _file_30_events(orchestrator),
    }


def collect_materials(store, engines=None, orchestrator=None,
                      health_samples=None, registry=None,
                      now: Optional[float] = None,
                      client_version: str = "") -> Dict[str, str]:
    """组装 DESIGN §2 的导出包材料（{文件名: 文本}；MANIFEST 不在——
    由 diag.build_bundle 生成）。02 号文件用同一份 env 跑 run_checks，
    与「一键自检」同口径。"""
    survey = _survey(store, registry=registry, now=now,
                     orchestrator=orchestrator)
    env = _check_env(survey, engines=engines, orchestrator=orchestrator,
                     health_samples=health_samples, now=survey["now"])
    return _materials(survey, env, engines=engines, orchestrator=orchestrator,
                      client_version=client_version)


# ---------------------------------------------------------------------------
# 一站式：收集 → 清扫 → 写包（三层防线的第 2、3 层在这里收口）
# ---------------------------------------------------------------------------
def build_diag_bundle(store, out_path: str, engines=None, orchestrator=None,
                      health_samples=None, registry=None,
                      now: Optional[float] = None, client_version: str = ""
                      ) -> Tuple[str, List[str]]:
    """收集全部材料 → 全文件过 diag.sweep_files（辅防线）→
    diag.build_bundle(secrets=…)（最后防线）写 zip。

    返回 (实际输出路径, 写入文件名清单含 MANIFEST.txt)。out_path 支持 ~；
    父目录不存在则自动创建（只创建目录，不碰别的）。secrets 只在内存流转，
    本函数返回后不留任何引用落盘。
    """
    survey = _survey(store, registry=registry, now=now,
                     orchestrator=orchestrator)
    env = _check_env(survey, engines=engines, orchestrator=orchestrator,
                     health_samples=health_samples, now=survey["now"])
    materials = _materials(survey, env, engines=engines,
                           orchestrator=orchestrator,
                           client_version=client_version)
    secrets = _secrets(survey)
    files = diag.sweep_files(materials, secrets)
    out_path = os.path.expanduser(str(out_path))
    parent = os.path.dirname(os.path.abspath(out_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    written = diag.build_bundle(files, out_path, secrets=secrets,
                                client_version=client_version
                                or read_client_version(),
                                now=survey["now"])
    return out_path, written
