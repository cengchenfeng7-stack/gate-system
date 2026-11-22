#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
设置页（ui/settings_dialog.py，task24 · 设置页落页）
=====================================================
work12/DESIGN §5/§6 + §8-4/§8-5 的落地（模态对话框）：

- 分组（§5.1）：连接·引擎入站 / 连接·规则与网络（UsageSettings 五项，
  **唯一编辑入口** §6——使用界面③区只复用同一份值）/ 自动探测 /
  名单复查（task33 落页：list_recheck_days 编辑器）/ 数据。
- 生效时机逐项标注（§5.3，不许含糊）：端口 / 规则五项 = 下次连接生效；
  自动探测 = 立即生效；数据目录 = 下次启动生效（不搬移旧数据）。
- 危险项确认（§5.4）：
  · 改数据目录 = 强确认：列旧/新目录，逐条写明后果，显式「确认更改」；
  · 改端口 = 保存前跑 check_ports_free 占用预检 + proxy_conflict 系统
    代理检查：被占用/冲突 → 拒绝保存并说明；无冲突 → 普通确认框
    「下次连接生效」；
  · 恢复默认 = 确认框列将被重置的项；先备份现文件（§4.2 同款改名留档）
    再写默认。
- 端口保留段（§8-4）：10818/10819/10828/10829（测速/自动探测引擎专用）
  列为拒绝，防主端口撞测速引擎。**呈现层校验**——模型层刻意不锁
  （DESIGN §8-4「避免实现细节进模型」）。
- 间隔四档（§8-5）：AUTO_PROBE_INTERVAL_CHOICES = (5,10,20,30) 供下拉；
  放开任意分钟数只改 UI 不动模型。

铁律对齐：设置页只管配置本身——没有任何节点选择/优选/自动切换/定时
拉新项；五项规则字段只在此出现一次（§6），保存统一走
merge(现值, 改动) → Settings → SettingsStore 落盘，值只有一份、存储
只有一处。设置页不生成任何配置片段——它只存值（§6-2）。

