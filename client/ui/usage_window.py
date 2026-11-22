#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用界面（ui/usage_window.py）—— U1：前台消费侧主窗（task16 / work10 §12 U1 行）
================================================================================
前台主窗，日常用 VPN 在这里（work10 §2.1 信息架构定稿）：

- ① 连接状态区（恒顶置）：常驻状态面板（未连接/连接中/已连接/已断开，
  红色告警复用现有样式）+ 当前节点行（实测名 exit_display_name 口径 +
  六板块徽标 + 场景徽标 + 延迟 format_latency_ms 口径）+ 主按钮行
  （连接/断开 + 接管方式，复用现窗 primaryBtn 交互与 orchestrator 管道）。
- ② 节点选择区：按 classify_proto.board_of 六板块分页 + 「未分类」兜底
  （判不出维度不许硬塞）；板块内排序复用 history.sorted_nodes——这是
  展示顺序，连接永远由用户点（铁律）；行内动作恰为
  连接/置顶/测速/测特殊站（第四件 task20/§9.2 追加，只测与回写）。
- 菜单：打开后台 / 设置 / 诊断（一键诊断、导出诊断包）。设置页未接线，
  点击如实提示；诊断两项 task22 接线（task7 欠账）：后台线程通道
  （_launch_diag，不占 _busy），结果与包路径走消息行/结果框，不回显节点信息。
- 现有 app.py 的 MainWindow 降为后台窗（供给侧），托盘归本窗（work10
  §2.1「托盘归属主窗」）；两窗共用同一 Store/Orchestrator（接法与线程
  口径见 work16/DESIGN.md §3/§4，app.py 集成改动照单执行）。
- orchestrator.py 一行不改（铁律）；永不自动切换节点；③区已落
  （task17），①区速度行、②区按板块批量测、⑤区监测状态行已落
  （task19/U4）；④区归 U6；⑥区已落（task20）；未落区不摆空壳占位；
  使用界面
  永不出现：导入/登记/
  更新来源、验真管理、搜集、删除/拉黑、分类改判（work10 §2.3 边界清单）。
- 状态变化一律走告警条/消息行，不弹模态框（0.7.0 既定偏好）。

用法（独立冒烟）:
  python -m client.ui.usage_window [--db 路径]
集成后由 app.py main() 与后台窗一起装配（work16/DESIGN.md §3）。
缺 PySide6 时打印安装指引并退出（不假装能跑）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import urllib.request

from ..core import backup_group
from ..core import classify_proto
from ..core import diag
from ..core import diag_collect
from ..core import failover
from ..core import history
from ..core import settings_store as settings_store_io, site_blocklist
from ..core import speedtest
from ..core import special_sites, special_probe
from ..core.settings_store import (
    SettingsStoreError, load_settings,
)
from ..core.health import (check_ports_free, describe_occupied, proxy_conflict,
                           read_system_proxy)
from ..core.orchestrator import Orchestrator, OrchestratorError
from ..core.settings_proto import (Settings, SettingsCorruptError,
                                   SettingsError, SettingsStore,
                                   SettingsVersionError, bootstrap_settings,
                                   derived_data_dir, resolve_db_path,
                                   settings_file_path)
from ..core.store import Store
from ..core.tun_config import MODES, mode_plan
from ..core.usage_model import UsageSettings
from ..core.verify import (apply_outcome, exit_display_name, make_real_probe,
                           verify_exit)
from ..engines.base import EngineError
from ..engines.openvpn_engine import OpenVPNEngine
from ..engines.singbox_engine import SINGBOX_PROTOCOLS, SingBoxEngine
from .cli import DEFAULT_DB, _build_orchestrator

try:
    from PySide6.QtCore import Qt, QTimer
    from PySide6.QtGui import QAction
    from PySide6.QtWidgets import (
        QAbstractItemView, QApplication, QComboBox, QDialog, QFileDialog,
        QFrame, QHBoxLayout, QHeaderView, QLabel, QMainWindow, QMenu, QMessageBox,
        QPushButton, QSystemTrayIcon, QTabWidget, QTableWidget,
        QTableWidgetItem, QVBoxLayout, QWidget,
    )
    from .app import (DARK_QSS, NETWORK_LABELS, SCENE_LABELS, _Bridge, _Worker,
                      _dot_icon, _qsettings_auto_probe_reader)
    from .usage_rules_page import UsageRulesPanel
    from .special_sites_page import SpecialSitesPanel, make_site_actions
    _QT_OK = True
except ImportError:  # pragma: no cover - 无 Qt 环境时的降级路径
    _QT_OK = False

INSTALL_HINT = ("缺少 PySide6，图形界面无法启动。\n"
                "安装：pip install PySide6-Essentials\n"
                "（命令行版不受影响：python -m client.ui.cli --help）")

_PAYLOAD_TIMEOUT_S = 30   # 单次建连/响应超时（整次下载另有 budget_s 预算）


def _make_proxy_opener(proxy_url):
    """经给定代理入口的 urllib opener——core/speedtest 的 opener 注入
    参数由此构造（真实网络胶水只活在 UI 层，core 不碰真实网络）。
    形态同 verify._opener（core/verify.py:357-362）：代理进 ProxyHandler，
    空代理（{}）=不配代理并覆盖环境变量代理。连接类网络错误按
    OSError/TimeoutError 抛出（urllib 正是如此），speedtest 据此归类。
    返回 url→二进制流 函数（符合 speedtest 的 opener 合同）。"""
    def _open(url: str):
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler(
                {"http": proxy_url, "https": proxy_url} if proxy_url else {}))
        return opener.open(url, timeout=_PAYLOAD_TIMEOUT_S)

    return _open

# ①区四态（work10 §2.2：未连接 / 连接中 / 已连接 / 已断开，红色告警同显）
ST_IDLE = "未连接"
ST_CONNECTING = "连接中"
ST_CONNECTED = "已连接"
ST_DROPPED = "已断开"


