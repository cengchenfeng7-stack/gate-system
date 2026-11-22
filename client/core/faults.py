#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
故障注入与替身（faults.py）—— task15 长稳与异常覆盖
======================================================
为 work15/DESIGN.md §2 异常分类表提供注入件与断言对象，纯标准库、
**不 import client 包**（对 client-dev 现状的断言一律走 FILE_ANCHORS
文件锚点，见文件末尾）。

组成：
- FakeEngine：可注入假引擎，四种行为——normal / start_fail / mid_death /
  stop_residue，与 engines/singbox_engine.py 的 handle/status/stop 形状一致；
- FakeProbe：假 probe，normal / timeout / parse_fail / raise 四种；
- SessionTwin：会话编排替身——与 core/orchestrator.py 同形状（状态机、
  事件流 connect_started/engine_started/connected/verify_failed/disconnected/
  dropped/error、落样本），实现的是 DESIGN §1 的**期望行为口径**（替身=验收
  标准，client-dev 现状=被测对象，两者分开：现状有差距的走 GAPS 登记）；
- twin_health_detail：health.py 分级同形替身（suspect 3 连败 / dead 5 连败）；
- DB 损坏工具：make_db / corrupt_truncate / corrupt_header / corrupt_middle /
  open_behavior——在临时目录造**真损坏文件**，按 store.py 的打开口径探测现状；
- GAPS：DESIGN §2.4 缺口登记表（测试用它反向断言 DESIGN.md 如实记录）；
- FILE_ANCHORS + iter_anchor_failures：client-dev 现状锚点（防编造）；
- pick_free_port：冷门测试端口选择（避开 0.9.0 撞车段，DESIGN §4）。

