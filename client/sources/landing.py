#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
来源合并落页——启动迁移触发（sources/landing.py，task23 接线件）
================================================================
task8（v0.12.0）把来源合并的迁移原型落在了 client/sources/merge_proto.py
（canonical 键 vpngate，合并/迁移口径以 work8/DESIGN §3 为准），但 GUI/CLI
从未在启动时跑过它——四页时代的存量（source=openvpn / vpngate_feed 的节点、
sources.json 里的 feed_mirror 登记）一直没迁。本模块就是那笔欠账的收口：

- run_startup_migration(store, registry_path=None)
    启动时调用（GUI main / CLI main 各自创建 Store 之后——实测两界面没有
    单一共同函数，共同点是 Store 的构造，落点见 work23 落点清单）。
    检测「sources.json 含 feed_mirror 残留 ∨ 库内存在旧两键节点」→ 一次性
    迁移；检测与执行都幂等，误触发无害（不命中零写入）。

红线（task23 简报 + work8/DESIGN §8）：
- 合并/迁移口径全部调 merge_proto 既有函数（migrate_library /
  sample_id_map / migrate_registry_json），本模块只做读写粘合（SQLite 行
  改写、sources.json 原子写回），不重写任何合并规则；
- 先迁登记表、再迁节点库；样本重映射与节点改写同一事务（work8/DONE §四）；
- 不联网、假存量 only；永不自动切换节点（本模块只搬数据，不评优劣）。
"""
from __future__ import annotations

import json
import os

from . import merge_proto as mp

# 登记表侧的镜像残留形态：类型 feed_mirror、旧条目 id feed-mirror；
# 旧页键 page（vpngate_feed/openvpn）也算残留——检测幂等、误触发无害，
# 顺手让 migrate_registry_json 把 page 归一（否则登记过 feed 却没喂过
# 节点的库永远触发不了迁移，条目 page 残留会让「更新已登记来源」失配）。
LEGACY_PAGE_KEYS = ("vpngate_feed", "openvpn")

_INSERT_NODE = (
    "INSERT INTO nodes (id, name, protocol, source, host, port, config_ref,"
    " exit_json, scenes, metrics_json, pinned, banned, extra_json, updated_at)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)")

# sources.json 原子写回（口径同 SourceRegistry._save：临时文件 + os.replace）
_REG_TMP_SUFFIX = ".tmp"


def registry_path_for(store) -> str:
    """登记表位置：与节点库同目录的 sources.json（GUI/CLI 共用同一份，
    口径同 ui/cli._registry_path）。内存库没有同目录可言，返回 None——
    跳过登记表侧检测与迁移，节点库照迁。"""
    path = getattr(store, "path", "") or ""
    if not path or path == ":memory:":
        return None
    return os.path.join(os.path.dirname(os.path.abspath(path)), "sources.json")


def _read_registry(path):
    """读登记表 JSON；文件不存在 / 读不动返回 None（登记表坏了不该挡启动，
    SourceRegistry 后续自会显式报错，不是迁移的职责）。"""
    if not path or not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _registry_has_legacy_marks(data) -> bool:
    """登记表是否还是四页时代形状：feed_mirror 条目（type 或旧 id），
    或任一条目 page 残留旧页键。"""
    if not isinstance(data, dict):
        return False
    for entry in data.get("entries", []) or []:
        if not isinstance(entry, dict):
            continue
        if (entry.get("type") == "feed_mirror"
                or entry.get("id") == mp.LEGACY_MIRROR_ENTRY_ID):
            return True
        if entry.get("page") in LEGACY_PAGE_KEYS:
            return True
    return False


def _legacy_nodes_present(conn) -> bool:
    """库内是否存在旧两键（openvpn / vpngate_feed）节点行。"""
    row = conn.execute(
        "SELECT COUNT(*) FROM nodes WHERE source IN (?, ?)",
        LEGACY_PAGE_KEYS).fetchone()
    return bool(row and row[0])


def needs_startup_migration(store, registry_path=None) -> bool:
    """task23 检测口径（简报原文）：sources.json 含 feed_mirror ∨
    库内存在旧两键（openvpn / vpngate_feed）。只读，零写入。"""
    with store._lock:
        legacy = _legacy_nodes_present(store._conn)
    if legacy:
        return True
    path = registry_path if registry_path is not None else registry_path_for(store)
    return _registry_has_legacy_marks(_read_registry(path))


def _record_from_row(row) -> dict:
    """sqlite Row → Node.to_dict() 形状（列口径逐项同 store._row_to_node）。"""
    return {
        "id": row["id"],
        "name": row["name"],
        "protocol": row["protocol"],
        "source": row["source"],
        "host": row["host"] or "",
        "port": int(row["port"] or 0),
        "config_ref": row["config_ref"] or "",
        "exit_profile": json.loads(row["exit_json"] or "{}"),
        "scenes": json.loads(row["scenes"] or "[]"),
        "metrics": json.loads(row["metrics_json"] or "{}"),
        "pinned": bool(row["pinned"]),
        "banned": bool(row["banned"]),
        "extra": json.loads(row["extra_json"] or "{}"),
    }


def _row_tuple(record: dict, updated_at: float) -> tuple:
    """迁移后记录 → nodes 表行（列口径逐项同 store.upsert_node 的写法）。
    updated_at 沿用该身份组旧行里的最新值（迁移不改节点的最后活跃时间）。"""
    return (
        record["id"], record["name"], record["protocol"], record["source"],
        record["host"], int(record["port"] or 0), record["config_ref"] or "",
        json.dumps(record["exit_profile"], ensure_ascii=False),
        json.dumps(sorted(record["scenes"])),
        json.dumps(record["metrics"], ensure_ascii=False),
        int(bool(record["pinned"])), int(bool(record["banned"])),
        json.dumps(record["extra"], ensure_ascii=False), updated_at,
    )


def _write_registry(path: str, data: dict) -> None:
    """登记表写回（口径同 SourceRegistry._save：entries 按 id 排序、
    临时文件 + os.replace 原子替换）。"""
    tmp = path + _REG_TMP_SUFFIX
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1,
                   "entries": sorted(data.get("entries", []),
                                     key=lambda e: e.get("id") or ""),
                   "last_updates": data.get("last_updates", {})},
                  f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def _migrate_nodes(store) -> dict:
    """节点库迁移 + 样本重映射（同一事务，work8/DONE §四第 2 条）。

    纯内存阶段：读全表 → migrate_library → sample_id_map（都是 merge_proto
    既有函数，本模块不重写合并口径）。落库阶段全部走裸 SQL 且只在末尾
    commit 一次——store 的公开方法各自 commit，事务里不能用；任何一步
    抛异常先 rollback 再抛，绝不留下半截迁移。"""
    conn = store._conn
    rows = conn.execute("SELECT * FROM nodes").fetchall()
    before = [_record_from_row(r) for r in rows]
    row_updated_at = {r["id"]: r["updated_at"] for r in rows}
    after = mp.migrate_library(before)
    mapping = mp.sample_id_map(before, after)

    legacy_ids = [r["id"] for r in before
                  if r["source"] in mp.LEGACY_SOURCE_KEYS]
    # 迁移目标（新 id）：哪些旧行并成它、组内最新 updated_at 是多少。
    # manual/pipeline/未知来源映射到自身（old==new），不进 targets。
    targets = {}
    for old_id, new_id in mapping.items():
        if old_id == new_id:
            continue
        slot = targets.setdefault(new_id, {"updated_at": 0.0})
        slot["updated_at"] = max(slot["updated_at"],
                                 float(row_updated_at.get(old_id) or 0.0))
    records_after = {r["id"]: r for r in after}
    insert_rows = [_row_tuple(records_after[new_id], info["updated_at"])
                   for new_id, info in sorted(targets.items())]

    report = {"nodes_rewritten": 0, "merged_away": 0, "samples_remapped": 0}
    try:
        # ① 样本表先改（行数不变；旧 node_id 指向的节点行马上要改 id）
        for old_id, new_id in sorted(mapping.items()):
            if old_id == new_id:
                continue
            cur = conn.execute(
                "UPDATE verify_samples SET node_id = ? WHERE node_id = ?",
                (new_id, old_id))
            report["samples_remapped"] += max(cur.rowcount, 0)
        # ② 删旧两键旧行（内容已并入迁移后记录；无损由 merged_superseded
        #    快照与样本行保证——work8/DESIGN §3.9、§4.3）
        conn.executemany("DELETE FROM nodes WHERE id = ?",
                         [(i,) for i in legacy_ids])
        # ③ 插入迁移后记录（每身份恰一条）
        conn.executemany(_INSERT_NODE, insert_rows)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    report["nodes_rewritten"] = len(insert_rows)
    report["merged_away"] = len(legacy_ids) - len(insert_rows)
    return report


def run_startup_migration(store, registry_path=None) -> dict:
    """启动一次性迁移（幂等；未触发零写入）。返回报告 dict：

    - triggered          是否触发了迁移（检测口径见 needs_startup_migration）
    - registry_migrated  sources.json 是否被改写
    - nodes_rewritten    节点表新插入的迁移后行数（每身份恰一条）
    - merged_away        按身份合并吃掉的旧行数（旧行数 − 迁移后行数）
    - samples_remapped   verify_samples 改写的行数

    顺序（work8/DONE §四）：先迁登记表（纯函数迁移 + 原子写回）、再迁
    节点库；样本重映射与节点改写同一事务。全程持 store._lock：启动期
    单线程，锁只为与 Store 的其他访问口径一致。"""
    path = registry_path if registry_path is not None else registry_path_for(store)
    report = {"triggered": False, "registry_migrated": False,
              "nodes_rewritten": 0, "merged_away": 0, "samples_remapped": 0}
    with store._lock:
        reg_data = _read_registry(path)
        legacy = _legacy_nodes_present(store._conn)
        if not legacy and not _registry_has_legacy_marks(reg_data):
            return report                      # 不触发：零写入，幂等
        report["triggered"] = True
        # ① 先迁登记表（work8/DONE §四第 1 条）
        if reg_data is not None:
            migrated = mp.migrate_registry_json(reg_data)
            if migrated != reg_data:
                _write_registry(path, migrated)
                report["registry_migrated"] = True
        # ② 再迁节点库（旧两键节点在库才动；样本同事务）
        if legacy:
            report.update(_migrate_nodes(store))
    return report
