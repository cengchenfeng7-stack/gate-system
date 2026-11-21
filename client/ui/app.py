#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图形界面（ui/app.py）—— M3：PySide6 窗口 + 托盘 + 常驻会话
=============================================================
与 CLI 共用同一套核心接线（直接复用 cli._build_orchestrator），保证
「界面操作与 CLI 行为一致」（设计第四节 M3 验收）：

- 四个来源页签（来源是第一组织维度，用户 2026-11-19 定），场景只是
  页内徽标/过滤；节点排序走 history.sorted_nodes（养鱼池口径）。
- 连接/断开/验出口全部经 Orchestrator——手动点选、连后强制验出口、
  掉线只告警停在原地，绝不自动切换（铁律在 orchestrator 里写死）。
- 会话在窗口进程内常驻：关窗最小化到托盘，托盘显示连接状态；
  巡检定时器调 orchestrator.poll()，掉线/假连 → 红色告警条 + 托盘提示。
- 连接/验出口/导入订阅等慢操作放工作线程，事件经 Qt 信号回主线程，
  界面不冻结。
- 数据红线同 CLI：只读本地库；除来源拉取与验出口外不发网络请求；
  订阅地址不回显、不写日志。

用法:
  python -m client.ui.app [--db 路径]
缺 PySide6 时打印安装指引并退出（不假装能跑）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from ..core import history
from ..core.model import SCENES, SOURCE_LABELS, SOURCES
from ..core.orchestrator import Orchestrator, OrchestratorError
from ..core.store import Store
from ..core.verify import apply_outcome, make_real_probe, verify_exit
from ..engines.base import EngineError
from ..engines.singbox_engine import SINGBOX_PROTOCOLS, SingBoxEngine
from ..sources import feed as feed_src
from ..sources import manual as manual_src
from ..sources import pipeline as pipeline_src
from ..sources import subscription as subscription_src
from .cli import DEFAULT_DB, _build_orchestrator

SCENE_LABELS = {"support": "客服", "ai": "AI", "games": "游戏", "download": "下载"}
NETWORK_LABELS = {"residential": "住宅", "datacenter": "机房", "unknown": "未知"}

# 深色主题（2026-11-21 用户反馈默认样式太素）：炭黑底 + 蓝紫强调色。
# 只管观感，不碰业务逻辑；main() 里 app.setStyleSheet 一次性挂上。
DARK_QSS = """\
QWidget { background:#1b1c20; color:#e8e8ee; font-size:13px; }
QMainWindow, QDialog { background:#1b1c20; }
QToolTip { background:#23252b; color:#e8e8ee; border:1px solid #3c3f49; padding:4px; }

QPushButton {
    background:#2b2d34; color:#e8e8ee;
    border:1px solid #3c3f49; border-radius:6px; padding:6px 14px; }
QPushButton:hover { background:#35383f; border-color:#4a4e5a; }
QPushButton:pressed { background:#26282e; }
QPushButton:disabled { background:#232428; color:#6f727c; border-color:#2e3036; }
QPushButton#primaryBtn {
    background:#5b5bd6; color:#ffffff; border:none;
    font-size:15px; font-weight:bold; padding:9px 30px; }
QPushButton#primaryBtn:hover { background:#6c6ce2; }
QPushButton#primaryBtn:pressed { background:#4d4dbf; }
QPushButton#primaryBtn:disabled { background:#3a3b55; color:#8b8cb0; border:none; }

QTabWidget::pane { border:1px solid #2e3038; border-radius:4px; top:-1px; }
QTabBar::tab {
    background:transparent; color:#9a9ca6; padding:7px 16px;
    border:1px solid transparent;
    border-top-left-radius:6px; border-top-right-radius:6px; }
QTabBar::tab:selected { background:#23252b; color:#ffffff; border-color:#2e3038; }
QTabBar::tab:hover:!selected { color:#c6c8d0; }

QTableWidget {
    background:#1e2025; alternate-background-color:#22242a;
    gridline-color:#2b2d35; border:1px solid #2e3038; border-radius:4px;
    selection-background-color:#3a3b6e; selection-color:#ffffff; }
QTableCornerButton::section { background:#262831; border:none; }
QHeaderView::section {
    background:#262831; color:#b9bcc7; border:none;
    border-right:1px solid #2e3038; padding:6px 8px; font-weight:bold; }

QComboBox {
    background:#2b2d34; border:1px solid #3c3f49;
    border-radius:6px; padding:4px 10px; }
QComboBox:hover { border-color:#5b5bd6; }
QComboBox::drop-down { border:none; width:22px; }
QComboBox::down-arrow {
    width:0; height:0;
    border-left:5px solid transparent; border-right:5px solid transparent;
    border-top:6px solid #9a9ca6; }
QComboBox QAbstractItemView {
    background:#23252b; color:#e8e8ee; border:1px solid #3c3f49;
    selection-background-color:#3a3b6e; selection-color:#ffffff; }

QCheckBox { color:#c9cbd3; spacing:6px; }
QCheckBox::indicator {
    width:16px; height:16px; border:1px solid #3c3f49;
    border-radius:4px; background:#23252b; }
QCheckBox::indicator:hover { border-color:#5b5bd6; }
QCheckBox::indicator:checked { background:#5b5bd6; border-color:#5b5bd6; }

QLineEdit {
    background:#23252b; border:1px solid #3c3f49;
    border-radius:6px; padding:5px 8px; }
QLineEdit:focus { border-color:#5b5bd6; }

QMenu {
    background:#23252b; color:#e8e8ee;
    border:1px solid #3c3f49; border-radius:6px; padding:4px; }
QMenu::item { padding:6px 24px; border-radius:4px; }
QMenu::item:selected { background:#3a3b6e; }
QMenu::separator { height:1px; background:#3c3f49; margin:4px 8px; }

QScrollBar:vertical { background:transparent; width:10px; margin:2px; }
QScrollBar::handle:vertical { background:#33363f; border-radius:5px; min-height:30px; }
QScrollBar::handle:vertical:hover { background:#434652; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QScrollBar:horizontal { background:transparent; height:10px; margin:2px; }
QScrollBar::handle:horizontal { background:#33363f; border-radius:5px; min-width:30px; }
QScrollBar::handle:horizontal:hover { background:#434652; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width:0; }
"""