铁律：替身**没有**任何 connect_best / connect_next / auto_switch /
auto_reconnect 之类的方法——异常处置的终点永远是回 IDLE 等用户动作
（test_faults.py 有反向断言）。绝不自动切换节点。
"""

from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# 测试端口（DESIGN §4）：避开 7897/7898、10818-10829、18089-18095
# ---------------------------------------------------------------------------
FORBIDDEN_PORT_RANGES = ((7897, 7898), (10818, 10829), (18089, 18095))
TEST_PORT_BASE = 29311          # 冷门段 29311-29329
TEST_SOCKS_PORT = 29311
TEST_HTTP_PORT = 29312


def _port_forbidden(port: int) -> bool:
    return any(lo <= port <= hi for lo, hi in FORBIDDEN_PORT_RANGES)


def pick_free_port(start: int = TEST_PORT_BASE, limit: int = 64) -> int:
    """从 start 起向上找一个可 bind 的冷门端口（跳过禁用段）。找不到抛错。
    只 bind 127.0.0.1 探测、测完即关，不监听不外联。"""
    port = start
    for _ in range(limit):
        if not _port_forbidden(port):
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                pass
            finally:
                sock.close()
        port += 1
    raise RuntimeError(f"冷门段 {start}+{limit} 内找不到空闲测试端口")


# ---------------------------------------------------------------------------
# 假引擎（形状对齐 engines/singbox_engine.py 的 _Handle/status/stop）
# ---------------------------------------------------------------------------
class FaultEngineError(Exception):
    """引擎层可预期失败（对齐 engines/base.EngineError 的角色）。"""


class FakeProc:
    """假子进程：只模拟 poll/terminate/kill 的存活语义，不起真进程。"""

    def __init__(self, pid: int):
        self.pid = pid
        self._alive = True
        self.refuse_terminate = False   # stop_residue 模拟 _terminate 吞错
        self.terminate_calls = 0

    def poll(self):
        return None if self._alive else 0

    def terminate(self):
        self.terminate_calls += 1
        if not self.refuse_terminate:
            self._alive = False

    kill = terminate

    def die(self):
        """模拟运行中途退出（外部原因）。"""
        self._alive = False


class FakeHandle:
    """形状对齐 singbox_engine._Handle：node_id/proc/config_dir/log_path。"""

    def __init__(self, node_id: str, proc: FakeProc, config_dir: str):
        self.node_id = node_id
        self.proc = proc
        self.config_dir = config_dir
        self.log_path = os.path.join(config_dir, "sing-box.log")
        self.stopped = False


class FakeEngine:
    """可注入假引擎。

    behavior:
      normal       启动成功，stop 干净（进程死、目录删）
      start_fail   start() 抛 FaultEngineError（注入文案点名成因）
      mid_death    启动成功，可经 handle.proc.die() 模拟中途退出
      stop_residue 启动成功，stop() 模拟 _terminate 吞错：进程拒 terminate
                   活下来、句柄照常摘除——复现 DESIGN GAP-E1 的残留链
    """

    name = "fake-engine"

    def __init__(self, behavior: str = "normal", base_dir: str = None,
                 socks_port: int = TEST_SOCKS_PORT,
                 http_port: int = TEST_HTTP_PORT):
        if behavior not in ("normal", "start_fail", "mid_death",
                            "stop_residue"):
            raise ValueError(f"未知假引擎行为: {behavior!r}")
        self.behavior = behavior
        self._base_dir = base_dir or tempfile.mkdtemp(prefix="work15-engines-")
        self.socks_port = socks_port
        self.http_port = http_port
        self._live_handles = []
        self._pid_seq = 40000
        self.start_calls = 0
        self.stop_calls = 0

    @property
    def live_count(self) -> int:
        return len(self._live_handles)

    def start(self, node, on_event=None):
        self.start_calls += 1
        if self.behavior == "start_fail":
            raise FaultEngineError(
                f"假引擎启动失败注入: 节点 {getattr(node, 'id', node)} "
                f"(behavior=start_fail)")
        self._pid_seq += 1
        config_dir = tempfile.mkdtemp(prefix="fake-engine-", dir=self._base_dir)
        proc = FakeProc(self._pid_seq)
        if self.behavior == "stop_residue":
            # 复现 GAP-E1：stop/terminate 吞错 → 进程拒 terminate 活下来
            proc.refuse_terminate = True
        handle = FakeHandle(str(getattr(node, "id", node) if not
                                isinstance(node, dict) else node["id"]),
                            proc, config_dir)
        self._live_handles.append(handle)
        if on_event is not None:
            try:
                on_event({"kind": "connected", "detail": "fake-proxy-url"})
            except Exception:
                pass                # 对齐真引擎：回调异常不弄丢已就绪会话
        return handle

    def status(self, handle) -> str:
        """诚实汇报（对齐 singbox_engine.status 的 poll 口径）。"""
        proc = getattr(handle, "proc", None) if handle is not None else None
        if proc is None:
            return "stopped"
        return "running" if proc.poll() is None else "stopped"

    def stop(self, handle) -> None:
        if handle is None:
            return
        self.stop_calls += 1
        self._live_handles = [h for h in self._live_handles
                              if h is not handle]
        proc = getattr(handle, "proc", None)
        if proc is not None:
            proc.terminate()        # stop_residue: refuse_terminate → 仍活
        handle.stopped = True
        if handle.config_dir:
            # 真引擎 stop 总会删临时目录（含凭证），残留只在进程侧
            shutil.rmtree(handle.config_dir, ignore_errors=True)
            handle.config_dir = ""

    def kill_active(self):
        """测试用：把当前活动句柄的进程弄死（模拟运行中途退出）。"""
        for h in self._live_handles:
            h.proc.die()


# ---------------------------------------------------------------------------
# 假 probe（形状对齐 core/verify.py 的 ProbeResult/ProbeFn）
# ---------------------------------------------------------------------------
class FakeProbeResult:
    """verify.ProbeResult 的最小同形替身（本模块自用字段）。"""

    def __init__(self, alive=False, exit_ip=None, error="",
                 latency_ms=None, error_kind=None):
        self.alive = alive
        self.exit_ip = exit_ip
        self.error = error
        self.latency_ms = latency_ms
        self.error_kind = error_kind    # DESIGN GAP-N1 的期望分级字段


class FakeProbe:
    """behavior: normal / timeout / parse_fail / raise。

    timeout / parse_fail 返回 alive=False 的结果（对齐真实 probe 的
    现状形状：失败是返回值不是异常）；raise 注入「probe 抛错」——
    复现 DESIGN GAP-E2 的编排层缺口（替身按期望口径接住它）。
    exit_ip 用 RFC 5737 保留段假 IP（TEST-NET-3），无真实节点数据。
    """

    def __init__(self, behavior: str = "normal",
                 exit_ip: str = "203.0.113.7"):
        if behavior not in ("normal", "timeout", "parse_fail", "raise"):
            raise ValueError(f"未知假 probe 行为: {behavior!r}")
        self.behavior = behavior
        self.exit_ip = exit_ip
        self.calls = 0

    def __call__(self, node):
        self.calls += 1
        if self.behavior == "raise":
            raise RuntimeError("注入: probe 抛错（非返回失败）")
        if self.behavior == "timeout":
            return FakeProbeResult(
                alive=False,
                error="存活探针全灭：链路不通（注入: 探测超时）",
                error_kind="timeout")
        if self.behavior == "parse_fail":
            return FakeProbeResult(
                alive=False,
                error="取不到出口 IP（注入: 解析失败）",
                error_kind="parse")
        return FakeProbeResult(alive=True, exit_ip=self.exit_ip,
                               latency_ms=120.0)


# ---------------------------------------------------------------------------
# 会话编排替身（形状对齐 core/orchestrator.py，实现 DESIGN §1 期望口径）
# ---------------------------------------------------------------------------
class TwinError(Exception):
    """会话层可预期失败（对齐 OrchestratorError 的角色）。"""


@dataclass
class TwinEvent:
    kind: str
    node_id: str = ""
    detail: str = ""
    ts: float = 0.0


@dataclass
class TwinSample:
    node_id: str
    kind: str
    success: bool
    detail: str = ""


class TwinStore:
    """最小样本/节点落点替身（内存版，形状对齐 store+history.record）。"""

    def __init__(self):
        self.nodes = {}
        self.samples = []

    def get_node(self, node_id):
        return self.nodes.get(node_id)

    def upsert_node(self, node):
        self.nodes[node["id"]] = node

    def add_sample(self, sample):
        self.samples.append(sample)


class FakeNode(dict):
    """假节点：属性访问兼容 dict 两种用法。"""

    def __getattr__(self, name):
        try:
            return self[name]
        except KeyError as exc:
            raise AttributeError(name) from exc


def make_node(node_id="node-1", protocol="fake-proto", banned=False,
              host="198.51.100.7", port=443, name="假节点"):
    """RFC 5737 假节点（TEST-NET-2），无真实节点数据。"""
    return FakeNode(id=node_id, name=name, protocol=protocol,
                    banned=banned, host=host, port=port)


class SessionTwin:
    """会话编排替身——与 orchestrator.py 同形状，实现 DESIGN §1 期望口径：

    - connect：预检（双连接拒绝/ban/无引擎）显式 TwinError；引擎启动失败 →
      落样本 + error 事件、保持 IDLE；verifier **任何异常都接住**（GAP-E2
      的期望侧）→ teardown + error 事件，绝不留半开隧道；verify 失败 →
      teardown + verify_failed；成功 → CONNECTED + connected 事件。
    - poll：引擎死亡 → teardown + dropped（「已停在原地，不会自动切换」），
      再 poll 返回 None；**没有任何自动重连/切换路径**。
    - disconnect / _teardown_engine：stop 后**显式核验进程真死**（GAP-E1
      的期望侧）：残留 → 记 self.residues + 发 error 事件亮告警。
    """

    IDLE = "idle"
    CONNECTED = "connected"

    # 铁律反向断言锚：替身公开面上不允许出现这些名字
    FORBIDDEN_METHODS = ("connect_best", "connect_next", "auto_switch",
                         "auto_reconnect", "auto_connect", "switch_to")

    def __init__(self, store=None, engines=None, verifier=None):
        self.store = store or TwinStore()
        self.engines = dict(engines or {})
        self.verifier = verifier
        self.state = self.IDLE
        self.active_node_id = None
        self._engine = None
        self._handle = None
        self.events = []
        self.residues = []          # stop 后进程残留的显式记录（期望口径）

    # ---------------- 事件/样本 ----------------
    def _emit(self, kind, node_id="", detail=""):
        ev = TwinEvent(kind=kind, node_id=node_id, detail=detail,
                       ts=time.time())
        self.events.append(ev)
        return ev

    def _record(self, node_id, kind, success, detail=""):
        self.store.add_sample(TwinSample(node_id=node_id, kind=kind,
                                         success=success, detail=detail))

    def events_of(self, kind):
        return [e for e in self.events if e.kind == kind]

    # ---------------- 内部 ----------------
    def _teardown_engine(self):
        if self._engine is None or self._handle is None:
            self._engine = None
            self._handle = None
            return
        engine, handle = self._engine, self._handle
        self._engine = None
        self._handle = None
        try:
            engine.stop(handle)
        except Exception as exc:            # stop 自身抛错也必须外显
            self._emit("error", handle.node_id,
                       f"引擎 stop 失败: {exc}")
        # 期望口径（GAP-E1 期望侧）：stop 后核验进程真死，残留必须亮告警
        try:
            alive = engine.status(handle) == "running"
        except Exception:
            alive = False
        if alive:
            residue = {
                "node_id": getattr(handle, "node_id", ""),
                "pid": getattr(getattr(handle, "proc", None), "pid", None),
                "socks_port": getattr(engine, "socks_port", None),
            }
            self.residues.append(residue)
            self._emit("error", residue["node_id"],
                       "引擎 stop 后进程仍存活（残留）——"
                       f"pid {residue['pid']}，端口 {residue['socks_port']}；"
                       "已停在原地，请手动处理（不会自动重连/切换）")

    # ---------------- 对外 ----------------
    def status(self):
        return {"state": self.state, "active_node_id": self.active_node_id}

    def connect(self, node_id) -> TwinEvent:
        if self.state != self.IDLE:
            raise TwinError(
                f"已有活动连接（{self.active_node_id}），先断开再连别的节点")
        node = self.store.get_node(node_id)
        if node is None:
            raise TwinError(f"节点不存在: {node_id}")
        if node.get("banned"):
            raise TwinError(f"节点已被拉黑（ban），拒绝连接: {node_id}")
        engine = self.engines.get(node.get("protocol"))
        if engine is None:
            raise TwinError(f"协议 {node.get('protocol')} 暂无引擎: {node_id}")

        self._emit("connect_started", node_id)
        try:
            handle = engine.start(node)
        except FaultEngineError as exc:
            self._record(node_id, "connect", False, detail=str(exc))
            return self._emit("error", node_id, f"引擎启动失败: {exc}")
        self._engine, self._handle = engine, handle
        self._emit("engine_started", node_id)

        # 期望口径（GAP-E2 期望侧）：verifier 任何异常都接住——teardown、
        # 回 IDLE、显式 error 事件，绝不让隧道半开
        try:
            outcome = self.verifier(node)
        except Exception as exc:
            self._teardown_engine()
            self.state = self.IDLE
            self.active_node_id = None
            self._record(node_id, "connect", False, detail=f"验出口异常: {exc}")
            return self._emit("error", node_id, f"验出口异常，已断开: {exc}")
        if not outcome.alive or not outcome.exit_ip:
            self._teardown_engine()
            self.state = self.IDLE
            self.active_node_id = None
            reason = outcome.error or "链路不通或取不到出口 IP"
            self._record(node_id, "connect", False, detail=f"验出口失败: {reason}")
            return self._emit("verify_failed", node_id,
                              f"已断开（防假连）：{reason}")
        self.state = self.CONNECTED
        self.active_node_id = node_id
        self._record(node_id, "connect", True,
                     detail=f"出口 {outcome.exit_ip}")
        return self._emit("connected", node_id, f"出口 {outcome.exit_ip}")

    def disconnect(self) -> TwinEvent:
        if self.state == self.IDLE:
            return self._emit("disconnected", "", "本来就没有活动连接")
        node_id = self.active_node_id or ""
        self._teardown_engine()
        self.state = self.IDLE
        self.active_node_id = None
        self._record(node_id, "disconnect", True, detail="用户断开")
        return self._emit("disconnected", node_id)

    def poll(self):
        """巡检：引擎死亡只上报 dropped 并回 IDLE，**绝不自动重连/切换**。"""
        if self.state != self.CONNECTED or self._engine is None:
            return None
        if self._engine.status(self._handle) == "running":
            return None
        node_id = self.active_node_id or ""
        self._teardown_engine()
        self.state = self.IDLE
        self.active_node_id = None
        self._record(node_id, "drop", False, detail="引擎进程已退出")
        return self._emit("dropped", node_id,
                          "节点掉线，已停在原地（不会自动切换）")


# ---------------------------------------------------------------------------
# 健康分级替身（形状对齐 core/health.py connection_health_detail）
# ---------------------------------------------------------------------------
HEALTH_OK = "ok"
HEALTH_SUSPECT = "suspect"
HEALTH_DEAD = "dead"
HEALTH_UNKNOWN = "unknown"
SUSPECT_FAILS = 3               # 对齐 health.py:340
DEAD_FAILS = 5                  # 对齐 health.py:341


def twin_health_detail(samples) -> dict:
    """health.py 同形替身：末尾连败 3→suspect、5→dead；坏样本当场抛错。"""
    rows = list(samples)
    if not rows:
        return {"status": HEALTH_UNKNOWN, "fail_streak": 0,
                "sample_count": 0, "last_ok": None}
    oks = []
    for row in rows:                    # 坏样本当场暴露，不许静默当成败
        if isinstance(row, dict):
            value = row.get("ok", None)
        else:
            value = getattr(row, "ok", None)
        if type(value) is not bool:
            raise ValueError(f"样本缺 ok 字段或非 bool: {row!r}")
        oks.append(value)
    streak = 0
    for ok in reversed(oks):
        if ok:
            break
        streak += 1
    if streak >= DEAD_FAILS:
        status = HEALTH_DEAD
    elif streak >= SUSPECT_FAILS:
        status = HEALTH_SUSPECT
    else:
        status = HEALTH_OK
    return {"status": status, "fail_streak": streak,
            "sample_count": len(rows), "last_ok": oks[-1]}


def twin_check_ports_free(ports, bind_host="127.0.0.1"):
    """health.check_ports_free 的最小同形替身（REUSEADDR bind 探测）。
    返回被占端口清单 [{"port":...}]；空列表=全空闲。"""
    occupied = []
    for port in ports:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((bind_host, int(port)))
        except OSError:
            occupied.append({"port": int(port)})
        finally:
            sock.close()
    return occupied


# ---------------------------------------------------------------------------
# DB 损坏工具（临时目录里造真损坏文件，按 store.py 打开口径探现状）
# ---------------------------------------------------------------------------
SQLITE_HEADER_MAGIC = b"SQLite format 3\x00"

_STORE_LIKE_SCHEMA = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY, name TEXT NOT NULL, extra TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS verify_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT NOT NULL, ts REAL);
"""


