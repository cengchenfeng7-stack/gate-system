#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""usage_window（U1 前台使用窗）离线测试：offscreen Qt + 假引擎/假 orchestrator。
================================================================简报口径：经 sys.path 只读引用 client-dev 的真实 core 模块（Store 用
:memory:、classify_proto、history），引擎/orchestrator 用假替身注入。
两组：
1. 锚点组（无 Qt 依赖）：复用点/铁律口径断言——任何环境都实跑；
2. offscreen 组（PySide6 缺席则整组 skip 并在 DONE 记原因，不伪造通过）：
   QT_QPA_PLATFORM=offscreen 真构造窗口，假数据驱动状态机/分组/行内动作。

红线自查内嵌：禁词（work10 §5.5 四禁词 + 优选）、行内动作恰为
{连接, 置顶, 测速}、orchestrator 铁律结构零改动。
"""
import importlib.util
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
QT_QPA = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _find_client_dev():
    """client-dev 只读引用定位：CLIENT_DEV 环境变量优先，其次仓库相对路径。"""
    env = os.environ.get("CLIENT_DEV")
    if env and os.path.isdir(env):
        return env
    cand = os.path.abspath(os.path.join(HERE, "..", ".."))  # client/tests -> client-dev 根
    if os.path.isdir(cand):
        return cand
    return None


CLIENT_DEV = _find_client_dev()
if CLIENT_DEV and CLIENT_DEV not in sys.path:
    sys.path.insert(0, CLIENT_DEV)

try:
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication, QPushButton
    _QT = True
except ImportError:
    _QT = False

# 锚点组只需要 client-dev 在位（读文件断言），不需要 Qt。
_HAS_DEV = CLIENT_DEV is not None

if _HAS_DEV:
    import client
    import client.ui
    # 集成后 usage_window 已是 client.ui 的真包成员，直接 import。
    import client.core.classify_proto as classify_proto
    import client.core.history as history
    import client.core.orchestrator as orch_mod
    import client.ui.usage_window as usage_mod   # 无 PySide6 也能 import（内部降级）
    from client.core.model import ExitProfile, Node
    from client.core.store import Store
else:
    classify_proto = history = orch_mod = None
    ExitProfile = Node = Store = None
    usage_mod = None


def seed(store):
    """假数据 only（RFC 5737 保留 IP / 虚构名称），覆盖七板块：六板块各一、
    免费住宅两台（测置顶排序）、旧来源键一台（task8 合并兼容）、未分类两台
    （缺来源性质 / 缺出口 ISP——不许硬塞）、已拉黑一台（任何板块不出现）。"""
    store.upsert_node(Node(id="fr1", name="日本家宽节点", protocol="vless",
                           source="vpngate", host="198.51.100.1", port=443,
                           scenes={"ai"},
                           exit_profile=ExitProfile(
                               ip="203.0.113.10", country="日本",
                               country_code="JP", network_type="residential",
                               org="NTT EAST", asn="AS2513", is_clean=True)))
    store.upsert_node(Node(id="fr2", name="大阪家宽节点", protocol="vless",
                           source="vpngate", host="198.51.100.2", port=443,
                           exit_profile=ExitProfile(
                               ip="203.0.113.20", country="日本",
                               country_code="JP", network_type="residential",
                               org="KDDI", is_clean=True)))
    store.upsert_node(Node(id="fd1", name="机房节点", protocol="ss",
                           source="vpngate", host="198.51.100.3", port=8388,
                           exit_profile=ExitProfile(
                               ip="203.0.113.11", country="美国",
                               country_code="US", network_type="datacenter",
                               org="DIGITALOCEAN", is_clean=True)))
    store.upsert_node(Node(id="ov1", name="隧道节点", protocol="openvpn",
                           source="openvpn", host="198.51.100.4", port=1194))
    store.upsert_node(Node(id="pl1", name="流水线节点", protocol="ss",
                           source="pipeline", host="198.51.100.5", port=8388,
                           exit_profile=ExitProfile(
                               ip="203.0.113.12", country="新加坡",
                               country_code="SG", org="OVH", is_clean=True)))
    store.upsert_node(Node(id="pr1", name="付费住宅节点", protocol="trojan",
                           source="manual", host="198.51.100.6", port=443,
                           extra={"nature": "paid"},
                           exit_profile=ExitProfile(
                               ip="203.0.113.13", country="美国",
                               country_code="US", org="COMCAST", is_clean=True)))
    store.upsert_node(Node(id="pd1", name="付费机房节点", protocol="vmess",
                           source="manual", host="198.51.100.7", port=443,
                           extra={"nature": "paid"},
                           exit_profile=ExitProfile(
                               ip="203.0.113.14", country="德国",
                               country_code="DE", org="HETZNER", is_clean=True)))
    store.upsert_node(Node(id="lg1", name="旧键免费住宅", protocol="vless",
                           source="vpngate_feed", host="198.51.100.8", port=443,
                           exit_profile=ExitProfile(
                               ip="203.0.113.15", country="美国",
                               country_code="US", org="COMCAST", is_clean=True)))
    store.upsert_node(Node(id="uc1", name="没标性质的手动节点", protocol="ss",
                           source="manual", host="198.51.100.9", port=8388,
                           exit_profile=ExitProfile(
                               ip="203.0.113.16", org="COMCAST", is_clean=True)))
    store.upsert_node(Node(id="uc2", name="出口未知节点", protocol="vless",
                           source="vpngate", host="198.51.100.10", port=443))
    store.upsert_node(Node(id="bn1", name="已拉黑节点", protocol="ss",
                           source="vpngate", host="198.51.100.11", port=8388,
                           banned=True))
    # 养鱼池样本（延迟展示走 history 口径）
    history.record(store, "fr1", "verify", True, latency_ms=250)


if _HAS_DEV:

    class FakeEngine:
        """假引擎替身：只记启动/停止（预检的 isinstance(SingBoxEngine)
        分支自然走「非 sing-box」路径，端口预检退化为系统代理检查）。"""

        def __init__(self):
            self.started = []
            self.alive = True

        def start(self, node, on_event=None):
            self.started.append(node.id)
            self.alive = True
            return {"node_id": node.id}

        def stop(self, handle):
            self.alive = False

        def status(self, handle):
            return "running" if self.alive else "stopped"

    class FakeOrchestrator:
        """假编排器替身：状态/事件口径与真 Orchestrator 同款（SessionEvent
        复用真数据类），connect/disconnect/poll 按测试预演。铁律同款：
        已连接再 connect 抛错；绝不自动换节点。"""

        IDLE = orch_mod.Orchestrator.IDLE
        CONNECTED = orch_mod.Orchestrator.CONNECTED

        def __init__(self, store, engines):
            self.store = store
            self.engines = engines
            self.state = self.IDLE
            self.active_node_id = None
            self.on_event = None
            self.fail_next = None      # "verify_failed" / "error" / None
            self.drop_on_poll = False

        def _emit(self, kind, node_id="", detail=""):
            ev = orch_mod.SessionEvent(kind=kind, node_id=node_id, detail=detail)
            if self.on_event:
                self.on_event(ev)
            return ev

        def connect(self, node_id):
            if self.state != self.IDLE:
                raise orch_mod.OrchestratorError(
                    f"已有活动连接（{self.active_node_id}），先断开再连别的节点")
            node = self.store.get_node(node_id)
            if node is None:
                raise orch_mod.OrchestratorError(f"节点不存在: {node_id}")
            self._emit("connect_started", node_id)
            if self.fail_next == "error":
                self.fail_next = None
                return self._emit("error", node_id, "引擎启动失败: 假引擎")
            if self.fail_next == "verify_failed":
                self.fail_next = None
                return self._emit("verify_failed", node_id,
                                  "已断开（防假连）：出口 IP 没变（假连）")
            self.state = self.CONNECTED
            self.active_node_id = node_id
            return self._emit("connected", node_id, "出口 203.0.113.10 / JP")

        def disconnect(self):
            if self.state == self.IDLE:
                return self._emit("disconnected", "", "本来就没有活动连接")
            node_id = self.active_node_id or ""
            self.state = self.IDLE
            self.active_node_id = None
            return self._emit("disconnected", node_id)

        def poll(self):
            if self.state == self.CONNECTED and self.drop_on_poll:
                node_id = self.active_node_id or ""
                self.state = self.IDLE
                self.active_node_id = None
                self.drop_on_poll = False
                return self._emit("dropped", node_id,
                                  "节点掉线，已停在原地（不会自动切换）")
            return None

    @unittest.skipUnless(_QT, "无 PySide6，offscreen 组整组跳过（原因记 DONE.md），不伪造通过")
    class TestUsageWindow(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            cls.app = QApplication.instance() or QApplication([])

        def make_window(self):
            store = Store(":memory:")
            seed(store)
            engines = {p: FakeEngine()
                       for p in ("vless", "ss", "vmess", "trojan", "openvpn")}
            orch = FakeOrchestrator(store, engines)
            win = usage_mod.UsageWindow(store, orchestrator=orch, engines=engines)

            def sync_run_task(tag, fn):
                try:
                    result = (True, fn())
                except Exception as exc:  # noqa: BLE001 - 与 _Worker 兜底同口径
                    result = (False, exc)
                win._on_task_finished(tag, result)

            win._run_task = sync_run_task   # 慢操作同步替身（行为路径与真实一致）
            self.addCleanup(win.close)
            return win, orch, store

        def select(self, win, board_key, node_id):
            page = win.boards[board_key]
            for row in range(page.table.rowCount()):
                item = page.table.item(row, 0)
                if item and item.data(Qt.UserRole) == node_id:
                    page.table.setCurrentCell(row, 0)
                    return page
            raise AssertionError(f"板块 {board_key} 里找不到节点 {node_id}")

        # ---------------- 窗口可构造 + 结构 ----------------
        def test_window_constructs(self):
            win, _orch, _store = self.make_window()
            self.assertEqual(win.windowTitle(), "gate-client · 使用界面")
            self.assertEqual(win.session_panel.state, "未连接")
            # 七板块：六板块 + 未分类，顺序=BOARDS 定稿 + 未分类收尾
            self.assertEqual(list(win.boards),
                             list(classify_proto.BOARDS)
                             + [classify_proto.UNCLASSIFIED])
            self.assertEqual(win.tabs.count(), 7)

        def test_menu_skeleton_three_items(self):
            # task22 接线后：第三项从单条「诊断导出」动作变为「诊断」菜单，
            # 内含「一键诊断」「导出诊断包」两个动作（work22/DONE.md）。
            win, _orch, _store = self.make_window()
            labels = [a.text() for a in win.menuBar().actions()]
            self.assertEqual(labels, ["打开后台", "设置", "诊断"])
            diag_texts = [a.text() for a in win.menu_diag.actions()]
            self.assertEqual(diag_texts, ["一键诊断", "导出诊断包"])

        # ---------------- ② 区分组（六板块 + 未分类） ----------------
        def test_board_grouping(self):
            win, _orch, _store = self.make_window()
            groups = {k: set(win.boards[k].visible_node_ids())
                      for k in win.boards}
            self.assertEqual(groups["free_residential"], {"fr1", "fr2", "lg1"})
            self.assertEqual(groups["free_datacenter"], {"fd1"})
            self.assertEqual(groups["openvpn"], {"ov1"})
            self.assertEqual(groups["pipeline"], {"pl1"})
            self.assertEqual(groups["paid_residential"], {"pr1"})
            self.assertEqual(groups["paid_datacenter"], {"pd1"})
            # 判不出维度不许硬塞：缺来源性质 / 缺出口 ISP 都落未分类
            self.assertEqual(groups["unclassified"], {"uc1", "uc2"})
            # 已拉黑节点不进任何板块
            for ids in groups.values():
                self.assertNotIn("bn1", ids)

        def test_board_sort_pinned_first(self):
            win, _orch, store = self.make_window()
            # fr1 有成功样本排最前（最近成功优先），其余无样本按名称序；
            # 置顶后必须排最前（history.sorted_nodes 口径：置顶 > 最近成功）
            order = win.boards["free_residential"].visible_node_ids()
            self.assertEqual(order, ["fr1", "fr2", "lg1"])
            store.set_pinned("fr2", True)
            win.refresh_boards()
            self.assertEqual(win.boards["free_residential"].visible_node_ids(),
                             ["fr2", "fr1", "lg1"])

        def test_row_actions_exact_set(self):
            win, _orch, _store = self.make_window()
            for key, page in win.boards.items():
                self.assertEqual(
                    set(page.ROW_ACTIONS),
                    {"连接", "置顶", "测速", "测特殊站"},
                    msg=f"板块 {key} 行内动作越界")
                self.assertEqual([t for t, _slot in page.row_action_bindings()],
                                 ["连接", "置顶", "测速", "测特殊站"])

        def test_no_supply_side_entries(self):
            """使用界面永不出现供给侧/处置动作（work10 §2.3 边界清单）。"""
            win, _orch, _store = self.make_window()
            forbidden = ["导入", "登记", "拉黑", "删除", "搜集", "改判",
                         "验真管理", "更新来源", "优选",
                         "connect_best", "connect_fastest"]
            surfaces = [a.text() for a in win.menuBar().actions()]
            surfaces += [b.text() for b in win.findChildren(QPushButton)]
            surfaces += [t for page in win.boards.values()
                         for t, _slot in page.row_action_bindings()]
            surfaces += [page.empty_hint.text() for page in win.boards.values()]
            for text in surfaces:
                for word in forbidden:
                    self.assertNotIn(word, text,
                                     msg=f"界面文案出现禁区词「{word}」：{text}")

        def test_banned_nodes_hidden(self):
            win, _orch, _store = self.make_window()
            for page in win.boards.values():
                self.assertNotIn("bn1", page.visible_node_ids())

        # ---------------- ① 区状态随会话状态切换 ----------------
        def test_state_transitions_connect_disconnect(self):
            win, orch, store = self.make_window()
            self.assertEqual(win._session_state, "未连接")
            page = self.select(win, "free_residential", "fr1")
            # 双击=连接的入口与按钮走同一管道；这里点主按钮路径
            win.connect_selected()
            self.assertEqual(orch.state, orch.CONNECTED)
            self.assertEqual(win._session_state, "已连接")
            self.assertEqual(win.session_panel.state, "已连接")
            # 当前节点行：实测名（exit_display_name 口径）+ 板块徽标 + 场景徽标
            # + 延迟（format_latency_ms 口径）
            detail = win.node_detail.text()
            self.assertIn("日本 · 203.0.113.10 · NTT EAST", detail)
            self.assertIn("[板块：免费住宅]", detail)
            self.assertIn("[场景：AI]", detail)
            self.assertIn("延迟：250ms", detail)
            fr_row = page.visible_node_ids().index("fr1")
            self.assertTrue(page.table.item(fr_row, 0).text().startswith("● "))
            # 连接中禁用连接与接管方式切换
            self.assertFalse(win.connect_btn.isEnabled())
            self.assertFalse(win.mode_combo.isEnabled())
            win.disconnect()
            self.assertEqual(orch.state, orch.IDLE)
            self.assertEqual(win._session_state, "未连接")
            self.assertEqual(win.node_detail.text(), "")
            self.assertTrue(win.mode_combo.isEnabled())

        def test_state_connect_started_shown(self):
            """connect_started 事件必须有呈现分支（work14 §5.1-2）。"""
            win, orch, _store = self.make_window()
            self.select(win, "free_residential", "fr1")

            def fake_connect(node_id):
                orch._emit("connect_started", node_id)
                return orch_mod.SessionEvent(kind="connect_started",
                                             node_id=node_id)

            orch.connect = fake_connect
            win.connect_selected()
            self.assertEqual(win._session_state, "连接中")
            self.assertIn("正在连接", win.message_line.text())

        def test_state_engine_started_shown(self):
            win, orch, _store = self.make_window()
            orch._emit("engine_started", "fr1")
            self.assertIn("引擎已启动", win.message_line.text())

        def test_state_error_shows_dropped_alert(self):
            win, orch, _store = self.make_window()
            self.select(win, "free_residential", "fr1")
            orch.fail_next = "error"
            win.connect_selected()
            self.assertEqual(win._session_state, "已断开")
            self.assertFalse(win.alert_bar.isHidden())
            self.assertIn("连接失败", win.alert_bar.text())

        def test_state_verify_failed_shows_alert(self):
            win, orch, _store = self.make_window()
            self.select(win, "free_residential", "fr1")
            orch.fail_next = "verify_failed"
            win.connect_selected()
            self.assertEqual(win._session_state, "已断开")
            self.assertIn("假连", win.alert_bar.text())

        def test_state_dropped_via_poll(self):
            win, orch, _store = self.make_window()
            self.select(win, "free_residential", "fr1")
            win.connect_selected()
            orch.drop_on_poll = True
            win.poll_once()
            self.assertEqual(win._session_state, "已断开")
            self.assertIn("掉线", win.alert_bar.text())
            # 掉线后停在原地（不自动重连/不自动换节点），状态不回未连接
            win.poll_once()
            self.assertEqual(win._session_state, "已断开")

        def test_state_reconcile_heals_missed_event(self):
            """漏事件兜底：poll_once 以 orchestrator 状态为准自愈①区。"""
            win, orch, _store = self.make_window()
            orch.state = orch.CONNECTED
            orch.active_node_id = "fd1"
            win.poll_once()
            self.assertEqual(win._session_state, "已连接")
            self.assertIn("[板块：免费机房]", win.node_detail.text())

        def test_mode_combo_disabled_while_connected(self):
            """连接中接管方式切换被禁用（简报口径）。"""
            win, orch, _store = self.make_window()
            self.assertTrue(win.mode_combo.isEnabled())
            self.select(win, "free_residential", "fr1")
            win.connect_selected()
            self.assertFalse(win.mode_combo.isEnabled())
            self.assertEqual(win.mode_combo.toolTip(), "先断开，再改接管方式")
            win.disconnect()
            self.assertTrue(win.mode_combo.isEnabled())

        # ---------------- ② 区库变更对账（同一 SQLite） ----------------
        def test_board_refresh_on_store_change(self):
            win, _orch, store = self.make_window()
            before = win.boards["paid_residential"].visible_node_ids()
            self.assertNotIn("pr2", before)
            store.upsert_node(Node(id="pr2", name="新付费住宅节点", protocol="ss",
                                   source="manual", host="198.51.100.30",
                                   port=443, extra={"nature": "paid"},
                                   exit_profile=ExitProfile(
                                       ip="203.0.113.30", org="KDDI",
                                       is_clean=True)))
            win.poll_once()   # 库计数变化 → 重排板块
            self.assertIn("pr2", win.boards["paid_residential"].visible_node_ids())

        # ---------------- 行内动作行为 ----------------
        def test_pin_action_toggles_display(self):
            win, _orch, store = self.make_window()
            page = self.select(win, "free_datacenter", "fd1")
            first = win.boards["free_residential"].visible_node_ids()[0]
            # 置顶 free_datacenter 的节点不影响其他板块顺序（板块内排序）
            page.window.toggle_pin_from_page(page)
            self.assertTrue(store.get_node("fd1").pinned)
            name_item = page.table.item(0, 0)
            self.assertIn("📌", name_item.text())
            self.assertEqual(
                win.boards["free_residential"].visible_node_ids()[0], first)

        def test_row_action_connect_uses_same_pipeline(self):
            """行内「连接」与主按钮走同一 connect_node 管道。"""
            win, orch, _store = self.make_window()
            page = self.select(win, "pipeline", "pl1")
            bindings = dict(page.row_action_bindings())
            bindings["连接"]()
            self.assertEqual(orch.state, orch.CONNECTED)
            self.assertEqual(orch.active_node_id, "pl1")
            self.assertEqual(win._session_state, "已连接")
            self.assertIn("[板块：私人流水线]", win.node_detail.text())

        # ---------------- 测速管道（同 _test_node 口径） ----------------
        def test_test_node_openvpn_skips_honestly(self):
            win, _orch, _store = self.make_window()
            kind, text = win._test_node("ov1")
            self.assertEqual(kind, "skip")
            self.assertIn("OpenVPN", text)

        def test_test_node_missing_node(self):
            win, _orch, _store = self.make_window()
            kind, text = win._test_node("nope")
            self.assertEqual(kind, "verify_failed")
            self.assertEqual(text, "节点不存在")

        # ---------------- 菜单：未接线项如实提示 ----------------
        def test_settings_stub_honest_diag_actions_wired(self):
            # task22 接线后：设置页仍是未接线 stub（点击如实提示）；诊断
            # 两项已接线——本测试只断言动作在（触发会起后台线程/弹结果框，
            # 行为测试见 work22/test_diag_wiring.py 的 GUI 组，不在这里
            # 触发，免起线程/弹模态框）。
            win, _orch, _store = self.make_window()
            # task24 落页后：设置菜单在有 settings_store 的真实应用形态打开
            # 统一设置页；本测试是无 settings_store 的离线替身形态 → 点击
            # 如实提示「设置存储不可用」不假开（诚实口径延续）。
            win.act_settings.trigger()
            self.assertIn("设置存储不可用", win.message_line.text())
            self.assertTrue(win.act_doctor.isEnabled())
            self.assertTrue(win.act_export_diag.isEnabled())

        # ---------------- 两窗共用 Store/Orchestrator ----------------
        def test_open_backend_shares_instances_and_events(self):
            win, orch, store = self.make_window()
            win._open_backend()
            backend = win._backend_window
            self.assertIsNotNone(backend)
            # 同一实例，不许各开一个连接（简报红线）
            self.assertIs(backend.orchestrator, orch)
            self.assertIs(backend.store, store)
            # 事件通道两窗都收得到（链式并入：主窗先收、后台窗随后）
            orch._emit("connected", "fd1", "出口 203.0.113.11 / US")
            self.assertEqual(win._session_state, "已连接")
            self.assertIn("已连接", backend.status_label.text())

    @unittest.skipUnless(_QT, "无 PySide6，offscreen 组整组跳过（原因记 DONE.md），不伪造通过")
    class TestStandaloneBuild(unittest.TestCase):
        """独立冒烟模式：orchestrator=None 时经 _build_orchestrator 自建
        （cli.py:264 同款管道，真实 Orchestrator + 真实引擎构造）。"""

        @classmethod
        def setUpClass(cls):
            cls.app = QApplication.instance() or QApplication([])

        def test_builds_real_orchestrator(self):
            store = Store(":memory:")
            seed(store)
            win = usage_mod.UsageWindow(store, probe_factory=lambda: None)
            self.addCleanup(win.close)
            self.assertIsInstance(win.orchestrator, orch_mod.Orchestrator)
            self.assertIn("openvpn", win.engines)
            self.assertIn("vless", win.engines)
            # 真实 Orchestrator 的铁律守卫原样生效：重复 connect 被拒
            # （不真启动引擎：引擎 start 在 orchestrator 拒绝之前不会被调）
            self.assertRaises(
                orch_mod.OrchestratorError,
                win.orchestrator.connect, "不存在的节点")


# ---------------------------------------------------------------------------
# 锚点组（无 Qt 依赖，任何环境实跑）
# ---------------------------------------------------------------------------
@unittest.skipUnless(_HAS_DEV, "client-dev 不在位，跳过锚点组")
class TestSourceAnchors(unittest.TestCase):
    """对 usage_window.py 源码断言复用点与铁律口径（审查方不看代码也能
    核对 DESIGN.md 清单是否落了地）。"""

    @classmethod
    def setUpClass(cls):
        path = usage_mod.__file__
        with open(path, "r", encoding="utf-8") as fh:
            cls.src = fh.read()

    def test_reuses_app_assets_via_import(self):
        for anchor in ("from .app import", "_Bridge", "_Worker",
                       "SCENE_LABELS", "NETWORK_LABELS", "DARK_QSS", "_dot_icon"):
            self.assertIn(anchor, self.src, msg=f"缺复用锚点：{anchor}")

    def test_board_grouping_uses_classify_proto(self):
        self.assertIn("classify_proto.board_of(classify_proto.dims_of(", self.src)
        self.assertIn("classify_proto.BOARDS", self.src)
        self.assertIn("classify_proto.UNCLASSIFIED", self.src)

    def test_display_sort_uses_history(self):
        self.assertIn("history.sorted_nodes(", self.src)
        self.assertIn("history.format_latency_ms(", self.src)

    def test_node_name_uses_exit_display_name(self):
        self.assertIn("exit_display_name(", self.src)

    def test_connect_pipeline_uses_orchestrator_with_worker(self):
        self.assertIn("orch.connect(node.id)", self.src)
        self.assertIn("_run_task(\"connect\"", self.src)

    def test_row_actions_constant_exact(self):
        self.assertIn('ROW_ACTIONS = ("连接", "置顶", "测速", "测特殊站")',
                      self.src)

    def test_iron_law_forbidden_words_absent(self):
        """work10 §5.5 四禁词 + 优选：全文件零命中。"""
        for word in ("connect_best", "connect_fastest", "优选",
                     "按延迟排序备用组", "定时延迟对比换节点"):
            self.assertNotIn(word, self.src, msg=f"铁律禁词命中：{word}")

    def test_auto_switch_only_in_negations(self):
        """「自动切换」只许出现在否定句里（铁律声明），不许出现肯定语义。"""
        for line in self.src.splitlines():
            if "自动切换" in line:
                self.assertTrue(
                    any(neg in line for neg in ("不", "绝不", "永不")),
                    msg=f"疑似肯定语义的自动切换：{line.strip()}")

    def test_orchestrator_structure_untouched(self):
        """orchestrator 铁律结构零改动：本文件只允许读写 on_event 单槽，
        不许改 connect/disconnect/poll 行为、不许新增 connect_* 语义方法。"""
        self.assertNotIn("def connect_best", self.src)
        self.assertNotIn("def connect_fastest", self.src)
        # on_event 的写点只允许出现在监听挂载处
        writes = [ln for ln in self.src.splitlines()
                  if "orchestrator.on_event" in ln and "=" in ln
                  and "==" not in ln]
        self.assertTrue(len(writes) <= 2,
                        msg=f"on_event 写点过多：{writes}")

    def test_design_doc_present_with_integration_checklist(self):
        path = os.path.join(os.path.dirname(usage_mod.__file__), "DESIGN.md")
        with open(path, "r", encoding="utf-8") as fh:
            design = fh.read()
        for anchor in ("改动点①", "改动点②", "改动点③", "改动点⑤",
                       "托盘归", "不许各开一个连接", "复用点清单"):
            self.assertIn(anchor, design, msg=f"DESIGN.md 缺锚点：{anchor}")


if __name__ == "__main__":
    unittest.main()

# ---------------------------------------------------------------------------
# 集成适配清单（task16，v0.19.0；其余用例与 work16 原件逐行一致）
# 1. _find_client_dev 兜底路径改 client-dev 口径（HERE=client/tests，根为 ../..）。
# 2. 删 work16 版的 client.ui.__path__ 内存挂载：集成后 usage_window 已是真包成员。
# 3. TestSourceAnchors 改读 usage_mod.__file__（client/ui/usage_window.py）。
# 4. DESIGN.md 断言改读 client/ui/DESIGN.md（随模块落 client/ui/）。
# 5. app.py 集成改动（DESIGN.md §3 照单执行，另有一处偏离见注）：
#    标题改「gate-client · 后台（来源管理）」；_setup_tray() 调用改 self.tray = None
#    （托盘归主窗；self.tray 其余引用全有 is None 守卫）；closeEvent 改直接
#    super().closeEvent(event)；main() 两窗装配 + 统一扇出。偏离：UsageWindow
#    在模块级 from .app import …，app.py 顶层 import 它会循环导入，故 main()
#    内懒加载（见 app.py main() 内注释），行为与 DESIGN.md §3 改动点①等价。
# ---------------------------------------------------------------------------
