#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示三：空态与待清改版（对应审计 G4 / G11 / G12 / G15 待裁决项）。

- 空库引导卡 / 过滤无结果卡（EmptyStateCard 两形态）；
- 「一键删废节点」动态文案：待清批次数直接写在按钮上（G4）；
- 行内确认条：删废节点不用模态弹窗，页内出确认条（G15 的待裁决演示，
  是否替换现窗的模态确认框由主人裁定，本原型只给样子）。

不接真实 Store/引擎；不 import client 包。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QStackedLayout, QVBoxLayout, QWidget)

from proto.proto_widgets import EmptyStateCard, MessageLine, PurgeButton


class InlineConfirmBar(QFrame):
    """行内确认条（G15 待裁决演示）：替代现窗模态确认框的样子。"""

    confirmed = Signal()
    cancelled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("confirmBar")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 6, 10, 6)
        self.text_label = QLabel("")
        self.text_label.setWordWrap(True)
        lay.addWidget(self.text_label, 1)
        self.yes_btn = QPushButton("删除")
        self.yes_btn.clicked.connect(self._on_yes)
        lay.addWidget(self.yes_btn)
        self.no_btn = QPushButton("取消")
        self.no_btn.clicked.connect(self._on_no)
        lay.addWidget(self.no_btn)
        self.setVisible(False)

    def ask(self, text: str):
        self.text_label.setText(text + "（行内确认条：不抢焦点、不打断，演示用）")
        self.setVisible(True)

    def _on_yes(self):
        self.setVisible(False)
        self.confirmed.emit()

    def _on_no(self):
        self.setVisible(False)
        self.cancelled.emit()


class EmptyStateDemo(QWidget):
    """空态与待清演示页：三个场景按钮切换 + 待清废节点按钮动态文案。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        self.message_line = MessageLine()   # 先建：上面的信号要连到它

        row = QHBoxLayout()
        self.btn_library = QPushButton("场景① 空库（新用户首见）")
        self.btn_library.clicked.connect(self._show_empty_library)
        self.btn_filtered = QPushButton("场景② 过滤无结果")
        self.btn_filtered.clicked.connect(self._show_filtered_out)
        self.btn_table = QPushButton("场景③ 正常表格")
        self.btn_table.clicked.connect(self._show_table)
        for b in (self.btn_library, self.btn_filtered, self.btn_table):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        self.stack = QWidget()
        self.stack_lay = QStackedLayout(self.stack)
        self.table_stub = QLabel(
            "（正常表格区：演示二已展示；此处留位便于对比空态切换）")
        self.table_stub.setAlignment(Qt.AlignCenter)
        self.table_stub.setMinimumHeight(120)
        self.empty_card = EmptyStateCard()
        self.empty_card.import_clicked.connect(
            lambda s: self.message_line.show_message(f"（原型）{s}：不接真实导入"))
        self.empty_card.clear_filters.connect(self._on_clear_filters)
        self.stack_lay.addWidget(self.table_stub)      # index 0
        self.stack_lay.addWidget(self.empty_card)      # index 1
        lay.addWidget(self.stack, 1)

        # —— 待清废节点（G4）——
        purge_row = QHBoxLayout()
        self.purge_btn = PurgeButton()
        self.purge_btn.clicked.connect(self._on_purge)
        purge_row.addWidget(self.purge_btn)
        self.btn_batch_done = QPushButton("模拟：批量测速完成（3 个废节点）")
        self.btn_batch_done.clicked.connect(lambda: self.purge_btn.set_pending(3))
        self.btn_batch_clear = QPushButton("模拟：批次已清")
        self.btn_batch_clear.clicked.connect(lambda: self.purge_btn.set_pending(0))
        purge_row.addWidget(self.btn_batch_done)
        purge_row.addWidget(self.btn_batch_clear)
        purge_row.addStretch(1)
        lay.addLayout(purge_row)

        # —— 行内确认条（G15 待裁决演示）——
        self.confirm_bar = InlineConfirmBar()
        self.confirm_bar.confirmed.connect(self._do_purge)
        self.confirm_bar.cancelled.connect(
            lambda: self.message_line.show_message("（原型）已取消删除"))
        lay.addWidget(self.confirm_bar)

        lay.addWidget(self.message_line)

        self._show_empty_library()

    # ---- 空态两形态 ----
    def _show_empty_library(self):
        self.empty_card.set_empty_library(
            ("粘贴链接", "导入订阅文件", "订阅地址导入", "更新已登记来源"))
        self.stack_lay.setCurrentWidget(self.empty_card)

    def _show_filtered_out(self):
        self.empty_card.set_filtered_out(5)     # 被过滤隐藏 5 个（假数）
        self.stack_lay.setCurrentWidget(self.empty_card)

    def _show_table(self):
        self.stack_lay.setCurrentWidget(self.table_stub)

    def _on_clear_filters(self):
        self.message_line.show_message("（原型）已清空过滤条件——真实现会恢复表格")

    # ---- 待清批次与确认 ----
    def _on_purge(self):
        n = self.purge_btn.pending
        if n:
            self.confirm_bar.ask(
                f"上一轮批量测出 {n} 个废节点，连同测速记录一起删，删了恢复不了。")
        else:
            self.confirm_bar.ask("本页有 2 个不通、1 个污染节点，删前先报数。")

    def _do_purge(self):
        n = self.purge_btn.pending
        self.message_line.show_message(f"（原型）已删除 {max(n, 3)} 个废节点")
        self.purge_btn.set_pending(0)