def make_db(path, rows: int = 5) -> None:
    """造一个带数据的真 SQLite 库（模拟 store.py 形状的两张表）。"""
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_STORE_LIKE_SCHEMA)
        for i in range(rows):
            conn.execute(
                "INSERT INTO nodes (id, name) VALUES (?, ?)",
                (f"node-{i}", f"节点{i}"))
        conn.commit()
    finally:
        conn.close()


def corrupt_truncate(path, keep_bytes: int = 0) -> None:
    """把库文件截断（默认清成 0 字节）。"""
    with open(path, "rb") as f:
        data = f.read()
    with open(path, "wb") as f:
        f.write(data[:keep_bytes])


def corrupt_header(path, garbage: bytes = b"NOTSQLITE-GARBAGE-HEAD") -> None:
    """覆写库文件头部（保留文件长度，只换前缀）。"""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < len(garbage):
        garbage = garbage[:len(data)] or b"X"
    with open(path, "wb") as f:
        f.write(garbage + data[len(garbage):])


def corrupt_middle(path, offset: int = 4096) -> None:
    """覆写库文件中段一页（页级损坏，头部完好）。"""
    with open(path, "rb") as f:
        data = bytearray(f.read())
    if offset >= len(data):
        offset = len(data) // 2
    for i in range(256):
        if offset + i < len(data):
            data[offset + i] = 0xFF
    with open(path, "wb") as f:
        f.write(bytes(data))


