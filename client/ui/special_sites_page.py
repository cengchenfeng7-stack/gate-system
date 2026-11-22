#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
⑥特殊网站测试区（ui/special_sites_page.py）—— U5：落 UsageWindow 主窗
========================================================================
task20 · 阶段五·使用界面线 第六段。work10/DESIGN.md §2.2 ⑥区定稿的
控件面：清单表（可增删条目 + enabled 勾选）+「测当前节点」按钮 +
「测本板块特殊站」批量入口 + 结果状态行。

职责边界（§9.1）：本面板是消费侧测试工具的增删入口，不是来源管理；
清单文件（special_sites.json）的唯一读写口径在 core/special_sites.py
（校验/原子写/缺文件空清单），本面板只调它，不另造第二套规则；真实
清单由主人自己在界面里维护（属数据不属代码）——本模块与测试只用
example.com 类假域名做占位提示，不放任何真实站点。

分级红线（§9.2）随手写在常驻说明里：L2 全过只标「登录页可达」；
L3 要登录会话/图形验证，客户端判不了，永远人工；测出风控命中只呈现，
不触发任何换节点/拉黑动作（永不自动切换节点——铁律）；结果只贴节点
extra["special_tests"]，不进 verify_samples 测速样本。

真实网络胶水（make_site_actions）只活在 UI 层：core/special_probe
的三个探测动作在这里接到真实网络——经给定代理（隧道出口）单次请求；
fetch 复用 core/verify 的 _opener/_fetch（与既有 muse 探测完全同一
网络行为口径：同 UA 策略、不跟随重定向、单次不重试），本模块不新增
任何请求头/重试/指纹手法。core 侧不碰网络的纪律不受影响。

集成（DESIGN.md §2 接线清单）：UsageWindow.__init__ 在⑤区之后构造并
addWidget；面板的测试回调由本窗注入（test_special_current /
test_special_board_current）；本模块不 import .app（避免循环导入），
样式随应用级 DARK_QSS。缺 PySide6 时模块可安全导入（面板定义降级），
core 侧离线测试不受影响。

