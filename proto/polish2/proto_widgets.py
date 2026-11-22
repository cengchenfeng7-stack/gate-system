#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task29 原型控件库（proto/proto_widgets.py）。

第二轮改版的关键控件形态，全部原型自足（不 import client 包、不用模态
对话框）：分档可关闭告警条、会话状态面板、可折叠分区、行内确认条、
带标记列/页脚槽的板块表。只管呈现形态，不接任何真实数据管道。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QToolButton, QVBoxLayout,
    QWidget,
)

import fake_data

# 分档配色（R5/N1：红=会话事故，琥珀=需处置警告；灰=过程消息走消息行）
LEVEL_RED = "red"
LEVEL_AMBER = "amber"

_ALERT_STYLES = {
    LEVEL_RED: "background:#b00020;color:white;padding:8px;font-weight:bold;",
    LEVEL_AMBER: "background:#3a2c12;color:#f2c063;padding:8px;"
                 "border:1px solid #b26a00;font-weight:bold;",
}
# 活动节点行高亮底色（N12/R9：非 selection 的状态色）
ACTIVE_ROW_BG = "#1f3a2a"


class AlertBar(QWidget):
    """分档可关闭告警条（R5/N1 形态）：红/琥珀两档 + 自动换行 + 「×」关闭。

    关闭只隐藏本条（不动任何触发源状态——语义与实树 _clear_alert 一致）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.label = QLabel("")
        self.label.setWordWrap(True)          # G17 教训：长文案必须换行
        lay.addWidget(self.label, 1)
        self.close_btn = QPushButton("×")
        self.close_btn.setFixedWidth(28)
        self.close_btn.setToolTip("关闭本条提示（不影响任何连接状态）")
        self.close_btn.clicked.connect(self.close_alert)
        lay.addWidget(self.close_btn)
        self.setVisible(False)

    def show_alert(self, text: str, level: str = LEVEL_RED):
        if level not in _ALERT_STYLES:
            raise ValueError(f"未知告警档位: {level}")
        self.label.setText(text)
        self.setStyleSheet(_ALERT_STYLES[level])
        self.setVisible(True)

    def close_alert(self):
        self.setVisible(False)
        self.label.setText("")

    @property
    def visible_text(self) -> str:
        return self.label.text() if self.isVisible() else ""


class SessionPanel(QFrame):
    """①区常驻状态面板（实树 SessionPanel 同形态）：圆点 + 大状态字。
    消息/告警永远写不进来——状态与消息并存不互冲是本原型的行为断言之一。
    """

    _DOT_COLOR = {"未连接": "#9e9e9e", "连接中": "#f2a900",
                  "已连接": "#2e7d32", "已断开": "#b00020"}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("statusPanel")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.dot_label = QLabel("●")
        lay.addWidget(self.dot_label)
        self.state_label = QLabel("未连接")
        self.state_label.setObjectName("statusText")
        self.state_label.setStyleSheet(
            "font-size:20px;font-weight:bold;padding:2px 6px;")
        lay.addWidget(self.state_label)
        lay.addStretch(1)
        self._apply()

    def set_state(self, state: str):
        if state not in self._DOT_COLOR:
            raise ValueError(f"未知会话状态: {state}")
        self.state_label.setText(state)
        self._apply()

    @property
    def state(self) -> str:
        return self.state_label.text()

    def _apply(self):
        self.dot_label.setStyleSheet(
            f"color:{self._DOT_COLOR[self.state]};font-size:22px;")


class CollapsibleSection(QWidget):
    """可折叠分区（N3/N16 形态）：标题行（箭头 + 标题 + 状态摘要）+
    可收起正文。收起时不丢信息——摘要就在标题行里。"""

    def __init__(self, title: str, summary: str, body: QWidget,
                 collapsed: bool = True, parent=None):
        super().__init__(parent)
        self._body = body
        self._collapsed = True        # 内部记账：不依赖 isVisible（页签
                                      # 未显示时可见性会向下传播为假）
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        header = QHBoxLayout()
        self.toggle = QToolButton()
        self.toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.toggle.setCheckable(True)
        header.addWidget(self.toggle)
        self.summary_label = QLabel(summary)
        self.summary_label.setStyleSheet("color:#9a9ca6;")
        header.addWidget(self.summary_label, 1)
        lay.addLayout(header)
        lay.addWidget(body)
        self.toggle.clicked.connect(self._on_toggle)
        self._title = title
        self.set_collapsed(collapsed)

    def _on_toggle(self, checked: bool):
        self.set_collapsed(not checked)

    def set_collapsed(self, collapsed: bool):
        self._collapsed = bool(collapsed)
        self._body.setVisible(not collapsed)
        self.toggle.setChecked(not collapsed)
        arrow = "▾" if not collapsed else "▸"
        self.toggle.setText(f"{arrow} {self._title}")

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def set_summary(self, text: str):
        self.summary_label.setText(text)


class ConfirmBar(QFrame):
    """行内确认条（R4 演示形态，**仅供 G15 裁决演示**）：破坏性操作前
    在条内给「确认/取消」，无模态打断。语义与模态一致：一次确认、
    默认焦点在取消（危险项不默认通过）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("confirmBar")
        lay = QHBoxLayout(self)
        self.label = QLabel("")
        self.label.setWordWrap(True)
        lay.addWidget(self.label, 1)
        self.yes_btn = QPushButton("确认")
        self.yes_btn.clicked.connect(self._on_yes)
        lay.addWidget(self.yes_btn)
        self.no_btn = QPushButton("取消")
        self.no_btn.clicked.connect(self.cancel)
        lay.addWidget(self.no_btn)
        self._pending = None
        self.setVisible(False)

    def show_confirm(self, text: str, confirm_label: str, on_yes):
        """展示确认条；on_yes 在「确认」点击后回调（无模态）。"""
        self.label.setText(text)
        self.yes_btn.setText(confirm_label)
        self._pending = on_yes
        self.setVisible(True)

    def _on_yes(self):
        cb, self._pending = self._pending, None
        self.cancel()
        if cb is not None:
            cb()

    def cancel(self):
        self._pending = None
        self.setVisible(False)


