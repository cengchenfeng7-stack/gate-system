#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图形界面（ui/app.py）—— M3：PySide6 窗口 + 托盘 + 常驻会话
=============================================================
与 CLI 共用同一套核心接线（直接复用 cli._build_orchestrator），保证
「界面操作与 CLI 行为一致」（设计第四节 M3 验收）：

- 三个来源页签（来源是第一组织维度，用户 2026-11-19 定；task8/task23
  落页：OpenVPN 页与 VPNGate feed 页合并为「VPNGate 来源页」，work8/
  DESIGN §1），场景只是页内徽标/过滤；节点排序走 history.sorted_nodes
  （养鱼池口径）。
- 连接/断开/验出口全部经 Orchestrator——手动点选、连后强制验出口、
  掉线只告警停在原地，绝不自动切换（铁律在 orchestrator 里写死）。
- 名单复查（task28 / work10 §6.3/§6.4）：白名单视图与隔离区节点按
  list_recheck_days 到期复查（机制在 core/list_recheck；定时形态默认关）。
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
from collections import deque
from types import SimpleNamespace

from ..core import failover
from ..core import history
from ..core import list_recheck
from ..core import ruleset_cache
from ..core import settings_store as settings_store_io, site_blocklist
from ..core.health import (HEALTH_DEAD, HEALTH_SUSPECT, check_ports_free,
                           check_tun_takeover, connection_health_detail,
                           describe_occupied, proxy_conflict,
                           read_system_proxy)
from ..core.tun_config import MODES, mode_plan
from ..core.model import SCENES, SOURCE_LABELS, SOURCES
from ..core.usage_model import UsageSettings
from ..core.orchestrator import Orchestrator, OrchestratorError
from ..core.settings_proto import (Settings, SettingsCorruptError,
                                   SettingsError, SettingsStore,
                                   SettingsVersionError, bootstrap_settings,
                                   derived_data_dir, merge, resolve_db_path,
                                   settings_file_path)
from ..core.store import Store
from ..core.verify import (apply_outcome, make_real_probe, pollution_of,
                           verify_exit)
