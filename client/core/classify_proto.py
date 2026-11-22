#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
task11 分类原型：界面六板块归类（设计见 DESIGN.md）
====================================================

只依赖标准库；不 import client 包、不依赖 PySide6（简报红线）。
对纯 dict 节点记录操作，字段口径与 client/core/model.py 的 Node / ExitProfile
对齐（DESIGN.md §2 有逐字段对照表）。

API 分层（自底向上）：
1. classify_exit(isp_name, org) -> str
     出口性质三态判定：residential / datacenter / unknown。
     只认传入的验真 ISP 字符串（集成映射：isp_name=ExitProfile.org、
     org=ExitProfile.asn），没有数据或未命中归类表一律 "unknown"，绝不猜。
2. dims_of(node) -> dict
     原始节点 dict → 三维 {source_nature, exit_kind, form}（口径见 DESIGN §3）。
3. board_of(node) -> str
     纯组合规则：入参含三维（source_nature / exit_kind / form），返回六板块
     键之一或 "unclassified"；缺维度不硬塞（DESIGN §4 组合表）。
4. classify_library(nodes) -> dict
     整库批量分类：各板块计数 + 未分类清单（带缺维原因，供人工补标）；
     只读，不改原数据。

铁律落点（简报红线）：
- exit_kind 只认验真出口画像的 ISP 数据；没有就 unknown，绝不按节点名、
  来源页或延迟猜（主人「只认验真出口」铁律在分类段的落点）。
- 板块是归类不是排名：本模块不含任何优选/排序/切换语义（永不自动切换节点）。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 内置归类表（纯数据，可直接扩充；维护规则见 DESIGN.md §5）
#
# 数据来源：与 client/core/verify.py 的 DATA_CENTER_ORG_KEYWORDS /
# RESIDENTIAL_ORG_KEYWORDS（58-72 行）同源校准（2026-11-22 快照），
# 另补中国主流 ISP 与若干尽人皆知厂商。条目全部是公开常识级厂商名
# （简报红线：ISP/托管商名称只许用尽人皆知的厂商名做示例）。
# 匹配语义：把两个入参拼成大写字符串后做【子串】匹配；机房先查、住宅后查
# （与 verify.classify_network 的先后顺序一致，保证两处判定不打架）。
# 扩充纪律：只增不改不删已有条目；短关键词（如两位缩写）先评估误伤再加；
# 新条目须公开常识级或经交叉核实（主人信息处理规则 2026-11-19：搜到的
# 信息只算原始资料，交叉对比通过才进表）；加完跑 test_classify 回归。
# ---------------------------------------------------------------------------

# 托管商/云厂商关键词（命中 → datacenter）
DATACENTER_KEYWORDS = (
    "GOOGLE", "AMAZON", "AWS", "MICROSOFT", "AZURE", "ORACLE", "IBM CLOUD",
    "DIGITALOCEAN", "CLOUDFLARE", "AKAMAI", "FASTLY", "OVH", "HETZNER",
    "RACKSPACE", "EQUINIX", "LINODE", "VULTR", "CHOOPA", "CONTABO", "M247",
    "LEASEWEB", "DATACAMP", "COGENT", "HURRICANE ELECTRIC",
    "TENCENT", "ALIBABA", "ALIYUN",
)

# 住宅 ISP 关键词（命中 → residential）
RESIDENTIAL_KEYWORDS = (
    # 日本（VPNGate 大本营）
    "NTT EAST", "NTT WEST", "NTT COMMUNICATIONS", "NTT BROADBAND", "NTT",
    "KDDI", "DOCOMO", "SOFTBANK", "J:COM", "JCOM", "OCN", "BIGLOBE", "IIJ",
    # 北美
    "COMCAST", "XFINITY", "AT&T", "VERIZON", "CHARTER", "SPECTRUM", "COX",
    "CENTURYLINK", "FRONTIER", "TELUS", "ROGERS", "BELL CANADA",
    # 欧洲
    "VODAFONE", "ORANGE", "DEUTSCHE TELEKOM", "TELEKOM", "FASTWEB",
    "TIM S.P.A", "LIBERO", "FREE FRANCE", "BRITISH TELECOM", "BT OPENREACH",
    # 亚太
    "KOREA TELECOM", "KT CORP", "SK BROADBAND", "LG UPLUS",
    "CHUNGHWA", "HINET", "SINGTEL", "STARHUB",
    # 中国
    "CHINA TELECOM", "CHINA UNICOM", "CHINA MOBILE", "CHINANET", "CMCC",
)

