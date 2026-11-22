#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
特殊站清单文件层（core/special_sites.py）—— U5：special_sites.json 落地
========================================================================
task20 · 阶段五·使用界面线 第六段（U5 特殊网站测试）。纯标准库；
不依赖 PySide6、不碰网络。同包 settings_store 只在「未显式给 data_dir
的默认目录推导」时懒加载（speedtest 同款纪律，独立导入本模块可加载）。

职责（简报交付物 1）：special_sites.json 的 load/save/校验 + 清单条目
的增删改。文件形态与损坏/缺失处置口径沿用 site_blocklist.py（work17
§4）同族做法——同目录、同包裹层、同原子写；路径解析与
site_blocklist.blocklist_path 同一族（settings_store.settings_path 的
目录推导），且 load/save 的 path 参数可注入以便离线测试。

文件形态（work10/DESIGN.md §9.1 定稿）：
- 落点：数据目录（与 settings.json / site_blocklist.json 同目录口径），
  文件名 special_sites.json。
- 条目四字段：{"name", "domain", "category", "enabled"}——只存类别与
  公开域名；**永不存账号、路径级探测点、私有站**（红线）。探测目标
  一律是域名根路径（https://<域名>/），路径级东西在清单层就不给进。
- 包裹层：{"version": 1, "entries": [...]}（与 site_blocklist.json
  / settings.json 同款包裹层口径）；version 高于/低于支持值一律拒读
  （防新旧版本静默降级/猜结构，同 task12 §4.2 口径）。
- 写入：校验通过后按四字段键序规范化，原子写（同目录 tmp +
  os.replace，task12 §4.1 口径）。

损坏/缺失处置（与 site_blocklist 同立场）：
- 文件不存在（首次运行）→ 空清单（不报错、**不自动编造默认站点**——
  真实清单由主人在界面里维护，属数据不属代码，代码与测试只用假域名
  示例如 example.com）；
- JSON 损坏 / 包裹层非法 / version 异常 / 条目非法 / 同域名重复 →
  如实报错（SpecialSitesError），绝不静默当空名单吞掉、不静默覆盖。

边界（work10 §2.3 / §9.1）：⑥区是消费侧测试工具的增删入口，不是来源
管理；本模块是清单的唯一读写口径，后台诊断读它只读不写。

铁律对齐：本模块没有任何节点选择/切换/优选逻辑，清单条目是网站域名，
不涉节点；连接永远由用户点。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

# 文件名与包裹层版本（形态定稿见 work10 §9.1；version 语义同 task12 §4.2）
SPECIAL_SITES_FILENAME = "special_sites.json"
SPECIAL_SITES_FILE_VERSION = 1

_WRAPPER_KEYS = frozenset({"version", "entries"})
_ENTRY_KEYS = frozenset({"name", "domain", "category", "enabled"})

_NAME_MAX = 64          # 条目显示名长度上限（防清单里塞长文）
_CATEGORY_MAX = 32      # 类别标签长度上限（如 ai / shop / news）

_DOMAIN_LABEL = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
# 裸域名形态：点分标签 ≥2 段（要有公开域名样子；单标签如 localhost 不收）
_DOMAIN_RE = re.compile(rf"{_DOMAIN_LABEL}(?:\.{_DOMAIN_LABEL})+\Z")


class SpecialSitesError(ValueError):
    """清单文件层非法（损坏/包裹层/version 异常/条目非法）。如实报错，
    不静默吞、不静默用默认清单覆盖。"""


def special_sites_path(data_dir=None) -> Path:
    """清单文件路径：显式 data_dir > 设置存储的内置默认目录（与
    settings.json / site_blocklist.json 同目录口径，路径从设置存储取，
    不写死）。

    data_dir 语义同 settings_store.settings_path 的显式覆盖（显式给
    data_dir = data_dir/special_sites.json，与 blocklist_path(data_dir)
    的推导结果一致）。settings_store 懒加载（speedtest.group_nodes_by_board
    同款纪律）：独立导入本模块（离线测试）时不带同包兄弟也能载入，
    此时不许自造默认目录——要么显式传 data_dir，要么显式传完整路径。
    """
    if data_dir is not None:
        return Path(data_dir) / SPECIAL_SITES_FILENAME
    from . import settings_store
    return settings_store.settings_path().parent / SPECIAL_SITES_FILENAME


# ---------------------------------------------------------------------------
# 条目校验（四字段；domain 只收裸域名，URL/路径/端口/userinfo 一律拒收）
# ---------------------------------------------------------------------------
def _validate_name(name) -> str:
    if not isinstance(name, str):
        raise SpecialSitesError(f"条目 name 必须是字符串，得到 {type(name).__name__}")
    name = name.strip()
    if not name:
        raise SpecialSitesError("条目 name 不能为空")
    if len(name) > _NAME_MAX:
        raise SpecialSitesError(f"条目 name 过长（>{_NAME_MAX} 字符）：{name[:20]}…")
    return name


