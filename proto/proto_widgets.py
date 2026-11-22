#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""原型共用控件：状态呈现改版的核心件（对应审计 G1/G2/G4/G5/G11/G12/G17）。

- StatusPanel：固定连接状态面板——「现在连着吗」唯一的常驻读数，只由
  会话事件读写，任何消息不许碰它（修 G1/G2）。
- MessageLine：临时消息条——过程消息/结果消息走这里，与状态面板分离。
- AlertBar：告警条——红/琥珀分档、可换行、可关闭（修 G5/G17/G18）。
- EmptyStateCard：空态卡——空库引导 / 过滤无结果两种形态（修 G11/G12）。
- PurgeButton：待清废节点按钮——待清批次数量写在按钮上（修 G4）。

不接真实 Store/引擎；不 import client 包。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QToolButton, QVBoxLayout, QWidget)

SEV_DANGER = "danger"    # 红：会话级事故（掉线/假连/端口占用/健康 dead）
SEV_WARN = "warn"        # 琥珀：需要处置的警告（系统代理冲突/引擎缺失）

_SEV_STYLE = {
    SEV_DANGER: ("#b00020", "#ffffff"),
    SEV_WARN: ("#b26a00", "#ffffff"),
}


def _dot_pixmap(color: str) -> QPixmap:
    pix = QPixmap(14, 14)
    pix.fill(Qt.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(1, 1, 12, 12)
    p.end()
    return pix


class StatusPanel(QFrame):
    """固定连接状态面板：圆点 + 状态文案（+ 连接时长）。

    写入点只有三个：set_idle / set_connecting / set_connected——演示
    「状态只有会话事件能改，消息永远写不进来」（修 G1/G2 的呈现侧答案）。
    """

    STATE_IDLE = "idle"
    STATE_CONNECTING = "connecting"
    STATE_CONNECTED = "connected"

    _DOT_COLOR = {STATE_IDLE: "#9e9e9e", STATE_CONNECTING: "#f2a900",
                  STATE_CONNECTED: "#2e7d32"}
    _TEXT = {STATE_IDLE: "未连接", STATE_CONNECTING: "连接中", STATE_CONNECTED: "已连接"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = self.STATE_IDLE
        self._node_name = ""
        self._since = 0.0
        self.setObjectName("statusPanel")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.dot_label = QLabel()
        lay.addWidget(self.dot_label)
        self.state_label = QLabel("未连接")
        self.state_label.setObjectName("statusText")
        lay.addWidget(self.state_label)
        self.detail_label = QLabel("")
        self.detail_label.setStyleSheet("color:#9a9ca6;")
        lay.addWidget(self.detail_label)
        lay.addStretch(1)
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick_duration)
        self._apply_look()

    # ---- 三个写入点（对应 orchestrator 事件映射）----
    def set_idle(self):
        self._state = self.STATE_IDLE
        self._node_name = ""
        self._since = 0.0
        self._timer.stop()
        self._apply_look()

    def set_connecting(self, node_name: str):
        """对应 connect_started 事件（现窗没接，见审计 G2）。"""
        self._state = self.STATE_CONNECTING
        self._node_name = node_name
        self._since = 0.0
        self._timer.stop()
        self._apply_look()

    def set_connected(self, node_name: str):
        self._state = self.STATE_CONNECTED
        self._node_name = node_name
        self._since = time.time()
        self._timer.start()
        self._apply_look()

    # ---- 读数 ----
    @property
    def state(self) -> str:
        return self._state

    def state_text(self) -> str:
        return self.state_label.text()

    def _apply_look(self):
        self.dot_label.setPixmap(_dot_pixmap(self._DOT_COLOR[self._state]))
        if self._state == self.STATE_IDLE:
            self.state_label.setText("未连接")
            self.detail_label.setText("")
        elif self._state == self.STATE_CONNECTING:
            self.state_label.setText(f"连接中：{self._node_name}…")
            self.detail_label.setText("正在启动引擎并验出口，请稍候")
        else:
            self.state_label.setText(f"已连接：{self._node_name}")
            self._tick_duration()
        self.style().unpolish(self)
        self.style().polish(self)

    def _tick_duration(self):
        if self._state == self.STATE_CONNECTED and self._since:
            secs = int(time.time() - self._since)
            self.detail_label.setText(f"连接时长 {secs // 60:02d}:{secs % 60:02d}")


class MessageLine(QLabel):
    """临时消息条：过程/结果消息专用，永不触碰状态面板（修 G1）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("messageLine")
        self.setWordWrap(True)
        self.setText("（消息区）操作与过程提示显示在这里，不影响上方连接状态")

    def show_message(self, text: str):
        self.setText(text)


class AlertBar(QFrame):
    """告警条：红/琥珀分档、可换行、可关闭（修 G5/G17/G18）。

    closed 信号供上层记录「用户已知悉」；关掉只隐藏本条，不改触发源。
    """

    closed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(10, 6, 4, 6)
        self.icon_label = QLabel("⚠")
        self._lay.addWidget(self.icon_label)
        self.text_label = QLabel("")
        self.text_label.setWordWrap(True)      # 修 G17：长文案换行不截断
        self._lay.addWidget(self.text_label, 1)
        self.close_btn = QToolButton()
        self.close_btn.setText("×")
        self.close_btn.setToolTip("关闭本条告警（只收起提示，不处理触发源）")
        self.close_btn.clicked.connect(self._on_close)
        self._lay.addWidget(self.close_btn)
        self.setVisible(False)
        self._severity = SEV_DANGER

    def show_alert(self, text: str, severity: str = SEV_DANGER):
        self._severity = severity
        bg, fg = _SEV_STYLE.get(severity, _SEV_STYLE[SEV_DANGER])
        self.setStyleSheet(
            f"AlertBar {{ background:{bg}; color:{fg}; border-radius:4px; }}")
        self.text_label.setText(text)
        self.setVisible(True)

    def is_showing(self) -> bool:
        return self.isVisible()

    def _on_close(self):
        self.setVisible(False)
        self.closed.emit()


class EmptyStateCard(QFrame):
    """空态卡：空库引导 / 过滤无结果两形态（修 G11/G12）。"""

    clear_filters = Signal()
    import_clicked = Signal(str)      # 按钮文案

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("emptyCard")
        lay = QVBoxLayout(self)
        lay.setAlignment(Qt.AlignCenter)
        lay.setSpacing(10)
        self.icon_label = QLabel("🗂")
        self.icon_label.setAlignment(Qt.AlignCenter)
        self.icon_label.setStyleSheet("font-size:30px; color:#6f727c;")
        lay.addWidget(self.icon_label)
        self.title_label = QLabel("这一页还没有节点")
        self.title_label.setAlignment(Qt.AlignCenter)
        self.title_label.setStyleSheet("font-size:15px; font-weight:bold;")
        lay.addWidget(self.title_label)
        self.hint_label = QLabel("")
        self.hint_label.setAlignment(Qt.AlignCenter)
        self.hint_label.setWordWrap(True)
        self.hint_label.setStyleSheet("color:#9a9ca6;")
        lay.addWidget(self.hint_label)
        self.clear_btn = QPushButton("清空过滤条件")
        self.clear_btn.clicked.connect(self.clear_filters.emit)
        self.clear_btn.setVisible(False)
        lay.addWidget(self.clear_btn, alignment=Qt.AlignCenter)
        self._action_row = QHBoxLayout()
        lay.addLayout(self._action_row)
        self.setVisible(False)

    def set_empty_library(self, import_actions):
        """空库形态：给该页的导入入口做引导（修 G11）。"""
        self.title_label.setText("这一页还没有节点")
        self.hint_label.setText("先从下面任一入口把节点导进来；"
                                "来源即归属，导进哪页就留在哪页。")
        self.clear_btn.setVisible(False)
        self._rebuild_actions(import_actions)
        self.setVisible(True)

    def set_filtered_out(self, hidden: int):
        """过滤无结果形态：说明被谁挡了 + 一键清过滤（修 G12）。"""
        self.title_label.setText(f"没有匹配的节点（被过滤隐藏了 {hidden} 个）")
        self.hint_label.setText("调整搜索词 / 场景过滤 / 显示开关，或一键清空。")
        self.clear_btn.setVisible(True)
        self._rebuild_actions([])
        self.setVisible(True)

    def set_hidden(self, hidden: int):
        """有结果但过滤挡了 N 个：不打断表格，只在卡外提示（轻形态）。"""
        self.hint_label.setText(f"另有 {hidden} 个节点被当前过滤条件隐藏")
        self.setVisible(False)

    def _rebuild_actions(self, texts):
        while self._action_row.count():
            item = self._action_row.takeAt(0)
            w = item.widget()
            if w:
                w.hide()          # 先摘出渲染树：deleteLater 是异步的，
                w.setParent(None)  # 不先摘会在 offscreen 截图里残影
                w.deleteLater()
        for t in texts:
            btn = QPushButton(t)
            btn.clicked.connect(lambda _=False, s=t: self.import_clicked.emit(s))
            self._action_row.addWidget(btn)
        self._action_row.addStretch(1)


class PurgeButton(QPushButton):
    """「一键删废节点」改版：待清批次数量直接写在按钮上（修 G4）。

    pending>0：「删上一轮废节点（N）」——语义=上一轮批量测出的那批；
    pending=0：「删本页废节点」——语义=当前页扫出来的不通+污染。
    """

    def __init__(self, parent=None):
        super().__init__("删本页废节点", parent)
        self._pending = 0
        self.setToolTip("待清批次为 0 时删本页的废节点（不通+污染）")

    def set_pending(self, count: int):
        self._pending = max(0, int(count))
        self._apply()

    @property
    def pending(self) -> int:
        return self._pending

    def _apply(self):
        if self._pending:
            self.setText(f"删上一轮废节点（{self._pending}）")
            self.setToolTip("上一轮批量测速测出的废节点数；点它删这批")
        else:
            self.setText("删本页废节点")
            self.setToolTip("待清批次为 0 时删本页的废节点（不通+污染）")
