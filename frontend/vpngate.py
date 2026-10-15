#!/usr/bin/env python3
"""
VPN Gate SSTP 节点检测流水线 (四场景稳连融合最终版 v4)
=====================================================
基底: 断网前的最后一版(v3) + 原版基线 + 修改后(A) 三版融合，并修复全部已知问题。

四场景专属通道（每条都是独立检测与排序，产物相互独立）:
  ⭐ 客服稳连  nodes_support.txt  干净IP(无滥用/非Tor/非虚假) + 住宅 + 长在线 + 低负载 + 低抖动 + 低延迟
  🎮 游戏低延迟 nodes_game.txt     实测低延迟 + 低抖动 + 不拥堵（响应速度优先）
  ⬇️ 下载高速   nodes_speed.txt    VPN Gate 账面带宽优先 + 实测可用
  🤖 国外AI     nodes_ai.txt       干净IP + 长在线 + 低抖动（AI/风控场景最怕脏IP）

相对 v3 的关键修复:
  R1 parse_csv 不再跳过任何数据行、不再抬高字段门槛，安全缺省取空（防整轮空跑）
  R2 彻底移除历史缓存兜底合并：本轮无新鲜成功节点时不发布、不清线（防僵尸节点冒充稳连）
  R3 检测会话与抓取会话分离：检测不带重试（429 直接判 Worker 异常，不打爆 Worker）
  R4 二次复测区分「业务失败」(淘汰) 与「Worker 抖动」(保留首结果, jitter=未知)
  R5 cron */30 + job 超时 25 分钟 + cancel-in-progress（不会互相掐死）
  R6 jitter 未复测为 None（不再冒充 0 抖动）；uptime=0 视为「未知」而非「长开机」
  R7 入口自检全失败时返回完整候选列表并明确告警（不再把死域名当兜底输出）
  R8 删除源码内硬编码私有 Worker 地址：未配置 WORKER_CHECK_URL 直接失败退出
  R9 节点链接统一使用 SSTP_USER/SSTP_PASS（前端复制不再写死 vpn:vpn）
"""

import base64
import csv
import io
import json
import os
import re
import socket
import ssl
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ---------------------------------------------------------------------------
# 配置项（env_str 系函数修复「已设置但为空」的环境变量覆盖默认值的问题）
# ---------------------------------------------------------------------------
def env_str(key: str, default: str) -> str:
    val = os.environ.get(key, "").strip()
    return val if val else default

def env_int(key: str, default: int) -> int:
    val = os.environ.get(key, "").strip()
    try:
        return int(val) if val else default
    except ValueError:
        return default

def env_float(key: str, default: float) -> float:
    val = os.environ.get(key, "").strip()
    try:
        return float(val) if val else default
    except ValueError:
        return default

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_PER_COUNTRY = env_int("MAX_PER_COUNTRY", 12)

# ---- 历史缓存 / 养鱼池 / 稳定性 ----
HISTORY_FILE = env_str("HISTORY_FILE", os.path.join(REPO_DIR, "cache", "nodes_history.json"))
AUTO_BAN_THRESHOLD = env_int("AUTO_BAN_THRESHOLD", 5)     # 连续 N 次失败自动黑名单
STABILITY_WINDOW = env_int("STABILITY_WINDOW", 5)         # 最近 N 次延迟
STABILITY_THRESHOLD = env_float("STABILITY_THRESHOLD", 0.30)  # 波动率 < 30% 为稳定
FISH_GRADUATE_ROUNDS = env_int("FISH_GRADUATE_ROUNDS", 3)  # 养鱼池毕业：连续 N 轮稳定
SPEED_MAX_LATENCY = env_float("SPEED_MAX_LATENCY", 5000)  # 下载场景延迟上限

# 官方 API（会话层已带重试，不再列重复的 http 源）
VPNGATE_API = env_str("VPNGATE_API", "https://www.vpngate.net/api/iphone/")
VPNGATE_MIRROR = env_str(
    "VPNGATE_MIRROR",
    "https://raw.githubusercontent.com/fdciabdul/Vpngate-Scraper-API/main/json/data.json",
)

# R8: 不在源码保留任何私有 Worker 地址，必须由环境变量 / Secret 提供
WORKER_CHECK_URL = (
    os.environ.get("CHECK_WORKER", "").strip()
    or os.environ.get("WORKER_CHECK_URL", "").strip()
)

CONCURRENCY = max(1, min(32, env_int("CHECK_CONCURRENCY", 16)))
CHECK_TIMEOUT = env_float("CHECK_TIMEOUT", 45.0)
MAX_CHECK_NODES = env_int("MAX_CHECK_NODES", 0)
HTTP_TIMEOUT = env_int("HTTP_TIMEOUT", 45)
DOUBLE_CHECK_TOP = env_int("DOUBLE_CHECK_TOP", 1) == 1
PUBLIC_DIR = env_str("PUBLIC_DIR", os.path.join(REPO_DIR, "web"))
TEMPLATE_HTML = os.path.join(REPO_DIR, "web", "index.html")

SSTP_USER = env_str("SSTP_USER", "vpn")
SSTP_PASS = env_str("SSTP_PASS", "vpn")

# ---- 手动干预：黑名单 / 置顶名单（仓库级文件，无动态后台也能人工介入）----
BAN_FILE = env_str("BAN_FILE", os.path.join(REPO_DIR, "ban.txt"))
PIN_FILE = env_str("PIN_FILE", os.path.join(REPO_DIR, "pin.txt"))
EXTRA_ENTRY_TOP = env_int("EXTRA_ENTRY_TOP", 3)      # 每场景前 N 名节点附加备用入口行
EXTRA_ENTRY_COUNT = env_int("EXTRA_ENTRY_COUNT", 2)  # 每个附加几个备用入口

# ---- 四场景阈值（均可用环境变量微调）----
SUPPORT_MIN_UPTIME_H = env_float("SUPPORT_MIN_UPTIME_H", 12.0)   # 客服: 最少连续在线小时
SUPPORT_MAX_SESSIONS = env_int("SUPPORT_MAX_SESSIONS", 120)      # 客服: 最大负载人数
SUPPORT_MAX_JITTER = env_float("SUPPORT_MAX_JITTER", 150)        # 客服: 最大抖动 ms
SUPPORT_MAX_LATENCY = env_float("SUPPORT_MAX_LATENCY", 600)      # 客服: 最大延迟 ms

GAME_MAX_LATENCY = env_float("GAME_MAX_LATENCY", 400)            # 游戏: 最大延迟 ms
GAME_MAX_JITTER = env_float("GAME_MAX_JITTER", 100)              # 游戏: 最大抖动 ms
GAME_MAX_SESSIONS = env_int("GAME_MAX_SESSIONS", 250)            # 游戏: 最大负载人数

SPEED_MIN_MBPS = env_float("SPEED_MIN_MBPS", 2.0)                # 下载: 最低带宽 Mbps
SPEED_MAX_SESSIONS = env_int("SPEED_MAX_SESSIONS", 250)          # 下载: 最大负载人数

AI_MIN_UPTIME_H = env_float("AI_MIN_UPTIME_H", 6.0)              # AI: 最少连续在线小时
AI_MAX_JITTER = env_float("AI_MAX_JITTER", 200)                  # AI: 最大抖动 ms
AI_MAX_LATENCY = env_float("AI_MAX_LATENCY", 800)                # AI: 最大延迟 ms

