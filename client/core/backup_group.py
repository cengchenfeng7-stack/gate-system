#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
备用组存储（core/backup_group.py）—— 容灾/自动替换的组配置（U6/task21）
=======================================================================
work10/DESIGN.md §5.2 定稿口径的落盘实现：

- 备用组 = 一组人工挑选的备用节点，**数组顺序 = 替换优先级**（第 0 个
  首选）。组内容全人工：本模块只做存取、校验、清组，不提供任何
  「自动加节点」「按指标排序」的入口（铁律，§5.5）。
- 数据落点：独立 JSON 文件 backup_group.json，与 sources.json 同目录、
  同「tmp + os.replace 原子写」口径（照 sources/source_registry.py
  SourceRegistry._save 的写法，不引入第二种落盘习惯）。
- 结构：{"version": 1, "enabled": false, "node_ids": ["id1", "id2", …]}
- 开启总开关前置校验：组内 <2 个节点拒绝开启（§5.2：一个节点的
  「备用组」没有容灾意义）。
- 清组：组内节点被后台删除/拉黑时自动移出组（§5.2 联动规则——连接一个
  被 ban 节点会被 orchestrator 拒，orchestrator.py:97-98，必须前置清组）；
  返回清理后的组 + 待提示清单，提示文案由界面层拼。

