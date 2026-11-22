#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
吞吐实测器（core/speedtest.py）—— U4：测速与速度展示（task19 / work10 §10）
==============================================================================
§10 点名的唯一真新增：连接态经当前会话 HTTP 入站（或未连接时经临时引擎，
端口纪律 10818/10819 手动组，同 ui 层 _test_node 形态）下载一段测速载荷
（分档 5/25 MB、可中止），结果写 NodeMetrics.speed_mbps 并同时写
extra["speed_measured_at"] 时间戳——首次让这个字段有「实测」来源，与
feed 自报值在数据层就分得清（展示层据此三态区分，见 format_speed_display）。

职责边界（本模块只有纯逻辑，红线逐条落死）：
- 传输层为注入参数（opener 函数）：core 内不 import urllib.request/socket、
  不直接碰真实网络；真实 opener 由 UI 层构造（接线清单 W2 的
  _make_proxy_opener，形态同 verify._opener）。测试注入假 opener。
- 载荷地址只认设置传入（§13-G 核定制）：客户端不内置任何地址，本模块
  连示例地址都不写——validate_payload_url 只认 http(s) 形态，空地址
  按配置错误处理；批量入口用 board_batch_plan 在空槽时给「未配置」
  提示而非静默。
- 只许用户触发或批量测时跑一次，绝不常驻/后台偷跑（常驻下载会自己
  干扰自己）；本模块没有任何定时器、线程、循环调度。
- 永不自动切换节点：本模块只算数、回写、拼展示文案，没有任何连接/
  选择/切换语义（铁律）。
- 定时监测不归本模块：⑤区状态行只读 task12 Settings 的 auto_probe
  两字段（monitor_status_text），不持有第二份开关/间隔。

opener 合同（注入参数）：
    opener(url: str) -> file-like   # 有 read(n)/close()，如 HTTPResponse
    - opener 已按目标代理入口绑定好（连接态=当前会话 HTTP 入站地址；
      未连接=临时引擎的 http_proxy_url，10818/10819 组）；
    - 连接阶段的网络错误按 OSError 族抛出（超时类抛 TimeoutError，
      含 socket.timeout）；读取阶段抛 OSError 族——本模块按阶段归类
      （连接阶段→unreachable/timeout，读取阶段→interrupted）；
    - 非 OSError/TimeoutError 的异常视为编程错误，原样上抛不吞。

中止合同：abort_fn 为返回布尔的回调（如批量停止旗 self._batch_stop），
每次读块前检查；置真即停，返回结果对象而非异常——
  - 已下到数据 → partial 实测（按已下载字节/实际耗时算，可写回）；
  - 零字节 → fail(aborted)（无数据不冒充实测，不写时间戳）。

时钟注入：clock_fn 默认 time.monotonic，测试给假时钟；耗时 = 实际
时钟差，Mbps = 实际字节×8 ÷ 实际秒 ÷ 1e6，逐档可对账。

失败类别（逐类可区分，全部不写时间戳、不碰节点既有自报值）：
    unreachable  连不上（opener 阶段非超时类 OSError）
    timeout      超时（opener 阶段 TimeoutError；或下载超出 budget_s 预算）
    empty        载荷为空（0 字节就干净结束）
    interrupted  中途断流（>0 字节后流提前结束/读取出错）
分组口径：group_nodes_by_board / board_node_ids / board_batch_plan 只
消费 task11 classify_proto（board_of 六键 + unclassified），不许自造
第二套归类；独立导入本模块时用 classify= 参数显式注入。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

# ---------------------------------------------------------------------------
# 常量（口径出处随注）
# ---------------------------------------------------------------------------

# 分档大小（work10 §10：5/25 MB、可中止；批量测走小档，深测走大档）
TIERS_MB = (5, 25)

# 一次下载的时间预算（秒）：宁可超时失败不给假数据；用户可随时中止
DEFAULT_BUDGET_S = 120.0
DEFAULT_CHUNK = 64 * 1024

_MiB = 1024 * 1024