from ..engines.base import EngineError
from ..engines.singbox_engine import SINGBOX_PROTOCOLS, SingBoxEngine
from ..sources import feed as feed_src
from ..sources import format_import as format_src
from ..sources import manual as manual_src
from ..sources import pipeline as pipeline_src
from ..sources import source_registry as registry_src
from ..sources import subscription as subscription_src
from ..sources.landing import run_startup_migration
from .cli import _build_orchestrator

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
        QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog,
        QFileDialog, QHBoxLayout,
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

    def _qsettings_auto_probe_reader():
        """QSettings("gate-client", "gui") 旧两键读取（§4.3 迁移专用注入）。
        只读 auto_probe/enabled、auto_probe/interval_min——**last_node_id
        与其他键一概不碰**（界面状态不是设置）；读不到/类型不符 → None
        （迁移函数按默认处理）。迁移完成后应用代码不再读写这两键（停用，
        §8-6）。"""
        out = {"auto_probe_enabled": None, "auto_probe_interval_min": None}
        try:
            cfg = QSettings("gate-client", "gui")

            def _get(key, type_):
                try:
                    return cfg.value(key, None, type=type_)
                except Exception:
                    return None

            if cfg.contains("auto_probe/enabled"):
                out["auto_probe_enabled"] = _get(
                    "auto_probe/enabled", bool)
            if cfg.contains("auto_probe/interval_min"):
                out["auto_probe_interval_min"] = _get(
                    "auto_probe/interval_min", int)
        except Exception:
            pass
        return out

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
            self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.Stretch)  # 名称列吃满余宽
            for col in range(1, len(self.COLUMNS)):
                header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
            self.table.itemSelectionChanged.connect(window._update_action_states)
            self.table.itemDoubleClicked.connect(lambda _it: window.connect_selected())
            layout.addWidget(self.table)

        def _import_actions(self):
            """每个来源页只给属于自己的导入入口（来源即归属）。合并页
            （vpngate）只剩「登记并更新 feed」一个登记入口——旧「登记
            feed 镜像」入口随 feed_mirror 类型废除（task8/task23 落页，
            work8/DESIGN §5.1、§6）。"""
            w = self.window
            if self.source == "vpngate":
                return [("导入 .ovpn", w.import_ovpn_files),
                        ("导入 feed 文件", w.import_feed_file),
                        ("登记并更新 feed", w.register_and_update_feed),
                        ("更新已登记来源", w.update_registered_sources)]
            if self.source == "pipeline":
                return [("导入 data.json", w.import_pipeline_data),
                        ("导入 nodes 文本", w.import_pipeline_nodes)]
            return [("粘贴链接", w.import_links_text),
                    ("导入订阅文件", w.import_subscription_file),
                    ("订阅地址导入", w.import_subscription_url),
                    ("更新已登记来源", w.update_registered_sources)]

        def selected_node_id(self):
            row = self.table.currentRow()
            if row < 0:
                return None
            item = self.table.item(row, 0)
            return item.data(Qt.UserRole) if item else None

        def selected_node_ids(self):
            """多选中的全部节点 id（Ctrl/Shift 多选）。"""
            ids = []
            for idx in self.table.selectionModel().selectedRows():
                item = self.table.item(idx.row(), 0)
                if item:
                    ids.append(item.data(Qt.UserRole))
            return ids

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
                lat_s = history.format_latency_ms(stats.median_latency_ms)
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
                     engines: dict = None, probe_factory=None,
                     settings: Settings = None, settings_store=None):
            super().__init__()
            self.store = store
            # 统一设置（task24 §8 接线）：settings=None → 默认值（=现行行为）；
            # settings_store=None → 无落盘点（离线测试替身形态，落盘跳过）。
            self.settings = (settings if settings is not None else Settings())
            self.settings_store = settings_store
            self._applying_settings = False   # 程序化同步控件期间挡重复落盘
            self.bridge = _Bridge()
            self.bridge.session_event.connect(self._on_session_event)
            self.bridge.task_finished.connect(self._on_task_finished)
            self.bridge.progress.connect(self._show_info)
            self._workers = []
            self._busy = False
            self._batch_active = False
            self._batch_stop = False
            self._batch_waste = []      # 上一轮批量测出的废节点（待一键删）
            self._auto_active = False    # 自动探测一轮在跑（不占 _busy）
            self._auto_cursor = 0        # 未测/失败节点的轮扫游标
            self._health_samples = deque(maxlen=10)  # 健康探测样本（当前连接）
            self._health_active = False    # 健康探测一轮在跑（不占 _busy）
            self._sysproxy_active = False  # 系统代理复查一轮在跑（不占 _busy）
            self._recheck_active = False   # 名单复查一轮在跑（不占 _busy，task28）
            self._collect_active = False   # 自动搜集一轮在跑（不占 _busy，task31）
            self._health_tick = 0          # poll_once 滴答计数（3 tick≈15s 探测）
            self._sysproxy_alert = False   # 告警条当前是否在亮系统代理冲突
            self._tun_alert = False        # 告警条当前是否在亮 TUN 接管未生效
            # U6 容灾信号出口（work21/WIRING.md 落点①的出口侧）：main() 注入
            # 前台窗的 _failover_feed；独立构造本窗（测试/冒烟）时保持 None。
            self._failover_feed = None

            if orchestrator is not None:
                self.orchestrator = orchestrator
                self.engines = engines or {}
            else:
                factory = probe_factory or (lambda: make_real_probe())
                holder = {}

                def engine_factory():
                    from ..engines.openvpn_engine import OpenVPNEngine
                    from ..engines.singbox_engine import SINGBOX_PROTOCOLS, SingBoxEngine
                    # task24 §8-1：out_dir 从派生基准目录（derived_data_dir：
                    # 与库同目录——库路径已按 §8-3 优先级解析，无覆盖时即
                    # settings.data_dir）的 ovpn_out 派生，同目录口径不变。
                    out_dir = os.path.join(
                        derived_data_dir(store.path, self.settings), "ovpn_out")
                    # U2（task17）：使用设置供给器（与 cli 接线 A 同款；
                    # store 已在场，覆盖目录同上推导）。
                    # task24 §4.1：settings.json 落点不跟随 data_dir 设置——
                    # 有 settings_store（main() 传入）时按它的落点现读，与
                    # 设置页/③区同一份文件；无（离线测试形态）时维持 task17
                    # 的「与库同目录」推导。
                    settings_file = (self.settings_store.path
                                     if self.settings_store is not None else None)

                    def _usage_provider():
                        data_dir = os.path.dirname(
                            os.path.abspath(store.path or "client.db"))
                        if settings_file is not None:
                            current = settings_store_io.load_settings(settings_file)
                        else:
                            current = settings_store_io.load_settings(
                                settings_store_io.settings_path(data_dir))
                        usage = UsageSettings(**current.usage_dict())
                        blocklist = site_blocklist.load_blocklist(
                            site_blocklist.blocklist_path(data_dir))
                        return usage, blocklist

                    def _ruleset_state_provider():
                        data_dir = os.path.dirname(
                            os.path.abspath(store.path or "client.db"))
                        return ruleset_cache.current_ruleset_state(data_dir)

                    singbox = SingBoxEngine(
                        usage_provider=_usage_provider,
                        ruleset_state_provider=_ruleset_state_provider,
                        socks_port=self.settings.socks_port,
                        http_port=self.settings.http_port)
                    built = {"openvpn": OpenVPNEngine(out_dir=out_dir)}
                    for proto in SINGBOX_PROTOCOLS:
                        built[proto] = singbox
                    holder["engines"] = built
                    return built

                self.orchestrator = _build_orchestrator(store, factory, engine_factory)
                self.engines = holder.get("engines", {})
            # 编排器事件 → bridge（可能来自工作线程，必须走信号回主线程）
            self.orchestrator.on_event = lambda ev: self.bridge.session_event.emit(ev)

            self.setWindowTitle("gate-client · 后台（来源管理）")  # U1：现窗降后台（task16）
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
            # 接管方式（task6/DESIGN §2.2）：只在未连接时可切（CONNECTED 时
            # 禁用，见 _update_action_states）；切换=改引擎实例属性，下次
            # 连接生效。TUN 需系统授权，提权启动器待真机实现。
            self.mode_combo = QComboBox()
            self.mode_combo.addItem("系统代理", "proxy")
            self.mode_combo.addItem("TUN 接管全机", "tun")
            self.mode_combo.setToolTip(
                "接管方式：只在未连接时可切换，切换后下次连接生效。"
                "TUN 接管全机流量，首次开启会弹系统授权框；提权启动器待"
                "真机实现，当前版本选 TUN 点连接会显式报错。")
            self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
            actions.addWidget(self.mode_combo)
            self.verify_btn = QPushButton("测速验出口")
            self.verify_btn.clicked.connect(self.verify_selected)
            actions.addWidget(self.verify_btn)
            self.pin_btn = QPushButton("置顶/取消")
            self.pin_btn.clicked.connect(self.toggle_pin)
            actions.addWidget(self.pin_btn)
            self.ban_btn = QPushButton("拉黑/取消")
            self.ban_btn.clicked.connect(self.toggle_ban)
            actions.addWidget(self.ban_btn)
            self.delete_btn = QPushButton("删除")
            self.delete_btn.clicked.connect(self.delete_selected)
            actions.addWidget(self.delete_btn)
            self.purge_btn = QPushButton("一键删废节点")
            self.purge_btn.clicked.connect(self.purge_waste_selected_page)
            actions.addWidget(self.purge_btn)
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
            # 名单复查（task28 / work10 §6.3/§6.4；task33 收尾）：白名单视图
            # 与隔离区节点每 list_recheck_days 天复查一次（默认 3，2–7）。
            # 手动按钮即跑一轮；定时形态走 auto_timer 子周期
            # （_maybe_recheck），开关默认关（铁律口径同自动搜集：自动类
            # 动作默认不开启）。勾选状态持久化进 settings.json
            # （list_recheck_auto_enabled，task33 销 work28 §4-3 尾巴）：
            # 初值读设置、用户拨动即经既有 settings 保存路径写回
            # （_persist_recheck_switch），下次开窗按设置恢复。
            self.recheck_btn = QPushButton("复查名单")
            self.recheck_btn.clicked.connect(self.recheck_now)
            actions.addWidget(self.recheck_btn)
            self.recheck_chk = QCheckBox("定时复查")
            # 初值来自设置（task33）；先设初值再接信号——启动恢复不落盘
            # （「启动恢复不算用户改动」，与 _load_auto_settings 同口径）。
            self.recheck_chk.setChecked(
                bool(self.settings.list_recheck_auto_enabled))
            self.recheck_chk.toggled.connect(
                self._on_recheck_setting_changed)
            self.recheck_chk.setToolTip(
                "按设置 list_recheck_days（默认 3 天，可调 2–7，设置页可改）"
                "的周期，挂在自动探测定时器子周期里检查到期对象并复查。"
                "默认关；勾选状态持久化进设置（list_recheck_auto_enabled），"
                "下次开窗沿用。")
            actions.addWidget(self.recheck_chk)
            refresh_btn = QPushButton("刷新")
            refresh_btn.clicked.connect(self.refresh_pages)
            actions.addWidget(refresh_btn)
            # 设置页入口（task24 §5/§6）：模态对话框；五项规则字段的唯一
            # 编辑入口在设置页（与③区共用同一份落盘值，不出现脏副本）。
            self.settings_btn = QPushButton("设置")
            self.settings_btn.clicked.connect(self.open_settings)
            actions.addWidget(self.settings_btn)
            root.addLayout(actions)

            # U1（task16）：托盘归主窗（UsageWindow），后台窗不建托盘；
            # self.tray 其余引用全有 is None 守卫，留 None 即自然 no-op。
            self.tray = None
            self.poll_timer = QTimer(self)
            self.poll_timer.setInterval(5000)
            self.poll_timer.timeout.connect(self.poll_once)
            self.poll_timer.start()
            self.auto_timer = QTimer(self)
            self.auto_timer.timeout.connect(self._maybe_auto_probe)
            # task28：定时名单复查挂同一 auto_timer 子周期（_maybe_recheck
            # 首行守卫开关关直返；不新增定时器。task33：开关状态持久化进
            # settings，初值恢复见创建点）。
            self.auto_timer.timeout.connect(self._maybe_recheck)
            # task31：自动搜集第二只定时器——同一位置、同一 _apply_* 刷新
            # 口径挂第二只 QTimer，不另起第二套调度实现（后台执行复用
            # task28 泛化的 _launch_auto(tag=…) 通道）。刻意不骑 auto_timer：
            # 它只在自动探测开关开时运行（_apply_auto_timer），骑上去会让
            # 「搜集开、探测关」的组合静默失效；collect_timer 的启停唯一
            # 归 auto_collect_enabled（默认关——铁律：不经主人点头，客户端
            # 不许自己联网拉新节点）。
            self.collect_timer = QTimer(self)
            self.collect_timer.timeout.connect(self._maybe_auto_collect)
            self._load_auto_settings()  # 恢复上次的开关/间隔，顺带起定时器
            self._apply_collect_timer()  # 自动搜集定时器按设置值起停（task31）

            self.refresh_pages()
            self._restore_last_node()
            self._startup_health_check()
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
            """开关/间隔变了：重设定时器并落盘（task24 §8-7 立即生效的接线点）。
            存档改走 settings.json（_persist_auto_probe）；QSettings 的
            auto_probe 两键已随迁移停用——不再读写（§4.3/§8-6）。"""
            self._apply_auto_timer()
            self._persist_auto_probe()

        def _apply_auto_timer(self):
            """开关/间隔 → 定时器（不落盘；启动恢复与程序化同步也走它）。"""
            if not hasattr(self, "auto_timer"):
                return
            self.auto_timer.setInterval(self._auto_interval_ms())
            if self.auto_chk.isChecked():
                self.auto_timer.start()
            else:
                self.auto_timer.stop()

        def _persist_auto_probe(self):
            """自动探测两项 → settings.json（唯一落盘点）。程序化同步控件
            期间跳过（对话框已保存，避免重复写）；无 settings_store（离线
            测试替身形态）跳过落盘。IO 失败显式告警，不静默吞。"""
            if self.settings_store is None or self._applying_settings:
                return
            minutes = self.auto_interval_combo.currentData()
            overrides = {"auto_probe_enabled": self.auto_chk.isChecked()}
            if minutes is not None:
                overrides["auto_probe_interval_min"] = int(minutes)
            self.settings = merge(self.settings, overrides)
            try:
                self.settings_store.save(self.settings)
            except OSError as exc:
                self._show_alert(f"设置保存失败：{exc}")

        def _load_auto_settings(self):
            """从统一设置恢复开关/间隔并起定时器（task24 §8-6：QSettings 的
            auto_probe 两键已停用——值经 §4.3 迁移进 settings.json，这里只读
            统一设置）。"""
            idx = self.auto_interval_combo.findData(
                int(self.settings.auto_probe_interval_min))
            if idx >= 0:
                self.auto_interval_combo.setCurrentIndex(idx)
            self.auto_chk.setChecked(bool(self.settings.auto_probe_enabled))
            # 两项都与设置值相同时不触发信号，这里手动同步一次定时器
            #（不落盘：启动恢复不算用户改动）
            self._apply_auto_timer()

        def schedule_startup_probe(self):
            """开窗后先自动探一轮（由 main() 调用，测试不调它）。"""
            QTimer.singleShot(self._AUTO_STARTUP_DELAY_MS, self._maybe_auto_probe)

        # ---------------- 自动搜集（task31：定时一轮，只加库不碰连接） ----------------
        def _apply_collect_timer(self):
            """开关/间隔 → collect_timer（不落盘；启动恢复与设置页保存同走
            它，与 _apply_auto_timer 同款刷新口径）。开关关 = 不启动（铁律）。
            刻意不骑 auto_timer（见 __init__ 注释）；间隔直接用
            collect_interval_min（分钟级独立周期，与 auto_probe 的
            interval → setInterval 同款映射，无需到点查到期）。"""
            if not hasattr(self, "collect_timer"):
                return
            self.collect_timer.setInterval(
                int(self.settings.collect_interval_min) * 60_000)
            if self.settings.auto_collect_enabled:
                self.collect_timer.start()
            else:
                self.collect_timer.stop()

        def _maybe_auto_collect(self):
            """定时器入口（自动搜集）：守卫全过才开一轮，与 _maybe_auto_probe
            同款纪律（忙时不跑、连接中不跑）。自动搜集只往库里加候选：
            不碰引擎连接、不切节点（铁律）；不与自动探测/名单复查互斥
            ——三者的临时引擎端口两两错开（搜集 10838/10839、探测与复查
            10828/10829）。搜集轮刻意不置 _busy（同自动探测：不挡用户
            手动操作）。"""
            if not self.settings.auto_collect_enabled:
                return
            if self._collect_active or self._busy or self._batch_active:
                return
            if self.orchestrator.state != Orchestrator.IDLE:
                return  # 会话期间不跑，免得搜集验真流量干扰延迟测量
            self._collect_active = True
            self._launch_auto(self._run_auto_collect, tag="auto_collect")

        def _run_auto_collect(self):
            """定时一轮 = CLI 本体 _sources_collect 原样复用（不抄第二份
            跑轮逻辑）：种子/账本/门槛数字全部由它现读落盘值；
            settings_file 用本窗 settings_store 的落点（与设置页同一份
            文件；离线测试替身形态无 settings_store → 库同目录推导兜底）。
            返回退出码（0 = 一轮跑完，含零产出轮与未启用）。"""
            from . import cli as cli_src
            settings_file = (self.settings_store.path
                             if self.settings_store is not None else None)
            return cli_src._sources_collect(self.store,
                                            settings_file=settings_file)

        # ---------------- 设置页（task24 §5/§6 落页） ----------------
        def open_settings(self):
            """打开设置页（模态）。保存成功后的落地见 _apply_saved_settings
            （§5.3 逐项生效时机）。无 settings_store（离线测试替身形态）
            时不打开——落盘是设置页的硬前提，不搞「改了不存」的假入口。"""
            if self.settings_store is None:
                self._show_info("设置存储不可用，设置页未打开。")
                return
            from .settings_dialog import SettingsDialog
            dlg = SettingsDialog(self.settings, self.settings_store, parent=self)
            if dlg.exec() == QDialog.Accepted and dlg.saved_settings is not None:
                self._apply_saved_settings(dlg.saved_settings)

        def _apply_saved_settings(self, s: Settings):
            """设置页保存后的窗侧落地（§5.3 逐项口径，不许含糊）：
            - 自动探测：**立即生效**——控件同步 → _apply_auto_timer；
            - 自动搜集（task31）：开关/间隔**立即生效**——
              _apply_collect_timer 按新值重建定时器（门槛数字不在窗侧
              持副本，定时跑轮每轮经 CLI 本体现读落盘值）；
            - 端口 / 规则五项：**下次连接生效**——空闲时即在保存点对齐
              引擎端口（_sync_engine_ports_with_settings；连接中不进来，
              断开后下次 connect 起新端口；连接构造点另有安全网）；规则
              五项的消费点在引擎 build_config 尾部合并（U2 已接线：供给
              器每次连接现读 settings.json）；
            - data_dir：**下次启动生效**（main() 启动构造点，不搬家）。"""
            self.settings = s
            self._applying_settings = True
            try:
                idx = self.auto_interval_combo.findData(
                    int(s.auto_probe_interval_min))
                if idx >= 0:
                    self.auto_interval_combo.setCurrentIndex(idx)
                self.auto_chk.setChecked(bool(s.auto_probe_enabled))
            finally:
                self._applying_settings = False
            self._apply_auto_timer()
            self._apply_collect_timer()
            self._sync_engine_ports_with_settings()
            self._show_info(
                "设置已保存：自动探测立即生效；自动搜集开关与间隔立即生效"
                "（门槛数字每轮现读）；端口 / 规则与网络下次连接生效；"
                "数据目录下次启动生效（不会搬移旧数据）。")

        def _sync_engine_ports_with_settings(self):
            """连接构造点对齐（§5.3「下次连接生效 = 重启引擎」/ §8-1）：空闲时
            若 sing-box 引擎端口与设置不一致（设置页改过端口），原地重建
            引擎束——本次 connect 即用新端口；连接中不会进来（连接按钮已
            禁用），断开后下次 connect 起新端口。OpenVPN 引擎不动：out_dir
            属启动构造点（§5.3 下次启动生效）。仅有 settings_store 的真实
            应用形态生效（外部注入引擎束的离线测试形态不介入）。"""
            if self.settings_store is None:
                return
            if self.orchestrator.state != Orchestrator.IDLE:
                return
            old = next((e for e in self.engines.values()
                        if isinstance(e, SingBoxEngine)), None)
            if old is None or (old.socks_port == self.settings.socks_port
                               and old.http_port == self.settings.http_port):
                return
            new = SingBoxEngine(socks_port=self.settings.socks_port,
                                http_port=self.settings.http_port)
            for proto in list(self.engines):
                if self.engines[proto] is old:
                    self.engines[proto] = new      # verifier 闭包共享同一 dict
            self.orchestrator.engines = dict(self.engines)  # orchestrator 副本同步

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
            # U1（task16）：后台窗关窗 = 真关（不再隐藏进托盘——会话常驻
            # 已由主窗托盘承担；self.tray 恒为 None，旧分支不再成立）。
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
            self._sysproxy_alert = False
            if node.extra.get("quarantined") == "1":
                # 与 CLI 同口径：警告但不阻断（用户点名即视为知情）
                self._show_alert(
                    f"⚠ 该节点已被标记为污染节点"
                    f"（{node.extra.get('quarantine_reason', '')}），仍按你的点名连接。")
            # 健康预检（task5）：①端口被占 → 取消本次连接；②系统代理冲突 →
            # 只告警、照常连接（用户点名优先）。False=取消本次连接。
            # 端口改动下次连接生效（task24 §5.3）：先在连接构造点对齐引擎
            # 端口，预检与接管方式都作用于对齐后的引擎。
            self._sync_engine_ports_with_settings()
            if not self._health_precheck(node):
                return
            # 接管方式（task6）：连接前把控件当前模式写进引擎实例属性
            # （IDLE 才可改，控件在 CONNECTED 时禁用）；OpenVPN 不走此引擎。
            engine = self.engines.get(node.protocol)
            if isinstance(engine, SingBoxEngine):
                engine.takeover_mode = self.mode_combo.currentData()
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
            # U6 §5.4：验真成功清「容灾换下」标记（无标记 no-op）。
            failover.clear_mark_on_link_success(self.store, node.id, True)
            ep = node.exit_profile
            lat = (f"，延迟 {history.format_latency_ms(outcome.latency_ms)}"
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
            self._batch_waste = []
            self._run_task("batch", lambda: self._run_batch(ids, label))

        def _run_batch(self, ids, label="本页"):
            ok_n = fail_n = skip_n = done = 0
            fail_ids, dirty_ids = [], []
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
                    node = self.store.get_node(nid)
                    if (node is not None and node.exit_profile is not None
                            and pollution_of(node.exit_profile)[0]):
                        dirty_ids.append(nid)
                elif kind == "skip":
                    skip_n += 1
                else:
                    fail_n += 1
                    fail_ids.append(nid)
                self.bridge.progress.emit(
                    f"测速中 {done}/{total}：可连 {ok_n} · 不通 {fail_n}"
                    f" · 跳过 {skip_n}")
            return ("batch_done", ok_n, fail_n, skip_n, done, total, label,
                    fail_ids, dirty_ids)

        # ---------------- 自动探测（定时后台测延迟，可用节点自动浮顶） ----------------
        def _maybe_auto_probe(self):
            """定时器入口：守卫全过才开一轮。自动探测刻意不置 _busy——
            它不该妨碍用户手动测速/连接；上轮没跑完、批量测速中、
            连接中、开关关闭，都直接跳过本轮（下个 tick 再说）。"""
            if not self.auto_chk.isChecked():
                return
            if self._auto_active or self._busy or self._batch_active:
                return
            if self._recheck_active:
                return  # 定时名单复查正占 10828/10829 端口对，两者互斥（task28）
            if self.orchestrator.state != Orchestrator.IDLE:
                return  # 连接期间不跑，免得探测流量干扰会话延迟测量
            ids = self._auto_probe_ids()
            if not ids:
                return
            self._auto_active = True
            self._launch_auto(lambda: self._run_auto_probe(ids))

        def _launch_auto(self, fn, tag="auto_probe"):
            """开一轮自动探测的后台线程（测试里可换成同步替身）。
            task28：定时名单复查复用本通道（tag="auto_recheck"），旗标
            各管各的（_auto_active / _recheck_active），互不误清。"""
            worker = _Worker(self.bridge, tag, fn)
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

        # ---------------- 名单复查（task28 / work10 §6.3/§6.4） ----------------
        # 口径（本窗只接线，机制在 core/list_recheck）：白名单 = 派生视图
        # （is_clean is True 且未被隔离）+ 隔离区，按 list_recheck_days
        # （默认 3 天，可调 2–7）到期复查；复查走 _test_node 既有管道，
        # 不新造探测。处置只提示不改人工标记：变脏移出视图不拉黑、链路死
        # 画像不动、黑名单恢复可连只提示（人工确认才解除）、隔离恢复自动
        # 摘牌（既有 _apply_quarantine 口径）。绝不自动拉黑、绝不自动放回。
        def _recheck_verify_outcome(self, node, make_engine=None):
            """run_recheck 注入的 verify_fn（合同见 list_recheck 模块头）：
            复用 _test_node 全套既有管道（临时独立端口引擎 + verify_exit +
            apply_outcome + verify 样本），把 (kind, text) 转译成 outcome
            形态（.ok/.reason）。判定/写回全在既有管道里，这里不复刻。"""
            kind, text = self._test_node(
                node.id, make_engine=make_engine or self._make_temp_engine)
            return SimpleNamespace(ok=(kind == "verify_ok"), reason=text)

        def _run_recheck_once(self, make_engine=None):
            """跑一轮名单复查（工作线程内调用）：计划/处置/样本全在
            core/list_recheck.run_recheck，本窗只供 store/settings/verify_fn。"""
            return list_recheck.run_recheck(
                self.store, self.settings,
                lambda node: self._recheck_verify_outcome(node, make_engine))

        def recheck_now(self):
            """手动「复查名单」：走 _run_task 工作线程（验真是磁盘/网络 IO，
            不进主线程）；忙时直接忽略（与连接/测速同款守卫）。"""
            if self._busy or self._batch_active or self._recheck_active:
                return
            self._run_task("recheck", self._run_recheck_once)

        def _on_recheck_setting_changed(self, *_):
            """定时复查开关变了：落盘（task24 §8-7 立即生效同款接线点）。
            复查挂 auto_timer 子周期、无独立定时器，无需重设——只写值。"""
            self._persist_recheck_switch()

        def _persist_recheck_switch(self):
            """定时复查开关 → settings.json（task33 销 work28 DONE §4-3
            尾巴：开关不再「关窗就忘」）。唯一落盘点 = 既有 settings 保存
            路径（merge → SettingsStore.save，与 _persist_auto_probe 同款，
            不新造通道）。程序化同步控件期间跳过（对话框已保存，避免重复
            写）；无 settings_store（离线测试替身形态）跳过落盘；IO 失败
            显式告警，不静默吞。
            单一事实源裁决（task33 简报二选一）：**与控件状态保持单一
            事实源**——控件初值来自设置、用户拨动即写回，_maybe_recheck
            首行守卫照旧读控件；设置页不载此开关（DESIGN §2.3），会话内
            无第二写入方，不存在分叉窗口。"""
            if self.settings_store is None or self._applying_settings:
                return
            self.settings = merge(self.settings, {
                "list_recheck_auto_enabled":
                    self.recheck_chk.isChecked()})
            try:
                self.settings_store.save(self.settings)
            except OSError as exc:
                self._show_alert(f"设置保存失败：{exc}")

        def _maybe_recheck(self):
            """定时复查入口（auto_timer 子周期，与自动探测同款守卫：忙时
            不跑）。开关默认关（铁律：自动类动作默认不开启；task33 起勾选
            状态持久化进 settings.list_recheck_auto_enabled，判断的单一
            事实源 = 控件状态——初值来自设置、拨动即写回，见
            _persist_recheck_switch）。守卫全过且确有到期对象才开一轮；与
            自动探测共用 10828/10829 端口对，双方互斥（_maybe_auto_probe
            亦查 _recheck_active）。"""
            if not self.recheck_chk.isChecked():
                return
            if (self._recheck_active or self._busy or self._batch_active
                    or self._auto_active):
                return
            if self.orchestrator.state != Orchestrator.IDLE:
                return  # 连接期间不跑（同自动探测口径）
            try:
                due = list_recheck.plan_recheck(self.store, self.settings)
            except ValueError as exc:   # list_recheck_days 非法/越界：如实报
                self._show_alert(f"名单复查设置非法，本轮不跑：{exc}")
                return
            if not due:
                return
            self._recheck_active = True
            self._launch_auto(lambda: self._run_recheck_once(
                make_engine=self._make_auto_engine), tag="auto_recheck")

        def _finish_recheck(self, report):
            """复查结果落地（主线程）：状态行只报计数与处置（不回显节点
            地址原文）。名单是派生视图，画像刷新后 refresh_pages 自然更新；
            变脏只提示不拉黑，黑名单可连只提示待人工确认（绝不自动放回）。"""
            c = report["counts"]
            parts = [f"名单复查完成：实测 {report['verified']}/{report['planned']}"
                     f" · 跳过 {c['skip']}"]
            if c["passed"]:
                parts.append(f"通过 {c['passed']}")
            if c["recheck_failed"]:
                parts.append(f"复查失败 {c['recheck_failed']}（画像未动）")
            if c["quarantine_cleared"]:
                parts.append(f"隔离摘牌 {c['quarantine_cleared']}")
            if c["still_quarantined"]:
                parts.append(f"仍污染 {c['still_quarantined']}")
            if c["view_lost"]:
                parts.append(f"干净度未知移出视图 {c['view_lost']}")
            text = " · ".join(parts)
            if c["dirty_out"]:
                text += (f"｜⚠ {c['dirty_out']} 个白名单节点复查变脏"
                         "（已移出白名单视图，不自动拉黑）")
            if c["banned_recoverable"]:
                text += (f"｜⚠ {c['banned_recoverable']} 个黑名单节点复查可连，"
                         "是否放回请人工确认（不会自动放回）")
            self._show_info(text)

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

        def delete_selected(self):
            """删除选中节点（支持多选）：连接中的跳过，删前一次确认。"""
            page = self.current_page()
            ids = page.selected_node_ids() if page else []
            if not ids:
                node = self.selected_node()
                ids = [node.id] if node else []
            if not ids:
                return
            active = self.orchestrator.active_node_id
            if active in ids:
                self._show_alert("正在连接的节点已跳过，先断开才能删它")
                ids = [i for i in ids if i != active]
            if not ids:
                return
            if len(ids) == 1:
                node = self.store.get_node(ids[0])
                name = node.name if node else ids[0]
                text = (f"确定删除「{name}」？\n"
                        "连同它的测速记录一起删，删了恢复不了。")
            else:
                text = (f"确定删除选中的 {len(ids)} 个节点？\n"
                        "连同它们的测速记录一起删，删了恢复不了。")
            ok = QMessageBox.question(self, "删除节点", text)
            if ok != QMessageBox.Yes:
                return
            n = self.delete_nodes_by_ids(ids)
            self._show_info(f"已删除 {n} 个节点")

        def delete_node_by_id(self, node_id: str) -> bool:
            """删除节点并刷新界面（与确认对话框分离，便于离线测试）。"""
            gone = self.store.delete_node(node_id)
            if gone:
                self.refresh_pages()
            return gone

        def delete_nodes_by_ids(self, node_ids) -> int:
            """批量删除（与确认对话框分离，便于离线测试）。返回实删个数。"""
            n = 0
            for nid in node_ids:
                if self.store.delete_node(nid):
                    n += 1
            if n:
                self.refresh_pages()
            return n

        def waste_node_ids(self, source):
            """本页的废节点 (不通, 污染)：不通 = 最近一次验真失败；
            污染 = 隔离标记或出口情报带污染。拉黑的不算（用户已处置）。"""
            dead, dirty = [], []
            for node in self.store.list_nodes(source=source,
                                              include_banned=False):
                polluted = node.extra.get("quarantined") == "1"
                if not polluted and node.exit_profile is not None:
                    polluted = pollution_of(node.exit_profile)[0]
                if polluted:
                    dirty.append(node.id)
                    continue
                last = history.last_result(self.store, node.id)
                if last is not None and not last[0]:
                    dead.append(node.id)
            return dead, dirty

        def purge_waste_on_page(self, source) -> int:
            """一键删废节点（与确认分离）：删本页不通+污染，连接中的跳过。"""
            dead, dirty = self.waste_node_ids(source)
            ids = [i for i in dead + dirty
                   if i != self.orchestrator.active_node_id]
            return self.delete_nodes_by_ids(ids)

        def purge_batch_waste(self) -> int:
            """删上一轮批量测出的废节点（与确认分离，便于离线测试）。"""
            ids = [i for i in self._batch_waste
                   if i != self.orchestrator.active_node_id]
            self._batch_waste = []
            return self.delete_nodes_by_ids(ids)

        def purge_waste_selected_page(self):
            """「一键删废节点」：优先清上一轮批量测出的那批；没有待清
            批次时清当前页的废节点。先报数，确认后删。"""
            if self._batch_waste:
                ok = QMessageBox.question(
                    self, "一键删废节点",
                    f"上一轮批量测出 {len(self._batch_waste)} 个废节点。\n"
                    "确定一键删除？连同测速记录一起删，删了恢复不了。")
                if ok != QMessageBox.Yes:
                    return
                n = self.purge_batch_waste()
                self._show_info(f"已删除 {n} 个废节点")
                return
            page = self.current_page()
            if page is None:
                return
            dead, dirty = self.waste_node_ids(page.source)
            if not dead and not dirty:
                self._show_info("本页没有测出不通或污染的节点")
                return
            ok = QMessageBox.question(
                self, "一键删废节点",
                f"本页有 {len(dead)} 个不通、{len(dirty)} 个污染节点。\n"
                "确定一键删除？连同测速记录一起删，删了恢复不了。")
            if ok != QMessageBox.Yes:
                return
            n = self.purge_waste_on_page(page.source)
            self._show_info(f"已删除 {n} 个废节点")

        # ---------------- 连接健康三件套（task5） ----------------
        def _takeover_mode(self) -> str:
            """当前接管方式：sing-box 引擎实例属性为准（IDLE 时与模式控件
            同步，CONNECTED 时控件禁用、属性即连接中的模式）。无 sing-box
            引擎时默认 proxy。"""
            engine = next((e for e in self.engines.values()
                           if isinstance(e, SingBoxEngine)), None)
            if engine is None:
                return "proxy"
            return getattr(engine, "takeover_mode", "proxy") or "proxy"

        def _on_mode_changed(self, _index):
            """模式控件切换：立即同步到引擎实例属性，并提示该模式的系统侧
            动作（mode_plan 纯数据）。切换本身不连接、不改节点（铁律）。"""
            mode = self.mode_combo.currentData()
            if mode not in MODES:
                return
            engine = next((e for e in self.engines.values()
                           if isinstance(e, SingBoxEngine)), None)
            if engine is not None:
                engine.takeover_mode = mode
            plan = mode_plan(mode)
            actions = "；".join(a["action"] for a in plan["system_actions"])
            self._show_info(f"接管方式：{mode}。{actions}。下次连接生效。")

        def _sysproxy_conflict_text(self, reason: str) -> str:
            """检测②冲突文案：TUN 模式换装（task6/DESIGN §6.2）——回环流量
            不经 tun，系统代理指到别家仍是真冲突；proxy 模式沿用原口径。"""
            if self._takeover_mode() == "tun":
                return (f"TUN 已接管全机流量，但{reason}——走系统代理的"
                        "应用流量到不了隧道。客户端不会改你的系统代理。")
            return reason

        def _health_precheck(self, node) -> bool:
            """连接前预检（检测①+②）：False=取消本次连接，True=继续。

            ①引擎端口被占 → 告警条并取消连接（引擎横竖起不来）；
            ②系统代理冲突 → 告警条但照常连接（用户点名优先，只提示不拦）。
            OpenVPN 是 TUN 模式、不占 7897/7898：只查②，参照端口仍用
            本客户端引擎端口。"""
            engine = self.engines.get(node.protocol)
            if isinstance(engine, SingBoxEngine):
                occupied = check_ports_free((engine.socks_port, engine.http_port))
                if occupied:
                    self._show_alert(
                        f"启动被挡：{describe_occupied(occupied)}。引擎起不来——"
                        "关掉占用它的程序（其他代理软件？）或改它的端口，再点一次连接。"
                        "客户端不会动你的系统设置，也不会自动重试。")
                    return False
                our_port = engine.http_port
            else:
                # 参照端口改用设置值（task24 §8-1 端口检查兜底点改读设置；
                # 无设置 → 模块默认端口兜底）。
                our_port = self.settings.http_port
            conflict = proxy_conflict(read_system_proxy(), our_port)
            if conflict["conflict"]:
                self._show_alert(
                    f"{self._sysproxy_conflict_text(conflict['reason'])}。"
                    "客户端不会改你的系统代理，"
                    "也不会切换节点，是否处置由你决定。")
                self._sysproxy_alert = True
            return True

        def _startup_health_check(self):
            """开窗体检（task5）：端口占用 + 系统代理冲突，只提示不拦截。"""
            engine = next((e for e in self.engines.values()
                           if isinstance(e, SingBoxEngine)), None)
            if engine is not None:
                occupied = check_ports_free((engine.socks_port, engine.http_port))
                if occupied:
                    self._show_alert(
                        f"启动被挡：{describe_occupied(occupied)}。引擎起不来——"
                        "关掉占用它的程序（其他代理软件？）或改它的端口，再点一次连接。"
                        "客户端不会动你的系统设置，也不会自动重试。")
                    return
                our_port = engine.http_port
            else:
                # 参照端口改用设置值（task24 §8-1 端口检查兜底点改读设置；
                # 无设置 → 模块默认端口兜底）。
                our_port = self.settings.http_port
            conflict = proxy_conflict(read_system_proxy(), our_port)
            if conflict["conflict"]:
                self._show_alert(
                    f"{self._sysproxy_conflict_text(conflict['reason'])}。"
                    "客户端不会改你的系统代理，"
                    "也不会切换节点，是否处置由你决定。")
                self._sysproxy_alert = True

        def _launch_health(self, tag: str, fn):
            """开一个健康后台线程（不占 _busy，见 _launch_auto 同款做法）。"""
            worker = _Worker(self.bridge, tag, fn)
            self._workers.append(worker)
            worker.finished.connect(lambda: self._workers.remove(worker))
            worker.start()

        def _health_probe_once(self, node, proxy_url):
            """一次出口探测（后台线程）：返回 (node_id, ok, latency_ms, reason)。"""
            probe = make_real_probe(proxy=proxy_url, test_muse=False)
            outcome = verify_exit(node, probe)
            return (node.id, outcome.ok, outcome.latency_ms, outcome.reason)

        def _sysproxy_check_once(self, http_port):
            """一次系统代理复查（后台线程）：返回 (conflict, reason)。"""
            result = proxy_conflict(read_system_proxy(), http_port)
            return (result["conflict"], result["reason"])

        def _maybe_health_probe(self):
            """检测③：每 15s 对当前活动连接做一次出口探测（后台线程）。"""
            if self._health_active or self._busy or self._batch_active:
                return
            node_id = self.orchestrator.active_node_id
            node = self.store.get_node(node_id) if node_id else None
            if node is None:
                return
            engine = self.engines.get(node.protocol)
            if isinstance(engine, SingBoxEngine):
                proxy_url = engine.http_proxy_url
            else:
                # OpenVPN 是 TUN 模式、无本地代理入站：走默认直连 probe
                # （与 cli verifier 分流同口径，流量实际走隧道）。
                proxy_url = None
            self._health_active = True
            self._launch_health(
                "health_probe",
                lambda: self._health_probe_once(node, proxy_url))

        def _maybe_sysproxy_check(self):
            """检测②复查：每 30s 看一眼系统代理是否被别家占了。"""
            if self._sysproxy_active or self._busy or self._batch_active:
                return
            engine = next((e for e in self.engines.values()
                           if isinstance(e, SingBoxEngine)), None)
            # 参照端口改用设置值（task24 §8-1 端口检查兜底点改读设置）
            http_port = (engine.http_port if engine is not None
                         else self.settings.http_port)
            self._sysproxy_active = True
            self._launch_health(
                "sysproxy_check",
                lambda: self._sysproxy_check_once(http_port))

        def _finish_health_probe(self, ok, payload):
            """健康探测结果落地（主线程）：只产生给人看的信号，绝不自动切换。"""
            if not ok:
                # 探测本身抛错 ≠ 节点不通（可能是本地引擎句柄异常）：
                # 原样展示错误，本次不计入样本。
                self._show_info(f"健康探测出错，本次不计入：{payload}")
                return
            node_id, sample_ok, latency_ms, _reason = payload
            if node_id != self.orchestrator.active_node_id:
                return  # 会话已换，旧样本不算到新节点头上
            self._health_samples.append({"ok": sample_ok})
            detail = connection_health_detail(self._health_samples)
            status = detail["status"]
            streak = detail["fail_streak"]
            if status == HEALTH_DEAD:
                text = (f"节点疑似已死：连续 {streak} 次探测失败。连接保持原样、"
                        "绝不自动换节点——请手动断开，再自己选下一个节点。")
                self._show_alert(text)
                if self.tray is not None:
                    self.tray.showMessage("gate-client 节点疑似已死", text,
                                          QSystemTrayIcon.Warning, 5000)
                # U6 容灾触发（work21/WIRING.md 落点①）：健康判死喂给容灾
                # 控制器（住前台窗，main() 注入本出口）；开关关时控制器零
                # 动作零记录。suspect 分支不喂（§5.1：不触发替换）。
                if self._failover_feed is not None:
                    self._failover_feed(failover.REASON_HEALTH_DEAD, node_id)
            elif status == HEALTH_SUSPECT:
                self._show_info(
                    f"连接可疑：连续 {streak} 次探测失败，仍保持连接、不自动切换。"
                    "可再观察一会，或自行断开后另选节点。")
            else:
                lat = (f"，延迟 {latency_ms:.0f}ms"
                       if latency_ms is not None else "")
                self._show_info(f"连接健康：出口探测正常{lat}")

        def _finish_sysproxy_check(self, ok, payload):
            """系统代理复查结果落地（主线程）：有冲突亮条，解除则撤条。"""
            if not ok:
                self._show_info(f"系统代理复查出错：{payload}")
                return
            conflict, reason = payload
            if conflict:
                self._show_alert(
                    f"{self._sysproxy_conflict_text(reason)}。客户端不会改你的系统代理，"
                    "也不会切换节点，是否处置由你决定。")
                self._sysproxy_alert = True
            elif self._sysproxy_alert:
                self._clear_alert()
                self._sysproxy_alert = False

        def poll_once(self):
            """巡检：掉线由 orchestrator.poll 上报 dropped 事件，绝不自动切换。"""
            if not self._busy:
                self.orchestrator.poll()
            # 健康子周期（task5）：复用 5s 父 tick，不新增定时器。
            self._health_tick += 1
            if self.orchestrator.state != Orchestrator.CONNECTED:
                return
            if self._health_tick % 3 == 0:
                self._maybe_health_probe()
            if self._health_tick % 6 == 0:
                self._maybe_sysproxy_check()
                # 检测④接管生效性（task6/DESIGN §6.4）：TUN 模式每 30s 只读
                # 查一次 /proc/net/route；未接管只告警、不修路由、不重连。
                if self._takeover_mode() == "tun":
                    self._tun_takeover_check_once()

        def _tun_takeover_check_once(self):
            """检测④落地（主线程）：只读检查，无后台线程（/proc 读入瞬时完成）。"""
            result = check_tun_takeover()
            if not result["effective"]:
                self._show_alert(
                    f"{result['reason']}。客户端不会自动修路由，请断开后"
                    "重试或向审查方反馈（可能需要提权路径排查）。")
                self._tun_alert = True
            elif self._tun_alert:
                self._clear_alert()
                self._tun_alert = False

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
                self._sysproxy_alert = False
                self._tun_alert = False
                # 新会话从 unknown 开始：上一条连接的连败史不带到新节点头上
                self._health_samples.clear()
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
            elif tag == "health_probe":
                # 健康探测同样不占 _busy（见 _launch_health）
                self._health_active = False
            elif tag == "sysproxy_check":
                self._sysproxy_active = False
            elif tag == "auto_recheck":
                # 定时名单复查同样不占 _busy：只复位自己的旗标（task28）
                self._recheck_active = False
            elif tag == "auto_collect":
                # 定时搜集轮同样不占 _busy：只复位自己的旗标（task31）
                self._collect_active = False
            else:
                self._busy = False
            if tag == "batch":
                self._batch_active = False
            ok, payload = result
            if tag == "health_probe":
                self._finish_health_probe(ok, payload)
            elif tag == "sysproxy_check":
                self._finish_sysproxy_check(ok, payload)
            elif tag == "auto_collect":
                # 定时搜集轮收尾（task31 红线）：失败只记 stderr 日志——
                # 不弹红条、不碰连接状态（status_label 不写）；成功也不抢
                # 状态栏（新节点经收尾统一的 refresh_pages 直接出现在库页；
                # 数字详情由 _sources_collect 打到 stdout，不入界面）。
                if not ok:
                    print(f"[自动搜集] 定时跑轮失败：{payload}",
                          file=sys.stderr)
            elif not ok:
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
                (_kind, ok_n, fail_n, skip_n, done, total, label,
                 fail_ids, dirty_ids) = payload
                stopped = "（已手动停止）" if done < total else ""
                waste = [i for i in fail_ids + dirty_ids
                         if i != self.orchestrator.active_node_id]
                self._batch_waste = waste
                msg = (f"批量测速完成（{label}）{stopped}：可连 {ok_n} · "
                       f"不通 {fail_n} · 跳过 {skip_n}（共测 {done}/{total}）")
                if waste:
                    msg += (f"；本轮废节点 {len(waste)} 个，"
                            "点「一键删废节点」可删掉")
                self._show_info(msg)
            elif tag in ("recheck", "auto_recheck") and ok \
                    and isinstance(payload, dict):
                # 名单复查（task28）：手动/定时同口径落地，状态行报计数
                self._finish_recheck(payload)
            elif tag == "import_url" and ok:
                self._intake_done(
                    payload.nodes,
                    f"订阅导入（格式 {payload.format}，跳过坏行 {payload.skipped}）")
            elif tag == "sources" and isinstance(payload, str):
                self._show_info(payload)
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
            # 接管方式只在未连接时可切（task6/DESIGN §2.2）：连接中禁用，
            # tooltip 说明先断开再改。
            self.mode_combo.setEnabled(not connected and not self._busy)
            self.mode_combo.setToolTip(
                "先断开，再改接管方式" if connected else
                "接管方式：只在未连接时可切换，切换后下次连接生效。"
                "TUN 接管全机流量，首次开启会弹系统授权框；提权启动器待"
                "真机实现，当前版本选 TUN 点连接会显式报错。")
            self.disconnect_btn.setEnabled(connected and not self._busy)
            self.verify_btn.setEnabled(has_sel and not self._busy)
            self.recheck_btn.setEnabled(not self._busy)   # task28：复查名单
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
                    res = format_src.import_any_file(path)
                    self._intake_done(res.nodes,
                                      f"订阅导入（格式 {res.format}，跳过坏行 {res.skipped}）")
                except (ValueError, RuntimeError, OSError) as exc:
                    self._show_alert(f"导入失败：{exc}")

        def import_subscription_url(self):
            url, ok = QInputDialog.getText(self, "订阅地址导入", "订阅地址（http/https）：")
            if not ok or not url.strip():
                return
            # 地址只在内存里用，不回显、不落日志（同 CLI 红线）
            def _import_and_register():
                res = format_src.import_any_text(
                    subscription_src.fetch_subscription_text(url.strip()),
                    subscription_url=url.strip())
                # 订阅地址导入即登记：下次一键更新不用再贴地址；已登记过不报错。
                try:
                    self._registry().register("subscription", url.strip())
                except ValueError:
                    pass
                return res
            self._run_task("import_url", _import_and_register)

        # ---------------- 来源登记（一键更新） ----------------
        def _registry(self):
            """登记表：与节点库同目录的 sources.json（CLI 共用同一份）。"""
            path = os.path.join(
                os.path.dirname(os.path.abspath(self.store.path or "client.db")),
                "sources.json")
            return registry_src.SourceRegistry(path)

        def _update_sources_for_page(self, source, fetch_fn=None):
            """更新某一页已登记的来源（工作线程内跑；fetch_fn 仅测试注入）。"""
            reg = self._registry()
            entries = [e for e in reg.entries()
                       if e["page"] == source and e["enabled"]]
            if not entries:
                return ("本页没有已登记的来源：订阅地址导入后自动登记，"
                        "VPNGate 页可用「登记并更新 feed」")
            return "\n".join(
                registry_src.update_entry(
                    reg, self.store, e["id"], fetch_fn=fetch_fn).summary()
                for e in entries)

        def update_registered_sources(self, fetch_fn=None):
            """当前页「更新已登记来源」按钮：真联网拉取，走工作线程。"""
            page = self.current_page()
            if page is None:
                return
            self._run_task(
                "sources",
                lambda: self._update_sources_for_page(page.source,
                                                      fetch_fn=fetch_fn))

        def _register_and_update(self, type_, entry_id, name, fetch_fn=None):
            reg = self._registry()
            if not reg.get(entry_id):
                reg.register(type_, name=name)
            return registry_src.update_entry(
                reg, self.store, entry_id, fetch_fn=fetch_fn).summary()

        def register_and_update_feed(self, fetch_fn=None):
            """VPNGate 页「登记并更新 feed」：首次登记官方 feed 地址并拉取。
            合并页唯一登记入口（task8/task23 落页，work8/DESIGN §5.1）。"""
            self._run_task(
                "sources",
                lambda: self._register_and_update(
                    "feed", "feed", "VPN Gate 官方 feed", fetch_fn=fetch_fn))

    def main(argv=None) -> int:
        parser = argparse.ArgumentParser(prog="gate-client-gui",
                                         description="自研代理客户端（M3 图形界面）")
        # --db 不给默认值：留 None 让下方按 §8-3 四档优先级解析
        #（--db 显式 > GATE_CLIENT_DB > settings.data_dir > 内置默认）。
        parser.add_argument("--db", default=None,
                            help="本地数据库路径（默认按 统一设置 > "
                                 "GATE_CLIENT_DB 环境变量 > 内置默认 "
                                 "~/.local/share/gate-client/client.db 解析）")
        args = parser.parse_args(argv)
        app = QApplication(sys.argv)
        app.setApplicationName("gate-client")   # WM_CLASS 对齐 .desktop 的 StartupWMClass
        app.setOrganizationName("gate-client")
        app.setStyle("Fusion")                  # 统一控件底子，QSS 不被原生主题带偏
        app.setStyleSheet(DARK_QSS)
        app.setQuitOnLastWindowClosed(False)
        # —— 统一设置：加载 + 首次初始化 + QSettings 迁移（task24 §4/§8-6）——
        # settings.json 跟随「显式覆盖目录，否则内置默认目录」（§4.1/§8-3）。
        env_db = os.environ.get("GATE_CLIENT_DB")
        sfile = settings_file_path(explicit_db=args.db, env_db=env_db)
        sstore = SettingsStore(sfile)
        try:
            settings = bootstrap_settings(sstore, _qsettings_auto_probe_reader)
        except SettingsVersionError as exc:
            # §4.2：版本高于当前支持 → 拒读报错，不重置不覆盖
            QMessageBox.critical(
                None, "设置文件无法读取",
                f"{exc}\n\n文件：{sfile}\n"
                "设置文件由更新版本的客户端写入，请先用该版本处理，"
                "本客户端不会覆盖它。")
            return 2
        except (SettingsCorruptError, SettingsError) as exc:
            # §4.2：损坏 / 未知键 / 非法值 → 弹窗给文件路径 + 「重置为默认」；
            # 重置 = 原文件改名留档（不静默覆盖、不删原始证据）再写默认
            choice = QMessageBox.warning(
                None, "设置文件有问题",
                f"{exc}\n\n文件：{sfile}\n\n"
                "是否重置为默认值？（原文件会改名留档，不会删除）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if choice != QMessageBox.Yes:
                return 2
            settings = sstore.reset(tag="corrupt")
        # 库路径四档优先级（task24 §8-3）；data_dir 档在此生效（下次启动
        # 构造点）。
        store = Store(resolve_db_path(explicit_db=args.db, env_db=env_db,
                                      settings=settings))
        # task23 落页：启动检测四页时代存量（sources.json 含 feed_mirror 残留
        # ∨ 库内旧两键节点）→ 一次性迁移，幂等、误触发无害、不联网
        # （work8/DESIGN §8-6；实现在 sources/landing.py，只调 merge_proto）。
        # 位置在两窗装配之前：UsageWindow/MainWindow 构造即读库。
        run_startup_migration(store)
        # U1（task16，work16/DESIGN.md §3）：两窗装配——主窗 UsageWindow
        # 在内建 orchestrator/engines，后台窗 MainWindow 注入同一实例，
        # 事件通道由 main() 统一扇出到两窗 bridge。懒加载：usage_window
        # 模块级 from .app import …，顶层 import 会循环导入。
        # task24 落页：统一设置注入两窗（同一份 settings/settings_store——
        # 值只有一份、存储只有一处，§6）。
        from .usage_window import UsageWindow

        usage = UsageWindow(store, settings=settings, settings_store=sstore)
        backend = MainWindow(store, orchestrator=usage.orchestrator,
                             engines=usage.engines,
                             settings=settings, settings_store=sstore)
        usage.set_backend_window(backend)
        # U6 容灾接线（work21/WIRING.md 落点②）：健康判死只在后台窗探测
        # 收尾检测到（_finish_health_probe HEALTH_DEAD 分支），控制器住
        # 前台窗——把前台窗信号适配器注入后台窗，替换动作经前台窗既有
        # _run_task 串行通道执行（与用户连接同一管道）。
        backend._failover_feed = usage._failover_feed

        def _fanout(ev):
            usage.bridge.session_event.emit(ev)
            backend.bridge.session_event.emit(ev)

        usage.orchestrator.on_event = _fanout
        usage.show()                            # 主窗启动显示；后台窗不 show
        backend.schedule_startup_probe()        # 自动探测留后台
        return app.exec()

else:

    def main(argv=None) -> int:  # pragma: no cover
        print(INSTALL_HINT, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
