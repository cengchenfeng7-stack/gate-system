#!/usr/bin/env python3
"""
VPN Gate 原生 OpenVPN 一键拨号器（系统级 VPN）  v2
==================================================
与 gate 流水线共用同一套场景评级（web/data.json），但走 VPN Gate 原生 OpenVPN
协议、不经过 Cloudflare Worker —— 适合打游戏、整机下载等需要系统级 VPN 的场景。

相对「原始版」的修复:
  F1 feed 本地为主、在线为辅：--feed-file 读本地文件；--refresh 在线拉取并存盘；
     默认读 feed.csv，没有再尝试在线。读本地文件兼容 BOM / UTF-16(PowerShell 重定向) / 镜像 JSON。
  F2 真正判断「连上了没」：读 OpenVPN 日志，等到 Initialization Sequence Completed 才算连上；
     "Completed With Errors"（多半是没用管理员运行 / 没有虚拟网卡）当作环境错误，立刻停下并提示，不再白白换节点。
  F3 连接超时（--connect-timeout，默认 45 秒）：死节点不会再把脚本一直挂住。
  F4 掉线检测：连上之后若日志出现重连/超时/断开，直接掐掉进程换下一个候选
     （OpenVPN 自带 persist-tun，掉线时多半原地重连而不退出，原始版的 rc 判断触发不了）。
  F5 路径问题：.ovpn 里的凭证文件用相对文件名 + cwd=输出目录，命令行也只传文件名，
     配置和命令行里都不再出现带空格/中文/反斜杠的绝对路径。
  F6 安全：feed 来自第三方志愿者节点（还可能经过镜像），拨号器以管理员运行，
     所以写 .ovpn 时剥掉会执行本地命令/写文件/开管理端口的指令，并显式 script-security 1，剥掉了什么都会打日志。
  F7 失败不静默：每个候选为什么失败都记录，全部失败时汇总打印；日志同时落盘到 <out-dir>/dial.log。

依赖:
  - 本机安装 OpenVPN Community: https://openvpn.net/community-downloads/
  - Windows 需「以管理员身份运行」终端（虚拟网卡与路由表需要权限）
  - 与 vpngate.py 放同一目录（复用其 feed 解析与 ban/pin 名单）

用法:
  python vpn_dial.py --dry-run                      # 只选节点+生成配置，不拨号（不需要 OpenVPN）
  python vpn_dial.py --feed-file feed.csv           # 用本地 feed 拨号（默认场景 support）
  python vpn_dial.py --refresh --scene game         # 先在线拉最新 feed（成功会存到 feed.csv）
"""

import argparse
import base64
import json
import locale
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

try:
    import vpngate as vp
except ImportError as _exc:
    print(f"[失败] 无法导入 vpngate.py: {_exc}\n"
          f"       vpn_dial.py 必须与 vpngate.py 放在同一目录，且已安装 requests (pip install requests)",
          file=sys.stderr)
    sys.exit(1)

FEED_STALE_HOURS = 24.0          # 本地 feed 超过这么久没更新就告警（节点每天都在上下线）
DEFAULT_CONNECT_TIMEOUT = 45.0   # 单个候选节点「启动→连上」的最长等待秒数

OPENVPN_FALLBACKS = [
    "openvpn",
    r"C:\Program Files\OpenVPN\bin\openvpn.exe",
    r"C:\Program Files (x86)\OpenVPN\bin\openvpn.exe",
    r"C:\Program Files\OpenVPN Connect\openvpn.exe",
]

# ---------------------------------------------------------------------------
# 日志（控制台 + 落盘）
# ---------------------------------------------------------------------------
_LOG_FILE = None

def set_log_file(path):
    global _LOG_FILE
    _LOG_FILE = path

def log(msg=""):
    print(msg, flush=True)
    if _LOG_FILE:
        try:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        except Exception:
            pass   # 日志写盘失败不能影响拨号本身

# ---------------------------------------------------------------------------
# OpenVPN 可执行文件 / 管理员权限
# ---------------------------------------------------------------------------
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

def is_admin():
    """True/False；无法判断返回 None"""
    try:
        if os.name == "nt":
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return None