class BoardTable(QWidget):
    """②区板块表改版形态（N6/N11/N12/N13/N8）：过滤行（搜索+排序）+
    十列表（名称/标记/协议/出口/类型/干净/延迟/稳定性/最近/特殊站）+
    空态卡 + 页脚「最近一轮」槽。

    - 空值统一「—」；最近列 M/D HH:MM；
    - 标记列独立（●活动/📌置顶/☠隔离），活动行整行高亮底色；
    - 排序下拉默认「推荐序」（第 0 档不动排序口径）；
    - 搜索纯前端过滤（名称/出口子串）。
    """

    COLUMNS = ("名称", "标记", "协议", "出口", "类型", "干净", "延迟",
               "稳定性", "最近", "特殊站")
    COL_MARKS = 1

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)

        bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按名称/出口搜索")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._refill)
        bar.addWidget(self.search_edit, 1)
        self.sort_combo = QComboBox()
        self.sort_combo.addItem("推荐序", "default")
        self.sort_combo.addItem("延迟↑", "latency")
        self.sort_combo.addItem("最近成功↓", "last_ok")
        self.sort_combo.currentIndexChanged.connect(self._refill)
        bar.addWidget(self.sort_combo)
        lay.addLayout(bar)

        self.empty_hint = QLabel(fake_data.EMPTY_BOARD_TEXT)
        self.empty_hint.setWordWrap(True)
        self.empty_hint.setObjectName("emptyCard")
        self.empty_hint.setStyleSheet(
            "color:#9a9ca6;padding:16px;border:1px dashed #3c3f49;"
            "border-radius:8px;")
        lay.addWidget(self.empty_hint)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels(self.COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(self.COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        header.setToolTip("双击行＝连接；行内动作在右键菜单（连接/置顶/测速/测特殊站）")
        lay.addWidget(self.table)

        # 页脚「最近一轮」槽（N8：批量摘要常驻，不与过程消息互冲）
        self.footer_slot = QLabel("")
        self.footer_slot.setObjectName("footerSlot")
        self.footer_slot.setStyleSheet("color:#8fd694;padding:2px;")
        self.footer_slot.setWordWrap(True)
        lay.addWidget(self.footer_slot)

        self._title = title
        self._refill()

    # ---------- 数据回填（原型内静态，测试可复驱） ----------
    def set_footer(self, text: str):
        self.footer_slot.setText(text)

    def _rows(self):
        rows = [n for n in fake_data.NODES
                if self._title is None or n["board"] == self._title]
        mode = self.sort_combo.currentData()
        if mode == "latency":
            def lat(n):
                s = n["latency"].split()[0]
                return int(s) if s.isdigit() else 10 ** 9
            rows = sorted(rows, key=lat)
        elif mode == "last_ok":
            rows = sorted(rows, key=lambda n: 0 if "可连" in n["last"] else 1)
        return rows

    def _refill(self):
        needle = self.search_edit.text().strip().lower()
        rows = self._rows()
        if needle:
            rows = [n for n in rows if needle in n["name"].lower()
                    or needle in n["exit"].lower()]
        self.table.setRowCount(0)
        for n in rows:
            row = self.table.rowCount()
            self.table.insertRow(row)
            mark_text = "".join({"active": "●", "pinned": "📌",
                                 "quarantined": "☠"}.get(m, "")
                                for m in n["marks"])
            last_s = "—" if n["last_ts"] is None else (
                n["last"] + " "
                + time.strftime("%m/%d %H:%M", time.localtime(n["last_ts"])))
            values = (n["name"], mark_text or "—", n["protocol"], n["exit"],
                      n["type"], n["clean"], n["latency"], n["stab"],
                      last_s, n["special"])
            for col, val in enumerate(values):
                item = QTableWidgetItem(str(val))
                if "active" in n["marks"]:
                    item.setBackground(QColor_bg(ACTIVE_ROW_BG))
                self.table.setItem(row, col, item)
        self.empty_hint.setVisible(len(rows) == 0)
        self.table.setVisible(len(rows) > 0)

    @property
    def row_count(self) -> int:
        return self.table.rowCount()


def QColor_bg(hex_color: str):
    """QColor 薄封装（表格行底色；独立函数便于无 GUI 测试打桩观察）。"""
    from PySide6.QtGui import QColor
    return QColor(hex_color)