无 PySide6 的环境：Qt 控件类不定义（同 ui/app.py 口径）；模块级纯
函数（保留段校验等）照常可用，离线测试不必等 Qt。
"""

from __future__ import annotations

import os

from ..core.health import check_ports_free, proxy_conflict, read_system_proxy
from ..core.settings_proto import (
    AUTO_PROBE_INTERVAL_CHOICES,
    DEFAULT_DATA_DIR,
    DEFAULT_HTTP_PORT,
    DEFAULT_SOCKS_PORT,
    DNS_PRESETS,
    SPLIT_MODE_LABELS,
    Settings,
    SettingsError,
    merge,
)

# ---------------------------------------------------------------------------
# 端口保留段（§8-4）：测速/自动探测临时引擎专用（ui/app.py
# _TEMP_SOCKS_PORT/_TEMP_HTTP_PORT/_AUTO_SOCKS_PORT/_AUTO_HTTP_PORT）。
# 主端口撞上这段，测速/自动探测的临时引擎起不来。呈现层校验——
# 模型层不锁（DESIGN §8-4）。
# ---------------------------------------------------------------------------
RESERVED_PORTS = (10818, 10819, 10828, 10829)


def reserved_port_text(port) -> str:
    """命中保留段返回拒绝文案，否则 None（纯函数，离线可测）。"""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return None
    if port in RESERVED_PORTS:
        return (f"端口 {port} 是测速/自动探测引擎的专用保留端口"
                f"（{'/'.join(str(p) for p in RESERVED_PORTS)}），"
                "主端口不能占用这段，请换一个。")
    return None


def _ports_occupied(ports):
    """check_ports_free 薄封装（测试打桩点，语义不变）。"""
    return check_ports_free(ports)


def _proxy_conflict(http_port):
    """proxy_conflict(read_system_proxy(), port) 薄封装（测试打桩点）。"""
    return proxy_conflict(read_system_proxy(), http_port)


# DNS 自定义项的内部标记（combo data；不是预设键）
_DNS_CUSTOM = "__custom__"

try:
    from PySide6.QtCore import Signal
    from PySide6.QtWidgets import (
        QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QGroupBox,
        QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox,
        QVBoxLayout,
    )
    _QT_OK = True
except ImportError:  # pragma: no cover - 无 Qt 环境时的降级路径
    _QT_OK = False

if _QT_OK:

    class SettingsDialog(QDialog):
        """设置页（§5 分组与控件形态；模态；保存走 merge → SettingsStore）。

        危险项确认拆成独立方法（_confirm_data_dir_change /
        _precheck_ports / _confirm_reset），离线测试可逐路径打桩。
        """

        def __init__(self, settings: Settings, settings_store, parent=None):
            super().__init__(parent)
            self.setWindowTitle("设置")
            self.resize(520, 620)   # task33：+名单复查组，加高防裁保存行
            if not isinstance(settings, Settings):
                raise SettingsError(
                    f"settings 必须是 Settings，得到 {type(settings).__name__}")
            self._base = settings
            self.settings_store = settings_store
            self.saved_settings = None       # 保存成功后的新 Settings（窗侧应用）

            self.error_label = QLabel("")
            self.error_label.setStyleSheet("color:#ff6b6b;")
            self.error_label.setWordWrap(True)

            layout = QVBoxLayout(self)
            layout.addWidget(self._group_engine_ports())
            layout.addWidget(self._group_rules_network())
            layout.addWidget(self._group_auto_probe())
            layout.addWidget(self._group_list_recheck())
            layout.addWidget(self._group_data())
            layout.addWidget(self.error_label)

            actions = QHBoxLayout()
            reset_btn = QPushButton("恢复默认")
            reset_btn.clicked.connect(self._reset_defaults)
            actions.addWidget(reset_btn)
            actions.addStretch(1)
            cancel_btn = QPushButton("取消")
            cancel_btn.clicked.connect(self.reject)
            save_btn = QPushButton("保存")
            save_btn.setDefault(True)
            save_btn.clicked.connect(self.save)
            actions.addWidget(cancel_btn)
            actions.addWidget(save_btn)
            layout.addLayout(actions)

            self._load_values(self._base)

        # ---------------- 分组构建（§5.1/§5.2） ----------------
        def _group_engine_ports(self) -> QGroupBox:
            group = QGroupBox("连接 · 引擎入站")
            form = QFormLayout(group)
            self.socks_spin = QSpinBox()
            self.socks_spin.setRange(1, 65535)
            self.http_spin = QSpinBox()
            self.http_spin.setRange(1, 65535)
            form.addRow("SOCKS 端口", self.socks_spin)
            form.addRow("HTTP 端口", self.http_spin)
            note = QLabel("生效时机：下次连接（重启引擎）。改端口保存前会做"
                          "占用/系统代理预检；当前会话照旧跑旧端口，空闲时改无感。")
            note.setWordWrap(True)
            form.addRow(note)
            return group

        def _group_rules_network(self) -> QGroupBox:
            group = QGroupBox("连接 · 规则与网络")
            form = QFormLayout(group)
            # 分流模式：SPLIT_MODE_LABELS 三项（task10 §4.1 文案，§5.2）
            self.split_combo = QComboBox()
            for mode, label in SPLIT_MODE_LABELS.items():
                self.split_combo.addItem(label, mode)
            form.addRow("分流模式", self.split_combo)
            self.ad_chk = QCheckBox("广告拦截")
            self.malware_chk = QCheckBox("恶意软件拦截")
            booleans = QHBoxLayout()
            booleans.addWidget(self.ad_chk)
            booleans.addWidget(self.malware_chk)
            booleans.addStretch(1)
            form.addRow(QLabel(""), booleans)
            self.ipv6_chk = QCheckBox("IPv6（关闭时 DNS 不接受 v6 地址）")
            form.addRow(QLabel(""), self.ipv6_chk)
            # DNS：预设下拉 + 「自定义…」→ IP 字面量输入框（§5.2）
            self.dns_combo = QComboBox()
            for key, ip in DNS_PRESETS.items():
                self.dns_combo.addItem(f"{key}（{ip}）", key)
            self.dns_combo.addItem("自定义…", _DNS_CUSTOM)
            self.dns_combo.currentIndexChanged.connect(self._on_dns_mode_changed)
            form.addRow("DNS 服务器", self.dns_combo)
            self.dns_custom_edit = QLineEdit()
            self.dns_custom_edit.setPlaceholderText(
                "IP 字面量（v4/v6）；域名拒收")
            form.addRow(QLabel(""), self.dns_custom_edit)
            note = QLabel("生效时机：下次连接（连接中修改不热重载）。"
                          "这里的五项与使用界面规则区是同一份值（唯一编辑入口）。")
            note.setWordWrap(True)
            form.addRow(note)
            return group

        def _group_auto_probe(self) -> QGroupBox:
            group = QGroupBox("自动探测")
            form = QFormLayout(group)
            self.auto_chk = QCheckBox("自动探测（后台定时测延迟）")
            form.addRow(self.auto_chk)
            self.interval_combo = QComboBox()
            for minutes in AUTO_PROBE_INTERVAL_CHOICES:
                self.interval_combo.addItem(f"{minutes} 分钟", minutes)
            form.addRow("间隔", self.interval_combo)
            note = QLabel("生效时机：立即（定时器与开关即时重设）。")
            note.setWordWrap(True)
            form.addRow(note)
            return group

        def _group_list_recheck(self) -> QGroupBox:
            """名单复查组（task33 落页，销 work28 DONE §4-7 尾巴）：
            list_recheck_days 编辑器。控件用可自由输入的行编辑而非
            QSpinBox(2..7)——范围锁会在控件层静默夹取 1/8、拒收非数字
            击键，简报红线「显式报错、不夹取、不静默改值」就不可达了；
            真规则只在唯一事实源 core/list_recheck.validate_recheck_days
            （保存期经 merge → Settings 校验原文报错），本页零复制。"""
            group = QGroupBox("名单复查")
            form = QFormLayout(group)
            self.recheck_days_edit = QLineEdit()
            self.recheck_days_edit.setPlaceholderText("2–7（默认 3）")
            form.addRow("复查周期（天）", self.recheck_days_edit)
            note = QLabel("生效时机：立即（下一轮复查按新周期判定）。"
                          "默认 3 天，可调 2–7；越界或非整数保存时红字"
                          "显式报错，不夹取、不静默改值。")
            note.setWordWrap(True)
            form.addRow(note)
            return group

        def _group_data(self) -> QGroupBox:
            group = QGroupBox("数据")
            form = QFormLayout(group)
            row = QHBoxLayout()
            self.data_dir_edit = QLineEdit()
            self.data_dir_edit.setReadOnly(True)   # §5.2：只读 + 浏览
            row.addWidget(self.data_dir_edit)
            browse_btn = QPushButton("浏览…")
            browse_btn.clicked.connect(self._browse_data_dir)
            row.addWidget(browse_btn)
            form.addRow("数据目录", row)
            note = QLabel("生效时机：下次启动。不会搬移旧数据——新目录从空库"
                          "开始，旧目录原样保留；保存前会强确认。")
            note.setWordWrap(True)
            form.addRow(note)
            return group

        # ---------------- 值装载与收集 ----------------
        def _load_values(self, s: Settings):
            """控件 ← Settings（构造与「恢复默认」后复用）。"""
            self.socks_spin.setValue(int(s.socks_port))
            self.http_spin.setValue(int(s.http_port))
            idx = self.split_combo.findData(s.split_mode)
            if idx >= 0:
                self.split_combo.setCurrentIndex(idx)
            self.ad_chk.setChecked(bool(s.ad_block))
            self.malware_chk.setChecked(bool(s.malware_block))
            self.ipv6_chk.setChecked(bool(s.ipv6))
            if s.dns_server in DNS_PRESETS:
                self.dns_combo.setCurrentIndex(
                    self.dns_combo.findData(s.dns_server))
                self.dns_custom_edit.setText("")
            else:
                self.dns_combo.setCurrentIndex(
                    self.dns_combo.findData(_DNS_CUSTOM))
                self.dns_custom_edit.setText(s.dns_server)
            self._on_dns_mode_changed()
            self.auto_chk.setChecked(bool(s.auto_probe_enabled))
            idx = self.interval_combo.findData(int(s.auto_probe_interval_min))
            if idx >= 0:
                self.interval_combo.setCurrentIndex(idx)
            # task33：名单复查周期编辑器（与其它字段同款装载）
            self.recheck_days_edit.setText(str(int(s.list_recheck_days)))
            self.data_dir_edit.setText(s.data_dir)
            self.error_label.clear()

        def _collect(self) -> dict:
            """控件 → 全字段 dict（merge 统一过模型校验）。"""
            return {
                "socks_port": int(self.socks_spin.value()),
                "http_port": int(self.http_spin.value()),
                "split_mode": self.split_combo.currentData(),
                "ad_block": self.ad_chk.isChecked(),
                "malware_block": self.malware_chk.isChecked(),
                "ipv6": self.ipv6_chk.isChecked(),
                "dns_server": self._dns_value(),
                "auto_probe_enabled": self.auto_chk.isChecked(),
                "auto_probe_interval_min": int(self.interval_combo.currentData()),
                # task33：名单复查周期（解析形态见 _recheck_days_form_value；
                # 越界/非整数由唯一校验器在 merge → Settings 保存期原文报错）
                "list_recheck_days": self._recheck_days_form_value(),
                "data_dir": self.data_dir_edit.text().strip(),
            }

        def _recheck_days_form_value(self):
            """周期控件文本 → int（可解析时）或原文本（不可解析时）。
            刻意不做数值纠正/夹取：非整数原样交给唯一事实源
            core/list_recheck.validate_recheck_days（经 merge → Settings
            校验）以原文显式报错——本页不复制第二套规则文案。"""
            raw = self.recheck_days_edit.text().strip()
            try:
                return int(raw)
            except ValueError:
                return raw

        def _dns_value(self) -> str:
            if self.dns_combo.currentData() == _DNS_CUSTOM:
                return self.dns_custom_edit.text().strip()
            return self.dns_combo.currentData()

        def _on_dns_mode_changed(self, *_):
            custom = self.dns_combo.currentData() == _DNS_CUSTOM
            self.dns_custom_edit.setVisible(custom)
            if custom:
                self.dns_custom_edit.setFocus()

        def _browse_data_dir(self):
            start = os.path.expanduser(
                self.data_dir_edit.text() or DEFAULT_DATA_DIR)
            if not os.path.isdir(start):
                start = ""
            path = QFileDialog.getExistingDirectory(self, "选择数据目录", start)
            if path:
                self.data_dir_edit.setText(path)

        # ---------------- 危险项确认（§5.4；离线可打桩） ----------------
        def _confirm_data_dir_change(self, old: str, new: str) -> bool:
            """改数据目录强确认（§5.4）：列旧/新目录，逐条写明后果；
            显式「确认更改」才保存。默认按钮 = 否（危险项不默认通过）。"""
            text = (
                f"数据目录将更改：\n\n"
                f"  旧目录：{old}\n  新目录：{new}\n\n"
                "· 不复制、不搬移旧数据；\n"
                "· 新目录从空库开始；\n"
                "· 节点库 / 登记表（sources.json）/ OpenVPN 导出（ovpn_out）"
                "/ 规则集缓存全部落新目录；\n"
                "· 旧目录原样保留。\n\n"
                "保存后下次启动生效。确认更改？")
            choice = QMessageBox.question(
                self, "更改数据目录", text,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            return choice == QMessageBox.Yes

        def _precheck_ports(self, new_socks: int, new_http: int) -> bool:
            """改端口保存前预检（§5.4）：占用 / 系统代理冲突 → 拒绝保存
            并说明；无冲突 → 普通确认框「下次连接生效」。False=中止保存。
            （保留段校验在 save() 更早一层，不进本方法。）"""
            occupied = _ports_occupied((new_socks, new_http))
            if occupied:
                detail = "；".join(
                    f"端口 {item['port']} 被占用"
                    + (f"（{item.get('process') or '未知进程'}）"
                       if item.get("process") else "")
                    for item in occupied)
                QMessageBox.warning(
                    self, "端口被占用，保存被拒绝",
                    f"{detail}。\n引擎下次连接会起不来——先换一个端口，"
                    "或关掉占用它的程序（其他代理软件？）再保存。")
                return False
            conflict = _proxy_conflict(new_http)
            if conflict.get("conflict"):
                QMessageBox.warning(
                    self, "与系统代理冲突，保存被拒绝",
                    f"{conflict.get('reason', '')}\n新 HTTP 端口与当前系统代理"
                    "指向冲突——换一个端口再保存。客户端不会改你的系统代理。")
                return False
            choice = QMessageBox.question(
                self, "更改端口",
                f"端口将在下次连接生效（重启引擎）：\n"
                f"SOCKS {new_socks} / HTTP {new_http}\n"
                "当前连接中的会话照旧跑旧端口。确认更改？",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            return choice == QMessageBox.Yes

        def _confirm_reset(self) -> bool:
            """恢复默认确认（§5.4）：列将被重置的项。"""
            text = ("将把以下设置恢复为默认值"
                    f"（当前设置文件先改名留档再写默认，不会删除）：\n\n"
                    f"· 端口：SOCKS {DEFAULT_SOCKS_PORT} / HTTP {DEFAULT_HTTP_PORT}\n"
                    "· 分流模式=全局代理、广告拦截=关、恶意软件拦截=关、"
                    "IPv6=关、DNS=Google\n"
                    "· 自动探测=开 / 10 分钟\n"
                    f"· 数据目录={DEFAULT_DATA_DIR}\n\n确认恢复默认？")
            choice = QMessageBox.question(
                self, "恢复默认", text,
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            return choice == QMessageBox.Yes

        def _reset_defaults(self):
            """恢复默认（§5.4）：确认 → 备份留档 → 写默认 → 控件回默认
            → 关窗（窗侧按 saved_settings 应用，杜绝文件与窗内状态分叉）。"""
            if not self._confirm_reset():
                return
            try:
                fresh = self.settings_store.reset(tag="bak")
            except OSError as exc:
                self.error_label.setText(f"恢复默认失败：{exc}")
                return
            self._base = fresh
            self._load_values(fresh)
            self.saved_settings = fresh
            self.accept()

        # ---------------- 保存（模型校验 → 危险项确认 → 落盘） ----------------
        def save(self):
            """保存流程：①保留段（§8-4）→ ②模型校验（merge，非法红字
            不保存）→ ③危险项确认（§5.4，只对**改动**的字段）→ ④落盘。"""
            self.error_label.clear()
            vals = self._collect()

            # ① 保留端口段：呈现层硬校验，先于模型与确认（§8-4）
            for name in ("socks_port", "http_port"):
                msg = reserved_port_text(vals[name])
                if msg:
                    self.error_label.setText(msg)
                    return

            # ② 模型校验 + 合并（非法值红字，不保存、不静默纠正）
            try:
                new_settings = merge(self._base, vals)
            except SettingsError as exc:
                self.error_label.setText(str(exc))
                return

            # ③ 危险项确认（§5.4；只对相对当前值的**改动**触发）
            if vals["data_dir"] != self._base.data_dir \
                    and not self._confirm_data_dir_change(
                        self._base.data_dir, vals["data_dir"]):
                return
            if (vals["socks_port"] != self._base.socks_port
                    or vals["http_port"] != self._base.http_port) \
                    and not self._precheck_ports(vals["socks_port"],
                                                 vals["http_port"]):
                return

            # ④ 落盘（原子写；IO 失败红字，不静默吞）
            try:
                self.settings_store.save(new_settings)
            except OSError as exc:
                self.error_label.setText(f"保存失败：{exc}")
                return
            self.saved_settings = new_settings
            self.accept()