# ---------------------------------------------------------------------------
# F1: feed 读取（本地为主，在线为辅）
# ---------------------------------------------------------------------------
def read_text_auto(path: str) -> str:
    """读本地 feed 文本：兼容 UTF-8 BOM、UTF-16（PowerShell `>` 重定向的默认编码）"""
    with open(path, "rb") as f:
        raw = f.read()
    return decode_feed_bytes(raw)

def decode_feed_bytes(raw: bytes) -> str:
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8", "replace")

def parse_feed_text(text: str) -> list:
    """按内容识别：JSON(镜像格式) 或 官方 CSV。失败抛 RuntimeError。"""
    s = text.lstrip()
    if s[:1] in ("[", "{"):
        try:
            return vp.parse_mirror_json(json.loads(s))
        except Exception as exc:
            raise RuntimeError(f"JSON feed 解析失败: {exc}")
    return vp.parse_csv(text)    # 找不到表头会抛 RuntimeError

def load_local_feed(path: str) -> list:
    return parse_feed_text(read_text_auto(path))

def _atomic_write_bytes(path: str, data: bytes):
    d = os.path.dirname(os.path.abspath(path))
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)       # 原子替换：下载到一半断了不会把旧 feed 弄坏

def fetch_online_feed(save_path: str, session=None):
    """在线拉取（官方 API → 镜像），成功后原样存盘到 save_path。返回 (rows, 来源名)。"""
    session = session or vp.create_feed_session()
    errors = []
    for name, url in (("官方 API", vp.VPNGATE_API), ("镜像", vp.VPNGATE_MIRROR)):
        try:
            resp = session.get(url, timeout=vp.HTTP_TIMEOUT)
            resp.raise_for_status()
            raw = resp.content
            rows = parse_feed_text(decode_feed_bytes(raw))
            if not rows:
                errors.append(f"{name}: 解析出 0 行")
                continue
            _atomic_write_bytes(save_path, raw)
            return rows, name
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    raise RuntimeError("; ".join(errors))

def rows_to_feed_configs(rows: list) -> dict:
    """{host(.opengw.net): feed行} —— 只保留带官方 OpenVPN 配置(base64)的节点"""
    out = {}
    for r in rows:
        if not r.get("config_b64"):
            continue
        host = r["host"] if r["host"].endswith(".opengw.net") else r["host"] + ".opengw.net"
        out[host] = r
    return out

def feed_age_hours(path: str):
    try:
        return (time.time() - os.path.getmtime(path)) / 3600.0
    except OSError:
        return None

def resolve_feed(feed_file, refresh: bool):
    """
    本地为主、在线为辅：
      --refresh            先在线拉（成功即存盘），失败退回本地文件
      --feed-file PATH     必须是存在且能解析的文件（显式指定就不静默改用别的来源）
      都没指定             默认 <vpngate.py 同目录>/feed.csv，不存在再尝试在线
    返回 (feed_configs, 来源描述)；失败直接 vp.die。
    """
    default_path = os.path.join(vp.REPO_DIR, "feed.csv")
    path = feed_file or default_path
    explicit = bool(feed_file)
    rows, desc = None, ""
    tried_online = False
    online_err = ""

    if refresh:
        tried_online = True
        log(f"[源] 在线拉取 VPN Gate feed（成功会保存到 {path}）...")
        try:
            rows, src = fetch_online_feed(path)
            desc = f"在线({src})，已保存到 {path}"
        except Exception as exc:
            online_err = str(exc)
            log(f"[在线失败] {exc}")

    if rows is None and os.path.exists(path):
        try:
            rows = load_local_feed(path)
            desc = f"本地文件 {path}"
            age = feed_age_hours(path)
            if age is not None and age > FEED_STALE_HOURS:
                log(f"[⚠️ feed 过期] {path} 已 {age:.0f} 小时没更新，节点可能已下线；"
                    f"建议重新下载或加 --refresh")
        except Exception as exc:
            msg = f"本地 feed 无法解析: {path}\n  原因: {exc}"
            if explicit:
                vp.die(msg)
            log(f"[本地 feed 异常] {msg}")
    elif rows is None and explicit:
        vp.die(f"--feed-file 指定的文件不存在: {path}")

    if rows is None and not tried_online and not explicit:
        log(f"[源] 本地没有可用 feed，尝试在线拉取（成功会保存到 {path}）...")
        try:
            rows, src = fetch_online_feed(path)
            desc = f"在线({src})，已保存到 {path}"
        except Exception as exc:
            online_err = str(exc)

    if rows is None:
        vp.die("拿不到 feed。\n"
               f"  在线: {online_err or '未尝试'}\n"
               f"  办法: 用浏览器（能上网的代理下）打开 {vp.VPNGATE_API}\n"
               f"        另存为 {default_path} ，再运行 python vpn_dial.py --feed-file feed.csv")

    feed_configs = rows_to_feed_configs(rows)
    if not feed_configs:
        vp.die(f"feed 里共 {len(rows)} 行，但没有一行带 OpenVPN 配置 — 下载的可能不是完整 feed")
    return feed_configs, desc

