#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则集来源配置模型（core/ruleset_source.py）—— U3：拦截规则集来源登记
========================================================================
task18 · 阶段五·使用界面线 实现第四段。work10/DESIGN.md §8.2 定稿的
「主人核定制，客户端不内置来源」的落地：按 tag 登记来源条目
{tag, type: local|remote, path 或 url, update_interval}，**默认状态 =
空槽**（type=None、无 url、无路径指向真实文件——语义上仍是占位，§8.2）。
主人核定后经 approve_source 填入，填入即产出可删占位键的真条目
（真条目不含 prototype_placeholder 键=核定完成，§8.2 收口口径）。

依赖口径（与 usage_model 同族的独立纯度）：只用标准库，不 import client
包内任何模块——usage_model（task18 接线后）与 ruleset_cache 都可以安全
import 本模块，不产生循环（settings_proto → usage_model 链不受影响）。
tag 名等口径与 usage_model 的同名常量逐字一致，由口径一致测试组机械锁定
（work18/test_rulesets.py，client-dev 缺席自动跳过，task12 先例）。

不内置任何真实来源（§8.2 红线）——task35 有意解除并落地默认来源：主会话
给定的公开候选已逐个实测验活（HTTP GET → .srs 魔数判定，记录在
work35/source-verification.md），验活通过的槽位落 `DEFAULT_REMOTE_SOURCES`
内置默认源（验活才落；全灭的槽位如 malware-blockset 保持空槽，绝不拿
未验源凑数）。登记文件 ruleset_sources.json 语义 = **用户覆盖层**：
用户经 approve_source 核定的条目逐槽位覆盖默认值，没登记的槽位用默认
（有默认者）或空槽（无默认者）。url 校验只查形态（http/https + 主机
存在），不查内容、不访问网络。

文件层（形态同 site_blocklist.json / settings.json 家族口径）：
- 落点：数据目录 ruleset_sources.json（与 settings.json / site_blocklist.json
  同目录；DEFAULT_DATA_DIR 字面量与 settings_proto.DEFAULT_DATA_DIR 由测试
  锁定同族）；路径可注入以便测试。
- 包裹层：{"version": 1, "sources": {tag: 条目}}；只存**已核定**条目
  （空槽不落盘——文件缺席 = 内置默认来源；task35 起登记文件为用户
  覆盖层，未登记槽位按 default_sources 补默认或空槽）。
- 写入：原子写（临时文件 + os.replace，task12 §4.1 同款口径）。

损坏/缺失处置（site_blocklist.json 同款口径，报错不静默吞）：
- 文件不存在（首次运行）→ 全部空槽（零提示零动作，平滑起步）；
- JSON 损坏 / 包裹层结构非法 / version 异常 / 条目非法 / 未知 tag →
  如实报错（RulesetSourceError），绝不静默当空槽吞掉、不静默覆盖。

铁律对齐：本模块没有任何节点选择/切换/优选逻辑——来源条目只描述规则集
从哪来（local 文件 / remote URL），拦截只走 reject 语义（TAG_RULE_SEMANTICS
里的 route+direct-out 仅 cn-directset 直连分流用途，与 work10 §4.1 既有
口径一致，不含任何节点优选行为）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

# ---------------------------------------------------------------------------
# 常量（tag 名与 usage_model 同名常量逐字一致——测试机械锁定，集成时不得改名）
# ---------------------------------------------------------------------------

# 规则集 tag（work10 §8.1 固定名；cn-directset 为 rule 模式国内直连规则集，
# usage_model 既有常量——本段收口覆盖全部三个占位条目）
ADS_RULESET_TAG = "ads-blockset"
MALWARE_RULESET_TAG = "malware-blockset"
CN_DIRECT_RULESET_TAG = "cn-directset"
KNOWN_RULESET_TAGS = (ADS_RULESET_TAG, MALWARE_RULESET_TAG, CN_DIRECT_RULESET_TAG)

