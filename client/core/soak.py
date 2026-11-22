#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
长跑稳定性脚本原型（soak.py）—— task15
========================================
DESIGN.md §3 的落地：循环动作集（连接/断开、批量测速、自动探测节奏模拟）
+ 监控指标（子进程数、fd 数、内存 RSS、DB 文件大小）+ 判据（轮数跑满、
子进程/fd 不增长、DB 有界增长），假引擎干跑必须跑满设定轮数；真引擎模式
本段**只写入口与判读口径、不真跑**（退出码 3）。

纯标准库；假世界来自同目录 faults.py（同为标准库，不 import client 包）。
不连外网：假引擎不起真进程、假 probe 不发真请求。

用法：
    python3 soak.py [--rounds N] [--interval SEC] [--mode fake|real]
                    [--db PATH] [--report PATH] [--speed-test-every N]
                    [--auto-probe-every N] [--db-max-growth-bytes N]

退出码：0=判据全过；1=判据失败；2=参数错；3=真引擎模式本段拒绝执行。

判读口径（真跑手册）见 DESIGN.md §3.5；真跑前置条件：本机真 sing-box、
用户点头的一次真连接、冷门测试端口（29311 段）、≥2 小时时长建议。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

try:
    from client.core import faults
except ImportError:  # 裸跑（work15/ 原型）：同目录 faults.py
    import faults

SOAK_VERSION_TAG = "work15-soak-prototype-0.17.0"


# ---------------------------------------------------------------------------
# 指标采集（Linux 只读 /proc，纯标准库；不可用时返回 None 并在判据里降级）
# ---------------------------------------------------------------------------
def count_child_processes() -> int:
    """扫 /proc 找 ppid == 本进程 的子进程数（孤儿引擎指标）。"""
    my_pid = os.getpid()
    count = 0
    try:
        entries = [e for e in os.listdir("/proc") if e.isdigit()]
    except OSError:
        return -1                          # 非 Linux/读不到：判据降级
    for entry in entries:
        try:
            with open(f"/proc/{entry}/stat", "r", encoding="utf-8") as f:
                # stat 第二字段是 (comm)，可能含空格——以最后一个 ) 切开
                tail = f.read().rsplit(")", 1)[1].split()
            ppid = int(tail[1])
        except (OSError, ValueError, IndexError):
            continue
        if ppid == my_pid:
            count += 1
    return count


def count_fds() -> int:
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return -1


def read_rss_kb() -> int:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])     # kB
    except (OSError, ValueError, IndexError):
        pass
    return -1


def sample_metrics(db_path: str) -> dict:
    return {
        "children": count_child_processes(),
        "fds": count_fds(),
        "rss_kb": read_rss_kb(),
        "db_size": os.path.getsize(db_path) if os.path.exists(db_path) else 0,
        "ts": time.time(),
    }


def metric_summary(samples) -> dict:
    """指标序列 → first/last/max/增长（None 值指标原样带过）。"""
    summary = {}
    for key in ("children", "fds", "rss_kb", "db_size"):
        values = [s[key] for s in samples if s[key] is not None and s[key] >= 0]
        if not values:
            summary[key] = {"first": None, "last": None, "max": None,
                            "growth": None, "available": False}
            continue
        summary[key] = {
            "first": values[0], "last": values[-1], "max": max(values),
            "growth": values[-1] - values[0], "available": True}
    return summary