# ---------------------------------------------------------------------------
# data.json 评级 / 候选挑选
# ---------------------------------------------------------------------------
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

_PROTO_RE = re.compile(r"^\s*proto\s+(\S+)", re.M | re.I)
_REMOTE_RE = re.compile(r"^\s*remote\s+(\S+)\s+(\d+)", re.M | re.I)

def decode_config(config_b64: str) -> str:
    try:
        text = base64.b64decode(config_b64, validate=False).decode("utf-8", "replace")
    except Exception as exc:
        raise RuntimeError(f"OpenVPN 配置解码失败: {exc}")
    if not _REMOTE_RE.search(text):
        # b64decode 对损坏数据不会报错，只会吐出垃圾；没有 remote 行的肯定不是可用配置
        raise RuntimeError("OpenVPN 配置损坏或不完整（找不到 remote 行）")
    return text

def ovpn_proto_port(cfg_text: str):
    """从官方配置里取 (proto, port)；取不到返回 ('?', 443)"""
    pm = _PROTO_RE.search(cfg_text)
    rm = _REMOTE_RE.search(cfg_text)
    proto = pm.group(1).lower() if pm else "?"
    try:
        port = int(rm.group(2)) if rm else 443
    except ValueError:
        port = 443
    return proto, port

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
            try:
                proto, port = ovpn_proto_port(decode_config(row["config_b64"]))
            except Exception:
                proto, port = "?", 443
            up_ms = row.get("uptime_ms") or 0
            n = {
                "host": host, "port": port, "proto": proto,
                "country": row.get("country_long") or "未知",
                "country_code": row.get("country_short") or "?",
                "uptime_hours": round(up_ms / 3600000.0, 1) if up_ms else None,
                "sessions": row.get("sessions") or 0,
                "speed_mbps": round((row.get("speed_bps") or 0) / 1000000.0, 1),
                "score": row.get("score") or 0, "ping_feed": row.get("ping", 999),
                "latency_ms": None, "jitter_ms": None, "residential": "unknown", "exit": None,
            }
            if vp._host_list_match(n, vp.BAN_HOSTS):
                continue
            cands.append(n)
        cands.sort(key=lambda n: (0 if vp._host_list_match(n, vp.PIN_HOSTS) else 1,
                                  -(n.get("score") or 0), n.get("ping_feed", 999), n.get("host")))

    return [(n, feed_configs[n["host"]]["config_b64"]) for n in cands[:limit]]

# ---------------------------------------------------------------------------
# F5/F6: 生成 .ovpn（净化 + 相对路径凭证）
# ---------------------------------------------------------------------------
# 会执行本地命令 / 读写任意文件 / 开本地控制端口的指令。VPN Gate 的官方配置不需要它们。
_DANGEROUS_DIRECTIVES = {
    "script-security", "up", "down", "up-restart", "down-pre", "route-up", "route-pre-down",
    "ipchange", "tls-verify", "learn-address", "client-connect", "client-disconnect",
    "auth-user-pass-verify", "plugin", "config", "cd", "chroot", "daemon", "service",
    "log", "log-append", "writepid", "status", "tmp-dir",
}

def _is_dangerous(first_token: str) -> bool:
    t = first_token.lower()
    return t in _DANGEROUS_DIRECTIVES or t.startswith("management")