def open_behavior(path):
    """按 store.py 的打开口径（connect + executescript(IF NOT EXISTS) +
    首查询）探测现状行为，返回结构化结果：

    {"opened": bool, "stage": "schema"|"query"|"-", "error": str|None,
     "node_count": int|None, "header_magic_ok": bool}

    对齐 store.py:68-80：connect 本身惰性打开，炸在 executescript 或
    首条查询；0 字节文件会被 SQLite 当合法新库静默打开（GAP-D1）。
    """
    with open(path, "rb") as f:
        head = f.read(len(SQLITE_HEADER_MAGIC))
    result = {"opened": False, "stage": "-", "error": None,
              "node_count": None,
              "header_magic_ok": head == SQLITE_HEADER_MAGIC}
    conn = None
    try:
        conn = sqlite3.connect(path)
        conn.executescript(_STORE_LIKE_SCHEMA)      # 对齐 _SCHEMA 的 IF NOT EXISTS
        result["stage"] = "query"
        cur = conn.execute("SELECT COUNT(*) FROM nodes")
        result["node_count"] = cur.fetchone()[0]
        result["opened"] = True
    except sqlite3.Error as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if conn is not None:
            conn.close()
    return result


# ---------------------------------------------------------------------------
# 缺口登记（与 DESIGN.md §2.4 同源；测试反向断言 DESIGN 如实记录）
# ---------------------------------------------------------------------------
GAPS = (
    {"id": "GAP-E1", "category": "engine",
     "title": "stop/terminate 失败被三处吞 → 残留进程无人认领",
     "owner": "引擎段+编排段", "severity": "中"},
    {"id": "GAP-E2", "category": "engine",
     "title": "verifier/probe 抛错未捕 → 半开隧道 + 原始异常冒出",
     "owner": "编排段", "severity": "高"},
    {"id": "GAP-E3", "category": "engine",
     "title": "引擎事件回调异常吞（有注释豁免，宜补日志）",
     "owner": "引擎段", "severity": "低"},
    {"id": "GAP-D1", "category": "db",
     "title": "0 字节截断库 → 静默新建空库覆盖，数据无声丢失",
     "owner": "存储段", "severity": "高"},
    {"id": "GAP-D2", "category": "db",
     "title": "覆写文件头 → DatabaseError 原样冒出构造点（CLI/GUI 无捕）",
     "owner": "接线段+存储段", "severity": "高"},
    {"id": "GAP-D3", "category": "db",
     "title": "无 schema 版本/迁移机制，schema 演进炸在日后查询期",
     "owner": "存储段", "severity": "中"},
    {"id": "GAP-D4", "category": "db",
     "title": "无完整性自检/修复路径（页级损坏日后炸原始错）",
     "owner": "存储段", "severity": "中"},
    {"id": "GAP-N1", "category": "network",
     "title": "探测失败原因不分级（超时/DNS/不可达折叠一句话）",
     "owner": "健康段", "severity": "中"},
    {"id": "GAP-N2", "category": "network",
     "title": "muse_code=0 标签误用「链路死」（alive=True 语境误导）",
     "owner": "健康段", "severity": "低"},
)


