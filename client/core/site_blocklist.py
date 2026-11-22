#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
网站黑名单文件层（core/site_blocklist.py）—— U2：site_blocklist.json 落地
========================================================================
task17 · 阶段五·使用界面线 实现第二段。纯标准库 + 同包 usage_model /
settings_store；不依赖 PySide6、不碰网络。

职责（简报交付物 3）：site_blocklist.json 的 load/save/校验——文件形态
定稿与损坏/缺失处置口径见 work17/DESIGN.md §4。条目的域名校验、两类
处置（reject / direct）、同域名双处置冲突检测一律沿用
usage_model.parse_site_blocklist（0.14.0 既定口径，单一事实源，本模块
不另造第二套校验）；「reject 条目全模式生效、direct 条目仅 rule 模式
生成」是 build_route_config 的消费语义，本文件层不生成任何路由。

文件形态（定稿，work17/DESIGN.md §4）：
- 落点：数据目录（与 settings.json 同目录口径），文件名
  site_blocklist.json；路径经 settings_store.settings_path() 的目录
  推导（路径从设置存储取，不写死）。
- 包裹层：{"version": 1, "entries": [...]}——与 settings.json 同款
  包裹层口径（task12 §4.1）；entries 为条目列表，条目=裸域名 str
  （默认处置 reject）或 {"domain":…, "action":"reject"|"direct"} dict。
- 写入：parse 校验通过后规范化为 dict 条目，原子写（tmp + os.replace，
  task12 §4.1 / work10 §4.2 同款口径）。

损坏/缺失处置（简报定稿：默认空名单 + 如实报错，不静默吞）：
- 文件不存在（首次运行）→ 空名单（零提示零动作，平滑起步）；
- JSON 损坏 / 包裹层结构非法 / version 高于支持 / 条目非法 / 同域名
  双处置 → 如实报错（SiteBlocklistError 或 usage_model.UsageSettingsError
  上抛），绝不静默当空名单吞掉、不静默覆盖原始证据。

边界（work10 §2.3）：名单编辑 UI 归后台，使用界面只读消费；本模块是
两侧共用的唯一读写口径。规则集真条目来源归 U3 且待主人核定——与本
名单无关（这是用户钦点名单，不是规则集）。

铁律对齐：本模块没有任何节点选择/切换/优选逻辑；名单条目是网站域名，
不涉节点，连接永远由用户点。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from . import settings_store
from .usage_model import parse_site_blocklist

# 文件名与包裹层版本（形态定稿见 work17/DESIGN.md §4；version 语义同
# task12 §4.2：高于支持值拒读，防新版本文件被旧版静默降级）
BLOCKLIST_FILENAME = "site_blocklist.json"
BLOCKLIST_FILE_VERSION = 1

_WRAPPER_KEYS = frozenset({"version", "entries"})


class SiteBlocklistError(ValueError):
    """名单文件层非法（损坏/包裹层结构/version 异常）。如实报错不静默吞。
    条目级校验错误沿用 usage_model.UsageSettingsError（同属 ValueError 族，
    消费方可统一按 ValueError 捕获后取各自类型区分呈现）。"""


def blocklist_path(data_dir=None):
    """名单文件路径：显式 data_dir > 设置存储的内置默认目录（与
    settings.json 同目录口径，路径从设置存储取，不写死）。

    data_dir 语义同 settings_store.settings_path 的显式覆盖：--db /
    GATE_CLIENT_DB 显式指定时的覆盖目录由调用方解析后传入（task12
    §4.1「显式覆盖时设置文件跟覆盖目录走」同款）；None = 内置默认目录。
    """
    return settings_store.settings_path(data_dir).parent / BLOCKLIST_FILENAME


def _read_wrapper(path) -> dict:
    """读文件 + 包裹层校验（损坏/结构/version 异常如实报错）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            wrapper = json.load(f)
    except (OSError, ValueError) as exc:
        raise SiteBlocklistError(
            f"网站黑名单文件损坏（读取/JSON 解析失败）: {path}——{exc}。"
            "名单未被消费，请修复或删除该文件后重试；本层不会静默当空名单。") from exc
    if not isinstance(wrapper, dict):
        raise SiteBlocklistError(
            f"网站黑名单文件结构非法（顶层必须是 dict 包裹层）: {path}")
    unknown = set(wrapper) - _WRAPPER_KEYS
    if unknown:
        raise SiteBlocklistError(
            f"网站黑名单文件包裹层含未知键（拒绝静默忽略）: {sorted(unknown)}——{path}")
    version = wrapper.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise SiteBlocklistError(
            f"网站黑名单文件 version 必须是整数，得到 {version!r}——{path}")
    if version > BLOCKLIST_FILE_VERSION:
        raise SiteBlocklistError(
            f"网站黑名单文件 version={version} 高于本版本支持的 "
            f"{BLOCKLIST_FILE_VERSION}（防新版本文件被旧版静默降级，拒读）——{path}")
    if version < BLOCKLIST_FILE_VERSION:
        raise SiteBlocklistError(
            f"网站黑名单文件 version={version} 低于本版本支持的 "
            f"{BLOCKLIST_FILE_VERSION}（结构非定稿形态，拒读不猜）——{path}")
    return wrapper


def load_blocklist(path=None) -> tuple:
    """读名单 → 规范化条目元组（[{"domain":…, "action":…}, …]，保序去重）。

    - 文件不存在 → ()（默认空名单，首次运行平滑）；
    - 条目校验/双处置冲突 → usage_model.parse_site_blocklist 原样上抛
      （UsageSettingsError，0.14.0 口径：歧义不静默吞）；
    - 其余文件层异常 → SiteBlocklistError。
    返回值可直接传 build_route_config(model, site_blocklist=…)（dict 条目
    是其既定入参形态）。
    """
    path = Path(path) if path is not None else blocklist_path()
    if not path.exists():
        return ()
    wrapper = _read_wrapper(path)
    entries = wrapper.get("entries")
    if not isinstance(entries, list):
        raise SiteBlocklistError(
            f"网站黑名单文件 entries 必须是列表，得到 {type(entries).__name__}——{path}")
    pairs = parse_site_blocklist(entries)          # 条目校验 + 双处置冲突报错
    return tuple({"domain": d, "action": a} for d, a in pairs)


def save_blocklist(entries, path=None) -> Path:
    """校验 + 规范化 + 原子写全量名单，返回写入路径。

    - entries：str / dict 条目序列（同 build_route_config 入参形态）；
      校验不过（含同域名双处置）原样上抛 UsageSettingsError，不落盘；
    - 规范化：str 条目补 action=reject；统一 {"domain","action"} 键序，
      保证 save(load(p)) 字节级一致（往返无损可实测）；
    - 原子写：同目录 tmp 文件 + os.replace（task12 §4.1 口径）。
    """
    path = Path(path) if path is not None else blocklist_path()
    pairs = parse_site_blocklist(entries)
    normalized = [{"domain": d, "action": a} for d, a in pairs]
    wrapper = {"version": BLOCKLIST_FILE_VERSION, "entries": normalized}
    payload = json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n"
    directory = path.parent
    if not directory.is_dir():
        directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".site_blocklist-", dir=str(directory))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp_name, path)                 # 原子替换（同目录保证同分区）
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return path
