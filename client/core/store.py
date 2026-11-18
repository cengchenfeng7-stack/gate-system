#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地存储（core/store.py）
===========================
SQLite（标准库 sqlite3）。只存本地，零遥测、零外发（设计红线）。
- nodes：节点全量（含出口画像/指标，JSON 列）
- verify_samples：每次验证/连接的结果样本，养鱼池（history）的数据来源
"""

from __future__ import annotations

import json
import os
import sqlite3
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
"""


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
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()
        self.path = path

    def close(self):
        self._conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---------------- nodes ----------------
    def upsert_node(self, node: Node) -> Node:
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
        row = self._conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        return self._row_to_node(row) if row else None

    def list_nodes(self, scene: Optional[str] = None, source: Optional[str] = None,
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
        return self._conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]

    def _set_flag(self, node_id: str, column: str, value: bool) -> bool:
        cur = self._conn.execute(
            f"UPDATE nodes SET {column} = ?, updated_at = ? WHERE id = ?",
            (int(value), time.time(), node_id))
        self._conn.commit()
        return cur.rowcount > 0

    def set_pinned(self, node_id: str, pinned: bool = True) -> bool:
        return self._set_flag(node_id, "pinned", pinned)

    def set_banned(self, node_id: str, banned: bool = True) -> bool:
        return self._set_flag(node_id, "banned", banned)

    # ---------------- samples ----------------
    def add_sample(self, sample: VerifySample) -> None:
        ts = sample.ts or time.time()
        self._conn.execute(
            """INSERT INTO verify_samples (node_id, ts, kind, success, latency_ms, jitter_ms, exit_ip, detail)
               VALUES (?,?,?,?,?,?,?,?)""",
            (sample.node_id, ts, sample.kind, int(sample.success),
             sample.latency_ms, sample.jitter_ms, sample.exit_ip, sample.detail))
        self._conn.commit()

    def samples_for(self, node_id: str, kind: Optional[str] = None):
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