# 例外表（先于关键词表匹配；用于「长词命中更具体性质」的纠偏）。
# 例：GOOGLE FIBER 是住宅宽带，若不例外会被 GOOGLE 的机房关键词抢先命中。
# 结构预留，扩充同上纪律。
KIND_EXCEPTIONS = {
    "GOOGLE FIBER": "residential",
}

# ---------------------------------------------------------------------------
# 维度与板块常量（唯一字典：前台/后台/测试一律引用这里，不许另定一套）
# ---------------------------------------------------------------------------

SOURCE_NATURES = ("free", "paid", "pipeline")            # 来源性质
EXIT_KINDS = ("residential", "datacenter", "unknown")    # 出口性质（三态）
FORMS = ("openvpn", "proxy")                             # 接入形态

BOARDS = (
    "free_residential",    # 免费住宅
    "free_datacenter",     # 免费机房
    "openvpn",             # OpenVPN
    "pipeline",            # 私人流水线
    "paid_residential",    # 付费住宅
    "paid_datacenter",     # 付费机房
)
BOARD_LABELS = {
    "free_residential": "免费住宅",
    "free_datacenter": "免费机房",
    "openvpn": "OpenVPN",
    "pipeline": "私人流水线",
    "paid_residential": "付费住宅",
    "paid_datacenter": "付费机房",
}
UNCLASSIFIED = "unclassified"   # 兜底板块：维度组合不全时落这里，不许硬塞

# task8 来源合并的旧键映射（与 work8/merge_proto.py 的 LEGACY_SOURCE_KEYS 同款）：
# 合并前四页时代 source ∈ {openvpn, vpngate_feed, manual, pipeline}，
# 合并后 canonical ∈ {vpngate, manual, pipeline}。整库分类兼容两代来源键。
LEGACY_SOURCE_KEYS = {"openvpn": "vpngate", "vpngate_feed": "vpngate"}

# 节点 extra 里的分类标记键（人工标注/改判落点；跨合并/更新/迁移必须存活，
# 见 DESIGN.md §8 集成注意）：
COLLECTED_MARK = "collected_from"        # task9 搜集入库标记 → 来源性质恒为 free
NATURE_MARK = "nature"                   # manual/登记订阅的性质标注：free / paid
EXIT_OVERRIDE_MARK = "exit_kind_override"  # 出口性质人工改判：residential / datacenter


# ---------------------------------------------------------------------------
# 1. 出口性质三态判定
# ---------------------------------------------------------------------------
def classify_exit(isp_name, org) -> str:
    """按内置归类表判定出口性质，返回 residential / datacenter / unknown。

    入参是验真出口画像里的 ISP 字符串（集成映射：isp_name=ExitProfile.org、
    org=ExitProfile.asn，见 DESIGN §3.2）。两个入参都允许 None/空串。
    判定顺序：例外表 → 机房关键词 → 住宅关键词 → unknown。
    三态里的 "unknown" 不许被上层猜掉：没数据或表外 ISP 一律 unknown。
    """
    blob = " ".join(str(t) for t in (isp_name, org) if t).strip().upper()
    if not blob:
        return "unknown"
    for frag, kind in KIND_EXCEPTIONS.items():
        if frag in blob:
            return kind
    if any(k in blob for k in DATACENTER_KEYWORDS):
        return "datacenter"
    if any(k in blob for k in RESIDENTIAL_KEYWORDS):
        return "residential"
    return "unknown"


