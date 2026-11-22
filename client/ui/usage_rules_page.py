#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
③规则与网络区（ui/usage_rules_page.py）—— U2：落 UsageWindow 主窗
========================================================================
task17 · 阶段五·使用界面线 实现第二段。work10/DESIGN.md §2.2 ③区定稿
的控件面：分流模式（global/rule/direct 三选一）、广告拦截、恶意拦截、
IPv6、DNS 服务器（预设 google/cloudflare/quad9 + 自定义 IP）。

唯一模型与唯一存储（work10 §11 / task12 §6-1，简报红线）：
- 控件只编辑 UsageSettings 五项（usage_model.SPLIT_MODES 等常量即口径，
  不另造第二套模型/常量）；
- 读写走 task12 的 Settings 存储同一份 settings.json（core/settings_store.py）：
  每次改动 = 现读存储 → settings_proto.merge(base, {只含本控件那一个字段})
  → 原子写回。merge 的「override 只覆盖显式字段」语义保证：一个控件
  只写自己那一个字段，③区与（后续集成段的）设置页写同一存储互不为
  副本——值只有一份、存储只有一处，不存在两个可编辑副本。
- 校验错误（SettingsError：v6 DNS 交叉校验等）→ 红字如实显示 + 控件
  回退到存储值，不静默纠正、不静默保存。

生效口径（work10 §2.2 ③区既有口径）：下次连接生效——连接时由引擎
构造点经供给器现读设置生成配置（work17/DESIGN.md §5 集成改动点）；
界面常驻明示本口径，不做热重载、不半生效。

边界（work10 §2.3）：名单编辑归后台（本区只读消费 site_blocklist.json）；
端口/数据目录/自动探测等其余设置归 task12 设置页，本区一个不放（未
实现的功能不放假开关）。状态反馈走本区状态行（红字/回执），不弹模态
框（0.7.0 既定偏好）。

集成（work17/DESIGN.md §6 改动点清单）：UsageWindow.__init__ 在②区
之后构造并 addWidget；本模块不 import .app（避免循环导入），样式随
应用级 DARK_QSS。

