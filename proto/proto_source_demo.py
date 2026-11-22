#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""演示二：来源页改版（对应审计 G6 / G7 / G8 / G10 / G13 / G14 / G19 / G22 / G23）。

- 过滤行与导入行拆开（G13/G14）；计数紧跟过滤条件；
- 搜索框（G7）；排序下拉不动推荐序口径本体（G8）；
- 9→10 列：标记独立成列（G23）、空值统一「—」（G22）、最近列带日期（G6）；
- 右键菜单 + 双击连接提示（G10）；
- 空态卡两形态叠在表格位（G11/G12，EmptyStateCard 复用）。

不接真实 Store/引擎；数据是 fake_data.make_fake_nodes() 的虚构节点。
"""
from __future__ import annotations

import sys
from pathlib import Path

_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox,
                               QHeaderView, QHBoxLayout, QLabel,
                               QLineEdit, QMenu, QPushButton, QStackedLayout,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from proto.fake_data import (SCENE_LABELS, SORT_MODES, FakeNode,
                             make_fake_nodes)
from proto.proto_widgets import EmptyStateCard, MessageLine

COLUMNS = ("名称", "标记", "协议", "出口", "类型", "干净", "延迟",
           "稳定性", "最近", "场景")

_IMPORT_ACTIONS = ("粘贴链接", "导入订阅文件", "订阅地址导入", "更新已登记来源")


class ProtoSourcePage(QWidget):
    """改版后的一个来源页（虚构来源「示例页」，组织维度不改）。"""

    node_message = Signal(str)       # 行内动作的反馈 → 消息区

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nodes = make_fake_nodes()
        lay = QVBoxLayout(self)
        lay.setSpacing(4)

        # —— 第一行：过滤（高频）+ 计数紧跟过滤条件（G13/G14）——
        bar = QHBoxLayout()
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按名称/出口搜索（G7 新增）")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self.render)
        bar.addWidget(self.search_edit, 1)
        bar.addWidget(QLabel("场景："))
        self.scene_combo = QComboBox()
        self.scene_combo.addItem("全部", None)
        for sc in SCENE_LABELS:
            self.scene_combo.addItem(SCENE_LABELS[sc], sc)
        self.scene_combo.currentIndexChanged.connect(self.render)
        bar.addWidget(self.scene_combo)
        self.show_quarantined_chk = QCheckBox("显示隔离")
        self.show_quarantined_chk.stateChanged.connect(self.render)
        bar.addWidget(self.show_quarantined_chk)
        self.show_banned_chk = QCheckBox("显示已拉黑")
        self.show_banned_chk.stateChanged.connect(self.render)
        bar.addWidget(self.show_banned_chk)
        self.count_label = QLabel("")
        self.count_label.setStyleSheet("color:#9a9ca6;")
        bar.addWidget(self.count_label)          # 计数贴着过滤条件，不再错位
        lay.addLayout(bar)

        # —— 第二行：排序（G8）+ 该页导入按钮（低频，靠右次要视觉 G14）——
        bar2 = QHBoxLayout()
        bar2.addWidget(QLabel("排序："))
        self.sort_combo = QComboBox()
        for name, _key in SORT_MODES:
            self.sort_combo.addItem(name)
        self.sort_combo.currentIndexChanged.connect(self.render)
        bar2.addWidget(self.sort_combo)
        self.sort_tip = QLabel("默认「推荐序」＝现行口径，本表只是加可选档")
        self.sort_tip.setStyleSheet("color:#8a8d97;")
        bar2.addWidget(self.sort_tip)
        bar2.addStretch(1)
        for text in _IMPORT_ACTIONS:
            btn = QPushButton(text)
            btn.clicked.connect(
                lambda _=False, s=text: self.node_message.emit(f"（原型）{s}：不接真实导入"))
            bar2.addWidget(btn)
        lay.addLayout(bar2)

        # —— 表格区叠空态卡（G11/G12）——
        self.stack = QWidget()
        self.stack_lay = QStackedLayout(self.stack)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, len(COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.itemDoubleClicked.connect(
            lambda it: self.node_message.emit(
                f"（原型）双击行＝连接：{self._node_at(it.row()).name}"))
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.stack_lay.addWidget(self.table)
        self.empty_card = EmptyStateCard()
        self.empty_card.clear_filters.connect(self.clear_filters)
        self.empty_card.import_clicked.connect(
            lambda s: self.node_message.emit(f"（原型）{s}：不接真实导入"))
        self.stack_lay.addWidget(self.empty_card)
        lay.addWidget(self.stack, 1)

        self.message_line = MessageLine()
        lay.addWidget(self.message_line)
        self.node_message.connect(self.message_line.show_message)

        self.render()

    # ---- 过滤与排序（G7/G8/G12）----
    def visible_nodes(self):
        text = self.search_edit.text().strip().lower()
        scene = self.scene_combo.currentData()
        out = []
        for n in self.nodes:
            if n.banned and not self.show_banned_chk.isChecked():
                continue
            if n.quarantined and not self.show_quarantined_chk.isChecked():
                continue
            if scene and scene not in n.scenes:
                continue
            if text and text not in n.name.lower() \
                    and text not in n.ip and text not in n.country_code.lower():
                continue
            out.append(n)
        return out

    def clear_filters(self):
        self.search_edit.clear()
        self.scene_combo.setCurrentIndex(0)
        self.show_quarantined_chk.setChecked(True)
        self.show_banned_chk.setChecked(True)
        self.render()

    # ---- 渲染（G6/G22/G23 口径在 FakeNode 上）----
    def render(self, *_args):
        visible = self.visible_nodes()
        hidden = len(self.nodes) - len(visible)
        mode = SORT_MODES[self.sort_combo.currentIndex()][1]
        rows = sorted(visible, key=mode)

        self.table.setRowCount(0)
        for n in rows:
            r = self.table.rowCount()
            self.table.insertRow(r)
            values = (n.name, n.marks(), n.protocol, n.exit_text(),
                      n.type_text(), n.clean_text(), n.latency_text(),
                      n.stability_text(), n.recent_text(), n.scenes_text())
            for col, val in enumerate(values):
                item = QTableWidgetItem(val)
                if col == 0:
                    item.setData(Qt.UserRole, n.nid)   # 行号≠节点序号，按 id 找
                if col == 1:
                    item.setToolTip(n.mark_tip())
                if col == 0 and n.active:
                    item.setText(val + "　（当前连接）")
                if n.active:
                    item.setBackground(Qt.darkCyan)
                self.table.setItem(r, col, item)

        self.count_label.setText(
            f"{len(rows)} 个节点" if rows else "0 个节点")
        if not self.nodes:
            self.empty_card.set_empty_library(_IMPORT_ACTIONS)
            self.stack_lay.setCurrentWidget(self.empty_card)
        elif not rows:
            self.empty_card.set_filtered_out(hidden)     # 修 G12
            self.stack_lay.setCurrentWidget(self.empty_card)
        else:
            if hidden:
                self.empty_card.set_hidden(hidden)
            self.stack_lay.setCurrentWidget(self.table)

    # ---- 行内动作（G10：右键菜单）----
    def _node_at(self, row: int):
        """按表格行取节点：名称列 UserRole 里存的节点 id（行序≠列表序）。"""
        item = self.table.item(row, 0)
        nid = item.data(Qt.UserRole) if item else None
        return next((n for n in self.nodes if n.nid == nid), None)

    def build_context_menu(self) -> QMenu:
        menu = QMenu(self)
        for text in ("连接", "测速", "置顶/取消", "拉黑/取消", "删除"):
            act = menu.addAction(text)
            act.triggered.connect(lambda _=False, s=text: self._row_action(s))
        return menu

    def _show_context_menu(self, pos):
        if self.table.currentRow() < 0:
            return
        menu = self.build_context_menu()
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def _row_action(self, action: str):
        row = self.table.currentRow()
        if row < 0:
            return
        node = self._node_at(row)
        if node is None:
            return
        if action == "置顶/取消":
            node.pinned = not node.pinned
            self.node_message.emit(f"（原型）已{'置顶' if node.pinned else '取消置顶'}：{node.name}")
        elif action == "拉黑/取消":
            node.banned = not node.banned
            self.node_message.emit(f"（原型）已{'拉黑' if node.banned else '取消拉黑'}：{node.name}")
        elif action == "删除":
            self.nodes.remove(node)
            self.node_message.emit(f"（原型）已删除：{node.name}（确认条形态见演示三）")
        elif action == "连接":
            self.node_message.emit(f"（原型）连接请求：{node.name}（状态演示见演示一）")
        elif action == "测速":
            self.node_message.emit(f"（原型）测速请求：{node.name}")
        self.render()
