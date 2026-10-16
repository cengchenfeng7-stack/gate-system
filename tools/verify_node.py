#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
节点验号 (verify_node.py) v2
============================
切完节点跑一条命令，直接告诉你三件事：
    1. 真实出口 IP + 国家 + 运营商（节点名会骗人，必须实测）
    2. 是不是 Cloudflare 段（muse.ai 专门拒这个）
    3. muse.ai 能不能注册（307=能 / 400=拒 / 000=链路死）

用法:
    python verify_node.py                        # 直连模式（Clash TUN 下直接跑）
    python verify_node.py --proxy 127.0.0.1:7897   # 指定代理端口（HTTP 代理模式）
    python verify_node.py --watch 15            # 每 15 秒测一次，切节点时自动刷新

判定:
    307  → Meta 收了这个出口，可以去注册 muse.ai
    400  → 出口 IP 被 Meta 拒（Cloudflare 段最常见），换节点
    403  → Cloudflare Bot 防护拦，换节点
    000  → 链路死（Clash 显示的延迟好看不代表能用，这步专抓假的）
"""

import argparse
import json
import ssl
import sys
import time
import urllib.error
import urllib.request

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36")
MUSE = "https://muse.ai/"

# 存活探针：全部非 Cloudflare，免得 CF 节点被误判成「隧道不通」
LIVENESS = [
    ("https://api.ipify.org", "ip"),
    ("https://icanhazip.com", "ip"),
    ("https://www.google.com/generate_204", "raw"),
]
IP_API = "http://ip-api.com/json/{ip}?lang=en&fields=status,message,country,countryCode,city,isp,org,as,query"

# 这些运营商/ASN 一旦出现，基本就是机房段（muse.ai 会拒）
DATACENTER_HINTS = ("cloudflare", "amazon", "google", "microsoft", "digitalocean",
                    "hetzner", "ovh", "contabo", "vultr", "linode", "azure",
                    "alibaba", "tencent", "choopa", "psychz", "racknerd")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


def opener(proxy):
    ctx = ssl.create_default_context()
    handlers = [urllib.request.HTTPSHandler(context=ctx),
                urllib.request.ProxyHandler({"http": proxy, "https": proxy}
                                         if proxy else {}), NoRedirect]
    return urllib.request.build_opener(*handlers)


def fetch(op, url, timeout=25):
    try:
        with op.open(urllib.request.Request(url, headers={"User-Agent": UA}),
                     timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""
    except Exception:
        return 0, ""


def verdict_muse(code):
    return {
        307: ("✅ 可注册", "Meta 收了这个出口，快去注册"),
        200: ("✅ 可注册", "站点正常响应"),
        400: ("❌ Meta 拒了", "出口 IP 被风控（Cloudflare 段最常见），换节点"),
        403: ("❌ 被拦", "Cloudflare Bot 防护拦截，换节点"),
        0:  ("💀 链路死", "节点不通，Clash 的延迟数字是假的"),
    }.get(code, (f"⚠️ {code}", "未知响应"))


def probe(proxy, test_muse=True):
    op = opener(proxy)

    # 1) 存活：逐个非 CF 探针试
    ip = None
    alive = False
    for url, kind in LIVENESS:
        code, body = fetch(op, url, timeout=20)
        if code in (200, 204):
            alive = True
            if kind == "ip" and body.strip():
                ip = body.strip().splitlines()[0]
            break
    print("=" * 58)
    if not alive:
        print("💀 隧道不通 —— 节点连不上（Clash 显示的延迟不可信）")
        print("=" * 58)
        return False
    print(f"[隧道] ✅ 通")

    # 2) 出口 IP + 来路
    if not ip:
        for url, _ in LIVENESS:
            code, body = fetch(op, url, timeout=15)
            if code == 200 and body.strip():
                cand = body.strip().splitlines()[0]
                if cand.count(".") == 3 and all(p.isdigit() for p in cand.split(".")):
                    ip = cand
                    break
    if not ip:
        print("[出口] 取不到 IP")
    else:
        info = {}
        try:
            with urllib.request.urlopen(
                    urllib.request.Request(IP_API.format(ip=ip),
                                         headers={"User-Agent": UA}), timeout=15) as r:
                info = json.loads(r.read().decode())
        except Exception:
            pass
        cc = (info.get("countryCode") or "??").upper()
        isp = info.get("isp") or "?"
        org = info.get("org") or "?"
        asn = info.get("as") or "?"
        city = info.get("city") or "?"
        blob = f"{isp} {org} {asn}".lower()
        dc = any(h in blob for h in DATACENTER_HINTS)
        tag = ""
        if cc in ("US", "CA"):
            tag = "🎯 目标地区" + ("，但机房段 ❌" if dc else "，住宅段 ✅")
        else:
            tag = "❌ 不是美/加"
        if dc and cc in ("US", "CA"):
            tag = "⚠️ 美/加但机房段 —— muse.ai 大概率拒"
        print(f"[出口] {ip}")
        print(f"      {city} / {cc}   {isp}")
        print(f"      {org}  ({asn})")
        print(f"      {tag}")

    # 3) muse.ai
    if test_muse:
        code, _ = fetch(op, MUSE, timeout=25)
        title, advice = verdict_muse(code)
        print(f"[muse] HTTP {code}  →  {title}")
        print(f"       {advice}")
        print("=" * 58)
        return code in (200, 307)
    print("=" * 58)
    return True


def main():
    ap = argparse.ArgumentParser(description="验证当前节点：真出口 + 是否机房 + muse.ai 能否注册")
    ap.add_argument("--proxy", default=None,
                    help="本地代理地址；不给则直连（Clash TUN 模式）")
    ap.add_argument("--watch", type=int, default=0, help="秒数；给了就循环测")
    ap.add_argument("--no-muse", action="store_true", help="不测 muse.ai，只看出口")
    args = ap.parse_args()

    print(f"模式: {'直连 (TUN)' if not args.proxy else '代理 ' + args.proxy}\n")
    if not args.watch:
        probe(args.proxy, not args.no_muse)
        return
    print(f"每 {args.watch} 秒测一次，切节点后自动刷新（Ctrl+C 停止）\n")
    try:
        while True:
            probe(args.proxy, not args.no_muse)
            time.sleep(max(3, args.watch))
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()
