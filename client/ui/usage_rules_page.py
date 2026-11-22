#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
③规则与网络区（ui/usage_rules_page.py）—— 只读摘要（task26 ①，裁决 A）
========================================================================
task17（0.22.0）曾把本区落为五项规则字段的编辑区；task26 ①（用户
2026-11-22 裁决选 A）起**降为只读摘要展示**：五项规则字段（split_mode /
ad_block / malware_block / ipv6 / dns_server）的唯一编辑入口收敛到
设置页（task24 的 SettingsDialog），本区只显示当前生效值 + 一行提示
「在设置页修改」。

展示口径（task26 ① 逐条）：
- 显示当前生效值：每次 reload **现读 settings.json**（经 core
  /settings_store 的函数面 → settings_proto.SettingsStore 唯一持久化
  实现，task26 ②），面板不持有第二份可编辑状态副本；设置页保存后由
  usage_window._apply_saved_settings 调 reload() 即见。
- 一行常驻提示「在设置页修改」，并保留「下次连接生效」口径（连接时由
  引擎构造点经供给器现读设置生成配置，work17/DESIGN.md §5；界面常驻
  明示，不做热重载、不半生效）。
- **禁写路径全部摘除**：编辑控件（下拉/勾选/输入框）与写回管道
  （现读→merge 单字段→原子写回）整体移除，本区对 settings.json 只读
  ——任何③区交互都不可能改设置文件（test_ruling_wiring.py md5 断言
  锁定）。读取失败（损坏/非法）如实红字 + 值行显「—」（不把默认值
  假装成生效值——不用默认值盖掉展示，task12 §4.2 同口径）。

区界不变（work10 §2.3 + task18 既有交付物）：规则集「立即更新」行是
task18 落的功能（写规则集缓存，不写 settings.json），不在五项规则字段
的收敛范围内，原样保留；名单编辑归后台；端口/数据目录/自动探测等其余
设置归设置页，本区一个不放。

集成（work17/DESIGN.md §6 改动点清单）：UsageWindow.__init__ 在②区
之后构造并 addWidget（task26 ① 起挂载点传 settings_path=设置页同一
落点，摘要在与设置页同一份文件上现读）；本模块不 import .app（避免
循环导入），样式随应用级 DARK_QSS。