铁律对齐：本区没有任何节点选择/切换/优选控件——它只改用户显式定好
的使用偏好，连接永远由用户在②区点。
"""

from __future__ import annotations

import os
import time

from ..core import settings_proto, settings_store
from ..core import ruleset_cache
from ..core.usage_model import DNS_PRESETS, SPLIT_MODE_LABELS

try:
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel,
        QLineEdit, QPushButton, QVBoxLayout,
    )
    _QT_OK = True
except ImportError:  # pragma: no cover - 无 Qt 环境时的降级路径
    _QT_OK = False

# 生效口径常驻文案（work10 §2.2 ③区：界面明确标注，不做热重载）
EFFECT_NOTE = "以上改动下次连接生效（连接时现读设置生成配置；不做热重载）"

# DNS 自定义档的内部标记（不与预设键冲突）
_DNS_CUSTOM = "_custom_ip_"

# 字段中文名（状态行回执用）
_FIELD_LABELS = {
    "split_mode": "分流模式",
    "ad_block": "广告拦截",
    "malware_block": "恶意拦截",
    "ipv6": "IPv6",
    "dns_server": "DNS 服务器",
}

_STATUS_ERR_STYLE = "color:#ff6b6b;padding:1px 0;"
_STATUS_OK_STYLE = "color:#8fd694;padding:1px 0;"


if _QT_OK:

    class UsageRulesPanel(QFrame):
        """③规则与网络区面板：五个控件 + 常驻生效口径 + 状态行。

        控件状态永远以存储为准：构造时从存储载入；每次改动现读存储 →
        merge 单字段 → 写回；失败则红字 + 控件回退（不静默保存）。
        面板自身不持有第二份可编辑状态副本。"""

        def __init__(self, settings_path=None, parent=None):
            super().__init__(parent)
            self._path = settings_path          # None = 内置默认目录（settings_store 口径）
            self._loading = False               # 程序性回填期间压制控件回调
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

            # 分流模式（三选一，SPLIT_MODE_LABELS 即 usage_model 口径文案）
            grid.addWidget(QLabel("分流模式"), row, 0)
            self.mode_combo = QComboBox()
            for key in SPLIT_MODE_LABELS:
                self.mode_combo.addItem(SPLIT_MODE_LABELS[key], key)
            grid.addWidget(self.mode_combo, row, 1)
            row += 1

            # 三个开关（默认全关=现行行为；严格 bool，勾选框给不出别的值）
            grid.addWidget(QLabel("广告拦截"), row, 0)
            self.ad_chk = QCheckBox()
            grid.addWidget(self.ad_chk, row, 1)
            row += 1
            grid.addWidget(QLabel("恶意拦截"), row, 0)
            self.mal_chk = QCheckBox()
            grid.addWidget(self.mal_chk, row, 1)
            row += 1
            grid.addWidget(QLabel("IPv6"), row, 0)
            self.ipv6_chk = QCheckBox()
            grid.addWidget(self.ipv6_chk, row, 1)
            row += 1

            # DNS 服务器：预设三档 + 自定义 IP（域名拒收——先有鸡问题，
            # usage_model/settings_proto 口径原样，模型层报错红字呈现）
            grid.addWidget(QLabel("DNS 服务器"), row, 0)
            self.dns_combo = QComboBox()
            for key, ip in DNS_PRESETS.items():
                self.dns_combo.addItem(f"{key} · {ip}", key)
            self.dns_combo.addItem("自定义 IP…", _DNS_CUSTOM)
            self.dns_edit = QLineEdit()
            self.dns_edit.setPlaceholderText("输入 IP 字面量（v4/v6；域名拒收）")
            self.dns_edit.setVisible(False)
            dns_box = QHBoxLayout()
            dns_box.addWidget(self.dns_combo, 0)
            dns_box.addWidget(self.dns_edit, 1)
            grid.addLayout(dns_box, row, 1)
            row += 1
            root.addLayout(grid)

            # 规则集「立即更新」行（U3/task18）：③区内按钮 + 逐 tag
            # 状态行（数据来自 ruleset_cache.cache_status；远端条目
            # 如实标注内核托管，不编造内核接口）
            grid.addWidget(QLabel("规则集更新"), row, 0)
            update_box = QHBoxLayout()
            self.update_btn = QPushButton("立即更新")
            update_box.addWidget(self.update_btn, 0)
            self.ruleset_status = QLabel("")
            self.ruleset_status.setWordWrap(True)
            update_box.addWidget(self.ruleset_status, 1)
            grid.addLayout(update_box, row, 1)
            row += 1

            # 常驻生效口径（界面明示「下次连接生效」；不做热重载）
            self.note_label = QLabel(EFFECT_NOTE)
            self.note_label.setStyleSheet("color:#9a9ca6;")
            root.addWidget(self.note_label)

            # 状态行（保存回执/校验红字；与①区连接状态、消息行互不相干）
            self.status_label = QLabel("")
            self.status_label.setWordWrap(True)
            root.addWidget(self.status_label)

            # 信号接线（程序性回填期间靠 _loading 压制）
            self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
            self.ad_chk.toggled.connect(
                lambda on: self._apply_field("ad_block", on, self.ad_chk))
            self.mal_chk.toggled.connect(
                lambda on: self._apply_field("malware_block", on, self.mal_chk))
            self.ipv6_chk.toggled.connect(
                lambda on: self._apply_field("ipv6", on, self.ipv6_chk))
            self.dns_combo.currentIndexChanged.connect(self._on_dns_preset)
            self.dns_edit.editingFinished.connect(self._on_dns_custom)
            self.update_btn.clicked.connect(self._on_update_now)

            self.reload()
            self._refresh_ruleset_status()

        # ---------------- 存储读写（唯一存储，无第二副本） ----------------
        def reload(self):
            """从存储重载控件（构造时调用；外部修复设置文件后可再次调用）。"""
            self._loading = True
            try:
                try:
                    usage = settings_store.load_settings(self._path).usage_dict()
                except ValueError as exc:
                    # 损坏/非法：如实红字（含路径），控件保持默认值并禁用——
                    # 不静默用默认值盖掉存储、不做会覆盖原始证据的保存
                    # （task12 §4.2 口径；重置入口归设置页集成段）。
                    self._sync_widgets(settings_proto.Settings().usage_dict())
                    self._set_status(f"设置存储读取失败，③区暂不可改：{exc}", error=True)
                    self._set_controls_enabled(False)
                    return
                self._sync_widgets(usage)
                self._set_controls_enabled(True)
                self._set_status("")
            finally:
                self._loading = False

        def _sync_widgets(self, usage: dict):
            """按 usage_dict 回填控件（不触发保存）。"""
            idx = self.mode_combo.findData(usage["split_mode"])
            if idx >= 0:
                self.mode_combo.setCurrentIndex(idx)
            self.ad_chk.setChecked(bool(usage["ad_block"]))
            self.mal_chk.setChecked(bool(usage["malware_block"]))
            self.ipv6_chk.setChecked(bool(usage["ipv6"]))
            dns = usage["dns_server"]
            idx = self.dns_combo.findData(dns)
            if idx >= 0:
                self.dns_combo.setCurrentIndex(idx)
                self.dns_edit.setVisible(False)
            else:                               # 非预设键 = IP 字面量 → 自定义档
                self.dns_combo.setCurrentIndex(self.dns_combo.count() - 1)
                self.dns_edit.setText(str(dns))
                self.dns_edit.setVisible(True)

        def _set_controls_enabled(self, enabled: bool):
            for w in (self.mode_combo, self.ad_chk, self.mal_chk,
                      self.ipv6_chk, self.dns_combo, self.dns_edit):
                w.setEnabled(enabled)

        def _set_status(self, text: str, error: bool = False):
            self.status_label.setText(text)
            self.status_label.setStyleSheet(
                _STATUS_ERR_STYLE if error else _STATUS_OK_STYLE)

        # ---------------- 改动管道：现读 → merge 单字段 → 原子写回 ----------------
        def _apply_field(self, field: str, value, widget) -> bool:
            """写一个字段。失败（存储读不了/校验不过）→ 红字 + 控件回退，
            返回 False；成功 → 存储已是唯一事实源，回执一行。"""
            if self._loading:
                return True
            try:
                base = settings_store.load_settings(self._path)
            except ValueError as exc:
                self._set_status(f"未保存（存储读取失败）：{exc}", error=True)
                return False
            try:
                # merge 只覆盖显式给出的这一个字段——其余九项（含设置页
                # 管的端口/数据目录/自动探测）原样保留（task12 §3.4 口径）
                merged = settings_proto.merge(base, {field: value})
            except ValueError as exc:
                # 校验不过（如 IPv6 关时拒 v6 DNS）：红字如实显示，控件
                # 回退到存储值——存储仍是对的，界面不谎报已保存
                self._set_status(f"未保存：{exc}", error=True)
                self._loading = True
                try:
                    self._sync_widgets(base.usage_dict())
                finally:
                    self._loading = False
                return False
            try:
                settings_store.save_settings(merged, self._path)
            except OSError as exc:
                # 磁盘/权限类故障与校验失败同待遇：红字如实，控件回退存储值
                self._set_status(f"未保存（写盘失败）：{exc}", error=True)
                self._loading = True
                try:
                    self._sync_widgets(base.usage_dict())
                finally:
                    self._loading = False
                return False
            self._set_status(
                f"已保存：{_FIELD_LABELS.get(field, field)}（下次连接生效）")
            return True

        # ---------------- 控件回调（每个控件只写自己那一个字段） ----------------
        def _on_mode_changed(self, _index):
            self._apply_field("split_mode", self.mode_combo.currentData(),
                              self.mode_combo)

        def _on_dns_preset(self, _index):
            data = self.dns_combo.currentData()
            if data == _DNS_CUSTOM:
                self.dns_edit.setVisible(True)
                self.dns_edit.setFocus()
                return                          # 自定义档等 IP 输入完成再写
            self.dns_edit.setVisible(False)
            self._apply_field("dns_server", data, self.dns_combo)

        def _on_dns_custom(self):
            # 以 combo 当前档为准（不用 isVisible 判断——offscreen/未 show
            # 时可见性恒 False，程序性状态才是可靠口径）
            if self._loading or self.dns_combo.currentData() != _DNS_CUSTOM:
                return
            text = self.dns_edit.text().strip()
            if not text:
                return                          # 清空=放弃编辑，不写空值
            self._apply_field("dns_server", text, self.dns_edit)

        # ---------------- 规则集立即更新（U3/task18） ----------------
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
            """
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
        def current_usage(self) -> dict:
            """当前控件值（usage_dict 形态）——仅供测试/审计对照存储，不是
            第二编辑副本：写入永远走 _apply_field 的存储管道。"""
            return {
                "split_mode": self.mode_combo.currentData(),
                "ad_block": self.ad_chk.isChecked(),
                "malware_block": self.mal_chk.isChecked(),
                "ipv6": self.ipv6_chk.isChecked(),
                "dns_server": (self.dns_edit.text().strip()
                               if self.dns_combo.currentData() == _DNS_CUSTOM and
                               self.dns_edit.text().strip()
                               else self.dns_combo.currentData()),
            }