# 结果三态（简报口径：ok / partial / fail，逐类可区分）
ST_OK = "ok"            # 完整跑完 → 可写回
ST_PARTIAL = "partial"  # 用户中止但下到了数据 → 按 已下载部分算，可写回
ST_FAIL = "fail"        # 失败 → 不写回

FAIL_UNREACHABLE = "unreachable"   # 连不上
FAIL_TIMEOUT = "timeout"           # 超时（连接超时或超预算）
FAIL_EMPTY = "empty"               # 载荷为空
FAIL_INTERRUPTED = "interrupted"   # 中途断流
CATEGORY_ABORTED = "aborted"       # 用户中止（partial 的类别标注；零字节中止归 fail）

# 实测时间戳的 extra 键（简报定死：有它就优先显示实测）
MEASURED_AT_KEY = "speed_measured_at"

# 空槽提示（简报口径：空槽显式提示「未配置测速载荷」，不许静默失败，
# 更不许内置地址凑合；由 board_batch_plan 消费，UI 拿去 _show_info）
UNCONFIGURED_HINT = ("未配置测速载荷：先把主人核定的载荷地址填进设置，"
                     "才能测吞吐——本批只测延迟。")


class SpeedTestConfigError(ValueError):
    """测速配置非法（载荷地址空/非 http(s) 形态、分档不在 TIERS_MB）。
    配置问题如实报错，不冒充网络失败类别，也不静默纠正。"""


# ---------------------------------------------------------------------------
# 载荷地址校验（单一事实源：settings_proto 的 U4 扩展位接线也调这里）
# ---------------------------------------------------------------------------
def validate_payload_url(url) -> str:
    """载荷地址校验：非空、只认 http(s) 形态（scheme + 主机名）。
    返回去首尾空白的地址；非法抛 SpeedTestConfigError。
    空地址按「未配置」报错——§13-G：客户端不内置地址，由主人核定后
    填入设置（settings_proto 扩展位，默认空）。"""
    if not isinstance(url, str) or not url.strip():
        raise SpeedTestConfigError(
            "测速载荷地址为空——未配置（地址须主人核定后填入设置）")
    stripped = url.strip()
    parsed = urlparse(stripped)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise SpeedTestConfigError(
            f"测速载荷地址必须是 http(s)://主机/路径 形态，得到 {url!r}")
    return stripped


# ---------------------------------------------------------------------------
# 结果对象
# ---------------------------------------------------------------------------
@dataclass
class SpeedTestResult:
    """一次吞吐实测的结果（中止返回结果而非异常）。

    status  ok / partial / fail（三态常量）
    mbps    实测 Mbps（按实际字节/实际耗时）；fail 与零耗时为 None
    partial 的 category 固定 CATEGORY_ABORTED；ok 的 category 为 None
    """
    status: str
    tier_mb: int
    bytes_downloaded: int
    elapsed_s: float
    mbps: Optional[float]
    category: Optional[str]
    aborted: bool = False
    detail: str = ""

    @property
    def measured(self) -> bool:
        """是否构成一次可写回的实测：完整跑完（ok），或中止但下到了
        部分数据（partial）。失败与零字节中止都不算——绝不拿空结果
        或自报值冒充实测（写回函数以此为准）。"""
        return (self.status in (ST_OK, ST_PARTIAL)
                and self.mbps is not None and self.mbps > 0)


def _mbps(nbytes: int, elapsed_s: float) -> Optional[float]:
    """实际字节/实际耗时 → Mbps。零或负耗时测不出可信速率，返回 None
    （不除零冒充数）。"""
    if elapsed_s <= 0:
        return None
    return nbytes * 8 / (elapsed_s * 1e6)


