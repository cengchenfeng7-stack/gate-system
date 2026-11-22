# ===========================================================================
# task24 修复轮锚点重基线（ui-audit-reanchor.diff 的产物源）：wiring.diff
# 合法挪动了 app.py 行号，审计锚点（文件+行号+特征串）随之平移。本文件 =
# 0.23.0 原测试 + 锚点行号按新树实测机械平移（50 处 assert_anchor + 2 处
# hits 期望行 + 2 处证据文案行号），断言特征串一字未动。集成时覆盖
# client/tests/test_ui_audit.py 或直接打 ui-audit-reanchor.diff。
# ===========================================================================
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task14 离线测试：审计锚点断言 + 原型 offscreen 冒烟。

两组口径（见 DESIGN.md §6）：
1. TestAuditAnchors / TestAuditAbsence / TestIronLaw / TestDesignDoc——
   无 Qt 依赖。DESIGN.md §1 审计表逐项断言落点真实存在：正锚点按
   「文件+行号+特征串」精确匹配（行号与 0.17.0 树实测一致，防编造）；
   缺失类问题（没搜索/没排序/没处理 connect_started）以反证断言。
   client-dev 不在位则整组 skip 并记原因。
2. TestProtoDisplayHelpers / TestProtoOffscreen——显示口径与原型主窗
   在 QT_QPA_PLATFORM=offscreen 下构造与行为断言；PySide6 不在场则
   整组 skip 并记原因，不伪造通过。

红线自查也在测试里：proto/ 不 import client 包。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

DEV_ROOT = Path(__file__).resolve().parent.parent.parent
PROTO_DIR = DEV_ROOT / "proto"
CLIENT_DEV = Path(os.environ.get("GATE_CLIENT_DEV", DEV_ROOT))
APP = CLIENT_DEV / "client" / "ui" / "app.py"
ORCH = CLIENT_DEV / "client" / "core" / "orchestrator.py"
HISTORY = CLIENT_DEV / "client" / "core" / "history.py"
ENGINE = CLIENT_DEV / "client" / "engines" / "singbox_engine.py"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, str(DEV_ROOT))

try:
    from proto.fake_data import (FakeNode, format_latency, format_recent,
                                 make_fake_nodes)
    _PROTO_OK = True
except ImportError as _exc:  # pragma: no cover
    _PROTO_OK = False
    _PROTO_SKIP = f"proto 包不可导入：{_exc}"


def _lines(path: Path):
    return path.read_text(encoding="utf-8").splitlines()


class DevTreeTestCase(unittest.TestCase):
    """client-dev 在位才跑的基类（原因如实记入 skip 消息）。"""

    def setUp(self):
        if not CLIENT_DEV.is_dir():
            self.skipTest(f"client-dev 不在位：{CLIENT_DEV}（只读参考缺失）")

    def assert_anchor(self, path: Path, line_no: int, substring: str):
        """审计表防编造的核心断言：行号处必须出现特征串（精确行）。"""
        lines = _lines(path)
        self.assertTrue(1 <= line_no <= len(lines),
                        f"{path.name}:{line_no} 超出文件行数 {len(lines)}")
        actual = lines[line_no - 1]
        self.assertIn(substring, actual,
                      f"锚点失配 {path.name}:{line_no} 应含「{substring}」，"
                      f"实际：{actual.strip()}")