# 来源形态（work10 §8.1：v1 默认 local 离线可用；remote 为内核自动拉新）
SOURCE_LOCAL = "local"
SOURCE_REMOTE = "remote"
SOURCE_TYPES = (SOURCE_LOCAL, SOURCE_REMOTE)

# usage_model 既有 tag 同名常量（本模块不复制的两件：占位键与占位目录——
# 占位语义归 usage_model._placeholder_ruleset 原样保留，本模块不产占位条目）

# 远端条目 download_detour 指向的出站 tag（usage_model.OUT_TAG 同值；§8.1
# 口径：远端规则集经节点下载。核定为 download_detour 在内核 1.14 弃用、
# 1.16 移除——现行钉版 1.14.4 仍有效，迁移路径见 work18/DESIGN.md §13-E）
DOWNLOAD_DETOUR_TAG = "node-out"

# cn-directset 的路由语义（usage_model 既有 ⑤ 块口径原样：命中走直连，
# 不拦不做节点动作；direct-out 为 usage_model.DIRECT_OUT_TAG 同值）
DIRECT_OUT_TAG = "direct-out"

# 逐 tag 的规则语义（usage_model ③/④/⑤ 块既有形态，本段不换语义）
TAG_RULE_SEMANTICS = {
    ADS_RULESET_TAG: {"action": "reject"},
    MALWARE_RULESET_TAG: {"action": "reject"},
    CN_DIRECT_RULESET_TAG: {"action": "route", "outbound": DIRECT_OUT_TAG},
}

# 逐 tag 的功能名（uncovered 显式清单用，work10 §8 语义名）
TAG_FEATURE_LABELS = {
    ADS_RULESET_TAG: "广告拦截",
    MALWARE_RULESET_TAG: "恶意拦截",
    CN_DIRECT_RULESET_TAG: "国内直连规则集",
}

# 数据目录默认（settings_proto.DEFAULT_DATA_DIR 同族字面量；测试锁定一致）
DEFAULT_DATA_DIR = "~/.local/share/gate-client"

# 来源登记文件（与 settings.json / site_blocklist.json 同目录口径）
SOURCES_FILENAME = "ruleset_sources.json"
SOURCES_FILE_VERSION = 1
_WRAPPER_KEYS = frozenset({"version", "sources"})

# update_interval 形态（内核 duration：数字+单位组合，如 1d / 12h / 30m /
# 1h30m；单位只认 ms/s/m/h/d——内核文档口径，最终由 sing-box check 实证）
_DURATION_RE = re.compile(r"^(?:\d+(?:\.\d+)?(?:ms|s|m|h|d))+$")


class RulesetSourceError(ValueError):
    """来源登记非法（条目校验/文件层损坏/未知 tag）。报错不静默吞。"""


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
def _validate_update_interval(value) -> None:
    """update_interval 校验：None（=内核默认 1d / 客户端未定调度）或
    duration 字符串（数字+单位组合）。非法报错不静默吞。"""
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, str) or not value.strip():
        raise RulesetSourceError(
            f"update_interval 必须是 duration 字符串（如 1d/12h/30m/1h30m）"
            f"或 None（缺省=内核默认 1d），得到 {value!r}")
    if not _DURATION_RE.match(value.strip()):
        raise RulesetSourceError(
            f"update_interval 不是合法 duration（数字+单位组合，单位只认 "
            f"ms/s/m/h/d）: {value!r}")


def _validate_source_path(raw) -> str:
    """local 来源 path 校验 → 规范化绝对路径字符串。语义：主人指定的
    .srs 取材文件（客户端缓存由此重下载落盘，§8.3 宁旧勿缺）；不是
    rule_set 条目路径（那条指向缓存目录，ruleset_cache 口径）。
    相对路径拒收（相对路径落盘会变成 CWD 相关的静默漂移，报错不静默
    纠正）；「~」展开为绝对路径。"""
    if isinstance(raw, bool) or not isinstance(raw, str) or not raw.strip():
        raise RulesetSourceError(f"local 来源 path 必须是非空字符串，得到 {raw!r}")
    path = os.path.expanduser(raw.strip())
    if not os.path.isabs(path):
        raise RulesetSourceError(
            f"local 来源 path 必须是绝对路径（「~」可用，相对路径拒收——"
            f"相对路径的解析依赖进程 CWD）: {raw!r}")
    return os.path.abspath(path)