# ---------------------------------------------------------------------------
# 主测函数
# ---------------------------------------------------------------------------
def run_throughput_test(payload_url, opener, tier_mb, *, abort_fn=None,
                        clock_fn=time.monotonic, budget_s=DEFAULT_BUDGET_S,
                        chunk_size=DEFAULT_CHUNK) -> SpeedTestResult:
    """经注入的 opener 下载测速载荷，按实际字节/实际耗时算 Mbps。

    参数：
    - payload_url：载荷地址（设置读入；§13-G，客户端不内置）。
    - opener：传输层注入参数（合同见模块 docstring；core 不碰真实网络）。
    - tier_mb：分档大小，只认 TIERS_MB（5/25 MB）。
    - abort_fn：中止旗回调（每次读块前检查；中止返回结果而非异常）。
    - clock_fn：单调时钟（测试注假时钟，逐档对账）。
    - budget_s：整次下载的时间预算，超出按 timeout 失败（宁缺毋假）。
    - chunk_size：单次读块大小（测试可调小以塑造读序列）。

    流程：opener 拿流 → 循环读到档位字节数为止 → 按实际字节/耗时算
    Mbps。中途置 abort → partial（有数据）或 fail(aborted)（零字节）；
    流提前结束/读取报错 → interrupted；0 字节结束 → empty；连接阶段
    异常 → timeout（TimeoutError）/unreachable（其余 OSError）。
    """
    url = validate_payload_url(payload_url)
    if isinstance(tier_mb, bool) or tier_mb not in TIERS_MB:
        raise SpeedTestConfigError(
            f"tier_mb 只认分档 {TIERS_MB}（MB），得到 {tier_mb!r}")
    if isinstance(budget_s, bool) or not isinstance(budget_s, (int, float)) \
            or budget_s <= 0:
        raise SpeedTestConfigError(f"budget_s 必须是正数，得到 {budget_s!r}")
    if isinstance(chunk_size, bool) or not isinstance(chunk_size, int) \
            or chunk_size < 1:
        raise SpeedTestConfigError(f"chunk_size 必须是正整数，得到 {chunk_size!r}")

    expected = tier_mb * _MiB
    start = clock_fn()
    downloaded = 0
    aborted = False
    stream = None
    try:
        stream = opener(url)
        while downloaded < expected:
            if abort_fn is not None and abort_fn():
                aborted = True
                break
            if clock_fn() - start > budget_s:
                return SpeedTestResult(
                    status=ST_FAIL, tier_mb=tier_mb,
                    bytes_downloaded=downloaded, elapsed_s=clock_fn() - start,
                    mbps=None, category=FAIL_TIMEOUT, aborted=False,
                    detail=f"下载超出时间预算 {budget_s:g}s（宁缺毋假，不记实测）")
            chunk = stream.read(min(chunk_size, expected - downloaded))
            if not chunk:
                if downloaded == 0:
                    return SpeedTestResult(
                        status=ST_FAIL, tier_mb=tier_mb, bytes_downloaded=0,
                        elapsed_s=clock_fn() - start, mbps=None,
                        category=FAIL_EMPTY, aborted=False,
                        detail="载荷为空：一个字节都没下到流就结束了")
                return SpeedTestResult(
                    status=ST_FAIL, tier_mb=tier_mb, bytes_downloaded=downloaded,
                    elapsed_s=clock_fn() - start, mbps=None,
                    category=FAIL_INTERRUPTED, aborted=False,
                    detail=(f"中途断流：应下 {expected} 字节，"
                            f"{downloaded} 字节后流提前结束"))
            downloaded += len(chunk)
    except TimeoutError as exc:
        # TimeoutError 在连接阶段=超时；在读取阶段=断流（超时打断传输）
        if stream is None:
            return SpeedTestResult(
                status=ST_FAIL, tier_mb=tier_mb, bytes_downloaded=downloaded,
                elapsed_s=clock_fn() - start, mbps=None, category=FAIL_TIMEOUT,
                aborted=False, detail=f"连接阶段超时: {exc}")
        return SpeedTestResult(
            status=ST_FAIL, tier_mb=tier_mb, bytes_downloaded=downloaded,
            elapsed_s=clock_fn() - start, mbps=None, category=FAIL_INTERRUPTED,
            aborted=False, detail=f"读取阶段超时（视作中途断流）: {exc}")
    except OSError as exc:
        # 其余 OSError：连接阶段=连不上；读取阶段=断流
        if stream is None:
            return SpeedTestResult(
                status=ST_FAIL, tier_mb=tier_mb, bytes_downloaded=downloaded,
                elapsed_s=clock_fn() - start, mbps=None,
                category=FAIL_UNREACHABLE, aborted=False,
                detail=f"连不上（经代理入口建立连接失败）: {exc}")
        return SpeedTestResult(
            status=ST_FAIL, tier_mb=tier_mb, bytes_downloaded=downloaded,
            elapsed_s=clock_fn() - start, mbps=None, category=FAIL_INTERRUPTED,
            aborted=False, detail=f"读取中断（视作中途断流）: {exc}")
    finally:
        if stream is not None:
            close = getattr(stream, "close", None)
            if callable(close):
                try:
                    close()
                except OSError:
                    pass  # 关闭失败不影响结果；不吞非 OSError 的编程错误

    elapsed = clock_fn() - start
    if aborted:
        if downloaded > 0:
            return SpeedTestResult(
                status=ST_PARTIAL, tier_mb=tier_mb,
                bytes_downloaded=downloaded, elapsed_s=elapsed,
                mbps=_mbps(downloaded, elapsed), category=CATEGORY_ABORTED,
                aborted=True,
                detail=f"已中止：按已下载 {downloaded}/{expected} 字节计 partial 实测")
        return SpeedTestResult(
            status=ST_FAIL, tier_mb=tier_mb, bytes_downloaded=0,
            elapsed_s=elapsed, mbps=None, category=CATEGORY_ABORTED,
            aborted=True, detail="已中止：未下到任何数据，不记实测")
    return SpeedTestResult(
        status=ST_OK, tier_mb=tier_mb, bytes_downloaded=downloaded,
        elapsed_s=elapsed, mbps=_mbps(downloaded, elapsed), category=None,
        aborted=False, detail="")