# ---------------------------------------------------------------------------
# client-dev 现状文件锚点（防编造：断言落点存在，不 import client 包）
# ---------------------------------------------------------------------------
# client-dev 根目录：由本文件位置推导（本文件在 client-dev/client/core/ 内时
# 即 client-dev 根；VM 上与旧默认值同路径，本机上即 ~/gate-client）。
# GATE_CLIENT_DEV_ROOT 环境变量仍可覆盖（见 client_root()）。
DEFAULT_CLIENT_ROOT = str(Path(__file__).resolve().parents[2])

# 相对路径 → 必须存在的锚点子串（0.17.0 快照实测；漂移以函数名为准）
FILE_ANCHORS = {
    "client/engines/singbox_engine.py": (
        "class SingBoxEngine",
        "def start(self, node: Node, on_event=None)",
        "def _fail_start(self, handle: _Handle, reason: str) -> None",
        "def stop(self, handle) -> None",
        "def status(self, handle) -> str",
        # E1 达标锚：启动失败显式报错链
        "_missing_binary_error(explicit)",
        "引擎端口被占用，启动被挡",
        "等待本地端口",
        "sing-box 启动失败",
        # GAP-E1 现状锚：_terminate 二次超时被宽 except 吞
        "except (OSError, subprocess.TimeoutExpired, ValueError):",
        # 孤儿清扫三层兜底 + owner 活着不碰（GAP-E1 现状链）
        "def reap_stale_engines(cls, root: str = None) -> int",
        "def _reap_one(cls, config_dir: str) -> bool",
        "所有者活着，不是孤儿",
        "atexit.register(self._stop_all_live)",
        # E5 豁免锚：回调异常吞、有注释
        "回调异常不能弄丢已就绪的隧道",
    ),
    "client/core/orchestrator.py": (
        "class Orchestrator",
        # E2 达标锚：dropped 只上报不自动连
        "节点掉线，已停在原地（不会自动切换）",
        "**不做任何自动重连/自动切换**——选下一个节点是用户的动作",
        # GAP-E2 现状锚：connect 只捕 EngineError、verifier 调用无 try
        "except EngineError as exc:",
        "outcome = self.verifier(node)",
        # teardown 吞 stop 异常（GAP-E1 现状链）
        "except Exception:\n                pass",
    ),
    "client/core/verify.py": (
        "def verify_exit(node: Node, probe: ProbeFn",
        # GAP-N1 现状锚：_fetch 宽 except 吞掉超时/DNS/不可达的差别
        "except Exception:\n        return 0, \"\"",
        "存活探针全灭：链路不通",
        # GAP-N2 现状锚：code 0 → 「链路死」标签
        "0:   (False, \"链路死\", \"节点不通（面板延迟数字不可信）\")",
        # N3 豁免锚：ip-api 失败降级未知（12 空格缩进的 pass，全文件唯一）
        "except Exception:\n            pass",
        # 达标锚：失败不覆盖旧画像
        "失败时不覆盖旧画像",
    ),
    "client/core/store.py": (
        "class Store",
        # GAP-D1 现状锚：IF NOT EXISTS + 打开即建表，无校验
        "CREATE TABLE IF NOT EXISTS",
        "self._conn.executescript(_SCHEMA)",
        # GAP-D3 现状锚：全文无版本机制（整文件不含 user_version，
        # 该断言在 test_faults.py 用「不存在子串」反向做）
        "def delete_node(self, node_id: str) -> bool",
    ),
    "client/core/health.py": (
        "def check_ports_free(ports, bind_host: str = \"127.0.0.1\") -> list",
        "SUSPECT_FAILS = 3",
        "DEAD_FAILS = 5",
        "def connection_health_detail(samples) -> dict",
        "样本缺 ok 字段",
        # N5 达标锚：判不了不硬判
        "跳过冲突判读",
    ),
    "client/ui/cli.py": (
        # GAP-D2 现状锚：构造点无捕获（task24 §8-3 落页后构造点改为
        # resolve_db_path 四档解析，「无 try 包裹」现状不变，锚随形态更新）
        "Store(resolve_db_path(explicit_db=args.db",
        # E2 边界锚：CLI 单次进程形态，连完即断
        "避免留下孤儿隧道进程",
    ),
    "client/ui/app.py": (
        # GAP-D2 现状锚：GUI 构造点无捕获（task24 §8-3 落页后同上）
        "Store(resolve_db_path(explicit_db=args.db",
        # 15s 巡检与 10 分钟自动探针节拍（长跑设计对照）
        "_maybe_health_probe",
        "_maybe_auto_probe",
        "自动探测设置",
    ),
    "client/engines/base.py": (
        "class EngineError(Exception)",
        '"running"  隧道已建立且进程存活',
    ),
    "client/VERSION": (
        # 版本针=当前 client/VERSION 实读（REVIEW_FAIL 适配 4：随集成推进
        # 同步改针，防漂移探测器失效；task15 的审计基线 0.17.0 仍由
        # DESIGN §0 声明，与针值无关）。假数据默认值不在本区块——按
        # 裁决不动（见 _DOC_NETS 按类放行，build_deb.py 修复副本）。
        "1.3.0",
    ),
}