def _validate_source_url(raw) -> str:
    """remote 来源 url 校验 → 规范化字符串。只查形态（http/https scheme +
    主机存在 + 无空白），不查内容、不访问网络（§8.2：客户端不内置来源，
    URL 只由主人填入）。"""
    if isinstance(raw, bool) or not isinstance(raw, str) or not raw.strip():
        raise RulesetSourceError(f"remote 来源 url 必须是非空字符串，得到 {raw!r}")
    url = raw.strip()
    if any(ch.isspace() for ch in url):
        raise RulesetSourceError(f"remote 来源 url 不允许空白字符: {url!r}")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise RulesetSourceError(
            f"remote 来源 url 只允许 http/https scheme，得到 {parts.scheme!r}: {url!r}")
    if not parts.netloc:
        raise RulesetSourceError(f"remote 来源 url 缺主机: {url!r}")
    return url


def validate_source_entry(entry, *, filled_only: bool = True) -> dict:
    """来源条目校验 → 规范化 dict（tag/type/path/url/update_interval 五键）。

    filled_only=True（文件层/核定制入口口径）：type 必须是 local|remote
    （空槽 type=None 不落盘——文件缺席即默认状态）；local 必有 path、
    必无 url；remote 必有 url、必无 path（「path 或 url」二选一，§8.2）。
    filled_only=False 仅用于空槽形态的自检（type=None 且三字段全 None）。
    """
    if not isinstance(entry, dict):
        raise RulesetSourceError(f"来源条目必须是 dict，得到 {type(entry).__name__}")
    unknown = set(entry) - {"tag", "type", "path", "url", "update_interval"}
    if unknown:
        raise RulesetSourceError(
            f"来源条目含未知键（拒绝静默忽略）: {sorted(unknown)}")
    if "tag" not in entry:
        raise RulesetSourceError(f"来源条目缺 tag: {entry!r}")
    tag = entry["tag"]
    if tag not in KNOWN_RULESET_TAGS:
        raise RulesetSourceError(
            f"来源 tag 只认 usage_model 规则集常量 {'/'.join(KNOWN_RULESET_TAGS)}，"
            f"得到 {tag!r}（不许自创 tag）")
    source_type = entry.get("type")
    path = entry.get("path")
    url = entry.get("url")
    update_interval = entry.get("update_interval")

    if source_type is None and not filled_only:
        for name, value in (("path", path), ("url", url),
                            ("update_interval", update_interval)):
            if value is not None:
                raise RulesetSourceError(
                    f"空槽条目 {tag} 的 {name} 必须为 None（空槽=未核定，"
                    f"无路径指向真实文件），得到 {value!r}")
        return {"tag": tag, "type": None, "path": None, "url": None,
                "update_interval": None}

    if source_type not in SOURCE_TYPES:
        raise RulesetSourceError(
            f"来源 {tag} 的 type 只允许 {'/'.join(SOURCE_TYPES)}"
            f"（None=空槽，不落盘），得到 {source_type!r}")
    _validate_update_interval(update_interval)
    if source_type == SOURCE_LOCAL:
        if url is not None:
            raise RulesetSourceError(
                f"local 来源 {tag} 不允许 url（「path 或 url」二选一）: {url!r}")
        path = _validate_source_path(path)
        url = None
    else:
        if path is not None:
            raise RulesetSourceError(
                f"remote 来源 {tag} 不允许 path（「path 或 url」二选一）: {path!r}")
        url = _validate_source_url(url)
        path = None
    return {"tag": tag, "type": source_type, "path": path, "url": url,
            "update_interval": (update_interval.strip()
                                if isinstance(update_interval, str) else None)}