def _validate_domain(domain) -> str:
    """域名形态校验：只要裸域名，不要 URL / 路径 / 端口 / userinfo。
    逐项报错（简报口径：坏 domain 逐项报错），最后正则收口。"""
    if not isinstance(domain, str):
        raise SpecialSitesError(f"条目 domain 必须是字符串，得到 {type(domain).__name__}")
    domain = domain.strip().lower()
    if not domain:
        raise SpecialSitesError("条目 domain 不能为空")
    if "://" in domain:
        raise SpecialSitesError(f"条目 domain 只要裸域名，不要带协议头（URL）：{domain}")
    if any(c.isspace() for c in domain):
        raise SpecialSitesError(f"条目 domain 不能含空白字符：{domain!r}")
    if "/" in domain or "?" in domain or "#" in domain:
        raise SpecialSitesError(
            f"条目 domain 只要裸域名，不要带路径/查询/锚点（路径级探测点永不入清单）：{domain}")
    if "@" in domain:
        raise SpecialSitesError(f"条目 domain 不要带 userinfo（@）：{domain}")
    if ":" in domain:
        raise SpecialSitesError(f"条目 domain 只要裸域名，不要带端口（:）：{domain}")
    if "*" in domain:
        raise SpecialSitesError(f"条目 domain 不收通配符（*）：{domain}")
    if not _DOMAIN_RE.match(domain):
        raise SpecialSitesError(
            f"条目 domain 形态非法（要点分裸域名、≥2 段、标签内只用"
            f"字母/数字/连字符）：{domain}")
    return domain


def _validate_category(category) -> str:
    if category is None:
        return ""                            # 类别可空（存空串，字段仍在）
    if not isinstance(category, str):
        raise SpecialSitesError(f"条目 category 必须是字符串，得到 {type(category).__name__}")
    category = category.strip()
    if len(category) > _CATEGORY_MAX:
        raise SpecialSitesError(f"条目 category 过长（>{_CATEGORY_MAX} 字符）")
    return category


def _validate_enabled(enabled) -> bool:
    if isinstance(enabled, bool):
        return enabled
    raise SpecialSitesError(f"条目 enabled 必须是布尔值，得到 {enabled!r}")


def validate_entry(entry) -> dict:
    """校验并规范化单个条目（四字段键序固定，保证 save(load(p)) 字节级一致）。
    输入可为 dict；其余形态报错。"""
    if not isinstance(entry, dict):
        raise SpecialSitesError(
            f"条目必须是 dict（四字段），得到 {type(entry).__name__}")
    unknown = set(entry) - _ENTRY_KEYS
    if unknown:
        raise SpecialSitesError(f"条目含未知键（拒绝静默忽略）: {sorted(unknown)}")
    missing = _ENTRY_KEYS - set(entry)
    if missing:
        raise SpecialSitesError(f"条目缺字段: {sorted(missing)}（四字段齐备）")
    return {
        "name": _validate_name(entry["name"]),
        "domain": _validate_domain(entry["domain"]),
        "category": _validate_category(entry["category"]),
        "enabled": _validate_enabled(entry["enabled"]),
    }


def _validate_entries(entries) -> list:
    """全清单校验 + 规范化；同域名重复如实报错（清单条目按域名唯一）。"""
    if not isinstance(entries, (list, tuple)):
        raise SpecialSitesError(
            f"entries 必须是列表，得到 {type(entries).__name__}")
    normalized = [validate_entry(e) for e in entries]
    seen = {}
    for e in normalized:
        if e["domain"] in seen:
            raise SpecialSitesError(
                f"同域名条目重复：{e['domain']}（清单按域名唯一）")
        seen[e["domain"]] = e
    return normalized