def sanitize_ovpn(cfg: str, creds_name: str):
    """
    返回 (净化后的配置文本, 被剥掉的指令列表)。
    - 剥掉内联/裸的 auth-user-pass（统一改用凭证文件）
    - 剥掉危险指令，并显式写 script-security 1
    """
    raw = cfg.splitlines()
    has_close = any(ln.strip().lower().startswith("</auth-user-pass>") for ln in raw)
    lines, removed = [], []
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
        first_tok = s.split()[0] if s.split() else ""
        if first_tok == "auth-user-pass" or first_tok.startswith("<auth-user-pass"):
            continue                   # 裸指令或无闭合的孤儿行：单行删除
        stripped = ln.strip()
        if stripped and not stripped.startswith(("#", ";", "<")):
            first = stripped.split()[0]
            if _is_dangerous(first):
                removed.append(stripped[:80])
                continue
        lines.append(ln)
    lines.append("script-security 1")
    lines.append(f"auth-user-pass {creds_name}")   # 相对文件名：拨号时 cwd=输出目录
    return "\n".join(lines) + "\n", removed

def write_node_files(node: dict, config_b64: str, out_dir: str):
    """生成 .ovpn 与凭证文件，返回 (ovpn_path, creds_path)"""
    os.makedirs(out_dir, exist_ok=True)
    safe = re.sub(r"[^a-zA-Z0-9.-]", "_", node["host"])
    ovpn_path = os.path.join(out_dir, f"node_{safe}_{node['port']}.ovpn")
    creds_name = "vpngate_creds.txt"
    creds_path = os.path.join(out_dir, creds_name)

    text, removed = sanitize_ovpn(decode_config(config_b64), creds_name)
    if removed:
        log(f"  [安全] {node['host']} 的配置含 {len(removed)} 条危险指令，已剥掉: "
            + " | ".join(removed[:5]))
    with open(ovpn_path, "w", encoding="utf-8") as f:
        f.write(text)
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
    proto = n.get("proto")
    where = f"{n['host']}:{n['port']}" + (f"/{proto}" if proto and proto != "?" else "")
    return (f"{where} | 延迟{lat_s} 抖动{jit_s} 在线{up_s} "
            f"负载{sess if sess is not None else '?'} | {' '.join(flags) or '-'}")

# ---------------------------------------------------------------------------
# F2/F3/F4: 拨号监督（读 OpenVPN 日志判断 连上/失败/掉线/环境错误/超时）
# ---------------------------------------------------------------------------
# 结果类型
R_EXIT_OK = "exit_ok"        # 连上过，OpenVPN 正常退出(rc=0)
R_FAILED = "failed"          # 没连上就失败了（认证/握手失败、进程退出…）
R_DROPPED = "dropped"        # 连上之后掉线
R_TIMEOUT = "timeout"        # 超时没连上
R_ENV = "env_error"          # 环境问题：换节点没用（权限/虚拟网卡）

# 环境类致命错误：换节点没有意义，立刻停下
_ENV_MARKERS = (
    "there are no tap-windows adapters",
    "all tap-windows adapters on this system are currently in use",
)
# 失败/掉线标志（连上前=失败，连上后=掉线）。注意不用泛泛的 "tls error"，
# 因为 "TLS Error: cannot locate HMAC ..." 只是无关杂包的噪音。
_FAIL_MARKERS = (
    "auth_failed", "auth failed",
    "tls key negotiation failed", "tls handshake failed",
    "process restarting", "restarting [",
    "inactivity timeout", "connection reset",
    "exiting due to fatal error",
)

def classify_line(low: str, connected: bool):
    """
    low 为小写日志行。返回 None / 'connected' / 'env' / 'failed' / 'dropped'。
    """
    for m in _ENV_MARKERS:
        if m in low:
            return "env"
    if "initialization sequence completed" in low:
        # "Initialization Sequence Completed With Errors" = 路由/网卡没配好，不算连上
        return "env" if "with errors" in low else "connected"
    for m in _FAIL_MARKERS:
        if m in low:
            return "dropped" if connected else "failed"
    return None

def decode_line(raw: bytes) -> str:
    """OpenVPN 输出可能是 UTF-8，也可能是中文 Windows 的 GBK；都兜住，不抛异常"""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return raw.decode(locale.getpreferredencoding(False) or "utf-8", "replace")
        except Exception:
            return raw.decode("utf-8", "replace")

def stop_process(proc, grace: float = 5.0):
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=grace)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=grace)
    except Exception:
        pass
    try:
        if proc.stdout:
            proc.stdout.close()
    except Exception:
        pass