铁律对齐：本区没有任何节点选择/切换/优选控件；降只读后更没有任何
设置写入路径。
"""

from __future__ import annotations

import os
import time

from ..core import settings_store
from ..core import ruleset_cache
from ..core.usage_model import DNS_PRESETS, SPLIT_MODE_LABELS

try:
    from PySide6.QtWidgets import (
        QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
    )
    _QT_OK = True
except ImportError:  # pragma: no cover - 无 Qt 环境时的降级路径
    _QT_OK = False

# 只读摘要口径常驻文案（task26 ① 裁决 A：值只读 + 编辑入口在设置页 +
# 保留「下次连接生效」口径——界面明确标注，不做热重载）
READONLY_NOTE = ("以上为当前生效值（现读设置）；在设置页修改"
                 "（菜单「设置」）。生效口径：下次连接生效——连接时现读"
                 "设置生成配置，不做热重载。")

# 值读不出时的占位（不把默认值假装成生效值——如实未知）
_VALUE_UNKNOWN = "—"

# 字段中文名（摘要行标签；与 task17 既有命名一致）
_FIELD_LABELS = {
    "split_mode": "分流模式",
    "ad_block": "广告拦截",
    "malware_block": "恶意拦截",
    "ipv6": "IPv6",
    "dns_server": "DNS 服务器",
}

# 布尔字段的展示词（摘要文案，非存储值——存储仍是严格 bool）
_BOOL_TEXT = {True: "开", False: "关"}

_STATUS_ERR_STYLE = "color:#ff6b6b;padding:1px 0;"
_STATUS_OK_STYLE = "color:#8fd694;padding:1px 0;"
_VALUE_STYLE = "color:#c9cbd3;padding:1px 0;"


if _QT_OK:

    class UsageRulesPanel(QFrame):
        """③规则与网络区面板（只读摘要）：五行当前生效值 + 常驻提示 +
        规则集更新行 + 状态行。

        值永远来自存储现读（reload）：构造时读一次；设置页保存后由
        usage_window 调 reload()。面板不持有任何可编辑状态副本，对
        settings.json 没有写入路径（禁写已整体摘除，task26 ①）。"""

        def __init__(self, settings_path=None, parent=None):
            super().__init__(parent)
            self._path = settings_path          # None = 内置默认目录（settings_store 口径）
            # U3：规则集登记/缓存的数据目录（settings.json 同目录口径；
            # None = 内置默认目录）。随面板注入以便测试。
            self._data_dir = (os.path.dirname(os.path.abspath(self._path))
                              if self._path else None)
            self.setFrameStyle(QFrame.StyledPanel)

            root = QVBoxLayout(self)
            root.setContentsMargins(10, 6, 10, 6)

            title = QLabel("③ 规则与网络")
            title.setStyleSheet("font-weight:bold;padding:1px 0;")
            root.addWidget(title)

            grid = QGridLayout()
            grid.setHorizontalSpacing(12)
            row = 0

            # 五项规则字段 → 只读摘要行（task26 ①：值行 QLabel，无编辑
            # 控件；字段文案沿用 usage_model 既有口径，不另造一套）
            self._value_labels = {}
            for key, label in _FIELD_LABELS.items():
                grid.addWidget(QLabel(label), row, 0)
                value_label = QLabel(_VALUE_UNKNOWN)
                value_label.setStyleSheet(_VALUE_STYLE)
                grid.addWidget(value_label, row, 1)
                self._value_labels[key] = value_label
                row += 1
            root.addLayout(grid)

            # 规则集「立即更新」行（U3/task18，③区既有功能原样保留）：
            # ③区内按钮 + 逐 tag 状态行（数据来自 ruleset_cache
            # .cache_status；远端条目如实标注内核托管，不编造内核接口）。
            # 它写的是规则集缓存，不写 settings.json——不在五项字段
            # 收敛范围（task26 ① 口径：五项规则字段的编辑入口收敛）。
            grid.addWidget(QLabel("规则集更新"), row, 0)
            update_box = QHBoxLayout()
            self.update_btn = QPushButton("立即更新")
            update_box.addWidget(self.update_btn, 0)
            self.ruleset_status = QLabel("")
            self.ruleset_status.setWordWrap(True)
            update_box.addWidget(self.ruleset_status, 1)
            grid.addLayout(update_box, row, 1)
            row += 1

            # 常驻只读口径 + 编辑入口提示（task26 ①：「在设置页修改」）
            self.note_label = QLabel(READONLY_NOTE)
            self.note_label.setStyleSheet("color:#9a9ca6;")
            self.note_label.setWordWrap(True)
            root.addWidget(self.note_label)

            # 状态行（只读时代的唯一反馈通道：设置读取失败红字；无保存
            # 回执——本区不再写设置。规则集更新结果走 ruleset_status）
            self.status_label = QLabel("")
            self.status_label.setWordWrap(True)
            root.addWidget(self.status_label)

            # 信号接线（只读面板仅规则集更新一个动作；值行不可交互）
            self.update_btn.clicked.connect(self._on_update_now)

            self.reload()
            self._refresh_ruleset_status()

        # ---------------- 存储现读（只读；无任何写路径） ----------------
        def reload(self):
            """从存储现读并刷新摘要（构造时调用；设置页保存后由
            usage_window 再次调用）。只读：任何分支都不写 settings.json。"""
            try:
                usage = settings_store.load_settings(self._path).usage_dict()
            except ValueError as exc:
                # 损坏/非法：如实红字（含路径），值行显「—」——不静默用
                # 默认值冒充生效值、不做任何会覆盖原始证据的写入
                # （task12 §4.2 口径在只读摘要下的对应处置）。
                for label in self._value_labels.values():
                    label.setText(_VALUE_UNKNOWN)
                self._displayed_usage = None
                self._set_status(f"设置存储读取失败，③区摘要暂不可显示：{exc}",
                                 error=True)
                return
            self._sync_labels(usage)
            self._displayed_usage = dict(usage)
            self._set_status("")

        def _sync_labels(self, usage: dict):
            """按 usage_dict 回填摘要值行（现读值，非缓存副本）。"""
            self._value_labels["split_mode"].setText(
                SPLIT_MODE_LABELS.get(usage["split_mode"],
                                      str(usage["split_mode"])))
            for key in ("ad_block", "malware_block", "ipv6"):
                self._value_labels[key].setText(
                    _BOOL_TEXT.get(bool(usage[key]), _VALUE_UNKNOWN))
            dns = usage["dns_server"]
            if dns in DNS_PRESETS:
                self._value_labels["dns_server"].setText(
                    f"{dns} · {DNS_PRESETS[dns]}")
            else:                               # 非预设键 = IP 字面量（自定义档）
                self._value_labels["dns_server"].setText(str(dns))

        def _set_status(self, text: str, error: bool = False):
            self.status_label.setText(text)
            self.status_label.setStyleSheet(
                _STATUS_ERR_STYLE if error else _STATUS_OK_STYLE)

        # ---------------- 规则集立即更新（U3/task18，原样保留） ----------
        def _ruleset_status_lines(self):
            """逐 tag 缓存状态行（数据来自缓存状态报告，mtime 即
            last_updated）。读失败如实给错误行（不静默当无缓存）。
            远端条目如实标注内核托管（§13-E：客户端无触发接口）。
            """
            try:
                status = ruleset_cache.cache_status(self._data_dir)
            except ValueError as exc:
                return [f"规则集缓存状态读取失败：{exc}"]
            lines = []
            for tag in sorted(status):
                item = status[tag]
                if item["present"]:
                    updated = time.strftime(
                        "%Y-%m-%d %H:%M:%S",
                        time.localtime(item["last_updated"]))
                    lines.append(f"{tag}：{item['size_bytes']} B · "
                                 f"更新于 {updated}")
                else:
                    lines.append(f"{tag}：无缓存")
            return lines

        def _refresh_ruleset_status(self):
            self.ruleset_status.setText("\n".join(
                self._ruleset_status_lines()))
            self.ruleset_status.setStyleSheet(_STATUS_OK_STYLE)
            self.update_btn.setEnabled(True)

        def _on_update_now(self):
            """「立即更新」：local 条目经缓存管理重下载（宁旧勿缺），
            remote 条目如实回报内核托管（客户端无触发接口，§13-E）；
            逐 tag 结果 + 缓存状态（last_updated）同走状态行，
            不弹模态框。同步执行（v1：取材为本地文件复制量级）。
            只写规则集缓存，不碰 settings.json（task26 ① 禁写口径）。"""
            self.update_btn.setEnabled(False)
            try:
                results = ruleset_cache.update_rulesets(
                    data_dir=self._data_dir)
            except ValueError as exc:
                self.ruleset_status.setText(f"更新失败：{exc}")
                self.ruleset_status.setStyleSheet(_STATUS_ERR_STYLE)
                self.update_btn.setEnabled(True)
                return
            lines = [f"{'✓' if r['ok'] else '—'} {r['message']}"
                     for r in results.values()]
            lines.extend(self._ruleset_status_lines())
            self.ruleset_status.setText("\n".join(lines))
            # 告警口径（§8.3）：取材/落盘失败才红字（宁旧勿缺的告警）；
            # no_source / 内核托管是如实回报，不当失败渲染
            warn = any(r["kind"] in ("fetch_failed", "write_failed")
                       for r in results.values())
            self.ruleset_status.setStyleSheet(
                _STATUS_ERR_STYLE if warn else _STATUS_OK_STYLE)
            self.update_btn.setEnabled(True)

        # ---------------- 测试/审计出口 ----------------
        def current_usage(self):
            """当前**展示**的生效值（usage_dict 形态）——现读存储的值，
            不是可编辑副本（只读面板没有编辑管道）。存储读不出（损坏/
            非法）时返回 None：摘要值行显「—」，不谎报默认值。"""
            return (dict(self._displayed_usage)
                    if self._displayed_usage is not None else None)
