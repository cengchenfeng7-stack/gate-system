#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验出口（core/verify.py）
=========================
把 tools/verify_node.py 的 probe()/verdict_muse 与 vpngate.py 的
classify_network()/evaluate_stability() 产品化成本地测量版本：

- 纯函数层（可离线单测）：网络分类、muse 判定、场景合格判定
- 网络层：probe 是可注入 callable；real_probe() 是真实实现（urllib，
  逻辑移植自 verify_node.probe，改为返回结构化结果而不是打印）。
  单元测试一律注入假 probe，绝不发真实请求。

场景阈值来源：vpngate.py 文件顶部的常量定义（env 可覆盖，此处取默认值）。
注意：vpngate.evaluate_stability 的 docstring 里写的还是旧阈值
（在线≥12h/抖动≤150/延迟≤600），与常量不一致——以常量为准：
SSTP 延迟天生 2-9 秒，常量是按协议现实放宽后的版本（见 vpngate 注释）。
"""

from __future__ import annotations

import json
import re
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable, Optional

from .model import ExitProfile, Node

# ---------------------------------------------------------------------------
# 场景阈值（复制自 vpngate.py 常量区，注明出处；改阈值须两边同步评估）
# ---------------------------------------------------------------------------
SUPPORT_MIN_UPTIME_H = 6.0      # vpngate.SUPPORT_MIN_UPTIME_H
SUPPORT_MAX_SESSIONS = 150      # vpngate.SUPPORT_MAX_SESSIONS
SUPPORT_MAX_JITTER = 800.0      # vpngate.SUPPORT_MAX_JITTER
SUPPORT_MAX_LATENCY = 3500.0    # vpngate.SUPPORT_MAX_LATENCY

GAME_MAX_LATENCY = 4000.0       # vpngate.GAME_MAX_LATENCY
GAME_MAX_JITTER = 1000.0        # vpngate.GAME_MAX_JITTER
GAME_MAX_SESSIONS = 300         # vpngate.GAME_MAX_SESSIONS

SPEED_MIN_MBPS = 2.0            # vpngate.SPEED_MIN_MBPS
SPEED_MAX_SESSIONS = 300        # vpngate.SPEED_MAX_SESSIONS
SPEED_MAX_LATENCY = 5000.0      # vpngate.SPEED_MAX_LATENCY

AI_MIN_UPTIME_H = 3.0           # vpngate.AI_MIN_UPTIME_H
AI_MAX_JITTER = 800.0           # vpngate.AI_MAX_JITTER
AI_MAX_LATENCY = 3500.0         # vpngate.AI_MAX_LATENCY

STABILITY_THRESHOLD = 0.30      # vpngate.STABILITY_THRESHOLD（波动率 <30% 为稳定）

# ---------------------------------------------------------------------------
# 网络分类（逻辑复制自 vpngate.classify_network，关键词表同步复制）
# ---------------------------------------------------------------------------
DATA_CENTER_ORG_KEYWORDS = [
    "GOOGLE", "AMAZON", "AWS", "MICROSOFT", "AZURE", "OVH", "HETZNER", "DIGITALOCEAN",
    "AKAMAI", "CLOUDFLARE", "FASTLY", "RACKSPACE", "EQUINIX", "LINODE", "VULTR",
    "HURRICANE", "TENCENT", "ALIBABA", "ALIYUN", "LEASEWEB", "LEASWEB", "CHOOPA",
    "CONTABO", "M247", "DATACAMP", "COGENT", "ORACLE",
]
RESIDENTIAL_ORG_KEYWORDS = [
    "NTT EAST", "NTT WEST", "NTT COMMUNICATIONS", "NTT BROADBAND", "KDDI", "DOCOMO",
    "SOFTBANK", "AU COMMUNICATIONS", "J:COM", "JCOM", "OCN", "BIGLOBE",
    "IIJ", "SEIKO", "CLEVER-NET", "AT&T", "COMCAST", "XFINITY", "VERIZON",
    "CHARTER", "SPECTRUM", "COX", "CENTURYLINK", "FRONTIER",
    "TELUS", "ROGERS", "BELL CANADA", "VODAFONE", "ORANGE", "DEUTSCHE TELEKOM",
    "BREEZE", "TIM S.P.A", "LIBERO", "FASTWEB", "FREE FRANCE", "BT OPEN",
    "KT", "SK BROADBAND", "LG UPLUS", "CHUNGHWA", "HINET", "SINGTEL", "STARHUB",
]


def classify_network(host, exit_org, is_datacenter=None) -> str:
    """返回 residential / datacenter / unknown。逻辑与 vpngate.classify_network 一致。"""
    if is_datacenter is True:
        return "datacenter"
    if is_datacenter is False:
        return "residential"
    org = (exit_org or "").upper()
    if org:
        if any(k in org for k in DATA_CENTER_ORG_KEYWORDS):
            return "datacenter"
        if any(k in org for k in RESIDENTIAL_ORG_KEYWORDS):
            return "residential"
    h = (host or "").lower()
    if h.startswith("public-vpn"):
        return "datacenter"
    if re.match(r"^vpn\d{5,}", h) or re.match(r"^vpnv\d+", h):
        return "residential"
    return "unknown"


# ---------------------------------------------------------------------------
# muse 判定（移植自 verify_node.verdict_muse）
# ---------------------------------------------------------------------------
def verdict_muse(code) -> tuple:
    """返回 (ok, 标签, 说明)。307/200=出口被接受；400=被拒；403=被拦；0=链路死。"""
    return {
        307: (True, "可注册", "Meta 收了这个出口"),
        200: (True, "可注册", "站点正常响应"),
        400: (False, "被拒", "出口 IP 被风控（Cloudflare 段最常见）"),
        403: (False, "被拦", "Cloudflare Bot 防护拦截"),
        0:   (False, "链路死", "节点不通（面板延迟数字不可信）"),
    }.get(code, (False, f"未知响应 {code}", "未识别的 HTTP 状态"))


# verify_node.DATACENTER_HINTS：出口 org/asn 命中即疑似机房段
DATACENTER_HINTS = ("cloudflare", "amazon", "google", "microsoft", "digitalocean",
                    "hetzner", "ovh", "contabo", "vultr", "linode", "azure",
                    "alibaba", "tencent", "choopa", "psychz", "racknerd")


def org_looks_datacenter(*texts) -> bool:
    blob = " ".join(t for t in texts if t).lower()
    return any(h in blob for h in DATACENTER_HINTS)


# ---------------------------------------------------------------------------
# 场景合格判定（移植自 vpngate.evaluate_stability 的四场景部分）
# ---------------------------------------------------------------------------
@dataclass
class SceneInput:
    success: bool = False
    is_clean: Optional[bool] = None        # None=未知
    residential: str = "unknown"
    uptime_hours: Optional[float] = None   # None=未知
    sessions: int = 0
    jitter_ms: Optional[float] = None      # None=未复测（未知）
    latency_ms: Optional[float] = None
    speed_mbps: float = 0.0
    stable: bool = False                   # 波动率达标（history 计算）


def derive_is_clean(exit_info: Optional[dict]) -> Optional[bool]:
    """与 vpngate 一致：无出口情报=None；有 is_clean 用之；否则由 abuser/tor/bogon 推导。"""
    if not exit_info:
        return None
    if exit_info.get("is_clean") is not None:
        return bool(exit_info["is_clean"])
    return not bool(exit_info.get("is_abuser") or exit_info.get("is_tor")
                    or exit_info.get("is_bogon"))


def evaluate_scenes(inp: SceneInput) -> set:
    """返回合格场景集合（客户端命名 support/ai/games/download）。"""
    scenes = set()
    if not inp.success:
        return scenes
    uptime_known = inp.uptime_hours is not None and inp.uptime_hours > 0
    jitter_known = inp.jitter_ms is not None
    lat_ok = inp.latency_ms is not None
    is_res = inp.residential == "residential"

    # ⭐客服稳连：干净 + 住宅 + 长在线 + 低负载 + 低抖动 + 低延迟
    if (inp.is_clean is True and is_res and uptime_known
            and inp.uptime_hours >= SUPPORT_MIN_UPTIME_H
            and inp.sessions <= SUPPORT_MAX_SESSIONS
            and jitter_known and inp.jitter_ms <= SUPPORT_MAX_JITTER
            and lat_ok and inp.latency_ms <= SUPPORT_MAX_LATENCY):
        scenes.add("support")
    # 🎮游戏：响应优先（允许短开机；脏 IP 已知为脏则排除，未知放行——同 vpngate）
    if (lat_ok and inp.latency_ms <= GAME_MAX_LATENCY
            and jitter_known and inp.jitter_ms <= GAME_MAX_JITTER
            and inp.sessions <= GAME_MAX_SESSIONS
            and (not uptime_known or inp.uptime_hours >= 1.0)
            and inp.is_clean is not False):
        scenes.add("games")
    # ⬇️下载：带宽达标 + 不拥堵 + 延迟放宽 + 必须稳定
    if (inp.speed_mbps >= SPEED_MIN_MBPS
            and inp.sessions <= SPEED_MAX_SESSIONS
            and lat_ok and inp.latency_ms <= SPEED_MAX_LATENCY
            and inp.stable):
        scenes.add("download")
    # 🤖国外AI：干净 + 在线时长 + 低抖动 + 低延迟（不要求住宅，同 vpngate）
    if (inp.is_clean is True and uptime_known
            and inp.uptime_hours >= AI_MIN_UPTIME_H
            and jitter_known and inp.jitter_ms <= AI_MAX_JITTER
            and lat_ok and inp.latency_ms <= AI_MAX_LATENCY):
        scenes.add("ai")
    return scenes


# ---------------------------------------------------------------------------
# Probe 接口与验证编排（网络层可注入）
# ---------------------------------------------------------------------------
@dataclass
class ProbeResult:
    """一次本地探针的结果。probe callable 返回它；假 probe 亦然。"""
    alive: bool = False
    exit_ip: Optional[str] = None
    country: Optional[str] = None
    country_code: Optional[str] = None
    org: Optional[str] = None
    asn: Optional[str] = None
    is_datacenter: Optional[bool] = None
    is_abuser: Optional[bool] = None
    is_tor: Optional[bool] = None
    is_bogon: Optional[bool] = None
    is_clean: Optional[bool] = None
    latency_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    muse_code: Optional[int] = None        # None=未测 muse
    error: str = ""


# probe 签名：probe(node) -> ProbeResult（在本机当前网络/已连隧道下测量）
ProbeFn = Callable[[Node], ProbeResult]


@dataclass
class VerifyOutcome:
    ok: bool                               # 隧道活着且拿到了出口
    reason: str = ""
    exit_profile: Optional[ExitProfile] = None
    scenes: set = field(default_factory=set)
    muse_ok: Optional[bool] = None


def verify_exit(node: Node, probe: ProbeFn, stable: bool = False,
                now: Optional[float] = None) -> VerifyOutcome:
    """
    对节点跑一次验真（probe 注入）。纯编排：不直接发网络请求。
    - 链路死 → ok=False（防假连的核心判定）
    - muse 被拒/被拦 → 出口 dirty 加 "muse"，且 is_clean 视为不干净（对 AI 场景）
    """
    r = probe(node)
    if not r.alive or not r.exit_ip:
        return VerifyOutcome(ok=False, reason=r.error or "链路不通或取不到出口 IP")
    network_type = classify_network(node.host, r.org, r.is_datacenter)
    exit_info = {"is_clean": r.is_clean, "is_abuser": r.is_abuser,
                 "is_tor": r.is_tor, "is_bogon": r.is_bogon}
    is_clean = derive_is_clean(exit_info)

    dirty = set()
    muse_ok = None
    if r.muse_code is not None:
        muse_ok, _label, _advice = verdict_muse(r.muse_code)
        if not muse_ok and r.muse_code in (400, 403):
            dirty.add("muse")
    if is_clean is False:
        dirty.add("dirty_ip")

    profile = ExitProfile(
        ip=r.exit_ip, country=r.country, country_code=r.country_code,
        network_type=network_type, org=r.org, asn=r.asn,
        is_clean=is_clean, dirty=dirty, verified_at=now or time.time())
    scenes = evaluate_scenes(SceneInput(
        success=True, is_clean=is_clean, residential=network_type,
        uptime_hours=node.metrics.uptime_hours, sessions=node.metrics.sessions,
        jitter_ms=r.jitter_ms if r.jitter_ms is not None else node.metrics.jitter_ms,
        latency_ms=r.latency_ms if r.latency_ms is not None else node.metrics.latency_ms,
        speed_mbps=node.metrics.speed_mbps, stable=stable))
    return VerifyOutcome(ok=True, exit_profile=profile, scenes=scenes, muse_ok=muse_ok)


def apply_outcome(node: Node, outcome: VerifyOutcome) -> Node:
    """把验证结果写回节点（出口画像 + 指标 + 场景）。失败时不覆盖旧画像。
    成功时顺带两件 M2.5 的事（用户 2026-11-20 亲定）：
    ① 按实测出口改名（原名保全进 extra["orig_name"]，只存第一次）；
    ② 污染节点打隔离标记 / 验干净自动解除（见 _apply_quarantine）。"""
    if outcome.ok and outcome.exit_profile is not None:
        node.exit_profile = outcome.exit_profile
        node.scenes = set(outcome.scenes)
        _apply_exit_naming(node, outcome.exit_profile)
        _apply_quarantine(node, outcome.exit_profile)
    return node


# ---------------------------------------------------------------------------
# M2.5：按实测出口改名 + 污染隔离（判定只认实测画像，绝不采信节点自报名字）
# ---------------------------------------------------------------------------
COUNTRY_NAMES_ZH = {
    "JP": "日本", "KR": "韩国", "US": "美国", "NG": "尼日利亚", "HK": "香港",
    "TW": "台湾", "MO": "澳门", "SG": "新加坡", "GB": "英国", "DE": "德国",
    "FR": "法国", "NL": "荷兰", "CA": "加拿大", "AU": "澳大利亚", "IN": "印度",
    "RU": "俄罗斯", "BR": "巴西", "ZA": "南非", "TR": "土耳其", "AE": "阿联酋",
    "TH": "泰国", "VN": "越南", "MY": "马来西亚", "ID": "印尼", "PH": "菲律宾",
    "IT": "意大利", "ES": "西班牙", "SE": "瑞典", "CH": "瑞士", "PL": "波兰",
    "UA": "乌克兰", "IL": "以色列", "EG": "埃及", "KE": "肯尼亚", "AR": "阿根廷",
    "MX": "墨西哥", "CN": "中国", "NZ": "新西兰", "IE": "爱尔兰", "AT": "奥地利",
    "BE": "比利时", "DK": "丹麦", "FI": "芬兰", "NO": "挪威", "PT": "葡萄牙",
    "GR": "希腊", "CZ": "捷克", "RO": "罗马尼亚", "HU": "匈牙利", "CL": "智利",
    "CO": "哥伦比亚", "PE": "秘鲁", "SA": "沙特阿拉伯", "QA": "卡塔尔",
    "KW": "科威特", "PK": "巴基斯坦", "BD": "孟加拉国", "LK": "斯里兰卡",
    "MM": "缅甸", "KH": "柬埔寨", "LA": "老挝",
}


def exit_display_name(profile: ExitProfile) -> str:
    """实测名格式（用户 2026-11-20 拍板）：「国家中文 · 出口IP · 运营商」；
    org 缺失省略第三段；国家优先 country_code 映射中文，其次画像自带
    country 名，再次代码本身——全是实测字段，不猜不编。"""
    country = (COUNTRY_NAMES_ZH.get(profile.country_code or "")
               or profile.country or profile.country_code or "未知")
    parts = [country, profile.ip or "未知IP"]
    if profile.org:
        parts.append(profile.org)
    return " · ".join(str(p) for p in parts)


def pollution_of(profile: ExitProfile) -> tuple:
    """污染判定：is_clean is False 或 dirty 非空。返回 (是否污染, 原因串)。"""
    reasons = sorted(profile.dirty or set())
    if profile.is_clean is False and "dirty_ip" not in reasons:
        reasons.append("dirty_ip")
    return (bool(reasons), ",".join(reasons))


def _apply_exit_naming(node: Node, profile: ExitProfile) -> None:
    if not profile.ip:
        return
    if "orig_name" not in node.extra:
        node.extra["orig_name"] = node.name
    node.name = exit_display_name(profile)


def _apply_quarantine(node: Node, profile: ExitProfile) -> None:
    polluted, reason = pollution_of(profile)
    if polluted:
        node.extra["quarantined"] = "1"
        node.extra["quarantine_reason"] = reason
    else:
        node.extra.pop("quarantined", None)
        node.extra.pop("quarantine_reason", None)


# ---------------------------------------------------------------------------
# 真实 probe（移植自 verify_node.probe；仅 CLI 真实模式使用，测试不调用）
# ---------------------------------------------------------------------------
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
_LIVENESS = [
    ("https://api.ipify.org", "ip"),
    ("https://icanhazip.com", "ip"),
    ("https://www.google.com/generate_204", "raw"),
]
_IP_API = ("http://ip-api.com/json/{ip}?lang=en&fields=status,message,"
           "country,countryCode,city,isp,org,as,query,hosting")
_MUSE = "https://muse.ai/"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def _opener(proxy):
    ctx = ssl.create_default_context()
    handlers = [urllib.request.HTTPSHandler(context=ctx),
                urllib.request.ProxyHandler({"http": proxy, "https": proxy} if proxy else {}),
                _NoRedirect]
    return urllib.request.build_opener(*handlers)


def _fetch(op, url, timeout=25):
    try:
        with op.open(urllib.request.Request(url, headers={"User-Agent": _UA}),
                     timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def make_real_probe(proxy: Optional[str] = None, test_muse: bool = True) -> ProbeFn:
    """返回真实 probe：经 proxy（None=当前系统网络，OpenVPN/TUN 模式下即隧道出口）测真实出口。"""
    def _probe(node: Node) -> ProbeResult:
        op = _opener(proxy)
        ip, alive = None, False
        t0 = time.monotonic()
        for url, kind in _LIVENESS:
            code, body = _fetch(op, url, timeout=20)
            if code in (200, 204):
                alive = True
                if kind == "ip" and body.strip():
                    cand = body.strip().splitlines()[0]
                    if cand.count(".") == 3 and all(p.isdigit() for p in cand.split(".")):
                        ip = cand
                break
        latency = round((time.monotonic() - t0) * 1000, 1)
        if not alive:
            return ProbeResult(alive=False, error="存活探针全灭：链路不通")
        if not ip:
            return ProbeResult(alive=False, error="取不到出口 IP")
        info = {}
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(_IP_API.format(ip=ip),
                                           headers={"User-Agent": _UA}), timeout=15) as resp:
                info = json.loads(resp.read().decode())
        except Exception:
            pass
        muse_code = None
        if test_muse:
            muse_code, _ = _fetch(op, _MUSE, timeout=25)
        return ProbeResult(
            alive=True, exit_ip=ip,
            country=info.get("country"), country_code=(info.get("countryCode") or None),
            org=info.get("org") or info.get("isp"),
            asn=info.get("as"),
            is_datacenter=(bool(info["hosting"]) if "hosting" in info else None),
            latency_ms=latency, muse_code=muse_code)
    return _probe
