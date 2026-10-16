#!/usr/bin/env python3
"""
VPN Gate 原生 OpenVPN 一键拨号器（系统级 VPN）
=============================================
与 gate 流水线共用同一套场景评级（web/data.json），但走 VPN Gate 原生 OpenVPN
协议、不经过 Cloudflare Worker —— 适合打游戏、整机下载等需要系统级 VPN 的场景。

工作流:
  1. 读取 web/data.json（四场景评级/置顶/黑名单）；没有则按 feed 账面分兜底排序
  2. 抓一次 feed 拿到各节点的官方 OpenVPN 配置（base64，自带 CA 证书）
  3. 按场景挑候选：置顶优先 → 黑名单剔除 → 场景门槛 → 综合排序
  4. 为候选生成 .ovpn + 凭证文件（凭证用 SSTP_USER/SSTP_PASS，默认 vpn/vpn）
  5. 调用本机 openvpn 拨号；掉线/失败自动切下一个候选
  6. --dry-run 只选节点+生成配置不拨号（自测用，不启动任何进程）

依赖:
  - 本机安装 OpenVPN Community: https://openvpn.net/community-downloads/
  - Windows 需「以管理员身份运行」终端（TAP 网卡与路由表需要权限）
  - 与 vpngate.py 放同一目录（复用其 feed 解析与 ban/pin 名单）
"""

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import time

try:
    import vpngate as vp
except ImportError:
    print("[失败] vpn_dial.py 必须与 vpngate.py 放在同一目录", file=sys.stderr)
    sys.exit(1)

OPENVPN_FALLBACKS = [
    "openvpn",
    r"C:\Program Files\OpenVPN\bin\openvpn.exe",
    r"C:\Program Files (x86)\OpenVPN\bin\openvpn.exe",
    r"C:\Program Files\OpenVPN Connect\openvpn.exe",
]

def find_openvpn() -> str:
    env_bin = os.environ.get("OPENVPN_BIN", "").strip()
    if env_bin and os.path.exists(env_bin):
        return env_bin
    found = shutil.which("openvpn")
    if found:
        return found
    for c in OPENVPN_FALLBACKS[1:]:
        if os.path.exists(c):
            return c
    return ""

def log(msg):
    print(msg, flush=True)

