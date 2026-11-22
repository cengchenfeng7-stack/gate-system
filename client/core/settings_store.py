#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一设置存储（core/settings_store.py）—— task12 §4 持久化口径落地
========================================================================
task17 · 阶段五·使用界面线 实现第二段。纯标准库 + 同包 settings_proto；
不依赖 PySide6、不碰网络、没有任何节点语义。

职责（简报交付物 4 的接线前提）：settings_proto.Settings（唯一设置模型，
work10 §11 口径）的落盘读写。③规则与网络区（work17 usage_rules_page.py）
与后续设置页（task12 集成段）经本模块读写**同一个** settings.json——
值只有一份、存储只有一处，不出现两个可编辑副本。

开工核对结论（如实记录，简报口径「不许自造平行存储绕过去」）：
client-dev 0.20.0 里 task12 的 Settings 模型已在（core/settings_proto.py），
但其 DESIGN §4 的持久化一环标注「原型不实现 IO，集成段落地」，全库
（除测试夹具提及文件名外）没有 settings.json 读写代码——正是简报预判
的「Settings 落盘接线尚缺的一环」。本模块按 task12 DESIGN §4.1–§4.3
定稿口径补上该环：文件/目录/包裹层/原子写/损坏处置逐条照 §4，未引入
任何新口径；若与 task12 后续集成实现冲突，以 settings_proto 模型与
task12 DESIGN 为准（差异报 DONE.md 等裁决，不悄悄分叉）。

持久化口径（task12 §4，出处随注）：
- 文件：<数据目录>/settings.json——与 client.db 同目录，不另起目录
  （§4.1）；设置文件固定在内置默认目录、不跟随 data_dir 设置（否则
  「设置说设置在哪」自举成环）；显式覆盖（--db / GATE_CLIENT_DB）时
  跟覆盖目录走——覆盖目录由调用方解析后经 settings_path(data_dir)
  显式传入，优先级 --db > GATE_CLIENT_DB > settings.data_dir > 内置默认
  是消费方的解析责任（§4.1/§8-3），本层不猜。
- 格式：包裹层 {"version": 1, "settings": Settings.to_dict()}（§4.1）。
- 写入：原子写（tmp 文件 + os.replace），每次全量写（§4.1）。
- 读不到/损坏/缺字段（§4.2，逐情形）：
  · 文件不存在（旧版/首次运行）→ 全默认值起，零提示零动作（§4.3）；
  · JSON 损坏 → SettingsStoreError 如实报错，**不用默认值盖掉**——
    呈现层据此弹窗给路径 + 重置入口；重置走 reset_settings()（先把
    损坏文件改名留档再写默认，不删原始证据）；
  · version 高于支持 → 拒读报错（防新版本文件被旧版静默降级）；
  · settings 缺字段 → Settings.from_dict 补默认值，不报错；
  · settings 未知键 / 字段值非法 → settings_proto.SettingsError 原样
    上抛（呈现层给重置入口），不静默丢弃。
- QSettings 迁移（auto_probe 两键首启导入，§4.3）：属界面层动作、依赖
  Qt，归 task12 设置页集成段，本层不做（core 层保持 Qt-free）；设置页
  接线时在首次初始化处调本层写入即可。

铁律对齐：本模块没有任何节点选择/切换/优选逻辑，不持有任何节点字段；
它只存用户显式改过的偏好。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from .settings_proto import (
    DEFAULT_SETTINGS_FILE,
    SETTINGS_FILE_VERSION,
    Settings,
    SettingsError,
)

_WRAPPER_KEYS = frozenset({"version", "settings"})


class SettingsStoreError(ValueError):
    """设置文件层非法（损坏/包裹层结构/version 异常）。如实报错不静默吞，
    呈现层据此给文件路径与重置入口（task12 §4.2）。
    字段级校验错误沿用 settings_proto.SettingsError（同属 ValueError 族）。"""