# ---------------------------------------------------------------------------
# 2. 原始节点 dict → 三维
# ---------------------------------------------------------------------------
def dims_of(node: dict) -> dict:
    """从原始节点记录推导三维 {source_nature, exit_kind, form}。

    口径（DESIGN §3）：
    - source_nature：来源事实优先——source=pipeline 恒为 pipeline、
      source=vpngate（含 task8 旧键）恒为 free、manual 且带 collected_from
      恒为 free（task9 搜集）；manual 靠 extra[NATURE_MARK] 标注，未标 → None。
    - exit_kind：extra[EXIT_OVERRIDE_MARK] 人工改判 > 验真画像 ISP 数据
      （exit_profile.org / exit_profile.asn 过 classify_exit）；两者都没有
      → "unknown"。
    - form：protocol=="openvpn" → openvpn，其余协议 → proxy；protocol 缺失
      但 extra.config_format 存在（task8：该键只在 OpenVPN 载荷节点上有）
      → openvpn；都缺 → None。

    显式维度键优先：node 里已带 source_nature / exit_kind / form 且取值合法
    时直接采信（供调用方传「已算好三维」的记录；非法值当作没给，走推导）。
    本函数只读，不改 node。
    """
    if not isinstance(node, dict):
        raise TypeError("node 必须是 dict")
    extra = node.get("extra") or {}
    profile = node.get("exit_profile") or {}
    return {
        "source_nature": _source_nature_of(node, extra),
        "exit_kind": _exit_kind_of(node, extra, profile),
        "form": _form_of(node, extra),
    }


def _source_nature_of(node: dict, extra: dict):
    given = node.get("source_nature")
    if given in SOURCE_NATURES:
        return given
    src = str(node.get("source") or "").strip().lower()
    if src in LEGACY_SOURCE_KEYS:
        src = LEGACY_SOURCE_KEYS[src]
    if src == "pipeline":
        return "pipeline"                       # 恒为私人流水线（硬判定）
    if src == "vpngate":
        return "free"                           # 恒为免费（硬判定，task8 合并页）
    if src == "manual":
        if extra.get(COLLECTED_MARK):           # task9 搜集入库：恒为免费
            return "free"
        mark = extra.get(NATURE_MARK)
        return mark if mark in ("free", "paid") else None   # 未标 → 兜底 None
    return None


def _exit_kind_of(node: dict, extra: dict, profile: dict) -> str:
    given = node.get("exit_kind")
    if given in ("residential", "datacenter"):  # 显式已判维度（unknown 视为没给）
        return given
    override = extra.get(EXIT_OVERRIDE_MARK)
    if override in ("residential", "datacenter"):
        return override                         # 人工改判 > 自动判定
    isp = str(profile.get("org") or "").strip()
    asn = str(profile.get("asn") or "").strip()
    if isp or asn:
        return classify_exit(isp, asn)          # 只认验真出口 ISP 数据
    return "unknown"                            # 无 ISP 数据：unknown，不许猜


def _form_of(node: dict, extra: dict):
    given = node.get("form")
    if given in FORMS:
        return given
    protocol = str(node.get("protocol") or "").strip().lower()
    if protocol:
        return "openvpn" if protocol == "openvpn" else "proxy"
    if extra.get("config_format"):              # task8：OpenVPN 载荷专属键
        return "openvpn"
    return None


