#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一设置模型原型（settings_proto.py，task12 · 阶段四·产品化 第五段）
========================================================================
DESIGN.md §3/§4 的定稿口径落成代码。只用标准库；不依赖 PySide6、
不 import client 包；不读文件、不碰网络——纯数据结构 + 校验 + 序列化
+ 合并（与 task10 usage_model.py 同一独立性口径）。

职责（DESIGN §3）：
- Settings：统一设置模型（frozen dataclass，10 字段）——连接层引擎端口
  （SOCKS/HTTP）、数据目录、界面层自动探测（开关/间隔）、以及 task10
  UsageSettings 五项的**同名字段镜像**（split_mode/ad_block/malware_block/
  ipv6/dns_server，字段定义逐字复用 usage_model.py，口径单一事实源 =
  UsageSettings，DESIGN §3.1；集成期改为 import 消镜像，见 DESIGN §8-2）。
- to_dict()/from_dict(d)：往返无损；缺字段补默认值；多余字段抛
  SettingsError（拒绝静默忽略，与 UsageSettings.from_dict 同口径）。
- merge(base, override)：用户改动与默认值的合并——override 只覆盖显式
  给出的字段，未知键/非法值照常报错。
- usage_dict()：五项字段子集 → 集成段 UsageSettings(**s.usage_dict())
  传给 build_route_config（DESIGN §6-2）。

镜像声明（DESIGN §3.1，红线：冲突以 UsageSettings 为准）：
- SPLIT_MODES / DNS_PRESETS / 规则五项默认值与校验：拷贝自
  client/core/usage_model.py（0.15.0 快照，行号随注）；test_settings.py
  的「口径一致」测试组机械锁定两边等价。
- 端口默认：engines/singbox_engine.py:54-55 DEFAULT_SOCKS_PORT/
  DEFAULT_HTTP_PORT。
- 数据目录默认：ui/cli.py:46-48 DEFAULT_DB（~/.local/share/gate-client/
  client.db）的目录口径。
- 探测默认：ui/app.py:438-445 auto_chk 默认勾选 + combo 四档默认第 2 项
  （10 分钟）＝ QSettings auto_probe/enabled、auto_probe/interval_min
  的现行默认（ui/app.py:509-523）。

铁律对齐：本模块没有任何节点选择/切换/优选逻辑，也不持有相关字段——
设置页只管配置本身（DESIGN §0）；「默认值 = 现行行为」逐字段可溯源。
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 常量（出处随注；集成时改为 from ..core.usage_model import 消镜像，DESIGN §8-2）
# ---------------------------------------------------------------------------

# 分流模式（镜像自 usage_model.py:40-44；默认 global = 现行行为）
SPLIT_GLOBAL = "global"
SPLIT_RULE = "rule"
SPLIT_DIRECT = "direct"
SPLIT_MODES = (SPLIT_GLOBAL, SPLIT_RULE, SPLIT_DIRECT)

# DNS 预设（镜像自 usage_model.py:63-67；8.8.8.8 与引擎现行写死值一致）
DNS_PRESETS = {
    "google": "8.8.8.8",
    "cloudflare": "1.1.1.1",
    "quad9": "9.9.9.9",
}

# 端口默认（engines/singbox_engine.py:54-55）
DEFAULT_SOCKS_PORT = 7898
DEFAULT_HTTP_PORT = 7897

# 数据目录默认（ui/cli.py:46-48 DEFAULT_DB 的目录口径；db = <目录>/client.db；
# sources.json / ovpn_out / rulesets 等派生落点全部跟随该目录，DESIGN §3.2）
DEFAULT_DATA_DIR = "~/.local/share/gate-client"

# 自动探测间隔 UI 四档（ui/app.py:443 现行四档；模型只锁正整数，
# 四档是呈现层约束——DESIGN §5.2）
AUTO_PROBE_INTERVAL_CHOICES = (5, 10, 20, 30)

# UsageSettings 五项同名字段（DESIGN §3.1；usage_dict() 的键序）
USAGE_FIELDS = ("split_mode", "ad_block", "malware_block", "ipv6", "dns_server")