# ---------------------------------------------------------------------------
# 空槽形态（默认状态 = 全部空槽，语义上仍是占位，work10 §8.2）
# ---------------------------------------------------------------------------
def empty_source(tag: str) -> dict:
    """单个空槽条目：type=None、无 url、无路径指向真实文件。"""
    if tag not in KNOWN_RULESET_TAGS:
        raise RulesetSourceError(
            f"tag 只认 {'/'.join(KNOWN_RULESET_TAGS)}，得到 {tag!r}")
    return {"tag": tag, "type": None, "path": None, "url": None,
            "update_interval": None}


def empty_sources() -> dict:
    """全部已知 tag 的空槽登记表（默认状态）。"""
    return {tag: empty_source(tag) for tag in KNOWN_RULESET_TAGS}


# ---------------------------------------------------------------------------
# 内置默认来源（task35：主会话给定候选逐个验活后落地；验活记录 =
# work35/source-verification.md）
# ---------------------------------------------------------------------------
# 只收验活通过槽位：ads-blockset（geosite-category-ads-all.srs，HTTP 200
# + SRS 魔数）与 cn-directset（geosite-cn.srs，HTTP 200 + SRS 魔数——槽位
# 语义为 rule 模式国内域名直连分流，取域名库；geoip-cn 亦验活通过，留档
# 备选不落默认）。malware-blockset 两个候选全灭（404×2）→ 保持空槽，
# 绝不拿未验源凑数（宁缺勿假）。地址为公开规则集仓库的公开地址，可出现
# 在代码与文档（task35 简报口径）；规则集本体内容不落盘。
DEFAULT_REMOTE_SOURCES = {
    ADS_RULESET_TAG:
        "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/"
        "geosite-category-ads-all.srs",
    CN_DIRECT_RULESET_TAG:
        "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/"
        "geosite-cn.srs",
}


def default_sources() -> dict:
    """内置默认来源登记表（task35）：验活通过槽位 = remote 真条目
    （type=remote、url=验活地址、update_interval=None=内核默认 1d；
    download_detour 由 plan_ruleset_parts 既有口径自动加 node-out），
    未核定槽位（候选全灭者）= 空槽原样。

    消费口径：load_sources 以本表为底、用户登记条目逐槽位覆盖——登记
    文件是用户覆盖层，主人核定的条目永远赢，没登记的槽位用默认（有
    默认者）或空槽（无默认者）。每次返回全新 dict（调用方就地改不动
    内部常量）；纯数据函数，零网络、零文件 IO。"""
    entries = empty_sources()
    for tag, url in DEFAULT_REMOTE_SOURCES.items():
        entries[tag] = validate_source_entry({
            "tag": tag, "type": SOURCE_REMOTE, "url": url,
            "update_interval": None,
        })
    return entries


# ---------------------------------------------------------------------------
# 文件层（形态同 site_blocklist.json 家族：包裹层 + 原子写 + 如实报错）
# ---------------------------------------------------------------------------
def sources_path(data_dir=None) -> Path:
    """来源登记文件路径：显式 data_dir（覆盖目录）> 内置默认目录
    （DEFAULT_DATA_DIR，expanduser——与 settings.json / site_blocklist.json
    同目录口径）。路径可注入以便测试。"""
    if data_dir is not None:
        return Path(data_dir) / SOURCES_FILENAME
    return Path(DEFAULT_DATA_DIR).expanduser() / SOURCES_FILENAME


def load_sources(path=None) -> dict:
    """读来源登记 → 全量登记表（task35 起以内置默认来源为底：用户登记
    条目逐槽位覆盖默认值；文件缺席 = 内置默认来源）。

    登记文件语义 = 用户覆盖层（task35）：主人核定条目永远赢，没登记的
    槽位用默认来源（有默认者，见 default_sources）或空槽（无默认者，
    如 malware-blockset 候选全灭）。损坏/包裹层结构/version/条目非法/
    未知 tag 一律 RulesetSourceError 如实报错（不静默吞，site_blocklist
    同款口径）。
    """
    file_path = Path(path) if path is not None else sources_path()
    entries = default_sources()   # task35：登记表 = 用户覆盖层，默认为底
    entries.update(_load_stored_entries(file_path))
    return entries