def settings_path(data_dir=None) -> Path:
    """设置文件路径：显式 data_dir（覆盖目录）> 内置默认
    （settings_proto.DEFAULT_SETTINGS_FILE，expanduser）。

    data_dir = --db / GATE_CLIENT_DB 显式指定时解析出的数据目录（task12
    §4.1「显式覆盖时设置文件跟覆盖目录走」）；None = 内置默认目录。
    不读 settings.data_dir 字段自举（§4.1：设置文件不跟随该设置）。
    """
    if data_dir is not None:
        return Path(data_dir) / "settings.json"
    return Path(DEFAULT_SETTINGS_FILE).expanduser()


def _read_wrapper(path) -> dict:
    """读文件 + 包裹层校验（损坏如实报错，不用默认值盖掉——§4.2）。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            wrapper = json.load(f)
    except (OSError, ValueError) as exc:
        raise SettingsStoreError(
            f"设置文件损坏（读取/JSON 解析失败）: {path}——{exc}。"
            "按 task12 §4.2 不用默认值盖掉：请修复、或在界面用「重置为默认」"
            "（先改名留档再写默认）。") from exc
    if not isinstance(wrapper, dict):
        raise SettingsStoreError(
            f"设置文件结构非法（顶层必须是 dict 包裹层）: {path}")
    unknown = set(wrapper) - _WRAPPER_KEYS
    if unknown:
        raise SettingsStoreError(
            f"设置文件包裹层含未知键（拒绝静默忽略）: {sorted(unknown)}——{path}")
    version = wrapper.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise SettingsStoreError(
            f"设置文件 version 必须是整数，得到 {version!r}——{path}")
    if version != SETTINGS_FILE_VERSION:
        raise SettingsStoreError(
            f"设置文件 version={version} 与本版本支持的 "
            f"{SETTINGS_FILE_VERSION} 不一致"
            + ("（由更新版本客户端写入，拒读防静默降级）" if version > SETTINGS_FILE_VERSION
               else "（结构非定稿形态，拒读不猜）")
            + f"——{path}")
    return wrapper


def load_settings(path=None) -> Settings:
    """读设置 → Settings 实例（task12 §4.2 逐情形处置）。

    - 文件不存在 → Settings()（全默认值 = 现行行为，零提示零动作）；
    - 损坏/包裹层/version 异常 → SettingsStoreError（如实报错，不用
      默认值盖掉，不静默覆盖原始证据）；
    - settings 缺字段 → from_dict 补默认值（不报错）；
    - settings 未知键/字段值非法 → SettingsError 原样上抛。
    """
    path = Path(path) if path is not None else settings_path()
    if not path.exists():
        return Settings()
    wrapper = _read_wrapper(path)
    payload = wrapper.get("settings")
    if not isinstance(payload, dict):
        raise SettingsStoreError(
            f"设置文件 settings 必须是 dict，得到 {type(payload).__name__}——{path}")
    return Settings.from_dict(payload)


def save_settings(settings: Settings, path=None) -> Path:
    """全量原子写设置（task12 §4.1：tmp 文件 + os.replace，每次全量写）。
    非 Settings 实例显式报错；返回写入路径。"""
    if not isinstance(settings, Settings):
        raise SettingsStoreError(
            f"settings 必须是 Settings 实例（唯一模型，不收散字典），"
            f"得到 {type(settings).__name__}")
    path = Path(path) if path is not None else settings_path()
    wrapper = {
        "version": SETTINGS_FILE_VERSION,
        "settings": settings.to_dict(),
    }
    payload = json.dumps(wrapper, ensure_ascii=False, indent=2) + "\n"
    directory = path.parent
    if not directory.is_dir():
        directory.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".settings-", dir=str(directory))
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


def reset_settings(path=None) -> Settings:
    """重置为默认（task12 §4.2 重置原语）：现存文件先改名
    settings.json.corrupt-<UTC时间戳> 留档（不静默覆盖、不删原始证据），
    再写默认值并返回。呈现层（GUI 弹窗/CLI 报错）接线归设置页集成段。
    """
    path = Path(path) if path is not None else settings_path()
    if path.exists():
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        backup = path.with_name(f"{path.name}.corrupt-{stamp}")
        os.replace(path, backup)                   # 留档改名（同目录原子）
    fresh = Settings()
    save_settings(fresh, path)
    return fresh
