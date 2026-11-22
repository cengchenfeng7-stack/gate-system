#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示一：状态呈现改版（对应审计 G1 / G2 / G5 / G16 / G17 / G21）。

一块面板三条通道：连接状态常驻（StatusPanel）、过程消息滚动
（MessageLine）、告警分档（AlertBar 红/琥珀）。模拟按钮把现窗的
真实事件序列重放一遍，让审查方直接对比改前/改后的呈现差异。
不接真实 Store/引擎，不 import client 包。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from proto.fake_data import format_latency
from proto.proto_widgets import (SEV_DANGER, SEV_WARN, AlertBar, MessageLine,
                                 StatusPanel)


class StatusDemo(QWidget):
    """状态呈现改版演示页。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)

        self.alert_bar = AlertBar()
        lay.addWidget(self.alert_bar)

        self.status_panel = StatusPanel()
        lay.addWidget(self.status_panel)

        self.message_line = MessageLine()
        lay.addWidget(self.message_line)

        lay.addWidget(self._label("—— 模拟事件（重放现窗真实序列，假数据）——"))
        row1 = QHBoxLayout()
        self.btn_click_connect = QPushButton("① 点「连 接」")
        self.btn_click_connect.clicked.connect(self._simulate_connect_click)
        self.btn_started = QPushButton("② connect_started 事件")
        self.btn_started.clicked.connect(self._simulate_started)
        self.btn_ok = QPushButton("③ 连接成功")
        self.btn_ok.clicked.connect(self._simulate_connected)
        for b in (self.btn_click_connect, self.btn_started, self.btn_ok):
            row1.addWidget(b)
        row1.addStretch(1)
        lay.addLayout(row1)

        row2 = QHBoxLayout()
        self.btn_health = QPushButton("健康探测 15 秒后（5745ms）")
        self.btn_health.clicked.connect(self._simulate_health_probe)
        self.btn_verify_fail = QPushButton("单节点测速失败")
        self.btn_verify_fail.clicked.connect(self._simulate_verify_fail)
        self.btn_auto = QPushButton("自动探测完成")
        self.btn_auto.clicked.connect(self._simulate_auto_done)
        for b in (self.btn_health, self.btn_verify_fail, self.btn_auto):
            row2.addWidget(b)
        row2.addStretch(1)
        lay.addLayout(row2)

        row3 = QHBoxLayout()
        self.btn_drop = QPushButton("节点掉线（红条）")
        self.btn_drop.clicked.connect(self._simulate_dropped)
        self.btn_sysproxy = QPushButton("系统代理冲突（琥珀条）")
        self.btn_sysproxy.clicked.connect(self._simulate_sysproxy)
        self.btn_long_alert = QPushButton("长文案告警（验证换行）")
        self.btn_long_alert.clicked.connect(self._simulate_long_alert)
        for b in (self.btn_drop, self.btn_sysproxy, self.btn_long_alert):
            row3.addWidget(b)
        row3.addStretch(1)
        lay.addLayout(row3)

        lay.addStretch(1)
        lay.addWidget(self._label(
            "看点：连接状态在 ①②③ 全程可读；健康探测/自动探测/测速失败只进"
            "消息区，冲不掉状态；掉线走红条、系统代理冲突走琥珀条；告警条长"
            "文案换行、右上 × 可关。"))

    @staticmethod
    def _label(text):
        lbl = QLabel(text)
        lbl.setWordWrap(True)
        lbl.setStyleSheet("color:#8a8d97;")
        return lbl

    # ---- 模拟事件序列（文案取自现窗真实事件口径）----
    def _simulate_connect_click(self):
        # 现窗此步零反馈（G2）；改版：按钮变「连接中…」+ 面板立即可读。
        self.message_line.show_message("（点连接后立即可见）正在连接 示例节点 · 甲…")
        self.status_panel.set_connecting("示例节点 · 甲")

    def _simulate_started(self):
        self.message_line.show_message("connect_started：引擎启动中，随后强制验出口")

    def _simulate_connected(self):
        self.status_panel.set_connected("示例节点 · 甲")
        self.alert_bar.setVisible(False)
        self.message_line.show_message("已连接。连接状态请看上方面板，本行只写过程消息")

    def _simulate_health_probe(self):
        # 现窗 G16：裸毫秒「延迟 5745ms」；改版走 0.3.2 口径 + 不碰状态面板。
        self.message_line.show_message(
            f"连接健康：出口探测正常，延迟 {format_latency(5745)}（15 秒一次，"
            "只写消息区）")

    def _simulate_verify_fail(self):
        # 现窗 G5：单节点测速失败也亮红条；改版降为消息区灰字。
        self.message_line.show_message("测速失败：示例节点 · 壬——链路不通或取不到出口 IP")

    def _simulate_auto_done(self):
        self.message_line.show_message(
            "自动探测完成：可连 3 · 不通 2（本轮测 5/5，可用节点已排在前面）")

    def _simulate_dropped(self):
        self.status_panel.set_idle()
        self.alert_bar.show_alert(
            "节点掉线：节点掉线，已停在原地（不会自动切换）——请手动选下一个节点。",
            SEV_DANGER)

    def _simulate_sysproxy(self):
        self.alert_bar.show_alert(
            "系统代理指向别家（127.0.0.1:7078），走系统代理的应用到不了隧道。"
            "客户端不会改你的系统代理，也不会切换节点，是否处置由你决定。",
            SEV_WARN)

    def _simulate_long_alert(self):
        self.alert_bar.show_alert(
            "启动被挡：端口 7897 被占用（占用者：another-proxy，PID 4321）。"
            "引擎起不来——关掉占用它的程序（其他代理软件？）或改它的端口，"
            "再点一次连接。客户端不会动你的系统设置，也不会自动重试。",
            SEV_DANGER)
