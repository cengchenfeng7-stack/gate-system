#!/usr/bin/env python3
"""
场景分流客户端配置生成器 (mihomo / Clash.Meta)
=============================================
读取 gate 流水线产出的 web/data.json，生成一份拿来即用的 mihomo 配置：

  - 四条场景通道预制成 proxy-group（⭐客服/🎮游戏/⬇️下载/🤖AI），默认 select 固定选择
  - 场景分流规则：客服工单域名 -> 稳连组；海外AI域名 -> AI组；游戏商店/语音 -> 游戏组
  - 全程不启用 URL-Test 自动切换（IP 漂移就是客服/AI 掉线的元凶），只允许手动切换

用法:
  python gen_client_config.py                          # 默认读 ./web/data.json，输出 ./client.yaml
  python gen_client_config.py --data ./web/data.json --out ./client.yaml

Worker 订阅地址（四个场景的 VLESS Worker 订阅，通常来自同一 Worker 的不同优选IP配置）:
  CP_SUPPORT_URL / CP_GAME_URL / CP_SPEED_URL / CP_AI_URL
未设置的场景组退化为 [DIRECT] 并在配置头部生成醒目的 TODO 注释。
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# 场景分流规则表（DOMAIN-SUFFIX，所有 mihomo/meta 内核通用，无需外部规则文件）
# ---------------------------------------------------------------------------
AI_DOMAINS = [
    "openai.com", "chatgpt.com", "chat.com", "oai.com", "sentry.io",
    "anthropic.com", "claude.ai", "gemini.google.com", "bard.google.com",
    "generativeai.google", "makersuite.google.com", "deepmind.google",
    "perplexity.ai", "poe.com", "x.ai", "grok.com", "twitter.com", "x.com",
    "midjourney.com", "stability.ai", "huggingface.co", "civitai.com",
    "runwayml.com", "leonardo.ai", "elevenlabs.io", "suno.com", "udio.com",
]

SUPPORT_DOMAINS = [
    "zendesk.com", "intercom.io", "intercom.com", "freshdesk.com", "freshworks.com",
    "helpscout.com", "drift.com", "salesforce.com", "force.com", "hubspot.com",
    "olark.com", "livechatinc.com", "tawk.to", "gorgias.com", "kayako.com",
    "uservoice.com", "zoho.com", "ringcentral.com", "frontapp.com", "plain",
    "crisp.chat", "smartsupp.com", "tidio.com", "zoho.eu", "niceincontact.com",
]

GAME_DOMAINS = [
    "steampowered.com", "steamcommunity.com", "steamserver.net", "steamstatic.com",
    "steam-chat.com", "valve.net", "riotgames.com", "leagueoflegends.com",
    "playvalorant.com", "blizzard.com", "battle.net", "worldofwarcraft.com",
    "epicgames.com", "fortnite.com", "nintendo.com", "nintendo.net",
    "ea.com", "origin.com", "ubisoft.com", "uplay.com", "rockstargames.com",
    "konami.com", "square-enix.com", "garena.com", "xbox.com", "xboxlive.com",
    "playstation.com", "playstation.net", "sonyentertainmentnetwork.com",
    "hoyoverse.com", "levelinfinite.com", "discord.gg", "discord.com", "discord.media",
]

SCENES = [
    {"key": "support", "name": "⭐客服稳连", "env": "CP_SUPPORT_URL",
     "note": "找欧美/亚洲客服、工单、长连接办公专用（干净IP+住宅+长在线+低抖动）"},
    {"key": "game", "name": "🎮游戏低延迟", "env": "CP_GAME_URL",
     "note": "外服游戏商店/登录/语音（实时对战走IP，域名规则只覆盖这部分；整机走VPN请用 vpn_dial.py）"},
    {"key": "speed", "name": "⬇️下载高速", "env": "CP_SPEED_URL",
     "note": "大文件下载（高带宽优先）。此组建议手动切换使用"},
    {"key": "ai", "name": "🤖国外AI", "env": "CP_AI_URL",
     "note": "ChatGPT / Claude / Gemini 等风控严格的海外AI（干净IP+长在线）"},
]

def load_data(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        print(f"[警告] 无法读取 {path}: {exc}（将生成纯模板配置）", file=sys.stderr)
        return {}

def data_age_minutes(data: dict) -> float:
    ts = data.get("generated_at")
    if not ts:
        return -1
    try:
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - t).total_seconds() / 60.0
    except Exception:
        return -1

def build_config(data: dict) -> str:
    s = data.get("stats") or {}
    age = data_age_minutes(data)
    missing = [sc["name"] for sc in SCENES if not os.environ.get(sc["env"], "").strip()]

    L = []
    L.append("# ================================================================")
    L.append("# 自动生成 by gen_client_config.py — VPN Gate 四场景分流配置 (mihomo/Clash.Meta)")
    L.append(f"# 数据时间: {data.get('generated_at', '未知')} | 来源: {data.get('source', '未知')}")
    L.append(f"# 场景节点数: ⭐客服 {s.get('support_stable', 0)} | 🎮游戏 {s.get('game_ready', 0)} | "
             f"⬇️下载 {s.get('speed_ready', 0)} | 🤖AI {s.get('ai_ready', 0)} | 全量 {s.get('success', 0)}")
    if 0 <= age and age >= 75:
        L.append(f"# ⚠️⚠️ 数据已过期 {int(age // 60)} 小时 {int(age % 60)} 分钟——最近一次巡检失败，节点评级可能已失真！")
    if missing:
        L.append("# ⚠️ TODO: 以下场景未设置 Worker 订阅地址，对应组目前只有 DIRECT（等于该场景不生效）:")
        for sc in SCENES:
            if not os.environ.get(sc["env"], "").strip():
                L.append(f"#   - {sc['name']}: 请设置环境变量 {sc['env']} 后重新生成")
    L.append("#")
    L.append("# ⚠️ 铁律：四个场景组都是 select 固定选择——选定一个节点后切勿开启 URL-Test 自动切换，")
    L.append("#    IP 一变，客服系统和海外 AI 会立刻断开你的会话要求重连。")
    L.append("# ================================================================")
    L.append("")
    L.append("mixed-port: 7890")
    L.append("socks-port: 7891")
    L.append("allow-lan: false")
    L.append("mode: rule")
    L.append("log-level: info")
    L.append("ipv6: false")
    L.append("external-controller: 127.0.0.1:9090")
    L.append("unified-delay: true")
    L.append("tcp-concurrent: false")
    L.append("geodata-mode: true")
    L.append("geo-auto-update: false")
    L.append("")
    L.append("profile:")
    L.append("  store-selected: true")
    L.append("  store-fake-ip: true")
    L.append("")
    L.append("dns:")
    L.append("  enable: true")
    L.append("  ipv6: false")
    L.append("  listen: 0.0.0.0:1053")
    L.append("  enhanced-mode: fake-ip")
    L.append("  fake-ip-range: 198.18.0.1/16")
    L.append("  fake-ip-filter:")
    L.append("    - '*.lan'")
    L.append("    - '*.local'")
    L.append("    - localhost.ptlogin2.qq.com")
    L.append("    - '+.stun.*'")
    L.append("    - '+.in-addr.arpa'")
    L.append("    - '+.ip6.arpa'")
    L.append("    - 'time.*.com'")
    L.append("    - 'time.*.gov'")
    L.append("  default-nameserver:")
    L.append("    - 223.5.5.5")
    L.append("    - 119.29.29.29")
    L.append("  nameserver:")
    L.append("    - https://dns.alidns.com/dns-query")
    L.append("    - https://doh.pub/dns-query")
    L.append("  fallback:")
    L.append("    - https://dns.google/dns-query")
    L.append("    - https://cloudflare-dns.com/dns-query")
    L.append("  fallback-filter:")
    L.append("    geoip: true")
    L.append("    geoip-code: CN")
    L.append("  proxy-server-nameserver:")
    L.append("    - 223.5.5.5")
    L.append("    - https://dns.alidns.com/dns-query")
    L.append("")
    L.append("proxy-providers:")
    for sc in SCENES:
        url = os.environ.get(sc["env"], "").strip()
        L.append(f"  {sc['key']}:")
        if url:
            L.append("    type: http")
            L.append(f"    url: {url}")
            L.append("    interval: 1800")
            L.append(f"    path: ./providers/{sc['key']}.yaml")
            L.append("    health-check:")
            L.append("      enable: true")
            L.append("      interval: 600")
            L.append("      url: https://www.gstatic.com/generate_204")
        else:
            L.append("    type: inline")
            L.append("    payload: []")
    L.append("")
    L.append("proxy-groups:")
    for sc in SCENES:
        L.append(f"  - name: \"{sc['name']}\"")
        L.append("    type: fallback")
        L.append("    disable-udp: false")
        L.append(f"    use:")
        L.append(f"      - {sc['key']}")
        L.append("    proxies:")
        L.append("      - DIRECT")
        L.append("    url: https://www.gstatic.com/generate_204")
        L.append("    interval: 60")
        L.append(f"    # {sc['note']}")
        L.append("")
    L.append("  - name: \"🚀默认出口\"")
    L.append("    type: fallback")
    L.append("    use:")
    L.append("      - support")
    L.append("    proxies:")
    L.append("      - DIRECT")
    L.append("    url: https://www.gstatic.com/generate_204")
    L.append("    interval: 60")
    L.append("    # 未命中任何场景规则的流量走这里；自动按顺序尝试")
    L.append("")
    L.append("rules:")
    L.append("  # ---- 🤖 海外 AI（风控严格，必须干净IP）----")
    for d in AI_DOMAINS:
        L.append(f"  - DOMAIN-SUFFIX,{d},\"🤖国外AI\"")
    L.append("  # ---- ⭐ 客服/工单/长连接办公 ----")
    for d in SUPPORT_DOMAINS:
        L.append(f"  - DOMAIN-SUFFIX,{d},\"⭐客服稳连\"")
    L.append("  # ---- 🎮 游戏商店/登录/语音（实时对战走IP，见组内注释）----")
    for d in GAME_DOMAINS:
        L.append(f"  - DOMAIN-SUFFIX,{d},\"🎮游戏低延迟\"")
    L.append("  # ---- 下载大文件想走带宽组，可自行追加：----")
    L.append("  # - DOMAIN-SUFFIX,example.com,\"⬇️下载高速\"")
    L.append("  # ---- 国内直连 / 兜底 ----")
    L.append("  - GEOSITE,CN,DIRECT")
    L.append("  - GEOIP,CN,DIRECT,no-resolve")
    L.append("  - GEOSITE,PRIVATE,DIRECT")
    L.append("  - MATCH,\"🚀默认出口\"")
    L.append("")
    return "\n".join(L)

def main():
    ap = argparse.ArgumentParser(description="生成 mihomo/Clash 四场景分流配置")
    ap.add_argument("--data", default=os.path.join("web", "data.json"), help="data.json 路径")
    ap.add_argument("--out", default="client.yaml", help="输出配置路径")
    args = ap.parse_args()

    data = load_data(args.data)
    cfg = build_config(data)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(cfg)

    s = data.get("stats") or {}
    print(f"✅ 已生成 {args.out}")
    print(f"   场景节点: ⭐{s.get('support_stable', 0)} 🎮{s.get('game_ready', 0)} "
          f"⬇️{s.get('speed_ready', 0)} 🤖{s.get('ai_ready', 0)}")
    missing = [sc["env"] for sc in SCENES if not os.environ.get(sc["env"], "").strip()]
    if missing:
        print(f"   ⚠️ 未配置订阅地址的场景组已退化为 DIRECT: {', '.join(missing)}")
    print("   用法: 在 mihomo/mihomo-party/Hiddify(Clash内核) 中导入本文件；")
    print("        每个场景组选定一个节点后请勿开启自动切换（URL-Test）。")

if __name__ == "__main__":
    main()
