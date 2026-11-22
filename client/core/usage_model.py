#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用界面规则配置模型原型（usage_model.py，task10 · 使用界面线 第一段）
========================================================================
task10/DESIGN.md §4 的定稿口径落成代码。只用标准库；不依赖 PySide6、
不 import client 包；不读文件、不碰网络——纯数据结构 + 校验 + 翻译。

职责（DESIGN §4）：
- UsageSettings：使用界面「规则与网络区」的设置模型——分流模式、
  广告拦截、恶意拦截、IPv6、DNS 服务器选择。白/黑名单归后台，
  **不进本模型**（DESIGN §0 红线 3 / §6）；网站黑名单只作为
  build_route_config 的外部入参传入。
- build_route_config(model, site_blocklist=())：把模型连同传入的
  网站黑名单翻成 sing-box 路由/DNS 配置**片段**（假数据可跑）。
  翻不出来的功能在返回的 uncovered 里显式标注，绝不假装生成
  （DESIGN §4.5 合并顺序契约 + 返回值里 insert_before 提示）。

铁律对齐：本模块没有任何节点选择/切换/优选逻辑——它只翻译用户
已经显式定好的设置；容灾/自动替换的口径（红色才换、只换组内、
默认关）在 DESIGN §5，本模块连它的配置字段都不持有。

字段口径的既有证据（client-dev 0.13.0，改动前先对照）：
- 出站 tag=node-out、route.final 指它、dns remote-dns detour=node-out：
  engines/singbox_engine.py build_config（222-245 行）；
- 规则动作形态 {"action": ...}：core/tun_config.py 277-278 行同族；
- resolve 规则 strategy=ipv4_only：singbox_engine.py 239 行（IPv6 关=现状）。
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 常量（与 DESIGN §4/§5/§8 一一对应；集成时不得改名——测试锁口径）
# ---------------------------------------------------------------------------

# 分流模式（DESIGN §4.1 三选一，默认 global = 现行行为）
SPLIT_GLOBAL = "global"
SPLIT_RULE = "rule"
SPLIT_DIRECT = "direct"
SPLIT_MODES = (SPLIT_GLOBAL, SPLIT_RULE, SPLIT_DIRECT)
SPLIT_MODE_LABELS = {
    SPLIT_GLOBAL: "全局代理（全部流量走节点，默认）",
    SPLIT_RULE: "规则分流（黑名单/国内直连规则集，其余走节点）",
    SPLIT_DIRECT: "全部直连（引擎在跑但不代理，对比/排障用）",
}

# 网站黑名单条目处置（DESIGN §4.2）
SITE_REJECT = "reject"      # 路由侧拦截：命中即断
SITE_DIRECT = "direct"      # 命中走直连不走节点（仅 rule 模式生成）

# 引擎侧既有 tag（singbox_engine.py _OUT_TAG 同名，保持一致）
OUT_TAG = "node-out"
DIRECT_OUT_TAG = "direct-out"
REMOTE_DNS_TAG = "remote-dns"      # 引擎 dns.servers 里经节点绕行的解析器
RESOLVE_RULE_SERVER = "remote-dns"  # 引擎 route.rules[0] resolve 规则引用的 server 名

# DNS 预设（DESIGN §4.4）。公共解析器地址非节点地址；8.8.8.8 与
# 引擎现行写死值一致（singbox_engine.py 225 行）。
DNS_PRESETS = {
    "google": "8.8.8.8",
    "cloudflare": "1.1.1.1",
    "quad9": "9.9.9.9",
}

# 规则集 tag（DESIGN §8.1 固定名）与占位标记
ADS_RULESET_TAG = "ads-blockset"
MALWARE_RULESET_TAG = "malware-blockset"
CN_DIRECT_RULESET_TAG = "cn-directset"
PLACEHOLDER_KEY = "prototype_placeholder"   # 占位条目必带此键（DESIGN §8.2：
                                            # 集成段换真条目时删占位键=核定完成）
_PLACEHOLDER_DIR = "cache/rulesets"

# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
class UsageSettingsError(ValueError):
    """设置/黑名单非法。报错不静默吞（任务口径）。"""


# 裸域名校验：小写字母数字连字符的标签、至少一个点、无 scheme/斜杠/空格/
# 通配符/@；IDN 要求先转 punycode（报错提示）。长度约束按 RFC 惯例。
_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}\.?$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")


