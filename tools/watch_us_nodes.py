#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
美加节点监控 (watch_us_nodes.py)
=================================
盯 gate-system 流水线发布的 data.json，一旦出现「出口在美国/加拿大 + 干净 + 非机房」
的节点就报警，省得手动刷 Clash。

数据源: https://cengchenfeng7-stack.github.io/gate-system/data.json
        (由 vpngate.py 每 30 分钟跑一轮，CF Worker 真实探测后发布)

用法:
    python watch_us_nodes.py                 # 常驻监控，每 10 分钟扫一次，只报新出现的
    python watch_us_nodes.py --once          # 扫一次就退出（看当前有没有美加节点）
    python watch_us_nodes.py --interval 5    # 改成 5 分钟一轮
    python watch_us_nodes.py --once --all    # 列出全部国家分布（排查为什么没有美加节点）
    python watch_us_nodes.py --popup         # 报警时弹 Windows 通知

注意:
    - 节点「出现」不等于「能连」。VPN Gate 节点寿命只有十几小时，
      报警后请立刻: 重新拉订阅 → 选对应的「XX-住宅-01」节点
      → 开 https://ipinfo.io (不要带路径) 确认出口真的是美国
    - 退出判断逻辑与 vpngate.py 的 is_clean / is_datacenter 字段保持一致。