def supervise(proc, connect_timeout: float, emit=None, poll: float = 0.5):
    """
    盯着 OpenVPN 进程的日志，直到有结论。返回 (结果类型, 说明, 是否连上过)。
    返回时进程可能仍在运行，调用方负责 stop_process。
    Ctrl+C 的 KeyboardInterrupt 原样向上抛。
    """
    q = queue.Queue()

    def reader():
        try:
            for raw in iter(proc.stdout.readline, b""):
                q.put(decode_line(raw))
        except Exception:
            pass
        finally:
            q.put(None)      # EOF 哨兵

    threading.Thread(target=reader, daemon=True).start()
    start = time.monotonic()
    connected = False

    while True:
        try:
            item = q.get(timeout=poll)
        except queue.Empty:
            item = ""

        if item is None:     # 输出结束 = 进程要退出了
            try:
                rc = proc.wait(timeout=5)
            except Exception:
                rc = None
            if connected and rc == 0:
                return R_EXIT_OK, "OpenVPN 正常退出", True
            if connected:
                return R_DROPPED, f"OpenVPN 退出 rc={rc}", True
            return R_FAILED, f"OpenVPN 在连上之前就退出了 rc={rc}", False

        if item:
            line = item.rstrip("\r\n")
            if emit:
                emit(line)
            kind = classify_line(line.lower(), connected)
            if kind == "env":
                return R_ENV, line.strip(), connected
            if kind == "connected":
                connected = True
                if emit:
                    emit(">>> 已连上")
            elif kind in ("failed", "dropped"):
                why = line.strip()
                return (R_DROPPED if connected else R_FAILED), why, connected

        if not connected and (time.monotonic() - start) > connect_timeout:
            return R_TIMEOUT, f"{connect_timeout:.0f} 秒内没有连上", False

def build_cmd(openvpn_bin, ovpn_name: str) -> list:
    """命令行只传相对文件名（cwd=输出目录），避开中文/空格路径。openvpn_bin 可以是 str 或 list。"""
    prefix = list(openvpn_bin) if isinstance(openvpn_bin, (list, tuple)) else [openvpn_bin]
    return prefix + ["--config", ovpn_name]