# ---------------------------------------------------------------------------
# 假世界干跑
# ---------------------------------------------------------------------------
def dry_run(args) -> dict:
    """假引擎干跑：跑满 --rounds 轮，输出指标与判据（DESIGN §3.2-§3.4）。"""
    os.makedirs(os.path.dirname(os.path.abspath(args.db)), exist_ok=True)
    action_counts = {"connect_ok": 0, "connect_fail": 0, "verify_fail": 0,
                     "disconnect": 0, "dropped": 0, "speed_test": 0,
                     "auto_probe": 0, "health_samples": 0}

    engine = faults.FakeEngine(behavior="normal")
    probe = faults.FakeProbe(behavior="normal")
    soak_db = SoakSampleDb(args.db)     # 真实 sqlite 文件：DB 体积指标有意义
    pool = []
    for i in range(args.pool):
        node_id = f"soak-node-{i}"
        soak_db.upsert_node(faults.make_node(node_id, "fake-proto",
                                             host="198.51.100.7"))
        pool.append(node_id)
    twin = faults.SessionTwin(store=soak_db, engines={"fake-proto": engine},
                              verifier=probe)

    samples = [sample_metrics(args.db)]     # 基线
    rounds_run = 0
    for round_no in range(1, args.rounds + 1):
        # ① 连接/断开循环（DESIGN §3.2-1，对应真机 15s 巡检节拍）
        node_id = pool[(round_no - 1) % len(pool)]
        ev = twin.connect(node_id)
        if ev.kind == "connected":
            action_counts["connect_ok"] += 1
            # 健康采样 1 次（假世界全是成功样本，分级恒 ok）
            soak_db.add_health_sample(node_id, ok=True)
            action_counts["health_samples"] += 1
        elif ev.kind in ("error", "verify_failed"):
            action_counts["connect_fail" if ev.kind == "error" else
                          "verify_fail"] += 1
        # ② 批量测速（每 speed-test-every 轮：全库各探一次）
        if round_no % args.speed_test_every == 0:
            for pid in pool:
                probe(faults.make_node(pid, "fake-proto"))
                soak_db.add_verify_sample(pid)
            action_counts["speed_test"] += 1
        # ③ 自动探测节奏模拟（每 auto-probe-every 轮，对应 10 分钟档）
        if round_no % args.auto_probe_every == 0:
            for pid in pool:
                probe(faults.make_node(pid, "fake-proto"))
                soak_db.add_verify_sample(pid)
            action_counts["auto_probe"] += 1
        # ①续：断开 + 掉线巡检反向断言（真跑里每轮 poll，此处同样断言）
        if twin.state == faults.SessionTwin.CONNECTED:
            twin.disconnect()
            action_counts["disconnect"] += 1
        dropped = twin.poll()
        if dropped is not None:
            action_counts["dropped"] += 1
        rounds_run = round_no
        samples.append(sample_metrics(args.db))
        if args.interval > 0:
            time.sleep(args.interval)

    soak_db.close()
    criteria = evaluate_criteria(args, rounds_run, samples)
    return {"mode": "fake", "rounds_requested": args.rounds,
            "rounds_run": rounds_run, "actions": action_counts,
            "metrics": metric_summary(samples), "criteria": criteria,
            "residues": twin.residues, "pool_size": len(pool),
            "stdout": ""}


def evaluate_criteria(args, rounds_run: int, samples) -> dict:
    """DESIGN §3.4 判据：轮数跑满 / 子进程不增长 / fd 不增长 / DB 有界。"""
    summary = metric_summary(samples)
    children = summary["children"]
    fds = summary["fds"]
    db = summary["db_size"]
    db_limit = (args.db_max_growth_bytes if args.db_max_growth_bytes
                else max(65536, 1024 * args.rounds))
    criteria = {
        "rounds_complete": {
            "pass": rounds_run >= args.rounds,
            "detail": f"rounds_run={rounds_run}/{args.rounds}"},
        "subprocess_leak": {
            "pass": (not children["available"]) or children["max"] == 0,
            "detail": (f"max_children={children['max']}"
                       if children["available"] else
                       "子进程计数不可用（/proc 缺失），判据降级")},
        "fd_leak": {
            "pass": (not fds["available"]) or fds["growth"] <= 2,
            "detail": (f"first={fds['first']} last={fds['last']} "
                       f"growth={fds['growth']}"
                       if fds["available"] else
                       "fd 计数不可用（/proc 缺失），判据降级")},
        "db_growth": {
            "pass": (not db["available"]) or db["growth"] <= db_limit,
            "detail": (f"growth={db['growth']}B limit={db_limit}B"
                       if db["available"] else "DB 指标不可用，判据降级")},
    }
    return criteria