只用标准库；不依赖 PySide6、不依赖 client 包其它模块（与 core/health.py
同一独立口径，接线层调用本模块，本模块绝不反向调用 orchestrator/引擎）。
"""

from __future__ import annotations

import json
import os

# ---------------------------------------------------------------------------
# 常量与默认结构
# ---------------------------------------------------------------------------

FILENAME = "backup_group.json"     # 与 sources.json 同目录
VERSION = 1
MIN_NODES_TO_ENABLE = 2            # §5.2：开启总开关的组内最小节点数

# 节点被移出组的两类原因（清组函数的 reason 取值）
REASON_DELETED = "deleted"         # 节点已从库中删除
REASON_BANNED = "banned"           # 节点已被人工拉黑

DEFAULT_GROUP = {"version": VERSION, "enabled": False, "node_ids": []}


class BackupGroupError(ValueError):
    """备用组文件读不了/结构非法。界面层应弹给人看，绝不静默重置——
    静默重置会把用户的组悄悄清掉（同 settings.json「不静默覆盖」口径）。"""


def backup_group_path(store_path: str = "") -> str:
    """由节点库路径推导 backup_group.json 的落点。

    与 ui/app.py MainWindow._registry() 的 sources.json 同一推导口径：
    dirname(abspath(store_path or "client.db")) + 文件名——保证两个 JSON
    永远待在一起（§5.2「与 sources.json 同目录」）。
    """
    base = os.path.dirname(os.path.abspath(store_path or "client.db"))
    return os.path.join(base, FILENAME)


# ---------------------------------------------------------------------------
# 结构校验
# ---------------------------------------------------------------------------

def validate_group(data) -> list:
    """结构校验，返回问题清单（空列表 = 合法）。

    校验口径（§5.2 定稿结构之外的部分按保守拒绝）：
    - 必须是 dict；version 必须 == 1；
    - enabled 必须是 bool（不许 0/1/字符串混入）；
    - node_ids 必须是「非空字符串的列表」，且**不允许重复**（数组顺序 =
      替换优先级，重复项会让优先级含糊）；
    - 多出来的键不报错（前向兼容：老版本客户端读新文件不炸）。
    """
    if not isinstance(data, dict):
        return ["备用组文件顶层必须是 JSON 对象"]
    problems = []
    if data.get("version") != VERSION:
        problems.append(f"version 必须是 {VERSION}，得到 {data.get('version')!r}")
    if not isinstance(data.get("enabled", False), bool):
        problems.append(f"enabled 必须是布尔值，得到 {data.get('enabled')!r}")
    ids = data.get("node_ids")
    if not isinstance(ids, list):
        problems.append(f"node_ids 必须是列表，得到 {type(ids).__name__}")
    else:
        for i, nid in enumerate(ids):
            if not isinstance(nid, str) or not nid.strip():
                problems.append(f"node_ids[{i}] 必须是非空字符串，得到 {nid!r}")
        seen = set()
        for nid in ids:
            if nid in seen:
                problems.append(f"node_ids 有重复项: {nid!r}（顺序即优先级，"
                                "重复项会让优先级含糊）")
            seen.add(nid)
    return problems


def _checked(data) -> dict:
    problems = validate_group(data)
    if problems:
        raise BackupGroupError("；".join(problems))
    return data


# ---------------------------------------------------------------------------
# 读取 / 保存（原子写）
# ---------------------------------------------------------------------------

def load_group(path: str) -> dict:
    """读备用组。文件不存在 → 返回默认结构（enabled=False，空组）。

    文件存在但 JSON 坏 / 结构非法 → 抛 BackupGroupError（带问题清单），
    绝不静默重置：重置 = 默认 enabled=False，虽然不会误动作，但会把用户
    手工维护的组悄悄丢掉。界面层接到异常应提示人去看文件。
    """
    if not os.path.exists(path):
        return dict(DEFAULT_GROUP, node_ids=[])
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise BackupGroupError(f"备用组文件不是合法 JSON：{path}（{exc}）") from exc
    return _checked(data)


def save_group(path: str, group: dict) -> None:
    """结构校验后原子写盘（tmp + os.replace，同 source_registry 口径）。

    校验不过就地抛 BackupGroupError，不写任何东西——宁可存不进去，
    也不把坏结构落盘。
    """
    _checked(group)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": group.get("version", VERSION),
                   "enabled": bool(group.get("enabled", False)),
                   "node_ids": list(group.get("node_ids", []))},
                  f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


# ---------------------------------------------------------------------------
# 总开关（§5.2：<2 拒绝开启；§5.5：默认关）
# ---------------------------------------------------------------------------

def set_enabled(group: dict, enabled: bool) -> tuple:
    """开/关总开关 → (新组, 是否成功, 拒绝原因)。

    开启（enabled=True）时组内 <2 个节点拒绝：返回 (原组, False, 原因)，
    原因文案直接给人看（界面提示原样可用）。关闭永远成功。
    """
    group = dict(group)
    if enabled and len(group.get("node_ids", [])) < MIN_NODES_TO_ENABLE:
        return (group, False,
                f"备用组内只有 {len(group.get('node_ids', []))} 个节点，"
                f"无法开启容灾自动替换：一个节点的「备用组」没有容灾意义，"
                f"请先在组里至少加入 {MIN_NODES_TO_ENABLE} 个节点。")
    group["enabled"] = bool(enabled)
    return (group, True, "")


# ---------------------------------------------------------------------------
# 清组（§5.2 联动规则：组内节点被删/被 ban → 自动移出 + 待提示）
# ---------------------------------------------------------------------------

def prune_group(group: dict, node_states: dict) -> tuple:
    """按节点现状清组 → (清理后的组, 待提示清单)。

    node_states：{node_id: {"exists": bool, "banned": bool}}，由界面层从
    节点库现读组装（本模块不依赖 client 包，不自己开 Store）。字典里没有
    的节点按「不存在」处理（被删 = 连状态都查不到）；节点仍在但 banned
    为真 → 移出（连一个被 ban 节点会被 orchestrator 拒，必须前置清组）。

    待提示清单每项 {"node_id", "reason"}，reason ∈ REASON_DELETED /
    REASON_BANNED，顺序按组内原顺序——界面层拿它拼「下次打开界面时提示」
    （§5.2）。**只移除，不重排、不补位**：剩余节点的相对顺序（=优先级）
    原样保留。enabled 原样保留（清理后是否 <2 交给 set_enabled 的开启
    门卫与界面提示处理，本函数不替用户做开关决定）。
    """
    removed = []
    kept = []
    for nid in group.get("node_ids", []):
        state = node_states.get(nid)
        if state is None or not state.get("exists", False):
            removed.append({"node_id": nid, "reason": REASON_DELETED})
            continue
        if state.get("banned", False):
            removed.append({"node_id": nid, "reason": REASON_BANNED})
            continue
        kept.append(nid)
    cleaned = dict(group)
    cleaned["node_ids"] = kept
    return (cleaned, removed)


def removed_reason_text(reason: str) -> str:
    """清组原因 → 提示文案片段（界面拼句用，保持单一口径）。"""
    return {REASON_DELETED: "节点已删除",
            REASON_BANNED: "节点已拉黑"}.get(reason, reason)