try:
    from PySide6.QtCore import QObject, QSettings, QThread, QTimer, Qt, Signal
    from PySide6.QtGui import QAction, QColor, QIcon, QPixmap
    from PySide6.QtWidgets import (
        QApplication, QCheckBox, QComboBox, QFileDialog, QHBoxLayout,
        QHeaderView, QInputDialog, QLabel, QMainWindow, QMessageBox, QPushButton,
        QSystemTrayIcon, QTabWidget, QTableWidget, QTableWidgetItem,
        QMenu, QVBoxLayout, QWidget,
    )
    _QT_OK = True
except ImportError:  # pragma: no cover - 无 Qt 环境时的降级路径
    _QT_OK = False

INSTALL_HINT = ("缺少 PySide6，图形界面无法启动。\n"
                "安装：pip install PySide6-Essentials\n"
                "（命令行版不受影响：python -m client.ui.cli --help）")

if _QT_OK:

    class _Bridge(QObject):
        """把后台线程/编排器回调的事件转成 Qt 信号，统一回主线程处理。"""
        session_event = Signal(object)          # SessionEvent
        task_finished = Signal(str, object)     # (tag, (ok, payload))
        progress = Signal(str)                  # 批量测速进度 → 状态栏

    class _Worker(QThread):
        """跑一个阻塞 callable，完成后经 bridge 回主线程。"""

        def __init__(self, bridge: _Bridge, tag: str, fn):
            super().__init__()
            self._bridge = bridge
            self._tag = tag
            self._fn = fn

        def run(self):
            try:
                self._bridge.task_finished.emit(self._tag, (True, self._fn()))
            except Exception as exc:  # noqa: BLE001 - 界面层兜底，错误要可见
                self._bridge.task_finished.emit(self._tag, (False, exc))

    def _dot_icon(color: str) -> QIcon:
        pix = QPixmap(32, 32)
        pix.fill(Qt.transparent)
        from PySide6.QtGui import QPainter
        painter = QPainter(pix)
        painter.setBrush(QColor(color))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(4, 4, 24, 24)
        painter.end()
        return QIcon(pix)

    class SourcePage(QWidget):
        """一个来源页：过滤条 + 导入按钮 + 节点表。"""

        COLUMNS = ("名称", "协议", "出口", "类型", "干净", "延迟", "稳定性", "最近", "场景")

        def __init__(self, window: "MainWindow", source: str):
            super().__init__()
            self.window = window
            self.source = source
            layout = QVBoxLayout(self)

            bar = QHBoxLayout()
            bar.addWidget(QLabel("场景过滤："))
            self.scene_combo = QComboBox()
            self.scene_combo.addItem("全部", None)
            for sc in SCENES:
                self.scene_combo.addItem(SCENE_LABELS[sc], sc)
            self.scene_combo.currentIndexChanged.connect(window.refresh_pages)
            bar.addWidget(self.scene_combo)
            self.show_quarantined_chk = QCheckBox("显示隔离节点")
            self.show_quarantined_chk.stateChanged.connect(window.refresh_pages)
            bar.addWidget(self.show_quarantined_chk)
            self.show_banned_chk = QCheckBox("显示已拉黑")
            self.show_banned_chk.stateChanged.connect(window.refresh_pages)
            bar.addWidget(self.show_banned_chk)
            bar.addStretch(1)
            self.count_label = QLabel("")
            bar.addWidget(self.count_label)
            for text, slot in self._import_actions():
                btn = QPushButton(text)
                btn.clicked.connect(slot)
                bar.addWidget(btn)
            layout.addLayout(bar)

            self.table = QTableWidget(0, len(self.COLUMNS))
            self.table.setHorizontalHeaderLabels(self.COLUMNS)
            self.table.setSelectionBehavior(QTableWidget.SelectRows)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.Stretch)  # 名称列吃满余宽
            for col in range(1, len(self.COLUMNS)):
                header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
            self.table.itemSelectionChanged.connect(window._update_action_states)
            self.table.itemDoubleClicked.connect(lambda _it: window.connect_selected())
            layout.addWidget(self.table)

        def _import_actions(self):
            """每个来源页只给属于自己的导入入口（来源即归属）。"""
            w = self.window
            if self.source == "openvpn":
                return [("导入 .ovpn", w.import_ovpn_files)]
            if self.source == "vpngate_feed":
                return [("导入 feed 文件", w.import_feed_file)]
            if self.source == "pipeline":
                return [("导入 data.json", w.import_pipeline_data),
                        ("导入 nodes 文本", w.import_pipeline_nodes)]
            return [("粘贴链接", w.import_links_text),
                    ("导入订阅文件", w.import_subscription_file),
                    ("订阅地址导入", w.import_subscription_url)]

        def selected_node_id(self):
            row = self.table.currentRow()
            if row < 0:
                return None
            item = self.table.item(row, 0)
            return item.data(Qt.UserRole) if item else None

        def select_node(self, node_id):
            """按节点 id 选中对应行（启动恢复上次节点用）。找到返回 True。"""
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item and item.data(Qt.UserRole) == node_id:
                    self.table.setCurrentCell(row, 0)
                    return True
            return False

        def visible_node_ids(self):
            return [self.table.item(r, 0).data(Qt.UserRole)
                    for r in range(self.table.rowCount())]

        def populate(self, nodes, active_node_id):
            keep_id = self.selected_node_id()
            self.table.setRowCount(0)
            for n in nodes:
                row = self.table.rowCount()
                self.table.insertRow(row)
                ep = n.exit_profile
                marks = ("📌" if n.pinned else "") + ("🚫" if n.banned else "") + \
                        ("☠" if n.extra.get("quarantined") == "1" else "")
                active = "● " if n.id == active_node_id else ""
                name_item = QTableWidgetItem(f"{active}{marks}{n.name}")
                name_item.setData(Qt.UserRole, n.id)
                exit_s = f"{ep.country_code or ep.country or ''} {ep.ip}".strip() if ep.ip else "未验"
                type_s = NETWORK_LABELS.get(ep.network_type, ep.network_type)
                if ep.is_clean is True:
                    clean_s = "干净"
                elif ep.is_clean is False:
                    clean_s = "脏" + (f"（{','.join(sorted(ep.dirty))}）" if ep.dirty else "")
                else:
                    clean_s = "未知"
                stats = history.stats_for(self.window.store, n.id)
                lat = stats.median_latency_ms
                lat_s = f"{lat:.0f}ms" if lat is not None else "—"
                if stats.total == 0:
                    stab_s = "无样本"
                else:
                    stab_s = f"{stats.success_rate:.0%}" + (" · 稳定" if stats.stable else "")
                last = history.last_result(self.window.store, n.id)
                if last is None:
                    last_s = "未测"
                else:
                    last_ok, last_ts = last
                    last_s = ("可连 " if last_ok else "不通 ") + \
                        time.strftime("%H:%M", time.localtime(last_ts))
                scenes_s = "/".join(SCENE_LABELS.get(s, s) for s in sorted(n.scenes)) or "—"
                values = (name_item, n.protocol, exit_s, type_s, clean_s,
                          lat_s, stab_s, last_s, scenes_s)
                for col, val in enumerate(values):
                    item = val if isinstance(val, QTableWidgetItem) else QTableWidgetItem(str(val))
                    self.table.setItem(row, col, item)
                if n.id == keep_id:
                    self.table.setCurrentCell(row, 0)
            self.count_label.setText(f"{len(nodes)} 个节点")

    class MainWindow(QMainWindow):
        def __init__(self, store: Store, orchestrator: Orchestrator = None,
                     engines: dict = None, probe_factory=None):
            super().__init__()
            self.store = store
            self.bridge = _Bridge()
            self.bridge.session_event.connect(self._on_session_event)
            self.bridge.task_finished.connect(self._on_task_finished)
            self.bridge.progress.connect(self._show_info)
            self._workers = []
            self._busy = False
            self._batch_active = False
            self._batch_stop = False
            self._auto_active = False    # 自动探测一轮在跑（不占 _busy）
            self._auto_cursor = 0        # 未测/失败节点的轮扫游标

            if orchestrator is not None:
                self.orchestrator = orchestrator
                self.engines = engines or {}
            else:
                factory = probe_factory or (lambda: make_real_probe())
                holder = {}

                def engine_factory():
                    from ..engines.openvpn_engine import OpenVPNEngine
                    from ..engines.singbox_engine import SINGBOX_PROTOCOLS, SingBoxEngine
                    out_dir = os.path.join(
                        os.path.dirname(os.path.abspath(store.path or "client.db")),
                        "ovpn_out")
                    singbox = SingBoxEngine()
                    built = {"openvpn": OpenVPNEngine(out_dir=out_dir)}
                    for proto in SINGBOX_PROTOCOLS:
                        built[proto] = singbox
                    holder["engines"] = built
                    return built

                self.orchestrator = _build_orchestrator(store, factory, engine_factory)
                self.engines = holder.get("engines", {})
            # 编排器事件 → bridge（可能来自工作线程，必须走信号回主线程）
            self.orchestrator.on_event = lambda ev: self.bridge.session_event.emit(ev)

            self.setWindowTitle("gate-client · 自研代理客户端")
            self.resize(1080, 640)
            central = QWidget()
            root = QVBoxLayout(central)
            self.setCentralWidget(central)

            # 红色告警条（假连/掉线/错误时才出现）
            self.alert_bar = QLabel("")
            self.alert_bar.setStyleSheet(
                "background:#b00020;color:white;padding:8px;font-weight:bold;")
            self.alert_bar.setVisible(False)
            root.addWidget(self.alert_bar)

            self.status_label = QLabel("未连接")
            self.status_label.setStyleSheet("padding:6px;font-size:14px;")
            root.addWidget(self.status_label)

            self.tabs = QTabWidget()
            self.pages = {}
            for src in SOURCES:
                page = SourcePage(self, src)
                self.pages[src] = page
                self.tabs.addTab(page, SOURCE_LABELS.get(src, src))
            root.addWidget(self.tabs)

            actions = QHBoxLayout()
            self.connect_btn = QPushButton("连 接")
            self.connect_btn.setObjectName("primaryBtn")  # 深色主题的主按钮样式
            self.connect_btn.clicked.connect(self.connect_selected)
            actions.addWidget(self.connect_btn)
            self.disconnect_btn = QPushButton("断开")
            self.disconnect_btn.clicked.connect(self.disconnect)
            actions.addWidget(self.disconnect_btn)
            self.verify_btn = QPushButton("测速验出口")
            self.verify_btn.clicked.connect(self.verify_selected)
            actions.addWidget(self.verify_btn)
            self.pin_btn = QPushButton("置顶/取消")
            self.pin_btn.clicked.connect(self.toggle_pin)
            actions.addWidget(self.pin_btn)
            self.ban_btn = QPushButton("拉黑/取消")
            self.ban_btn.clicked.connect(self.toggle_ban)
            actions.addWidget(self.ban_btn)
            actions.addStretch(1)
            # 自动探测开关 + 间隔（2026-11-21 需求：后台定时测延迟，
            # 能用的节点一直排在前面）。设置经 QSettings 持久化。
            self.auto_chk = QCheckBox("自动探测")
            self.auto_chk.setChecked(True)
            self.auto_chk.stateChanged.connect(self._on_auto_setting_changed)
            actions.addWidget(self.auto_chk)
            self.auto_interval_combo = QComboBox()
            for minutes in (5, 10, 20, 30):
                self.auto_interval_combo.addItem(f"{minutes} 分钟", minutes)
            self.auto_interval_combo.setCurrentIndex(1)
            self.auto_interval_combo.currentIndexChanged.connect(
                self._on_auto_setting_changed)
            actions.addWidget(self.auto_interval_combo)
            self.batch_btn = QPushButton("测本页")
            self.batch_btn.clicked.connect(lambda: self.toggle_batch_test(all_nodes=False))
            actions.addWidget(self.batch_btn)
            self.batch_all_btn = QPushButton("一键全测")
            self.batch_all_btn.clicked.connect(lambda: self.toggle_batch_test(all_nodes=True))
            actions.addWidget(self.batch_all_btn)
            refresh_btn = QPushButton("刷新")
            refresh_btn.clicked.connect(self.refresh_pages)
            actions.addWidget(refresh_btn)
            root.addLayout(actions)

            self._setup_tray()
            self.poll_timer = QTimer(self)
            self.poll_timer.setInterval(5000)
            self.poll_timer.timeout.connect(self.poll_once)
            self.poll_timer.start()
            self.auto_timer = QTimer(self)
            self.auto_timer.timeout.connect(self._maybe_auto_probe)
            self._load_auto_settings()  # 恢复上次的开关/间隔，顺带起定时器

            self.refresh_pages()
            self._restore_last_node()
            self._update_action_states()

        # ---------------- 上次节点恢复 ----------------

        def _restore_last_node(self):
            """开窗时把上次连接的节点重新选中并切到它那一页——只选中，
            不自动连接（连不连永远由用户点）。节点已删则静默跳过。"""
            try:
                node_id = QSettings("gate-client", "gui").value(
                    "last_node_id", "", type=str)
            except Exception:
                return
            if not node_id:
                return
            node = self.store.get_node(node_id)
            if node is None:
                return
            page = self.pages.get(node.source)
            if page is not None and page.select_node(node_id):
                self.tabs.setCurrentWidget(page)

        # ---------------- 自动探测设置 ----------------
        _AUTO_STARTUP_DELAY_MS = 15000   # 开窗 15 秒先来一轮，不等首个 interval

        def _auto_interval_ms(self) -> int:
            minutes = self.auto_interval_combo.currentData()
            return int(minutes) * 60_000 if minutes else 600_000

        def _on_auto_setting_changed(self, *_):
            """开关/间隔变了：重设定时器并存档（QSettings）。"""
            if not hasattr(self, "auto_timer"):
                return
            self.auto_timer.setInterval(self._auto_interval_ms())
            if self.auto_chk.isChecked():
                self.auto_timer.start()
            else:
                self.auto_timer.stop()
            cfg = QSettings("gate-client", "gui")
            cfg.setValue("auto_probe/enabled", self.auto_chk.isChecked())
            cfg.setValue("auto_probe/interval_min",
                         self.auto_interval_combo.currentData())

        def _load_auto_settings(self):
            cfg = QSettings("gate-client", "gui")
            minutes = cfg.value("auto_probe/interval_min", 10, type=int)
            idx = self.auto_interval_combo.findData(int(minutes))
            if idx >= 0:
                self.auto_interval_combo.setCurrentIndex(idx)
            self.auto_chk.setChecked(
                cfg.value("auto_probe/enabled", True, type=bool))
            # 两项都与默认值相同时不触发信号，这里手动同步一次定时器
            self._on_auto_setting_changed()

        def schedule_startup_probe(self):
            """开窗后先自动探一轮（由 main() 调用，测试不调它）。"""
            QTimer.singleShot(self._AUTO_STARTUP_DELAY_MS, self._maybe_auto_probe)

        # ---------------- 托盘 ----------------
        def _setup_tray(self):
            self.tray = None
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            self.tray = QSystemTrayIcon(self)
            menu = QMenu()
            show_act = QAction("显示窗口", self)
            show_act.triggered.connect(self._show_window)
            menu.addAction(show_act)
            disc_act = QAction("断开连接", self)
            disc_act.triggered.connect(self.disconnect)
            menu.addAction(disc_act)
            quit_act = QAction("退出", self)
            quit_act.triggered.connect(self._quit)
            menu.addAction(quit_act)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(
                lambda reason: self._show_window()
                if reason == QSystemTrayIcon.DoubleClick else None)
            self._update_tray("idle", "未连接")
            self.tray.show()

        def _update_tray(self, state: str, text: str):
            if self.tray is None:
                return
            color = "#2e7d32" if state == "connected" else "#9e9e9e"
            self.tray.setIcon(_dot_icon(color))
            self.tray.setToolTip(f"gate-client：{text}")

        def _show_window(self):
            self.show()
            self.raise_()
            self.activateWindow()

        def _quit(self):
            try:
                if self.orchestrator.state != Orchestrator.IDLE:
                    self.orchestrator.disconnect()
            finally:
                QApplication.quit()

        def closeEvent(self, event):
            # 关窗 = 最小化到托盘（会话常驻）；真正退出走托盘菜单。
            if self.tray is not None:
                event.ignore()
                self.hide()
                self.tray.showMessage(
                    "gate-client", "已最小化到托盘，连接保持不变。",
                    QSystemTrayIcon.Information, 3000)
            else:
                super().closeEvent(event)

        # ---------------- 数据刷新 ----------------
        def refresh_pages(self):
            for src, page in self.pages.items():
                nodes = self.store.list_nodes(
                    source=src, include_banned=page.show_banned_chk.isChecked())
                scene = page.scene_combo.currentData()
                if scene:
                    nodes = [n for n in nodes if scene in n.scenes]
                if not page.show_quarantined_chk.isChecked():
                    nodes = [n for n in nodes if n.extra.get("quarantined") != "1"]
                nodes = history.sorted_nodes(self.store, nodes)
                page.populate(nodes, self.orchestrator.active_node_id)
            self._update_action_states()

        def current_page(self) -> SourcePage:
            return self.tabs.currentWidget()

        def selected_node(self):
            page = self.current_page()
            node_id = page.selected_node_id() if page else None
            return self.store.get_node(node_id) if node_id else None

        # ---------------- 会话动作 ----------------
        def _run_task(self, tag: str, fn):
            worker = _Worker(self.bridge, tag, fn)
            self._workers.append(worker)
            worker.finished.connect(lambda: self._workers.remove(worker))
            self._busy = True
            self._update_action_states()
            worker.start()

        def connect_selected(self):
            if self._busy:
                return
            node = self.selected_node()
            if node is None:
                self._show_info("先在当前页选中一个节点，再点连接。")
                return
            self._clear_alert()
            if node.extra.get("quarantined") == "1":
                # 与 CLI 同口径：警告但不阻断（用户点名即视为知情）
                self._show_alert(
                    f"⚠ 该节点已被标记为污染节点"
                    f"（{node.extra.get('quarantine_reason', '')}），仍按你的点名连接。")
            orch = self.orchestrator
            self._run_task("connect", lambda: orch.connect(node.id))

        def disconnect(self):
            if self._busy:
                return
            orch = self.orchestrator
            self._run_task("disconnect", lambda: orch.disconnect())

        def verify_selected(self):
            if self._busy:
                return
            node = self.selected_node()
            if node is None:
                self._show_info("先选中一个节点再测速。")
                return
            self._run_task("verify", lambda: self._test_node(node.id))

        # 临时测速引擎用独立端口，避开活动会话引擎的 7897/7898
        _TEMP_SOCKS_PORT = 10818
        _TEMP_HTTP_PORT = 10819
        # 自动探测再单独一组端口：它不锁 _busy，可能与手动测速并存，
        # 端口必须各用各的（10818/10819 手动，10828/10829 自动）。
        _AUTO_SOCKS_PORT = 10828
        _AUTO_HTTP_PORT = 10829
        _AUTO_SLICE = 40  # 每轮轮扫未测/失败节点的上限，防 300+ 死节点拖垮一轮

        def _make_temp_engine(self):
            return SingBoxEngine(socks_port=self._TEMP_SOCKS_PORT,
                                 http_port=self._TEMP_HTTP_PORT)

        def _make_auto_engine(self):
            return SingBoxEngine(socks_port=self._AUTO_SOCKS_PORT,
                                 http_port=self._AUTO_HTTP_PORT)

        def _test_node(self, node_id: str, fast: bool = False, make_engine=None):
            """测一个节点：能不能连、延迟多少、出口是哪。口径（2026-11-20
            真机教训修正）：旧实现没启动引擎就拿引擎代理地址去测，未连接
            时端口根本没人听，测谁都是「全灭」——那测的是空气，不是节点。
            - 正在连着的节点：经活动引擎的代理测；
            - sing-box 系节点：临时拉一个独立端口的引擎，测完即停，
              不碰当前会话；
            - OpenVPN 未连接：隧道模式没法离线单测，如实 skip、不记假样本。
            结果落 verify 样本（带延迟），界面延迟/稳定性/最近列随之有数。"""
            node = self.store.get_node(node_id)
            if node is None:
                return ("verify_failed", "节点不存在")
            stats = history.stats_for(self.store, node_id)
            orch = self.orchestrator
            if orch.state != Orchestrator.IDLE and orch.active_node_id == node_id:
                engine = self.engines.get(node.protocol)
                proxy = engine.http_proxy_url if isinstance(engine, SingBoxEngine) else None
                return self._probe_and_record(node, proxy, stats, fast)
            if node.protocol in SINGBOX_PROTOCOLS:
                engine = (make_engine or self._make_temp_engine)()
                try:
                    handle = engine.start(node)
                except EngineError as exc:
                    history.record(self.store, node_id, "verify", False,
                                   detail=f"引擎启动失败: {exc}")
                    return ("verify_failed", f"引擎启动失败: {exc}")
                try:
                    return self._probe_and_record(
                        node, engine.http_proxy_url, stats, fast)
                finally:
                    engine.stop(handle)
            if node.protocol == "openvpn":
                return ("skip", "OpenVPN 节点是隧道模式，未连接时测不了——"
                                "先连接它，连接流程自带验出口和延迟记录。")
            return self._probe_and_record(node, None, stats, fast)

        def _probe_and_record(self, node, proxy, stats, fast):
            probe = make_real_probe(proxy=proxy, test_muse=not fast)
            outcome = verify_exit(node, probe, stable=stats.stable)
            if not outcome.ok:
                history.record(self.store, node.id, "verify", False,
                               detail=outcome.reason)
                return ("verify_failed", outcome.reason)
            apply_outcome(node, outcome)
            self.store.upsert_node(node)
            history.record(self.store, node.id, "verify", True,
                           latency_ms=outcome.latency_ms,
                           exit_ip=node.exit_profile.ip, detail="验出口通过")
            ep = node.exit_profile
            lat = (f"，延迟 {outcome.latency_ms:.0f}ms"
                   if outcome.latency_ms is not None else "")
            return ("verify_ok",
                    f"出口 {ep.ip} / {ep.country}({ep.country_code}) / "
                    f"{NETWORK_LABELS.get(ep.network_type, ep.network_type)}{lat}")

        # ---------------- 批量测速（本页 / 全部） ----------------
        def toggle_batch_test(self, all_nodes: bool = False):
            if self._batch_active:
                self._batch_stop = True
                self._show_info("正在停止批量测速…")
                return
            if self._busy:
                return
            if all_nodes:
                ids = [n.id for n in self.store.list_nodes(include_banned=False)]
                label = "全部节点"
            else:
                page = self.current_page()
                ids = page.visible_node_ids() if page else []
                label = "本页"
            if not ids:
                self._show_info("没有可测的节点。")
                return
            self._batch_stop = False
            self._batch_active = True
            self._run_task("batch", lambda: self._run_batch(ids, label))

        def _run_batch(self, ids, label="本页"):
            ok_n = fail_n = skip_n = done = 0
            total = len(ids)
            for nid in ids:
                if self._batch_stop:
                    break
                node = self.store.get_node(nid)
                name = node.name if node else nid
                self.bridge.progress.emit(
                    f"测速中 {done + 1}/{total}：{name}")
                kind, _text = self._test_node(nid, fast=True)
                done += 1
                if kind == "verify_ok":
                    ok_n += 1
                elif kind == "skip":
                    skip_n += 1
                else:
                    fail_n += 1
                self.bridge.progress.emit(
                    f"测速中 {done}/{total}：可连 {ok_n} · 不通 {fail_n}"
                    f" · 跳过 {skip_n}")
            return ("batch_done", ok_n, fail_n, skip_n, done, total, label)

        # ---------------- 自动探测（定时后台测延迟，可用节点自动浮顶） ----------------
        def _maybe_auto_probe(self):
            """定时器入口：守卫全过才开一轮。自动探测刻意不置 _busy——
            它不该妨碍用户手动测速/连接；上轮没跑完、批量测速中、
            连接中、开关关闭，都直接跳过本轮（下个 tick 再说）。"""
            if not self.auto_chk.isChecked():
                return
            if self._auto_active or self._busy or self._batch_active:
                return
            if self.orchestrator.state != Orchestrator.IDLE:
                return  # 连接期间不跑，免得探测流量干扰会话延迟测量
            ids = self._auto_probe_ids()
            if not ids:
                return
            self._auto_active = True
            self._launch_auto(lambda: self._run_auto_probe(ids))

        def _launch_auto(self, fn):
            """开一轮自动探测的后台线程（测试里可换成同步替身）。"""
            worker = _Worker(self.bridge, "auto_probe", fn)
            self._workers.append(worker)
            worker.finished.connect(lambda: self._workers.remove(worker))
            worker.start()

        def _auto_probe_ids(self):
            """本轮要测的节点 id：最近成功的一组全测（保持延迟新鲜、
            及时发现刚死的）；其余（没测过/上次失败）按 _auto_cursor
            旋转切片，多轮覆盖全库。OpenVPN 未连接时测不了（隧道模式），
            已拉黑的不再打扰。"""
            all_ids = [n.id for n in self.store.list_nodes(include_banned=False)
                       if n.protocol != "openvpn"]
            fresh, rest = [], []
            for nid in all_ids:
                last = history.last_result(self.store, nid)
                (fresh if last and last[0] else rest).append(nid)
            n = len(rest)
            take = rest[self._auto_cursor:self._auto_cursor + self._AUTO_SLICE]
            if len(take) < self._AUTO_SLICE and self._auto_cursor > 0:
                # 到尾回卷：从头补足本轮。cursor==0 时整库不足一轮、已全选，
                # 再回卷只会重复（2026-11-21 测试抓过：3 个节点变 6 个任务）
                take += rest[:min(self._AUTO_SLICE - len(take), self._auto_cursor)]
            self._auto_cursor = ((self._auto_cursor + self._AUTO_SLICE) % max(n, 1))
            return fresh + take

        def _run_auto_probe(self, ids):
            ok_n = fail_n = done = 0
            total = len(ids)
            for nid in ids:
                node = self.store.get_node(nid)
                name = node.name if node else nid
                self.bridge.progress.emit(f"自动探测 {done + 1}/{total}：{name}")
                kind, _text = self._test_node(nid, fast=True,
                                              make_engine=self._make_auto_engine)
                done += 1
                if kind == "verify_ok":
                    ok_n += 1
                else:
                    fail_n += 1  # skip 不会出现：入口已排除 openvpn
                self.bridge.progress.emit(
                    f"自动探测 {done}/{total}：可连 {ok_n} · 不通 {fail_n}")
            return ("auto_done", ok_n, fail_n, done, total)

        def toggle_pin(self):
            node = self.selected_node()
            if node is not None:
                self.store.set_pinned(node.id, not node.pinned)
                self.refresh_pages()

        def toggle_ban(self):
            node = self.selected_node()
            if node is not None:
                self.store.set_banned(node.id, not node.banned)
                self.refresh_pages()

        def poll_once(self):
            """巡检：掉线由 orchestrator.poll 上报 dropped 事件，绝不自动切换。"""
            if not self._busy:
                self.orchestrator.poll()

        # ---------------- 事件与结果回主线程 ----------------
        def _on_session_event(self, ev):
            if ev.kind == "connected":
                # 记住上次连接的节点，下次开窗自动选中（只选中不自动连）
                QSettings("gate-client", "gui").setValue(
                    "last_node_id", ev.node_id)
                node = self.store.get_node(ev.node_id)
                name = node.name if node else ev.node_id
                self.status_label.setText(f"已连接：{name}（{ev.detail}）")
                self._update_tray("connected", f"已连接 {name}")
                self._clear_alert()
            elif ev.kind == "verify_failed":
                self._show_alert(f"假连已断开：{ev.detail}")
                self.status_label.setText("未连接")
                self._update_tray("idle", "未连接")
            elif ev.kind == "dropped":
                self._show_alert(f"节点掉线：{ev.detail}")
                self.status_label.setText("未连接")
                self._update_tray("idle", "未连接")
                if self.tray is not None:
                    self.tray.showMessage("gate-client 节点掉线", ev.detail,
                                          QSystemTrayIcon.Warning, 5000)
            elif ev.kind == "error":
                self._show_alert(f"连接失败：{ev.detail}")
                self.status_label.setText("未连接")
                self._update_tray("idle", "未连接")
            elif ev.kind == "disconnected":
                self.status_label.setText("未连接")
                self._update_tray("idle", "未连接")
            self.refresh_pages()

        def _on_task_finished(self, tag: str, result):
            if tag == "auto_probe":
                # 自动探测不占 _busy：只复位自己的旗标，别误清用户任务的锁
                self._auto_active = False
            else:
                self._busy = False
            if tag == "batch":
                self._batch_active = False
            ok, payload = result
            if not ok:
                if isinstance(payload, OrchestratorError):
                    self._show_alert(str(payload))
                else:
                    self._show_alert(f"操作失败：{payload}")
            elif tag == "auto_probe" and isinstance(payload, tuple):
                _kind, ok_n, fail_n, done, total = payload
                self._show_info(
                    f"自动探测完成：可连 {ok_n} · 不通 {fail_n}"
                    f"（本轮测 {done}/{total}，可用节点已排在前面）")
            elif tag == "verify" and isinstance(payload, tuple):
                kind, text = payload
                if kind == "verify_ok":
                    self._show_info(f"测速通过：{text}")
                elif kind == "skip":
                    self._show_info(text)
                else:
                    self._show_alert(f"测速失败：{text}")
            elif tag == "batch" and isinstance(payload, tuple):
                _kind, ok_n, fail_n, skip_n, done, total, label = payload
                stopped = "（已手动停止）" if done < total else ""
                self._show_info(
                    f"批量测速完成（{label}）{stopped}：可连 {ok_n} · 不通 {fail_n}"
                    f" · 跳过 {skip_n}（共测 {done}/{total}）")
            elif tag == "import_url" and ok:
                self._intake_done(
                    payload.nodes,
                    f"订阅导入（格式 {payload.source_format}，跳过坏行 {payload.skipped}）")
            self.refresh_pages()
            self._update_action_states()

        # ---------------- 小件 ----------------
        def _show_alert(self, text: str):
            self.alert_bar.setText(text)
            self.alert_bar.setVisible(True)

        def _clear_alert(self):
            self.alert_bar.setVisible(False)
            self.alert_bar.setText("")

        def _show_info(self, text: str):
            self.status_label.setText(text)

        def _update_action_states(self):
            connected = self.orchestrator.state != Orchestrator.IDLE
            has_sel = self.selected_node() is not None
            self.connect_btn.setEnabled(has_sel and not connected and not self._busy)
            self.disconnect_btn.setEnabled(connected and not self._busy)
            self.verify_btn.setEnabled(has_sel and not self._busy)
            self.pin_btn.setEnabled(has_sel)
            self.ban_btn.setEnabled(has_sel)
            if self._batch_active:
                self.batch_btn.setText("停止测速")
                self.batch_btn.setEnabled(True)
                self.batch_all_btn.setText("停止测速")
                self.batch_all_btn.setEnabled(True)
            else:
                self.batch_btn.setText("测本页")
                self.batch_btn.setEnabled(not self._busy)
                self.batch_all_btn.setText("一键全测")
                self.batch_all_btn.setEnabled(not self._busy)

        # ---------------- 导入（来源即归属，与 CLI 同函数） ----------------
        def _intake_done(self, nodes, what: str):
            if not nodes:
                self._show_alert(f"{what}：没有解析出任何节点。")
                return
            self.store.upsert_nodes(nodes)
            self._show_info(f"{what}：已收纳 {len(nodes)} 个节点"
                            f"（库内共 {self.store.count_nodes()} 个）")
            self.refresh_pages()

        def _pick_files(self, title: str, pattern: str):
            paths, _ = QFileDialog.getOpenFileNames(self, title, "", pattern)
            return paths

        def import_ovpn_files(self):
            for path in self._pick_files("选择 .ovpn 文件", "OpenVPN 配置 (*.ovpn)"):
                try:
                    self._intake_done([manual_src.load_ovpn_file(path)], f"导入 {os.path.basename(path)}")
                except (ValueError, RuntimeError, OSError) as exc:
                    self._show_alert(f"导入失败：{exc}")

        def import_feed_file(self):
            for path in self._pick_files("选择 VPN Gate feed 文件", "feed 文件 (*.csv *.json *.txt)"):
                try:
                    self._intake_done(feed_src.load_feed_file(path), "导入 feed")
                except (ValueError, RuntimeError, OSError) as exc:
                    self._show_alert(f"导入失败：{exc}")

        def import_pipeline_data(self):
            for path in self._pick_files("选择流水线 data.json", "JSON (*.json)"):
                try:
                    self._intake_done(pipeline_src.load_data_json_file(path), "导入流水线产物")
                except (ValueError, RuntimeError, OSError) as exc:
                    self._show_alert(f"导入失败：{exc}")

        def import_pipeline_nodes(self):
            for path in self._pick_files("选择流水线 nodes 文本", "文本 (*.txt)"):
                try:
                    self._intake_done(pipeline_src.load_nodes_text_file(path), "导入流水线产物")
                except (ValueError, RuntimeError, OSError) as exc:
                    self._show_alert(f"导入失败：{exc}")

        def import_links_text(self):
            text, ok = QInputDialog.getMultiLineText(
                self, "粘贴分享链接", "每行一个链接（vless:// / ss:// / trojan:// …）：")
            if not ok or not text.strip():
                return
            try:
                self._intake_done(manual_src.parse_links_text(text), "粘贴导入")
            except (ValueError, RuntimeError) as exc:
                self._show_alert(f"导入失败：{exc}")

        def import_subscription_file(self):
            for path in self._pick_files("选择订阅文件", "订阅文件 (*.txt *.yaml *.yml *.json)"):
                try:
                    res = subscription_src.load_subscription_file(path)
                    self._intake_done(res.nodes,
                                      f"订阅导入（格式 {res.source_format}，跳过坏行 {res.skipped}）")
                except (ValueError, RuntimeError, OSError) as exc:
                    self._show_alert(f"导入失败：{exc}")

        def import_subscription_url(self):
            url, ok = QInputDialog.getText(self, "订阅地址导入", "订阅地址（http/https）：")
            if not ok or not url.strip():
                return
            # 地址只在内存里用，不回显、不落日志（同 CLI 红线）
            self._run_task("import_url",
                           lambda: subscription_src.import_subscription_url(url.strip()))

    def main(argv=None) -> int:
        parser = argparse.ArgumentParser(prog="gate-client-gui",
                                         description="自研代理客户端（M3 图形界面）")
        parser.add_argument("--db", default=DEFAULT_DB,
                            help=f"本地数据库路径（默认 {DEFAULT_DB}）")
        args = parser.parse_args(argv)
        app = QApplication(sys.argv)
        app.setApplicationName("gate-client")   # WM_CLASS 对齐 .desktop 的 StartupWMClass
        app.setOrganizationName("gate-client")
        app.setStyle("Fusion")                  # 统一控件底子，QSS 不被原生主题带偏
        app.setStyleSheet(DARK_QSS)
        app.setQuitOnLastWindowClosed(False)
        store = Store(args.db)
        window = MainWindow(store)
        window.show()
        window.schedule_startup_probe()
        return app.exec()

else:

    def main(argv=None) -> int:  # pragma: no cover
        print(INSTALL_HINT, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
