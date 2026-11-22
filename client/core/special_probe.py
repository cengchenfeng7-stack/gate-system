#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
特殊站分级判定器（core/special_probe.py）—— U5：verdict_muse 的推广版
========================================================================
task20 · 阶段五·使用界面线 第六段。纯标准库；不依赖 PySide6、不碰网络
——探测动作（DNS / 连接 / HTTP 请求）全部为注入参数，真实网络胶水只
活在 UI 层（work10 §9.2；与 core/verify.py「probe 注入、单测不发真实
请求」同一纪律）。

分级判定（work10/DESIGN.md §9.2 定稿，逐级一致）：
- L0 链路    ：DNS 解析 + TCP/TLS 可达；
- L1 可打开  ：HTTP 响应 200/204/3xx；
- L2 登录页真实：200 且响应体是正常页面形态（非风控拦截页）；
  400=被拒（出口被风控）、403=被拦（Bot 防护）、429=限流——均记
  「风控命中」（§9.2 把这三个码放在 L2 关卡，判定复用 verify.py
  verdict_muse 的语义推广为 verdict_site）；
- L3 实际使用：**永远人工**（要登录会话/图形验证，客户端判不了）。
  **红线：L2 全过只标「登录页可达」，不许标成能实际使用的样子
  （测试锁死，禁词两字在源码里以拼接构造——全仓字面零命中）。**

本模块只测「这个出口被不被接受」：不重试、不跟随重定向、不伪造指纹、
不解验证、不换 UA——这些绕过手法一概不做，也不提供任何开关。

结果落点（简报口径）：写节点 extra["special_tests"]
（{domain: {level, code, ts}}），**不落 verify_samples 主口径**（不污
染养鱼池统计，本模块不 import history/store，结构断言见 test_lock）。
诊断导出时该键被 diag 的 extra 未知键默认剔除口径自动覆盖（diag.py
_redact_extra 四档外的键一律剔除），不泄漏测试域名——task7 已落口径，
本段不绕开、不改 diag。

level 语义（唯一口径，呈现函数据此解读，presented 口径只有一条）：
- 2 = 判定走到 L2 关卡：code 200 = 登录页真实（全过）；code ∈
  {400,403,429} = 风控命中（未过）。
- 1 = 判定停在 L1：拿到了 HTTP 响应但 L2 未给过（非 200/204/3xx 码、
  204/3xx 无体可判、或 200 但体形态判不过/判不准——判不准时宁记未过，
  不冒充通过）。code = 实际状态码。
- 0 = 判定停在 L0：链路层（DNS/TCP/TLS/请求未完成）就不通，code 0。
唯一「通过」判据 = level==2 且 code==200（login_page_ok）。

铁律对齐：本模块只产生分级结论与呈现文案，没有任何连接/切换/拉黑
路径——测出风控命中只呈现，处置归后台人工（永不自动切换节点）。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# 关卡与判定常量
# ---------------------------------------------------------------------------
LEVEL_LINK = 0     # L0 链路（DNS + TCP/TLS）
LEVEL_OPEN = 1     # L1 可打开（HTTP 200/204/3xx）
LEVEL_PAGE = 2     # L2 登录页真实（200 且正常页面形态）

RISK_CODES = (400, 403, 429)      # 风控命中三码（§9.2：被拒/被拦/限流）
PASS_CODE = 200                   # 唯一通过码：L2 全过 = level 2 + code 200

SPECIAL_TESTS_KEY = "special_tests"   # 结果在节点 extra 里的键名（简报口径）

# 风控拦截页形态的通用特征标记（公开可知的挑战页指纹，注入可覆盖——
# 这是「识别拦截页」的检测特征，不是任何绕过手法；清单条目同理只认
# 通用特征，不存任何真实站点的路径级探测点）
INTERSTITIAL_MARKERS = (
    "just a moment",
    "checking your browser",
    "browser verification",
    "cf-challenge",
    "challenge-platform",
    "cf-browser-verification",
    "ddos-guard",
    "attention required",
    "enable javascript and cookies",
)
MIN_BODY_CHARS = 200               # 正常登录页形态的长度下限（宁严不松）
_HTML_SHAPE_MARKS = ("<html", "<body", "<div", "<title", "<form", "<!doctype")

# 判定函数合同：吃响应体（str 或 None），返回 (形态过, 原因, 是否拦截页特征)
ShapeJudge = Callable[[Optional[str]], tuple]

# ---------------------------------------------------------------------------
# 呈现文案的禁词自检（红线第二道锁：文案出口统一收口，谁塞禁词谁炸）
# ---------------------------------------------------------------------------
_BANNED_SUBSTR = ("可" "用",)   # 禁词两字拼接构造（全仓字面零命中，test_lock 锁）