铁律对齐：本面板没有任何节点选择/切换/优选控件——测试按钮只「测与
回写」，连接永远由用户在②区点。
"""

from __future__ import annotations

from ..core import special_sites

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import (
        QAbstractItemView, QFrame, QHBoxLayout, QHeaderView,
        QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
        QVBoxLayout,
    )
    _QT_OK = True
except ImportError:  # pragma: no cover - 无 Qt 环境时的降级路径
    _QT_OK = False

_STATUS_ERR_STYLE = "color:#ff6b6b;padding:1px 0;"
_STATUS_OK_STYLE = "color:#8fd694;padding:1px 0;"

# 常驻说明（§9.1/§9.2 口径；文案过禁区词与禁词——test 锁定）
PANEL_NOTE = (
    "分级：L0 链路 → L1 可打开 → L2 登录页真实；L2 全过只标「登录页可达」。"
    "L3 要登录会话/图形验证才能确认，客户端判不了，永远人工。\n"
    "只测「这个出口被不被接受」：单次请求、不重试、不伪造指纹、不解验证；"
    "结果只贴在节点记录上，不进测速样本、不会切换任何节点。\n"
    "清单只存类别与公开域名（裸域名，不带路径），由你自己维护。"
)

_CONNECT_TIMEOUT_S = 8.0    # 直连口径的 TCP 探测超时（经代理时真实关卡在 fetch）


def make_site_actions(proxy_url):
    """⑥区真实探测胶水（UI 层专用；core/special_probe 不碰网络）。

    返回 (resolve, connect, fetch) 三件注入动作：
    - proxy_url 非空（经隧道/临时引擎测）：DNS/TCP 由远端出口完成，本地
      无从分辨——resolve/connect 给直通透传，真实关卡是 fetch（经代理的
      单次请求，失败按码 0 归 L0 计）；
    - proxy_url 为空（直连口径，wiring 备用）：resolve 走 getaddrinfo
      只解析不连；connect 做一次 TCP 连接（443）。
    fetch 复用 core/verify 的 _opener/_fetch（同 UA 策略、不跟随重定向、
    单次不重试）——本模块不新增任何请求头/重试/指纹手法。
    """
    from ..core import verify   # 懒加载：保持面板可独立导入（无 Qt 也能载入本模块）

    if proxy_url:
        def resolve(domain):
            return domain            # 远端解析，本地透传

        def connect(_addr):
            return True              # 真实关卡在 fetch

        opener = verify._opener(proxy_url)

        def fetch(url):
            return verify._fetch(opener, url, timeout=25)

        return resolve, connect, fetch

    import socket

    def resolve(domain):
        try:
            infos = socket.getaddrinfo(domain, 443, proto=socket.IPPROTO_TCP)
            return infos[0][4][0] if infos else None
        except Exception:
            return None

    def connect(addr):
        try:
            with socket.create_connection((addr, 443),
                                          timeout=_CONNECT_TIMEOUT_S):
                return True
        except Exception:
            return False

    opener = verify._opener(None)

    def fetch(url):
        return verify._fetch(opener, url, timeout=25)

    return resolve, connect, fetch


if _QT_OK:

    class SpecialSitesPanel(QFrame):
        """⑥特殊网站测试区面板：清单管理（增删/启用勾选）+ 测试触发 +
        状态行。清单状态永远以文件为准（构造/每次改动后 reload），面板
        不持有第二份可编辑副本。"""

        COLUMNS = ("名称", "域名", "类别", "启用")
        COL_NAME, COL_DOMAIN, COL_CATEGORY, COL_ENABLED = range(4)

        def __init__(self, sites_path=None, on_test_current=None,
                     on_test_board=None, parent=None):
            super().__init__(parent)
            self._path = sites_path    # None = 内置默认目录（special_sites 口径）
            self._on_test_current = on_test_current
            self._on_test_board = on_test_board
            self._loading = False      # 程序性回填期间压制 itemChanged
            self._entries = ()         # 只读缓存（测试/审计对照；写入走文件）
            self._controls_ok = False  # 清单可读才允许编辑（损坏禁改）
            self.setFrameStyle(QFrame.StyledPanel)

            root = QVBoxLayout(self)
            root.setContentsMargins(10, 6, 10, 6)

            title = QLabel("⑥ 特殊网站测试")
            title.setStyleSheet("font-weight:bold;padding:1px 0;")
            root.addWidget(title)

            self.note_label = QLabel(PANEL_NOTE)
            self.note_label.setWordWrap(True)
            self.note_label.setStyleSheet("color:#9a9ca6;")
            root.addWidget(self.note_label)

            # 清单表（四列；启用列 = 勾选框；表不可直接编辑文本）
            self.table = QTableWidget(0, len(self.COLUMNS))
            self.table.setHorizontalHeaderLabels(self.COLUMNS)
            self.table.setSelectionBehavior(QTableWidget.SelectRows)
            self.table.setSelectionMode(QAbstractItemView.SingleSelection)
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            header = self.table.horizontalHeader()
            header.setSectionResizeMode(self.COL_NAME, QHeaderView.Stretch)
            for col in (self.COL_DOMAIN, self.COL_CATEGORY, self.COL_ENABLED):
                header.setSectionResizeMode(col, QHeaderView.ResizeToContents)
            self.table.itemChanged.connect(self._on_item_changed)
            root.addWidget(self.table, 1)

            # 清单编辑行：名称 / 域名 / 类别（占位只用假域名示例）
            edit_row = QHBoxLayout()
            self.name_edit = QLineEdit()
            self.name_edit.setPlaceholderText("名称（例：ExampleAI）")
            self.domain_edit = QLineEdit()
            self.domain_edit.setPlaceholderText("裸域名（例：example.com）")
            self.category_edit = QLineEdit()
            self.category_edit.setPlaceholderText("类别（例：ai，可空）")
            self.add_btn = QPushButton("添加")
            self.add_btn.clicked.connect(self._on_add)
            self.remove_btn = QPushButton("移除选中")
            self.remove_btn.clicked.connect(self._on_remove)
            for w in (self.name_edit, self.domain_edit, self.category_edit):
                edit_row.addWidget(w)
            edit_row.addWidget(self.add_btn)
            edit_row.addWidget(self.remove_btn)
            root.addLayout(edit_row)

            # 测试触发行（动作本体在 UsageWindow 接线方法里；面板只发信号）
            test_row = QHBoxLayout()
            self.test_btn = QPushButton("测当前节点")
            self.test_btn.setToolTip(
                "对②区当前选中的节点测一遍特殊站分级（经隧道出口单次请求）。"
                "只测与回写，不连不切节点。")
            self.test_btn.clicked.connect(self._emit_test_current)
            self.board_btn = QPushButton("测本板块特殊站")
            self.board_btn.setToolTip(
                "对当前板块页里的节点逐个测特殊站（复用批量测速管道形态与"
                "临时引擎端口纪律）。只测与回写，不连不切节点；再点一次停止。")
            self.board_btn.clicked.connect(self._emit_test_board)
            test_row.addWidget(self.test_btn)
            test_row.addWidget(self.board_btn)
            test_row.addStretch(1)
            root.addLayout(test_row)

            # 状态行（保存回执/校验红字/测试总结；与①区连接状态、消息行互不相干）
            self.status_label = QLabel("")
            self.status_label.setWordWrap(True)
            root.addWidget(self.status_label)

            self.reload()

        # ---------------- 清单加载与表格回填 ----------------
        def reload(self):
            """从文件重载清单并回填表格（构造时与每次改动后调用）。"""
            self._loading = True
            try:
                try:
                    self._entries = special_sites.load_sites(self._path)
                except ValueError as exc:
                    # 损坏/非法：如实红字（含路径），表格清空并禁改——
                    # 不静默当空清单、不做会覆盖原始证据的保存
                    #（site_blocklist/task12 §4.2 同款立场）。
                    self._entries = ()
                    self._fill_table()
                    self._set_status(f"特殊站清单读不了，⑥区暂不可改：{exc}",
                                     error=True)
                    self._set_controls_enabled(False)
                    return
                self._fill_table()
                self._set_controls_enabled(True)
                self._set_status("")
            finally:
                self._loading = False

        def _fill_table(self):
            self.table.setRowCount(0)
            for entry in self._entries:
                row = self.table.rowCount()
                self.table.insertRow(row)
                for col, text in (
                        (self.COL_NAME, entry["name"]),
                        (self.COL_DOMAIN, entry["domain"]),
                        (self.COL_CATEGORY, entry["category"] or "（无类别）")):
                    item = QTableWidgetItem(str(text))
                    item.setData(Qt.UserRole, entry["domain"])
                    self.table.setItem(row, col, item)
                chk = QTableWidgetItem()
                chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled
                             | Qt.ItemIsSelectable)
                chk.setCheckState(Qt.Checked if entry["enabled"]
                                  else Qt.Unchecked)
                chk.setData(Qt.UserRole, entry["domain"])
                self.table.setItem(row, self.COL_ENABLED, chk)

        def _set_controls_enabled(self, enabled: bool):
            self._controls_ok = enabled
            for w in (self.name_edit, self.domain_edit, self.category_edit,
                      self.add_btn, self.remove_btn, self.table):
                w.setEnabled(enabled)
            self._refresh_test_buttons()

        def _set_status(self, text: str, error: bool = False):
            self.status_label.setText(text)
            self.status_label.setStyleSheet(
                _STATUS_ERR_STYLE if error else _STATUS_OK_STYLE)

        # ---------------- 改动管道：改清单 → 校验 → 原子写 → reload --------
        def _apply_entries(self, entries, ok_msg: str) -> bool:
            """写一份新清单。校验/写盘失败 → 红字 + reload 回退（文件
            仍是对的，界面不谎报已保存）；成功 → 文件是唯一事实源。"""
            try:
                special_sites.save_sites(entries, self._path)
            except (ValueError, OSError) as exc:
                self._set_status(f"未保存：{exc}", error=True)
                self.reload()
                return False
            self.reload()
            self._set_status(ok_msg)
            return True

        def _selected_domain(self):
            row = self.table.currentRow()
            if row < 0:
                return None
            item = self.table.item(row, self.COL_DOMAIN)
            return item.data(Qt.UserRole) if item else None

        def _on_add(self):
            if self._loading or not self._controls_ok:
                return
            try:
                entries = special_sites.add_site(
                    self._entries,
                    self.name_edit.text().strip(),
                    self.domain_edit.text().strip(),
                    self.category_edit.text().strip(),
                    enabled=True)
            except ValueError as exc:
                self._set_status(f"未添加（校验不过）：{exc}", error=True)
                return
            if self._apply_entries(entries, f"已添加：{entries[-1]['domain']}"):
                for w in (self.name_edit, self.domain_edit,
                          self.category_edit):
                    w.clear()

        def _on_remove(self):
            if self._loading or not self._controls_ok:
                return
            domain = self._selected_domain()
            if not domain:
                self._set_status("先在清单里选中一行再移除。", error=True)
                return
            try:
                entries = special_sites.remove_site(self._entries, domain)
            except ValueError as exc:
                self._set_status(f"未移除：{exc}", error=True)
                return
            self._apply_entries(entries, f"已移除：{domain}")

        def _on_item_changed(self, item):
            if self._loading or not self._controls_ok:
                return
            if item.column() != self.COL_ENABLED:
                return
            domain = item.data(Qt.UserRole)
            enabled = item.checkState() == Qt.Checked
            try:
                entries = special_sites.set_enabled(self._entries, domain,
                                                    enabled)
            except ValueError as exc:
                self._set_status(f"未保存：{exc}", error=True)
                self.reload()
                return
            self._apply_entries(entries, f"已保存：{domain} "
                                         f"{'启用' if enabled else '停用'}")

        # ---------------- 测试触发与状态出口 ----------------
        def _emit_test_current(self):
            if self._on_test_current is None:
                self._set_status("测试动作未接线（预览模式）。", error=True)
                return
            self._on_test_current()

        def _emit_test_board(self):
            if self._on_test_board is None:
                self._set_status("测试动作未接线（预览模式）。", error=True)
                return
            self._on_test_board()

        def set_busy(self, busy: bool, stopping: bool = False):
            """批量/单测进行中的按钮状态（UsageWindow._update_action_states
            接线）。stopping=True 时板块按钮兼做「停止」。"""
            self._busy_state = bool(busy)
            self._stopping_state = bool(stopping)
            self._refresh_test_buttons()

        def _refresh_test_buttons(self):
            if not hasattr(self, "test_btn"):
                return
            busy = getattr(self, "_busy_state", False)
            stopping = getattr(self, "_stopping_state", False)
            self.test_btn.setEnabled(self._controls_ok and not busy)
            self.board_btn.setText("停止特殊站批量" if stopping
                                   else "测本板块特殊站")
            self.board_btn.setEnabled(self._controls_ok
                                      and (stopping or not busy))

        def set_result(self, text: str, error: bool = False):
            """测试总结/失败原因写⑥区状态行（UsageWindow 接线）。"""
            self._set_status(text, error=error)

        # ---------------- 测试/审计出口 ----------------
        def current_entries(self) -> tuple:
            """当前清单（load_sites 的只读缓存）——仅供测试/审计对照文件，
            不是第二编辑副本：写入永远走 _apply_entries 的文件管道。"""
            return self._entries