# 反向锚：这些文件里**不允许出现**的子串（现状=缺失即是现状证据）
FILE_NEGATIVE_ANCHORS = {
    "client/core/store.py": (
        "user_version",          # GAP-D3：无 schema 版本机制
        "integrity_check",       # GAP-D4：无完整性自检
        "quick_check",
    ),
    "client/core/orchestrator.py": (
        # 铁律：编排层不存在自动切换/自动重连的**方法定义**（负向锚只锚
        # def 行——docstring 里「故意不提供 connect_best / …」的声明文字
        # 本身包含这些词，不能整词禁现）
        "def connect_best",
        "def connect_next",
        "def auto_switch",
        "def auto_reconnect",
        "def switch_to",
    ),
}


def client_root() -> str:
    """client-dev 根目录（GATE_CLIENT_DEV_ROOT 可覆盖，缺省 ~/workspace/...）。"""
    return os.path.expanduser(
        os.environ.get("GATE_CLIENT_DEV_ROOT", DEFAULT_CLIENT_ROOT))


def iter_anchor_failures(root: str = None):
    """逐条核对 FILE_ANCHORS / FILE_NEGATIVE_ANCHORS，
    产出 (相对路径, 锚点子串, 是否负向锚, 错误说明)；无错产出空。"""
    root = root or client_root()
    for rel, needles in FILE_ANCHORS.items():
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            for needle in needles:
                yield (rel, needle, False, "文件不存在")
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        for needle in needles:
            if needle not in text:
                yield (rel, needle, False, "锚点子串不存在（现状漂移或编造）")
    for rel, needles in FILE_NEGATIVE_ANCHORS.items():
        path = os.path.join(root, rel)
        if not os.path.isfile(path):
            for needle in needles:
                yield (rel, needle, True, "文件不存在")
            continue
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
        for needle in needles:
            if needle in text:
                yield (rel, needle, True, "禁现子串出现（现状漂移：该缺失的出现了）")


# 集成适配（work15 原件 → client-dev 集成版的全部改动，审查方留痕）：
# 1. DEFAULT_CLIENT_ROOT 改由本文件位置推导（REVIEW_FAIL 适配 4）：
#    本文件在 client-dev/client/core/ 内时 parents[2] 即 client-dev 根
#    （VM 上与旧默认值同路径；本机上即 ~/gate-client）；GATE_CLIENT_DEV_ROOT
#    仍可覆盖。work15/ 原型副本保留旧写死默认值。
# 2. FILE_ANCHORS 的 client/VERSION 针为 1.3.0（0.31.0 task27、0.32.0 task29、0.33.0 task28、0.34.0 task30、0.35.0 task31、1.0.0 task32 门禁收口、1.1.0 task33 名单复查收尾、1.2.0 task34 真实种子源清单落地、1.3.0 task35 规则集来源与测速地址核定落地逐轮推进，针随最终版走）（集成版本号，适配 4）。
# 其余与原件逐行一致。