# ---------------------------------------------------------------------------
# 结果回写（实测/自报在数据层的分界线）
# ---------------------------------------------------------------------------
def write_back(node, result: SpeedTestResult, *, now_fn=time.time) -> bool:
    """实测结果回写节点（简报口径）：写 metrics.speed_mbps + 
    extra[MEASURED_AT_KEY]。只有真跑完（ok）或中止但下到部分数据
    （partial）才写时间戳；失败与零字节中止一律不写、不碰节点既有
    自报值——绝不拿自报值冒充实测。

    node 按 client.core.model.Node 鸭子类型（有 metrics.speed_mbps 与
    extra dict），不 import 模型类，保持本模块可独立加载。返回是否写入。
    """
    if not result.measured:
        return False
    node.metrics.speed_mbps = round(result.mbps, 2)
    node.extra[MEASURED_AT_KEY] = float(now_fn())
    return True


# ---------------------------------------------------------------------------
# 实测/自报三态展示口径（节点行/①区速度行共用，简报交付物 1-2）
# ---------------------------------------------------------------------------
def format_speed_display(node, *, fmt="%m-%d %H:%M",
                         localtime_fn=time.localtime) -> str:
    """给节点行/①区速度行的纯函数（work10 §10 口径）：

    - 有实测时间戳且值 > 0 → 「实测 X.X Mbps（时间）」（实测优先）；
    - 否则值 > 0          → 「自报 X.X Mbps」（feed 自报，必须分得清）；
    - 都没有              → 「—」。

    数据层分界 = extra[MEASURED_AT_KEY] 在不在：在，则 speed_mbps 即
    最近实测值；不在，则是 feed 自报值。时间戳在但值 ≤0 视为无值（
    时间戳不冒充数）。localtime_fn 可注入（测试用 gmtime 定死时区）。
    """
    metrics = getattr(node, "metrics", None)
    mbps = getattr(metrics, "speed_mbps", None) if metrics is not None else None
    extra = getattr(node, "extra", None) or {}
    measured_at = extra.get(MEASURED_AT_KEY)
    value_ok = (isinstance(mbps, (int, float)) and not isinstance(mbps, bool)
                and mbps > 0)
    stamp_ok = (isinstance(measured_at, (int, float))
                and not isinstance(measured_at, bool))
    if stamp_ok and value_ok:
        ts_s = time.strftime(fmt, localtime_fn(float(measured_at)))
        return f"实测 {mbps:.1f} Mbps（{ts_s}）"
    if value_ok:
        return f"自报 {mbps:.1f} Mbps"
    return "—"