"""

import argparse
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.request
from collections import Counter
from datetime import datetime
from pathlib import Path

DEFAULT_URL = "https://cengchenfeng7-stack.github.io/gate-system/data.json"
STATE_FILE = Path(__file__).with_name(".watch_us_ca_seen.json")
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) gate-watch/1.0"
TARGET_CODES = {"US", "CA"}
TARGET_NAMES = {"US": "美国", "CA": "加拿大"}
# data.json 里所有可能装节点的数组
NODE_KEYS = ("nodes", "watch_nodes", "fish_nodes", "datacenter_nodes", "datacenter_premium")


# ---------------------------------------------------------------------------
# 取数
# ---------------------------------------------------------------------------
def fetch(url: str, timeout: int = 30) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Cache-Control": "no-cache"})
    ctx = ssl.create_default_context()
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def iter_nodes(data: dict):
    """摊平 data.json 里所有节点数组，同一个节点可能在多个数组里重复出现"""
    seen = set()
    for key in NODE_KEYS:
        for node in data.get(key) or []:
            if not isinstance(node, dict):
                continue
            host = node.get("host")
            port = node.get("port")
            if host and (host, port) in seen:
                continue
            if host:
                seen.add((host, port))
            yield key, node


# ---------------------------------------------------------------------------
# 筛选
# ---------------------------------------------------------------------------
def exit_info(node: dict) -> dict:
    return node.get("exit") or {}


def node_country_code(node: dict) -> str:
    ex = exit_info(node)
    return (ex.get("country_code") or node.get("country_code") or "").upper()


def is_target(node: dict) -> bool:
    """美/加 + 干净 + 非机房 + 本轮探测成功"""
    if node_country_code(node) not in TARGET_CODES:
        return False
    ex = exit_info(node)
    if ex.get("is_abuser") or ex.get("is_tor") or ex.get("is_bogon"):
        return False
    if ex.get("is_datacenter") is True:
        return False
    if ex.get("is_clean") is False:
        return False
    if node.get("success") is False:
        return False
    if node.get("status") not in (None, "success"):
        return False
    return True


# ---------------------------------------------------------------------------
# 展示
# ---------------------------------------------------------------------------
def fmt_node(node: dict) -> str:
    ex = exit_info(node)
    code = node_country_code(node)
    flag = TARGET_NAMES.get(code, code)
    uptime = node.get("uptime_hours")
    up_txt = f"{uptime:.1f}h" if isinstance(uptime, (int, float)) else "未知"
    jitter = node.get("jitter_ms")
    jit_txt = f"±{jitter}" if isinstance(jitter, (int, float)) else "未知"
    lat = node.get("latency_ms")
    lat_txt = f"{lat}ms" if isinstance(lat, (int, float)) else "未知"
    return "\n".join([
        f"  [{flag}] {node.get('ip') or ex.get('ip')}",
        f"      节点    : {node.get('host')}:{node.get('port')}",
        f"      出口    : {ex.get('city', '?')}, {ex.get('country', '?')}  "
        f"{ex.get('org', '?')} (AS{ex.get('asn', '?')})",
        f"      质量    : 干净={ex.get('is_clean')} 机房={ex.get('is_datacenter')} "
        f"滥用={ex.get('is_abuser')} 住宅={node.get('residential', '?')}",
        f"      在线    : {up_txt}   延迟: {lat_txt}   抖动: {jit_txt}",
        f"      负载    : {node.get('sessions', '?')} 人   带宽: {node.get('speed_mbps', '?')} Mbps",
        f"      link    : {node.get('link', '')}",
    ])


def print_hint():
    print(
        "\n下一步（趁节点还活着赶紧做）:\n"
        "  1. Clash Verge → 订阅 → 重新拉取订阅\n"
        "  2. 选带「美国-住宅」「加拿大-住宅」字样的节点（手动固定，别用 LoadBalance）\n"
        "  3. 浏览器开 https://ipinfo.io  ← 注意不要带任何路径，确认出口 IP 真的是美国\n"
        "  4. 开 https://muse.ai 看能否跳到 auth.muse.ai 注册页\n"
    )


def popup(msg: str):
    """弹 Windows 通知框（失败也不影响主流程）"""
    try:
        safe = msg.replace("'", "''").replace("\n", " ")
        subprocess.Popen(
            ["powershell", "-NoProfile", "-Command",
             "Add-Type -AssemblyName System.Windows.Forms;"
             "[System.Windows.Forms.MessageBox]::Show('%s')" % safe],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass


def now() -> str:
    return datetime.now().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# 状态（只报新出现的）
# ---------------------------------------------------------------------------
def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text("utf-8"))
        except Exception:
            return {}
    return {}


def save_state(state: dict):
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), "utf-8")
    except Exception as e:
        print(f"[warn] 状态文件写入失败: {e}")


def node_id(node: dict) -> str:
    return f"{node.get('host')}:{node.get('port')}"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def scan(url: str, show_all: bool = False):
    try:
        data = fetch(url)
    except Exception as e:
        print(f"[{now()}] [错误] 拉取失败: {e}")
        return None

    nodes = list(iter_nodes(data))
    if not nodes:
        print(f"[{now()}] [警告] data.json 里没解析到节点，检查流水线是否发布成功")
        return []

    if show_all:
        c = Counter(node_country_code(n) for _, n in nodes)
        print(f"[{now()}] 共 {len(nodes)} 个节点，国家分布:")
        for code, cnt in c.most_common():
            mark = "  <== 目标" if code in TARGET_CODES else ""
            print(f"    {code or '??':>4} : {cnt}{mark}")
        return []

    targets = [n for _, n in nodes if is_target(n)]
    if not targets:
        c = Counter(node_country_code(n) for _, n in nodes)
        top = ", ".join(f"{k or '??'}x{v}" for k, v in c.most_common(6))
        print(f"[{now()}] 本轮无美/加干净节点（共 {len(nodes)} 个；分布: {top}）")
        return []

    # 按在线时长优先，其次延迟
    targets.sort(key=lambda n: (-(n.get("uptime_hours") or 0), n.get("latency_ms") or 999999))
    print(f"\n{'=' * 64}")
    print(f"[{now()}] ★ 发现 {len(targets)} 个美国/加拿大干净节点")
    print(f"{'=' * 64}")
    for n in targets:
        print(fmt_node(n))
    print_hint()
    return targets


def main():
    ap = argparse.ArgumentParser(description="盯 gate-system data.json，报美国/加拿大干净节点")
    ap.add_argument("--url", default=DEFAULT_URL, help="data.json 地址")
    ap.add_argument("--interval", type=int, default=10, help="轮询间隔（分钟），默认 10")
    ap.add_argument("--once", action="store_true", help="只扫一次就退出")
    ap.add_argument("--all", action="store_true", help="打印国家分布（排查用）")
    ap.add_argument("--popup", action="store_true", help="报警时弹 Windows 通知")
    ap.add_argument("--reset", action="store_true", help="清空「已报警」记录")
    args = ap.parse_args()

    if args.reset:
        if STATE_FILE.exists():
            STATE_FILE.unlink()
            print(f"已清空 {STATE_FILE.name}")
        return

    if args.once:
        scan(args.url, show_all=args.all)
        return

    state = load_state()
    print(f"开始监控: {args.url}")
    print(f"轮询间隔: {args.interval} 分钟   目标: 美国/加拿大 + 干净 + 非机房")
    print(f"已报警记录: {len(state)} 个   (Ctrl+C 停止)\n")

    try:
        while True:
            targets = scan(args.url)
            if targets:
                fresh = [n for n in targets if node_id(n) not in state]
                if fresh:
                    for n in fresh:
                        state[node_id(n)] = now()
                    save_state(state)
                    print(f"\n🔔 新增 {len(fresh)} 个美/加节点，已记录（下次不再重复提醒）")
                    if args.popup:
                        popup(f"发现 {len(fresh)} 个美国/加拿大节点：\n" +
                               "\n".join(f"{node_id(n)}  在线 {n.get('uptime_hours')}h"
                                         for n in fresh) +
                               "\n\n快去重新拉订阅！")
                else:
                    print("（都是之前报过的，保持即可）")
            time.sleep(max(1, args.interval) * 60)
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