# ---------------------------------------------------------------------------
# 文件层：load / save（原子写；缺文件=空清单，不编造默认站点）
# ---------------------------------------------------------------------------
def load_sites(path=None) -> tuple:
    """读清单 → 规范化条目元组（保序）。

    - 文件不存在 → ()（默认空清单，首次运行平滑；不报错、不自动编造
      默认站点——真实清单由主人在界面里维护）；
    - 损坏 / 包裹层非法 / version 异常 / 条目非法 / 同域名重复 →
      SpecialSitesError（如实报错，绝不静默当空清单吞）。
    """
    path = Path(path) if path is not None else special_sites_path()
    if not path.exists():
        return ()
    try:
        with open(path, "r", encoding="utf-8") as f:
            wrapper = json.load(f)
    except (OSError, ValueError) as exc:
        raise SpecialSitesError(
            f"特殊站清单文件损坏（读取/JSON 解析失败）: {path}——{exc}。"
            "清单未被消费，请修复或删除该文件后重试；本层不会静默当空清单。") from exc
    if not isinstance(wrapper, dict):
        raise SpecialSitesError(
            f"特殊站清单文件结构非法（顶层必须是 dict 包裹层）: {path}")
    unknown = set(wrapper) - _WRAPPER_KEYS
    if unknown:
        raise SpecialSitesError(
            f"特殊站清单文件包裹层含未知键（拒绝静默忽略）: {sorted(unknown)}——{path}")
    version = wrapper.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise SpecialSitesError(
            f"特殊站清单文件 version 必须是整数，得到 {version!r}——{path}")
    if version != SPECIAL_SITES_FILE_VERSION:
        raise SpecialSitesError(
            f"特殊站清单文件 version={version} 与本版本支持的 "
            f"{SPECIAL_SITES_FILE_VERSION} 不一致（拒读不猜，防静默降级）——{path}")
    entries = wrapper.get("entries")
    if not isinstance(entries, list):
        raise SpecialSitesError(
            f"特殊站清单文件 entries 必须是列表，得到 {type(entries).__name__}——{path}")
    return tuple(_validate_entries(entries))


def save_sites(entries, path=None) -> Path:
    """校验 + 规范化 + 原子写全量清单，返回写入路径。

    - 校验不过（坏 domain / 同域名重复 / 字段非法）原样上抛
      SpecialSitesError，不落盘；
    - 原子写：同目录 tmp 文件 + os.replace（同目录保证同分区）。
    """
    path = Path(path) if path is not None else special_sites_path()
    normalized = _validate_entries(entries)
    wrapper = {"version": SPECIAL_SITES_FILE_VERSION, "entries": normalized}
    payload = json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n"
    directory = path.parent
    if not directory.is_dir():
        directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".special_sites-", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, path)             # 原子替换
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path


# ---------------------------------------------------------------------------
# 清单增删改（纯函数：吃条目序列、吐新条目元组，不改入参——GUI 层改完
# 再 save_sites 落盘；校验同一份 validate_entry，不另造第二套规则）
# ---------------------------------------------------------------------------
def find_site(entries, domain) -> dict | None:
    """按域名找条目（域名大小写不敏感，规范化后匹配）；没有返回 None。"""
    d = str(domain or "").strip().lower()
    for e in entries:
        if e["domain"] == d:
            return e
    return None


def add_site(entries, name, domain, category="", enabled=True) -> tuple:
    """新增条目（同域名重复报错）。返回新清单（不改入参）。"""
    entry = validate_entry({"name": name, "domain": domain,
                            "category": category, "enabled": enabled})
    if find_site(entries, entry["domain"]) is not None:
        raise SpecialSitesError(f"清单里已有该域名：{entry['domain']}")
    return tuple(entries) + (entry,)


def remove_site(entries, domain) -> tuple:
    """按域名移除条目；域名不在清单里如实报错（不静默当成功）。"""
    d = str(domain or "").strip().lower()
    kept = tuple(e for e in entries if e["domain"] != d)
    if len(kept) == len(tuple(entries)):
        raise SpecialSitesError(f"清单里没有该域名：{d}")
    return kept


def set_enabled(entries, domain, enabled) -> tuple:
    """改某条目的 enabled 开关（域名不在清单里如实报错）。"""
    enabled = _validate_enabled(enabled)
    d = str(domain or "").strip().lower()
    if find_site(entries, d) is None:
        raise SpecialSitesError(f"清单里没有该域名：{d}")
    return tuple(
        {**e, "enabled": enabled} if e["domain"] == d else e for e in entries)


def update_site(entries, domain, *, name=None, category=None,
                enabled=None) -> tuple:
    """改某条目的显示名/类别/开关（域名是条目身份，不改；要改就删了重加）。
    只覆盖显式给出的字段，None = 不动。"""
    d = str(domain or "").strip().lower()
    if find_site(entries, d) is None:
        raise SpecialSitesError(f"清单里没有该域名：{d}")
    out = []
    for e in entries:
        if e["domain"] != d:
            out.append(e)
            continue
        patched = dict(e)
        if name is not None:
            patched["name"] = _validate_name(name)
        if category is not None:
            patched["category"] = _validate_category(category)
        if enabled is not None:
            patched["enabled"] = _validate_enabled(enabled)
        out.append(validate_entry(patched))
    return tuple(out)


def enabled_sites(entries) -> tuple:
    """当前 enabled=True 的条目（②区列渲染与测试触发只消费这些）。"""
    return tuple(e for e in entries if e["enabled"])