# 持久化包裹层版本（DESIGN §4.1；原型不读写文件，集成段消费）
SETTINGS_FILE_VERSION = 1
DEFAULT_SETTINGS_FILE = "~/.local/share/gate-client/settings.json"

_PORT_MIN = 1
_PORT_MAX = 65535


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
class SettingsError(ValueError):
    """设置非法。报错不静默吞、不静默纠正（与 UsageSettingsError 同款口径）。"""


def _require_bool(name: str, value) -> None:
    """严格布尔校验：0/1 冒充拒收（镜像 usage_model.py:177-181 口径）。"""
    if not isinstance(value, bool):
        raise SettingsError(
            f"{name} 必须是布尔值（不许 0/1 冒充），得到 {value!r}")


def _require_port(name: str, value) -> None:
    """端口校验：int（bool 拒收）且 1..65535（DESIGN §3.2）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(
            f"{name} 必须是整数端口，得到 {value!r}")
    if not _PORT_MIN <= value <= _PORT_MAX:
        raise SettingsError(
            f"{name} 越界（合法区间 {_PORT_MIN}..{_PORT_MAX}），得到 {value!r}")


def _require_positive_int(name: str, value) -> None:
    """正整数校验：int（bool 拒收）且 ≥1（DESIGN §3.2 间隔口径）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(
            f"{name} 必须是整数，得到 {value!r}")
    if value < 1:
        raise SettingsError(
            f"{name} 必须是正数（≥1），得到 {value!r}")


def _validate_dns_server(value, ipv6_on: bool) -> str:
    """DNS 服务器校验（镜像 usage_model.py:184-205 逐字口径）：
    预设键或 IP 字面量；域名拒收（DNS 服务器自己要先被解析，先有鸡
    问题）；交叉校验——IPv6 关时拒 v6 地址（v4-only 防泄漏口径）。
    返回实际 IP（预设解析、IP 规范化）。"""
    if value in DNS_PRESETS:
        server_ip = DNS_PRESETS[value]
    else:
        if isinstance(value, bool) or not isinstance(value, str) \
                or not value.strip():
            raise SettingsError(
                f"dns_server 必须是预设键 {'/'.join(DNS_PRESETS)} 或 IP 字面量，"
                f"得到 {value!r}")
        try:
            server_ip = str(ipaddress.ip_address(value.strip()))
        except ValueError:
            raise SettingsError(
                f"dns_server 不是合法 IP 字面量也不是预设键 "
                f"{'/'.join(DNS_PRESETS)}（域名形态拒收：解析 DNS 服务器"
                f"本身有先有鸡问题）: {value!r}") from None
    if not ipv6_on and ":" in server_ip:
        raise SettingsError(
            f"IPv6 开关关闭时 DNS 服务器不能用 v6 地址（{server_ip}）——"
            "现行 v4-only 防泄漏口径下引擎到 v6 DNS 的出站不可靠；"
            "先开 IPv6 或换 v4 DNS 服务器")
    return server_ip


