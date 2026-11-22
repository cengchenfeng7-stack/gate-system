#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
设置存储模块函数（core/settings_store.py）—— task26 ② 收敛为转调层
========================================================================
task12 §4 持久化口径的函数面（task17 落地），task26 ②（用户 2026-11-22
裁决「收」）起**函数体全部转调 settings_proto.SettingsStore**——持久化
实现全库唯一（读写/序列化/原子写/包裹层校验/留档改名都只在
settings_proto.SettingsStore 一处），本模块不再有任何独立的读写/序列化
逻辑（test_ruling_wiring.py 的 grep 级断言锁定此形态）。

保留的四件事（调用点不动，task26 ② 口径）：
- 函数签名：settings_path / load_settings / save_settings / reset_settings
  原样；settings_path 的「显式 data_dir 目录」档是**目录语义**（与
  settings_file_path 的「--db 文件路径取目录」档不同构），路径拼装保留
  在此，默认档转调 settings_file_path()（DEFAULT_SETTINGS_FILE 常量仍
  只在 settings_proto 一处，§6-7 不动）。
- SettingsStoreError：既有调用点（usage_window ⑤区/批量测等）的捕获面。
  文件层错误（损坏/包裹层/version/读取失败）由转调层从
  settings_proto.SettingsFileError 族**原文翻译**而来（str 不加工）；
  字段级校验错误仍为 settings_proto.SettingsError 原样上抛——两族同属
  ValueError，校验口径不变。
- load_settings 的「文件不存在 → 全默认值起、零提示零动作、不写回」
  （task12 §4.2/§4.3）：SettingsStore.load 对缺失文件抛
  SettingsFileError（bootstrap 先 exists() 再 load 的既有合同），本函数
  在此保留存在性分流——这是调用合同，不是第二套读逻辑。
- reset_settings 的留档后缀 `.corrupt-<UTC时间戳>`：经
  SettingsStore.reset(tag="corrupt") 得到（同一实现，同一名）。

行为等价红线（task26 ②）：读写文件、格式（{"version":1,"settings":…}+
换行收尾）、字段、默认值、校验口径一律不变；save 落盘字节与 task12 §4
历史格式逐字节同形（SettingsStore.save 已按此统一）。与
SettingsStore 直用面的差异收敛记录（message 措辞/临时文件名
`<file>.tmp`/未知包裹层键现在统一拒收）见 work26/DONE.md §对账。

铁律对齐：本模块没有任何节点选择/切换/优选逻辑，不持有任何节点字段；
它只存用户显式改过的偏好。
"""

from __future__ import annotations

from pathlib import Path

from .settings_proto import (
    Settings,
    SettingsError,
    SettingsFileError,
    SettingsStore,
    settings_file_path,
)

__all__ = ["SettingsStoreError", "settings_path", "load_settings",
           "save_settings", "reset_settings"]


class SettingsStoreError(ValueError):
    """设置文件层非法（损坏/包裹层/version 异常）。如实报错不静默吞，
    呈现层据此给文件路径与重置入口（task12 §4.2）。
    字段级校验错误沿用 settings_proto.SettingsError（同属 ValueError 族）。
    task26 ② 收敛后：实现本体在 settings_proto.SettingsStore，本类保留为
    既有调用点的捕获面——文件层错误自 SettingsFileError 族原文转译，
    语义与实现单一事实源不变。"""


def settings_path(data_dir=None) -> Path:
    """设置文件路径：显式 data_dir（覆盖目录）> 内置默认。

    data_dir = --db / GATE_CLIENT_DB 显式指定时解析出的数据目录（task12
    §4.1「显式覆盖时设置文件跟覆盖目录走」）；None = 内置默认目录——
    转调 settings_file_path()（settings_proto.DEFAULT_SETTINGS_FILE
    expanduser，路径常量唯一出处 §6-7）。
    不读 settings.data_dir 字段自举（§4.1：设置文件不跟随该设置）。
    """
    if data_dir is not None:
        return Path(data_dir) / "settings.json"
    return Path(settings_file_path())


def load_settings(path=None) -> Settings:
    """读设置 → Settings 实例（task12 §4.2 逐情形处置；实现转调
    SettingsStore.load，task26 ②）。

    - 文件不存在 → Settings()（全默认值 = 现行行为，零提示零动作，
      不写回文件）；
    - 损坏/包裹层/version 异常/读取失败 → SettingsStoreError（自
      SettingsFileError 族原文转译，不用默认值盖掉，不静默覆盖原始证据）；
    - settings 缺字段 → from_dict 补默认值（不报错）；
    - settings 未知键/字段值非法 → SettingsError 原样上抛。
    """
    p = Path(path) if path is not None else settings_path()
    if not p.exists():                             # §4.3：首次运行全默认起
        return Settings()
    store = SettingsStore(str(p))
    try:
        return store.load()
    except SettingsFileError as exc:               # 文件层 → 既有捕获面
        raise SettingsStoreError(str(exc)) from None


def save_settings(settings: Settings, path=None) -> Path:
    """全量原子写设置（实现转调 SettingsStore.save，task26 ②；§4.1：
    临时文件 + os.replace，每次全量写）。非 Settings 实例显式报错
    （SettingsStoreError，既有捕获面）；返回写入路径。"""
    p = Path(path) if path is not None else settings_path()
    store = SettingsStore(str(p))
    try:
        store.save(settings)
    except SettingsError as exc:                   # 含文件层子类 → 既有捕获面
        raise SettingsStoreError(str(exc)) from None
    return p


def reset_settings(path=None) -> Settings:
    """重置为默认（task12 §4.2 重置原语；实现转调 SettingsStore.reset
    (tag="corrupt")，task26 ②）：现存文件先改名
    settings.json.corrupt-<UTC时间戳> 留档（不静默覆盖、不删原始证据），
    再写默认值并返回。呈现层（GUI 弹窗/CLI 报错）接线归设置页集成段。"""
    p = Path(path) if path is not None else settings_path()
    store = SettingsStore(str(p))
    return store.reset(tag="corrupt")
