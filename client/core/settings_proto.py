#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一设置模型 + 持久化（settings_proto.py 落地形态，task24 · 设置页落页）
========================================================================
task12 原型（§3/§4 定稿口径）的集成落地。与原型的差别（DESIGN §8 落地清单）：

- **消镜像（§8-2）**：规则五项的常量、校验、错误文案唯一事实源 =
  usage_model——本模块 `from .usage_model import`，镜像常量删除；
  五项校验改为构造 `UsageSettings` 借它的 `__post_init__`，错误原样
  翻译成 `SettingsError`（文案不另造一套）。等价性由「同一对象」
  直接 import 断言机械锁定（test_settings_landing.py）。
- **接上持久化（§4）**：`SettingsStore` 读写 settings.json（§4.1：JSON
  包裹层 version=1 + 字段平面层；原子写 = 临时文件 + os.replace，与
  task10 sources.json 同口径）；损坏/缺字段/未知键/版本处置按 §4.2
  ——报错不静默吞、不静默覆盖、不删原始证据。
- **QSettings 迁移（§4.3/§8-6）**：首次初始化只搬 auto_probe 两键
  （enabled / interval_min），此后两键停用；last_node_id 是界面状态，
  留在 QSettings，不迁移。
- **路径优先级（§8-3）**：`--db` 显式 > `GATE_CLIENT_DB` > 
  settings.data_dir > 内置默认；settings.json 跟随「显式覆盖目录，
  否则内置默认目录」（不跟随 data_dir 设置——否则「设置说设置在哪」
  自举成环）。settings.json 最终路径只在一处常量（DEFAULT_SETTINGS_FILE）。

不变（原型既定口径）：`Settings` frozen dataclass 10 字段、默认值 =
现行行为、to_dict / from_dict（缺字段补默认、未知键抛错）/ merge
（只覆盖显式字段）/ usage_dict；非法值一律 `SettingsError(ValueError)`
——报错不静默吞、不静默纠正。

依赖口径（从原型放宽，集成后必然）：允许 `from .usage_model import`；
U4/task19 扩展位：`speedtest_payload_url`（测速载荷地址，默认空槽；
校验单一事实源 = core/speedtest.validate_payload_url，与实测器同用一份）；
持久化只用标准库（json/os/tempfile/time）；不依赖 PySide6（迁移经
reader 注入）；不碰网络。无任何节点语义——设置只管配置本身，铁律：
**永不自动切换节点**。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 消镜像（§8-2）：规则五项常量/校验唯一事实源 = usage_model
# ---------------------------------------------------------------------------
from .usage_model import (
    DNS_PRESETS,
    SPLIT_DIRECT,
    SPLIT_GLOBAL,
    SPLIT_MODE_LABELS,
    SPLIT_MODES,
    SPLIT_RULE,
    UsageSettings,
    UsageSettingsError,
)

# U4/task19 扩展位：载荷地址校验器——校验单一事实源，不复制第二份规则
from .speedtest import SpeedTestConfigError, validate_payload_url

# ---------------------------------------------------------------------------
# 常量（出处随注；端口/目录默认与原型一致）
# ---------------------------------------------------------------------------

# 端口默认（engines/singbox_engine.py DEFAULT_SOCKS_PORT/DEFAULT_HTTP_PORT
# 同值；引擎模块常量保留作兜底默认 §8-1，此处是设置模型默认值口径）
DEFAULT_SOCKS_PORT = 7898
DEFAULT_HTTP_PORT = 7897

# 数据目录默认（ui/cli.py DEFAULT_DB 的目录口径；db = <目录>/client.db；
# sources.json / ovpn_out / rulesets 等派生落点跟随有效数据目录，§3.2）
DEFAULT_DATA_DIR = "~/.local/share/gate-client"
DEFAULT_DB_FILENAME = "client.db"

# 自动探测间隔 UI 四档（现行四档 5/10/20/30；模型只锁正整数，
# 四档是呈现层约束——DESIGN §5.2/§8-5，放开任意分钟数只改 UI 不动模型）
AUTO_PROBE_INTERVAL_CHOICES = (5, 10, 20, 30)

# UsageSettings 五项同名字段（§3.1；usage_dict() 的键序）
USAGE_FIELDS = ("split_mode", "ad_block", "malware_block", "ipv6", "dns_server")

# 持久化包裹层版本（§4.1）；settings.json 最终路径若主会话另有定夺，
# 只改 DEFAULT_SETTINGS_FILE 这一处常量（§9-2）
SETTINGS_FILE_VERSION = 1
DEFAULT_SETTINGS_FILE = "~/.local/share/gate-client/settings.json"