def _check_label(label: str) -> str:
    """对外文案出口过一遍禁词（verdict_site 全部 label 出口已挂；
    badge/row/summary 的文案由字面量拼装、禁词主锁在 test_lock.py
    全量枚举 + 全仓 grep）。"""
    for w in _BANNED_SUBSTR:
        if w in label:
            raise ValueError(f"呈现文案命中禁词（L2 不许说成能用）：{label!r}")
    return label


# ---------------------------------------------------------------------------
# 状态码判定：verdict_muse 的推广版（语义逐项对齐，见 test 锁定用例）
# ---------------------------------------------------------------------------
@dataclass
class SiteVerdict:
    """一次状态码判定的结论。"""
    level: int        # 判定停住的关卡（0/1/2，语义见模块 docstring）
    code: int         # 判定依据码（0 = 链路层失败/请求未完成）
    ok: bool          # True 仅当登录页真实（全过；L2 红线口径）
    risk: bool        # 风控命中（被拒/被拦/限流/拦截页形态）
    label: str        # 人话标签（呈现用）


def verdict_site(code, body_hint=None, shape_judge=None) -> SiteVerdict:
    """通用状态码判定（verdict_muse 的推广）：

    - 0/None → L0 链路死；
    - 400 → L2 被拒（出口 IP 被风控）；403 → L2 被拦（Bot 防护）；
      429 → L2 限流——均记风控命中；
    - 200 → 交形态判定（注入 shape_judge，默认 default_shape_judge）：
      形态过 = L2 全过「登录页可达」；形态不过/判不准 = 停 L1 记未过；
    - 204/3xx → L1 过（可打开），但无响应体可判，登录页未判——宁记
      未过，不给 L2；
    - 其余码 → L1 未打开。
    """
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = 0
    if code <= 0:
        return SiteVerdict(LEVEL_LINK, 0, False, False, "链路死")
    if code in RISK_CODES:
        label = {400: "被拒（出口被风控）", 403: "被拦（Bot 防护）",
                 429: "限流"}[code]
        return SiteVerdict(LEVEL_PAGE, code, False, True, _check_label(label))
    if code == PASS_CODE:
        judge = shape_judge if shape_judge is not None else default_shape_judge
        shape_ok, reason, risk = _run_shape_judge(judge, body_hint)
        if shape_ok:
            return SiteVerdict(LEVEL_PAGE, PASS_CODE, True, False,
                               _check_label("登录页可达"))
        return SiteVerdict(LEVEL_OPEN, PASS_CODE, False, bool(risk),
                           _check_label(f"登录页未确认（{reason}）"))
    if code == 204 or 300 <= code < 400:
        return SiteVerdict(LEVEL_OPEN, code, False, False,
                           _check_label("可打开（登录页未判）"))
    return SiteVerdict(LEVEL_OPEN, code, False, False,
                       _check_label(f"未打开（HTTP {code}）"))


def _run_shape_judge(judge, body_hint) -> tuple:
    """形状判定合同兜底：judge 异常/返回形态不对时一律按「判不准 →
    未过」处理（宁记未过，不冒充通过）。"""
    try:
        out = judge(body_hint)
    except Exception:
        return (False, "形态判定函数出错", False)
    if not isinstance(out, tuple) or len(out) != 3:
        return (False, "形态判定返回形态非法", False)
    return (bool(out[0]), str(out[1]), bool(out[2]))


# ---------------------------------------------------------------------------
# L2 形态判定（只认状态码 + 响应体形态特征；特征全部可注入）
# ---------------------------------------------------------------------------
def default_shape_judge(body_hint) -> tuple:
    """默认形态判定（长度/关键标记/页面形态三步，宁严不松）：

    1) 体为空 → 未过（判不了）；
    2) 命中拦截页特征标记（大小写不敏感，长页也拦）→ 未过且记风控特征；
    3) 体过短（< MIN_BODY_CHARS）→ 未过（正常登录页不至于这么短）；
    4) 体无 HTML 页面形态（没有任何标记标签）→ 未过；
    5) 其余 → 过。

    局限如实记录（DESIGN.md §L2）：判不准时一律往「未过」倒——宁可
    少标一个「登录页可达」，不冒充通过；已知假阴性/假阳性边界见
    DONE.md 未解决项。
    """
    if body_hint is None or not str(body_hint).strip():
        return (False, "响应体为空", False)
    text = str(body_hint)
    low = text.lower()
    hit = next((m for m in INTERSTITIAL_MARKERS if m in low), None)
    if hit:
        return (False, f"命中拦截页特征（{hit}）", True)
    if len(text) < MIN_BODY_CHARS:
        return (False, f"响应体过短（{len(text)} 字符）", False)
    if "<" not in low or not any(m in low for m in _HTML_SHAPE_MARKS):
        return (False, "响应体无页面形态", False)
    return (True, "", False)