def _load_stored_entries(file_path) -> dict:
    """读登记文件的**用户已核定条目**（纯覆盖层，不含内置默认；
    文件缺席 = 空 dict）。损坏/包裹层结构/version/条目非法/未知 tag
    一律 RulesetSourceError 如实报错（不静默吞）。

    approve_source 走本读取（而非 load_sources 合并视图）落盘——默认
    来源是代码内置事实，绝不借保存路径写进用户文件（否则默认值被冻结
    成「用户核定」，覆盖层语义失效）。"""
    if not file_path.exists():
        return {}
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            wrapper = json.load(f)
    except (OSError, ValueError) as exc:
        raise RulesetSourceError(
            f"规则集来源登记文件损坏（读取/JSON 解析失败）: {file_path}——{exc}。"
            "不静默当空槽吞掉：请修复该文件（条目形态见 work18/DESIGN.md）。") from exc
    if not isinstance(wrapper, dict):
        raise RulesetSourceError(
            f"规则集来源登记文件结构非法（顶层必须是 dict 包裹层）: {file_path}")
    unknown = set(wrapper) - _WRAPPER_KEYS
    if unknown:
        raise RulesetSourceError(
            f"规则集来源登记文件包裹层含未知键（拒绝静默忽略）: "
            f"{sorted(unknown)}——{file_path}")
    version = wrapper.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise RulesetSourceError(
            f"规则集来源登记文件 version 必须是整数，得到 {version!r}——{file_path}")
    if version != SOURCES_FILE_VERSION:
        raise RulesetSourceError(
            f"规则集来源登记文件 version={version} 与本版本支持的 "
            f"{SOURCES_FILE_VERSION} 不一致"
            + ("（由更新版本客户端写入，拒读防静默降级）"
               if version > SOURCES_FILE_VERSION
               else "（结构非定稿形态，拒读不猜）") + f"——{file_path}")
    stored = wrapper.get("sources")
    if not isinstance(stored, dict):
        raise RulesetSourceError(
            f"规则集来源登记文件 sources 必须是 dict，得到 {stored!r}——{file_path}")
    entries = {}
    for tag, entry in stored.items():
        if tag not in KNOWN_RULESET_TAGS:
            raise RulesetSourceError(
                f"来源登记文件含未知 tag {tag!r}（tag 只认 "
                f"{'/'.join(KNOWN_RULESET_TAGS)}）——{file_path}")
        if not isinstance(entry, dict):
            raise RulesetSourceError(
                f"来源登记条目 {tag} 必须是 dict，得到 {entry!r}——{file_path}")
        entries[tag] = validate_source_entry(dict(entry, tag=tag))
    return entries


def save_sources(entries: dict, path=None) -> Path:
    """登记表 → 落盘（只写已核定条目；原子写 tmp + os.replace）。

    entries 为 load_sources 形态（含空槽）：空槽跳过不落盘（文件缺席 =
    默认状态语义）。传入非法条目/未知 tag 一律报错，不静默修正。
    """
    file_path = Path(path) if path is not None else sources_path()
    stored = {}
    for tag, entry in entries.items():
        if tag not in KNOWN_RULESET_TAGS:
            raise RulesetSourceError(
                f"来源登记含未知 tag {tag!r}（tag 只认 "
                f"{'/'.join(KNOWN_RULESET_TAGS)}）")
        if entry.get("type") is None:
            validate_source_entry(dict(entry), filled_only=False)
            continue                            # 空槽不落盘
        stored[tag] = validate_source_entry(dict(entry))
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=file_path.name + ".",
                                        suffix=".tmp",
                                        dir=str(file_path.parent))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"version": SOURCES_FILE_VERSION, "sources": stored},
                      f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_name, file_path)
    except BaseException:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)           # 失败清临时文件，原文件未被动
            except OSError:
                pass
        raise
    return file_path