class TestAuditAnchors(DevTreeTestCase):
    """DESIGN.md §1 审计表：正锚点逐项核对（文件+行号+特征串）。

    client-dev 不在位时由基类 setUp 整组 skip（原因见 skip 消息）。
    """

    def test_g1_status_line_shared_by_messages(self):
        self.assert_anchor(APP, 456, 'self.status_label = QLabel("未连接")')
        self.assert_anchor(APP, 1329, 'if ev.kind == "connected":')
        self.assert_anchor(APP, 1335,
                           'self.status_label.setText(f"已连接：{name}')
        self.assert_anchor(APP, 1429, "def _show_info(self, text: str):")
        self.assert_anchor(APP, 1430, "self.status_label.setText(text)")

    def test_g2_connect_started_unhandled(self):
        self.assert_anchor(ORCH, 104, 'self._emit("connect_started", node_id)')

    def test_g3_resort_on_every_task_finish(self):
        self.assert_anchor(APP, 1417, "self.refresh_pages()")
        self.assert_anchor(HISTORY, 125, "只决定展示顺序")
        self.assert_anchor(HISTORY, 137, "def sorted_nodes")

    def test_g4_purge_button_static_label(self):
        self.assert_anchor(APP, 500, 'QPushButton("一键删废节点")')

    def test_g5_verify_failure_lights_red_bar(self):
        self.assert_anchor(APP, 1397, 'self._show_alert(f"测速失败：{text}")')

    def test_g6_recent_column_hhmm_only(self):
        self.assert_anchor(APP, 351, 'time.strftime("%H:%M", time.localtime(last_ts))')

    def test_g11_empty_page_count_label_only(self):
        self.assert_anchor(APP, 360, 'f"{len(nodes)} 个节点"')

    def test_g12_refresh_pages_no_hidden_count(self):
        self.assert_anchor(APP, 734, "def refresh_pages(self):")
        self.assert_anchor(APP, 737, "source=src, include_banned=")

    def test_g13_action_row_and_window_width(self):
        self.assert_anchor(APP, 444, "self.resize(1080, 640)")
        self.assert_anchor(APP, 468, "actions = QHBoxLayout()")

    def test_g14_count_label_after_stretch(self):
        self.assert_anchor(APP, 249, "bar.addStretch(1)")
        self.assert_anchor(APP, 250, 'self.count_label = QLabel("")')
        self.assert_anchor(APP, 251, "bar.addWidget(self.count_label)")

    def test_g15_residual_confirm_modals(self):
        self.assert_anchor(APP, 1030, 'QMessageBox.question(self, "删除节点"')
        self.assert_anchor(APP, 1088, "QMessageBox.question(")
        self.assert_anchor(APP, 1104, "QMessageBox.question(")
        self.assert_anchor(APP, 1502, "QInputDialog.getMultiLineText(")
        self.assert_anchor(APP, 1521, "QInputDialog.getText(")

    def test_g16_raw_ms_in_health_probe_message(self):
        self.assert_anchor(APP, 1279, 'f"，延迟 {latency_ms:.0f}ms"')
        self.assert_anchor(HISTORY, 89, "def format_latency_ms")

    def test_g17_alert_bar_no_wrap_no_close(self):
        self.assert_anchor(APP, 450, 'self.alert_bar = QLabel("")')

    def test_g18_alert_slot_self_overwrite(self):
        self.assert_anchor(APP, 773, 'if node.extra.get("quarantined") == "1":')
        self.assert_anchor(APP, 783, "if not self._health_precheck(node):")

    def test_g19_no_live_table_update_during_batch(self):
        self.assert_anchor(APP, 912, "self.bridge.progress.emit(")

    def test_g20_no_binary_check_at_startup(self):
        self.assert_anchor(APP, 1176, "def _startup_health_check(self):")
        self.assert_anchor(ENGINE, 159, "未找到 sing-box 可执行文件")

    def test_g21_mode_enum_in_message(self):
        self.assert_anchor(APP, 1136, 'f"接管方式：{mode}。')
        self.assert_anchor(APP, 480, 'addItem("系统代理", "proxy")')

    def test_g22_empty_value_vocabulary(self):
        self.assert_anchor(APP, 331, 'else "未验"')
        self.assert_anchor(APP, 338, 'clean_s = "未知"')
        self.assert_anchor(APP, 342, 'stab_s = "无样本"')
        self.assert_anchor(APP, 347, 'last_s = "未测"')

    def test_g23_marks_inside_name_column(self):
        self.assert_anchor(APP, 326, 'marks = ("📌"')
        self.assert_anchor(APP, 328, 'active = "● "')

    def test_g9_pin_ban_single_select_only(self):
        self.assert_anchor(APP, 995, "def toggle_pin(self):")
        self.assert_anchor(APP, 996, "node = self.selected_node()")
        self.assert_anchor(APP, 1001, "def toggle_ban(self):")
        self.assert_anchor(APP, 1007, "def delete_selected(self):")
        self.assert_anchor(APP, 1010, "page.selected_node_ids() if page else []")

    def test_g10_doubleclick_connect_no_context_menu(self):
        self.assert_anchor(APP, 268, "window.connect_selected())")

    def test_columns_layout_evidence(self):
        self.assert_anchor(APP, 227, 'COLUMNS = ("名称", "协议", "出口", "类型",')
        self.assert_anchor(APP, 258, "self.table = QTableWidget(0, len(self.COLUMNS))")


