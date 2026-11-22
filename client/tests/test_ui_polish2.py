#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task29 离线测试（test_ui_polish2.py，跑在 work29/ 内）。

三组（DESIGN.md §7 验收口径）：
1. 锚点组——复审表 R1–R9 与新审计 N1–N16 逐项断言落点真实（文件+行号+
   特征串精确匹配，防编造）；缺失类以 grep 反证固定；client-dev 不在位
   整组 skip 记原因。行号 = 0.30.0 实树实测；后续集成段改动界面文件致
   锚点漂移时测试失败并指出漂移处——这是特性不是缺陷。
2. 设计互证组——DESIGN.md 审计条目齐全性（R/N 逐 ID 双处出现：审计表 +
   改造设计表）、快赢/铁律/diff 草案在位、待裁定项不含预设结论。
3. 原型组——PySide6 缺席则整组 skip 记原因（不伪造通过）：offscreen
   构造原型主窗、行为断言（状态与消息并存不互冲、告警换行+关闭、空值
   「—」、待清/摘要文案、折叠状态数）、shoot() 截图通路、红线看守
   （proto 不 import client、不用模态对话框）。

运行：
  cd work29 && python3 -m unittest          # 系统 Python：原型组 skip
  work14/.venv-gui/bin/python -m unittest   # 有 PySide6：全绿 0 skip
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

DEV_ROOT = Path(__file__).resolve().parents[2]
CLIENT_DEV = Path(os.environ.get("GATE_CLIENT_DEV", DEV_ROOT))
APP = CLIENT_DEV / "client" / "ui" / "app.py"
USAGE = CLIENT_DEV / "client" / "ui" / "usage_window.py"
RULES = CLIENT_DEV / "client" / "ui" / "usage_rules_page.py"
SPECIAL = CLIENT_DEV / "client" / "ui" / "special_sites_page.py"
SETTINGS = CLIENT_DEV / "client" / "ui" / "settings_dialog.py"
ORCH = CLIENT_DEV / "client" / "core" / "orchestrator.py"
FAILOVER = CLIENT_DEV / "client" / "core" / "failover.py"
HISTORY = CLIENT_DEV / "client" / "core" / "history.py"
SPECIAL_PROBE = CLIENT_DEV / "client" / "core" / "special_probe.py"
DESIGN = DEV_ROOT / "proto" / "polish2" / "DESIGN.md"
PROTO_DIR = DEV_ROOT / "proto" / "polish2"


def _lines(path: Path):
    return path.read_text(encoding="utf-8").splitlines()


def _block(path: Path, start: int, end: int) -> str:
    """1-based 闭区间行块拼串（块内特征检查用）。"""
    return "\n".join(_lines(path)[start - 1:end])


class DevTreeTestCase(unittest.TestCase):
    """client-dev 不在位 → 整组 skip 记原因（不伪造通过）。"""

    def setUp(self):
        if not CLIENT_DEV.is_dir():
            self.skipTest(f"client-dev 不在位：{CLIENT_DEV}（只读参考缺失）")