def load_metrics(path: str) -> dict:
    """{host: node} —— 来自 gate 流水线产出的 data.json"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        out = {}
        for n in (data.get("available") or []):
            if n.get("host"):
                out[n["host"]] = dict(n)
        return out
    except Exception:
        return {}

def fetch_feed_configs(feed_file=None) -> dict:
    """{host(.opengw.net): feed行} —— 含官方 OpenVPN 配置(base64)与账面分

    双模式：本地为主，在线为辅。
    - 本地 feed 文件存在 → 从本地读取（主，零网络依赖）
    - 本地不存在 → 尝试在线拉取（辅，成功则保存本地缓存）
    - 在线失败 → 用本地缓存（如果有）
    """
    def _rows_to_configs(rows):
        out = {}
        for r in rows:
            if not r.get("config_b64"):
                continue
            host = r["host"] if r["host"].endswith(".opengw.net") else r["host"] + ".opengw.net"
            out[host] = r
        return out

    def _read_local(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return vp.parse_csv(f.read()) or None
        except Exception:
            return None

    # 主模式：本地 feed 文件
    if feed_file and os.path.exists(feed_file):
        log(f"[源] 读取本地 feed: {feed_file}")
        rows = _read_local(feed_file)
        if rows:
            return _rows_to_configs(rows)
        log("[源] 本地 feed 为空，尝试在线拉取")

    # 辅模式：在线拉取
    try:
        session = vp.create_feed_session()
        rows, _src = vp.fetch_vpngate(session)
        if rows:
            if feed_file:
                try:
                    os.makedirs(os.path.dirname(feed_file) or ".", exist_ok=True)
                    resp = session.get(vp.VPNGATE_API, timeout=vp.HTTP_TIMEOUT)
                    resp.raise_for_status()
                    with open(feed_file, "w", encoding="utf-8") as f:
                        f.write(resp.text)
                    log(f"[源] 在线拉取成功，已保存本地缓存")
                except Exception as exc:
                    log(f"[源] 保存本地缓存失败（不影响本次）: {exc}")
            return _rows_to_configs(rows)
    except Exception as exc:
        log(f"[源] 在线拉取失败: {exc}")

    # 在线失败，用本地缓存
    if feed_file and os.path.exists(feed_file):
        log(f"[源] 使用本地缓存: {feed_file}")
        rows = _read_local(feed_file)
        if rows:
            return _rows_to_configs(rows)

    vp.die("feed 获取失败：本地无缓存且在线拉取失败")

def _scene_ok(node: dict, scene: str) -> bool:
    if scene == "all":
        return True
    return bool(node.get(f"{scene}_ready"))

def _sort_key(node: dict, scene: str):
    if scene == "game":
        return ((node.get("latency_ms") or 9999) + (node.get("jitter_ms") or 0) * 2, node.get("host") or "")
    if scene == "speed":
        return (-(node.get("speed_mbps") or 0), node.get("latency_ms") or 9999, node.get("host") or "")
    if scene == "ai":
        return (
            0 if node.get("residential") == "residential" else 1,
            -(node.get("uptime_hours") or 0),
            node.get("jitter_ms") if node.get("jitter_ms") is not None else 9999,
            node.get("latency_ms") or 9999,
        )
    # support / all：综合稳态分
    return (node.get("composite_score", 9999), node.get("latency_ms") or 9999, node.get("host") or "")

def build_candidates(scene: str, metrics: dict, feed_configs: dict, limit: int):
    """
    产出 [(node, config_b64)]：
      置顶(pin)优先 → 黑名单(ban)剔除 → 场景门槛 → 场景排序
    metrics 为空时（没有 data.json）用 feed 账面分兜底。
    """
    if metrics:
        cands = []
        for host, n in metrics.items():
            if host not in feed_configs:
                continue
            if vp._host_list_match(n, vp.BAN_HOSTS):
                continue
            if not _scene_ok(n, scene):
                continue
            cands.append(n)
        cands.sort(key=lambda n: (0 if vp._host_list_match(n, vp.PIN_HOSTS) else 1, _sort_key(n, scene)))
    else:
        # 无 data.json 兜底：按 feed 账面分（score 降序、ping 升序）构造候选
        cands = []
        for host, row in feed_configs.items():
            n = {
                "host": host, "port": 443, "country": row.get("country_long") or "未知",
                "country_code": row.get("country_short") or "?",
                "uptime_hours": None, "sessions": row.get("sessions") or 0,
                "speed_mbps": round((row.get("speed_bps") or 0) / 1000000.0, 1),
                "score": row.get("score") or 0, "ping_feed": row.get("ping", 999),
                "latency_ms": None, "jitter_ms": None, "residential": "unknown", "exit": None,
            }
            if vp._host_list_match(n, vp.BAN_HOSTS):
                continue
            cands.append(n)
        cands.sort(key=lambda n: (0 if vp._host_list_match(n, vp.PIN_HOSTS) else 1,
                                  -(n.get("score") or 0), n.get("ping_feed", 999), n.get("host")))

    out = []
    for n in cands[:limit]:
        out.append((n, feed_configs[n["host"]]["config_b64"]))
    return out

def write_node_files(node: dict, config_b64: str, out_dir: str):
    """生成 .ovpn（凭证用绝对路径引用）与凭证文件"""
    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9.-]", "_", node["host"])
    ovpn_path = os.path.join(out_dir, f"node_{safe}_{node['port']}.ovpn")
    creds_path = os.path.join(out_dir, "vpngate_creds.txt")

    try:
        cfg = base64.b64decode(config_b64, validate=False).decode("utf-8", "replace")
    except Exception as exc:
        raise RuntimeError(f"OpenVPN 配置解码失败: {exc}")

    raw = cfg.splitlines()
    has_close = any(ln.strip().lower().startswith("</auth-user-pass>") for ln in raw)
    lines = []
    skip_block = False
    for ln in raw:
        s = ln.strip().lower()
        if has_close and s.startswith("<auth-user-pass"):
            skip_block = True          # 有配对闭合标签：整体剥掉内联凭证块
            continue
        if skip_block:
            if s.startswith("</auth-user-pass>"):
                skip_block = False
            continue
        if s.startswith("auth-user-pass") or s.startswith("<auth-user-pass"):
            continue                   # 裸指令或无闭合的孤儿行：单行删除
        lines.append(ln)
    lines.append(f"auth-user-pass {creds_path}")   # 绝对路径，避免 CWD 分歧
    with open(ovpn_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    with open(creds_path, "w", encoding="utf-8") as f:
        f.write(f"{vp.SSTP_USER}\n{vp.SSTP_PASS}\n")
    return ovpn_path, creds_path

def _fmt_node(n: dict) -> str:
    lat = n.get("latency_ms")
    lat_s = f"{lat}ms" if lat is not None else "?"
    jit = n.get("jitter_ms")
    jit_s = f"{jit}ms" if jit is not None else "未测"
    up = n.get("uptime_hours")
    up_s = f"{up}h" if up else "未知"
    sess = n.get("sessions")
    flags = []
    if n.get("pinned"): flags.append("📌置顶")
    if n.get("support_ready"): flags.append("⭐")
    if n.get("game_ready"): flags.append("🎮")
    if n.get("is_clean") is False: flags.append("⚠️脏IP")
    return (f"{n['host']}:{n['port']} | 延迟{lat_s} 抖动{jit_s} 在线{up_s} "
            f"负载{sess if sess is not None else '?'} | {' '.join(flags) or '-'}")

def dial(cands, openvpn_bin: str, out_dir: str):
    for i, (node, b64) in enumerate(cands, 1):
        try:
            ovpn_path, creds_path = write_node_files(node, b64, out_dir)
        except Exception as exc:
            log(f"  [跳过] 配置生成失败: {exc}")
            continue
        log(f"[{i}/{len(cands)}] 拨号: {_fmt_node(node)}")
        proc = subprocess.Popen([openvpn_bin, "--config", ovpn_path], cwd=out_dir)
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            log("\n[用户中断] 正在断开 VPN ...")
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
            return
        if rc == 0:
            log("[完成] OpenVPN 正常退出")
            return
        log(f"  [掉线/失败 rc={rc}] 2 秒后切换下一个候选 ...")
        time.sleep(2)
    log("[失败] 全部候选均已尝试且失败 — 请检查网络/OpenVPN 安装/节点状态")

def run(argv=None):
    ap = argparse.ArgumentParser(description="VPN Gate 原生 OpenVPN 一键拨号器")
    ap.add_argument("--scene", default="support", choices=["support", "game", "speed", "ai", "all"],
                    help="场景通道（默认 support 客服稳连）")
    ap.add_argument("--data", default=os.path.join("web", "data.json"), help="data.json 路径（无则按 feed 兜底）")
    ap.add_argument("--feed-file", default=os.path.join(vp.REPO_DIR, "feed.csv"),
                    help="本地 feed 文件（主模式：存在则直接读；不存在则在线拉取并保存）")
    ap.add_argument("--refresh", action="store_true", help="强制在线拉取 feed（忽略本地缓存）")
    ap.add_argument("--limit", type=int, default=8, help="最多尝试多少个候选节点")
    ap.add_argument("--out-dir", default=os.path.join(vp.REPO_DIR, "ovpn_out"), help=".ovpn 输出目录")
    ap.add_argument("--dry-run", action="store_true", help="只选节点+生成配置，不启动 OpenVPN")
    args = ap.parse_args(argv)

    if not vp.WORKER_CHECK_URL:
        log("[提示] 未配置 WORKER_CHECK_URL（不影响拨号器，它不经过 Worker）")

    metrics = load_metrics(args.data)
    if metrics:
        log(f"[数据] 已加载 {args.data} 的场景评级（{len(metrics)} 个节点）")
    else:
        log(f"[数据] 未找到 {args.data}，按 feed 账面分兜底排序")

    log(f"[源] 抓取 VPN Gate feed 获取 OpenVPN 配置 ...")
    if args.refresh:
        log("[源] --refresh 强制在线拉取（忽略本地缓存）")
    feed_configs = fetch_feed_configs(feed_file=None if args.refresh else args.feed_file)
    if not feed_configs:
        vp.die("feed 未返回任何 OpenVPN 配置")

    cands = build_candidates(args.scene, metrics, feed_configs, args.limit)
    if not cands:
        vp.die(f"场景 [{args.scene}] 没有候选节点（可能没人达标；--scene all 看看全量）")

    log(f"[候选] 场景 [{args.scene}] 选出 {len(cands)} 个候选（置顶/黑名单已生效）:")
    for n, _ in cands:
        log(f"  - {_fmt_node(n)}")

    if args.dry_run:
        log("[DRY-RUN] 仅生成配置文件，不拨号:")
        for n, b64 in cands:
            ovpn_path, creds_path = write_node_files(n, b64, args.out_dir)
            log(f"  - {os.path.basename(ovpn_path)}  (凭证: {os.path.basename(creds_path)})")
        log(f"[DRY-RUN] 输出目录: {os.path.abspath(args.out_dir)}")
        return cands

    openvpn_bin = find_openvpn()
    if not openvpn_bin:
        vp.die("未找到 openvpn 可执行文件 — 请安装 OpenVPN Community 或用环境变量 OPENVPN_BIN 指定路径")
    log(f"[OpenVPN] {openvpn_bin}")
    log("[提示] Windows 需以管理员身份运行本脚本；连接后所有流量走 VPN（系统级）")
    dial(cands, openvpn_bin, args.out_dir)
    return []

if __name__ == "__main__":
    run()