def _validate_domain(raw) -> str:
    """网站黑名单条目的域名校验 → 规范化小写裸域名。非法抛 UsageSettingsError。"""
    if isinstance(raw, bool) or not isinstance(raw, str):
        raise UsageSettingsError(f"网站黑名单条目必须是字符串域名，得到 {raw!r}")
    domain = raw.strip().lower().rstrip(".")
    if not domain:
        raise UsageSettingsError("网站黑名单条目不能是空串")
    if "://" in domain or "/" in domain or " " in domain or "@" in domain:
        raise UsageSettingsError(
            f"网站黑名单条目必须是裸域名（不带 scheme/路径/空格/@）: {raw!r}")
    if "*" in domain:
        raise UsageSettingsError(
            f"网站黑名单暂不支持通配符（domain_suffix 本身含子域匹配，"
            f"写裸域名即可）: {raw!r}")
    try:
        domain.encode("ascii")
    except UnicodeEncodeError:
        raise UsageSettingsError(
            f"网站黑名单条目含非 ASCII（IDN 请先转 punycode）: {raw!r}") from None
    if "." not in domain:
        raise UsageSettingsError(
            f"网站黑名单条目必须是带点的域名（拒绝单标签/保留名）: {raw!r}")
    if not _DOMAIN_RE.match(domain):
        raise UsageSettingsError(f"网站黑名单条目不是合法域名: {raw!r}")
    return domain


def _validate_site_entry(entry) -> tuple:
    """黑名单条目 → (domain, action)。str=默认 reject；dict=显式两键。"""
    if isinstance(entry, dict):
        extra = set(entry) - {"domain", "action"}
        if extra:
            raise UsageSettingsError(
                f"网站黑名单 dict 条目只允许 domain/action 键，多出 {sorted(extra)}")
        if "domain" not in entry:
            raise UsageSettingsError(f"网站黑名单 dict 条目缺 domain: {entry!r}")
        action = entry.get("action", SITE_REJECT)
        if action not in (SITE_REJECT, SITE_DIRECT):
            raise UsageSettingsError(
                f"网站黑名单 action 只允许 '{SITE_REJECT}'/'{SITE_DIRECT}'，"
                f"得到 {action!r}")
        return _validate_domain(entry["domain"]), action
    return _validate_domain(entry), SITE_REJECT


def parse_site_blocklist(site_blocklist) -> tuple:
    """外部入参 → 去重排序前的 (domain, action) 元组序列。

    同域名两种处置并存 → 报错（歧义不静默吞）；同域名同处置重复 → 去重
    （先出现者胜，保序）。"""
    pairs = []
    seen = {}
    for entry in site_blocklist or ():
        domain, action = _validate_site_entry(entry)
        if domain in seen:
            if seen[domain] != action:
                raise UsageSettingsError(
                    f"网站黑名单同域名两种处置并存（歧义）: {domain} "
                    f"既有 {seen[domain]} 又来 {action}")
            continue                      # 同域名同处置重复：去重保序
        seen[domain] = action
        pairs.append((domain, action))
    return tuple(pairs)