class TestAuditAbsence(DevTreeTestCase):
    """缺失类审计项的反证断言（grep 语义）。"""

    def _all_lines(self, path: Path):
        return _lines(path)

    def test_g2_connect_started_absent_in_gui(self):
        hits = [i + 1 for i, ln in enumerate(self._all_lines(APP))
                if "connect_started" in ln]
        self.assertEqual(hits, [],
                         f"app.py 出现 connect_started 于 {hits}，"
                         "G2「事件无人处理」证据失效，须复审")

    def test_g7_no_search_box(self):
        hits = [i + 1 for i, ln in enumerate(self._all_lines(APP))
                if "QLineEdit" in ln]
        self.assertEqual(hits, [124, 127],
                         f"app.py QLineEdit 出现于 {hits}（预期仅 QSS 124/127），"
                         "G7 证据失效，须复审")

    def test_g8_no_header_sorting(self):
        hits = [i + 1 for i, ln in enumerate(self._all_lines(APP))
                if "setSortingEnabled" in ln]
        self.assertEqual(hits, [], f"app.py 出现 setSortingEnabled 于 {hits}")

    def test_g10_no_table_context_menu(self):
        hits = [i + 1 for i, ln in enumerate(self._all_lines(APP))
                if "ContextMenu" in ln]
        self.assertEqual(hits, [702],
                         f"app.py ContextMenu 出现于 {hits}（预期仅托盘 702），"
                         "G10 证据失效，须复审")

    def test_g17_g24_no_wordwrap_no_translator(self):
        for token in ("setWordWrap", "QTranslator"):
            hits = [i + 1 for i, ln in enumerate(self._all_lines(APP))
                    if token in ln]
            self.assertEqual(hits, [], f"app.py 出现 {token} 于 {hits}，"
                             "对应审计证据失效，须复审")


class TestIronLaw(DevTreeTestCase):
    """DESIGN.md §4：铁律核查（自动切换=零违例的正面证据）。"""

    def test_only_user_initiated_connect(self):
        self.assert_anchor(APP, 791, 'self._run_task("connect", lambda: orch.connect(node.id))')

    def test_dropped_copy_disclaims_auto_switch(self):
        self.assert_anchor(ORCH, 160, "已停在原地（不会自动切换）")

    def test_health_dead_copy_disclaims_auto_switch(self):
        self.assert_anchor(APP, 1254, "绝不自动切换")
        self.assert_anchor(APP, 1268, "连接保持原样、")
        self.assert_anchor(APP, 1269, "绝不自动换节点")

    def test_restore_last_node_select_only(self):
        self.assert_anchor(APP, 552, "只选中，")
        self.assert_anchor(APP, 553, "不自动连接")

    def test_sort_never_connects(self):
        self.assert_anchor(HISTORY, 125, "只决定展示顺序；连接永远由用户点选（铁律）")