def make_shape_judge(markers=None, min_chars=None) -> ShapeJudge:
    """构造注入用形态判定函数（特征口径同 default_shape_judge，标记表
    与长度阈值可覆盖——主人后续想收紧/放宽特征只换参数，不改判定结构）。
    """
    marks = tuple(m.lower() for m in (markers if markers is not None
                                      else INTERSTITIAL_MARKERS))
    min_chars = MIN_BODY_CHARS if min_chars is None else int(min_chars)

    def _judge(body_hint) -> tuple:
        if body_hint is None or not str(body_hint).strip():
            return (False, "响应体为空", False)
        text = str(body_hint)
        low = text.lower()
        hit = next((m for m in marks if m in low), None)
        if hit:
            return (False, f"命中拦截页特征（{hit}）", True)
        if len(text) < min_chars:
            return (False, f"响应体过短（{len(text)} 字符）", False)
        if "<" not in low or not any(m in low for m in _HTML_SHAPE_MARKS):
            return (False, "响应体无页面形态", False)
        return (True, "", False)

    return _judge


# ---------------------------------------------------------------------------
# L0→L2 探测流水线（动作全注入；任一级失败即停并记录级别与代码）
# ---------------------------------------------------------------------------
@dataclass
class SiteProbeResult:
    """单站流水线结果（write_special_tests 的输入形态）。"""
    domain: str
    level: int
    code: int
    label: str
    risk: bool


def probe_site(domain, resolve, connect, fetch, shape_judge=None) -> SiteProbeResult:
    """对单站跑 L0→L2 流水线。注入合同：

    - resolve(domain) -> Optional[str]：DNS 解析，返回地址；None/异常
      = L0 败；
    - connect(addr) -> bool：TCP/TLS 可达；False/异常 = L0 败；
    - fetch(url) -> (code, body)：**单次** HTTP GET（异常/码 ≤0 = 链路
      败，归 L0 口径）；探测目标只有根路径 https://<域名>/（路径级
      探测点永不出现，红线）。

    不重试（每个动作只调一次）、不跟随重定向（由 fetch 实现方保证）、
    不带任何绕过手法。任一级失败即停，记录停住的级别与依据码。
    """
    url = f"https://{domain}/"
    try:
        addr = resolve(domain)
    except Exception as exc:
        return SiteProbeResult(domain, LEVEL_LINK, 0, f"DNS 解析失败（{exc}）", False)
    if not addr:
        return SiteProbeResult(domain, LEVEL_LINK, 0, "DNS 解析失败", False)
    try:
        linked = connect(addr)
    except Exception as exc:
        return SiteProbeResult(domain, LEVEL_LINK, 0, f"连接失败（{exc}）", False)
    if not linked:
        return SiteProbeResult(domain, LEVEL_LINK, 0,
                               "连接失败（TCP/TLS 不可达）", False)
    try:
        code, body = fetch(url)
    except Exception as exc:
        return SiteProbeResult(domain, LEVEL_LINK, 0, f"请求失败（{exc}）", False)
    v = verdict_site(code, body, shape_judge)
    return SiteProbeResult(domain, v.level, v.code, v.label, v.risk)


# ---------------------------------------------------------------------------
# 结果回写（extra["special_tests"]；不落 verify_samples——本模块不碰
# history/store，落库 upsert 由调用方负责）
# ---------------------------------------------------------------------------
def write_special_tests(node, results, *, now_fn=time.time) -> dict:
    """把流水线结果合并回节点 extra["special_tests"]：
    {domain: {level, code, ts}}——新结果覆盖同域名旧结果，其他域名的
    旧结果保留（节点行徽标按当前清单渲染，旧域名记录不删不显）。

    node 按 client.core.model.Node 鸭子类型（有 .extra dict），不 import
    模型类；results 是 SiteProbeResult 序列（或带 domain/level/code 属性
    的等价物）。键值形态非法（level 出 0..2、code 负数）如实报错。
    返回合并后的 records；时间戳经 now_fn 注入（测试可定死）。
    """
    extra = getattr(node, "extra", None)
    if not isinstance(extra, dict):
        raise TypeError("node 需要带 .extra dict（Node 鸭子类型）")
    records = extra.get(SPECIAL_TESTS_KEY)
    if records is None:
        records = {}
        extra[SPECIAL_TESTS_KEY] = records
    if not isinstance(records, dict):
        raise ValueError(
            f"extra[{SPECIAL_TESTS_KEY!r}] 已存在且不是 dict（形态被外部改坏），"
            "拒绝覆盖原始证据——请人工检查该节点记录。")
    ts = float(now_fn())
    for r in results:
        domain = str(getattr(r, "domain", "") or "").strip()
        level = int(getattr(r, "level", -1))
        code = int(getattr(r, "code", -1))
        if not domain:
            raise ValueError(f"探测结果缺 domain：{r!r}")
        if level not in (LEVEL_LINK, LEVEL_OPEN, LEVEL_PAGE) or code < 0:
            raise ValueError(f"探测结果形态非法（level/code 越界）：{r!r}")
        records[domain] = {"level": level, "code": code, "ts": ts}
    return records