def approve_source(tag: str, *, source_type: str, path=None, url=None,
                   update_interval=None, data_dir=None) -> dict:
    """「主人核定后填入」的唯一入口（work10 §8.2 核定制收口）。

    核定一个 tag 的来源并原子落盘：local 给 path（主人指定的 .srs 取材
    文件），remote 给 url（http/https；形态校验，不访问网络）。
    update_interval 可选 duration（remote=内核拉新周期，缺省内核 1d；
    local=客户端重下载周期登记，调度接线属后续段位）。

    返回规范化条目（五键）。该 tag 自此视为已核定：配置收口
    （usage_model 接线后的 plan_ruleset_parts）据其产出**不带
    prototype_placeholder 键**的真条目——「删占位键即视为核定完成」
    （work10 §8.2）由此机械达成，无需任何手工删键步骤。

    task35 覆盖层口径：落盘读写走 _load_stored_entries（纯用户层）——
    内置默认来源不借保存路径写进用户文件，登记文件只存用户核定条目。

    data_dir 语义同 sources_path（覆盖目录注入以便测试）。
    """
    entry = validate_source_entry({
        "tag": tag, "type": source_type, "path": path, "url": url,
        "update_interval": update_interval,
    })
    entries = _load_stored_entries(sources_path(data_dir))
    entries[tag] = entry
    save_sources(entries, sources_path(data_dir))
    return entry


# ---------------------------------------------------------------------------
# 状态合并与配置收口计划（纯数据，无 IO——供 usage_model 接线与测试直用）
# ---------------------------------------------------------------------------
def resolve_ruleset_state(sources: dict, cache_status: dict) -> dict:
    """来源登记 × 缓存状态 → 逐 tag 收口状态（plan_ruleset_parts 的输入）。

    sources：load_sources 形态（全 tag，含空槽）；cache_status：
    ruleset_cache.cache_status 形态（tag → {path, present, ...}，缺席的
    tag 视为无缓存）。输出逐 tag：{tag, approved, type, path, url,
    update_interval, cache_file, cache_present}。纯函数，不改入参。
    """
    state = {}
    for tag in KNOWN_RULESET_TAGS:
        source = sources.get(tag) or empty_source(tag)
        status = cache_status.get(tag) or {}
        state[tag] = {
            "tag": tag,
            "approved": source.get("type") in SOURCE_TYPES,
            "type": source.get("type"),
            "path": source.get("path"),
            "url": source.get("url"),
            "update_interval": source.get("update_interval"),
            "cache_file": status.get("path"),
            "cache_present": bool(status.get("present")),
        }
    return state