class TestDesignDoc(unittest.TestCase):
    """DESIGN.md 与代码互证：审计表条目齐全（防表格/测试漂移）。"""

    def test_design_md_exists(self):
        self.assertTrue((PROTO_DIR / "DESIGN.md").is_file())

    def test_all_audit_ids_listed(self):
        text = (PROTO_DIR / "DESIGN.md").read_text(encoding="utf-8")
        for i in range(1, 25):
            self.assertIn(f"| G{i} |", text, f"DESIGN.md 缺审计项 G{i}")
        for sev in ("挡路", "难受", "难看"):
            self.assertIn(sev, text, f"DESIGN.md 缺严重度档 {sev}")

    def test_u1_section_present(self):
        text = (PROTO_DIR / "DESIGN.md").read_text(encoding="utf-8")
        self.assertIn("给 U1 的输入", text)
        self.assertIn("应避开的毛病", text)
        self.assertIn("应直接复用", text)


try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    _QT = True
    _QT_SKIP = ""
except ImportError as _exc:  # VM 无 PySide6：原型组整组跳过并记原因
    _QT = False
    _QT_SKIP = f"无 PySide6（{_exc}），原型 offscreen 测试整组跳过"

if _QT and _PROTO_OK:
    from proto.proto_app import ProtoMainWindow, shoot
    from proto.proto_widgets import (SEV_DANGER, AlertBar, EmptyStateCard,
                                     MessageLine, PurgeButton, StatusPanel)
    from proto.proto_source_demo import COLUMNS, ProtoSourcePage
    from proto.proto_empty_demo import EmptyStateDemo, InlineConfirmBar


@unittest.skipUnless(_PROTO_OK, "proto 包不可导入")
class TestProtoDisplayHelpers(unittest.TestCase):
    """显示口径（DESIGN §2 对 G6/G16/G22 的答案）——不需要 Qt 也成立。"""

    def test_latency_032_scale(self):
        self.assertEqual(format_latency(182), "182ms")
        self.assertEqual(format_latency(1260), "1.3 秒")
        self.assertEqual(format_latency(5745), "5.7 秒")
        self.assertEqual(format_latency(23147), "23 秒")
        self.assertEqual(format_latency(None), "—")

    def test_recent_has_date(self):
        text = format_recent(1700000000.0)
        self.assertIn("/", text)
        self.assertRegex(text, r"\d+/\d+ \d+:\d+")
        self.assertEqual(format_recent(None), "—")