# ---------------------------------------------------------------------------
# 3. 三维 → 六板块（纯组合规则）
# ---------------------------------------------------------------------------
def board_of(node: dict) -> str:
    """按组合规则返回六板块键之一或 "unclassified"。

    入参含三维：source_nature（free/paid/pipeline）、exit_kind
    （residential/datacenter/unknown）、form（openvpn/proxy）；
    也可以直接传原始节点 dict（此时先过 dims_of 推导，见 DESIGN §4）。
    组合优先级（依据见 DESIGN §4）：
      P1 source_nature == pipeline   → "pipeline"（自家产物优先归拢）
      P2 form == openvpn             → "openvpn"（OpenVPN 形态单列，不论免费付费）
      P3 nature ∈ {free, paid} × exit_kind × form==proxy → 四张性质×出口板块
    缺维度/取值非法/出口未知 → "unclassified"，不许硬塞。
    """
    if not isinstance(node, dict):
        raise TypeError("node 必须是 dict")
    raw = node
    complete = ("source_nature" in raw and "form" in raw and "exit_kind" in raw)
    node = raw if complete else dims_of(raw)    # 缺三维的原始记录先推导

    nature = node.get("source_nature")
    exit_kind = node.get("exit_kind")
    form = node.get("form")

    if nature == "pipeline":                    # P1
        return "pipeline"
    if form == "openvpn":                       # P2
        return "openvpn"
    if nature not in ("free", "paid"):
        return UNCLASSIFIED                     # 缺来源性质（含非法值）
    if form != "proxy":
        return UNCLASSIFIED                     # 缺接入形态：不知是否 OpenVPN
    if exit_kind == "residential":
        return "free_residential" if nature == "free" else "paid_residential"
    if exit_kind == "datacenter":
        return "free_datacenter" if nature == "free" else "paid_datacenter"
    return UNCLASSIFIED                         # 出口未知/缺失：不许猜（铁律）


# ---------------------------------------------------------------------------
# 4. 整库批量分类
# ---------------------------------------------------------------------------
def classify_library(nodes: list) -> dict:
    """整库批量分类，只读，不改原数据。

    返回：
      total         节点总数
      counts        各板块计数（六板块键 + "unclassified"）
      boards        六板块各自的节点 id 清单（后台徽标/前台分组直接取用）
      unclassified  未分类清单，每条 {id, name, source, missing}；
                    missing 是缺维原因列表（供人工补标，逐条可读）
    """
    counts = {b: 0 for b in BOARDS}
    counts[UNCLASSIFIED] = 0
    boards = {b: [] for b in BOARDS}
    unclassified = []
    total = 0

    for i, node in enumerate(nodes or []):
        if not isinstance(node, dict):
            raise TypeError(f"nodes[{i}] 必须是 dict")
        total += 1
        nid = node.get("id") or node.get("name") or f"#{i}"
        dims = dims_of(node)
        board = board_of(dims)
        if board == UNCLASSIFIED:
            counts[UNCLASSIFIED] += 1
            unclassified.append({
                "id": nid,
                "name": node.get("name") or "",
                "source": node.get("source") or "",
                "missing": _missing_reasons(node, dims),
            })
        else:
            counts[board] += 1
            boards[board].append(nid)

    return {
        "total": total,
        "counts": counts,
        "boards": boards,
        "unclassified": unclassified,
    }


def _missing_reasons(node: dict, dims: dict) -> list:
    """未分类节点的缺维原因（逐条人话，供后台补标清单用）。"""
    extra = node.get("extra") or {}
    profile = node.get("exit_profile") or {}
    has_isp = bool(str(profile.get("org") or "").strip()
                   or str(profile.get("asn") or "").strip())
    nature, form, exit_kind = (dims["source_nature"], dims["form"],
                               dims["exit_kind"])
    reasons = []

    if nature is None:
        src = str(node.get("source") or "").strip().lower()
        if src == "manual" and not extra.get(COLLECTED_MARK):
            reasons.append("source_nature: manual 来源未标性质（免费/付费），"
                           "登记补标后归位，不许猜")
        else:
            reasons.append(f"source_nature: 来源键「{node.get('source')}」"
                           "无法判性质（未知来源）")
    if form is None and nature != "pipeline":
        reasons.append("form: 缺 protocol/form，接入形态未知（不知是否 OpenVPN）")
    if (nature in ("free", "paid") and form == "proxy"
            and exit_kind not in ("residential", "datacenter")):
        if has_isp:
            reasons.append("exit_kind: 出口 ISP 未命中归类表；可扩充归类表或人工改判")
        else:
            reasons.append("exit_kind: 无验真出口 ISP 数据（铁律：不许按名字/"
                           "来源/延迟猜）；验真后归位")
    if not reasons:                             # 防御位：不该出现
        reasons.append("维度组合未命中任何板块（不应出现，请报缺陷）")
    return reasons