if _QT_OK:

    class SessionPanel(QFrame):
        """①区常驻状态面板：圆点 + 大状态字。

        写入点只有 set_state——消息/告警永远写不进来（work14 §5.1-1：
        现窗「一条状态行当所有通道用」是最大教训，前台窗从第一版就
        分通道）。状态只随会话事件变化（work14 §5.3-1）。"""

        _DOT_COLOR = {ST_IDLE: "#9e9e9e", ST_CONNECTING: "#f2a900",
                      ST_CONNECTED: "#2e7d32", ST_DROPPED: "#b00020"}

        def __init__(self, parent=None):
            super().__init__(parent)
            lay = QHBoxLayout(self)
            lay.setContentsMargins(10, 6, 10, 6)
            self.dot_label = QLabel()
            lay.addWidget(self.dot_label)
            self.state_label = QLabel(ST_IDLE)
            self.state_label.setStyleSheet(
                "font-size:20px;font-weight:bold;padding:2px 6px;")
            lay.addWidget(self.state_label)
            lay.addStretch(1)
            self._apply()

        def set_state(self, state: str):
            """唯一写入点：①区四态之一。"""
            if state not in self._DOT_COLOR:
                raise ValueError(f"未知会话状态: {state}")
            self.state_label.setText(state)
            self._apply()

        @property
        def state(self) -> str:
            return self.state_label.text()

        def _apply(self):
            color = self._DOT_COLOR[self.state_label.text()]
            self.dot_label.setText("●")
            self.dot_label.setStyleSheet(f"color:{color};font-size:22px;")

    class BoardPage(QWidget):
        """②区一个板块页：空态提示 + 八列节点表（列口径同后台
        SourcePage，简报口径：名称/协议/出口/类型/干净/延迟/稳定性/最近）。

        行内动作恰为 连接/置顶/测速/测特殊站（ROW_ACTIONS）——右键菜单
        + 双击=连接。
        板块是归类不是排名；置顶只影响展示顺序（history.sort_key 口径）。"""

        COLUMNS = ("名称", "协议", "出口", "类型", "干净", "延迟", "稳定性",
                   "最近", "特殊站")
        # 「特殊站」列（task20 / work10 §9.2）：Σ 通过数/总数 + 失败站
        # 徽标（special_probe.row_special_text 口径）；与场景徽标并存
        # 不合并（画像判定 vs 实测徽标是两回事，各走各的列）。
        ROW_ACTIONS = ("连接", "置顶", "测速", "测特殊站")
        # 行内动作全集（U1 定为三件；task20/§9.2 追加「测特殊站」为
        # 第四件——只测与回写 extra，不连不切节点；铁律边界照旧）。

        def __init__(self, window: "UsageWindow", board_key: str):
            super().__init__()
            self.window = window
            self.board_key = board_key
            layout = QVBoxLayout(self)

            bar = QHBoxLayout()
            self.count_label = QLabel("")
            bar.addWidget(self.count_label)
            bar.addStretch(1)
            layout.addLayout(bar)

            # 板块级批量测入口（task19，②区落点；不是 ROW_ACTIONS 行内
            # 三件套）。决策在 core/speedtest.board_batch_plan（板块分组
            # + 载荷门卫），本按钮只执行计划；批量中兼做「停止」。
            self.batch_btn = QPushButton("测本板块")
            self.batch_btn.setToolTip(
                "按板块批量测：延迟必测；设置里填了测速载荷地址才附带吞吐"
                "实测（5MB 档、可中止）。只测与回写，不连不切节点。")
            self.batch_btn.clicked.connect(
                lambda: self.window.batch_test_board(self))
            bar.addWidget(self.batch_btn)

            self.empty_hint = QLabel(self._empty_text())
            self.empty_hint.setWordWrap(True)
            self.empty_hint.setStyleSheet("color:#9a9ca6;padding:16px;")
            layout.addWidget(self.empty_hint)

            self.table = QTableWidget(0, len(self.COLUMNS))
            self.table.setHorizontalHeaderLabels(self.COLUMNS)
            self.table.setSelectionBehavior(QTableWidget.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(0, QHeaderView.Stretch)  # 名称列吃满余宽
            for col in range(1, len(self.COLUMNS)):
                header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
            self.table.itemSelectionChanged.connect(window._update_action_states)
            self.table.itemDoubleClicked.connect(
                lambda _it: self.window.connect_from_page(self))
            # 行内动作走右键菜单（work14 §5.1-4：节点级动作进行内，
            # 不进全局按钮排；前台全局按钮排只有①区主按钮）
            self.table.setContextMenuPolicy(Qt.CustomContextMenu)
            self.table.customContextMenuRequested.connect(self._show_row_menu)
            layout.addWidget(self.table)

        def _empty_text(self) -> str:
            if self.board_key == classify_proto.UNCLASSIFIED:
                return ("本板块暂无节点。判不出维度的节点落这里（不许硬塞六板块）；"
                        "补全维度请到后台处理，归类完成后节点自动归位。")
            return ("本板块暂无节点。节点从后台入库并完成归类后会出现在这里；"
                    "板块是归类不是排名，连接永远由你自己点。")

        # ---------------- 行渲染（列口径同后台 SourcePage.populate） ----------
        def populate(self, nodes, active_node_id):
            keep_id = self.selected_node_id()
            self.table.setRowCount(0)
            for n in nodes:
                row = self.table.rowCount()
                self.table.insertRow(row)
                ep = n.exit_profile
                marks = ("📌" if n.pinned else "") + \
                        ("☠" if n.extra.get("quarantined") == "1" else "")
                active = "● " if n.id == active_node_id else ""
                name_item = QTableWidgetItem(f"{active}{marks}{n.name}")
                name_item.setData(Qt.UserRole, n.id)
                exit_s = (f"{ep.country_code or ep.country or ''} {ep.ip}".strip()
                          if ep.ip else "未验")
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
                values = (name_item, n.protocol, exit_s, type_s, clean_s,
                          lat_s, stab_s, last_s,
                          self.window._special_text_for(n))
                for col, val in enumerate(values):
                    item = val if isinstance(val, QTableWidgetItem) else QTableWidgetItem(str(val))
                    self.table.setItem(row, col, item)
                if n.id == keep_id:
                    self.table.setCurrentCell(row, 0)
            self.count_label.setText(f"{len(nodes)} 个节点")
            self.empty_hint.setVisible(len(nodes) == 0)
            self.table.setVisible(len(nodes) > 0)

        def selected_node_id(self):
            row = self.table.currentRow()
            if row < 0:
                return None
            item = self.table.item(row, 0)
            return item.data(Qt.UserRole) if item else None

        def visible_node_ids(self):
            return [self.table.item(r, 0).data(Qt.UserRole)
                    for r in range(self.table.rowCount())
                    if self.table.item(r, 0) is not None]

        # ---------------- 行内动作（恰为 ROW_ACTIONS 三个） ----------------
        def row_action_bindings(self):
            """行内动作绑定（菜单与测试共用同一出口，集合恰为 ROW_ACTIONS）。"""
            return [
                ("连接", lambda: self.window.connect_from_page(self)),
                ("置顶", lambda: self.window.toggle_pin_from_page(self)),
                ("测速", lambda: self.window.test_from_page(self)),
                ("测特殊站", lambda: self.window.test_special_from_page(self)),
            ]

        def _show_row_menu(self, pos):
            if self.table.currentRow() < 0:
                return
            menu = QMenu(self)
            for text, slot in self.row_action_bindings():
                act = QAction(text, self)
                act.triggered.connect(slot)
                menu.addAction(act)
            menu.exec(self.table.viewport().mapToGlobal(pos))

    class UsageWindow(QMainWindow):
        """前台主窗：①连接状态区 + ②六板块节点选择区 + 菜单骨架。

        两窗共用同一 Store/Orchestrator（简报红线：不许各开一个连接）：
        - 集成模式：app.py main() 建本窗（orchestrator 在此内建），再把同一
          orchestrator/engines 注入后台窗，事件通道由 main() 统一扇出
          （work16/DESIGN.md §3 改动点③）。
        - 独立冒烟模式：orchestrator=None 时经 _build_orchestrator 自建
          （cli.py:264 同款）；菜单「打开后台」时现建一个共用实例的后台窗。
        """

        def __init__(self, store: Store, orchestrator: Orchestrator = None,
                     engines: dict = None, probe_factory=None,
                     backend_window=None,
                     settings=None, settings_store=None):
            super().__init__()
            self.store = store
            # 统一设置（task24 §8 接线）：settings=None → 默认值（=现行行为）；
            # settings_store=None → 无落盘点（离线测试替身形态，落盘/设置页
            # 跳过）。
            self.settings = (settings if settings is not None else Settings())
            self.settings_store = settings_store
            self.bridge = _Bridge()
            self.bridge.session_event.connect(self._on_session_event)
            self.bridge.task_finished.connect(self._on_task_finished)
            self.bridge.progress.connect(self._show_info)
            self._workers = []
            self._busy = False
            self._session_state = ST_IDLE
            self._backend_window = backend_window
            self._last_node_count = -1   # ②区库变更对账游标
            self._batch_active = False   # ②区按板块批量测进行中旗（task19）
            self._batch_stop = False     # 批量停止旗（后台 _run_batch 同名同义）
            # U6 ④容灾区（task21）：替换进行中旗（tag="failover" 任务收尾
            # 复位）+ 因 _busy 暂缓待派发的容灾信号（poll_once/任务收尾补派）。
            self._failover_active = False
            self._failover_pending = None

            if orchestrator is not None:
                self.orchestrator = orchestrator
                self.engines = engines if engines is not None else orchestrator.engines
            else:
                factory = probe_factory or make_real_probe
                # task24 §8-1：引擎构造点改读设置——经 engine_factory 自建
                # 引擎束（SingBoxEngine 传设置端口 + task17 供给器原样保留；
                # out_dir 从派生基准目录 derived_data_dir 的 ovpn_out 派生）。
                # 保留 built 原字典：verifier 闭包共享它，端口对齐
                #（_sync_engine_ports_with_settings）原地替换才对两条引用
                # 都生效。
                holder = {}
                settings_file = (self.settings_store.path
                                 if self.settings_store is not None else None)

                def _engine_factory():
                    out_dir = os.path.join(
                        derived_data_dir(store.path, self.settings), "ovpn_out")

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

                    singbox = SingBoxEngine(
                        usage_provider=_usage_provider,
                        socks_port=self.settings.socks_port,
                        http_port=self.settings.http_port)
                    built = {"openvpn": OpenVPNEngine(out_dir=out_dir)}
                    for proto in SINGBOX_PROTOCOLS:
                        built[proto] = singbox
                    holder["engines"] = built
                    return built

                self.orchestrator = _build_orchestrator(
                    store, factory, _engine_factory,
                    settings=self.settings, settings_file=settings_file)
                self.engines = holder.get("engines", self.orchestrator.engines)
            self._install_event_listener()
            self._setup_failover()   # U6：④容灾控制器装配（work21/WIRING.md 落点③）

            self.setWindowTitle("gate-client · 使用界面")
            self.resize(1080, 640)
            central = QWidget()
            root = QVBoxLayout(central)
            self.setCentralWidget(central)

            # ---------------- 菜单骨架（简报口径三项） ----------------
            mbar = self.menuBar()
            self.act_backend = QAction("打开后台", self)
            self.act_backend.triggered.connect(self._open_backend)
            mbar.addAction(self.act_backend)
            self.act_settings = QAction("设置", self)
            self.act_settings.triggered.connect(self._open_settings)
            mbar.addAction(self.act_settings)
            self.menu_diag = mbar.addMenu("诊断")
            self.act_doctor = QAction("一键诊断", self)
            self.act_doctor.triggered.connect(self._run_doctor)
            self.menu_diag.addAction(self.act_doctor)
            self.act_export_diag = QAction("导出诊断包", self)
            self.act_export_diag.triggered.connect(self._export_diag)
            self.menu_diag.addAction(self.act_export_diag)

            # ---------------- ① 连接状态区（恒顶置） ----------------
            # 红色告警条（掉线/假连/错误——会话级事故才亮；样式同现窗 app.py:382-386）
            self.alert_bar = QLabel("")
            self.alert_bar.setStyleSheet(
                "background:#b00020;color:white;padding:8px;font-weight:bold;")
            self.alert_bar.setWordWrap(True)
            self.alert_bar.setVisible(False)
            root.addWidget(self.alert_bar)

            # 常驻状态面板（work14 §5.1-1：连接状态常驻，消息写不进来）
            self.session_panel = SessionPanel()
            root.addWidget(self.session_panel)

            # 当前节点行（实测名 + 板块徽标 + 场景徽标 + 延迟；只随会话事件写）
            self.node_detail = QLabel("")
            self.node_detail.setWordWrap(True)
            self.node_detail.setStyleSheet("color:#c9cbd3;padding:2px 12px;")
            root.addWidget(self.node_detail)

            # ①区速度行（task19，work10 §10 展示口径）：延迟（既有探测值）
            # + 最近吞吐实测/自报三态（speedtest.format_speed_display）。
            # 只显示最近实测，不常驻跑吞吐（常驻下载会干扰连接本身）。
            self.speed_line = QLabel("")
            self.speed_line.setWordWrap(True)
            self.speed_line.setStyleSheet("color:#c9cbd3;padding:2px 12px;")
            root.addWidget(self.speed_line)

            # 过程消息行（work14 §5.1-1：与连接状态分开的第三条通道）
            self.message_line = QLabel("")
            self.message_line.setWordWrap(True)
            self.message_line.setStyleSheet("color:#9a9ca6;padding:2px 12px;")
            root.addWidget(self.message_line)

            # 主按钮行：连接（primaryBtn 同款）/断开 + 接管方式（现窗约束照搬）
            actions = QHBoxLayout()
            self.connect_btn = QPushButton("连 接")
            self.connect_btn.setObjectName("primaryBtn")  # 深色主题的主按钮样式
            self.connect_btn.clicked.connect(self.connect_selected)
            actions.addWidget(self.connect_btn)
            self.disconnect_btn = QPushButton("断开")
            self.disconnect_btn.clicked.connect(self.disconnect)
            actions.addWidget(self.disconnect_btn)
            # 接管方式（task6 口径，同现窗 mode_combo app.py:411-419）：只在
            # 未连接时可切（CONNECTED 禁用，见 _update_action_states）；切换=
            # 改引擎实例属性，下次连接生效。TUN 未就绪的报错文案由引擎
            # _check_tun_privilege 经 error 事件上告警条，tooltip 同款照搬。
            self.mode_combo = QComboBox()
            self.mode_combo.addItem("系统代理", "proxy")
            self.mode_combo.addItem("TUN 接管全机", "tun")
            self.mode_combo.setToolTip(
                "接管方式：只在未连接时可切换，切换后下次连接生效。"
                "TUN 接管全机流量，首次开启会弹系统授权框；提权启动器待"
                "真机实现，当前版本选 TUN 点连接会显式报错。")
            self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
            actions.addWidget(self.mode_combo)
            actions.addStretch(1)
            root.addLayout(actions)

            # ---------------- ② 节点选择区（六板块 + 未分类） ----------------
            self.tabs = QTabWidget()
            self.boards = {}
            for b in classify_proto.BOARDS:
                page = BoardPage(self, b)
                self.boards[b] = page
                self.tabs.addTab(page, classify_proto.BOARD_LABELS[b])
            page = BoardPage(self, classify_proto.UNCLASSIFIED)
            self.boards[classify_proto.UNCLASSIFIED] = page
            self.tabs.addTab(page, "未分类")
            root.addWidget(self.tabs)
            # ---------------- ③ 规则与网络区（U2/task17） ----------------
            # 唯一编辑值 = task12 Settings 存储（usage_dict() 一侧）；生效
            # 口径=下次连接生效（连接时供给器现读），面板常驻明示。
            self.rules_panel = UsageRulesPanel()
            root.addWidget(self.rules_panel)

            # ---------------- ⑤ 测速与监测区（task19：本段落监测状态行） ----
            # 状态行只读 task12 Settings 的 auto_probe 两字段
            # （speedtest.monitor_status_text 拼文案），不持有第二份
            # 开关/间隔；轮询时现读 settings.json 同一份存储（改即见）。
            zone5 = QFrame()
            z5 = QVBoxLayout(zone5)
            z5.setContentsMargins(10, 4, 10, 4)
            zone5_title = QLabel("测速与监测")
            zone5_title.setStyleSheet("font-weight:bold;padding:2px;")
            z5.addWidget(zone5_title)
            self.monitor_line = QLabel("")
            self.monitor_line.setWordWrap(True)
            self.monitor_line.setStyleSheet("color:#9a9ca6;padding:2px 12px;")
            z5.addWidget(self.monitor_line)
            root.addWidget(zone5)

            # ---------------- ⑥ 特殊网站测试区（task20 / work10 §9） ----
            # 清单文件与 site_blocklist.json 同目录口径（数据目录从
            # store.path 推导；special_sites.special_sites_path 可注入，
            # 离线测试落临时目录）。面板只管清单增删与触发；测试动作走
            # 本窗接线方法——批量复用批量测速管道形态（_Worker + tag +
            # 停止旗 + progress），临时引擎纪律同 _test_node（10818/10819）。
            self._special_batch_active = False   # ⑥区批量进行中旗（同款形态）
            self._special_stop = False           # ⑥区批量停止旗
            _special_dir = os.path.dirname(os.path.abspath(store.path
                                                            or "client.db"))
            self._special_sites_path = special_sites.special_sites_path(
                _special_dir)
            self.special_panel = SpecialSitesPanel(
                sites_path=self._special_sites_path,
                on_test_current=self.test_special_current,
                on_test_board=self.test_special_board_current)
            root.addWidget(self.special_panel)

            # 托盘归主窗（work10 §2.1）；巡检定时器同现窗节奏（5s）
            self._setup_tray()
            self.poll_timer = QTimer(self)
            self.poll_timer.setInterval(5000)
            self.poll_timer.timeout.connect(self.poll_once)
            self.poll_timer.start()

            self.refresh_boards()
            self._startup_health_check()
            self._update_action_states()

        # ---------------- 两窗共用：事件通道 ----------------
        def _install_event_listener(self):
            """把本窗的会话事件监听并入 orchestrator 的事件通道（不覆盖既有监听）。

            orchestrator 只有单个 on_event 槽（铁律结构一行不改）：约定
            后来者把既有监听链在自己后面，谁先挂谁先收、互不覆盖。
            集成模式的规范接法是 main() 统一扇出（work16/DESIGN.md §3
            改动点③），本方法是其独立冒烟模式的兜底。"""
            prev = self.orchestrator.on_event

            def _emit(ev):
                self.bridge.session_event.emit(ev)
                if prev is not None:
                    prev(ev)

            self.orchestrator.on_event = _emit

        def set_backend_window(self, window):
            """集成装配：app.py main() 建好后台窗后注入引用（菜单「打开后台」用）。"""
            self._backend_window = window

        def _open_backend(self):
            """菜单「打开后台」：显示后台窗（work10 §2.1 跳转关系）。

            集成模式用注入的后台窗；独立冒烟模式现建一个共用同一
            Store/Orchestrator 的后台窗（现窗 app.py:373 会整体覆盖事件
            通道，建好后把本窗监听重新挂回链头）。"""
            win = self._backend_window
            if win is None:
                from .app import MainWindow
                # task24：后台窗共享同一份统一设置（§6 值只有一份）
                win = MainWindow(self.store, orchestrator=self.orchestrator,
                                 engines=self.engines,
                                 settings=self.settings,
                                 settings_store=self.settings_store)
                self._backend_window = win
                self._install_event_listener()
            win.show()
            win.raise_()
            win.activateWindow()

        def _open_settings(self):
            """设置菜单（task24 设置页落页）：打开统一设置页（模态）。
            保存后的生效时机落地见 _apply_saved_settings（§5.3 三档）。
            无 settings_store（离线测试替身形态）时如实提示不假开——
            落盘是设置页的硬前提，不搞「改了不存」的假入口。"""
            if self.settings_store is None:
                self._show_info("设置存储不可用，设置页未打开。")
                return
            from .settings_dialog import SettingsDialog
            dlg = SettingsDialog(self.settings, self.settings_store, parent=self)
            if dlg.exec() == QDialog.Accepted and dlg.saved_settings is not None:
                self._apply_saved_settings(dlg.saved_settings)

        def _apply_saved_settings(self, s):
            """设置页保存后的本窗落地（task24 §5.3 逐项口径）：
            - 端口：**下次连接生效**——连接构造点对齐
              （connect_node → _sync_engine_ports_with_settings）；
            - 规则五项：**下次连接生效**——引擎供给器（task17）每次连接
              现读 settings.json，本窗只存值；
            - 自动探测（立即）/ 数据目录（下次启动）：归后台窗落地——
              自动探测的定时器与启动构造点都在后台窗，这里转交同一份
              settings，保证两窗设置状态不分叉（值只有一份、存储只有一处）。"""
            self.settings = s
            self._sync_engine_ports_with_settings()
            if self._backend_window is not None \
                    and hasattr(self._backend_window, "_apply_saved_settings"):
                self._backend_window._apply_saved_settings(s)
            self._show_info(
                "设置已保存：端口 / 规则与网络下次连接生效；自动探测立即生效、"
                "数据目录下次启动生效（在后台窗落地）。")

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

        # ---------------- 诊断（task22 · task7 欠账接线） ----------------
        def _launch_diag(self, tag: str, fn) -> bool:
            """诊断后台线程通道（app.py `_launch_health` 同口径：不占 _busy）。
            导出是磁盘 IO、一键诊断含本机探测，都绝不进主线程；同一时刻
            只跑一轮（_diag_active 守卫）。返回是否真的起了线程。"""
            if self._diag_active:
                self._show_info("上一轮诊断还没跑完，等它结束后再试。")
                return False
            self._diag_active = True
            worker = _Worker(self.bridge, tag, fn)
            self._workers.append(worker)
            worker.finished.connect(lambda: self._workers.remove(worker))
            worker.start()
            return True

        def _run_doctor(self):
            """菜单「一键诊断」（work7/DESIGN §5.3）：后台线程组装 env +
            run_checks（探测复用 core/health 现成函数、判读在 diag.run_checks
            纯函数），回主线程弹结果框。只呈现结果，不触发任何连接动作。"""
            orch = self.orchestrator
            engines = self.engines

            def _job():
                env = diag_collect.collect_check_env(
                    self.store, engines=engines, orchestrator=orch)
                return diag.render_checks(diag.run_checks(env))

            self._launch_diag("diag_doctor", _job)

        def _export_diag(self):
            """菜单「导出诊断包」（work7/DESIGN §5.3）：主线程选保存路径 →
            后台线程收集材料 + build_bundle → 完成只提示包路径与文件数
            （与 CLI export-diag 同口径，不回显任何节点信息）。"""
            suggested = diag_collect.default_bundle_path()
            path, _filt = QFileDialog.getSaveFileName(
                self, "导出诊断包", suggested, "诊断包 (*.zip);;所有文件 (*)")
            if not path:
                return
            orch = self.orchestrator
            engines = self.engines

            def _job():
                out_path, written = diag_collect.build_diag_bundle(
                    self.store, path, engines=engines, orchestrator=orch)
                return (out_path, len(written))

            self._launch_diag("diag_export", _job)

        def _show_doctor_report(self, text: str):
            """一键诊断结果框（work7/DESIGN §4.2：GUI 弹结果对话框；
            文本与 CLI doctor / 02-checks.txt 同一份 render_checks 口径）。"""
            QMessageBox.information(self, "一键诊断", text)

        # ---------------- ④ 容灾/自动替换（U6/task21） ----------------
        def _setup_failover(self):
            """④容灾控制器装配（work21/WIRING.md 落点③）。

            failover.FailoverController 全部纯逻辑、依赖注入：会话通道 =
            本窗 orchestrator 原样（disconnect/connect，orchestrator
            零改动）；组与总开关每次现读 backup_group.json（与
            sources.json 同目录，改即见）；样本走 history.record
            kind="failover"（latency 恒空，不污染养鱼池延迟统计）；
            告警走本窗 _show_alert + 托盘。开关关时控制器零动作零记录
            （failover.py handle 首检查）。本窗只接线不决策。"""
            store, orch = self.store, self.orchestrator

            def _group():
                return backup_group.load_group(
                    backup_group.backup_group_path(store.path or "client.db"))

            def _last_link_ok(node_id):
                last = history.last_result(store, node_id)
                return last[0] if last else None

            def _update_extra(node_id, extra):
                node = store.get_node(node_id)
                if node is not None:
                    node.extra = dict(extra)
                    store.upsert_node(node)

            self.failover = failover.FailoverController(failover.FailoverOps(
                enabled=lambda: _group().get("enabled", False),
                group_node_ids=lambda: _group().get("node_ids", []),
                get_node=store.get_node,
                last_link_ok=_last_link_ok,
                active_node_id=lambda: orch.active_node_id,
                disconnect=orch.disconnect,
                connect=orch.connect,
                record_sample=lambda node_id, success, detail: history.record(
                    store, node_id, "failover", success, detail=detail),
                update_node_extra=_update_extra,
                alert=self._failover_alert,
            ))

        def _failover_feed(self, reason, node_id):
            """U6 容灾信号入口（work21/WIRING.md 落点④）。

            两路喂入：本窗 _on_session_event 的 dropped 分支（引擎掉线），
            后台窗 _finish_health_probe 的 HEALTH_DEAD 分支（main() 把本
            适配器注入 backend._failover_feed）。suspect 不进任何一路
            （§5.1：不触发替换）。信号只登记，动作经 _run_task 串行通道
            执行——与用户连接同一条管道，绝不另起线程同时碰 orchestrator。"""
            if self._failover_active:
                return          # 单飞：一轮替换进行中，同因信号不叠加
            if self._busy:
                # 用户任务占着 orchestrator 串行通道：暂存待派发，
                # poll_once / 任务收尾后重试，不丢信号。
                self._failover_pending = (reason, node_id)
                return
            self._failover_pending = None
            self._failover_active = True
            ctrl = self.failover
            self._run_task("failover", lambda: ctrl.handle(reason, node_id))

        def _failover_retry_pending(self):
            """暂存容灾信号补派（_busy 清空后由 poll_once / 任务收尾调）。"""
            if self._failover_pending and not self._busy:
                pending = self._failover_pending
                self._failover_pending = None
                self._failover_feed(*pending)

        def _failover_alert(self, payload):
            """容灾告警展示（work21/WIRING.md 落点⑦）：红色告警条 + 托盘
            通知，复用既有通道；文案由 failover.py 生成（§5.3 模板：
            「已按容灾替换：A → B（原因：…）」）。"""
            text = payload.get("text", "")
            self._show_alert(text)
            if self.tray is not None:
                self.tray.showMessage("gate-client 容灾替换", text,
                                      QSystemTrayIcon.Warning, 5000)

        # ---------------- ② 区数据刷新 ----------------
        def refresh_boards(self):
            """②区刷新：整库按 classify_proto.board_of 分板块（判不出维度落
            「未分类」，不许硬塞）+ 板块内 history.sorted_nodes 展示排序。

            已拉黑节点不进任何板块（与后台默认口径一致）；板块是归类不是
            排名，连接永远由用户点（铁律）。"""
            nodes = self.store.list_nodes(include_banned=False)
            # ⑥区/「特殊站」列数据源（task20）：现读清单文件一份口径
            #（主人改清单即见）；special_tests 记录只读节点 extra。
            self._special_enabled = special_sites.enabled_sites(
                self._load_special_sites())
            groups = {b: [] for b in classify_proto.BOARDS}
            groups[classify_proto.UNCLASSIFIED] = []
            for n in nodes:
                board = classify_proto.board_of(classify_proto.dims_of(n.to_dict()))
                groups.setdefault(board, []).append(n)
            for key, pg in self.boards.items():
                page_nodes = history.sorted_nodes(self.store, groups.get(key, []))
                pg.populate(page_nodes, self.orchestrator.active_node_id)
                label = classify_proto.BOARD_LABELS.get(key, "未分类")
                self.tabs.setTabText(self.tabs.indexOf(pg),
                                     f"{label} · {len(page_nodes)}")
            self._last_node_count = self.store.count_nodes()
            self._update_action_states()

        def _selected_node(self):
            page = self.tabs.currentWidget()
            node_id = page.selected_node_id() if isinstance(page, BoardPage) else None
            return self.store.get_node(node_id) if node_id else None

        # ---------------- 会话动作（与现窗同管道） ----------------
        def _run_task(self, tag: str, fn):
            worker = _Worker(self.bridge, tag, fn)
            self._workers.append(worker)
            worker.finished.connect(lambda: self._workers.remove(worker))
            self._busy = True
            self._update_action_states()
            worker.start()

        def connect_selected(self):
            node = self._selected_node()
            if node is None:
                self._show_info("先在板块里选中一个节点，再点连接。")
                return
            self.connect_node(node)

        def connect_from_page(self, page: BoardPage):
            node_id = page.selected_node_id()
            node = self.store.get_node(node_id) if node_id else None
            if node is None:
                self._show_info("先选中一个节点再连接。")
                return
            self.connect_node(node)

        def connect_node(self, node):
            """连接管道（现窗 connect_selected 同口径，app.py:613-637）：
            隔离节点警告不拦 → 健康预检（端口占用=取消；系统代理冲突=只
            告警）→ 接管方式写引擎 → 工作线程 orchestrator.connect。
            连谁永远由用户点，失败停在原地，绝不自动换节点（铁律）。"""
            if self._busy:
                return
            self._clear_alert()
            if node.extra.get("quarantined") == "1":
                # 与 CLI 同口径：警告但不阻断（用户点名即视为知情）
                self._show_alert(
                    f"⚠ 该节点已被标记为污染节点"
                    f"（{node.extra.get('quarantine_reason', '')}），仍按你的点名连接。")
            # 端口改动下次连接生效（task24 §5.3）：先在连接构造点对齐引擎
            # 端口，预检与接管方式都作用于对齐后的引擎。
            self._sync_engine_ports_with_settings()
            if not self._health_precheck(node):
                return
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

        def toggle_pin_from_page(self, page: BoardPage):
            """置顶/取消（work10 §2.1 允许的前台写操作之一）：只影响展示
            排序（history.sort_key 口径），不连接、不切换任何节点。"""
            node_id = page.selected_node_id()
            node = self.store.get_node(node_id) if node_id else None
            if node is not None:
                self.store.set_pinned(node.id, not node.pinned)
                self.refresh_boards()

        def test_from_page(self, page: BoardPage):
            if self._busy:
                return
            node_id = page.selected_node_id()
            if not node_id:
                self._show_info("先选中一个节点再测速。")
                return
            self._run_task("verify", lambda: self._test_node(node_id))

        # ---------------- ②区按板块批量测 + 吞吐实测（task19/U4） ----------
        def batch_test_board(self, page: BoardPage):
            """②区板块级批量测入口：决策（板块分组/载荷门卫）全在 core 的
            board_batch_plan 纯函数，本方法只执行计划。批量管道 = 后台窗
            toggle_batch_test/_run_batch 同款形态（_Worker + "batch" tag +
            _batch_stop 停止旗 + progress 消息协议）——不是第二条管道。
            载荷空槽时给「未配置测速载荷」提示并只测延迟（不静默失败）。
            停止同款：再点一次置 _batch_stop，吞吐实测即时中止（partial）。"""
            if self._batch_active:
                self._batch_stop = True
                self._show_info("正在停止批量测速…")
                return
            if self._busy:
                return
            try:
                payload_url = load_settings().speedtest_payload_url
            except SettingsStoreError as exc:
                self._show_alert(f"设置文件读不了，批量测停在这里：{exc}")
                return
            plan = speedtest.board_batch_plan(
                self.store.list_nodes(include_banned=False), page.board_key,
                payload_url)
            if plan["notice"]:
                self._show_info(plan["notice"])
            if not plan["ids"]:
                return
            label = classify_proto.BOARD_LABELS.get(page.board_key, "未分类")
            self._batch_stop = False
            self._batch_active = True
            self._run_task("batch", lambda: self._run_board_batch(
                plan["ids"], label, plan["measure_speed"], payload_url))

        def _run_board_batch(self, ids, label, measure_speed, payload_url):
            """批量管道主体（后台 _run_batch 同形态，app.py:761-787）:
            逐节点先 _test_node（延迟/出口，fast 口径；临时引擎 10818/10819
            纪律同款），载荷已配置再附带吞吐实测（_throughput_for_node）。
            只展示与回写结果，不连不切任何节点（铁律）；前端不删节点，
            不带后台的废节点清单语义（work10 §2.3 边界清单）。"""
            ok_n = fail_n = skip_n = done = speed_n = 0
            fail_ids, speed_notes = [], []
            total = len(ids)
            for nid in ids:
                if self._batch_stop:
                    break
                node = self.store.get_node(nid)
                name = node.name if node else nid
                self.bridge.progress.emit(f"测速中 {done + 1}/{total}：{name}")
                kind, _text = self._test_node(nid, fast=True)
                done += 1
                if kind == "verify_ok":
                    ok_n += 1
                elif kind == "skip":
                    skip_n += 1
                else:
                    fail_n += 1
                    fail_ids.append(nid)
                if measure_speed and not self._batch_stop:
                    result = self._throughput_for_node(nid, payload_url)
                    if result is not None and result.measured:
                        speed_n += 1
                    elif result is not None:
                        speed_notes.append(
                            f"{name}：吞吐未实测（{result.category}："
                            f"{result.detail}）")
                self.bridge.progress.emit(
                    f"测速中 {done}/{total}：可连 {ok_n} · 不通 {fail_n}"
                    f" · 跳过 {skip_n} · 实测吞吐 {speed_n}")
            return ("batch_done", ok_n, fail_n, skip_n, done, total, label,
                    speed_n, speed_notes)

        def _throughput_for_node(self, node_id: str, payload_url: str,
                                 tier_mb=None):
            """单节点吞吐实测（_test_node 三分支同形态，端口纪律同款——
            未连接走手动组 10818/10819 临时引擎，测完即停，不碰当前会话、
            不碰 10828/10829 自动组）：
            - 正在连着的节点：经活动引擎 HTTP 入站（当前会话）测；
            - sing-box 系未连接：临时引擎测完即停；
            - OpenVPN（会话无 HTTP 入站，直连无法判定走不走隧道）与其余
              形态：如实返回 None 不硬测——宁缺毋假。
            结果经 speedtest.write_back 回写（只有 ok/partial 才写
            speed_mbps + speed_measured_at）并落库。只测与回写，不连不切
            任何节点（铁律）。"""
            node = self.store.get_node(node_id)
            if node is None:
                return None
            tier = tier_mb if tier_mb is not None else speedtest.TIERS_MB[0]
            orch = self.orchestrator
            proxy, temp_engine, handle = None, None, None
            if orch.state != Orchestrator.IDLE and orch.active_node_id == node_id:
                engine = self.engines.get(node.protocol)
                if not isinstance(engine, SingBoxEngine):
                    return None
                proxy = engine.http_proxy_url
            elif node.protocol in SINGBOX_PROTOCOLS:
                temp_engine = self._make_temp_engine()
                try:
                    handle = temp_engine.start(node)
                except EngineError:
                    return None   # 引擎起不来＝连不上口径，放弃本节点吞吐
                proxy = temp_engine.http_proxy_url
            else:
                return None
            try:
                opener = _make_proxy_opener(proxy)
                return speedtest.run_throughput_test(
                    payload_url, opener, tier,
                    abort_fn=lambda: self._batch_stop)
            finally:
                if temp_engine is not None:
                    temp_engine.stop(handle)

        def _refresh_zone5(self):
            """⑤区状态行数据供给：现读 task12 设置一份口径（settings.json
            同一份存储），文案由 speedtest.monitor_status_text 拼（只读
            auto_probe 两字段，不持有第二份状态）。文件损坏如实显示，
            不用默认值盖掉（settings_store §4.2 口径）。"""
            try:
                self.monitor_line.setText(
                    speedtest.monitor_status_text(load_settings()))
            except SettingsStoreError as exc:
                self.monitor_line.setText(f"设置读不出：{exc}")

        # 测速管道（现窗 _test_node/_probe_and_record 逐行同款，app.py:654-724）
        # 临时测速引擎用独立端口，避开活动会话引擎的 7897/7898
        _TEMP_SOCKS_PORT = 10818
        _TEMP_HTTP_PORT = 10819

        def _make_temp_engine(self):
            return SingBoxEngine(socks_port=self._TEMP_SOCKS_PORT,
                                 http_port=self._TEMP_HTTP_PORT)

        def _test_node(self, node_id: str, fast: bool = False, make_engine=None):
            """测一个节点：能不能连、延迟多少、出口是哪（口径同现窗）。
            - 正在连着的节点：经活动引擎的代理测；
            - sing-box 系节点：临时拉一个独立端口的引擎，测完即停，
              不碰当前会话；
            - OpenVPN 未连接：隧道模式没法离线单测，如实 skip、不记假样本。
            结果落 verify 样本（带延迟），②区延迟/稳定性/最近列随之有数。"""
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
            # U6 §5.4：验真成功清「容灾换下」标记（无标记 no-op；恢复与否
            # 由既有验真口径说了算，不由容灾模块自己判断）。
            failover.clear_mark_on_link_success(self.store, node.id, True)
            ep = node.exit_profile
            lat = (f"，延迟 {history.format_latency_ms(outcome.latency_ms)}"
                   if outcome.latency_ms is not None else "")
            return ("verify_ok",
                    f"出口 {ep.ip} / {ep.country}({ep.country_code}) / "
                    f"{NETWORK_LABELS.get(ep.network_type, ep.network_type)}{lat}")

        # ---------------- ⑥ 特殊网站测试（task20 / work10 §9） -----------
        def _load_special_sites(self):
            """现读特殊站清单（⑥区面板与②区「特殊站」列同一份文件口径）。
            损坏/非法：如实提示并按空清单消费（面板侧禁改，修复后 reload
            即见）；本方法不静默改写文件。"""
            try:
                return special_sites.load_sites(self._special_sites_path)
            except ValueError as exc:
                self._show_info(f"特殊站清单读不出：{exc}")
                return ()

        def _special_text_for(self, node):
            """②区行「特殊站」列文案（Σ 通过数/总数 + 失败站徽标，
            special_probe.row_special_text 口径；records 取节点 extra）。"""
            records = node.extra.get(special_probe.SPECIAL_TESTS_KEY)
            return special_probe.row_special_text(records, self._special_enabled)

        def test_special_current(self):
            """⑥区「测当前节点」：测②区当前选中的节点（_run_task 管道，
            单飞由 _busy 守卫）。"""
            if self._busy:
                return
            node = self._selected_node()
            if node is None:
                self._show_info("先在板块里选中一个节点，再测特殊站。")
                return
            if not self._special_enabled:
                self._show_info("特殊站清单为空：先在⑥区添加站点再测。")
                return
            self._run_task("special_test",
                           lambda: self._special_probe_node(node.id))

        def test_special_from_page(self, page: BoardPage):
            """②区行内「测特殊站」（右键菜单动作，task20 挂载点）。"""
            if self._busy:
                return
            node_id = page.selected_node_id()
            if not node_id:
                self._show_info("先选中一个节点再测特殊站。")
                return
            if not self._special_enabled:
                self._show_info("特殊站清单为空：先在⑥区添加站点再测。")
                return
            self._run_task("special_test",
                           lambda: self._special_probe_node(node_id))

        def test_special_board_current(self):
            """⑥区「测本板块特殊站」：批量触发（复用批量测速管道形态——
            _Worker + tag + 停止旗 + progress 消息；临时引擎端口纪律同
            _test_node 的 10818/10819 口径不变）。进行中再点一次=停止。"""
            if self._special_batch_active:
                self._special_stop = True
                self._show_info("正在停止特殊站批量测…")
                return
            if self._busy:
                return
            page = self.tabs.currentWidget()
            ids = (page.visible_node_ids()
                   if isinstance(page, BoardPage) else [])
            if not ids:
                self._show_info("本板块没有可测的节点。")
                return
            if not self._special_enabled:
                self._show_info("特殊站清单为空：先在⑥区添加站点再测。")
                return
            self._special_stop = False
            self._special_batch_active = True
            self._run_task("special_batch",
                           lambda: self._run_special_batch(ids))

        def _special_actions_for(self, node):
            """⑥区探测的三件注入动作 + 收尾函数（真实网络胶水只在 UI 层，
            core/special_probe 不碰网络）。引擎纪律与 _throughput_for_node
            同款：正在连着的节点经活动引擎 HTTP 入站（当前会话出口）；
            sing-box 系未连接走临时引擎（10818/10819），测完即停；
            OpenVPN 隧道模式未连接测不了，如实 None（宁缺毋假）。
            返回 (resolve, connect, fetch, cleanup) 或 None。"""
            orch = self.orchestrator
            proxy, temp_engine, handle = None, None, None
            if orch.state != Orchestrator.IDLE \
                    and orch.active_node_id == node.id:
                engine = self.engines.get(node.protocol)
                if not isinstance(engine, SingBoxEngine):
                    return None
                proxy = engine.http_proxy_url
            elif node.protocol in SINGBOX_PROTOCOLS:
                temp_engine = self._make_temp_engine()
                try:
                    handle = temp_engine.start(node)
                except EngineError:
                    return None   # 引擎起不来＝连不上口径，放弃本节点
                proxy = temp_engine.http_proxy_url
            else:
                return None
            resolve, connect, fetch = make_site_actions(proxy)

            def _cleanup():
                if temp_engine is not None and handle is not None:
                    temp_engine.stop(handle)

            return resolve, connect, fetch, _cleanup

        def _special_probe_node(self, node_id):
            """对单节点跑一轮特殊站分级测试（worker 线程上下文）。
            结果只写节点 extra["special_tests"]（special_probe
            .write_special_tests 口径：{domain: {level, code, ts}}，新
            结果覆盖同域名旧结果）并 upsert；**不进 verify_samples、不记
            历史样本、不触发任何换节点/拉黑**（铁律；拉黑归后台人工）。
            返回 (kind, text)：special_done / special_skip /
            special_failed。"""
            node = self.store.get_node(node_id)
            if node is None:
                return ("special_failed", "节点不存在")
            sites = self._special_enabled
            actions = self._special_actions_for(node)
            if actions is None:
                return ("special_skip",
                        "OpenVPN 节点是隧道模式，未连接时测不了特殊站——"
                        "先连接它（连接流程不会自动测特殊站）。")
            resolve, connect, fetch, cleanup = actions
            try:
                results = [special_probe.probe_site(s["domain"],
                                                    resolve=resolve,
                                                    connect=connect,
                                                    fetch=fetch)
                           for s in sites]
            finally:
                cleanup()
            special_probe.write_special_tests(node, results)
            self.store.upsert_node(node)
            records = node.extra.get(special_probe.SPECIAL_TESTS_KEY) or {}
            return ("special_done", special_probe.summary_text(records, sites))

        def _run_special_batch(self, ids):
            """特殊站批量主体（_run_board_batch 同形态）：逐节点
            _special_probe_node，progress 消息逐节点上报，停止旗随时生效。
            只测与回写，不连不切任何节点（铁律）。"""
            done = ok_n = skip_n = fail_n = 0
            notes = []
            total = len(ids)
            for nid in ids:
                if self._special_stop:
                    break
                node = self.store.get_node(nid)
                name = node.name if node else nid
                self.bridge.progress.emit(
                    f"特殊站测试 {done + 1}/{total}：{name}")
                kind, text = self._special_probe_node(nid)
                done += 1
                if kind == "special_done":
                    ok_n += 1
                elif kind == "special_skip":
                    skip_n += 1
                else:
                    fail_n += 1
                notes.append(f"{name}：{text}")
                self.bridge.progress.emit(
                    f"特殊站测试 {done}/{total}：完成 {ok_n} · 跳过 "
                    f"{skip_n} · 失败 {fail_n}")
            return ("special_batch_done", ok_n, skip_n, fail_n, done,
                    total, notes)

        # ---------------- 连接健康预检（现窗三件套文案，app.py:960-1041） -----
        def _takeover_mode(self) -> str:
            """当前接管方式：sing-box 引擎实例属性为准。无 sing-box 引擎时
            默认 proxy（与现窗同口径）。"""
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
            """检测②冲突文案：TUN 模式换装（task6/DESIGN §6.2），与现窗同款。"""
            if self._takeover_mode() == "tun":
                return (f"TUN 已接管全机流量，但{reason}——走系统代理的"
                        "应用流量到不了隧道。客户端不会改你的系统代理。")
            return reason

        def _health_precheck(self, node) -> bool:
            """连接前预检（检测①+②，与现窗同款）：False=取消本次连接。
            ①引擎端口被占 → 告警条并取消；②系统代理冲突 → 告警条但照常
            连接（用户点名优先，只提示不拦）。OpenVPN 是 TUN 模式、不占
            7897/7898：只查②。"""
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
            return True

        def _startup_health_check(self):
            """开窗体检（task5，与现窗同款）：端口占用 + 系统代理冲突，
            只提示不拦截。"""
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

        # ---------------- 巡检与对账 ----------------
        def poll_once(self):
            """巡检（5s，与现窗同节奏）：掉线由 orchestrator.poll 上报
            dropped 事件，绝不自动切换（铁律在 orchestrator）。本窗另做两件
            对账：①区状态以 orchestrator 为准自愈（漏事件兜底）；②区在
            库计数变化时重排（后台入库/删除后本窗即见，同一 SQLite）。"""
            if not self._busy:
                self.orchestrator.poll()
                # U6：_busy 清空后补派暂存的容灾信号（如有）。
                self._failover_retry_pending()
            if self.orchestrator.state == Orchestrator.CONNECTED:
                if self._session_state != ST_CONNECTED:
                    self._set_session_state(ST_CONNECTED)
                    self._apply_node_detail(self.orchestrator.active_node_id)
            elif (self.orchestrator.state == Orchestrator.IDLE
                  and self._session_state == ST_CONNECTED):
                self._set_session_state(ST_IDLE)
                self._clear_node_detail()
            if self.store.count_nodes() != self._last_node_count:
                self.refresh_boards()
            self._refresh_zone5()   # ⑤区状态行现读设置（task19，改即见）

        # ---------------- ① 区状态机 ----------------
        def _set_session_state(self, state: str):
            self._session_state = state
            self.session_panel.set_state(state)
            self._update_tray_state()
            self._update_action_states()

        def _apply_node_detail(self, node_id: str):
            """①区当前节点行：实测名（exit_display_name 口径）+ 板块徽标
            （classify_proto）+ 场景徽标（SCENE_LABELS）+ 延迟
            （history.format_latency_ms 口径，养鱼池中位）。"""
            node = self.store.get_node(node_id) if node_id else None
            if node is None:
                self._clear_node_detail()
                return
            ep = node.exit_profile
            name = exit_display_name(ep) if ep.ip else node.name
            board = classify_proto.board_of(classify_proto.dims_of(node.to_dict()))
            board_label = classify_proto.BOARD_LABELS.get(board, "未分类")
            scenes = "/".join(SCENE_LABELS.get(s, s) for s in sorted(node.scenes)) or "无场景"
            stats = history.stats_for(self.store, node.id)
            self.node_detail.setText(
                f"当前节点：{name}　[板块：{board_label}]　[场景：{scenes}]　"
                f"延迟：{history.format_latency_ms(stats.median_latency_ms)}")
            self._apply_speed_row(node.id)

        def _clear_node_detail(self):
            self.node_detail.setText("")
            self._apply_speed_row(None)

        def _apply_speed_row(self, node_id):
            """①区速度行数据供给（task19）：当前节点延迟（history 中位，
            既有探测值）+ 最近吞吐实测/自报三态
            （speedtest.format_speed_display）。只读最近实测，不触发任何
            测速（不常驻跑吞吐）；无节点清空。"""
            node = self.store.get_node(node_id) if node_id else None
            if node is None:
                self.speed_line.setText("")
                return
            stats = history.stats_for(self.store, node.id)
            self.speed_line.setText(
                f"速度：{speedtest.format_speed_display(node)}　"
                f"延迟：{history.format_latency_ms(stats.median_latency_ms)}")

        # ---------------- 会话事件（七个 kind 每个都有分支，work14 §5.1-2） --
        def _on_session_event(self, ev):
            if ev.kind == "connect_started":
                node = self.store.get_node(ev.node_id)
                name = node.name if node else ev.node_id
                self._set_session_state(ST_CONNECTING)
                self._show_info(f"正在连接 {name}…（连接中，等验出口结果）")
            elif ev.kind == "engine_started":
                self._show_info("引擎已启动，正在验真实出口…")
            elif ev.kind == "connected":
                self._set_session_state(ST_CONNECTED)
                self._apply_node_detail(ev.node_id)
                self._clear_alert()
                # U6 §5.4：连接成功（含容灾替换选中的新节点）= 验出口通过，
                # 清掉残留的「容灾换下」标记（clear_mark_on_link_success
                # 只动 failover_* 两键，对 banned/quarantined 只读不写）。
                failover.clear_mark_on_link_success(self.store, ev.node_id, True)
            elif ev.kind == "verify_failed":
                self._set_session_state(ST_DROPPED)
                self._show_alert(f"假连已断开：{ev.detail}")
                self._clear_node_detail()
            elif ev.kind == "dropped":
                self._set_session_state(ST_DROPPED)
                self._show_alert(f"节点掉线：{ev.detail}")
                self._clear_node_detail()
                if self.tray is not None:
                    self.tray.showMessage("gate-client 节点掉线", ev.detail,
                                          QSystemTrayIcon.Warning, 5000)
                # U6 容灾触发（work21/WIRING.md 落点⑤）：引擎掉线喂给容灾
                # 控制器（开关关时零动作零记录）。
                self._failover_feed(failover.REASON_ENGINE_DROPPED, ev.node_id)
            elif ev.kind == "error":
                self._set_session_state(ST_DROPPED)
                self._show_alert(f"连接失败：{ev.detail}")
                self._clear_node_detail()
            elif ev.kind == "disconnected":
                self._set_session_state(ST_IDLE)
                self._clear_node_detail()
            self.refresh_boards()
            self._update_action_states()

        def _on_task_finished(self, tag: str, result):
            if tag in ("diag_doctor", "diag_export"):
                # 诊断线程不占 _busy（同 health_probe 口径），只复位自己的旗标
                self._diag_active = False
                ok, payload = result
                if not ok:
                    what = "一键诊断" if tag == "diag_doctor" else "导出诊断包"
                    self._show_alert(f"{what}失败：{payload}")
                elif tag == "diag_doctor":
                    self._show_doctor_report(payload)
                else:
                    # 输出红线（与 CLI export-diag 同口径）：只提示包路径与
                    # 文件数，不回显任何节点信息。
                    out_path, n_files = payload
                    self._show_info(f"诊断包已导出：{out_path}"
                                    f"（{n_files} 个文件）——包内容已脱敏，"
                                    "可交给支持方")
                self._update_action_states()
                return
            self._busy = False
            if tag == "failover":
                # U6 容灾替换收尾（work21/WIRING.md 落点⑥）：复位单飞旗；
                # 告警已由控制器经 _failover_alert 出过，回执不重复弹；
                # 控制器异常走下方通用「操作失败」告警。
                self._failover_active = False
            if tag == "batch":
                self._batch_active = False   # 批量收尾（旗标复位，后台同款）
            if tag == "special_batch":
                self._special_batch_active = False   # ⑥区批量收尾（同款）
            ok, payload = result
            if not ok:
                if isinstance(payload, OrchestratorError):
                    self._show_alert(str(payload))
                else:
                    self._show_alert(f"操作失败：{payload}")
            elif tag == "failover":
                # U6：正常回执后补派暂存信号（如有）。
                self._failover_retry_pending()
            elif tag == "batch" and isinstance(payload, tuple):
                (_kind, ok_n, fail_n, skip_n, done, total, label,
                 speed_n, speed_notes) = payload
                stopped = "（已手动停止）" if done < total else ""
                msg = (f"批量测速完成（{label}）{stopped}：可连 {ok_n} · "
                       f"不通 {fail_n} · 跳过 {skip_n}"
                       f"（共测 {done}/{total}）· 吞吐实测 {speed_n}")
                for note in speed_notes[:3]:
                    msg += f"\n· {note}"
                self._show_info(msg)
            elif tag == "verify" and isinstance(payload, tuple):
                kind, text = payload
                if kind == "verify_ok":
                    self._show_info(f"测速通过：{text}")
                elif kind == "skip":
                    self._show_info(text)
                else:
                    self._show_alert(f"测速失败：{text}")
            elif tag == "special_test" and isinstance(payload, tuple):
                kind, text = payload
                if kind == "special_failed":
                    self._show_alert(f"特殊站测试失败：{text}")
                    self.special_panel.set_result(f"测试失败：{text}",
                                                  error=True)
                elif kind == "special_skip":
                    self._show_info(text)
                    self.special_panel.set_result(text)
                else:
                    self._show_info(f"特殊站测试完成：{text}")
                    self.special_panel.set_result(text)
            elif tag == "special_batch" and isinstance(payload, tuple):
                (_kind, ok_n, skip_n, fail_n, done, total, notes) = payload
                stopped = "（已手动停止）" if done < total else ""
                msg = (f"特殊站批量完成{stopped}：完成 {ok_n} · 跳过 "
                       f"{skip_n} · 失败 {fail_n}（共测 {done}/{total}）")
                for note in notes[:3]:
                    msg += f"\n· {note}"
                self._show_info(msg)
                self.special_panel.set_result(msg.splitlines()[0])
            self.refresh_boards()
            self._apply_speed_row(self.orchestrator.active_node_id)
            self._update_action_states()

        # ---------------- 托盘（自现窗 app.py:530-580 迁移，归主窗） ---------
        def _setup_tray(self):
            self.tray = None
            if not QSystemTrayIcon.isSystemTrayAvailable():
                return
            self.tray = QSystemTrayIcon(self)
            menu = QMenu()
            show_act = QAction("显示主窗", self)
            show_act.triggered.connect(self._show_window)
            menu.addAction(show_act)
            back_act = QAction("打开后台", self)
            back_act.triggered.connect(self._open_backend)
            menu.addAction(back_act)
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
            self._update_tray_state()
            self.tray.show()

        def _update_tray_state(self):
            """托盘圆点：已连接绿色，其余灰色（现窗 _update_tray 口径，
            状态字取①区四态）。"""
            if self.tray is None:
                return
            color = "#2e7d32" if self._session_state == ST_CONNECTED else "#9e9e9e"
            self.tray.setIcon(_dot_icon(color))
            self.tray.setToolTip(f"gate-client：{self._session_state}")

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
            # work14 §5.2 的「前台关窗=真退出或另定」待主人裁定，裁定后
            # 只改本方法（见 work16/DESIGN.md §6）。
            if self.tray is not None:
                event.ignore()
                self.hide()
                self.tray.showMessage(
                    "gate-client", "已最小化到托盘，连接保持不变。",
                    QSystemTrayIcon.Information, 3000)
            else:
                super().closeEvent(event)

        # ---------------- 小件（与现窗同名同调用面，app.py:1261-1270） -------
        def _show_alert(self, text: str):
            self.alert_bar.setText(text)
            self.alert_bar.setVisible(True)

        def _clear_alert(self):
            self.alert_bar.setVisible(False)
            self.alert_bar.setText("")

        def _show_info(self, text: str):
            """过程消息 → 消息行（不写连接状态——work14 §5.1-1 的前台分工）。"""
            self.message_line.setText(text)

        def _update_action_states(self):
            connected = self.orchestrator.state != Orchestrator.IDLE
            has_sel = self._selected_node() is not None
            self.connect_btn.setEnabled(has_sel and not connected and not self._busy)
            # 接管方式只在未连接时可切（同现窗 app.py:1278-1283）：连接中
            # 禁用，tooltip 说明先断开再改。
            self.mode_combo.setEnabled(not connected and not self._busy)
            self.mode_combo.setToolTip(
                "先断开，再改接管方式" if connected else
                "接管方式：只在未连接时可切换，切换后下次连接生效。"
                "TUN 接管全机流量，首次开启会弹系统授权框；提权启动器待"
                "真机实现，当前版本选 TUN 点连接会显式报错。")
            self.disconnect_btn.setEnabled(connected and not self._busy)
            # 板块级批量测按钮（task19）：批量进行中＝停止测速，其余随 _busy
            batch_text = "停止测速" if self._batch_active else "测本板块"
            batch_enabled = True if self._batch_active else not self._busy
            for pg in self.boards.values():
                pg.batch_btn.setText(batch_text)
                pg.batch_btn.setEnabled(batch_enabled)
            # ⑥区测试按钮（task20）：单测随 _busy；批量进行中面板板块
            # 按钮兼做「停止」（同 batch_btn 的停止语义）。
            self.special_panel.set_busy(self._busy,
                                        stopping=self._special_batch_active)

    def main(argv=None) -> int:
        parser = argparse.ArgumentParser(prog="gate-client-usage",
                                         description="自研代理客户端（使用界面）")
        # --db 不给默认值：留 None 按 §8-3 四档优先级解析（task24，同 app.main）
        parser.add_argument("--db", default=None,
                            help="本地数据库路径（默认按 统一设置 > "
                                 "GATE_CLIENT_DB 环境变量 > 内置默认 "
                                 "~/.local/share/gate-client/client.db 解析）")
        args = parser.parse_args(argv)
        app = QApplication(sys.argv)
        app.setApplicationName("gate-client")
        app.setOrganizationName("gate-client")
        app.setStyle("Fusion")
        app.setStyleSheet(DARK_QSS)
        app.setQuitOnLastWindowClosed(False)
        # —— 统一设置：加载 + 首次初始化 + QSettings 迁移（task24 §4/§8-6）——
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
            # §4.2：损坏 / 未知键 / 非法值 → 弹窗给文件路径 + 「重置为默认」
            choice = QMessageBox.warning(
                None, "设置文件有问题",
                f"{exc}\n\n文件：{sfile}\n\n"
                "是否重置为默认值？（原文件会改名留档，不会删除）",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if choice != QMessageBox.Yes:
                return 2
            settings = sstore.reset(tag="corrupt")
        store = Store(resolve_db_path(explicit_db=args.db, env_db=env_db,
                                      settings=settings))
        window = UsageWindow(store, settings=settings, settings_store=sstore)
        window.show()
        return app.exec()

else:

    def main(argv=None) -> int:  # pragma: no cover
        print(INSTALL_HINT, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