# ---------------------------------------------------------------------------
# 设置模型
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    """统一设置模型（DESIGN §3.2，纯数据 + 校验）。

    默认值 = 现行行为（逐字段出处见模块 docstring）：
    客户端不做用户没要求的事，所有改变流量的行为都显式开启。
    UsageSettings 五项为同名字段镜像——口径单一事实源 = UsageSettings，
    等价性由 test_settings.py 口径一致测试组机械锁定（DESIGN §3.1）。
    """
    # —— 连接层：规则与网络（UsageSettings 五项，镜像） ——
    split_mode: str = SPLIT_GLOBAL
    ad_block: bool = False
    malware_block: bool = False
    ipv6: bool = False
    dns_server: str = "google"
    # —— 连接层：引擎入站端口 ——
    socks_port: int = DEFAULT_SOCKS_PORT
    http_port: int = DEFAULT_HTTP_PORT
    # —— 应用层：数据目录 ——
    data_dir: str = DEFAULT_DATA_DIR
    # —— 界面层：自动探测 ——
    auto_probe_enabled: bool = True
    auto_probe_interval_min: int = 10

    def __post_init__(self):
        # 连接层五项：镜像 usage_model.py:173-205 校验（口径以彼为准）
        if self.split_mode not in SPLIT_MODES:
            raise SettingsError(
                f"分流模式必须是 {'/'.join(SPLIT_MODES)} 之一，"
                f"得到 {self.split_mode!r}")
        for name in ("ad_block", "malware_block", "ipv6"):
            _require_bool(name, getattr(self, name))
        _validate_dns_server(self.dns_server, self.ipv6)

        # 端口：各自 1..65535 + 交叉互异（DESIGN §3.2）
        _require_port("socks_port", self.socks_port)
        _require_port("http_port", self.http_port)
        if self.socks_port == self.http_port:
            raise SettingsError(
                f"socks_port 与 http_port 不能相同（{self.socks_port}）——"
                "两个入站监听同一端口必有一个起不来")

        # 数据目录：非空字符串（目录不存在不算错——首次运行时创建；
        # 消费端 expanduser，DESIGN §3.2/§5.2）
        if isinstance(self.data_dir, bool) or not isinstance(self.data_dir, str) \
                or not self.data_dir.strip():
            raise SettingsError(
                f"data_dir 必须是非空目录路径，得到 {self.data_dir!r}")

        # 界面层自动探测：严格布尔 + 正整数间隔（DESIGN §3.2）
        _require_bool("auto_probe_enabled", self.auto_probe_enabled)
        _require_positive_int("auto_probe_interval_min",
                              self.auto_probe_interval_min)

    # ---------------- 序列化（DESIGN §3.4） ----------------
    def to_dict(self) -> dict:
        """全字段平面 dict（声明序）；往返无损。"""
        return {
            "split_mode": self.split_mode,
            "ad_block": self.ad_block,
            "malware_block": self.malware_block,
            "ipv6": self.ipv6,
            "dns_server": self.dns_server,
            "socks_port": self.socks_port,
            "http_port": self.http_port,
            "data_dir": self.data_dir,
            "auto_probe_enabled": self.auto_probe_enabled,
            "auto_probe_interval_min": self.auto_probe_interval_min,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        """缺字段补默认值；多余字段抛 SettingsError（拒绝静默忽略——
        与 UsageSettings.from_dict 同口径，DESIGN §3.4 定「抛错」）。"""
        if not isinstance(d, dict):
            raise SettingsError(f"设置必须是 dict，得到 {d!r}")
        extra = set(d) - set(cls.__dataclass_fields__)
        if extra:
            raise SettingsError(
                f"设置含未知键（拒绝静默忽略）: {sorted(extra, key=repr)}")
        return cls(**d)

    def usage_dict(self) -> dict:
        """UsageSettings 五项字段子集（键序 = USAGE_FIELDS）。
        集成段消费：UsageSettings(**s.usage_dict()) → build_route_config。"""
        return {k: getattr(self, k) for k in USAGE_FIELDS}


# ---------------------------------------------------------------------------
# 合并（DESIGN §3.4：override 只覆盖显式给出的字段）
# ---------------------------------------------------------------------------
def merge(base: Settings, override=None) -> Settings:
    """base 设置 + 用户显式改动 → 新 Settings。

    - override 为 dict：只覆盖其中显式给出的字段，其余继承 base；
      未知键抛 SettingsError；值照常过 __post_init__ 校验（非法值当场
      报错，不回退不改写）。
    - override 为 None：等价「无改动」，返回 base 本身。
    - base 必须是 Settings；产物是新 frozen 实例，base 不被改动。
    """
    if not isinstance(base, Settings):
        raise SettingsError(
            f"base 必须是 Settings，得到 {type(base).__name__}")
    if override is None:
        return base
    if not isinstance(override, dict):
        raise SettingsError(
            f"override 必须是 dict（显式给出的字段覆盖），得到 {override!r}")
    extra = set(override) - set(Settings.__dataclass_fields__)
    if extra:
        raise SettingsError(
            f"override 含未知键（拒绝静默忽略）: {sorted(extra, key=repr)}")
    return Settings(**{**base.to_dict(), **override})