# ---------------------------------------------------------------------------
# 设置模型
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class UsageSettings:
    """使用界面规则与网络区的设置模型（DESIGN §4，纯数据 + 校验）。

    默认值 = 现行行为（global / 全关 / v4-only / google DNS）：客户端
    不做用户没要求的事，所有改变流量的行为都显式开启（DESIGN §4.3）。
    白/黑名单节点侧归后台，不在此模型（DESIGN §0 红线 3）。
    """
    split_mode: str = SPLIT_GLOBAL
    ad_block: bool = False
    malware_block: bool = False
    ipv6: bool = False
    dns_server: str = "google"            # 预设键或 IP 字面量（v4/v6）

    def __post_init__(self):
        if self.split_mode not in SPLIT_MODES:
            raise UsageSettingsError(
                f"分流模式必须是 {'/'.join(SPLIT_MODES)} 之一，得到 {self.split_mode!r}")
        for name in ("ad_block", "malware_block", "ipv6"):
            value = getattr(self, name)
            if not isinstance(value, bool):
                raise UsageSettingsError(
                    f"{name} 必须是布尔值（不许 0/1 冒充），得到 {value!r}")
        # DNS 服务器：预设键或 IP 字面量；域名拒收（DNS 服务器自己要先被
        # 解析，域名形态先有鸡问题——DESIGN §4.4）。
        if self.dns_server in DNS_PRESETS:
            server_ip = DNS_PRESETS[self.dns_server]
        else:
            if isinstance(self.dns_server, bool) or not isinstance(self.dns_server, str) \
                    or not self.dns_server.strip():
                raise UsageSettingsError(
                    f"dns_server 必须是预设键 {'/'.join(DNS_PRESETS)} 或 IP 字面量，"
                    f"得到 {self.dns_server!r}")
            try:
                server_ip = str(ipaddress.ip_address(self.dns_server.strip()))
            except ValueError:
                raise UsageSettingsError(
                    f"dns_server 不是合法 IP 字面量也不是预设键 "
                    f"{'/'.join(DNS_PRESETS)}（域名形态拒收：解析 DNS 服务器"
                    f"本身有先有鸡问题）: {self.dns_server!r}") from None
        # 交叉校验：IPv6 关时 DNS 服务器不允许 v6 地址——v4-only 口径下
        # 引擎出站到 v6 DNS 可能失败，报错不静默（DESIGN §4.4）。
        if not self.ipv6 and ":" in server_ip:
            raise UsageSettingsError(
                f"IPv6 开关关闭时 DNS 服务器不能用 v6 地址（{server_ip}）——"
                "现行 v4-only 防泄漏口径下引擎到 v6 DNS 的出站不可靠；"
                "先开 IPv6 或换 v4 DNS 服务器")

    # ---------------- 序列化（task12 设置页复用，口径同 DESIGN §11） ----------------
    def to_dict(self) -> dict:
        return {
            "split_mode": self.split_mode,
            "ad_block": self.ad_block,
            "malware_block": self.malware_block,
            "ipv6": self.ipv6,
            "dns_server": self.dns_server,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "UsageSettings":
        if not isinstance(d, dict):
            raise UsageSettingsError(f"设置必须是 dict，得到 {d!r}")
        extra = set(d) - set(cls.__dataclass_fields__)
        if extra:
            raise UsageSettingsError(f"设置含未知键（拒绝静默忽略）: {sorted(extra)}")
        return cls(**d)

    def resolve_dns_ip(self) -> str:
        """DNS 选择 → 实际 IP（预设解析、IP 原样规范化）。"""
        if self.dns_server in DNS_PRESETS:
            return DNS_PRESETS[self.dns_server]
        return str(ipaddress.ip_address(self.dns_server.strip()))


# ---------------------------------------------------------------------------
# 片段生成
# ---------------------------------------------------------------------------
def _placeholder_ruleset(tag: str, filename: str) -> dict:
    """本地占位规则集条目（DESIGN §8.2）：假路径 + 占位键，绝不假装是
    真来源；集成段主人核定来源后替换并删占位键。"""
    return {
        "type": "local",
        "tag": tag,
        "format": "binary",
        "path": f"{_PLACEHOLDER_DIR}/{filename}",
        PLACEHOLDER_KEY: True,
    }


def build_route_config(model: UsageSettings, site_blocklist=()) -> dict:
    """设置模型 + 网站黑名单 → sing-box 路由/DNS 配置片段（假数据可跑）。

    返回结构（DESIGN §4.5 合并顺序契约的机械落地说明）：
    - route.rules：按语义排序的规则（引擎合并时插在既有 resolve 规则
      **之前**——先拦截后解析，被拦域名不做无谓解析；insert_before 提示）；
    - route.rule_set：本片段引用的规则集定义（全部占位条目，带
      prototype_placeholder 键，DESIGN §8.2）；
    - route.final_override：仅 direct 模式给 direct-out，其余 None
      （route.final 引擎原样保留，单节点单出站语义不动）；
    - dns_patch：写回引擎既有字段的指令——remote-dns 的 server、
      resolve 规则的 strategy（None=移除该字段，IPv6 开）；
    - outbound_patch：mode≠global 时需引擎追加的出站（direct-out）；
    - uncovered：显式未覆盖清单（翻不出来的功能绝不假装生成）；
    - notes：模式语义/跳过说明。

    本函数不产完整引擎配置：inbounds/出站主体/TUN 合并全是引擎与
    core/tun_config.py 的既有职责（uncovered 里同名标注）。
    """
    if not isinstance(model, UsageSettings):
        raise UsageSettingsError(
            f"model 必须是 UsageSettings，得到 {type(model).__name__}")
    pairs = parse_site_blocklist(site_blocklist)

    rules = []
    rule_set = []
    notes = []
    outbound_patch = []
    final_override = None

    # ① 网站黑名单 reject 条目：所有模式生效（黑名单语义=命中即断，
    # 主人钦点名单不该被模式冲掉，DESIGN §4.1/§4.2）。
    reject_domains = [d for d, a in pairs if a == SITE_REJECT]
    if reject_domains:
        rules.append({"domain_suffix": reject_domains, "action": SITE_REJECT})

    # ② 网站黑名单 direct 条目：仅 rule 模式生成；global/direct 模式下
    # 与模式语义冲突 → 显式跳过并记 notes（不静默吞）。
    direct_domains = [d for d, a in pairs if a == SITE_DIRECT]
    if direct_domains:
        if model.split_mode == SPLIT_RULE:
            rules.append({"domain_suffix": direct_domains, "action": "route",
                          "outbound": DIRECT_OUT_TAG})
        else:
            notes.append(
                f"网站黑名单 {len(direct_domains)} 个 direct 条目在 "
                f"{model.split_mode} 模式下不生成（与模式语义冲突，显式跳过）："
                + ", ".join(direct_domains))

    # ③ 广告拦截（DESIGN §8：rule_set 机制，tag 固定）
    if model.ad_block:
        rules.append({"rule_set": [ADS_RULESET_TAG], "action": SITE_REJECT})
        rule_set.append(_placeholder_ruleset(ADS_RULESET_TAG, "ads.srs"))

    # ④ 恶意软件拦截（与广告同一机制，独立 tag）
    if model.malware_block:
        rules.append({"rule_set": [MALWARE_RULESET_TAG], "action": SITE_REJECT})
        rule_set.append(_placeholder_ruleset(MALWARE_RULESET_TAG, "malware.srs"))

    # ⑤ rule 模式的国内直连规则集（DESIGN §4.1：常识规则归规则集，
    # 不另设开关；来源同走 §8.2 核定制）
    if model.split_mode == SPLIT_RULE:
        rules.append({"rule_set": [CN_DIRECT_RULESET_TAG], "action": "route",
                      "outbound": DIRECT_OUT_TAG})
        rule_set.append(_placeholder_ruleset(CN_DIRECT_RULESET_TAG, "cn-direct.srs"))
        outbound_patch.append({"type": "direct", "tag": DIRECT_OUT_TAG})
    elif model.split_mode == SPLIT_DIRECT:
        # direct 模式：route.final 改指 direct-out（引擎合并时写回）。
        final_override = DIRECT_OUT_TAG
        outbound_patch.append({"type": "direct", "tag": DIRECT_OUT_TAG})
    # global 模式：final 不动、不加出站（保持单节点单出站现状）。

    dns_patch = {
        # 写回 dns.servers 中 tag=remote-dns 条目的 server 字段
        # （detour=node-out 与 dns.final=local-dns 不动，DESIGN §4.4）。
        "remote_dns_server_tag": REMOTE_DNS_TAG,
        "remote_dns_server": model.resolve_dns_ip(),
        # 写回 route.rules 中 action=resolve 规则的 strategy 字段；
        # None=移除（IPv6 开=放开 v4-only，引擎侧同步删字段）。
        "resolve_rule_strategy": None if model.ipv6 else "ipv4_only",
    }

    return {
        "mode": model.split_mode,
        "route": {
            "insert_before": "resolve",     # 引擎合并顺序提示（DESIGN §4.5-2）
            "rules": rules,
            "rule_set": rule_set,
            "final_override": final_override,
        },
        "dns_patch": dns_patch,
        "outbound_patch": outbound_patch,
        "uncovered": _uncovered(),
        "notes": notes,
    }


def _uncovered() -> list:
    """翻不出来的功能，显式标注（DESIGN §3 逐项路径表同源，绝不假装生成）。"""
    return [
        {"feature": "容灾 / 自动替换",
         "belongs": "客户端自有逻辑（DESIGN §5：红色才换、只换组内、默认关）",
         "why": "会话行为，无路由配置片段"},
        {"feature": "TUN 模式",
         "belongs": "core/tun_config.py 专职生成",
         "why": "已有专职模块，本模型不重复生成；合并时 tun 的 sniff/hijack-dns 保持最前置"},
        {"feature": "测速 / 网络速度展示",
         "belongs": "客户端自有逻辑（DESIGN §10）",
         "why": "实测行为，无配置片段"},
        {"feature": "定时监测",
         "belongs": "客户端定时器（复用 5s poll tick 子周期机制）",
         "why": "调度行为，无配置片段"},
        {"feature": "自动更新",
         "belongs": "来源登记制 update_entry 定时包装 + 规则集更新按钮",
         "why": "调度行为，无配置片段"},
        {"feature": "特殊网站测试",
         "belongs": "验真探针族扩展（DESIGN §9）",
         "why": "实测行为，无配置片段"},
        {"feature": "节点白/黑名单",
         "belongs": "后台节点库字段（DESIGN §6）",
         "why": "节点属性，不进路由；网站黑名单已由本函数入参消费"},
        {"feature": "应用净化",
         "belongs": "DESIGN §7（v1 不做，v2 待 process_name 核实）",
         "why": "Linux 无通用按应用分流机制，无片段"},
        {"feature": "订阅导入",
         "belongs": "sources 层（已有全套）",
         "why": "供给侧功能，无配置片段"},
    ]
