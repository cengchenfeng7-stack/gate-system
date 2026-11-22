#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task29 原型主窗（proto/proto_app.py）：三个演示页组装 + offscreen 截图自检。

运行（正常显示）:
  python proto/proto_app.py
运行（offscreen 截图，供无显示器环境自检）:
  QT_QPA_PLATFORM=offscreen python proto/proto_app.py --shot <目录>
运行（offscreen 行为自检，无截图，退出码 0=全过）:
  QT_QPA_PLATFORM=offscreen python proto/proto_app.py --selfcheck

不接真实 Store/引擎；不 import client 包；不用模态对话框；数据全虚构
（见 fake_data.py 红线）。

演示页（DESIGN.md §6）：
- 组一：前台窗①②区改版——四通道并存不互冲、分档可关闭告警条、标记列/
  活动行高亮/空值「—」/最近列带日期/页脚批量摘要槽、延迟只出现一次。
- 组二：分区折叠与④区收起态——③④⑤⑥折叠分区（收起=标题行摘要）、
  ②区吃满余宽。
- 组三：行内确认条与可发现性——G15 演示形态（不裁决）、搜索/排序、
  ⑥区批量按钮动态带板块名。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# 集成适配（work29 原件 → client-dev/proto/polish2/ 落位）：
# 原件 parent.parent 指向 work29/（其 proto/ 与 work14 的 proto/ 同名文件
# 各自独立）；落位后 proto/ 下已有 work14 原型同名文件（fake_data.py /
# proto_widgets.py），parent.parent 会误引 work14 的文件。改为 parent
#（polish2/ 自身），import fake_data / proto_widgets 只认本轮第二轮原型。
_PARENT = str(Path(__file__).resolve().parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from PySide6.QtWidgets import (
    QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton,
    QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

import fake_data
from proto_widgets import (ACTIVE_ROW_BG, AlertBar, BoardTable, ConfirmBar,
                           CollapsibleSection, LEVEL_AMBER, LEVEL_RED,
                           SessionPanel)

PROTO_QSS = """
QWidget { background:#1b1c20; color:#e8e8ee; font-size:13px; }
QLabel { background: transparent; }
QLabel#statusText { font-size:15px; font-weight:bold; }
QFrame#statusPanel { background:#23252b; border:1px solid #2e3038;
                     border-radius:6px; }
QLabel#messageLine { color:#c6c8d0; background:#23252b;
                     border:1px solid #2e3038; border-radius:6px;
                     padding:6px; }
QFrame#confirmBar { background:#2b2416; border:1px solid #b26a00;
                    border-radius:6px; padding:4px; }
QPushButton { background:#2b2d34; color:#e8e8ee; border:1px solid #3c3f49;
              border-radius:6px; padding:6px 14px; }
QPushButton:hover { background:#35383f; }
QPushButton#primaryBtn { background:#5b5bd6; color:#ffffff; border:none;
                         font-weight:bold; padding:9px 26px; }
QLineEdit { background:#23252b; border:1px solid #3c3f49;
            border-radius:6px; padding:5px 8px; }
QComboBox { background:#2b2d34; border:1px solid #3c3f49;
            border-radius:6px; padding:4px 10px; }
QToolButton { background:transparent; color:#e8e8ee; border:none;
              font-weight:bold; padding:4px 6px; }
QTableWidget { background:#1e2025; alternate-background-color:#22242a;
               gridline-color:#2b2d35; border:1px solid #2e3038;
               border-radius:4px; selection-background-color:#3a3b6e; }
QHeaderView::section { background:#262831; color:#b9bcc7; border:none;
                       border-right:1px solid #2e3038; padding:6px 8px; }
QTabWidget::pane { border:1px solid #2e3038; }
QTabBar::tab { background:transparent; color:#9a9ca6; padding:7px 16px; }
QTabBar::tab:selected { background:#23252b; color:#ffffff; }
"""


def _drive_row(board_table: BoardTable, node_id: str, **fields):
    """演示驱动：改一行假数据的显示值（只动 fake_data 内存副本，不落盘）。"""
    for n in fake_data.NODES:
        if n["id"] == node_id:
            n.update(fields)


class ProtoMainWindow(QWidget):
    """原型主窗：三个演示页。"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("task29 原型 · 界面优化第二轮（假数据）")
        self.resize(1080, 720)
        root = QVBoxLayout(self)
        self.tabs = QTabWidget()
        root.addWidget(self.tabs)
        self.tab1 = self._build_tab1()
        self.tabs.addTab(self.tab1, "组一 · ①②区改版")
        self.tab2 = self._build_tab2()
        self.tabs.addTab(self.tab2, "组二 · 分区折叠")
        self.tab3 = self._build_tab3()
        self.tabs.addTab(self.tab3, "组三 · 行内确认与可发现性")

    # ---------------- 组一：前台窗①②区改版 ----------------
    def _build_tab1(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        # 演示控制行（驱动状态用，不是交付形态的一部分）
        demo = QHBoxLayout()
        demo.addWidget(QLabel("演示驱动："))
        for text, slot in (
                ("连接中", lambda: self.tab1_session.set_state("连接中")),
                ("已连接", lambda: self.tab1_session.set_state("已连接")),
                ("健康判死(红)", lambda: self.tab1_alert.show_alert(
                    fake_data.ALERT_DEAD, LEVEL_RED)),
                ("可疑(琥珀)", lambda: self.tab1_alert.show_alert(
                    fake_data.ALERT_SUSPECT, LEVEL_AMBER)),
                ("测速失败→消息行", lambda: self.tab1_message.setText(
                    "⚠ 测速失败：出口验证未通过（不亮红条——N9 口径）")),
                ("批量摘要→页脚", lambda: self.tab1_board.set_footer(
                    fake_data.BATCH_SUMMARY)),
                ("清告警", lambda: self.tab1_alert.close_alert())):
            btn = QPushButton(text)
            btn.clicked.connect(slot)
            demo.addWidget(btn)
        demo.addStretch(1)
        lay.addLayout(demo)

        # ①区四通道：告警条 / 状态面板 / 节点行（延迟只在此处） / 速度行（只吞吐） / 消息行
        self.tab1_alert = AlertBar()
        lay.addWidget(self.tab1_alert)
        self.tab1_session = SessionPanel()
        lay.addWidget(self.tab1_session)
        node_row = QLabel("当前节点：示例住宅-01（192.0.2.11）　[板块：免费住宅]"
                          "　[场景：客服]　延迟：45 ms")
        node_row.setWordWrap(True)
        node_row.setStyleSheet("color:#c9cbd3;padding:2px;")
        lay.addWidget(node_row)
        speed_row = QLabel("速度：实测 23.4 Mbps（11/22 09:05）")
        speed_row.setWordWrap(True)
        speed_row.setStyleSheet("color:#c9cbd3;padding:2px;")
        lay.addWidget(speed_row)
        self.tab1_message = QLabel("")
        self.tab1_message.setWordWrap(True)
        self.tab1_message.setObjectName("messageLine")
        lay.addWidget(self.tab1_message)

        # ①区主按钮行
        actions = QHBoxLayout()
        connect_btn = QPushButton("连 接")
        connect_btn.setObjectName("primaryBtn")
        actions.addWidget(connect_btn)
        actions.addWidget(QPushButton("断开"))
        actions.addWidget(QPushButton("系统代理 ▾"))
        actions.addStretch(1)
        lay.addLayout(actions)

        # ②区板块表（改版形态）
        self.tab1_board = BoardTable("免费住宅")
        lay.addWidget(self.tab1_board, 1)
        return w

    # ---------------- 组二：分区折叠与④区收起态 ----------------
    def _build_tab2(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        # ②区放最上（折叠形态下吃满余宽）
        self.tab2_board = BoardTable("免费机房")
        lay.addWidget(self.tab2_board, 1)

        # ③区：折叠 + 标题行摘要 + 只读摘要正文（task26 形态 + N15 两行提示）
        body3 = self._rules_body()
        self.sec3 = CollapsibleSection(
            "③ 规则与网络", "分流：全局 · 拦截：关 / 关 · DNS：Google",
            body3, collapsed=True)
        lay.addWidget(self.sec3)

        # ④区：收起态（N16 设计输入——落位时机归④区集成段，本原型只定形态）
        body4 = QLabel("④区面板控件本体归④区集成段落位：总开关（默认关）+"
                       "备用组列表（拖动排序）+ <2 禁用说明，控件面沿用 "
                       "work21/proto/failover_zone_proto.py 的已验收形态；"
                       "收起态即本标题行形态，一行不丢关键状态。")
        body4.setWordWrap(True)
        body4.setStyleSheet("color:#9a9ca6;padding:6px;")
        self.sec4 = CollapsibleSection("④ 容灾", "总开关：关 · 组内 0 个",
                                       body4, collapsed=True)
        lay.addWidget(self.sec4)

        # ⑤区：折叠 + 监测状态行正文（⑤区数字口径：定时监测三态）
        body5 = QLabel("定时监测：开 · 每 10 分钟自动探测延迟（开关与间隔在设置页改）")
        body5.setWordWrap(True)
        body5.setStyleSheet("color:#9a9ca6;padding:6px;")
        self.sec5 = CollapsibleSection("⑤ 测速与监测", "定时：开 · 每 10 分钟",
                                       body5, collapsed=True)
        lay.addWidget(self.sec5)

        # ⑥区：折叠 + 清单面板正文（徽标文案口径照实树：L2=登录页可达）
        body6 = self._special_body()
        self.sec6 = CollapsibleSection("⑥ 特殊网站测试", "清单 3 站 · 全部启用",
                                       body6, collapsed=True)
        lay.addWidget(self.sec6)
        return w

    def _rules_body(self) -> QWidget:
        body = QWidget()
        grid = QGridLayout(body)
        grid.setContentsMargins(10, 4, 10, 4)
        fields = (("分流模式", "全局代理"), ("广告拦截", "关"),
                  ("恶意拦截", "关"), ("IPv6", "关"),
                  ("DNS 服务器", "Google · 8.8.8.8"))
        for row, (k, v) in enumerate(fields):
            grid.addWidget(QLabel(k), row, 0)
            grid.addWidget(QLabel(v), row, 1)
        note1 = QLabel("以上为当前生效值（现读设置）；在设置页修改（菜单「设置」）。")
        note1.setStyleSheet("color:#9a9ca6;")
        note1.setWordWrap(True)
        grid.addWidget(note1, len(fields), 0, 1, 2)
        note2 = QLabel("生效口径：下次连接生效——连接时现读设置生成配置，不做热重载。")
        note2.setStyleSheet("color:#9a9ca6;")
        note2.setWordWrap(True)
        grid.addWidget(note2, len(fields) + 1, 0, 1, 2)
        return body

    def _special_body(self) -> QWidget:
        body = QWidget()
        lay = QVBoxLayout(body)
        note = QLabel(fake_data.FAKE_SITES[0] + " 等三站（example 保留域演示）；"
                      "L2 全过只标「登录页可达」，不标「可用」。")
        note.setWordWrap(True)
        note.setStyleSheet("color:#9a9ca6;")
        lay.addWidget(note)
        table = QTableWidget(3, 4)
        table.setHorizontalHeaderLabels(("名称", "域名", "类别", "启用"))
        for r, (name, dom, cat) in enumerate((
                ("示例AI", "example-ai.example", "ai"),
                ("示例搜索", "search.example", ""),
                ("示例门户", "portal.example.com", "portal"))):
            table.setItem(r, 0, QTableWidgetItem(name))
            table.setItem(r, 1, QTableWidgetItem(dom))
            table.setItem(r, 2, QTableWidgetItem(cat or "（无类别）"))
            table.setItem(r, 3, QTableWidgetItem("✓"))
        lay.addWidget(table)
        return body

    # ---------------- 组三：行内确认条与可发现性 ----------------
    def _build_tab3(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)

        # 行内确认条（R4 演示形态——仅供 G15 裁决，不是裁决结论）
        self.confirm_bar = ConfirmBar()
        lay.addWidget(self.confirm_bar)
        actions = QHBoxLayout()
        actions.addWidget(QLabel("删除 2 个选中节点 → "))
        del_btn = QPushButton("删除")
        del_btn.clicked.connect(lambda: self.confirm_bar.show_confirm(
            "确定删除选中的 2 个节点？连同测速记录一起删，删了恢复不了。",
            "确认删除", lambda: self.tab3_result.setText("已删除 2 个节点（演示）")))
        actions.addWidget(del_btn)
        purge_btn = QPushButton("删上一轮废节点（3）")
        purge_btn.clicked.connect(lambda: self.confirm_bar.show_confirm(
            "上一轮批量测出 3 个废节点。确定一键删除？连同测速记录一起删，"
            "删了恢复不了。", "确认删除",
            lambda: self.tab3_result.setText("已删除 3 个废节点（演示）")))
        actions.addWidget(purge_btn)
        cancel_btn = QPushButton("收起确认条")
        cancel_btn.clicked.connect(self.confirm_bar.cancel)
        actions.addWidget(cancel_btn)
        actions.addStretch(1)
        lay.addLayout(actions)
        self.tab3_result = QLabel("")
        self.tab3_result.setStyleSheet("color:#8fd694;")
        lay.addWidget(self.tab3_result)

        # 可发现性：搜索/排序/双击提示全在 BoardTable（组三给全库表）
        lay.addWidget(QLabel("板块页过滤行：搜索框 + 排序下拉（默认推荐序）+ "
                             "表头 tooltip「双击行＝连接」；⑥区批量按钮动态"
                             "带板块名 →"))
        board_btn = QPushButton("测「免费住宅」板块特殊站")
        board_btn.setToolTip("作用域=②区当前页签，按钮文案动态点名板块（N5 口径）。")
        lay.addWidget(board_btn)
        self.tab3_board = BoardTable(None)   # None=全库演示
        lay.addWidget(self.tab3_board, 1)
        return w

    # ---------------- 自检与截图 ----------------
    def drive_showcase(self):
        """把三页驱到有信息量的状态（--shot 与 --selfcheck 共用）。"""
        self.tabs.setCurrentIndex(0)
        self.tab1_session.set_state("已连接")
        self.tab1_message.setText(fake_data.MSG_CONNECTED)
        self.tab1_alert.show_alert(fake_data.ALERT_DEAD, LEVEL_RED)
        self.tab1_board.set_footer(fake_data.BATCH_SUMMARY)
        self.tabs.setCurrentIndex(1)
        self.tab2_board.set_footer(
            fake_data.batch_summary("免费机房"))
        self.tabs.setCurrentIndex(2)
        self.confirm_bar.show_confirm(
            "确定删除选中的 2 个节点？连同测速记录一起删，删了恢复不了。",
            "确认删除", None)
        self.tab3_result.setText("")
        self.tabs.setCurrentIndex(0)

    def selfcheck(self) -> list:
        """offscreen 行为自检：返回失败清单（空=全过）。"""
        fails = []

        def check(cond, what):
            if not cond:
                fails.append(what)

        self.drive_showcase()
        # 组一：状态与消息并存不互冲
        self.tabs.setCurrentIndex(0)
        check(self.tab1_session.state == "已连接", "组一: 状态面板应保持已连接")
        check(self.tab1_message.text() == fake_data.MSG_CONNECTED,
              "组一: 消息行应承载测速消息")
        check(self.tab1_alert.visible_text == fake_data.ALERT_DEAD,
              "组一: 红条应显示健康判死文案")
        # 告警换行 + 关闭
        check(self.tab1_alert.label.wordWrap(), "组一: 告警条应可换行")
        self.tab1_alert.close_alert()
        check(not self.tab1_alert.isVisible(), "组一: 告警条应可关闭")
        self.tab1_alert.show_alert(fake_data.ALERT_SUSPECT, LEVEL_AMBER)
        check(self.tab1_alert.isVisible(), "组一: 琥珀档应可亮出")
        # 页脚摘要槽与空值「—」
        check(self.tab1_board.footer_slot.text() == fake_data.BATCH_SUMMARY,
              "组一: 批量摘要应常驻页脚")
        check(any(self.tab3_board.table.item(r, c) is not None
                  and self.tab3_board.table.item(r, c).text() == "—"
                  for r in range(self.tab3_board.row_count)
                  for c in range(2, 8)),
              "组一/组三: 未测节点空值应统一「—」")
        # 组二：折叠分区默认收起、展开可见、④区收起态文案
        self.tabs.setCurrentIndex(1)
        check(self.sec3.collapsed and self.sec4.collapsed
              and self.sec5.collapsed and self.sec6.collapsed,
              "组二: ③④⑤⑥应默认收起")
        check("总开关：关" in self.sec4.summary_label.text(),
              "组二: ④区收起态摘要应含开关状态")
        self.sec4.set_collapsed(False)
        check(not self.sec4.collapsed, "组二: ④区应可展开")
        check(self.sec4._body.isVisible(), "组二: ④区展开后正文应可见")
        self.sec4.set_collapsed(True)
        check(self.sec4.collapsed, "组二: ④区应可再收起")
        # 组三：行内确认条（无模态）+ 搜索过滤 + 排序默认档
        self.tabs.setCurrentIndex(2)
        check(self.confirm_bar.isVisible(), "组三: 确认条应可见")
        check(self.confirm_bar.yes_btn.text() == "确认删除",
              "组三: 确认按钮文案应中文")
        self.confirm_bar.cancel()
        check(not self.confirm_bar.isVisible(), "组三: 取消应收起确认条")
        check(self.tab3_board.sort_combo.currentData() == "default",
              "组三: 排序默认档应为推荐序")
        total = self.tab3_board.row_count
        self.tab3_board.search_edit.setText("示例住宅")
        check(0 < self.tab3_board.row_count < total,
              "组三: 搜索应过滤行数")
        self.tab3_board.search_edit.setText("不存在的节点 xyz")
        check(self.tab3_board.row_count == 0, "组三: 搜空应出空态卡")
        check(self.tab3_board.empty_hint.isVisible(), "组三: 空态卡应可见")
        self.tab3_board.search_edit.setText("")
        self.tabs.setCurrentIndex(0)
        return fails

    def shoot(self, out_dir: str):
        """三页各落一张 PNG（offscreen 自检用）。"""
        os.makedirs(out_dir, exist_ok=True)
        self.drive_showcase()
        self.grab().save(os.path.join(out_dir, "proto_main.png"))
        self.tabs.setCurrentIndex(1)
        self.grab().save(os.path.join(out_dir, "proto_tab2.png"))
        self.sec3.set_collapsed(False)
        self.sec6.set_collapsed(False)
        self.resize(1080, 860)          # 展开态加高，⑥区清单完整入镜
        self.grab().save(os.path.join(out_dir, "proto_tab2_expanded.png"))
        self.resize(1080, 720)
        self.sec3.set_collapsed(True)
        self.sec6.set_collapsed(True)
        self.tabs.setCurrentIndex(2)
        self.confirm_bar.show_confirm(
            "确定删除选中的 2 个节点？连同测速记录一起删，删了恢复不了。",
            "确认删除", None)
        self.grab().save(os.path.join(out_dir, "proto_tab3.png"))
        self.drive_showcase()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="task29 原型主窗")
    parser.add_argument("--shot", default=None, help="截图输出目录")
    parser.add_argument("--selfcheck", action="store_true",
                        help="offscreen 行为自检（无窗口等待）")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(PROTO_QSS)
    win = ProtoMainWindow()
    win.show()
    if args.selfcheck:
        fails = win.selfcheck()
        for f in fails:
            print(f"FAIL: {f}", file=sys.stderr)
        print("selfcheck:", "OK" if not fails else f"{len(fails)} fails")
        return 0 if not fails else 1
    if args.shot:
        win.shoot(args.shot)
        print(f"shots written to {args.shot}")
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