# ---------------------------------------------------------------------------
# 呈现纯函数（②区节点行「特殊站」列 / ⑥区总结口径；全过只说登录页可达）
# ---------------------------------------------------------------------------
def login_page_ok(record) -> bool:
    """唯一「通过」判据：level==2 且 code==200（登录页真实）。其余一切
    形态（含 level 2 + 风控码、level 1 任意码、无记录）都算未过。"""
    if not isinstance(record, dict):
        return False
    level, code = record.get("level"), record.get("code")
    return (isinstance(level, int) and not isinstance(level, bool)
            and level == LEVEL_PAGE
            and isinstance(code, int) and not isinstance(code, bool)
            and code == PASS_CODE)


def is_risk_hit(record) -> bool:
    """风控命中（记录口径）：level==2 且 code ∈ {400,403,429}。
    拦截页形态的命中在记录里落 level 1 + code 200（形态判不过——宁记
    未过），此处不重复计。"""
    if not isinstance(record, dict):
        return False
    level, code = record.get("level"), record.get("code")
    return (isinstance(level, int) and not isinstance(level, bool)
            and level == LEVEL_PAGE
            and code in RISK_CODES)


def badge_text(name, record):
    """失败站徽标文案（「<站名> <码>」口径，如「某站 403」）：
    通过站返回 None（无徽标）；无记录由调用方给「未测」徽标（见
    row_special_text）。level 1 + code 200（开了门但登录页未确认）
    显示「<站名> 存疑」——不显示裸 200，免得被读成通过。"""
    if record is None or login_page_ok(record):
        return None
    level = record.get("level") if isinstance(record, dict) else None
    code = record.get("code") if isinstance(record, dict) else None
    if level == LEVEL_LINK:
        return f"{name} 不通"
    if level == LEVEL_OPEN and code == PASS_CODE:
        return f"{name} 存疑"
    return f"{name} {code}"


def row_special_text(records, sites) -> str:
    """②区节点行「特殊站」列文案：Σ 通过数/总数 + 失败站徽标。

    - 只统计当前 enabled 站点（disabled 的不进总数不进徽标；清单里
      已移除域名的旧记录不显不计）；
    - enabled 但无记录的站给「<站名> 未测」徽标（诚实呈现缺口）；
    - 无 enabled 站点 → ""（列留空，不是 0/0）。
    与 scenes.ai 场景徽标并存不合并（画像判定 vs 实测徽标是两回事，
    各走各的列，互不覆盖）。
    """
    enabled = [s for s in (sites or [])
               if isinstance(s, dict) and s.get("enabled")]
    if not enabled:
        return ""
    records = records if isinstance(records, dict) else {}
    passed, badges = 0, []
    for s in enabled:
        name = str(s.get("name") or s.get("domain") or "?")
        rec = records.get(s.get("domain"))
        if login_page_ok(rec):
            passed += 1
        else:
            badge = badge_text(name, rec)
            badges.append(badge if badge is not None else f"{name} 未测")
    head = f"{passed}/{len(enabled)}"
    return head + (" · " + "、".join(badges) if badges else "")


def summary_text(records, sites) -> str:
    """⑥区/完成消息的总结口径：全过 → 「登录页可达」（红线：绝不出现
    把 L2 说成能用的表述，test_lock 全量锁死）；否则给通过数 + 徽标。
    L3 永远人工的口径随全过文案一并呈现。"""
    enabled = [s for s in (sites or [])
               if isinstance(s, dict) and s.get("enabled")]
    if not enabled:
        return "特殊站清单为空：先在⑥区添加要测的站点（只用你自己核定的公开域名）。"
    records = records if isinstance(records, dict) else {}
    total = len(enabled)
    passed = sum(1 for s in enabled if login_page_ok(records.get(s.get("domain"))))
    if passed == total:
        return (f"登录页可达（{passed}/{total} 全部 L2 通过）。"
                "真实登录与使用仍需人工确认——客户端只测到「登录页真实」这一级。")
    badges = []
    for s in enabled:
        name = str(s.get("name") or s.get("domain") or "?")
        badge = badge_text(name, records.get(s.get("domain")))
        badges.append(badge if badge is not None else f"{name} 未测")
    return f"通过 {passed}/{total}：" + "、".join(badges)