class SoakSampleDb(faults.TwinStore):
    """soak 自己的样本落点（真实 sqlite 文件，DB 体积指标的对象）。

    继承 TwinStore 的节点/样本接口（SessionTwin 的依赖形状），样本落
    sqlite 而非内存——干跑需要「每轮往 DB 写几条样本」的真实体积语义。
    注意：这不是 client-dev 的 store.py——表结构自管，与 client 库无关。"""

    def __init__(self, path: str):
        super().__init__()
        import sqlite3
        self._conn = sqlite3.connect(path)
        self._conn.executescript(
            "CREATE TABLE IF NOT EXISTS soak_samples ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, node_id TEXT, ts REAL,"
            " kind TEXT, success INTEGER)")

    def add_sample(self, sample) -> None:
        self._conn.execute(
            "INSERT INTO soak_samples (node_id, ts, kind, success) "
            "VALUES (?, ?, ?, ?)",
            (sample.node_id, time.time(), sample.kind, int(sample.success)))
        self._conn.commit()

    def add_verify_sample(self, node_id: str) -> None:
        self.add_sample(faults.TwinSample(node_id=node_id, kind="verify",
                                          success=True))

    def add_health_sample(self, node_id: str, ok: bool) -> None:
        self.add_sample(faults.TwinSample(node_id=node_id, kind="health",
                                          success=ok))

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# 真引擎模式（本段只写入口与判读口径，不真跑）
# ---------------------------------------------------------------------------
def real_mode_notice(args) -> str:
    return """真引擎长跑（--mode real）本段不真跑（设计 + 干跑原型交付）。

本模式只校验入口与判读口径，未启动任何引擎、未发起任何连接。

真跑手册（DESIGN.md §3.5，留本机集成段执行）：
  前置：本机真 sing-box（GATE_CLIENT_SINGBOX_BIN 或 ~/.local/bin/sing-box），
        用户点头的一次真连接，测试端口用冷门段 29311-29329
        （避开 7897/7898、10818-10829、18089-18095），磁盘余量。
  动作集：连接/断开循环 + 每 N 轮批量测速 + 自动探测节奏模拟，
        每轮 poll() 反向断言不自动重连/切换（铁律）。
  判读口径：
    - 子进程数：≤ 活动会话数（循环里 ≤1，断开态=0）；连续 2 轮 >0
      判孤儿泄漏（0.9.0/0.10.0 真机事故口径）。
    - fd：对数期后持平（增速≈0 判稳）；持续线性增长判泄漏。
    - RSS：先跑 2 小时取基线，上限待基线定（先观察不硬判）。
    - DB：同干跑有界判据（--db-max-growth-bytes），每轮样本几百字节。
    - sing-box 日志文件大小一并记录（不判；日志轮转另行评估）。
  时长：建议 ≥2 小时（覆盖 ≥12 个 10 分钟自动探测节拍）。
  报告：stdout 人话小结 + --report JSON，与本干跑同 schema。
"""


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="soak.py", description="长跑稳定性脚本（task15 原型）")
    ap.add_argument("--rounds", type=int, default=50,
                    help="干跑轮数（默认 50；判据要求跑满）")
    ap.add_argument("--interval", type=float, default=0.05,
                    help="轮间睡眠秒数（真跑拉长，如 15 对应巡检节拍）")
    ap.add_argument("--mode", choices=("fake", "real"), default="fake",
                    help="fake=假引擎干跑（本段默认）；real=真引擎（本段拒绝执行）")
    ap.add_argument("--db", default=os.path.join(tempfile_dir(),
                                                 "work15-soak.db"),
                    help="soak 自用样本库路径（DB 体积指标对象）")
    ap.add_argument("--report", default="",
                    help="JSON 报告输出路径（默认只打印 stdout）")
    ap.add_argument("--speed-test-every", type=int, default=10,
                    help="每 N 轮做一次全库批量测速（默认 10）")
    ap.add_argument("--auto-probe-every", type=int, default=10,
                    help="每 N 轮模拟一次自动探测节拍（默认 10）")
    ap.add_argument("--db-max-growth-bytes", type=int, default=0,
                    help="DB 增长上限字节（默认 max(65536, 1024*rounds)）")
    ap.add_argument("--pool", type=int, default=5,
                    help="假节点池大小（默认 5，全是 RFC 5737 假数据）")
    return ap


def tempfile_dir() -> str:
    import tempfile
    return tempfile.gettempdir()


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.rounds < 1 or args.pool < 1 or args.interval < 0 \
            or args.speed_test_every < 1 or args.auto_probe_every < 1:
        print("[错误] 参数非法：rounds/pool ≥1，interval ≥0，"
              "speed-test-every/auto-probe-every ≥1", file=sys.stderr)
        return 2
    if args.mode == "real":
        print(real_mode_notice(args))
        return 3

    result = dry_run(args)
    failed = [name for name, c in result["criteria"].items() if not c["pass"]]
    result["all_pass"] = not failed

    # stdout 人话小结
    print(f"== soak 干跑报告（{SOAK_VERSION_TAG}）==")
    print(f"轮数: {result['rounds_run']}/{result['rounds_requested']}  "
          f"假节点池: {result['pool_size']}")
    print(f"动作计数: {result['actions']}")
    for key, summary in result["metrics"].items():
        if summary["available"]:
            print(f"{key}: first={summary['first']} last={summary['last']} "
                  f"max={summary['max']} growth={summary['growth']}")
        else:
            print(f"{key}: 不可用（判据降级）")
    if result["residues"]:
        print(f"!! 引擎残留 {len(result['residues'])} 个: {result['residues']}")
    print("判据:")
    for name, c in result["criteria"].items():
        print(f"  [{'PASS' if c['pass'] else 'FAIL'}] {name}: {c['detail']}")
    verdict = "全部通过" if result["all_pass"] else f"失败项: {failed}"
    print(f"结论: {verdict}")
    result["stdout"] = f"soak 干跑{'通过' if result['all_pass'] else '失败'}"

    if args.report:
        os.makedirs(os.path.dirname(os.path.abspath(args.report)),
                    exist_ok=True)
        with open(args.report, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"JSON 报告已写: {args.report}")
    return 0 if result["all_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())


# 集成适配（work15 原件 → client-dev 集成版的全部改动，审查方留痕）：
# 1. import faults 改为 try 包导入、退回裸导入（REVIEW_FAIL 适配 3，两种跑法都通）。
# 其余与原件逐行一致；SOAK_VERSION_TAG 保留 0.17.0 原值（适配 5）。
