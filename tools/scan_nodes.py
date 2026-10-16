#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
免费节点筛选 (scan_nodes.py)
===========================
从订阅链接拉节点，做两级筛选：

    第一级（自动）: 解析 + TCP 可达性实测 —— 剔除死节点、按名字归类国家
    第二级（需配合）: 真实出口 IP / 国家 / 是否机房 —— 必须真连一次才知道，
                     节点名会骗人。生成 mihomo 测试配置，导入后本脚本用
                     mihomo API 自动逐个真连出报告。

为什么必须真连:
    名字写「US美国」的，出口可能在香港；
    出口真在美国的，也可能是个 Amazon 机房段，照样被 Meta 拒。

用法:
    python scan_nodes.py --url <订阅链接>            # 第一级：筛选 + 产出文件
    python scan_nodes.py --url <订阅链接> --probe     # 第二级：真连验出口（需先导入配置）
    python scan_nodes.py --url <订阅链接> --probe --muse   # 顺带测 muse.ai 能否注册
"""

import argparse
import base64
import json
import socket
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
OUT_DIR = Path(__file__).parent / "out"
CTRL = "http://127.0.0.1:9099"

# 名字里的国家关键词 -> ISO2（顺序有意义，先匹配更具体的）
COUNTRY_PATTERNS = [
    ("US", ["美国", "United States", "USA", "🇺🇸", " 美 ", "US|"]),
    ("CA", ["加拿大", "Canada", "🇨🇦", "CA|"]),
    ("HK", ["香港", "Hong Kong", "HK", "🇭🇰"]),
    ("JP", ["日本", "Japan", "JP", "🇯🇵"]),
    ("KR", ["韩国", "Korea", "KR", "🇰🇷"]),
    ("TW", ["台湾", "Taiwan", "TW", "🇹🇼"]),
    ("SG", ["新加坡", "狮城", "Singapore", "SG", "🇸🇬"]),
    ("RU", ["俄罗斯", "Russia", "RU", "🇷🇺"]),
    ("GB", ["英国", "United Kingdom", "UK", "GB", "🇬🇧"]),
    ("DE", ["德国", "Germany", "DE", "🇩🇪"]),
    ("FR", ["法国", "France", "FR", "🇫🇷"]),
    ("NL", ["荷兰", "Netherlands", "NL", "🇳🇱"]),
    ("ID", ["印尼", "印度尼西亚", "Indonesia", "ID", "🇮🇩"]),
    ("TH", ["泰国", "Thailand", "TH", "🇹🇭"]),
    ("VN", ["越南", "Vietnam", "VN", "🇻🇳"]),
    ("TR", ["土耳其", "Turkey", "TR", "🇹🇷"]),
    ("IN", ["印度", "India", "IN", "🇮🇳"]),
    ("AU", ["澳大利亚", "澳洲", "Australia", "AU", "🇦🇺"]),
    ("AR", ["阿根廷", "Argentina", "AR", "🇦🇷"]),
    ("BR", ["巴西", "Brazil", "BR", "🇧🇷"]),
]
TARGET = {"US", "CA"}


# ---------------------------------------------------------------------------
# 拉取 + 解码
# ---------------------------------------------------------------------------
def fetch_text(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout,
                                context=ssl.create_default_context()) as r:
        return r.read().decode("utf-8", "replace")


def maybe_b64decode(text):
    t = "".join(text.split())
    if len(t) > 200 and not t.startswith(("vless://", "vmess://", "trojan://",
                                          "ss://", "hysteria2://", "anytls://", "socks://")):
        pad = "=" * ((4 - len(t) % 4) % 4)
        try:
            return base64.b64decode(t + pad).decode("utf-8", "replace")
        except Exception:
            pass
    return text


# ---------------------------------------------------------------------------
# 解析
# ---------------------------------------------------------------------------
def node_name(link):
    frag = link.split("#", 1)[1] if "#" in link else ""
    return urllib.parse.unquote(frag) or "(无名)"


def guess_country(name):
    for code, kws in COUNTRY_PATTERNS:
        for kw in kws:
            if kw in name:
                return code
    return "??"


def parse_node(link):
    """尽量提取 server/port，失败返回 None"""
    try:
        scheme = link.split("://", 1)[0].lower()
        if scheme == "vmess":
            b = link.split("://", 1)[1].split("#")[0]
            b += "=" * ((4 - len(b) % 4) % 4)
            d = json.loads(base64.b64decode(b).decode("utf-8", "replace"))
            return {"proto": "vmess", "server": d.get("add"), "port": int(d.get("port") or 0)}
        body = link.split("://", 1)[1]
        if "@" in body:
            hostport = body.split("@", 1)[1].split("?", 1)[0].split("#", 1)[0]
            host = urllib.parse.unquote(hostport.rsplit(":", 1)[0].strip("[]")) \
                if ":" in hostport else hostport
            port = int(hostport.rsplit(":", 1)[1]) if ":" in hostport else 0
            return {"proto": scheme, "server": host, "port": port}
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# TCP 可达性实测
# ---------------------------------------------------------------------------
def tcp_alive(server, port, timeout=5.0):
    if not server or not port:
        return False, 99999.0
    t0 = time.perf_counter()
    try:
        with socket.create_connection((server, port), timeout=timeout):
            return True, (time.perf_counter() - t0) * 1000
    except Exception:
        return False, 99999.0


# ---------------------------------------------------------------------------
# mihomo 配置生成（供 --probe 用）
# ---------------------------------------------------------------------------
def to_mihomo(link):
    """vless/trojan/ss/hysteria2/anytls/socks -> mihomo proxy dict"""
    scheme = link.split("://", 1)[0].lower()
    body = link.split("://", 1)[1]
    frag = urllib.parse.unquote(link.split("#", 1)[1]) if "#" in link else "node"
    try:
        if scheme in ("vless", "trojan", "anytls"):
            userinfo, rest = body.split("@", 1)
            user = urllib.parse.unquote(userinfo)
            hostport, _, qs = rest.partition("?")
            host, _, port = hostport.partition(":")
            q = urllib.parse.parse_qs(qs)
            g = lambda k, d=None: (q.get(k) or [d])[0]
            p = {
                "name": frag,
                "type": {"vless": "vless", "trojan": "trojan",
                         "anytls": "anytls"}[scheme],
                "server": urllib.parse.unquote(host),
                "port": int(port),
            }
            if scheme == "vless":
                p["uuid"] = user
                p["udp"] = True
            else:
                p["password"] = user
            p["tls"] = (g("security", "none") != "none") or (scheme == "trojan")
            if p["tls"]:
                p["servername"] = g("sni") or g("host") or p["server"]
                if g("fp"):
                    p["client-fingerprint"] = g("fp")
            if g("type") in ("ws", "grpc", "http", "h2"):
                net = {"ws": "ws", "grpc": "grpc", "http": "http", "h2": "http"}[g("type")]
                p["network"] = net
                if net == "ws":
                    p["ws-opts"] = {"path": urllib.parse.unquote(g("path", "/") or "/")}
                    if g("host"):
                        p["ws-opts"]["headers"] = {"Host": g("host")}
                elif net == "grpc":
                    p["grpc-opts"] = {"grpc-service-name": urllib.parse.unquote(g("path", ""))}
                else:
                    p["http-opts"] = {"path": [urllib.parse.unquote(g("path", "/") or "/")]}
                    if g("host"):
                        p["http-opts"]["headers"] = {"Host": [g("host")]}
            return p
        if scheme == "hysteria2":
            userinfo, rest = body.split("@", 1)
            hostport, _, qs = rest.partition("?")
            host, _, port = hostport.partition(":")
            q = urllib.parse.parse_qs(qs)
            return {
                "name": frag, "type": "hysteria2",
                "server": urllib.parse.unquote(host), "port": int(port),
                "password": urllib.parse.unquote(userinfo.split(":")[-1]),
                "sni": (q.get("sni") or [urllib.parse.unquote(host)])[0],
            }
        if scheme == "ss":
            b = body.split("#")[0]
            b += "=" * ((4 - len(b) % 4) % 4)
            dec = base64.b64decode(b).decode("utf-8", "replace")
            if "@" in dec:
                cred, _, hp = dec.rpartition("@")
                m, pwd = (cred.split(":", 1) + [""])[:2]
            else:
                m, pwd, hp = "", "", dec
            host, _, port = hp.partition(":")
            return {"name": frag, "type": "ss", "server": host.strip("[]"),
                    "port": int(port), "cipher": "aes-128-gcm", "password": pwd}
        if scheme == "socks":
            hp = body.split("@")[-1].split("?")[0]
            host, _, port = hp.partition(":")
            return {"name": frag, "type": "socks5",
                    "server": host.strip("[]"), "port": int(port)}
    except Exception:
        return None
    return None


def unique_names(proxies):
    """mihomo 拒绝重名节点 —— 这批源 64 个 US 节点名字完全一样，必须逐个加序号"""
    seen = {}
    for p in proxies:
        base = p.get("name") or "node"
        seen[base] = seen.get(base, 0) + 1
        p["name"] = f"{base} #{seen[base]}"
    return proxies


def write_mihomo_config(proxies, path):
    proxies = unique_names(proxies)
    lines = [
        "# 自动生成 —— 只含候选节点，用于真连验出口",
        "# 导入 Clash Verge 后，外部控制器会开在 127.0.0.1:9099，",
        "# 然后运行 scan_nodes.py --probe 即可自动逐个真连出报告。",
        "mixed-port: 7898",
        "external-controller: 127.0.0.1:9099",
        "log-level: warning",
        "mode: global",
        "proxies:",
    ]
    for p in proxies:
        lines.append("  - " + json.dumps(p, ensure_ascii=False))
    lines += [
        "proxy-groups:",
        "  - name: TEST",
        "    type: select",
        "    proxies:",
    ]
    for p in proxies:
        lines.append(f"      - {json.dumps(p['name'], ensure_ascii=False)}")
    lines += [
        "rules:",
        "  - MATCH,TEST",
        "",
    ]
    Path(path).write_text("\n".join(lines), "utf-8")
    return len(proxies)


# ---------------------------------------------------------------------------
# probe: 借 mihomo API 真连
# ---------------------------------------------------------------------------
def probe_via_mihomo(links, test_muse):
    try:
        urllib.request.urlopen(f"{CTRL}/version", timeout=5).read()
    except Exception:
        print("❌ 连不上 mihomo 控制器 127.0.0.1:9099")
        print("   请先在 Clash Verge 里导入 out/mihomo_probe.yaml 这个配置档，")
        print("   并确保「外部控制器」开着（端口 9099），再跑本命令。")
        return None

    import urllib.request as ur
    proxies = ur.urlopen(f"{CTRL}/proxies", timeout=10).read()
    all_p = json.loads(proxies).get("proxies", {})
    names = [n for n in all_p if n != "TEST"]
    order = {n: i for i, n in enumerate(names)}
    links_by_name = {node_name(l): l for l in links}

    proxy = "http://127.0.0.1:7898"
    results = []
    for i, name in enumerate(sorted(names, key=lambda n: order[n]), 1):
        req = ur.Request(f"{CTRL}/proxies/TEST", method="PUT",
                         data=json.dumps({"name": name}).encode(),
                         headers={"Content-Type": "application/json"})
        try:
            ur.urlopen(req, timeout=10).read()
        except Exception:
            continue
        time.sleep(0.6)
        row = {"name": name, "proto": name.split()[0] if " " in name else "?"}
        # 出口
        try:
            op = ur.build_opener(ur.ProxyHandler({"http": proxy, "https": proxy}))
            with op.open(ur.Request("https://ipinfo.io/json",
                                    headers={"User-Agent": UA}), timeout=20) as r:
                d = json.loads(r.read().decode())
            row.update(ip=d.get("ip"), cc=(d.get("country") or "??").upper(),
                       org=d.get("org"), city=d.get("city"))
        except Exception:
            row.update(ip=None, cc=None, org=None, city=None)
        if test_muse and row.get("ip"):
            try:
                class NR(ur.HTTPRedirectHandler):
                    def redirect_request(self, *a, **k):
                        return None
                op2 = ur.build_opener(ur.ProxyHandler({"http": proxy, "https": proxy}), NR)
                with op2.open(ur.Request("https://muse.ai/",
                                         headers={"User-Agent": UA}), timeout=25) as r:
                    row["muse"] = r.status
            except urllib.error.HTTPError as e:
                row["muse"] = e.code
            except Exception:
                row["muse"] = 0
        results.append(row)
        print(f"[{i}/{len(names)}] {name[:44]:<44} "
              f"{(row.get('cc') or 'DEAD'):<3} {str(row.get('ip') or '')[:15]:<16} "
              f"{'muse=' + str(row.get('muse')) if test_muse else ''}")
    return results


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="免费节点筛选 + 真连验出口")
    ap.add_argument("--url", required=True, help="订阅链接")
    ap.add_argument("--out-dir", default=str(OUT_DIR))
    ap.add_argument("--probe", action="store_true", help="借 mihomo API 逐个真连验出口")
    ap.add_argument("--muse", action="store_true", help="真连时顺带测 muse.ai")
    ap.add_argument("--timeout", type=float, default=5.0, help="TCP 探测超时（秒）")
    ap.add_argument("--workers", type=int, default=32, help="TCP 并发数")
    args = ap.parse_args()

    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # 拉取
    print(f"[{datetime.now():%H:%M:%S}] 拉取订阅 …")
    raw = fetch_text(args.url)
    text = maybe_b64decode(raw)
    links = [l.strip() for l in text.splitlines()
             if l.strip() and "://" in l]
    # 去重（保留顺序）
    seen, uniq = set(), []
    for l in links:
        if l not in seen:
            seen.add(l)
            uniq.append(l)
    links = uniq
    print(f"    共 {len(links)} 个节点")

    # 协议统计
    protos = {}
    for l in links:
        p = l.split("://", 1)[0].lower()
        protos[p] = protos.get(p, 0) + 1
    print("    协议: " + ", ".join(f"{k}×{v}" for k, v in
                                  sorted(protos.items(), key=lambda x: -x[1])))

    # 名字国家分布（不可信，仅作参考）
    country_claim = {}
    for l in links:
        c = guess_country(node_name(l))
        country_claim[c] = country_claim.get(c, 0) + 1
    print("    名字自称国家: " + ", ".join(f"{k}×{v}" for k, v in
                                          sorted(country_claim.items(), key=lambda x: -x[1])[:10]))
    claimed_target = sum(v for k, v in country_claim.items() if k in TARGET)
    print(f"    → 自称美/加的有 {claimed_target} 个（名字不可信，必须实测）")

    # TCP 实测
    parsed = [(l, parse_node(l)) for l in links]
    todo = [(l, p) for l, p in parsed if p and p.get("server") and p.get("port")]
    print(f"[{datetime.now():%H:%M:%S}] TCP 实测 {len(todo)} 个 …")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        results = list(ex.map(lambda t: tcp_alive(t[1]["server"], t[1]["port"],
                                                  args.timeout), todo))
    alive = [todo[i][0] for i, r in enumerate(results) if r[0]]
    alive_rt = [r[1] for r in results if r[0]]
    dead = len(todo) - len(alive)
    print(f"    通 {len(alive)} 个 / 死 {dead} 个"
          + (f"，存活节点 TCP 握手中位 {sorted(alive_rt)[len(alive_rt)//2]:.0f}ms"
             if alive_rt else ""))

    # 分类产出
    def cc(l):
        return guess_country(node_name(l))

    us_ca = [l for l in alive if cc(l) in TARGET]
    (out / "nodes_us_ca.txt").write_text("\n".join(us_ca), "utf-8")
    (out / "nodes_all_alive.txt").write_text("\n".join(alive), "utf-8")
    (out / "nodes_all.txt").write_text("\n".join(links), "utf-8")

    # mihomo 配置（给 probe 用）
    mp = [p for p in (to_mihomo(l) for l in us_ca) if p]
    if mp:
        n = write_mihomo_config(mp, out / "mihomo_probe.yaml")
        print(f"\n→ 已生成 {out/'mihomo_probe.yaml'}（{n} 个美/加候选，供真连测试）")

    print(f"\n{'='*60}")
    print(f"总节点        : {len(links)}")
    print(f"TCP 通        : {len(alive)}    死: {dead}")
    print(f"自称美/加且通 : {len(us_ca)}")
    print(f"{'='*60}")
    print(f"产出文件:")
    print(f"  {out/'nodes_us_ca.txt'}      自称美/加且 TCP 通的节点")
    print(f"  {out/'nodes_all_alive.txt'}  全部存活节点（其他国家，按你说的保留）")
    print(f"  {out/'nodes_all.txt'}        原始全量（含死的）")

    if args.probe:
        print("\n[真连阶段] 借 mihomo API 逐个验真实出口 …\n")
        r = probe_via_mihomo(us_ca, args.muse)
        if r:
            (out / "probe_result.json").write_text(
                json.dumps(r, ensure_ascii=False, indent=2), "utf-8")
            ok = [x for x in r if x.get("cc") in TARGET]
            print(f"\n{'='*60}")
            print(f"真连结果: 测 {len(r)} 个，出口真在美/加的 {len(ok)} 个")
            for x in ok:
                print(f"  ✅ {x['name'][:40]:<40} {x.get('cc')} {x.get('ip')}"
                      + (f" muse={x.get('muse')}" if args.muse else ""))
            print(f"{'='*60}")
            print(f"明细已存 {out/'probe_result.json'}")


if __name__ == "__main__":
    main()