def plan_ruleset_parts(active: dict, ruleset_state) -> dict:
    """开关/模式激活的规则集 tag → 片段产出计划（配置收口唯一逻辑，
    work18/DESIGN.md 改动点 2 的接线目标）。

    active：{tag: bool}——ads/malware 由开关驱动、cn-directset 由 rule
    模式驱动（usage_model ③/④/⑤ 既有激活条件，本函数不另造）。
    ruleset_state：resolve_ruleset_state 产物；**None = 未接线/原型口径**
    → 逐 tag 产出带 prototype_placeholder 键的占位条目（PLACEHOLDER_KEY/
    占位假路径归 usage_model 既有常量与 _placeholder_ruleset，本函数返回
    占位引用由 usage_model 接线块用其既有 _placeholder_ruleset 落地——
    保持 0.14.0 片段层占位口径原样）。

    四种组合（work18 简报验收口径）：
    - 开关关 → 该 tag 零产出（现状）；
    - 开 + 来源已核定 remote → remote 真条目（无占位键；字段形状按
      work18/DESIGN.md §13-E 核实结果）；
    - 开 + 来源已核定 local + 缓存文件在 → local 真条目指向真实缓存路径
      （无占位键）；
    - 开 + 既无缓存文件又无核定来源 → **零条目零规则** + uncovered 显式
      记录（绝不生成指向不存在文件的条目假装拦截已生效）。

    返回 {"rules", "rule_set", "uncovered", "notes"} 四个列表（纯函数，
    不改入参）。active 含未知 tag 一律报错（契约破坏不静默吞）。
    """
    unknown = set(active) - set(KNOWN_RULESET_TAGS)
    if unknown:
        raise RulesetSourceError(
            f"plan_ruleset_parts 的 active 含未知 tag（只认 "
            f"{'/'.join(KNOWN_RULESET_TAGS)}）: {sorted(unknown)}")
    rules, rule_set, uncovered, notes = [], [], [], []
    for tag in KNOWN_RULESET_TAGS:
        if not active.get(tag):
            continue                          # 开关关 → 什么都不产出（现状）
        if ruleset_state is None:
            # 未接线/原型口径：占位条目由 usage_model 既有 _placeholder_ruleset
            # 落地（usage_model 接线块负责），本函数只声明需要占位。
            rules.append(dict(TAG_RULE_SEMANTICS[tag], rule_set=[tag]))
            rule_set.append({"__placeholder__": tag})
            continue
        state = ruleset_state.get(tag)
        if state is None:
            raise RulesetSourceError(
                f"ruleset_state 已提供但缺 {tag} 的状态（resolve_ruleset_state "
                f"应产出全部已知 tag）——契约破坏，拒绝猜测")
        if not state.get("approved"):
            uncovered.append({
                "feature": f"{TAG_FEATURE_LABELS[tag]}（{tag}）",
                "belongs": "core/ruleset_source.py（来源核定）+ "
                           "core/ruleset_cache.py（缓存落盘）",
                "why": "开关已开，但来源未核定（空槽）且本片段不生成该规则集"
                       "条目——绝不假装拦截已生效；先在来源登记里核定来源"
                       "（approve_source）并备好缓存，下次连接生效",
            })
            notes.append(
                f"{TAG_FEATURE_LABELS[tag]}开关已开：{tag} 来源未核定（空槽），"
                "本次连接该规则集不生成、不生效（uncovered 在案）")
            continue
        if state.get("type") == SOURCE_REMOTE:
            entry = {
                "type": SOURCE_REMOTE,
                "tag": tag,
                "format": "binary",
                "url": state.get("url"),
                # §13-E 核实结果（work18/DESIGN.md）：钉版 1.14.4 口径——
                # download_detour 经节点下载（1.14 弃用未移除）；缺省
                # update_interval = 内核默认 1d。字段迁移（内核 ≥1.16 换
                # http_client）见 DESIGN.md §13-E，本处不预写。
                "download_detour": DOWNLOAD_DETOUR_TAG,
            }
            if state.get("update_interval"):
                entry["update_interval"] = state["update_interval"]
            rules.append(dict(TAG_RULE_SEMANTICS[tag], rule_set=[tag]))
            rule_set.append(entry)
            continue
        # local 来源：缓存文件在 → 真条目；不在 → uncovered（宁旧勿缺的
        # 反面同样成立：宁缺勿假——没有文件绝不假装拦截）
        if state.get("cache_present") and state.get("cache_file"):
            entry = {
                "type": SOURCE_LOCAL,
                "tag": tag,
                "format": "binary",
                "path": state["cache_file"],
            }
            rules.append(dict(TAG_RULE_SEMANTICS[tag], rule_set=[tag]))
            rule_set.append(entry)
        else:
            uncovered.append({
                "feature": f"{TAG_FEATURE_LABELS[tag]}（{tag}）",
                "belongs": "core/ruleset_cache.py（缓存落盘）",
                "why": "来源已核定（local）但缓存文件缺失，本片段不生成该"
                       "规则集条目——绝不生成指向不存在文件的条目假装拦截"
                       "已生效；先执行「立即更新」落缓存，下次连接生效",
            })
            notes.append(
                f"{TAG_FEATURE_LABELS[tag]}来源已核定但缓存缺失：{tag} 本次"
                "连接不生成、不生效（先「立即更新」落缓存）")
    return {"rules": rules, "rule_set": rule_set,
            "uncovered": uncovered, "notes": notes}
