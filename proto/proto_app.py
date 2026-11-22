#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task14 原型主窗：三个演示页组装 + offscreen 截图自检入口。

运行（正常显示）:
  python proto/proto_app.py
运行（offscreen 截图，供无显示器环境自检）:
  QT_QPA_PLATFORM=offscreen python proto/proto_app.py --shot <目录>

不接真实 Store/引擎；不 import client 包；数据全虚构。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_PARENT = str(Path(__file__).resolve().parent.parent)
if _PARENT not in sys.path:
    sys.path.insert(0, _PARENT)

from PySide6.QtWidgets import QApplication, QTabWidget, QVBoxLayout, QWidget

# 深色观感只取现窗 QSS 的一个子集思路，原型内自足，不 import client
PROTO_QSS = """
QWidget { background:#1b1c20; color:#e8e8ee; font-size:13px; }
QLabel { background: transparent; }
QLabel#statusText { font-size:15px; font-weight:bold; }
QFrame#statusPanel { background:#23252b; border:1px solid #2e3038;
                     border-radius:6px; }
QLabel#messageLine { color:#c6c8d0; background:#23252b;
                     border:1px solid #2e3038; border-radius:6px;
                     padding:6px; }
QFrame#emptyCard { background:#20222a; border:1px dashed #3c3f49;
                   border-radius:8px; }
QFrame#confirmBar { background:#2b2416; border:1px solid #b26a00;
                    border-radius:6px; }
QPushButton { background:#2b2d34; color:#e8e8ee; border:1px solid #3c3f49;
              border-radius:6px; padding:6px 14px; }
QPushButton:hover { background:#35383f; }
QLineEdit { background:#23252b; border:1px solid #3c3f49;
            border-radius:6px; padding:5px 8px; }
QLineEdit:focus { border-color:#5b5bd6; }
QComboBox { background:#2b2d34; border:1px solid #3c3f49;
            border-radius:6px; padding:4px 10px; }
QCheckBox { color:#c9cbd3; spacing:6px; }
QTableWidget { background:#1e2025; alternate-background-color:#22242a;
               gridline-color:#2b2d35; border:1px solid #2e3038;
               border-radius:4px; selection-background-color:#3a3b6e; }
QHeaderView::section { background:#262831; color:#b9bcc7; border:none;
                       border-right:1px solid #2e3038; padding:6px 8px; }
QTabWidget::pane { border:1px solid #2e3038; }
QTabBar::tab { background:transparent; color:#9a9ca6; padding:7px 16px; }
QTabBar::tab:selected { background:#23252b; color:#ffffff; }
"""


class ProtoMainWindow(QWidget):
    """原型主窗：三个演示页（状态呈现 / 来源页 / 空态与待清）。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("gate-client 界面改造原型（task14·假数据）")
        self.resize(1180, 680)      # 对应 G13：默认宽 1080→1180
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs)

        # 每页在此组装，保证测试可直接定位
        from proto.proto_status_demo import StatusDemo
        from proto.proto_source_demo import ProtoSourcePage
        from proto.proto_empty_demo import EmptyStateDemo
        self.status_demo = StatusDemo()
        self.source_page = ProtoSourcePage()
        self.empty_demo = EmptyStateDemo()
        self.tabs.addTab(self.status_demo, "① 状态呈现改版")
        self.tabs.addTab(self.source_page, "② 来源页改版")
        self.tabs.addTab(self.empty_demo, "③ 空态与待清")


def shoot(window: ProtoMainWindow, out_dir: str):
    """offscreen 自检：先把三个演示页各驱到一个有信息量的状态再落图。

    落 4 张：tab1（已连接+红条长文案）、tab2（默认表格）、tab3（过滤无
    结果+待清批次 3）、主窗。返回落图路径清单。
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance()
    paths = []

    def snap(name):
        if app:
            app.processEvents()
        p = out / name
        window.grab().save(str(p))
        paths.append(str(p))

    # tab1：走完连接三步 + 亮一条长文案红条（验证状态不被冲掉、换行、×）
    window.tabs.setCurrentIndex(0)
    window.status_demo._simulate_connect_click()
    window.status_demo._simulate_started()
    window.status_demo._simulate_connected()
    window.status_demo._simulate_long_alert()
    window.status_demo._simulate_health_probe()
    snap("proto_tab1.png")

    # tab2：默认假数据表格（排序/列口径/标记列/带日期最近列）
    window.tabs.setCurrentIndex(1)
    snap("proto_tab2.png")

    # tab3：过滤无结果卡 + 待清批次计数（验证 G4/G12 呈现）
    window.tabs.setCurrentIndex(2)
    window.empty_demo.purge_btn.set_pending(3)
    window.empty_demo._show_filtered_out()
    snap("proto_tab3.png")

    if app:
        app.processEvents()
    # 主窗收尾：切回来源页（信息量最大的一页）再落一张整窗图
    window.tabs.setCurrentIndex(1)
    if app:
        app.processEvents()
    p = out / "proto_main.png"
    window.grab().save(str(p))
    paths.append(str(p))
    return paths


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="proto_app",
                                     description="task14 界面改造原型（假数据）")
    parser.add_argument("--shot", default="",
                        help="offscreen 截图目录；给了则截图后退出不显示窗口")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setStyleSheet(PROTO_QSS)
    window = ProtoMainWindow()
    if args.shot:
        window.show()
        paths = shoot(window, args.shot)
        for p in paths:
            print(p)
        return 0
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
