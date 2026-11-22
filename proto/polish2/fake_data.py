#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task29 原型假数据（proto/fake_data.py）。

红线：假数据 only——RFC 5737 保留 IP（192.0.2.x / 198.51.100.x /
203.0.113.x）、example 保留域（example.com 等）、虚构名称；不出现任何
真实节点地址/订阅内容/密钥/真实站点域名。时间戳用固定 epoch，保证
offscreen 截图与测试逐字节可复现。
"""

# 固定 epoch（2026-11-21 14:23 / 2026-11-22 09:05 本地时间形态）
TS_YESTERDAY = 1763704580   # 「最近」列演示跨夜：11/21 14:23
TS_TODAY = 1763785500       # 11/22 09:05

# 节点行假数据（dict 形态，原型表直接消费；字段名与实树列口径对齐）
NODES = [
    {"id": "n1", "name": "示例住宅-01", "protocol": "vless",
     "exit": "JP 192.0.2.11", "type": "住宅", "clean": "干净",
     "latency": "45 ms", "stab": "100% · 稳定", "last": "可连",
     "last_ts": TS_TODAY, "special": "3/3",
     "marks": ("active", "pinned"), "board": "免费住宅"},
    {"id": "n2", "name": "示例机房-07", "protocol": "trojan",
     "exit": "SG 198.51.100.23", "type": "机房", "clean": "脏（vpn）",
     "latency": "120 ms", "stab": "83%", "last": "可连",
     "last_ts": TS_TODAY, "special": "2/3 · 示例AI 403",
     "marks": (), "board": "免费机房"},
    {"id": "n3", "name": "示例流水线-12", "protocol": "ss",
     "exit": "—", "type": "未知", "clean": "—", "latency": "—",
     "stab": "—", "last": "未测", "last_ts": None, "special": "—",
     "marks": ("quarantined",), "board": "免费机房"},
    {"id": "n4", "name": "示例住宅-02", "protocol": "vless",
     "exit": "US 203.0.113.9", "type": "住宅", "clean": "干净",
     "latency": "210 ms", "stab": "67%", "last": "不通",
     "last_ts": TS_YESTERDAY, "special": "1/3 · 示例AI 403、示例搜索 不通",
     "marks": ("pinned",), "board": "免费住宅"},
    {"id": "n5", "name": "示例机房-21", "protocol": "ss",
     "exit": "NL 192.0.2.77", "type": "机房", "clean": "干净",
     "latency": "89 ms", "stab": "95% · 稳定", "last": "可连",
     "last_ts": TS_TODAY, "special": "3/3",
     "marks": (), "board": "免费机房"},
]

# ⑥区清单假站（全部 example 保留域）
FAKE_SITES = ("示例AI (example-ai.example)", "示例搜索 (search.example)",
              "示例门户 (portal.example.com)")

# 告警/消息演示文案（口径对齐实树，数据虚构）
ALERT_DEAD = ("节点疑似已死：连续 5 次探测失败。连接保持原样、绝不自动换"
              "节点——请手动断开，再自己选下一个节点。")
ALERT_SUSPECT = ("连接可疑：连续 3 次探测失败，仍保持连接、不自动切换。"
                 "可再观察一会，或自行断开后另选节点。")
MSG_CONNECTED = ("测速通过：出口 192.0.2.11 / 日本(JP) / 住宅，延迟 45 ms")
def batch_summary(board: str = "免费住宅") -> str:
    """批量摘要文案（N8 页脚槽演示；板块名随所在页）。"""
    return (f"最近一轮（{board}）：可连 5 · 不通 2 · 跳过 0"
            "（共测 7/7）· 吞吐实测 3")


BATCH_SUMMARY = batch_summary()
EMPTY_BOARD_TEXT = ("本板块暂无节点。节点从后台入库并完成归类后会出现在"
                    "这里；板块是归类不是排名，连接永远由你自己点。")