# ---------------------------------------------------------------------------
# ⑤区监测状态行文案（只读 task12 Settings 两字段，不持有第二份状态）
# ---------------------------------------------------------------------------
def monitor_status_text(settings) -> str:
    """⑤测速与监测区状态行文案（task19 简报：定时监测整合口径）。

    settings 按 task12 core/settings_proto.Settings 鸭子类型——只读
    auto_probe_enabled / auto_probe_interval_min 两个属性（自动探测的
    开关/间隔的唯一口径），不 import 设置模型、不存第二份状态、不造
    第二套定时器。开关关/间隔缺一眼可读。"""
    enabled = getattr(settings, "auto_probe_enabled", None)
    interval = getattr(settings, "auto_probe_interval_min", None)
    if enabled is True and isinstance(interval, int) \
            and not isinstance(interval, bool) and interval >= 1:
        return (f"定时监测：开 · 每 {interval} 分钟自动探测延迟"
                "（开关与间隔在设置页改）")
    if enabled is False:
        return "定时监测：关（自动探测未开启，可在设置页开启）"
    return "定时监测：设置读不出有效值（auto_probe 开关/间隔缺失或非法）"


# ---------------------------------------------------------------------------
# 按板块批量测的分组口径（只消费 task11 classify_proto，不许自造）
# ---------------------------------------------------------------------------
def group_nodes_by_board(nodes, *, classify=None) -> dict:
    """整批节点按 task11 板块键分组（board_of 六键 + unclassified），只读。

    节点鸭子类型：需要 .id 与 .to_dict()（与 usage_window.refresh_boards
    同口径）；组内保持传入顺序——展示排序归 history.sorted_nodes，分组
    不管顺序。classify= 注入 classify_proto 模块；默认懒加载同包模块，
    独立导入本模块（离线测试）时必须显式注入，不许自造归类口径。"""
    if classify is None:
        try:
            from . import classify_proto as classify
        except ImportError as exc:
            raise SpeedTestConfigError(
                "分组口径依赖同包 classify_proto（task11）；独立导入本模块时"
                "请显式注入 classify=…，不许自造第二套归类。") from exc
    groups = {b: [] for b in classify.BOARDS}
    groups[classify.UNCLASSIFIED] = []
    for n in (nodes or []):
        board = classify.board_of(classify.dims_of(n.to_dict()))
        groups.setdefault(board, []).append(n)
    return groups


def board_node_ids(nodes, board_key, *, classify=None) -> list:
    """某板块键的待测节点 id 清单（board_batch_plan 的分组底座）。"""
    return [n.id for n in
            group_nodes_by_board(nodes, classify=classify).get(board_key, [])]


def board_batch_plan(nodes, board_key, payload_url, *, classify=None) -> dict:
    """②区按板块批量测入口的决策（纯函数；GUI 拿计划直接执行，接线见
    work19/DESIGN.md W2）：分组、载荷门卫、提示语都在这里定，UI 不另判。

    返回：
      board         板块键（六键之一或 unclassified）
      ids           该板块待测节点 id（board_of 分组口径，组内原序）
      measure_speed 载荷已配置（非空且形态合法）才 True——空槽绝不静默跑
      notice        开跑前给用户的一句话（未配置/非法/空板块），无则 None
    """
    ids = board_node_ids(nodes, board_key, classify=classify)
    if not ids:
        return {"board": board_key, "ids": [], "measure_speed": False,
                "notice": "本板块没有可测的节点。"}
    notice = None
    measure = False
    url = (payload_url or "").strip()
    if not url:
        notice = UNCONFIGURED_HINT
    else:
        try:
            validate_payload_url(url)
            measure = True
        except SpeedTestConfigError as exc:
            notice = f"测速载荷地址不可用（{exc}），本批只测延迟。"
    return {"board": board_key, "ids": ids, "measure_speed": measure,
            "notice": notice}