# 客服与高频工作常用区域分类
ASIA_SUPPORT_CODES = {"JP", "SG", "KR", "TW", "MY", "TH", "VN"}
WEST_SUPPORT_CODES = {"US", "CA", "GB", "UK", "DE", "FR", "NL", "CH", "IE", "SE", "AU"}

DATA_CENTER_ORG_KEYWORDS = [
    "GOOGLE", "AMAZON", "AWS", "MICROSOFT", "AZURE", "OVH", "HETZNER", "DIGITALOCEAN",
    "AKAMAI", "CLOUDFLARE", "FASTLY", "RACKSPACE", "EQUINIX", "LINODE", "VULTR",
    "HURRICANE", "TENCENT", "ALIBABA", "ALIYUN", "LEASEWEB", "LEASWEB", "CHOOPA",
    "CONTABO", "M247", "DATACAMP", "COGENT", "ORACLE",
]
RESIDENTIAL_ORG_KEYWORDS = [
    "NTT EAST", "NTT WEST", "NTT COMMUNICATIONS", "NTT BROADBAND", "KDDI", "DOCOMO",
    "SOFTBANK", "AU COMMUNICATIONS", "J:COM", "JCOM", "OCN", "BIGLOBE",
    "IIJ", "SEIKO", "CLEVER-NET", "AT&T", "COMCAST", "XFINITY", "VERIZON",
    "CHARTER", "SPECTRUM", "COX", "CENTURYLINK", "FRONTIER",
    "TELUS", "ROGERS", "BELL CANADA", "VODAFONE", "ORANGE", "DEUTSCHE TELEKOM",
    "BREEZE", "TIM S.P.A", "LIBERO", "FASTWEB", "FREE FRANCE", "BT OPEN",
    "KT", "SK BROADBAND", "LG UPLUS", "CHUNGHWA", "HINET", "SINGTEL", "STARHUB",
]

COUNTRY_ZH = {
    "US": "美国", "CA": "加拿大", "MX": "墨西哥",
    "BR": "巴西", "AR": "阿根廷", "CL": "智利", "CO": "哥伦比亚",
    "GB": "英国", "UK": "英国", "FR": "法国", "DE": "德国", "IT": "意大利", "ES": "西班牙",
    "NL": "荷兰", "BE": "比利时", "IE": "爱尔兰", "CH": "瑞士", "AT": "奥地利", "PT": "葡萄牙", "GR": "希腊",
    "SE": "瑞典", "DK": "丹麦", "NO": "挪威", "FI": "芬兰",
    "PL": "波兰", "CZ": "捷克", "HU": "匈牙利", "UA": "乌克兰", "RO": "罗马尼亚",
    "JP": "日本", "KR": "韩国", "TW": "台湾",
    "SG": "新加坡", "TH": "泰国", "MY": "马来西亚", "ID": "印度尼西亚", "PH": "菲律宾", "VN": "越南",
    "IN": "印度", "AU": "澳大利亚", "NZ": "新西兰",
    "AE": "阿联酋", "IL": "以色列", "TR": "土耳其", "SA": "沙特",
    "ZA": "南非", "EG": "埃及",
}

# ---------------------------------------------------------------------------
# 日志与安全脱敏
# ---------------------------------------------------------------------------
_section = None

def mask_worker_url(url: str) -> str:
    """脱敏日志与公开数据中的 Worker 域名，防止私有 Worker 被盗刷"""
    try:
        p = urlparse(url)
        if p.hostname:
            parts = p.hostname.split(".")
            if len(parts) >= 2:
                masked_host = parts[0][:3] + "***." + ".".join(parts[1:])
                return f"{p.scheme}://{masked_host}{p.path}"
    except Exception:
        pass
    return "https://***.workers.dev/check"

def log(section, msg=""):
    global _section
    if section != _section:
        print(f"========== {section} ==========")
        _section = section
    if msg:
        print(msg, flush=True)

def die(msg):
    log("FATAL", f"[失败] {msg}")
    sys.exit(1)

def load_host_list(path: str) -> set:
    """读取仓库级 host 名单（ban.txt / pin.txt）：每行一个 host 或 host:port，# 开头为注释"""
    hosts = set()
    try:
        with open(path, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip().lower()
                if not ln or ln.startswith("#"):
                    continue
                hosts.add(ln)
    except FileNotFoundError:
        pass
    except Exception as exc:
        log("CONFIG", f"读取 {os.path.basename(path)} 失败: {exc}")
    return hosts

def _host_list_match(node: dict, host_set: set) -> bool:
    if not host_set:
        return False
    h = (node.get("host") or "").lower()
    return h in host_set or f'{h}:{node.get("port")}' in host_set

BAN_HOSTS = load_host_list(BAN_FILE)
PIN_HOSTS = load_host_list(PIN_FILE)
if BAN_HOSTS or PIN_HOSTS:
    log("CONFIG", f"黑名单 {len(BAN_HOSTS)} 条 | 置顶名单 {len(PIN_HOSTS)} 条")

# R3: 抓取会话（允许重试，仅用于拉取 feed 与镜像）
def create_feed_session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=3, read=2, connect=2, backoff_factor=0.8,
        status_forcelist=(429, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (gate-checker/4.0)",
        "Accept-Encoding": "gzip, deflate",
    })
    return session

# R3: 检测会话（严禁重试：429/5xx 直接判 Worker 异常，绝不对 Worker 放大压力）
def create_check_session() -> requests.Session:
    session = requests.Session()
    adapter = HTTPAdapter(max_retries=0, pool_connections=CONCURRENCY + 4, pool_maxsize=CONCURRENCY + 4)
    session.mount("http://", adapter)
    session.mount("https://", adapter)
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (gate-checker/4.0)",
        "Accept-Encoding": "gzip, deflate",
    })
    return session