_PORT_MIN = 1
_PORT_MAX = 65535


# ---------------------------------------------------------------------------
# 校验
# ---------------------------------------------------------------------------
class SettingsError(ValueError):
    """设置非法。报错不静默吞、不静默纠正（与 UsageSettingsError 同款口径）。"""


class SettingsFileError(SettingsError):
    """settings.json 文件层问题（§4.2）：呈现方给路径 + 重置入口。"""


class SettingsCorruptError(SettingsFileError):
    """JSON 损坏 / 包裹层结构缺失。处置（§4.2）：如实报错，重置 = 原文件
    改名留档再写默认——不静默覆盖、不删原始证据。"""


class SettingsVersionError(SettingsFileError):
    """version 高于当前支持：拒读报错，防新版本文件被旧版静默降级；
    不重置不覆盖（§4.2）。"""


def _require_bool(name: str, value) -> None:
    """严格布尔校验：0/1 冒充拒收（口径同 UsageSettings 布尔五项）。"""
    if not isinstance(value, bool):
        raise SettingsError(
            f"{name} 必须是布尔值（不许 0/1 冒充），得到 {value!r}")


def _require_port(name: str, value) -> None:
    """端口校验：int（bool 拒收）且 1..65535（§3.2）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(
            f"{name} 必须是整数端口，得到 {value!r}")
    if not _PORT_MIN <= value <= _PORT_MAX:
        raise SettingsError(
            f"{name} 越界（合法区间 {_PORT_MIN}..{_PORT_MAX}），得到 {value!r}")


def _require_positive_int(name: str, value) -> None:
    """正整数校验：int（bool 拒收）且 ≥1（§3.2 间隔口径）。"""
    if isinstance(value, bool) or not isinstance(value, int):
        raise SettingsError(
            f"{name} 必须是整数，得到 {value!r}")
    if value < 1:
        raise SettingsError(
            f"{name} 必须是正数（≥1），得到 {value!r}")


def _validate_usage_fields(split_mode, ad_block, malware_block, ipv6,
                           dns_server) -> None:
    """规则五项校验（§8-2 消镜像）：借 UsageSettings.__post_init__ 校验
    ——字段定义/校验/错误文案唯一事实源 = usage_model；错误翻译成
    SettingsError，文案原样透传，不另造一套话术。"""
    try:
        UsageSettings(split_mode=split_mode, ad_block=ad_block,
                      malware_block=malware_block, ipv6=ipv6,
                      dns_server=dns_server)
    except UsageSettingsError as exc:
        raise SettingsError(str(exc)) from None


# ---------------------------------------------------------------------------
# 设置模型
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Settings:
    """统一设置模型（§3.2，纯数据 + 校验）。

    默认值 = 现行行为（逐字段出处见模块 docstring）：客户端不做用户没
    要求的事，所有改变流量的行为都显式开启。规则五项为 UsageSettings
    同名字段——口径单一事实源 = usage_model（§8-2 消镜像），同一对象
    由 test_settings_landing.py 直接 import 断言机械锁定。
    """
    # —— 连接层：规则与网络（UsageSettings 五项，同名字段） ——
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
    # —— U4 扩展位：测速载荷地址（task19 / work10 §10、§13-G） ——
    # 默认空 = 未配置（客户端不内置地址，主人在设置页核定填入）；
    # 非空只认 http(s) 形态，校验单一事实源 = core/speedtest
    # .validate_payload_url（实测器同用一份，两处口径不打架）。
    speedtest_payload_url: str = ""

    def __post_init__(self):
        # 规则五项：校验与文案唯一事实源 = usage_model（§8-2 消镜像）
        _validate_usage_fields(self.split_mode, self.ad_block,
                               self.malware_block, self.ipv6, self.dns_server)

        # 端口：各自 1..65535 + 交叉互异（§3.2）。保留端口段
        # （10818/10819/10828/10829）不在此锁——那是呈现层校验
        # （ui/settings_dialog.py），DESIGN §8-4 原口径「避免实现细节进模型」
        _require_port("socks_port", self.socks_port)
        _require_port("http_port", self.http_port)
        if self.socks_port == self.http_port:
            raise SettingsError(
                f"socks_port 与 http_port 不能相同（{self.socks_port}）——"
                "两个入站监听同一端口必有一个起不来")

        # 数据目录：非空字符串（目录不存在不算错——首次运行时创建；
        # 消费端 expanduser，§3.2/§5.2）
        if isinstance(self.data_dir, bool) or not isinstance(self.data_dir, str) \
                or not self.data_dir.strip():
            raise SettingsError(
                f"data_dir 必须是非空目录路径，得到 {self.data_dir!r}")

        # 界面层自动探测：严格布尔 + 正整数间隔（§3.2）
        _require_bool("auto_probe_enabled", self.auto_probe_enabled)
        _require_positive_int("auto_probe_interval_min",
                              self.auto_probe_interval_min)
        # U4 扩展位（task19）：载荷地址空 = 未配置（合法默认）；非空
        # 只认 http(s) 形态（校验器与实测器同源，见模块 docstring）。
        # 校验失败换 SettingsError 抛——设置层的错归设置层错误族。
        if self.speedtest_payload_url:
            try:
                validate_payload_url(self.speedtest_payload_url)
            except SpeedTestConfigError as exc:
                raise SettingsError(f"speedtest_payload_url: {exc}") from exc

    # ---------------- 序列化（§3.4） ----------------
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
            "speedtest_payload_url": self.speedtest_payload_url,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Settings":
        """缺字段补默认值；多余字段抛 SettingsError（拒绝静默忽略——
        与 UsageSettings.from_dict 同口径，§3.4 定「抛错」）。"""
        if not isinstance(d, dict):
            raise SettingsError(f"设置必须是 dict，得到 {d!r}")
        extra = set(d) - set(cls.__dataclass_fields__)
        if extra:
            raise SettingsError(
                f"设置含未知键（拒绝静默忽略）: {sorted(extra, key=repr)}")
        return cls(**d)

    def usage_dict(self) -> dict:
        """UsageSettings 五项字段子集（键序 = USAGE_FIELDS）。
        消费形态（§3.1/§6-2）：UsageSettings(**s.usage_dict()) →
        build_route_config。"""
        return {k: getattr(self, k) for k in USAGE_FIELDS}

    def usage_settings(self) -> UsageSettings:
        """五项 → UsageSettings 实例（§3.1 集成消费形态，U2 侧直接可用）。"""
        return UsageSettings(**self.usage_dict())


# ---------------------------------------------------------------------------
# 合并（§3.4：override 只覆盖显式给出的字段）
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


# ---------------------------------------------------------------------------
# 持久化（§4；原型不实现 IO，本段落地）
# ---------------------------------------------------------------------------
def settings_file_path(explicit_db=None, env_db=None) -> str:
    """settings.json 落点（§4.1/§8-3）：显式覆盖目录（--db 或
    GATE_CLIENT_DB，与 client.db 同目录）优先，否则内置默认目录
    （DEFAULT_SETTINGS_FILE 常量——最终路径若主会话另有定夺只改这一处，
    §9-2）。刻意不跟随 data_dir 设置：否则「设置说设置在哪」自举成环
    （§4.1）。"""
    base = explicit_db or env_db
    if base:
        return os.path.join(os.path.dirname(os.path.abspath(base)),
                            "settings.json")
    return os.path.expanduser(DEFAULT_SETTINGS_FILE)


def resolve_db_path(explicit_db=None, env_db=None, settings=None) -> str:
    """节点库路径四档优先级（§8-3）：`--db` 显式 > `GATE_CLIENT_DB` >
    settings.data_dir > 内置默认。显式指定 beats 存储偏好（§4.3）。"""
    if explicit_db:
        return explicit_db
    if env_db:
        return env_db
    data_dir = settings.data_dir if settings is not None else DEFAULT_DATA_DIR
    return os.path.join(os.path.expanduser(data_dir), DEFAULT_DB_FILENAME)


def derived_data_dir(store_path="", settings=None) -> str:
    """派生落点（ovpn_out / sources.json）的基准数据目录（§8-1）。

    store_path 非空 → 与库同目录（现行「同目录派生」口径不变；store.path
    已按 resolve_db_path 的四档优先级解析，无显式覆盖时即
    settings.data_dir 的目录）；store_path 为空（无库路径可依）→
    settings.data_dir（未给 settings → 内置默认）。"""
    if store_path:
        return os.path.dirname(os.path.abspath(store_path))
    if settings is not None:
        return os.path.expanduser(settings.data_dir)
    return os.path.expanduser(DEFAULT_DATA_DIR)


class SettingsStore:
    """settings.json 读写（§4.1/§4.2）。

    - load()：包裹层校验（version 管理在包裹层，不进 from_dict）；
      字段层校验交给 Settings.from_dict（缺字段补默认、未知键/非法值
      抛 SettingsError 上抛给呈现方——不静默丢配置）。
    - save()：原子写（临时文件 + os.replace，与 task10 sources.json 同
      口径），每次全量写。
    - reset(tag)：现文件改名留档（corrupt→`.corrupt-<UTC时间戳>`，
      恢复默认→`.bak-<UTC时间戳>`；§4.2/§5.4——不静默覆盖、不删原始
      证据）再写默认，返回默认 Settings。
    """

    def __init__(self, path: str):
        self.path = path

    def exists(self) -> bool:
        return os.path.isfile(self.path)

    def load(self) -> Settings:
        """读 settings.json → Settings（§4.2 逐行处置，异常上抛给呈现方）。"""
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError as exc:
            raise SettingsFileError(f"设置文件不存在: {self.path}") from exc
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise SettingsCorruptError(
                f"设置文件损坏（JSON 解析失败）：{self.path}（{exc}）") from None
        if not isinstance(data, dict):
            raise SettingsCorruptError(
                f"设置文件损坏（顶层不是 JSON 对象）：{self.path}")
        version = data.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            raise SettingsCorruptError(
                f"设置文件损坏（包裹层缺 version 或不是整数）：{self.path}")
        if version > SETTINGS_FILE_VERSION:
            raise SettingsVersionError(
                f"设置文件由更新版本客户端写入（version {version} > 当前支持 "
                f"{SETTINGS_FILE_VERSION}），拒读防降级：{self.path}")
        if version < 1:
            raise SettingsCorruptError(
                f"设置文件包裹层 version 非法（{version}）：{self.path}")
        if not isinstance(data.get("settings"), dict):
            raise SettingsCorruptError(
                f"设置文件损坏（包裹层缺 settings 字段）：{self.path}")
        return Settings.from_dict(data["settings"])

    def save(self, settings: Settings) -> None:
        """原子写：{"version": 1, "settings": <全字段平面 dict>}。"""
        if not isinstance(settings, Settings):
            raise SettingsError(
                f"save 只收 Settings，得到 {type(settings).__name__}")
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        payload = {"version": SETTINGS_FILE_VERSION,
                   "settings": settings.to_dict()}
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def reset(self, tag: str = "bak") -> Settings:
        """现文件改名留档（不删原始证据）再写默认（§4.2/§5.4）。
        返回默认 Settings 实例。"""
        if self.exists():
            stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            os.replace(self.path, f"{self.path}.{tag}-{stamp}")
        fresh = Settings()
        self.save(fresh)
        return fresh


def migrate_qsettings_auto_probe(reader) -> dict:
    """QSettings 旧两键 → 初始 override dict（§4.3/§8-6）。

    reader：GUI 注入的读取回调（无参数），返回形如
    {"auto_probe_enabled": bool|None, "auto_probe_interval_min": int|None}
    的 dict。只认 auto_probe 两键——**last_node_id 与其他键一概不搬**
    （界面状态不是设置，§4.3）；类型不符/读不到 → 该键用默认（即不进
    override）。返回 {} 表示无可迁移项。"""
    overrides = {}
    try:
        vals = reader() or {}
    except Exception:
        return {}
    enabled = vals.get("auto_probe_enabled")
    if isinstance(enabled, bool):
        overrides["auto_probe_enabled"] = enabled
    interval = vals.get("auto_probe_interval_min")
    if isinstance(interval, int) and not isinstance(interval, bool) \
            and interval >= 1:
        overrides["auto_probe_interval_min"] = int(interval)
    return overrides


def bootstrap_settings(store: SettingsStore, qsettings_reader=None) -> Settings:
    """设置子系统初始化（§4.2/§4.3）：

    - 文件在 → 原样 load（损坏/版本/未知键/非法值异常上抛，由呈现方按
      §4.2 处置——GUI 弹窗给重置入口、CLI 报错退出码 2）。
    - 文件不在（首次运行）→ QSettings 两键迁移（§4.3，只此一次：落盘后
      文件存在，下次走加载路径，迁移不会重跑）→ 默认值 + 迁移值写盘。
    - qsettings_reader=None（CLI / 无 Qt 环境）→ 无迁移，纯默认落盘。

    注意：CLI 刻意不走本函数（read-only 消费者，settings.json 的首次
    初始化与迁移只在 GUI 进行——避免无 PySide6 的 CLI 抢先把文件建成
    纯默认、丢掉 QSettings 偏好）。"""
    if store.exists():
        return store.load()
    overrides = {}
    if qsettings_reader is not None:
        overrides = migrate_qsettings_auto_probe(qsettings_reader)
    settings = merge(Settings(), overrides)
    store.save(settings)
    return settings