def dial(cands, openvpn_bin, out_dir: str, connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
         pause: float = 2.0) -> str:
    """
    依次尝试候选。返回:
      'ok'          连上过并且正常结束（用户 Ctrl+C 断开也算 ok）
      'env_error'   环境问题，已提示
      'failed'      所有候选都失败
    """
    attempts = []   # [(host, 结果类型, 说明)]

    def emit(line):
        log(f"    [ovpn] {line}")

    for i, (node, b64) in enumerate(cands, 1):
        host = node["host"]
        try:
            ovpn_path, _ = write_node_files(node, b64, out_dir)
        except Exception as exc:
            log(f"  [跳过] {host} 配置生成失败: {exc}")
            attempts.append((host, "config_error", str(exc)))
            continue

        log(f"[{i}/{len(cands)}] 拨号: {_fmt_node(node)}")
        cmd = build_cmd(openvpn_bin, os.path.basename(ovpn_path))
        try:
            proc = subprocess.Popen(cmd, cwd=out_dir, stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        except OSError as exc:
            log(f"[失败] 无法启动 OpenVPN ({cmd[0]}): {exc}")
            return "env_error"

        t0 = time.monotonic()
        try:
            kind, detail, _was_connected = supervise(proc, connect_timeout, emit)
        except KeyboardInterrupt:
            log("\n[用户中断] 正在断开 VPN ...")
            stop_process(proc)
            log("[已断开]")
            return "ok"
        stop_process(proc)
        dur = time.monotonic() - t0

        if kind == R_EXIT_OK:
            log("[完成] OpenVPN 正常退出")
            return "ok"
        if kind == R_ENV:
            log(f"[环境错误] {detail}")
            log("  这不是节点的问题，换节点没用。请检查：")
            log("  1) 是否用「管理员身份」运行了终端  2) OpenVPN 的虚拟网卡驱动(Wintun/TAP)是否安装且未被占用")
            return "env_error"

        label = {R_FAILED: "没连上", R_TIMEOUT: "超时", R_DROPPED: "掉线"}.get(kind, kind)
        log(f"  [{label}] {host} 持续 {dur:.0f}s: {detail}")
        attempts.append((host, kind, detail))
        if i < len(cands):
            log(f"  {pause:.0f} 秒后切换下一个候选 ...")
            time.sleep(pause)

    log("[失败] 全部候选均已尝试且失败。每个候选的原因:")
    for host, kind, detail in attempts:
        log(f"  - {host}: {kind} — {detail}")
    log("  排查：网络能否访问这些节点的端口 / 换 --scene all 或加大 --limit / 用 --refresh 换新 feed")
    return "failed"

# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def run(argv=None) -> int:
    ap = argparse.ArgumentParser(description="VPN Gate 原生 OpenVPN 一键拨号器 v2")
    ap.add_argument("--scene", default="support", choices=["support", "game", "speed", "ai", "all"],
                    help="场景通道（默认 support 客服稳连）")
    ap.add_argument("--data", default=os.path.join(vp.PUBLIC_DIR, "data.json"),
                    help="data.json 路径（没有则按 feed 账面分兜底）")
    ap.add_argument("--ignore-data", action="store_true", help="忽略 data.json，直接按 feed 账面分排序")
    ap.add_argument("--feed-file", default="", help="本地 feed 文件（官方 CSV 或镜像 JSON）；默认 feed.csv")
    ap.add_argument("--refresh", action="store_true", help="先在线拉取最新 feed 并存盘，失败再用本地文件")
    ap.add_argument("--limit", type=int, default=8, help="最多尝试多少个候选节点")
    ap.add_argument("--connect-timeout", type=float, default=DEFAULT_CONNECT_TIMEOUT,
                    help=f"单个节点连接超时秒数（默认 {DEFAULT_CONNECT_TIMEOUT:.0f}）")
    ap.add_argument("--out-dir", default=os.path.join(vp.REPO_DIR, "ovpn_out"), help=".ovpn 输出目录")
    ap.add_argument("--dry-run", action="store_true", help="只选节点+生成配置，不启动 OpenVPN")
    ap.add_argument("--no-admin-check", action="store_true", help="跳过管理员权限检查（一般不要用）")
    args = ap.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    set_log_file(os.path.join(args.out_dir, "dial.log"))

    metrics = {} if args.ignore_data else load_metrics(args.data)
    if metrics:
        log(f"[数据] 已加载 {args.data} 的场景评级（{len(metrics)} 个节点）")
    else:
        log("[数据] 没有可用的 data.json 评级，按 feed 账面分兜底排序")

    feed_configs, feed_desc = resolve_feed(args.feed_file, args.refresh)
    log(f"[源] feed 来自 {feed_desc}（{len(feed_configs)} 个带 OpenVPN 配置的节点）")

    cands = build_candidates(args.scene, metrics, feed_configs, args.limit)
    if not cands:
        hint = "没人达标；试试 --scene all，或 --ignore-data 按 feed 账面分排序" if metrics else "feed 里没有可用节点（可能全被 ban.txt 拉黑）"
        vp.die(f"场景 [{args.scene}] 没有候选节点（{hint}）")

    log(f"[候选] 场景 [{args.scene}] 选出 {len(cands)} 个候选（置顶/黑名单已生效）:")
    for n, _ in cands:
        log(f"  - {_fmt_node(n)}")

    if args.dry_run:
        log("[DRY-RUN] 仅生成配置文件，不拨号:")
        for n, b64 in cands:
            ovpn_path, _creds = write_node_files(n, b64, args.out_dir)
            log(f"  - {os.path.basename(ovpn_path)}")
        log(f"[DRY-RUN] 输出目录: {os.path.abspath(args.out_dir)}")
        return 0

    openvpn_bin = find_openvpn()
    if not openvpn_bin:
        vp.die("未找到 openvpn 可执行文件 — 请安装 OpenVPN Community 或用环境变量 OPENVPN_BIN 指定路径")
    log(f"[OpenVPN] {openvpn_bin}")

    if not args.no_admin_check and is_admin() is False:
        vp.die("当前不是管理员/root 权限，OpenVPN 无法创建虚拟网卡和路由。"
               "请「以管理员身份运行」终端后重试（Windows）或用 sudo（Linux/macOS）")

    log("[提示] 连上后所有流量走 VPN（系统级）；按 Ctrl+C 断开。连上后请自行打开一个「查本机 IP」的页面确认出口已变")
    status = dial(cands, openvpn_bin, args.out_dir, connect_timeout=args.connect_timeout)
    return 0 if status == "ok" else 1

if __name__ == "__main__":
    sys.exit(run())