# ---------------------------------------------------------------------------
# 数据抓取（多源容错 + 完整稳定性指标提取）
# ---------------------------------------------------------------------------
def fetch_vpngate(session: requests.Session):
    try:
        log("VPN GATE", f"获取官方 API: {VPNGATE_API}")
        resp = session.get(VPNGATE_API, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        rows = parse_csv(resp.text)
        if rows:
            log("VPN GATE", f"主源(官方 API) 获取到 {len(rows)} 个原始节点")
            return rows, "vpngate.net/api/iphone"
        log("VPN GATE", "官方 API 解析出 0 行，尝试回退镜像")
    except Exception as exc:
        log("VPN GATE", f"官方 API 获取失败: {exc}")

    try:
        log("VPN GATE", f"回退镜像: {VPNGATE_MIRROR}")
        resp = session.get(VPNGATE_MIRROR, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        rows = parse_mirror_json(resp.json())
        if rows:
            log("VPN GATE", f"回退源(镜像) 获取到 {len(rows)} 个原始节点")
            return rows, "github-mirror"
    except Exception as exc:
        log("VPN GATE", f"回退镜像也失败: {exc}")

    die("VPN Gate 官方 API 与回退镜像均不可用, 数据源完全失败")

def _safe_int(val, default=0) -> int:
    try:
        return int(float(str(val).strip()))
    except Exception:
        return default

def _field(fields, idx, default=""):
    """安全取列：缺列/越界返回默认值，绝不因字段数量抛异常"""
    if 0 <= idx < len(fields):
        return fields[idx].strip()
    return default

# R1: 不跳行、不抬门槛；兼容 "*" 前缀数据行与缺列旧格式 feed
def parse_csv(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    header_idx = None
    for i, ln in enumerate(lines):
        if ln.lstrip("#*").lower().startswith("hostname"):
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError("找不到 CSV 表头行 (HostName)")

    header = [h.strip().lstrip("*").lower() for h in lines[header_idx].lstrip("#*").split(",")]
    data_lines = lines[header_idx + 1:]

    def col_idx(name, fallback):
        return header.index(name) if name in header else fallback

    pos = {
        "hostname": col_idx("hostname", 0),
        "ip": col_idx("ip", 1),
        "score": col_idx("score", 2),
        "ping": col_idx("ping", 3),
        "speed": col_idx("speed", 4),
        "countrylong": col_idx("countrylong", 5),
        "countryshort": col_idx("countryshort", 6),
        "sessions": col_idx("numvpnsessions", 7),
        "uptime": col_idx("uptime", 8),
        "config_b64": col_idx("openvpn_configdata_base64", len(header) - 1),
    }

    rows = []
    for ln in data_lines:
        ln = ln.lstrip("*")  # 兼容 "*" 前缀行（不再整行跳过）
        try:
            fields = next(csv.reader(io.StringIO(ln)))
        except Exception:
            continue
        if len(fields) < 7:  # 与原版基线一致的最低门槛
            continue
        host = _field(fields, pos["hostname"])
        ip = _field(fields, pos["ip"])
        if not host or not ip:
            continue
        rows.append({
            "host": host,
            "ip": ip,
            "score": _safe_int(_field(fields, pos["score"], "0"), 0),
            "ping": _safe_int(_field(fields, pos["ping"], "999"), 999),
            "speed_bps": _safe_int(_field(fields, pos["speed"], "0"), 0),
            "country_long": _field(fields, pos["countrylong"]),
            "country_short": _field(fields, pos["countryshort"]).upper(),
            "sessions": _safe_int(_field(fields, pos["sessions"], "0"), 0),
            "uptime_ms": _safe_int(_field(fields, pos["uptime"], "0"), 0),
            "config_b64": _field(fields, pos["config_b64"]),
        })
    return rows

def parse_mirror_json(data):
    servers = []
    items = data if isinstance(data, list) else [data]
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("servers"), list):
            servers.extend(item["servers"])
        elif isinstance(item, dict):
            servers.append(item)
    rows = []
    for s in servers:
        host = str(s.get("hostname") or s.get("host") or "").strip()
        ip = str(s.get("ip") or "").strip()
        if not host or not ip:
            continue
        rows.append({
            "host": host,
            "ip": ip,
            "score": _safe_int(s.get("score"), 0),
            "ping": _safe_int(s.get("ping"), 999),
            "speed_bps": _safe_int(s.get("speed"), 0),
            "country_long": str(s.get("countrylong") or s.get("country_long") or s.get("country") or "").strip(),
            "country_short": str(s.get("countryshort") or s.get("country_short") or "").strip().upper(),
            "sessions": _safe_int(s.get("numvpnsessions") or s.get("sessions"), 0),
            "uptime_ms": _safe_int(s.get("uptime"), 0),
            "config_b64": str(s.get("openvpn_configdata_base64") or s.get("config_b64") or "").strip(),
        })
    return rows

# ---------------------------------------------------------------------------
# 筛选 SSTP 节点
# ---------------------------------------------------------------------------
_PROTO_TCP_RE = re.compile(r"^proto\s+(tcp|tcp4|tcp6)\b", re.M)
_REMOTE_RE = re.compile(r"^remote\s+\S+\s+(\d+)", re.M)
_HOST_VALID_RE = re.compile(r"^[a-zA-Z0-9.-]+$")

def to_sstp_nodes(rows):
    nodes = []
    for r in rows:
        cfg = ""
        if r["config_b64"]:
            try:
                cfg = base64.b64decode(r["config_b64"], validate=False).decode("utf-8", "replace")
            except Exception:
                cfg = ""
        if not _PROTO_TCP_RE.search(cfg):
            continue
        m = _REMOTE_RE.search(cfg)
        if not m:
            continue
        port = int(m.group(1))
        if not (1 <= port <= 65535):
            continue
        host = r["host"]
        if not host.endswith(".opengw.net"):
            host = f"{host}.opengw.net"
        if not _HOST_VALID_RE.match(host):
            continue

        # R6: uptime=0 视为「未知」，绝不当成「长开机」
        uptime_hours = round(r.get("uptime_ms", 0) / 3600000.0, 1) if r.get("uptime_ms") else None
        speed_mbps = round(r.get("speed_bps", 0) / 1000000.0, 1)

        nodes.append({
            "host": host,
            "port": port,
            "ip": r["ip"],
            "country": r["country_long"],
            "country_code": r["country_short"],
            "uptime_hours": uptime_hours,
            "sessions": r.get("sessions", 0),
            "speed_mbps": speed_mbps,
            "score": r.get("score", 0),
            "ping_feed": r.get("ping", 999),
        })
    return nodes

def dedupe(nodes):
    seen = set()
    out = []
    for n in nodes:
        key = (n["host"].lower(), n["port"], "sstp")
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out

# ---------------------------------------------------------------------------
# 优选入口域名 (EDGE_HOSTS) 实时健康自检
# ---------------------------------------------------------------------------
DEFAULT_EDGE_HOSTS = [
    "saas.sin.fan:443",
    "cdn.204910.best:443",
    "www.mfyx.cn:443",
    "p.etime.vip:443",
    "cdn.ctn32.us.kg:443",
    "cf.877774.xyz:443",
    "www.visa.com.sg:443",
    "www.visa.com.hk:443",
    "icook.tw:443",
]

def verify_edge_host(entry: str, timeout: float = 3.0):
    """检测优选入口域名是否可以正常解析并完成 TCP/TLS 握手（只握手，不外发数据）"""
    try:
        if ":" in entry:
            host, port_s = entry.rsplit(":", 1)
            port = int(port_s)
        else:
            host, port = entry, 443
        t0 = time.perf_counter()
        ctx = ssl.create_default_context()
        with socket.create_connection((host, port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=host):
                rtt = (time.perf_counter() - t0) * 1000.0
                return entry, True, round(rtt, 1)
    except Exception:
        return entry, False, 9999.0

def get_healthy_edge_hosts() -> list:
    raw_env = os.environ.get("HOSTS_ENTRY", "").strip() or os.environ.get("EDGE_HOSTS", "").strip()
    candidates = [h.strip() for h in raw_env.split(",") if h.strip()] if raw_env else list(DEFAULT_EDGE_HOSTS)
    # 始终加入 Cloudflare 全球官方高可用域名作为保底
    for fallback in ("www.visa.com.sg:443", "www.visa.com.hk:443", "icook.tw:443"):
        if fallback not in candidates:
            candidates.append(fallback)

    healthy = []
    with ThreadPoolExecutor(max_workers=min(10, len(candidates))) as pool:
        futs = [pool.submit(verify_edge_host, c) for c in candidates]
        for fut in as_completed(futs):
            entry, ok, rtt = fut.result()
            if ok:
                healthy.append((rtt, entry))
            else:
                log("EDGE CHECK", f"⚠️ 剔除失效/超时优选入口域名: {entry}")

    healthy.sort(key=lambda x: x[0])
    valid_list = [x[1] for x in healthy]
    if not valid_list:
        # R7: 全失败时保留完整候选顺序并明确告警（不再把刚失败的域名当"健康兜底"）
        log("EDGE CHECK", "⚠️ 所有优选域名自检失败（CI 网络异常？），按原始列表顺序输出，请留意入口可用性")
        return candidates
    log("EDGE CHECK", f"优选入口自检通过 {len(valid_list)}/{len(candidates)} 个: {', '.join(valid_list[:6])}")
    return valid_list[:6]

# ---------------------------------------------------------------------------
# 检测与稳态评级
# ---------------------------------------------------------------------------
def classify_network(host, exit_org, is_datacenter=None):
    if is_datacenter is True:
        return "datacenter"
    if is_datacenter is False:
        return "residential"
    org = (exit_org or "").upper()
    if org:
        if any(k in org for k in DATA_CENTER_ORG_KEYWORDS):
            return "datacenter"
        if any(k in org for k in RESIDENTIAL_ORG_KEYWORDS):
            return "residential"
    h = host.lower()
    if h.startswith("public-vpn"):
        return "datacenter"
    if re.match(r"^vpn\d{5,}", h) or re.match(r"^vpnv\d+", h):
        return "residential"
    return "unknown"

# ---------------------------------------------------------------------------
# 历史缓存 / 自动黑名单 / 稳定性判定
# ---------------------------------------------------------------------------
def load_history():
    """加载历史节点缓存（cache/nodes_history.json）"""
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_history(history):
    """保存历史节点缓存"""
    os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=1)

def calc_volatility(latencies):
    """计算延迟波动率（标准差/均值），< STABILITY_THRESHOLD 为稳定"""
    if len(latencies) < 2:
        return None
    import statistics
    mean = statistics.mean(latencies)
    if mean == 0:
        return None
    return statistics.stdev(latencies) / mean

def update_history(history, results, feed_nodes):
    """
    更新历史缓存：
    - feed 中的节点（host:port）更新 last_seen
    - 检测成功的节点记录延迟
    - 检测失败的节点累加 fail_count
    - 连续失败超过阈值 → 返回需自动 ban 的列表
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    to_ban = []

    # feed 中出现的节点标记 last_seen
    feed_keys = set()
    for n in feed_nodes:
        key = f"{n['host']}:{n['port']}"
        feed_keys.add(key)
        if key not in history:
            history[key] = {"first_seen": now, "last_seen": now, "fail_count": 0, "latencies": [], "stable_rounds": 0}
        else:
            history[key]["last_seen"] = now

    # 检测结果更新
    for r in results:
        key = f"{r.get('host') or r.get('hostName')}:{r.get('port')}"
        if key not in history:
            history[key] = {"first_seen": now, "last_seen": now, "fail_count": 0, "latencies": [], "stable_rounds": 0}
        h = history[key]
        if r.get("success"):
            h["fail_count"] = 0
            lat = r.get("latency_ms")
            if lat is not None:
                h["latencies"] = (h["latencies"] + [lat])[-STABILITY_WINDOW:]
            # 稳定性判定
            vol = calc_volatility(h["latencies"])
            if vol is not None and vol < STABILITY_THRESHOLD:
                h["stable_rounds"] = h.get("stable_rounds", 0) + 1
            else:
                h["stable_rounds"] = 0
        else:
            h["fail_count"] = h.get("fail_count", 0) + 1
            h["stable_rounds"] = 0
            if h["fail_count"] >= AUTO_BAN_THRESHOLD and key not in feed_keys:
                to_ban.append(key)

    # 清理太久没出现的节点（>30 天）
    cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S UTC")
    history = {k: v for k, v in history.items() if v.get("last_seen", "") >= cutoff}

    return history, to_ban

def merge_history(uniq, history):
    """合并历史节点到检测队列（去重后）"""
    existing = {f"{n['host']}:{n['port']}" for n in uniq}
    merged = list(uniq)
    for key, h in history.items():
        if key in existing:
            continue
        if h.get("fail_count", 0) >= AUTO_BAN_THRESHOLD:
            continue  # 已在黑名单
        host, port = key.rsplit(":", 1)
        merged.append({
            "host": host, "port": int(port), "ip": "",
            "country": "未知", "country_code": "",
            "uptime_hours": None, "sessions": 0,
            "speed_mbps": 0, "score": 0, "ping_feed": 999,
        })
    return merged

def evaluate_stability(node: dict, history_entry: dict = None) -> dict:
    """
    四场景评级（全部基于实测 + feed 指标 + 历史稳定性，不臆造）:
      support_ready  ⭐客服稳连: 干净IP + 住宅 + 在线>=12h + 负载<=120 + 抖动<=150 + 延迟<=600
      game_ready     🎮游戏:     延迟<=400 + 抖动<=100 + 负载<=250（允许短开机，响应优先）
      speed_ready    ⬇️下载:     feed带宽>=阈值 + 实测可用 + 负载<=250 + 延迟<=5000 + 稳定(波动率<30%)
      ai_ready       🤖国外AI:   干净IP + 在线>=6h + 抖动<=200 + 延迟<=800
    返回 dict，直接 update 进节点。
    """
    uptime_h = node.get("uptime_hours")            # None = 未知
    sessions = node.get("sessions") or 0
    lat = node.get("latency_ms")
    jitter = node.get("jitter_ms")                 # None = 未复测
    is_res = node.get("residential") == "residential"

    # 稳定性：从历史缓存计算波动率
    vol = None
    stable = False
    if history_entry:
        vol = calc_volatility(history_entry.get("latencies", []))
        stable = vol is not None and vol < STABILITY_THRESHOLD

    exit_info = node.get("exit") or {}
    is_clean = exit_info.get("is_clean")
    if is_clean is None:
        if not exit_info:
            is_clean = None                        # 无出口情报：未知
        else:
            is_clean = not bool(exit_info.get("is_abuser") or exit_info.get("is_tor") or exit_info.get("is_bogon"))

    uptime_known = uptime_h is not None and uptime_h > 0
    jitter_known = jitter is not None
    lat_ok = lat is not None

    support_ready = bool(
        is_clean is True and is_res and uptime_known and uptime_h >= SUPPORT_MIN_UPTIME_H
        and sessions <= SUPPORT_MAX_SESSIONS and jitter_known and jitter <= SUPPORT_MAX_JITTER
        and lat_ok and lat <= SUPPORT_MAX_LATENCY
    )
    is_platinum = bool(
        support_ready and uptime_h >= 48 and sessions <= 80
        and jitter <= 80 and lat <= 450
    )
    game_ready = bool(
        lat_ok and lat <= GAME_MAX_LATENCY and jitter_known and jitter <= GAME_MAX_JITTER
        and sessions <= GAME_MAX_SESSIONS and (not uptime_known or uptime_h >= 1.0)
        and is_clean is not False
    )
    # 下载：放宽延迟到 5000ms，但要求稳定（波动率<30%）
    speed_ready = bool(
        (node.get("speed_mbps") or 0) >= SPEED_MIN_MBPS
        and sessions <= SPEED_MAX_SESSIONS and lat_ok and lat <= SPEED_MAX_LATENCY
        and stable
    )
    ai_ready = bool(
        is_clean is True and uptime_known and uptime_h >= AI_MIN_UPTIME_H
        and jitter_known and jitter <= AI_MAX_JITTER and lat_ok and lat <= AI_MAX_LATENCY
    )

    # 养鱼池：检测成功但不够格进四场景，且稳定
    fish_ready = bool(node.get("success") and not (support_ready or game_ready or speed_ready or ai_ready) and stable)

    lat_val = lat if lat_ok else 9999
    jitter_val = jitter if jitter_known else 0
    if uptime_known:
        short_lived_penalty = 300 if uptime_h < 2.0 else (100 if uptime_h < 12.0 else 0)
    else:
        short_lived_penalty = 80   # 未知在线时长：轻度不可信，但仍可参与普通排序
    crowded_penalty = 250 if sessions > 150 else (80 if sessions > 90 else 0)
    res_bonus = 0 if is_res else 120
    dirty_penalty = 500 if is_clean is False else 0
    composite = lat_val + jitter_val * 1.5 + short_lived_penalty + crowded_penalty + res_bonus + dirty_penalty

    if is_platinum:
        tier, tier_label = "platinum", "⭐客服稳连"
    elif support_ready and lat_val <= 400:
        tier, tier_label = "gold", "⚡高速稳定"
    else:
        tier, tier_label = "standard", "普通可用"

    return {
        "tier": tier,
        "tier_label": tier_label,
        "composite_score": round(composite, 1),
        "support_ready": support_ready,
        "game_ready": game_ready,
        "speed_ready": speed_ready,
        "ai_ready": ai_ready,
        "is_clean": is_clean,
        "fish_ready": fish_ready,
        "is_stable": stable,
        "volatility": round(vol, 3) if vol is not None else None,
    }

def check_one(node, session):
    url = WORKER_CHECK_URL + quote(f"{node['host']}:{node['port']}", safe="")
    out = dict(node)
    out["protocol"] = "sstp"
    out["link"] = f"sstp://{SSTP_USER}:{SSTP_PASS}@{node['host']}:{node['port']}"  # R9
    out["status"] = "failed"
    out["checked_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    out["exit"] = None
    out["residential"] = "unknown"
    out["jitter_ms"] = None     # R6: 未复测 = 未知，不是 0
    out["worker_error"] = False
    try:
        r = session.get(url, timeout=CHECK_TIMEOUT)
        if r.status_code != 200:
            out["error"] = f"HTTP {r.status_code}"
            out["worker_error"] = True
            return out
        j = r.json()
        ok = bool(j.get("success"))
        out["success"] = ok
        out["status"] = "success" if ok else "failed"
        out["latency_ms"] = j.get("responseTime")
        out["colo"] = j.get("colo")
        out["error"] = None if ok else (j.get("error") or j.get("message") or "check failed")
        exit_info = j.get("exit") or {}
        if exit_info:
            asn = exit_info.get("asn") or {}
            org = asn.get("org") or asn.get("name") or ""
            out["exit"] = {
                "ip": exit_info.get("ip"),
                "country": exit_info.get("country"),
                "country_code": (exit_info.get("country_code") or "").upper() or None,
                "city": exit_info.get("city"),
                "continent": exit_info.get("continent"),
                "asn": asn.get("asn"),
                "org": org,
                "type": asn.get("type"),
                "is_datacenter": exit_info.get("is_datacenter"),
                "is_abuser": exit_info.get("is_abuser"),
                "is_tor": exit_info.get("is_tor"),
                "is_bogon": exit_info.get("is_bogon"),
                "is_clean": exit_info.get("is_clean"),
            }
            out["residential"] = classify_network(out["host"], org, exit_info.get("is_datacenter"))
        else:
            out["residential"] = classify_network(out["host"], None, None)
        return out
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
        out["worker_error"] = True
        return out

def check_all(nodes, session):
    results = []
    with ThreadPoolExecutor(max_workers=CONCURRENCY) as pool:
        futures = [pool.submit(check_one, n, session) for n in nodes]
        for fut in as_completed(futures):
            results.append(fut.result())
    return results

def double_check_candidates(results, session):
    """
    R4: 二次复测区分「业务失败」与「Worker 抖动」：
      - 复测 worker_error（429/超时/5xx）：保留首次结果，jitter 保持未知，不冤杀节点
      - 复测业务失败：判定闪断节点，淘汰
      - 复测成功：jitter = |lat2-lat1|，latency 取两次均值
    """
    available = [r for r in results if r.get("success")]
    if not available or not DOUBLE_CHECK_TOP:
        return results

    by_c = {}
    for n in available:
        c = n["country"] or "未知"
        by_c.setdefault(c, []).append(n)

    to_recheck = []
    for c, grp in by_c.items():
        grp.sort(key=lambda x: (x.get("latency_ms") is None, x.get("latency_ms") or 9999))
        to_recheck.extend(grp[: MAX_PER_COUNTRY + 2])

    log("STABILITY CHECK", f"对各地区前排 {len(to_recheck)} 个候选节点进行第 2 次防闪断复测...")
    recheck_map = {}
    with ThreadPoolExecutor(max_workers=min(CONCURRENCY, 12)) as pool:
        fut_map = {pool.submit(check_one, n, session): n for n in to_recheck}
        for fut in as_completed(fut_map):
            orig = fut_map[fut]
            recheck_map[(orig["host"], orig["port"])] = fut.result()

    dropped = 0
    worker_shaken = 0
    for r in results:
        if not r.get("success"):
            continue
        key = (r["host"], r["port"])
        if key not in recheck_map:
            continue
        r2 = recheck_map[key]
        if r2.get("worker_error"):
            worker_shaken += 1
            continue
        if not r2.get("success"):
            r["success"] = False
            r["status"] = "failed"
            r["error"] = "Failed stability re-check (flaky node)"
            dropped += 1
            continue
        lat1 = r.get("latency_ms") or 300
        lat2 = r2.get("latency_ms") or lat1
        r["jitter_ms"] = abs(lat2 - lat1)
        r["latency_ms"] = int((lat1 + lat2) / 2)

    log("STABILITY CHECK", f"二次复测完成：剔除闪断节点 {dropped} 个；Worker 抖动保留 {worker_shaken} 个")
    return results

# ---------------------------------------------------------------------------
# 生成数据与多场景订阅产物
# ---------------------------------------------------------------------------
def build_outputs(results, raw_count, sstp_count, source, healthy_edges, history=None):
    # R2: 只用本轮实测成功的节点，绝不合并历史缓存
    available = [r for r in results if r.get("success")]
    history = history or {}

    for n in available:
        key = f"{n.get('host')}:{n.get('port')}"
        h_entry = history.get(key, {})
        n.update(evaluate_stability(n, h_entry))
        code = (n.get("country_code") or "?").upper()
        n["region_group"] = "west" if code in WEST_SUPPORT_CODES else ("asia" if code in ASIA_SUPPORT_CODES else "other")
        # 机房标记
        n["is_datacenter_node"] = n.get("residential") == "datacenter"
        # 置顶名单：免场景门槛保送全部四条通道；但脏IP 仍不得进客服/AI（pin 不覆盖安全底线）
        n["pinned"] = _host_list_match(n, PIN_HOSTS)
        if n["pinned"]:
            if n.get("is_clean") is not False:
                n["support_ready"] = True
                n["ai_ready"] = True
            n["game_ready"] = True
            n["speed_ready"] = True

    countries = {}
    for n in available:
        c = n["country"] or "未知"
        countries.setdefault(c, {"code": (n.get("country_code") or "?").upper(), "nodes": []})["nodes"].append(n)

    by_country = {}
    filtered_available = []
    for name, grp in countries.items():
        grp["nodes"].sort(key=lambda n: (
            0 if n.get("pinned") else 1,
            n.get("composite_score", 9999),
            n.get("latency_ms") or 9999,
            n.get("host") or "",
        ))
        grp["nodes"] = grp["nodes"][:MAX_PER_COUNTRY]
        grp["count"] = len(grp["nodes"])
        grp["residential"] = sum(1 for n in grp["nodes"] if n.get("residential") == "residential")
        grp["datacenter"] = sum(1 for n in grp["nodes"] if n.get("residential") == "datacenter")
        grp["support_count"] = sum(1 for n in grp["nodes"] if n.get("support_ready"))
        grp["game_count"] = sum(1 for n in grp["nodes"] if n.get("game_ready"))
        grp["speed_count"] = sum(1 for n in grp["nodes"] if n.get("speed_ready"))
        grp["ai_count"] = sum(1 for n in grp["nodes"] if n.get("ai_ready"))
        by_country[name] = grp
        filtered_available.extend(grp["nodes"])

    def _cnt(flag):
        return sum(1 for n in filtered_available if n.get(flag))

    stats = {
        "raw_nodes": raw_count,
        "sstp_nodes": sstp_count,
        "checked": len(results),
        "success": len(filtered_available),
        "failed": len(results) - len(available),
        "countries": len(by_country),
        "residential_est": sum(1 for n in filtered_available if n.get("residential") == "residential"),
        "datacenter_est": sum(1 for n in filtered_available if n.get("residential") == "datacenter"),
        "support_stable": _cnt("support_ready"),
        "game_ready": _cnt("game_ready"),
        "speed_ready": _cnt("speed_ready"),
        "ai_ready": _cnt("ai_ready"),
        "fish_ready": _cnt("fish_ready"),          # 养鱼池（稳定但暂不够格进四场景）
        "stable_count": _cnt("is_stable"),         # 波动率达标节点数
        "datacenter_count": sum(1 for n in filtered_available if n.get("is_datacenter_node")),
        "watch_count": len(data.get("watch_nodes", []) if "watch_nodes" in data else [n for n in filtered_available if not n.get("is_stable") and not (n.get("support_ready") or n.get("game_ready") or n.get("speed_ready") or n.get("ai_ready"))]),
        "healthy_edges": len(healthy_edges),
    }

    # 机房节点单独分组（好的机房 = 稳定 + 延迟相对较低）
    datacenter_nodes = [n for n in filtered_available if n.get("is_datacenter_node")]
    datacenter_premium = [n for n in datacenter_nodes if n.get("is_stable") and (n.get("latency_ms") or 9999) <= 3000]

    # 养鱼池：稳定但不够格进四场景
    fish_nodes = [n for n in filtered_available if n.get("fish_ready")]
    fish_nodes.sort(key=lambda n: (n.get("composite_score", 9999), n.get("host") or ""))

    # 观察组：不稳定但可用（波动大，观察中）
    watch_nodes = [n for n in filtered_available
                   if not n.get("fish_ready") and not (n.get("support_ready") or n.get("game_ready") or n.get("speed_ready") or n.get("ai_ready"))]
    watch_nodes.sort(key=lambda n: (n.get("volatility") or 9999, n.get("host") or ""))

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "source": source,
        "worker": mask_worker_url(WORKER_CHECK_URL),
        "edge_hosts": healthy_edges,
        "stats": stats,
        "countries": by_country,
        "available": filtered_available,
        "datacenter_nodes": datacenter_nodes,
        "datacenter_premium": datacenter_premium,
        "fish_nodes": fish_nodes,
        "watch_nodes": watch_nodes,
    }

NODES_URL = env_str("NODES_URL", "https://<your-user>.github.io/gate-system/nodes.txt")
SUBSCRIPTION_URL = env_str("SUBSCRIPTION_URL", "https://<your-user>.github.io/gate/subscription.txt")

MODE_META = {
    "all":     {"file": "nodes.txt",          "label": "全量精选"},
    "support": {"file": "nodes_support.txt",  "label": "⭐客服稳连"},
    "game":    {"file": "nodes_game.txt",     "label": "🎮游戏低延迟"},
    "speed":   {"file": "nodes_speed.txt",    "label": "⬇️下载高速"},
    "ai":      {"file": "nodes_ai.txt",       "label": "🤖国外AI"},
    "datacenter": {"file": "nodes_datacenter.txt", "label": "🖥️机房节点"},
    "fish":       {"file": "nodes_fish.txt",       "label": "🐟养鱼池"},
}

def _mode_node_ok(n, mode):
    if mode == "support":
        return n.get("support_ready") and (n.get("country_code") or "").upper() in (WEST_SUPPORT_CODES | ASIA_SUPPORT_CODES)
    if mode == "game":
        return n.get("game_ready")
    if mode == "speed":
        return n.get("speed_ready")
    if mode == "ai":
        return n.get("ai_ready")
    if mode == "datacenter":
        return n.get("is_datacenter_node")
    if mode == "fish":
        return n.get("fish_ready") or n.get("is_stable")
    return True

def _mode_node_sort(n, mode):
    if mode == "game":
        return ((n.get("latency_ms") or 9999) + (n.get("jitter_ms") or 0) * 2, n.get("host") or "")
    if mode == "speed":
        return (-(n.get("speed_mbps") or 0), n.get("latency_ms") or 9999, n.get("host") or "")
    if mode == "ai":
        return (
            0 if n.get("residential") == "residential" else 1,
            -(n.get("uptime_hours") or 0),
            n.get("jitter_ms") if n.get("jitter_ms") is not None else 9999,
            n.get("latency_ms") or 9999,
        )
    return (n.get("composite_score", 9999), n.get("host") or "")

def _mode_country_sort(kv, mode):
    cname, grp = kv
    code = str(grp.get("code") or "?").upper()
    if mode == "support":
        priority = 0 if code in (WEST_SUPPORT_CODES | ASIA_SUPPORT_CODES) else 1
        return (priority, -int(grp.get("support_count") or 0), code)
    count_key = {"game": "game_count", "speed": "speed_count", "ai": "ai_count"}.get(mode, "count")
    return (-int(grp.get(count_key) or 0), code)

def _group_by_country(nodes):
    """把节点列表转成 build_nodes_text 需要的 countries 格式"""
    countries = {}
    for n in nodes:
        cname = n.get("country") or "未知"
        countries.setdefault(cname, {"code": (n.get("country_code") or "?").upper(), "nodes": []})["nodes"].append(n)
    return countries

def build_nodes_text(data, mode="all"):
    """
    生成 EdgeTunnel / VLESS 自定义优选格式:
    每行 = 实测存活的入口地址#国家-类型-场景标签-序号$sstp://user:pass@host:port
    """
    countries = data["countries"]
    edge = data.get("edge_hosts") or DEFAULT_EDGE_HOSTS
    lines = []
    idx = 0

    def _zh(n):
        code = (n.get("country_code") or "?").upper()
        return COUNTRY_ZH.get(code) or (code if code and code != "?" else (n.get("country") or "未知"))

    def _tag(n):
        if mode == "support": return "稳连"
        if mode == "game": return "游戏"
        if mode == "speed": return "下载"
        if mode == "ai": return "AI"
        if mode == "datacenter": return "机房"
        if mode == "fish": return "养鱼"
        return "稳连" if n.get("tier") == "platinum" else ("住宅" if n.get("residential") == "residential" else "机房")

    def _emit(n, seq):
        nonlocal idx
        entry = edge[idx % len(edge)]
        idx += 1
        pin_mark = "📌" if n.get("pinned") else ""
        lines.append(f"{entry}#{_zh(n)}-{_tag(n)}-{seq}{pin_mark}$sstp://{SSTP_USER}:{SSTP_PASS}@{n['host']}:{n['port']}")
        return entry

    ordered = sorted(countries.items(), key=lambda kv: _mode_country_sort(kv, mode))

    # 1) 置顶节点：跨国家排到文件最前（序号全局连续），并附加备用入口行
    pinned_all = []
    for cname, grp in ordered:
        for n in grp["nodes"]:
            if n.get("pinned") and _mode_node_ok(n, mode):
                pinned_all.append(n)
    pinned_all.sort(key=lambda n: _mode_node_sort(n, mode))
    for i, n in enumerate(pinned_all, 1):
        used = _emit(n, f"{i:02d}")
        for k, alt in enumerate([e for e in edge if e != used][:EXTRA_ENTRY_COUNT], 1):
            lines.append(f"{alt}#{_zh(n)}-{_tag(n)}-{i:02d}备{k}$sstp://{SSTP_USER}:{SSTP_PASS}@{n['host']}:{n['port']}")

    # 2) 常规节点：按国家分组输出；每场景前 EXTRA_ENTRY_TOP 名附加备用入口行
    extra_used = 0
    for cname, grp in ordered:
        code = str(grp.get("code") or "?").upper()
        zh = COUNTRY_ZH.get(code) or (code if code and code != "?" else cname)
        nodes = [n for n in grp["nodes"] if _mode_node_ok(n, mode) and not n.get("pinned")]
        if not nodes:
            continue
        nodes.sort(key=lambda n: _mode_node_sort(n, mode))

        for i, n in enumerate(nodes, 1):
            used = _emit(n, f"{i:02d}")
            if extra_used < EXTRA_ENTRY_TOP:
                extra_used += 1
                for k, alt in enumerate([e for e in edge if e != used][:EXTRA_ENTRY_COUNT], 1):
                    lines.append(f"{alt}#{zh}-{_tag(n)}-{i:02d}备{k}$sstp://{SSTP_USER}:{SSTP_PASS}@{n['host']}:{n['port']}")

    if not lines:
        return f"# 本轮无{MODE_META.get(mode, {}).get('label', mode)}达标节点\n"
    return "\n".join(lines) + "\n"

def build_subscription_text(data):
    """标准通用订阅格式 (兼容 Hiddify / Clash / Sing-box / v2rayN 等)"""
    s = data["stats"]
    lines = [
        "# ============================================",
        "# VPN Gate SSTP 四场景精选稳连节点 - 通用订阅",
        f"# 生成时间: {data['generated_at']}",
        f"# 可用节点: {s['success']}  ⭐客服稳连: {s.get('support_stable', 0)}  🎮游戏: {s.get('game_ready', 0)}  ⬇️下载: {s.get('speed_ready', 0)}  🤖AI: {s.get('ai_ready', 0)}",
        f"# 覆盖国家: {s['countries']}",
        "# ============================================\n",
    ]
    countries = data["countries"]
    ordered = sorted(countries.items(), key=lambda kv: (-int(kv[1].get("count") or 0), str(kv[1].get("code") or kv[0])))

    for cname, grp in ordered:
        code = str(grp.get("code") or "?").upper()
        zh = COUNTRY_ZH.get(code) or (code if code and code != "?" else cname)
        for i, n in enumerate(grp["nodes"], 1):
            lat = n.get("latency_ms")
            lat_str = f"{lat}ms" if lat else "N/A"
            jitter = n.get("jitter_ms")
            jitter_str = f"{jitter}ms" if jitter is not None else "未测"
            up_h = n.get("uptime_hours")
            up_str = "-" if not up_h else (f"{round(up_h / 24, 1)}天" if up_h >= 24 else f"{up_h}h")
            kind = "住宅" if n.get("residential") == "residential" else "机房"
            tier_lbl = n.get("tier_label", "")
            scenes = []
            if n.get("support_ready"): scenes.append("客服")
            if n.get("game_ready"): scenes.append("游戏")
            if n.get("speed_ready"): scenes.append("下载")
            if n.get("ai_ready"): scenes.append("AI")
            scene_str = "/".join(scenes) if scenes else "-"
            remark = quote(f"{zh}-{kind}-{i:02d} [{tier_lbl}|{lat_str}|在线{up_str}|{scene_str}]")
            lines.append(f"# {zh} {kind} {i:02d} | 延迟={lat_str} 抖动={jitter_str} 在线={up_str} 会话={n.get('sessions', 0)} 场景={scene_str}")
            lines.append(f"sstp://{SSTP_USER}:{SSTP_PASS}@{n['host']}:{n['port']}#{remark}")
        lines.append("")
    return "\n".join(lines) + "\n"

def build_subscription_base64(data):
    lines = []
    for n in data["available"]:
        code = (n.get("country_code") or "?").upper()
        zh = COUNTRY_ZH.get(code) or code
        kind = "住宅" if n.get("residential") == "residential" else "机房"
        remark = quote(f"{zh}-{kind}-{n.get('tier_label', '')}")
        lines.append(f"sstp://{SSTP_USER}:{SSTP_PASS}@{n['host']}:{n['port']}#{remark}")
    content = "\n".join(lines)
    return base64.b64encode(content.encode("utf-8")).decode("ascii")

def write_outputs(data):
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    paths = []

    data_path = os.path.join(PUBLIC_DIR, "data.json")
    with open(data_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    paths.append(data_path)

    html_path = os.path.join(PUBLIC_DIR, "index.html")
    if os.path.exists(TEMPLATE_HTML):
        with open(TEMPLATE_HTML, "r", encoding="utf-8") as f:
            html = f.read()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        paths.append(html_path)

    for mode, meta in MODE_META.items():
        p = os.path.join(PUBLIC_DIR, meta["file"])
        with open(p, "w", encoding="utf-8") as f:
            f.write(build_nodes_text(data, mode=mode))
        paths.append(p)

    # 机房节点单独产物
    dc_path = os.path.join(PUBLIC_DIR, "nodes_datacenter.txt")
    with open(dc_path, "w", encoding="utf-8") as f:
        f.write(build_nodes_text({"countries": _group_by_country(data.get("datacenter_nodes", [])), "stats": data.get("stats", {}), "edge_hosts": data.get("edge_hosts", [])}, mode="datacenter"))
    paths.append(dc_path)

    dcp_path = os.path.join(PUBLIC_DIR, "nodes_datacenter_premium.txt")
    with open(dcp_path, "w", encoding="utf-8") as f:
        f.write(build_nodes_text({"countries": _group_by_country(data.get("datacenter_premium", [])), "stats": data.get("stats", {}), "edge_hosts": data.get("edge_hosts", [])}, mode="datacenter"))
    paths.append(dcp_path)

    # 养鱼池 + 观察组
    fish_path = os.path.join(PUBLIC_DIR, "nodes_fish.txt")
    with open(fish_path, "w", encoding="utf-8") as f:
        f.write(build_nodes_text({"countries": _group_by_country(data.get("fish_nodes", [])), "stats": data.get("stats", {}), "edge_hosts": data.get("edge_hosts", [])}, mode="fish"))
    paths.append(fish_path)

    watch_path = os.path.join(PUBLIC_DIR, "nodes_watch.txt")
    with open(watch_path, "w", encoding="utf-8") as f:
        f.write(build_nodes_text({"countries": _group_by_country(data.get("watch_nodes", [])), "stats": data.get("stats", {}), "edge_hosts": data.get("edge_hosts", [])}, mode="fish"))
    paths.append(watch_path)

    sub_path = os.path.join(PUBLIC_DIR, "subscription.txt")
    with open(sub_path, "w", encoding="utf-8") as f:
        f.write(build_subscription_text(data))
    paths.append(sub_path)

    sub_b64_path = os.path.join(PUBLIC_DIR, "subscription_base64.txt")
    with open(sub_b64_path, "w", encoding="utf-8") as f:
        f.write(build_subscription_base64(data))
    paths.append(sub_b64_path)

    return paths

# ---------------------------------------------------------------------------
# main 主函数
# ---------------------------------------------------------------------------
def main():
    # R8: 未配置 Worker 地址直接失败（源码内不保留任何私有地址）
    if not WORKER_CHECK_URL:
        die("未配置 WORKER_CHECK_URL / CHECK_WORKER 环境变量 — 请在 GitHub Secrets 中配置检测 Worker 地址后重试")

    feed_session = create_feed_session()
    check_session = create_check_session()

    # 0. 加载历史缓存（历史节点累积 + 稳定性判定数据）
    history = load_history()

    # 1. 自检优选入口域名，剔除死域名
    healthy_edges = get_healthy_edge_hosts()

    # 2. 获取上游节点
    rows, source = fetch_vpngate(feed_session)
    raw_count = len(rows)
    if raw_count == 0:
        die("VPN Gate 返回 0 个原始节点 (数据源异常, 不允许生成空结果)")

    sstp_nodes = to_sstp_nodes(rows)
    sstp_count = len(sstp_nodes)
    if sstp_count == 0:
        die(f"从 {raw_count} 个原始节点中没有解析出任何 SSTP(TCP) 节点 — 数据格式可能已变化")
    uniq = dedupe(sstp_nodes)
    # 合并历史节点（历史缓存累积，扩大检测池）
    uniq = merge_history(uniq, history)

    # 黑名单：命中即永久剔除（在送检前剔除，节省 Worker 配额）
    if BAN_HOSTS:
        before = len(uniq)
        uniq = [n for n in uniq if not _host_list_match(n, BAN_HOSTS)]
        dropped = before - len(uniq)
        if dropped:
            log("CONFIG", f"黑名单剔除 {dropped} 个节点（不送检、不出现在任何产物）")
        if not uniq:
            die("黑名单过滤后无剩余节点 — 请检查 ban.txt 是否过于激进")

    # 限额截断前先按 feed 评分/延迟 prioritize，保证留下的是账面最优
    uniq.sort(key=lambda n: (-(n.get("score") or 0), n.get("ping_feed", 999), n.get("host") or ""))
    if MAX_CHECK_NODES > 0:
        uniq = uniq[:MAX_CHECK_NODES]

    log("VPN GATE", f"获取原始节点: {raw_count} | SSTP 节点: {sstp_count} | 去重后: {len(uniq)}")
    log("CLOUDFLARE WORKER", f"提交检测: {len(uniq)} (并发 {CONCURRENCY}, 超时 {CHECK_TIMEOUT}s, Worker: {mask_worker_url(WORKER_CHECK_URL)})")

    t0 = time.time()
    results = check_all(uniq, check_session)
    results = double_check_candidates(results, check_session)
    elapsed = time.time() - t0

    success = [r for r in results if r.get("success")]
    failed = [r for r in results if not r.get("success")]
    worker_errors = [r for r in failed if r.get("worker_error")]

    # 更新历史缓存 + 自动黑名单（连续 N 次失败自动归入黑名单）
    history, to_ban = update_history(history, results, sstp_nodes)
    save_history(history)
    if to_ban:
        log("AUTO BAN", f"连续 {AUTO_BAN_THRESHOLD} 次失败的节点自动归入黑名单: {len(to_ban)} 个")
        for key in to_ban[:10]:
            log("AUTO BAN", f"  - {key}")

    # 失败原因聚合（规则2：失败不静默，落盘 + 日志）
    error_counts = {}
    for r in failed:
        err = (r.get("error") or "未知")[:80]
        error_counts[err] = error_counts.get(err, 0) + 1
    if error_counts:
        log("ERROR SUMMARY", "失败原因分布 (Top10):")
        for err, cnt in sorted(error_counts.items(), key=lambda x: -x[1])[:10]:
            log("ERROR SUMMARY", f"  [{cnt}] {err}")

    log("CLOUDFLARE WORKER", f"最终稳定可用: {len(success)} | 淘汰/失败: {len(failed)} (Worker 异常 {len(worker_errors)}) | 耗时: {elapsed:.1f}s")

    # 置顶名单中被拉黑的/检测失败的节点给出明确告警（pin 了一个死节点没意义）
    for n in failed:
        if _host_list_match(n, PIN_HOSTS):
            log("CONFIG", f"⚠️ 置顶名单中的 {n.get('host')}:{n.get('port')} 本轮检测失败，未出现在产物中")

    # R2: 本轮无任何新鲜成功节点 → 不发布、不清线，保留上次线上数据
    if not success:
        die("本轮无可用节点（Worker 异常或全部探测失败）— 保留上次线上数据，不发布空结果")

    data = build_outputs(results, raw_count, sstp_count, source, healthy_edges, history=history)
    s = data["stats"]
    log("RESULT", f"入选精选节点: {s['success']} | ⭐客服稳连 {s['support_stable']} | 🎮游戏 {s['game_ready']} | ⬇️下载 {s['speed_ready']} | 🤖AI {s['ai_ready']} | 国家 {s['countries']}")

    paths = write_outputs(data)
    for p in paths:
        log("WEBSITE", f"生成 {os.path.relpath(p, REPO_DIR)}")
    log("USAGE", f"订阅链接基线: {NODES_URL}")

if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        import traceback
        log("FATAL", f"程序异常: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        sys.exit(1)
