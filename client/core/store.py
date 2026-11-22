#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地存储（core/store.py）
===========================
SQLite（标准库 sqlite3）。只存本地，零遥测、零外发（设计红线）。
- nodes：节点全量（含出口画像/指标，JSON 列）
- verify_samples：每次验证/连接的结果样本，养鱼池（history）的数据来源
- sources：来源登记表（task30 折进，与 SourceRegistry 文件形态同口径；
  与 nodes 共用同一连接 + 同一把 RLock，登记写与节点写同锁串行可见）
- source_last_updates：登记条目的上次更新记账（JSON 列，同上共用锁）
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Optional

from .model import Node, SCENES, SOURCES

_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    protocol    TEXT NOT NULL,
    source      TEXT NOT NULL,
    host        TEXT,
    port        INTEGER,
    config_ref  TEXT,
    exit_json   TEXT NOT NULL DEFAULT '{}',
    scenes      TEXT NOT NULL DEFAULT '[]',
    metrics_json TEXT NOT NULL DEFAULT '{}',
    pinned      INTEGER NOT NULL DEFAULT 0,
    banned      INTEGER NOT NULL DEFAULT 0,
    extra_json  TEXT NOT NULL DEFAULT '{}',
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS verify_samples (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id     TEXT NOT NULL,
    ts          REAL NOT NULL,
    kind        TEXT NOT NULL,          -- verify / connect / disconnect / drop
    success     INTEGER NOT NULL,
    latency_ms  REAL,
    jitter_ms   REAL,
    exit_ip     TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_samples_node ON verify_samples(node_id, ts);
CREATE TABLE IF NOT EXISTS sources (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    page        TEXT NOT NULL DEFAULT '',
    address     TEXT NOT NULL DEFAULT '',
    name        TEXT NOT NULL DEFAULT '',
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS source_last_updates (
    id          TEXT PRIMARY KEY,
    last_update TEXT NOT NULL DEFAULT '{}'
);
"""

# 来源登记（task30 折进 Store 的 sources 表）的域常量：与
# sources/source_registry.py 的 ENTRY_TYPES / ENTRY_PAGE /
# FEED_DEFAULT_ADDRESS 逐字同文（register 的校验、单例、地址口径、错误
# 文案两处实现必须一字不差，由 test_registry_store 的等价组看守不漂移；
# 同文副本 + 注释互指是本库既有惯例——sources/merge_proto.py 的
# FEED_DEFAULT_ADDRESS 同款，不搞 core→sources 反向依赖）。
SOURCE_ENTRY_TYPES = ("subscription", "feed")
SOURCE_ENTRY_PAGE = {
    "subscription": "manual",        # 手动导入页（D1：订阅导入归手动页）
    "feed": "vpngate",               # VPNGate 来源页（合并页，task8/task23）
}
SOURCE_FEED_DEFAULT_ADDRESS = "https://www.vpngate.net/api/iphone/"


@dataclass
class VerifySample:
    node_id: str
    kind: str
    success: bool
    ts: float = 0.0                       # 0 = 落库时取当前时间
    latency_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    exit_ip: Optional[str] = None
    detail: str = ""


class Store:
    def __init__(self, path: str = ":memory:"):
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        # check_same_thread=False + 内部锁：M3 图形界面里连接/验出口跑在
        # 工作线程、表格读取在主线程，同一个 Store 要跨线程用（sqlite
        # 连接对象本身不是线程安全的，靠这把 RLock 串行化全部访问）。
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self.path = path

    def close(self):
        with self._lock:
            self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------- nodes ----------------
    def upsert_node(self, node: Node) -> Node:
        with self._lock:
            return self._upsert_node_impl(node)

    def _upsert_node_impl(self, node: Node) -> Node:
        """插入或更新。已存在时保留库里的人工标记（pin/ban 不被来源刷新冲掉）。"""
        old = self.get_node(node.id)
        if old is not None:
            node.pinned = old.pinned
            node.banned = old.banned
        self._conn.execute(
            """INSERT INTO nodes (id, name, protocol, source, host, port, config_ref,
                   exit_json, scenes, metrics_json, pinned, banned, extra_json, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name, protocol=excluded.protocol, source=excluded.source,
                   host=excluded.host, port=excluded.port, config_ref=excluded.config_ref,
                   exit_json=excluded.exit_json, scenes=excluded.scenes,
                   metrics_json=excluded.metrics_json, pinned=excluded.pinned,
                   banned=excluded.banned, extra_json=excluded.extra_json,
                   updated_at=excluded.updated_at""",
            (node.id, node.name, node.protocol, node.source, node.host, node.port,
             node.config_ref, json.dumps(node.exit_profile.to_dict(), ensure_ascii=False),
             json.dumps(sorted(node.scenes)), json.dumps(node.metrics.to_dict()),
             int(node.pinned), int(node.banned),
             json.dumps(node.extra, ensure_ascii=False), time.time()),
        )
        self._conn.commit()
        return node

    def upsert_nodes(self, nodes) -> int:
        for n in nodes:
            self.upsert_node(n)
        return len(nodes)

    def _row_to_node(self, row: sqlite3.Row) -> Node:
        return Node.from_dict({
            "id": row["id"], "name": row["name"], "protocol": row["protocol"],
            "source": row["source"], "host": row["host"] or "", "port": row["port"] or 0,
            "config_ref": row["config_ref"] or "",
            "exit_profile": json.loads(row["exit_json"] or "{}"),
            "scenes": json.loads(row["scenes"] or "[]"),
            "metrics": json.loads(row["metrics_json"] or "{}"),
            "pinned": bool(row["pinned"]), "banned": bool(row["banned"]),
            "extra": json.loads(row["extra_json"] or "{}"),
        })

    def get_node(self, node_id: str) -> Optional[Node]:
        with self._lock:
            row = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
            return self._row_to_node(row) if row else None

    def list_nodes(self, scene: Optional[str] = None, source: Optional[str] = None,
                   include_banned: bool = False):
        with self._lock:
            return self._list_nodes_impl(scene=scene, source=source,
                                         include_banned=include_banned)

    def _list_nodes_impl(self, scene: Optional[str] = None, source: Optional[str] = None,
                         include_banned: bool = False):
        if scene is not None and scene not in SCENES:
            raise ValueError(f"未知场景: {scene}（可选 {SCENES}）")
        if source is not None and source not in SOURCES:
            raise ValueError(f"未知来源: {source}（可选 {SOURCES}）")
        rows = self._conn.execute("SELECT * FROM nodes ORDER BY name, id").fetchall()
        nodes = [self._row_to_node(r) for r in rows]
        if not include_banned:
            nodes = [n for n in nodes if not n.banned]
        if source is not None:
            nodes = [n for n in nodes if n.source == source]
        if scene is not None:
            nodes = [n for n in nodes if scene in n.scenes]
        return nodes

    def count_nodes(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    def _set_flag(self, node_id: str, column: str, value: bool) -> bool:
        with self._lock:
            cur = self._conn.execute(
                f"UPDATE nodes SET {column} = ?, updated_at = ? WHERE id = ?",
                (int(value), time.time(), node_id))
            self._conn.commit()
            return cur.rowcount > 0

    def set_pinned(self, node_id: str, pinned: bool = True) -> bool:
        return self._set_flag(node_id, "pinned", pinned)

    def set_banned(self, node_id: str, banned: bool = True) -> bool:
        return self._set_flag(node_id, "banned", banned)

    def delete_node(self, node_id: str) -> bool:
        """真删除：节点行 + 它的全部验证样本一并清掉，返回是否删到了。

        拉黑只是打标记、节点还在库里；没有删除，库只进不出会越攒越脏
        （2026-11-21 用户亲点要删除功能）。"""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM nodes WHERE id = ?", (node_id,))
            self._conn.execute(
                "DELETE FROM verify_samples WHERE node_id = ?", (node_id,))
            self._conn.commit()
            return cur.rowcount > 0

    # ---------------- samples ----------------
    def add_sample(self, sample: VerifySample) -> None:
        with self._lock:
            self._add_sample_impl(sample)

    def _add_sample_impl(self, sample: VerifySample) -> None:
        ts = sample.ts or time.time()
        self._conn.execute(
            """INSERT INTO verify_samples (node_id, ts, kind, success, latency_ms, jitter_ms, exit_ip, detail)
               VALUES (?,?,?,?,?,?,?,?)""",
            (sample.node_id, ts, sample.kind, int(sample.success),
             sample.latency_ms, sample.jitter_ms, sample.exit_ip, sample.detail))
        self._conn.commit()

    def samples_for(self, node_id: str, kind: Optional[str] = None):
        with self._lock:
            return self._samples_for_impl(node_id, kind=kind)

    def _samples_for_impl(self, node_id: str, kind: Optional[str] = None):
        if kind:
            rows = self._conn.execute(
                "SELECT * FROM verify_samples WHERE node_id = ? AND kind = ? ORDER BY ts",
                (node_id, kind)).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM verify_samples WHERE node_id = ? ORDER BY ts",
                (node_id,)).fetchall()
        return [VerifySample(node_id=r["node_id"], kind=r["kind"], success=bool(r["success"]),
                             ts=r["ts"], latency_ms=r["latency_ms"], jitter_ms=r["jitter_ms"],
                             exit_ip=r["exit_ip"], detail=r["detail"] or "") for r in rows]

    # ---------------- sources（来源登记表，task30 折进） ----------------
    # 与 SourceRegistry 同名同义同口径（register / entries / get / remove /
    # set_enabled / last_update / set_last_update）：参数、返回值、校验、
    # 错误文案与 sources/source_registry.py 的文件形态一字不差（两处实现
    # 由 test_registry_store 等价组看守）；读写全部走 self._lock +
    # self._conn——与 nodes 共用同一把锁、同一个连接，登记写与节点写
    # 同锁串行、同连接可见（不做第二种落盘习惯）。

    def _row_to_source(self, row: sqlite3.Row) -> dict:
        """sources 行 → entry dict（字段与 SourceRegistry 的 entry dict 同构）。
        空串/0 兜底只为老库防御：register 写入时各列恒非空。"""
        return {
            "id": row["id"], "type": row["type"], "page": row["page"] or "",
            "address": row["address"] or "", "name": row["name"] or "",
            "enabled": bool(row["enabled"]), "created_at": row["created_at"] or 0.0,
        }

    def register(self, type_: str, address: str = "", name: str = "",
                 enabled: bool = True, now: float = None) -> dict:
        """登记一个可续期来源。feed 地址可留空（自动填官方默认）；
        subscription 必须给 http/https 地址。返回登记条目副本。
        校验/去重/单例/id 派生与错误文案 = SourceRegistry.register 的逐字
        移植（口径注释见模块头常量；feed_mirror 已废除，传它会在这里
        显式报错）。"""
        with self._lock:
            if type_ not in SOURCE_ENTRY_TYPES:
                raise ValueError(
                    f"未知来源类型: {type_}（可选 {SOURCE_ENTRY_TYPES}）")
            address = (address or "").strip()
            name = (name or "").strip()
            if type_ == "subscription":
                scheme = (address.split("://", 1)[0].lower()
                          if "://" in address else "")
                if scheme not in ("http", "https"):
                    raise ValueError("订阅地址只允许 http/https（登记制存的就是它）")
                entry_id = "sub-" + hashlib.sha1(
                    address.encode("utf-8")).hexdigest()[:8]
                if self._conn.execute("SELECT id FROM sources WHERE id = ?",
                                      (entry_id,)).fetchone() is not None:
                    raise ValueError("该订阅地址已登记过（同地址不重复登记）")
            else:
                if self._conn.execute("SELECT id FROM sources WHERE type = ?",
                                      (type_,)).fetchone() is not None:
                    raise ValueError(f"{type_} 是单例来源，已登记，不可重复")
                if address:
                    scheme = (address.split("://", 1)[0].lower()
                              if "://" in address else "")
                    if scheme not in ("http", "https"):
                        raise ValueError("feed 地址只允许 http/https")
                else:
                    address = SOURCE_FEED_DEFAULT_ADDRESS
                entry_id = "feed"            # feed 类唯一条目 id（不变）
            entry = {
                "id": entry_id, "type": type_, "page": SOURCE_ENTRY_PAGE[type_],
                "address": address, "name": name, "enabled": bool(enabled),
                "created_at": now if now is not None else time.time(),
            }
            self._conn.execute(
                "INSERT INTO sources (id, type, page, address, name, enabled, created_at)"
                " VALUES (?,?,?,?,?,?,?)",
                (entry["id"], entry["type"], entry["page"], entry["address"],
                 entry["name"], int(entry["enabled"]), entry["created_at"]))
            self._conn.commit()
            return dict(entry)

    def entries(self) -> list:
        """全部登记条目（副本列表），按 id 排序——同文件形态的持久化排序
        （JSON 落盘即按 id 排序，重开读回就是 id 序），store 形态把它定为
        恒定次序（跨会话稳定，update_all 逐条顺序因此可复现）。"""
        with self._lock:
            rows = self._conn.execute("SELECT * FROM sources ORDER BY id").fetchall()
            return [self._row_to_source(r) for r in rows]

    def get(self, entry_id: str):
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM sources WHERE id = ?", (entry_id,)).fetchone()
            return self._row_to_source(row) if row else None

    def remove(self, entry_id: str) -> bool:
        """删除登记：只删条目与其记账行（同文件形态 remove 的 _last.pop），
        **节点原地不动**（样本/置顶/拉黑/消失标记都在节点上）。返回是否删到。"""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sources WHERE id = ?", (entry_id,))
            self._conn.execute(
                "DELETE FROM source_last_updates WHERE id = ?", (entry_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def set_enabled(self, entry_id: str, enabled: bool) -> None:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE sources SET enabled = ? WHERE id = ?",
                (int(bool(enabled)), entry_id))
            if cur.rowcount == 0:
                self._conn.rollback()   # 无行可写，事务原样收掉再报错
                raise ValueError(f"未登记的来源: {entry_id}")
            self._conn.commit()

    def last_update(self, entry_id: str):
        """上次更新结果（含上次时间与结果计数）；从未更新过返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT last_update FROM source_last_updates WHERE id = ?",
                (entry_id,)).fetchone()
            return json.loads(row["last_update"]) if row else None

    def set_last_update(self, entry_id: str, payload: dict) -> None:
        """写一次更新记账（文件形态里的私有 _record，折进 Store 后取这个
        公开名；SourceRegistry._record 的 store 形态委托到这里）。"""
        with self._lock:
            self._conn.execute(
                "INSERT INTO source_last_updates (id, last_update) VALUES (?,?)"
                " ON CONFLICT(id) DO UPDATE SET last_update = excluded.last_update",
                (entry_id, json.dumps(payload, ensure_ascii=False)))
            self._conn.commit()

    def import_source(self, entry: dict, last_update: dict = None) -> bool:
        """迁移专用原始入库（migrate_registry_json_to_store 用）：不校验、
        不派生 id，条目七字段原样进表（表内以 JSON 为准收编，不重跑
        register 的口径）；id 已在表内 → 整条跳过返回 False（重复迁移
        零重复）。last_update 只随新导入的条目落账——表内已有条目的记账
        以表内现值为准，不被旧 JSON 盖写。"""
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sources (id, type, page, address, name, enabled, created_at)"
                " VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO NOTHING",
                (entry["id"], entry.get("type", ""), entry.get("page", ""),
                 entry.get("address", ""), entry.get("name", ""),
                 int(bool(entry.get("enabled", True))),
                 float(entry.get("created_at") or 0.0)))
            if cur.rowcount == 0:
                self._conn.commit()
                return False
            if last_update is not None:
                self._conn.execute(
                    "INSERT INTO source_last_updates (id, last_update) VALUES (?,?)"
                    " ON CONFLICT(id) DO UPDATE SET last_update = excluded.last_update",
                    (entry["id"], json.dumps(last_update, ensure_ascii=False)))
            self._conn.commit()
            return True