# ---------------------------------------------------------------------------
# 1. 锚点组：复审 R1–R9（work14 推迟项对 0.30.0 实树）
# ---------------------------------------------------------------------------
class TestReauditAnchors(DevTreeTestCase):

    # R1：G13 后台动作排 14 控件单行 + 窗宽 1080 + 已降后台定位
    def test_r1_action_row_14_widgets_one_layout(self):
        self.assertIn("actions = QHBoxLayout()", _block(APP, 485, 485))
        for lineno, needle in ((460, "gate-client · 后台（来源管理）"),
                (461, "self.resize(1080, 640)"),
                (486, 'QPushButton("连 接")'),
                (490, 'QPushButton("断开")'),
                (496, "self.mode_combo = QComboBox()"),
                (505, 'QPushButton("测速验出口")'),
                (508, 'QPushButton("置顶/取消")'),
                (511, 'QPushButton("拉黑/取消")'),
                (514, 'QPushButton("删除")'),
                (517, 'QPushButton("一键删废节点")'),
                (523, 'QCheckBox("自动探测")'),
                (527, "self.auto_interval_combo = QComboBox()"),
                (534, 'QPushButton("测本页")'),
                (537, 'QPushButton("一键全测")'),
                (564, 'QPushButton("刷新")'),
                (569, 'QPushButton("设置")'),
                (572, "root.addLayout(actions)")):
            self.assertIn(needle, _block(APP, lineno, lineno),
                          f"app.py:{lineno} 应含 {needle!r}（R1 锚点漂移）")

    # R2：G3/G19 序保持——整表重建 + 全量重排 + 任务/事件收尾全刷，两窗都在犯
    def test_r2_full_rebuild_and_resort_both_windows(self):
        self.assertIn("self.table.setRowCount(0)", _block(APP, 327, 327))
        self.assertIn("history.sorted_nodes(self.store, nodes)",
                      _block(APP, 847, 847))
        self.assertIn("self.refresh_pages()", _block(APP, 1658, 1658))
        self.assertIn("self.table.setRowCount(0)", _block(USAGE, 234, 234))
        self.assertIn(
            "page_nodes = history.sorted_nodes(self.store, groups.get(key, []))",
            _block(USAGE, 790, 790))
        self.assertIn("self.refresh_boards()", _block(USAGE, 1411, 1411))
        self.assertIn("self.refresh_boards()", _block(USAGE, 1491, 1491))
        # 反证：序保持机制（keep_order）两窗都没落
        self.assertNotIn("keep_order", APP.read_text(encoding="utf-8"))
        self.assertNotIn("keep_order", USAGE.read_text(encoding="utf-8"))

    # R3：托盘/关窗——前台窗关窗=进托盘（默认形态已落、裁定注释在位）、后台窗真关
    def test_r3_tray_close_default_and_pending_ruling(self):
        self.assertIn("def closeEvent(self, event):", _block(USAGE, 1542, 1542))
        self.assertIn("待主人裁定", _block(USAGE, 1544, 1545))
        self.assertIn("event.ignore()", _block(USAGE, 1547, 1547))
        self.assertIn("已最小化到托盘，连接保持不变。", _block(USAGE, 1550, 1550))
        self.assertIn("def closeEvent(self, event):", _block(APP, 832, 832))
        self.assertIn("super().closeEvent(event)", _block(APP, 835, 835))

    # R4：G15 确认模态存量（只列选项不裁决）——逐处行号
    def test_r4_confirm_modals_inventory(self):
        self.assertIn('QMessageBox.question(self, "删除节点", text)',
                      _block(APP, 1248, 1248))
        self.assertIn("QMessageBox.question(", _block(APP, 1306, 1306))
        self.assertIn("QMessageBox.question(", _block(APP, 1322, 1322))
        self.assertIn("QInputDialog.getMultiLineText(", _block(APP, 1744, 1744))
        self.assertIn("QInputDialog.getText(self", _block(APP, 1763, 1763))
        for lineno in (337, 365, 382):
            self.assertIn("QMessageBox.question(",
                          _block(SETTINGS, lineno, lineno))
        for lineno in (353, 360):
            self.assertIn("QMessageBox.warning(",
                          _block(SETTINGS, lineno, lineno))
        self.assertIn('QMessageBox.information(self, "一键诊断", text)',
                      _block(USAGE, 692, 692))
        self.assertIn("QMessageBox.critical(", _block(USAGE, 1615, 1615))
        self.assertIn("QMessageBox.warning(", _block(USAGE, 1623, 1623))

    # R5：G17 告警条——前台已换行；后台未换行；两窗均无关闭按钮、均单一红
    def test_r5_alert_bar_tiers_incomplete(self):
        self.assertIn("self.alert_bar.setWordWrap(True)",
                      _block(USAGE, 424, 424))
        self.assertIn("background:#b00020", _block(APP, 469, 469))
        self.assertIn("background:#b00020", _block(USAGE, 423, 423))
        # 反证：后台告警条块内无 wordWrap；两窗无「×」关闭、无琥珀档
        self.assertNotIn("setWordWrap", _block(APP, 467, 471))
        self.assertNotIn('"×"', APP.read_text(encoding="utf-8"))
        self.assertNotIn('"×"', USAGE.read_text(encoding="utf-8"))
        self.assertNotIn("b26a00", APP.read_text(encoding="utf-8"))
        self.assertNotIn("b26a00", USAGE.read_text(encoding="utf-8"))

    # R6：G8 排序档位两窗都没做（grep 反证）
    def test_r6_no_sort_options(self):
        for path in (APP, USAGE):
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("setSortingEnabled", text, f"{path.name}")
            self.assertNotIn("sort_combo", text, f"{path.name}")

    # R7：G6 「最近」列 HH:MM 两窗原样
    def test_r7_recent_column_hhmm_only(self):
        self.assertIn('time.strftime("%H:%M", time.localtime(last_ts))',
                      _block(APP, 357, 357))
        self.assertIn('time.strftime("%H:%M", time.localtime(last_ts))',
                      _block(USAGE, 265, 265))

    # R8：G4 删废按钮静态文案、待清批次藏分支里
    def test_r8_purge_button_static_label(self):
        self.assertIn('QPushButton("一键删废节点")', _block(APP, 517, 517))
        self.assertIn("if self._batch_waste:", _block(APP, 1305, 1305))
        # 反证：按钮文案从不动态更新
        self.assertNotIn("purge_btn.setText",
                         APP.read_text(encoding="utf-8"))

    # R9：G23 标记拼名称列、无活动行高亮（两窗）
    def test_r9_marks_in_name_no_highlight(self):
        self.assertIn('marks = ("📌" if n.pinned else "")',
                      _block(APP, 332, 332))
        self.assertIn('active = "● " if n.id == active_node_id else ""',
                      _block(APP, 334, 334))
        self.assertIn('marks = ("📌" if n.pinned else "")',
                      _block(USAGE, 239, 239))
        self.assertIn('name_item = QTableWidgetItem(f"{active}{marks}{n.name}")',
                      _block(USAGE, 242, 242))
        # 反证：无行底色（QSS selection 外状态色不存在）
        self.assertNotIn("setBackground",
                         APP.read_text(encoding="utf-8"))
        self.assertNotIn("setBackground",
                         USAGE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 2. 锚点组：新审计 N1–N16（使用界面 U1–U6）
# ---------------------------------------------------------------------------
class TestNewAuditAnchors(DevTreeTestCase):

    # N1：健康三态前台全盲——探测/落地全在后台窗、后台窗从不 show、无转发
    def test_n1_health_probe_presentation_blind_on_frontend(self):
        self.assertIn("def _maybe_health_probe(self):", _block(APP, 1437, 1437))
        self.assertIn("def _finish_health_probe(self, ok, payload):",
                      _block(APP, 1471, 1471))
        self.assertIn("if status == HEALTH_DEAD:", _block(APP, 1485, 1485))
        self.assertIn("self._show_alert(text)", _block(APP, 1488, 1488))
        self.assertIn("self.tray = None", _block(APP, 576, 576))
        self.assertIn("backend = MainWindow(store, orchestrator=usage.orchestrator,",
                      _block(APP, 1885, 1885))
        self.assertIn("usage.show()", _block(APP, 1900, 1900))
        self.assertIn("if self.orchestrator.state == Orchestrator.CONNECTED:",
                      _block(USAGE, 1317, 1317))
        self.assertIn("def _failover_alert(self, payload):",
                      _block(USAGE, 762, 762))
        # 反证：呈现转发通道未落（N1 改法落地后此断言失败=特性）
        self.assertNotIn("_frontend_feed", APP.read_text(encoding="utf-8"))
        self.assertNotIn("_frontend_feed", USAGE.read_text(encoding="utf-8"))

    # N2：_diag_active 未初始化——首点诊断即 AttributeError（bug 锚点）
    def test_n2_diag_active_never_initialized(self):
        self.assertIn("if self._diag_active:", _block(USAGE, 646, 646))
        self.assertIn("self._diag_active = False", _block(USAGE, 1417, 1417))
        # 反证：__init__（321–542）没有初始化行——修复后此断言失败=特性
        self.assertNotIn("_diag_active", _block(USAGE, 321, 542),
                         "N2 已被修复或行号漂移：请核对 §4 快赢 1")

    # N3：前台窗纵向 10 段常驻堆叠（9 addWidget + 1 addLayout），折叠未落
    def test_n3_vertical_stack_10_blocks_no_collapse(self):
        self.assertIn("self.resize(1080, 640)", _block(USAGE, 398, 398))
        addwidget = [ln for ln in _lines(USAGE)
                     if "root.addWidget(" in ln]
        self.assertEqual(len(addwidget), 9,
                         "前台窗 root.addWidget 应恰 9 处（N3 漂移）")
        self.assertIn('title = QLabel("③ 规则与网络")', _block(RULES, 105, 105))
        self.assertIn('title = QLabel("⑥ 特殊网站测试")',
                      _block(SPECIAL, 144, 144))
        text = USAGE.read_text(encoding="utf-8")
        self.assertNotIn("CollapsibleSection", text)
        self.assertNotIn("QToolButton", text)

    # N4：延迟数字①区两行重复
    def test_n4_latency_duplicated_in_two_rows(self):
        self.assertIn("延迟：{history.format_latency_ms(stats.median_latency_ms)}",
                      _block(USAGE, 1352, 1352))
        self.assertIn("延迟：{history.format_latency_ms(stats.median_latency_ms)}",
                      _block(USAGE, 1371, 1371))

    # N5：⑥区批量按钮作用域=②区当前页签但不点名
    def test_n5_special_batch_button_scope_unnamed(self):
        self.assertIn('QPushButton("测本板块特殊站")',
                      _block(SPECIAL, 191, 191))
        self.assertIn("page = self.tabs.currentWidget()",
                      _block(USAGE, 1118, 1118))
        # 反证：无动态板块名出口（set_board_label 类）
        self.assertNotIn("set_board_label",
                         SPECIAL.read_text(encoding="utf-8"))

    # N6：②区无搜索/无排序/双击无 tooltip（grep 反证）
    def test_n6_board_page_no_search_no_sort_hint(self):
        text = USAGE.read_text(encoding="utf-8")
        self.assertNotIn("QLineEdit", text)
        self.assertNotIn("setSortingEnabled", text)
        self.assertIn("self.table.itemDoubleClicked.connect(",
                      _block(USAGE, 216, 216))
        self.assertNotIn("双击行＝连接", text)

    # N7：跨窗刷新缺口——前台只在库计数变化时对账、无轻刷新通知
    def test_n7_cross_window_refresh_gap(self):
        self.assertIn("if self.store.count_nodes() != self._last_node_count:",
                      _block(USAGE, 1325, 1325))
        self.assertIn("self.refresh_boards()", _block(USAGE, 1326, 1326))
        self.assertNotIn("_refresh_notify", APP.read_text(encoding="utf-8"))
        self.assertNotIn("_light_refresh", USAGE.read_text(encoding="utf-8"))

    # N8：批量摘要写单行消息槽、无页脚常驻槽
    def test_n8_batch_summary_fleeting(self):
        self.assertIn("for note in speed_notes[:3]:", _block(USAGE, 1459, 1459))
        self.assertIn("self._show_info(msg)", _block(USAGE, 1461, 1461))
        text = USAGE.read_text(encoding="utf-8")
        self.assertNotIn("footer", text)
        self.assertNotIn("最近一轮", text)

    # N9：测速失败/特殊站失败亮红条（两窗）
    def test_n9_test_failure_lights_red_bar(self):
        self.assertIn('self._show_alert(f"测速失败：{text}")',
                      _block(USAGE, 1469, 1469))
        self.assertIn('self._show_alert(f"特殊站测试失败：{text}")',
                      _block(USAGE, 1473, 1473))
        self.assertIn('self._show_alert(f"测速失败：{text}")',
                      _block(APP, 1634, 1634))

    # N10：0 样本满屏无引导、⑥区清单空无语义态（反证）
    def test_n10_no_data_dimension_guidance(self):
        text = USAGE.read_text(encoding="utf-8")
        self.assertNotIn("还没有测速数据", text)
        self.assertNotIn("先测一轮", text)
        self.assertNotIn("空态", SPECIAL.read_text(encoding="utf-8"))

    # N11：空值口径五样（前台新实例 + 特殊站列空串）
    def test_n11_empty_value_vocabulary(self):
        self.assertIn('if ep.ip else "未验")', _block(USAGE, 245, 245))
        self.assertIn('clean_s = "未知"', _block(USAGE, 252, 252))
        self.assertIn('stab_s = "无样本"', _block(USAGE, 256, 256))
        self.assertIn('last_s = "未测"', _block(USAGE, 261, 261))
        self.assertIn('return ""', _block(SPECIAL_PROBE, 359, 359))

    # N12：标记拼名称列（前台实例）——与 R9 前台半边同锚，行号在此独立锁定
    def test_n12_marks_concatenated_front(self):
        self.assertIn('name_item = QTableWidgetItem(f"{active}{marks}{n.name}")',
                      _block(USAGE, 242, 242))

    # N13：「最近」列无日期（前台实例）
    def test_n13_recent_no_date_front(self):
        self.assertIn('time.strftime("%H:%M", time.localtime(last_ts))',
                      _block(USAGE, 265, 265))

    # N14：接管方式英文枚举（两窗）+ 反证无 currentText
    def test_n14_mode_enum_english(self):
        self.assertIn('f"接管方式：{mode}。{actions}。下次连接生效。")',
                      _block(USAGE, 1246, 1246))
        self.assertIn('f"接管方式：{mode}。{actions}。下次连接生效。")',
                      _block(APP, 1354, 1354))
        self.assertNotIn("currentText", USAGE.read_text(encoding="utf-8"))
        self.assertNotIn("currentText", APP.read_text(encoding="utf-8"))

    # N15：③区只读提示一行塞三层信息（task26 裁决形态本身不重议）
    def test_n15_readonly_note_three_layers_one_note(self):
        note = _block(RULES, 59, 61)
        self.assertIn("READONLY_NOTE", note)
        self.assertIn("现读设置", note)
        self.assertIn("在设置页修改", note)
        self.assertIn("下次连接生效", note)

    # N16：④区整区缺席——控制器在、UI 控件零
    def test_n16_failover_zone_ui_absent(self):
        self.assertIn("def _setup_failover(self):", _block(USAGE, 695, 695))
        self.assertIn("self.ops.connect(chosen_id)", _block(FAILOVER, 282, 282))
        text = USAGE.read_text(encoding="utf-8")
        self.assertNotIn("QCheckBox", text)      # 无总开关控件
        self.assertNotIn("加入备用组", text)      # 无组编辑入口
        self.assertNotIn("拖动排序", text)        # 无排序 UI


# ---------------------------------------------------------------------------
# 3. 锚点组：铁律专项（§3，零违例）
# ---------------------------------------------------------------------------
class TestIronLawAnchors(DevTreeTestCase):

    def test_only_user_named_connect_sites(self):
        hits = []
        for base in (CLIENT_DEV / "client" / "ui",
                     CLIENT_DEV / "client" / "core",
                     CLIENT_DEV / "client" / "engines"):
            for py in base.glob("*.py"):
                for i, ln in enumerate(_lines(py), 1):
                    if "orch.connect(" in ln:
                        hits.append((py.name, i))
        self.assertEqual(
            sorted(hits),
            [('app.py', 895), ('cli.py', 924), ('usage_window.py', 849)],
            "orchestrator.connect 调用点应恰 3 处且全在用户点名路径")

    def test_failover_only_dead_nodes_group_only_no_live_switch(self):
        text = FAILOVER.read_text(encoding="utf-8")
        self.assertIn('REASON_HEALTH_DEAD = "health_dead"',
                      _block(FAILOVER, 51, 51))
        self.assertIn('REASON_ENGINE_DROPPED = "engine_dropped"',
                      _block(FAILOVER, 52, 52))
        self.assertIn("绝不出现的代码", text)
        self.assertNotIn("sorted(", text)   # 无按指标重排路径
        self.assertNotIn(".sort(", text)
        self.assertNotIn("QTimer", text)    # 无定时器
        # suspect 分支不喂（后台窗 suspect 块内无 _failover_feed）
        self.assertNotIn("_failover_feed", _block(APP, 1497, 1500))
        self.assertIn("_failover_feed", _block(APP, 1492, 1496))
        self.assertIn("_failover_feed", _block(USAGE, 1401, 1403))

    def test_disclaim_auto_switch_copy(self):
        self.assertIn("不会自动切换", _block(ORCH, 160, 160))
        self.assertIn("绝不自动换节点", _block(APP, 1486, 1487))
        self.assertIn("不自动切换", _block(APP, 1499, 1499))
        self.assertIn("只决定展示顺序", _block(HISTORY, 125, 125))
        self.assertIn("只选中", _block(APP, 606, 621))
        self.assertIn("不连接、不切换任何节点", _block(USAGE, 857, 859))


# ---------------------------------------------------------------------------
# 4. 设计互证组（DESIGN.md 与测试/审计表不漂移）
# ---------------------------------------------------------------------------
class TestDesignDoc(unittest.TestCase):

    def setUp(self):
        if not DESIGN.is_file():
            self.skipTest(f"DESIGN.md 不在位：{DESIGN}")

    def _text(self) -> str:
        return DESIGN.read_text(encoding="utf-8")

    def test_all_audit_ids_in_both_tables(self):
        text = self._text()
        for rid in [f"R{i}" for i in range(1, 10)] \
                + [f"N{i}" for i in range(1, 17)]:
            self.assertGreaterEqual(text.count(f"| {rid} "), 2,
                                    f"{rid} 应同时在审计表与改造设计表出现")

    def test_required_sections_present(self):
        text = self._text()
        for needle in ("## 3. 铁律专项", "零违例",
                       "### 4.1 明确不改的项", "### 4.2 快赢清单",
                       "## 5. diff 草案", "## 0. 审计口径",
                       "## 1. 复审", "## 2. 新审计", "## 6. 原型"):
            self.assertIn(needle, text)

    def test_pending_ruling_items_have_no_verdict(self):
        text = self._text()
        # R3/R4/N16 是待裁定项：只列选项、不裁决
        self.assertIn("待主人裁定", text)
        self.assertIn("只列选项不裁决", text)
        self.assertIn("本段不裁决落位时机", text)
        self.assertIn("不预设结论", text)
        # R4 三选项在位（A 维持模态 / B 行内确认条 / C 折中）
        for opt in ("A. 维持模态", "B. 破坏性确认改行内确认条", "C. 折中"):
            self.assertIn(opt, text)

    def test_version_anchor_is_0300(self):
        self.assertIn("0.30.0", self._text())


# ---------------------------------------------------------------------------
# 5. 原型红线看守（无 Qt 依赖，系统 Python 也要跑）
# ---------------------------------------------------------------------------
class TestProtoRedLines(unittest.TestCase):

    def setUp(self):
        if not PROTO_DIR.is_dir():
            self.skipTest(f"proto/ 不在位：{PROTO_DIR}")

    def _sources(self):
        return {p.name: p.read_text(encoding="utf-8")
                for p in sorted(PROTO_DIR.glob("*.py"))}

    def test_no_client_package_import(self):
        import ast
        for name, src in self._sources().items():
            tree = ast.parse(src)
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    bad = [a.name for a in node.names
                           if a.name == "client" or a.name.startswith("client.")]
                    self.assertEqual(bad, [], f"{name}:{node.lineno} import client")
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    self.assertFalse(
                        mod == "client" or mod.startswith("client."),
                        f"{name}:{node.lineno} from client import …")
            self.assertNotRegex(src, r"import_module\(", f"{name}")

    def test_no_modal_dialogs(self):
        for name, src in self._sources().items():
            for banned in ("QMessageBox", "QInputDialog", "QFileDialog",
                           "QDialog"):
                self.assertNotIn(banned, src, f"{name} 用了模态控件 {banned}")
            # .exec( 仅放行 QApplication 事件循环（app.exec()）；QMenu.exec、
            # 对话框 exec 等一切模态 exec 都不放行
            modal_exec = [m.group(1) for m in re.finditer(r"(\w+)\.exec\(", src)
                          if m.group(1) != "app"]
            self.assertEqual(modal_exec, [],
                             f"{name} 有模态 exec 调用：{modal_exec}")

    def test_fake_data_only(self):
        src = (PROTO_DIR / "fake_data.py").read_text(encoding="utf-8")
        hits = sum(1 for ip in ("192.0.2.", "198.51.100.", "203.0.113.")
                   if ip in src)
        self.assertGreaterEqual(hits, 2, "假数据应使用 RFC 5737 保留段")
        self.assertIn("example", src)
        for name, src in self._sources().items():
            self.assertNotIn("http://", src, f"{name} 不该有网络地址")
            self.assertNotIn("https://", src, f"{name} 不该有网络地址")


# ---------------------------------------------------------------------------
# 6. 原型组（PySide6 缺席整组 skip 记原因）
# ---------------------------------------------------------------------------
def _pyside6_available() -> bool:
    try:
        import PySide6  # noqa: F401
        return True
    except ImportError:
        return False


class TestProtoOffscreen(unittest.TestCase):

    def setUp(self):
        if not _pyside6_available():
            self.skipTest("PySide6 不在位（系统 Python）：原型组整组跳过；"
                          "GUI 验收请用 .venv-gui 跑（work14/.venv-gui）")
        if not (PROTO_DIR / "proto_app.py").is_file():
            self.skipTest(f"原型主窗不在位：{PROTO_DIR / 'proto_app.py'}")

    @classmethod
    def _import_proto(cls):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        if str(PROTO_DIR) not in sys.path:
            sys.path.insert(0, str(PROTO_DIR))
        import proto_app  # noqa: F401  （原型模块，不 import client）
        return proto_app

    def test_selfcheck_all_pass(self):
        proto_app = self._import_proto()
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        win = proto_app.ProtoMainWindow()
        win.show()
        try:
            fails = win.selfcheck()
            self.assertEqual(fails, [], "原型行为自检应全过")
        finally:
            win.close()
            del win
            del app

    def test_shoot_writes_pngs(self):
        proto_app = self._import_proto()
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        win = proto_app.ProtoMainWindow()
        win.show()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                win.shoot(tmp)
                names = sorted(p.name for p in Path(tmp).glob("*.png"))
                self.assertEqual(names, ["proto_main.png", "proto_tab2.png",
                                         "proto_tab2_expanded.png",
                                         "proto_tab3.png"])
                for p in Path(tmp).glob("*.png"):
                    self.assertGreater(p.stat().st_size, 1000,
                                       f"{p.name} 应为非空 PNG")
        finally:
            win.close()
            del win
            del app

    def test_runtime_no_client_modules_loaded(self):
        proto_app = self._import_proto()
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        win = proto_app.ProtoMainWindow()
        try:
            win.selfcheck()
            # 集成适配（见文件末尾注记 4）：以 client.tests 包形态运行时，
            # 测试用例自身即 client 包模块（client / client.tests /
            # client.tests.test_ui_polish2）——那是测试框架的加载，不是原
            # 型加载。红线本意是原型不得加载 client 包，故排除用例自身及
            # 其父包，只断言原型运行时新加载的 client 模块。
            harness = {"client", "client.tests", __name__}
            loaded = [m for m in sys.modules
                      if (m == "client" or m.startswith("client."))
                      and m not in harness]
            self.assertEqual(loaded, [], "原型运行时不得加载 client 包")
        finally:
            win.close()
            del win
            del app


if __name__ == "__main__":
    unittest.main()


# 集成适配（work29 原件 → client-dev 集成版，审查方留痕）：
# 1. WORK29 → DEV_ROOT：本文件位于 client/tests/，DEV_ROOT = parents[2] 即
#    client-dev 根；PROTO_DIR = DEV_ROOT/proto/polish2（第二轮原型单独立
#    子目录——proto/ 根已有 work14 第一轮原型同名文件 proto_app.py /
#    proto_widgets.py / fake_data.py，直接覆盖会毁掉第一轮原型；shots/ 4 张
#    自检截图不落树、只留 work29/）。DESIGN.md 随原型落位
#    proto/polish2/DESIGN.md。proto_app.py 的 _PARENT 由 parent.parent 改
#    为 parent（见该文件内注释），使 import fake_data / proto_widgets 只认
#    本轮文件。
# 2. CLIENT_DEV 默认值由 ~/workspace/vpn-project/client-dev 改为 DEV_ROOT
#    （环境变量 GATE_CLIENT_DEV 仍可覆盖；VM 上两者同一路径，本机上 DEV_ROOT
#    即 ~/gate-client，锚点测试在本机也能实跑而不跳过）。
# 4. test_runtime_no_client_modules_loaded 的 harness 排除（落位形态所致，
#    非交付物问题）：原件在 work29/ 内以顶层模块形态跑，sys.modules 里本
#    无 client.*；落位后本文件即 client.tests.test_ui_polish2，测试框架
#    自身的加载会进 sys.modules。排除用例自身及其父包（client /
#    client.tests / __name__）后断言不变——原型加载 client 包仍会被抓到。
# 3. 铁律锚点重锚（task27 集成所致，非交付物问题）：orch.connect 调用点
#    cli.py 407 → 614——task27 的 collect 接线在 cmd_connect 之前插入约
#    207 行，调用点本身（cmd_connect 内 `ev = orch.connect(args.node_id)`）
#    一字未动，三处调用点仍恰 3 处且全在用户点名路径；app.py:802 /
#    usage_window.py:849 未动。其余 R1–R9/N1–N16 锚点行号对 0.31.0 实树
#    逐项复核无漂移（task27 只改 cli.py，锚点文件均未动）。
# 5. 铁律锚点再重锚（task30 集成所致，非交付物问题）：orch.connect 调用点
#    cli.py 729 → 743——task30 的登记制入库接线（_registry_path 注记 /
#    _registry 改 store 形态 / cmd_sources 迁移触发）在 cmd_connect 之前
#    插入 14 行，调用点本身一字未动，三处调用点仍恰 3 处且全在用户点名
#    路径；app.py:825 / usage_window.py:849 未动。
# 其余 38 个用例与原件逐行一致；proto/polish2 4 文件与 DESIGN.md 与原件
# md5 一致（proto_app.py 仅 _PARENT 一行适配）。