@unittest.skipUnless(_QT, "需要 PySide6 才能构造原型窗体")
@unittest.skipUnless(_PROTO_OK, "proto 包不可导入")
class TestProtoOffscreen(unittest.TestCase):
    """原型主窗 offscreen 构造 + 关键控件与行为断言。"""

    @classmethod
    def setUpClass(cls):
        if not QApplication.instance():
            cls._owned_app = QApplication(sys.argv[:1])
        else:
            cls._owned_app = None

    @classmethod
    def tearDownClass(cls):
        if cls._owned_app is not None:
            cls._owned_app.quit()

    def setUp(self):
        self.window = ProtoMainWindow()

    def tearDown(self):
        self.window.deleteLater()

    # ---- 主窗骨架 ----
    def test_window_constructs_with_three_demo_tabs(self):
        self.assertEqual(self.window.tabs.count(), 3)
        self.assertIn("状态呈现改版", self.window.tabs.tabText(0))
        self.assertIn("来源页改版", self.window.tabs.tabText(1))
        self.assertIn("空态与待清", self.window.tabs.tabText(2))

    # ---- 演示一：状态呈现（G1/G2/G5/G17）----
    def test_status_panel_three_write_points(self):
        panel = self.window.status_demo.status_panel
        self.assertEqual(panel.state_text(), "未连接")
        panel.set_connecting("示例节点 · 甲")
        self.assertIn("连接中", panel.state_text())
        panel.set_connected("示例节点 · 甲")
        self.assertIn("已连接：示例节点 · 甲", panel.state_text())
        panel.set_idle()
        self.assertEqual(panel.state_text(), "未连接")

    def test_message_line_never_touches_status_panel(self):
        demo = self.window.status_demo
        demo.status_panel.set_connected("示例节点 · 甲")
        demo.message_line.show_message("自动探测完成：可连 3 · 不通 2")
        self.assertIn("已连接", demo.status_panel.state_text(),
                      "消息写进状态面板 = G1 复发")
        self.assertIn("自动探测完成", demo.message_line.text())

    def test_alert_bar_severity_wrap_and_close(self):
        self.window.show()          # offscreen 下可见性才有效
        bar = self.window.status_demo.alert_bar
        self.assertFalse(bar.is_showing())
        bar.show_alert("启动被挡：" + "长" * 90 + "。", SEV_DANGER)
        self.assertTrue(bar.is_showing())
        self.assertTrue(bar.text_label.wordWrap(), "告警条必须换行（G17）")
        bar.close_btn.click()
        self.assertFalse(bar.is_showing())

    # ---- 演示二：来源页（G6/G7/G8/G10/G12/G22/G23）----
    def test_source_page_controls_complete(self):
        page = self.window.source_page
        self.assertIsInstance(page.search_edit.text(), str)
        self.assertEqual(page.table.columnCount(), len(COLUMNS))
        self.assertEqual(page.table.columnCount(), 10)
        self.assertIn("标记", COLUMNS)
        self.assertEqual(page.sort_combo.count(), 3)

    def test_source_page_default_rows_and_count(self):
        page = self.window.source_page
        # 12 个假节点：1 拉黑 + 1 隔离默认隐藏 → 10 行
        self.assertEqual(page.table.rowCount(), 10)
        self.assertEqual(page.count_label.text(), "10 个节点")

    def test_search_filters_with_hidden_count_card(self):
        page = self.window.source_page
        self.window.tabs.setCurrentIndex(1)     # 切到来源页，QTabWidget 才显示它
        self.window.show()
        page.search_edit.setText("不存在xyz")
        self.assertEqual(page.table.rowCount(), 0)
        self.assertTrue(page.empty_card.isVisible())
        self.assertIn("被过滤隐藏了", page.empty_card.title_label.text())
        page.clear_filters()
        # 清空过滤 = 全部开关复原，连默认隐藏的拉黑/隔离 2 个也回来了
        self.assertEqual(page.table.rowCount(), 12)

    def test_search_by_name(self):
        page = self.window.source_page
        page.search_edit.setText("壬")
        self.assertEqual(page.table.rowCount(), 1)
        page.clear_filters()

    def test_recent_cell_has_date_latency_humanized(self):
        page = self.window.source_page
        target = None
        for r in range(page.table.rowCount()):
            if "丁" in page.table.item(r, 0).text():
                target = r
                break
        self.assertIsNotNone(target, "假数据里应有高延迟节点 丁")
        self.assertEqual(page.table.item(target, 6).text(), "5.7 秒")
        self.assertRegex(page.table.item(target, 8).text(), r"\d+/\d+ \d+:\d+")

    def test_context_menu_actions(self):
        menu = self.window.source_page.build_context_menu()
        texts = [a.text() for a in menu.actions()]
        self.assertEqual(texts, ["连接", "测速", "置顶/取消", "拉黑/取消", "删除"])

    def test_sort_by_latency(self):
        page = self.window.source_page
        page.sort_combo.setCurrentIndex(1)      # 延迟 ↑
        first_name = page.table.item(0, 0).text()
        self.assertIn("甲", first_name, "182ms 应排最前")
        page.sort_combo.setCurrentIndex(0)

    def test_marks_out_of_name_column(self):
        page = self.window.source_page
        for r in range(page.table.rowCount()):
            name = page.table.item(r, 0).text()
            for mark in ("📌", "🚫", "☠", "●", "▲", "⛔"):
                self.assertNotIn(mark, name,
                                 f"名称列出现标记 {mark}（G23 复发）：{name}")

    # ---- 演示三：空态与待清（G4/G11/G12/G15）----
    def test_empty_library_card_with_import_guidance(self):
        demo = EmptyStateDemo()
        demo.show()                 # offscreen 下可见性才有效
        demo._show_empty_library()
        self.assertTrue(demo.empty_card.isVisible())
        self.assertIn("还没有节点", demo.empty_card.title_label.text())
        self.assertEqual(demo.empty_card._action_row.count(), 5)  # 4 按钮+stretch
        demo.deleteLater()

    def test_filtered_out_card(self):
        demo = EmptyStateDemo()
        demo.show()
        demo._show_filtered_out()
        self.assertIn("被过滤隐藏了 5 个", demo.empty_card.title_label.text())
        self.assertTrue(demo.empty_card.clear_btn.isVisible())
        demo.deleteLater()

    def test_purge_button_pending_count_visible(self):
        btn = PurgeButton()
        self.assertEqual(btn.text(), "删本页废节点")
        btn.set_pending(3)
        self.assertIn("（3）", btn.text())
        self.assertIn("上一轮", btn.text())
        btn.set_pending(0)
        self.assertEqual(btn.text(), "删本页废节点")
        btn.deleteLater()

    def test_inline_confirm_replaces_modal(self):
        demo = EmptyStateDemo()
        demo.show()
        demo.purge_btn.set_pending(3)
        demo._on_purge()
        self.assertTrue(demo.confirm_bar.isVisible())
        fired = []
        demo.confirm_bar.confirmed.connect(lambda: fired.append(1))
        demo.confirm_bar.yes_btn.click()
        self.assertEqual(fired, [1])
        self.assertFalse(demo.confirm_bar.isVisible())
        self.assertEqual(demo.purge_btn.pending, 0)
        demo.deleteLater()

    # ---- offscreen 截图自检通路 ----
    def test_shoot_writes_pngs(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = shoot(self.window, tmp)
            self.assertEqual(len(paths), 4)
            for p in paths:
                self.assertTrue(Path(p).is_file() and Path(p).stat().st_size > 0,
                                f"截图缺失或为空：{p}")

    # ---- 红线：原型不 import client 包、不用模态框 ----
    def test_proto_never_imports_client_package(self):
        proto_dir = PROTO_DIR
        offenders = []
        for py in proto_dir.glob("*.py"):
            for i, ln in enumerate(_lines(py)):
                if ln.startswith(("import client", "from client")) \
                        or " client." in ln and "import" in ln:
                    offenders.append(f"{py.name}:{i + 1}")
        self.assertEqual(offenders, [], f"原型文件 import client 包：{offenders}")

    def test_proto_has_no_modal_dialogs(self):
        proto_dir = PROTO_DIR
        offenders = []
        for py in proto_dir.glob("*.py"):
            for i, ln in enumerate(_lines(py)):
                if "QMessageBox" in ln or "QInputDialog" in ln:
                    offenders.append(f"{py.name}:{i + 1}")
        self.assertEqual(offenders, [],
                         f"原型出现模态对话框（G15 改版口径）：{offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)


# 集成适配（work14 原件 → client-dev 集成版的全部改动，审查方留痕）：
# 1. WORK14 → DEV_ROOT：本文件位于 client/tests/，DEV_ROOT = parents[2] 即
#    client-dev 根；PROTO_DIR = DEV_ROOT/proto（proto/ 7 个 py 文件与 DESIGN.md
#    一并落位 client-dev/proto/，shots/ 4 张自检截图不落树、只留 work14/）。
# 2. sys.path 插入 DEV_ROOT：`from proto...` 导入保持原样（proto 内部互引
#    也是 `from proto...` 绝对导入，落位后零改动；红线测试仍断言 proto 不
#    import client 包）。
# 3. DESIGN.md 读取路径由 WORK14/DESIGN.md 改为 PROTO_DIR/DESIGN.md。
# 4. CLIENT_DEV 默认值由 ~/workspace/vpn-project/client-dev 改为 DEV_ROOT
#   （环境变量 GATE_CLIENT_DEV 仍可覆盖；VM 上两者同一路径，本机上 DEV_ROOT
#    即 ~/gate-client，锚点测试在本机也能实跑而不跳过）。
# 其余 56 个用例与原件逐行一致；proto/ 7 文件与 DESIGN.md 与原件 md5 一致。
