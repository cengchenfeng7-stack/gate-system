#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
诊断导出与一键自检（core/diag.py，task7 · 阶段四·产品化 第一段）落地
====================================================================
纯标准库、可独立 import，不依赖 PySide6、不依赖 client 包（与
core/health.py 同一独立性口径）。设计全文见 zcode-tasks/work7/DESIGN.md，
**字段口径以 DESIGN §3 为准，代码与文档一一对应**：

- mask_host / mask_url            掩码基元（DESIGN §3.1）
- redact_text(text, secrets)      文本清扫：命中的 secret 换成 ***（§3.3）
- redact_node_fields(record)      节点行逐字段脱敏，返回脱敏副本（§3.2）
- run_checks(env)                 一键自检：纯判读，不探测（§4）
- build_bundle(files, out_path)   把已脱敏材料写成 zip，含 MANIFEST（§2）；
                                  secrets 参数是写包前的最后防线（§3.0）

铁则：本模块只产生「给人看的信号与文件」，没有任何连接、切换节点、
改设置的路径；不发任何网络请求（探测结果全部由调用方注入 env）。
"""

from __future__ import annotations

import re
import time
import zipfile
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional

DIAG_VERSION = "0.1.0"          # 诊断工具自身版本（进 MANIFEST / 00 号文件）
MASK = "***"
# 唯一「不脱」例外（DESIGN §3.3）：长度 <4 的 secret 跳过——单字符/两字符
# 的密码几乎无信息量，替换会把整篇文本打成 *** 让包不可用。真凭证
# （uuid/密码/订阅 token）实测长度远超 4。
MIN_SECRET_LEN = 4

_MISSING = object()             # 区分「env 没给」与「env 给了 None」


# ---------------------------------------------------------------------------
# 掩码基元（DESIGN §3.1）
# ---------------------------------------------------------------------------
def mask_host(host, strict: bool = False) -> str:
    """server/host 类掩码：保留首尾各一段，中段换成 ***。

    - 按 `.` 分段，≥2 段：`首段.***.尾段`（vpn123456.opengw.net →
      vpn123456.***.net；203.0.113.45 → 203.***.45）。
    - 单段（无点）：整体 ***（localhost → ***）。
    - 含 `:`（IPv6 / 误传带端口的串）：整体 ***（宁可多脱）。
    - 空串：返回空串。
    - strict=True：整体 ***（DESIGN 标注的升级选项，私有服务器名不留痕）。
    """
    host = str(host or "").strip()
    if not host:
        return ""
    if strict or ":" in host:
        return MASK
    parts = host.split(".")
    if len(parts) == 1:
        return MASK
    return f"{parts[0]}.{MASK}.{parts[-1]}"


def mask_url(url, strict: bool = False) -> str:
    """订阅/feed 地址类掩码：只留 netloc 的域名部分，且再过 mask_host。

    scheme、userinfo（user:pass@）、端口、path、query 一律不出现——
    订阅路径常带一次性 token，是本设计最要防的泄漏点。解析不出
    hostname（裸串/垃圾）→ ***。例：
    https://user:pw@sub.example.com:8443/api/v1/sub?token=abc → sub.***.com
    """
    from urllib.parse import urlsplit
    raw = str(url or "").strip()
    if not raw:
        return MASK
    try:
        hostname = urlsplit(raw).hostname
    except ValueError:
        return MASK
    if not hostname:
        return MASK
    return mask_host(hostname, strict=strict)


# ---------------------------------------------------------------------------
# 文本清扫（DESIGN §3.3）
# ---------------------------------------------------------------------------
def redact_text(text, secrets):
    """把 text 中出现的每条 secret 替换为 ***。

    - secrets 为空/None → 原样返回（任务口径）。
    - 每条 secret 按**长度降序**替换（长 secret 先替换：先短后长会把
      「password123」拆成「***word123」半截泄露）；匹配不区分大小写。
    - 长度 < MIN_SECRET_LEN 的 secret 跳过（DESIGN §3.3 唯一例外）。
    - 非 str 的 secret（数字等）先 str() 再处理；str 形式的单条 secret
      直接按一条处理（不按字符迭代——防御调用方误传）。
    - 非 str 的 text 原样返回（清扫只管文本材料）。
    """
    if not isinstance(text, str):
        return text
    if not secrets:
        return text
    if isinstance(secrets, str):
        secrets = [secrets]
    items = set()
    for secret in secrets:
        if secret is None:
            continue
        secret = str(secret)
        if len(secret) < MIN_SECRET_LEN:
            continue
        items.add(secret)
    out = text
    for secret in sorted(items, key=len, reverse=True):
        out = re.sub(re.escape(secret), MASK, out, flags=re.IGNORECASE)
    return out


def sweep_files(files: Dict[str, str], secrets) -> Dict[str, str]:
    """整个材料集过一遍清扫（组装器在 build_bundle 前调用的辅防线）。"""
    return {name: redact_text(text, secrets) for name, text in files.items()}


# ---------------------------------------------------------------------------
# 节点行逐字段脱敏（DESIGN §3.2，字段口径逐行对应）
# ---------------------------------------------------------------------------
# extra 键分四档：剔除 / 掩码 / 本地状态白名单保留 / 协议参数白名单保留；
# 四档都盖不住的键一律剔除（宁可多脱），并记入 redacted_keys。

# 剔除档：凭证类键（键名小写后含这些子串即命中）+ 固定的 query/clash
_SECRET_EXTRA_MARKS = ("password", "passwd", "uuid", "username", "user",
                       "token", "secret", "auth", "credential", "key")
_FIXED_DROP_EXTRA = ("query", "clash")
# 掩码档：主机类键（mask_host）与 URL 类键（mask_url）
_HOST_EXTRA_KEYS = ("host", "server", "address", "sni", "servername",
                    "endpoint", "peer")
_URL_EXTRA_KEYS = ("subscription_url", "url", "feed_url")
# 本地状态白名单：隔离/登记戳/消失标记/订阅名（10/12 号文件的诊断信息）
_LOCAL_KEEP_EXTRA = ("quarantined", "quarantine_reason", "registry_entry",
                     "source_gone", "source_gone_at", "source_gone_from",
                     "config_format", "subscription_name")
# 协议参数白名单：协议名/传输参数类，无凭证
_PROTO_KEEP_EXTRA = ("method", "flow", "alpn", "fp", "fingerprint",
                     "security", "network", "type", "transport",
                     "header_type", "version")


def mask_node_id(node_id: str) -> str:
    """节点 ID = {source}:{protocol}:{host}:{port}[:sha1前8位]——只掩 host 段。

    digest 判定：末段恰为 8 位十六进制 ⇒ 是 sha1 digest（port 与 digest
    都在尾部时 host 段到 -2 截止；IPv6 分组最长 4 位十六进制，8 位不可能是
    地址段，判定无歧义）。host 段合并后过 mask_host（兼容 IPv6：含 `:`
    → 整体 ***，宁可多脱）；port 与 digest 段保留（端口无 host 不构成
    定位；sha1 前 8 位不可逆）。
    """
    parts = str(node_id or "").split(":")
    if len(parts) < 3:
        return str(node_id or "")
    host_end = -1                                   # 默认末段是端口
    if re.fullmatch(r"[0-9a-f]{8}", parts[-1] or ""):
        host_end = -2                               # 末段是 sha1 digest
    host = ":".join(parts[2:host_end])
    if not host:
        return str(node_id or "")
    masked = mask_host(host)
    return ":".join(parts[:2] + [masked] + parts[host_end:])


def _replace_anywhere(text: str, needle: str, repl: str) -> str:
    """大小写不敏感地把 needle 全部替换为 repl（名字里的出口 IP 用）。"""
    if not needle:
        return text
    return re.sub(re.escape(needle), repl.replace("\\", "\\\\"), text,
                  flags=re.IGNORECASE)


def redact_node_fields(record: dict) -> dict:
    """按 DESIGN §3.2 的字段口径返回节点行的脱敏副本。

    record 是 nodes 表一行 / Node.to_dict() 的 dict 形状。输入不被修改；
    副本新增 `redacted_keys`（被剔除键的路径清单，排序稳定），支持方
    看得见「这里有东西、被脱了」。
    """
    if not isinstance(record, dict):
        raise TypeError(f"record 必须是 dict，得到 {type(record).__name__}")
    out: Dict = {}
    dropped: List[str] = []

    # 顶层固定字段
    for key, value in record.items():
        if key == "redacted_keys":
            continue
        if key == "id":
            out["id"] = mask_node_id(value or "")
        elif key == "name":
            ip = (record.get("exit_profile") or {}).get("ip") \
                if isinstance(record.get("exit_profile"), dict) else None
            out["name"] = _replace_anywhere(str(value or ""), str(ip or ""),
                                            mask_host(ip)) if ip else str(value or "")
        elif key == "host":
            out["host"] = mask_host(value)
        elif key == "config_ref":
            dropped.append("config_ref")            # 原始载荷整项剔除
        elif key == "exit_profile" and isinstance(value, dict):
            profile = dict(value)
            if "ip" in profile:
                profile["ip"] = mask_host(profile["ip"])
            out["exit_profile"] = profile
        elif key == "extra" and isinstance(value, dict):
            out["extra"], extra_dropped = _redact_extra(value)
            dropped.extend(f"extra.{k}" for k in extra_dropped)
        else:
            out[key] = value                        # protocol/source/port/… 原样

    out["redacted_keys"] = sorted(dropped)
    return out


def _redact_extra(extra: dict):
    """extra 逐键四档处理，返回 (脱敏 dict, 被剔除键名清单)。"""
    out: Dict = {}
    dropped: List[str] = []
    for key, value in extra.items():
        low = str(key).lower()
        if low in _FIXED_DROP_EXTRA or any(m in low for m in _SECRET_EXTRA_MARKS):
            dropped.append(key)                     # 凭证/原始载荷：整项剔除
        elif low in _URL_EXTRA_KEYS:
            out[key] = mask_url(value)              # 订阅地址：只留掩码域名
        elif low in _HOST_EXTRA_KEYS:
            out[key] = mask_host(value)             # 服务器特征：掩码
        elif low in _LOCAL_KEEP_EXTRA or low in _PROTO_KEEP_EXTRA:
            out[key] = value                        # 白名单保留
        else:
            dropped.append(key)                     # 白名单外默认剔（宁可多脱）
    return out, dropped


# ---------------------------------------------------------------------------
# 一键自检（DESIGN §4：纯判读，探测结果由调用方注入 env）
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    name: str      # 稳定英文短名（DESIGN §4.1 表），测试与集成引用它
    ok: bool
    detail: str    # 人话（失败时含建议动作）

    def to_dict(self) -> dict:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


def _getter(env):
    """env 是 dict 或带同名属性的对象——统一成 (key, default) -> value。"""
    if hasattr(env, "get"):
        return lambda key, default=None: env.get(key, default)
    return lambda key, default=None: getattr(env, key, default)


def _skip(name: str, what: str, inject_hint: str) -> CheckResult:
    """env 缺键 = 判不了 ≠ 失败（DESIGN §4.1：与 health「宁可判不了，
    不硬判误报」同口径）。"""
    return CheckResult(name, True, f"没拿到 {what}，跳过判读（注入 {inject_hint}）")


def _describe_occupied(occupied) -> str:
    """占用清单 → 人话（与 core/health.describe_occupied 同文案口径，
    本模块不能 import client，故按同一规则实现）。"""
    parts = []
    for item in occupied:
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        proc, pid = item.get("process"), item.get("pid")
        if proc:
            who = f"「{proc}（pid {pid}）」"
        elif pid:
            who = f"「pid {pid}」"
        else:
            who = ""
        parts.append(f"端口 {item.get('port')}"
                     + (f"（被 {who} 占用）" if who else "（被占用，进程名查不到）"))
    return "；".join(parts)


def _check_database(g) -> CheckResult:
    db_ok = g("db_ok", _MISSING)
    if db_ok is _MISSING:
        return _skip("database", "节点库探测结果", "db_ok")
    if db_ok:
        node_count, sample_count = g("db_node_count"), g("db_sample_count")
        bits = []
        if node_count is not None:
            bits.append(f"{node_count} 个节点")
        if sample_count is not None:
            bits.append(f"{sample_count} 条验真样本")
        detail = "节点库可读" + (f"（{'、'.join(bits)}）" if bits else "")
        return CheckResult("database", True, detail)
    error = g("db_error") or "原因未知"
    return CheckResult(
        "database", False,
        f"节点库打不开（{error}）——所有节点数据读不出来；"
        "检查磁盘与权限，或用 --db 指定别的库。")


def _check_singbox(g) -> CheckResult:
    found = g("singbox_found", _MISSING)
    if found is _MISSING:
        return _skip("singbox_binary", "sing-box 二进制探测结果", "singbox_found")
    if found:
        version = str(g("singbox_version") or "").strip()
        return CheckResult("singbox_binary", True,
                           "sing-box 已找到" + (f"（{version}）" if version else "")
                           + "——vless/ss/trojan 等 7 种协议可用")
    return CheckResult(
        "singbox_binary", False,
        "找不到 sing-box——vless/ss/trojan 等 7 种协议都连不了。"
        "安装 sing-box 到 ~/.local/bin，或用环境变量 GATE_CLIENT_SINGBOX_BIN "
        "指定路径。")


def _check_openvpn(g) -> CheckResult:
    needed = bool(g("openvpn_needed", False))
    found = g("openvpn_found", _MISSING)
    if not needed:
        return CheckResult("openvpn_binary", True,
                           "OpenVPN 页没有节点，此项不要求 openvpn")
    if found is _MISSING:
        return _skip("openvpn_binary", "openvpn 二进制探测结果", "openvpn_found")
    if found:
        return CheckResult("openvpn_binary", True, "openvpn 已找到——OpenVPN 页可用")
    return CheckResult(
        "openvpn_binary", False,
        "找不到 openvpn——OpenVPN 页的节点连不了（其他页不受影响）。"
        "安装 OpenVPN Community，或用环境变量 OPENVPN_BIN 指定路径。")


def _check_ports(g) -> CheckResult:
    occupied = g("ports_occupied", _MISSING)
    if occupied is _MISSING:
        return _skip("engine_ports", "端口占用探测结果",
                     "ports_occupied（来自 core/health.check_ports_free）")
    if not occupied:
        return CheckResult("engine_ports", True,
                           "引擎端口 7897（HTTP）/ 7898（SOCKS）空闲，引擎能起")
    return CheckResult(
        "engine_ports", False,
        f"引擎端口被占用，引擎起不来：{_describe_occupied(occupied)}"
        "——关掉占用它的程序（其他代理软件？）再重试；"
        "客户端不会动你的系统设置。")


def _check_sysproxy(g) -> CheckResult:
    conflict = g("proxy_conflict", _MISSING)
    if conflict is _MISSING:
        return _skip("system_proxy", "系统代理判读结果",
                     "proxy_conflict（来自 core/health.proxy_conflict）")
    if conflict.get("conflict"):
        return CheckResult(
            "system_proxy", False,
            f"{conflict.get('reason') or '系统代理指向别家'}"
            "——客户端不会改你的系统代理，也不会切换节点，是否处置由你决定。")
    return CheckResult("system_proxy", True,
                       conflict.get("reason") or "系统代理无冲突")


def _check_connection(g) -> CheckResult:
    conn = g("connection", _MISSING)
    if conn is _MISSING:
        return _skip("connection_alive", "连接健康探测结果",
                     "connection（来自 core/health.connection_health_detail）")
    if conn is None:
        return CheckResult("connection_alive", True, "当前没有活动连接")
    status = conn.get("status")
    streak = conn.get("fail_streak") or 0
    if status == "ok":
        return CheckResult("connection_alive", True, "连接健康：出口探测正常")
    if status == "unknown":
        return CheckResult("connection_alive", True, "连接刚建立，还没有探测样本")
    if status == "suspect":
        return CheckResult(
            "connection_alive", False,
            f"连接可疑：出口探测已连续失败 {streak} 次——面板还挂着，"
            "但可能已经不通；先观察，处置由你决定。")
    if status == "dead":
        return CheckResult(
            "connection_alive", False,
            f"连接基本已死：出口探测已连续失败 {streak} 次——先断开这个连接，"
            "再由你手动选下一个节点（客户端永不自动切换）。")
    return CheckResult("connection_alive", True,
                       "连接状态判读不了（status 缺失），跳过")


def _check_tun(g) -> CheckResult:
    mode = str(g("takeover_mode", "proxy") or "proxy").strip().lower()
    if mode != "tun":
        return CheckResult("tun_takeover", True,
                           f"非 TUN 模式（当前接管方式：{mode}），此项跳过")
    takeover = g("tun_takeover", _MISSING)
    if takeover is _MISSING:
        return _skip("tun_takeover", "TUN 接管生效探测结果",
                     "tun_takeover（来自 core/health.check_tun_takeover）")
    if takeover.get("effective"):
        return CheckResult("tun_takeover", True,
                           takeover.get("reason") or "TUN 接管生效：默认路由已走 gate-tun")
    return CheckResult(
        "tun_takeover", False,
        f"{takeover.get('reason') or 'TUN 网卡已建但默认路由未接管'}"
        "——只提醒不修路由：重新开一次 TUN 连接试试，或检查提权授权（pkexec）。")


def _check_verify_coverage(g) -> CheckResult:
    stats = g("verify_stats", _MISSING)
    if stats is _MISSING:
        return _skip("verify_coverage", "验真统计", "verify_stats")
    total = stats.get("total_nodes") or 0
    verified = stats.get("verified_nodes") or 0
    if total == 0:
        return CheckResult("verify_coverage", True, "节点库为空：先导入节点，再跑验真")
    if verified == 0:
        return CheckResult(
            "verify_coverage", False,
            f"库里 {total} 个节点还没有一个验过出口——先「测本页」跑一轮验真，"
            "才能知道哪些能用。")
    detail = f"{verified}/{total} 个节点验过出口"
    recent = stats.get("recent_success_24h")
    if recent is not None:
        detail += f"，近 24 小时成功验真 {recent} 次"
    return CheckResult("verify_coverage", True, detail)


def _check_sources(g) -> CheckResult:
    failures = g("registry_failures", _MISSING)
    if failures is _MISSING:
        return _skip("source_updates", "来源登记更新结果", "registry_failures")
    if not failures:
        count = g("registry_count")
        detail = "登记来源最近更新全部成功"
        if count is not None:
            detail += f"（共 {count} 条）"
        return CheckResult("source_updates", True, detail)
    first = str(failures[0])
    truncated = len(first) > 120
    first = first[:120] + ("…" if truncated else "")
    return CheckResult(
        "source_updates", False,
        f"有 {len(failures)} 条登记来源最近更新失败：{first}"
        "——到来源页「更新已登记来源」手动重试，失败原因详见 12-sources.txt。")


# 检查项注册表：顺序即 DESIGN §4.1 的 1-9
_CHECKS = (_check_database, _check_singbox, _check_openvpn, _check_ports,
           _check_sysproxy, _check_connection, _check_tun,
           _check_verify_coverage, _check_sources)

CHECK_NAMES = tuple(fn.__name__[7:] for fn in _CHECKS)   # 稳定短名清单


def run_checks(env) -> List[CheckResult]:
    """一键自检：env 为注入了探测结果的 dict/对象（DESIGN §4.1 逐项列出键），
    返回 9 项 CheckResult(name/ok/detail)。纯函数，不探测、不联网、
    不重复造检测——探测一律复用 core/health 三件套的结论。"""
    g = _getter(env)
    return [check(g) for check in _CHECKS]


def render_checks(results: List[CheckResult]) -> str:
    """CheckResult 列表 → 02-checks.txt / CLI doctor / GUI 结果框的文本。"""
    lines = [f"一键自检报告（diag {DIAG_VERSION}）", ""]
    for r in results:
        mark = "ok" if r.ok else "fail"
        lines.append(f"[{mark}] {r.name}：{r.detail}")
    fails = sum(1 for r in results if not r.ok)
    lines.append("")
    if fails == 0:
        lines.append(f"总结：{len(results)} 项检查全部通过")
    else:
        lines.append(f"总结：{fails}/{len(results)} 项未通过（详见上方 [fail] 行）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 导出包（DESIGN §2）：把已脱敏的文本材料写成 zip
# ---------------------------------------------------------------------------
# 已知文件的用途一句话（进 MANIFEST，支持方按号找内容）
_KNOWN_PURPOSES = {
    "00-version.txt": "客户端版本与运行平台",
    "01-environment.txt": "环境摘要（库/二进制/系统代理/接管方式）",
    "02-checks.txt": "一键自检报告",
    "03-health-now.txt": "连接健康当下结论",
    "10-nodes-summary.txt": "脱敏节点清单与聚合计数",
    "11-verify-stats.txt": "最近验真统计",
    "12-sources.txt": "来源登记摘要（地址只留掩码域名）",
    "20-engine-log-tail.txt": "引擎日志尾部（仅会话进行中）",
    "30-events-recent.txt": "最近会话事件",
}


def _validate_name(name: str) -> None:
    """zip 内文件名只许平铺的普通文件名（防路径逃逸，集成期误用当场爆）。"""
    if not name or "/" in name or "\\" in name or name in (".", "..") \
            or name.startswith("."):
        raise ValueError(f"非法的包内文件名: {name!r}（只许平铺文件名）")


def _manifest(files: Dict[str, str], client_version: str, now) -> str:
    lines = [
        "gate-client 诊断导出包（已脱敏）",
        f"生成时间（UTC）: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(now))}",
        f"客户端版本: {client_version or '未提供（组装器应传入 client/VERSION 内容）'}",
        f"诊断工具版本: diag {DIAG_VERSION}",
        "脱敏说明: 本包已经过三层脱敏（结构性剔除 / secret 文本清扫 / 写包前兜底）；"
        "host 类字段保留首尾段，订阅地址只留掩码域名，凭证类字段整项剔除。"
        "字段口径详见 diag 包 DESIGN.md §3。",
        "",
        "文件清单（字节数 / 行数 / 用途）:",
    ]
    for name in ["MANIFEST.txt"] + sorted(n for n in files if n != "MANIFEST.txt"):
        if name == "MANIFEST.txt":
            lines.append(f"{name}\t-\t-\t本清单")
            continue
        text = files[name]
        purpose = _KNOWN_PURPOSES.get(name, "-")
        lines.append(f"{name}\t{len(text.encode('utf-8'))}\t"
                     f"{len(text.splitlines())}\t{purpose}")
    return "\n".join(lines) + "\n"


def build_bundle(files: Dict[str, str], out_path: str,
                 secrets: Optional[Iterable[str]] = None,
                 client_version: str = "", now: Optional[float] = None) -> List[str]:
    """把已脱敏的文本材料写成 zip（平铺、UTF-8、DEFLATED）。

    - files: {文件名: 文本内容}；文件名必须是平铺普通文件名（_validate_name）。
    - secrets: 最后防线（DESIGN §3.0 第三层）——给了就把每个文件再清扫
      一遍再写入；上游漏脱的原文出不了包。None 则按调用方已脱敏处理
      （任务签名 build_bundle(files, out_path) 行为不变）。
    - client_version: 进 MANIFEST 头部（组装器传 client/VERSION 内容）。
    - now: 生成时间戳注入点（测试定表；缺省取当前 UTC）。
    返回写入的文件名清单（含 MANIFEST.txt）。
    """
    material = dict(files)
    if secrets:
        material = sweep_files(material, secrets)
    if now is None:
        now = time.time()
    if "MANIFEST.txt" in material:
        raise ValueError("MANIFEST.txt 由 build_bundle 生成，不要传入")
    manifest = _manifest(material, client_version, now)
    material["MANIFEST.txt"] = manifest
    if secrets:                                  # 清单也过一道兜底
        material["MANIFEST.txt"] = redact_text(manifest, secrets)
    written = []
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(material):
            _validate_name(name)
            zf.writestr(name, material[name].encode("utf-8"))
            written.append(name)
    return written
