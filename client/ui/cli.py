#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
命令行界面（ui/cli.py）—— M1 的界面层，只调 core，不碰引擎细节。

用法:
  python -m client.ui.cli --db client.db intake --pipeline-data data.json
  python -m client.ui.cli --db client.db intake --feed feed.csv --link "vless://..."
  python -m client.ui.cli --db client.db import-subscription --file sub.txt [--name 名称]
  python -m client.ui.cli --db client.db import-subscription --url <订阅地址>
  python -m client.ui.cli --db client.db list --scene support
  python -m client.ui.cli --db client.db pin <node_id>
  python -m client.ui.cli --db client.db verify <node_id>
  python -m client.ui.cli --db client.db connect <node_id>
  python -m client.ui.cli --db client.db status / disconnect
  python -m client.ui.cli --db client.db doctor
  python -m client.ui.cli --db client.db export-diag [--out 路径]
  python -m client.ui.cli --db client.db lists recheck [--dry-run]

注：connect 的会话状态只在本进程内有效（M1 CLI 形态）；常驻守护/托盘在 M3。
verify 默认走真实 probe（本机测量）；测试通过 main(probe_factory=...) 注入假 probe。
import-subscription 的输出只报汇总数字与格式，不回显订阅地址、不打印节点链接原文。
sources collect 的输出只报数字（种子/候选/验真/入库/门槛/截断/开关），
不回显任何节点地址、种子或候选地址原文、出口 IP；手动命令永远可用
（不受自动开关控制）。自动定时开关在统一设置（auto_collect_enabled，
默认关——铁律：不经主人点头，客户端不许自己联网拉新节点），GUI 定时器
按它到点跑同一个 _sources_collect；CLI 本体无定时器。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from ..core import diag
from ..core import diag_collect
from ..core import failover
from ..core import history
from ..core import list_recheck
from ..core import ruleset_cache
from ..core import settings_store, site_blocklist
from ..core.health import (check_ports_free, describe_occupied,
                           proxy_conflict, read_system_proxy)
from ..core.model import SCENES, SOURCES
from ..core.usage_model import UsageSettings
from ..core.orchestrator import Orchestrator, OrchestratorError
from ..core.settings_proto import (Settings, SettingsCorruptError,
                                   SettingsError, SettingsStore,
                                   SettingsVersionError, derived_data_dir,
                                   resolve_db_path, settings_file_path)
from ..core.store import Store
from ..core.verify import (VerifyOutcome, apply_outcome, make_real_probe,
                           pollution_of, verify_exit)
from ..engines.base import EngineError
from ..engines.openvpn_engine import OpenVPNEngine
from ..engines.singbox_engine import (DEFAULT_HTTP_PORT, DEFAULT_SOCKS_PORT,
                                      SINGBOX_PROTOCOLS, SingBoxEngine)
from ..sources import collector as collector_src
from ..sources import feed as feed_src
from ..sources import format_import as format_src
from ..sources import manual as manual_src
from ..sources import pipeline as pipeline_src
from ..sources import seed_config as seed_src
from ..sources import source_registry as registry_src
from ..sources import subscription as subscription_src
from ..sources.landing import run_startup_migration
from urllib.parse import urlsplit

DEFAULT_DB = os.environ.get(
    "GATE_CLIENT_DB",
    os.path.join(os.path.expanduser("~"), ".local", "share", "gate-client", "client.db"))

# CLI 复查临时引擎端口（task28）：与 GUI 手动测速 _TEMP_SOCKS_PORT/
# _TEMP_HTTP_PORT 同值同源（ui/settings_dialog.py RESERVED_PORTS 保留段）。
# CLI 是单次进程形态，与后台窗临时引擎撞口的窗口极小；真撞上即引擎启动
# 失败 → 该节点如实记「复查失败」，重跑即可（不静默换端口）。
RECHECK_SOCKS_PORT = 10818
RECHECK_HTTP_PORT = 10819


def _load_settings_readonly(args_db):
    """CLI 读统一设置（task24 §8-3/§4.2）：settings.json 在 → 读（损坏/
    版本/未知键异常上抛给 main 呈现，退出码 2）；不在 → 全默认、零提示
    零动作。CLI 刻意不落盘不迁移：settings.json 的首次初始化与 QSettings
    迁移只在 GUI 进行（§4.3），避免无 PySide6 的 CLI 抢先把文件建成纯
    默认、丢掉 QSettings 偏好。"""
    sfile = settings_file_path(explicit_db=args_db,
                               env_db=os.environ.get("GATE_CLIENT_DB"))
    st = SettingsStore(sfile)
    if not st.exists():
        return Settings()
    return st.load()


def _err(msg: str) -> int:
    print(f"[错误] {msg}", file=sys.stderr)
    return 2


def _fmt_node(n) -> str:
    ep = n.exit_profile
    scenes = "/".join(sorted(n.scenes)) or "-"
    lat = f"{n.metrics.latency_ms:.0f}ms" if n.metrics.latency_ms is not None else "?"
    exit_s = ep.ip or "未验"
    marks = ("📌" if n.pinned else "") + ("🚫" if n.banned else "") + \
            ("☠" if n.extra.get("quarantined") == "1" else "")
    return (f"{n.id}  {n.name}  [{n.protocol}/{n.source}] 出口={exit_s} "
            f"{ep.network_type} 延迟={lat} 场景={scenes} {marks}".rstrip())


# ---------------------------------------------------------------------------
def cmd_intake(store: Store, args) -> int:
    nodes = []
    try:
        if args.pipeline_data:
            nodes += pipeline_src.load_data_json_file(args.pipeline_data)
        if args.pipeline_nodes:
            nodes += pipeline_src.load_nodes_text_file(args.pipeline_nodes)
        if args.feed:
            nodes += feed_src.load_feed_file(args.feed)
        for link in args.link or []:
            nodes.append(manual_src.parse_link(link))
        for path in args.ovpn or []:
            nodes.append(manual_src.load_ovpn_file(path))
    except FileNotFoundError as exc:
        return _err(f"文件不存在: {exc.filename}")
    except (ValueError, RuntimeError) as exc:
        return _err(f"导入失败: {exc}")
    if not nodes:
        return _err("没有导入任何节点（检查参数或文件内容）")
    store.upsert_nodes(nodes)
    print(f"已收纳 {len(nodes)} 个节点（库内共 {store.count_nodes()} 个）")
    return 0


def cmd_import_subscription(store: Store, args, fetcher=None) -> int:
    """订阅导入本地库：--file 本地文件 / --url 订阅地址（恰好一个）。
    fetcher 默认 subscription_src.fetch_subscription_text（测试注入假 fetcher）。
    输出红线：只报汇总数字与格式，不回显订阅地址全文、不打印节点链接原文。"""
    if fetcher is None:
        fetcher = subscription_src.fetch_subscription_text
    if bool(args.file) == bool(args.url):
        return _err("import-subscription 需要 --file 与 --url 二选一（恰好一个）")
    subscription_name = args.name or ""
    try:
        if args.file:
            res = format_src.import_any_file(
                args.file, subscription_name=subscription_name)
        else:
            text = fetcher(args.url)
            res = format_src.import_any_text(
                text, subscription_url=args.url, subscription_name=subscription_name)
    except FileNotFoundError as exc:
        return _err(f"文件不存在: {exc.filename}")
    except ValueError as exc:
        return _err(f"导入失败: {exc}")
    except OSError as exc:   # 含 URLError / HTTPError / socket.timeout
        return _err(f"拉取订阅失败: {exc}")
    if not res.nodes:
        return _err("没有解析出任何节点（检查订阅内容或格式；"
                    "sing-box JSON 暂不支持）")
    store.upsert_nodes(res.nodes)
    if args.url:
        # 订阅地址导入即登记：下次一键更新不用再贴地址；已登记过不重复报错。
        try:
            _registry(store).register("subscription", args.url,
                                      args.name or "")
        except ValueError:
            pass
    print(f"已收纳 {len(res.nodes)} 个节点（跳过坏行 {res.skipped}，"
          f"格式 {res.format}，库内共 {store.count_nodes()} 个）")
    return 0


def _registry_path(store: Store) -> str:
    """登记表 JSON 落点：与节点库同目录的 sources.json（GUI/CLI 共用）。

    task30 起登记表事实源是 Store 的 sources 表（_registry 走 store 形态），
    本路径只在「表空且该文件存在」的一次性迁移（cmd_sources →
    migrate_registry_json_to_store）被读一次；此后 JSON 文件留存备查，
    不再被读写，也不再是事实源（写入只走表）。GUI（app.py）本段不动，
    暂仍走文件形态。"""
    return os.path.join(
        os.path.dirname(os.path.abspath(store.path or "client.db")),
        "sources.json")


def _registry(store: Store):
    """store-backed 登记表（task30）：读写走 Store 的 sources 表，与 nodes
    共用同一连接与 RLock；不再读 sources.json——存量 JSON 由 cmd_sources
    首次使用表形态时一次性只读搬进表（migrate_registry_json_to_store）。"""
    return registry_src.SourceRegistry(store=store)


def cmd_sources(store: Store, args, fetcher=None, engine_factory=None,
                settings_file=None) -> int:
    """来源登记命令：list/add/update/update-all/remove/enable/disable/collect。
    fetcher 为测试注入点（默认真联网拉取）；engine_factory 为 collect 验真
    临时引擎的测试注入点（测试注假引擎 + 假探针，零联网）。
    settings_file = main() 解析出的 settings.json 落点（collect 门槛数字
    现读它，与 cmd_connect 的 settings_file 同款落点口径；未给时按
    「与库同目录」推导兜底，task17 _usage_provider 先例）。
    task30：首次使用表形态时（表空且 sources.json 存在）把存量 JSON 只读
    搬进 sources 表，幂等；JSON 文件保留不动作回退，此后写入只走表。"""
    if not store.entries() and os.path.exists(_registry_path(store)):
        registry_src.migrate_registry_json_to_store(
            _registry_path(store), store)
    reg = _registry(store)
    if args.sources_cmd == "list":
        entries = reg.entries()
        if not entries:
            print("未登记任何来源")
            return 0
        for e in entries:
            host = urlsplit(e["address"]).netloc or "（官方默认）"
            lu = reg.last_update(e["id"])
            lu_txt = ("未更新过" if not lu
                      else ("成功" if lu.get("ok")
                            else f"失败：{lu.get('error', '')}"))
            print(f"{e['id']} [{e['type']}/{e['page']}] "
                  f"{e['name'] or '（未命名）'} · {host} · "
                  f"{'启用' if e['enabled'] else '停用'} · 上次：{lu_txt}")
        return 0
    if args.sources_cmd == "add":
        try:
            entry = reg.register(args.type, args.address or "",
                                 args.name or "")
        except ValueError as exc:
            return _err(str(exc))
        print(f"已登记 {entry['id']}（{entry['type']}）")
        return 0
    if args.sources_cmd in ("update", "update-all"):
        if args.sources_cmd == "update":
            if not reg.get(args.entry_id):
                return _err(f"未登记的来源: {args.entry_id}")
            reports = [registry_src.update_entry(
                reg, store, args.entry_id, fetch_fn=fetcher)]
        else:
            reports = registry_src.update_all(reg, store, fetch_fn=fetcher)
        for rep in reports:
            print(f"{rep.entry_id}：{rep.summary()}")
        return 0 if all(r.ok for r in reports) else 1
    if args.sources_cmd == "remove":
        if reg.remove(args.entry_id):
            print(f"已删除登记 {args.entry_id}（节点不动）")
        else:
            print(f"未登记的来源: {args.entry_id}")
        return 0
    if args.sources_cmd in ("enable", "disable"):
        try:
            reg.set_enabled(args.entry_id, args.sources_cmd == "enable")
        except ValueError as exc:
            return _err(str(exc))
        print(f"已{'启用' if args.sources_cmd == 'enable' else '停用'} "
              f"{args.entry_id}")
        return 0
    if args.sources_cmd == "collect":
        return _sources_collect(store, fetcher=fetcher,
                                engine_factory=engine_factory,
                                settings_file=settings_file)
    return _err(f"未知 sources 子命令: {args.sources_cmd}")


# ---------------------------------------------------------------------------
# sources collect：手动搜集接线（task27；work9 DESIGN §7.2 步骤 3–4 落地）
# ---------------------------------------------------------------------------
# task31 收口 work9 §7.3 的三个未定项（定稿数字与依据见 work31 DESIGN §2）：
# 转登记门槛（_collect_gate_ok 的连续通过计数）、候选池验真的批量节奏与
# 上限（_collect_enter_store 的截断 + 可注假时钟的候选间隔）、自动搜集
# 开关与定时器（开关默认关；GUI 定时器按 collect_interval_min 跑本文件
# 的 _sources_collect，CLI 本体仍只有手动一条命令、永远可用）。
# 门槛数字每轮现读统一设置（不持第二份可编辑副本）；「永不自动切换
# 节点」不因定时化松动——搜集只往库里加候选，不碰引擎、不碰连接。

COLLECT_TEMP_SOCKS_PORT = 10838   # 验真临时引擎独立端口（与 GUI 临时组
COLLECT_TEMP_HTTP_PORT = 10839    # 10818/10819、自动组 10828/10829 错开）


def _collect_parse(text: str) -> list:
    """候选正文 → 节点条目（work9 DESIGN §7.2 步骤 2 的既定配方）：既有
    format_import 四格式识别（b64 订阅 / 明文链接 / txt / Clash YAML）。
    种子页的候选地址抽取是 collector.extract_candidates（run_collection
    内建，本段零改动）。source=manual：搜集节点归手动导入页（与订阅导入
    的 D1 落页同口径）。解析认不出 → ValueError，由 run_collection 吞进
    dead_candidates（stage=parse），整轮不断。"""
    return format_src.import_any_text(text, source="manual").nodes


def _make_collect_temp_engine() -> SingBoxEngine:
    """验真临时引擎（真实模式默认）：独立端口起一个 sing-box，测完即停，
    不碰当前会话（口径同 GUI _test_node 的临时引擎纪律）。"""
    return SingBoxEngine(socks_port=COLLECT_TEMP_SOCKS_PORT,
                         http_port=COLLECT_TEMP_HTTP_PORT)


def _verify_candidate(node, engine_factory=None) -> VerifyOutcome:
    """单候选验真：既有验真流水线（verify_exit + make_real_probe），口径
    同 cmd_connect 的 verifier 分流——sing-box 系候选起临时引擎，探针经
    引擎实例自己的 http_proxy_url 造（流量经它出网，才测到候选的真实
    出口）；engine_factory 为测试注入点（假引擎 + 假探针，零联网）。引擎
    起不来（如缺 sing-box 二进制）→ 如实判失败，绝不降级直连探针——
    直连测到的是本机出口，拿来冒充候选出口就是假验真（work9 §0 红线 1）。
    OpenVPN 是 TUN/隧道模式，未连接时测不了（GUI _test_node 同款口径）→
    如实判失败；其余无引擎协议同样没有可验路径。"""
    if node.protocol in SINGBOX_PROTOCOLS:
        engine = (engine_factory or _make_collect_temp_engine)()
        try:
            handle = engine.start(node)
        except EngineError as exc:
            return VerifyOutcome(ok=False, reason=f"临时引擎启动失败: {exc}")
        try:
            return verify_exit(node,
                               make_real_probe(proxy=engine.http_proxy_url))
        finally:
            engine.stop(handle)
    if node.protocol == "openvpn":
        return VerifyOutcome(
            ok=False, reason="OpenVPN 是隧道模式，未连接时测不了（先连接再验）")
    return VerifyOutcome(ok=False,
                         reason=f"{node.protocol} 协议暂无本机引擎可验")


def _collect_gate_values(settings: Settings = None) -> dict:
    """门槛数字解析（task31 唯一入口）：settings 给定 → 读统一设置五
    字段（只读快照，不持第二份可编辑副本——调用方 _sources_collect 每轮
    经 settings_store.load_settings 现读传入）；None → Settings() 默认值
    兜底（「默认口径」= 开关关 + task27「过即进」门槛 + 本段定稿的节奏
    默认）。单一事实源 = settings_proto 默认值，本层不另立常数。"""
    s = settings if settings is not None else Settings()
    return {"enabled": bool(s.auto_collect_enabled),
            "interval_min": int(s.collect_interval_min),
            "per_round": int(s.collect_verify_per_round),
            "delay_ms": int(s.collect_verify_delay_ms),
            "promote_min": int(s.collect_promote_min_ok)}


def _collect_base_ok(outcome: VerifyOutcome) -> bool:
    """入库基础口径（task27 `_collect_gate_ok` 函数体逐字平移）：验真
    通过且出口未污染（pollution_of）——work9 DESIGN §0 红线 1 / §7.2
    步骤 4，与订阅更新 update_entry 的 merge_fresh「能过就进」同口径。
    纯函数：不记账、不碰库。"""
    if not outcome.ok or outcome.exit_profile is None:
        return False
    polluted, _reason = pollution_of(outcome.exit_profile)
    return not polluted


def _ledger_candidate(ledger: dict, seed_id: str, node_id: str,
                      now: float) -> dict:
    """账本候选档案（task31 新挂点，不另建文件）：挂在 collect_ledger.
    json 种子记录的 "collect_candidates" 子键下，按身份键 node.id（内容
    指纹，跨轮稳定）建档。种子记录本体 13 字段 schema 冻结（work25）——
    本函数只新增子键、既有键零触碰；collector.record_result 只改既定
    字段并整档案 dict(rec) 浅拷贝带回（子键原样存活）、save_ledger 整
    档案 json 落盘、load_ledger 只校验包裹层形状——对子键透明，
    collector.py 零改动（test_collect_thresholds 机械锁定）。种子记录
    缺失时经 collector 的 R0 建档补齐（同包私有复用，先例 =
    _merge_collected 复用 _GONE_KEYS；正常轮里该种子必有档案，这是
    防御分支）。"""
    rec = collector_src._seed_record(ledger, seed_id, now)
    cands = rec.setdefault("collect_candidates", {})
    prof = cands.get(node_id)
    if prof is None:
        prof = {"streak": 0, "last_status": "", "last_round": "",
                "updated_at": now}
        cands[node_id] = prof
    return prof


def _collect_gate_ok(outcome: VerifyOutcome, ledger: dict = None,
                     item=None, promote_min: int = 1,
                     now: float = None) -> bool:
    """入库门槛（task27 具名空槽；task31 在此收口「转登记门槛数字」）。

    - 只给 outcome（既有调用形态）= 纯基础口径（_collect_base_ok），
      零行为变化，task27 测试零改动；
    - 给 ledger + item（池内条目）：先按基础口径判定，再把本次结果记进
      账本候选档案（collect_ledger.json 种子记录的 collect_candidates
      子键，不另建文件）：基础口径过 → streak +1（last_status="passed"），
      连续次数达 promote_min 才放行；不过 → streak 清零（失败清零，
      last_status="failed"）。未达标只记账不入库（调用方记
      report["collect_held"]，不进死账）。
    - 连续口径：池去重后每候选每轮至多验一次，streak 逐轮累积；失败
      清零；缺席/截断不打断（缺席不是失败证据）。
    - now：记账时间戳注入点（离线测试注假时钟）；None → time.time()。"""
    base_ok = _collect_base_ok(outcome)
    if ledger is None or item is None:
        return base_ok
    ts = time.time() if now is None else float(now)
    sid = str(item.extra.get("collect_seed_id") or "")
    if not sid:
        return base_ok          # 无溯源键的池条目（正常轮不会出现）：退基础口径
    prof = _ledger_candidate(ledger, sid, item.id, ts)
    prof["updated_at"] = ts
    prof["last_round"] = str(item.extra.get("collect_round") or "")
    if base_ok:
        prof["streak"] = int(prof.get("streak", 0)) + 1
        prof["last_status"] = "passed"
        return prof["streak"] >= promote_min
    prof["streak"] = 0
    prof["last_status"] = "failed"
    return False


def _merge_collected(old, fresh):
    """同身份已在库时的本地状态保回（source_registry.merge_fresh §5 口径
    的搜集版，不打 registry_entry——搜集节点不属于任何登记条目，溯源由
    extra 的 collect_* 四键管）。搜集验真是刚测的新鲜证据：出口画像/
    延迟抖动/场景名以新验真为准（apply_outcome 已写回），场景与旧档并集；
    pinned/banned 由 store.upsert_node 自保；orig_name 保最早一次（「只存
    第一次」）；merged_superseded 快照带回；消失标记清除（验真活着 =
    回归，口径同 merge_fresh）。quarantined 不从旧档带回：新验真的污染
    判定已由 apply_outcome 重算，带回旧标记会盖住新鲜证据。"""
    if old is None:
        return fresh
    if old.extra.get("orig_name"):
        fresh.extra["orig_name"] = old.extra["orig_name"]
    if old.extra.get("merged_superseded") is not None:
        fresh.extra["merged_superseded"] = old.extra["merged_superseded"]
    for key in registry_src._GONE_KEYS:      # 同包私有常量复用，不抄第二份
        fresh.extra.pop(key, None)
    if fresh.metrics.latency_ms is None:
        fresh.metrics.latency_ms = old.metrics.latency_ms
    if fresh.metrics.jitter_ms is None:
        fresh.metrics.jitter_ms = old.metrics.jitter_ms
    fresh.scenes = set(old.scenes) | set(fresh.scenes)
    return fresh


def _collect_enter_store(store: Store, report: dict,
                         engine_factory=None, settings: Settings = None,
                         ledger: dict = None, sleeper=None,
                         now: float = None) -> tuple:
    """候选池 → 验真 → 入库（work9 DESIGN §7.2 步骤 4 的接线；task27 落
    骨架，task31 收口「每轮验真上限 / 候选间隔」两个具名空槽）。

    数字来源（不持第二份可编辑副本）：settings 给定 → 读统一设置落盘
    五字段（调用方 _sources_collect 每轮现读传入）；None → Settings()
    默认值（「默认口径」，单一事实源 = settings_proto 默认值）。
    - 每轮上限 collect_verify_per_round：池大只验前 N 个（池序 =
      run_collection 产出序），余下只记账（候选档案 last_status=
      "overflow" =「本轮未验」）留待下轮自然重验、不进死账（截断不是
      失败证据）；截断数记 report["collect_truncated"]。
    - 候选间隔 collect_verify_delay_ms：候选之间（首前末后不睡）经
      sleeper 注入点等待——sleeper=None 落 time.sleep，离线测试注假
      时钟验证；delay_ms=0 不睡。sleep 只发生在本注入点上，循环里
      不埋不可测的 wall sleep。
    - 门槛：_collect_gate_ok（连续通过计数进账本候选档案，未达
      collect_promote_min_ok 只记账不入库，记 report["collect_held"]，
      不进死账）。
    返回 (验真通过, 验真未过, 入库) 三个数（task27 既有形状，既有测试
    零改动；暂存数 / 截断数经 report 带走）。"""
    gate = _collect_gate_values(settings)
    ts = time.time() if now is None else float(now)
    pool = list(report.get("pool") or [])
    verify_pool = pool[:gate["per_round"]]
    overflow = pool[gate["per_round"]:]
    report["collect_truncated"] = len(overflow)
    for item in overflow:                      # 本轮未验：只记账，不记死
        if ledger is not None:
            sid = str(item.extra.get("collect_seed_id") or "")
            if sid:
                prof = _ledger_candidate(ledger, sid, item.id, ts)
                prof["last_status"] = "overflow"
                prof["last_round"] = str(report.get("round_id") or "")
                prof["updated_at"] = ts
    sleeper = sleeper or time.sleep
    passed = failed = entered = held = 0
    for idx, item in enumerate(verify_pool):
        if idx and gate["delay_ms"] > 0:       # 候选之间隔开（首前末后不睡）
            sleeper(gate["delay_ms"] / 1000.0)
        try:
            outcome = _verify_candidate(item, engine_factory=engine_factory)
        except Exception as exc:   # noqa: BLE001 - 单候选失败吞进记账，整轮不断
            outcome = VerifyOutcome(ok=False,
                                    reason=f"{type(exc).__name__}: {exc}")
        if _collect_gate_ok(outcome, ledger=ledger, item=item,
                            promote_min=gate["promote_min"], now=ts):
            passed += 1
            fresh = apply_outcome(item, outcome)
            store.upsert_node(
                _merge_collected(store.get_node(fresh.id), fresh))
            entered += 1
        elif _collect_base_ok(outcome):
            # 验真通过但连续次数未达门槛：只记账不入库，不是死账。
            # passed = 验真通过数（含暂存，DESIGN §8-2）——「验真通过 N」
            # 如实反映实测，「入库 M」如实反映落库，差额由门槛行解释。
            passed += 1
            held += 1
        else:
            failed += 1
            if outcome.ok:
                reason = (f"出口污染（{pollution_of(outcome.exit_profile)[1]}）")
            else:
                reason = outcome.reason or "验真失败"
            report["dead_candidates"].append({
                "url": item.extra.get("collected_from", ""),
                "seed_id": item.extra.get("collect_seed_id", ""),
                "stage": "verify",
                "error": reason,
            })
    report["collect_held"] = held
    report["candidates_dead"] = len(report["dead_candidates"])  # 账面保持一致
    return passed, failed, entered


def _print_collect_report(store: Store, report: dict, passed: int,
                          failed: int, entered: int, gate: dict = None,
                          gate_source: str = None) -> None:
    """整轮报告逐行打印。输出红线（与 cmd_import_subscription 不回显订阅
    地址同口径）：只报数字——节点地址、候选/种子地址原文、出口 IP、
    dead_candidates 明细与 seeds[].error（异常文本可能含地址）一律不上屏。
    task31 追加三行：门槛值（含「落盘设置 / 默认口径」来源注明与本轮
    暂存数）/ 本轮截断数 / 开关状态——仍只报数字与固定词。"""
    gate = gate or _collect_gate_values(None)
    print(f"搜集轮 {report['round_id']}：种子 {report['seeds_total']}"
          f"（成功 {report['seeds_ok']}、失败 {report['seeds_failed']}、"
          f"停用跳过 {report['seeds_skipped']}）")
    print(f"候选 {report['candidates_total']}"
          f"（去重 {report['candidates_unique']}、"
          f"死 {report['candidates_dead']}，其中验真未过 {failed}）")
    print(f"候选池 {report['nodes_unique']}：验真通过 {passed}")
    print(f"入库 {entered}（库内共 {store.count_nodes()} 个）")
    held = report.get("collect_held", 0)
    gate_line = (f"门槛：转登记须连续通过 {gate['promote_min']} 次 · "
                 f"每轮验真上限 {gate['per_round']} 个 · "
                 f"候选间隔 {gate['delay_ms']}ms"
                 f"（{gate_source or '默认口径'}）")
    if held:
        gate_line += f" · 本轮暂存 {held} 个（验真通过、连续次数未达）"
    print(gate_line)
    truncated = report.get("collect_truncated", 0)
    if truncated:
        print(f"本轮截断 {truncated} 个"
              "（超每轮上限，余下留待下轮验真，不记死账）")
    else:
        print("本轮截断 0 个")
    if gate["enabled"]:
        print(f"自动搜集开关：开（每 {gate['interval_min']} 分钟定时一轮；"
              "只加库，不碰连接）")
    else:
        print("自动搜集开关：关（定时器不跑；手动 sources collect 不受影响）")
    if report["zero_yield"]:
        print("本轮产出 0：如实报告，无收获，不进正式库（零产出轮不假装有收获）。")


def _sources_collect(store: Store, fetcher=None, engine_factory=None,
                     settings_file=None) -> int:
    """sources collect 本体：手动跑一轮自动搜集（CLI 触发形态只有手动
    这一条命令，永远可用、不受 auto_collect_enabled 控制——开关只管
    GUI 定时器是否到点自动跑同一个本函数）。

    流程 = work9 DESIGN §7.2 步骤 3–4 的接线：读种子配置（collect_seeds.
    json，seed_config_path 推导落点）→ 读健康账本（collect_ledger.json，
    collect_ledger_path 推导落点）→ run_collection 一轮（fetch_fn 走
    cmd_sources 既有 fetcher 注入点，默认复用 subscription.fetch_
    subscription_text 的联网拉取口径，不另起第二套联网实现；parse_fn 见
    _collect_parse）→ 账本落盘 → 候选池验真（门槛数字每轮现读统一设置，
    task31）→ 过门槛入库 → 逐行打印数字报告。

    门槛数字现读（task31，不持第二份可编辑副本）：settings_file（main/
    cmd_sources 传来的 settings.json 落点，§4.1 同款口径）> 未给时与库
    同目录推导（task17 _usage_provider 兜底先例）。文件在 → 落盘值；
    文件不在 / 读取失败（损坏等）→ Settings() 默认值兜底（「默认口径」，
    报告注明）——设置损坏的致命处置归 main 启动路径既有口径（退出码 2），
    collect 运行中撞上只按默认口径跑完本轮并如实注明。读取只发生在种子
    闸门通过之后，「未启用」早退路径零 IO。

    未配置（not_configured）/ 空清单 / 全停用：打印「未启用」退出 0——
    没配种子不是故障、不告警（work25 DONE 集成注意项）。配置或账本损坏
    → ValueError 显式报错退出 2（seed_config / collector 既有口径，绝不
    静默吞）。"""
    try:
        seeds, status = seed_src.load_seed_config(
            seed_src.seed_config_path(store.path))
    except ValueError as exc:
        return _err(str(exc))
    if status == seed_src.STATUS_NOT_CONFIGURED:
        # task34：无用户配置 → 用内置默认种子集（seed_config.default_seeds，
        # 逐个验活名单，记录见 work34/seeds-verification.md）；默认集也为
        # 空 → 照旧「未启用」早退。用户已有配置文件（含空清单/全停用）
        # 一律以用户文件为准，不覆盖、不合并——下面 EMPTY / 全停用两个
        # 早退分支原样不动，就是这条口径的执行面。
        seeds = seed_src.default_seeds()
        if not seeds:
            print("搜集未启用：还没有种子配置（collect_seeds.json），且内置"
                  "默认种子集为空——按 schema 填一张种子表即接入。")
            return 0
        print(f"未配置 collect_seeds.json：使用内置默认种子集（{len(seeds)} 个，"
              "为逐个实测验活后的名单）；建一张用户种子表即完全取代默认集。")
    elif status == seed_src.STATUS_EMPTY:
        print("搜集未启用：种子清单为空（collect_seeds.json 的 seeds 是空数组）。")
        return 0
    # 启用种子过滤：配置 enabled=false 的不进轮；账本停用的由 run_collection
    # 自己跳过（缓刑复测口径不动）——双闸各管各的清单，既有行为零改动。
    seeds = [s for s in seeds if s["enabled"]]
    if not seeds:
        print("搜集未启用：种子清单里没有启用的种子（enabled 全为 false）。")
        return 0
    # —— 门槛数字现读统一设置（task31）——
    gate_settings, gate_source = None, "默认口径"
    spath = (settings_file if settings_file is not None
             else settings_store.settings_path(
                 os.path.dirname(os.path.abspath(store.path or "client.db"))))
    if os.path.isfile(os.fspath(spath)):
        try:
            gate_settings = settings_store.load_settings(spath)
            gate_source = "落盘设置"
        except (settings_store.SettingsStoreError, SettingsError):
            gate_settings = None          # 损坏等：按默认口径跑完并注明
    ledger_path = seed_src.collect_ledger_path(store.path)
    try:
        ledger = collector_src.load_ledger(ledger_path)
    except ValueError as exc:
        return _err(str(exc))
    fetch_fn = fetcher if fetcher is not None \
        else subscription_src.fetch_subscription_text
    report = collector_src.run_collection(seeds, fetch_fn, _collect_parse,
                                          ledger=ledger)
    collector_src.save_ledger(ledger_path, ledger)
    passed, failed, entered = _collect_enter_store(
        store, report, engine_factory=engine_factory,
        settings=gate_settings, ledger=ledger)
    if report.get("pool"):
        # task31：候选档案（连续通过计数 / 本轮未验）回写账本——第二轮
        # 原子落盘（task27 的第一次落盘只含种子记账，位置与行为不动）。
        collector_src.save_ledger(ledger_path, ledger)
    _print_collect_report(store, report, passed, failed, entered,
                          gate=_collect_gate_values(gate_settings),
                          gate_source=gate_source)
    return 0


# ---------------------------------------------------------------------------
# 名单复查（task28 / work10 §6.3/§6.4）：机制本体在 core/list_recheck，
# 这里只做两层粘合——verify_fn 按既有调用点对齐（不新造探测），cmd_lists
# 负责计划/报告的打印（输出红线：只报 id/处置/原因与计数，不回显节点
# 地址原文）。
# ---------------------------------------------------------------------------
def _recheck_verify_fn(store: Store, probe_factory, socks_port, http_port):
    """lists recheck 的 verify_fn（合同见 core/list_recheck 模块头）。

    sing-box 系节点：拉临时独立端口引擎测（与 GUI _test_node 同款口径；
    探针必须经引擎代理出网——直连测到的是本机出口，假连当真连，P0）；
    其余协议（隧道/未知）：走注入 probe_factory（与 cmd_verify 同款）。
    verify_exit → 成功 apply_outcome + upsert + verify 样本 + 清容灾换下
    标记（与 cmd_verify 同款）；失败只记样本、画像不动（既有口径）。
    判定/写回全在既有管道里，这里不复刻。"""
    def _fn(node):
        stats = history.stats_for(store, node.id)
        if node.protocol in SINGBOX_PROTOCOLS:
            engine = SingBoxEngine(socks_port=socks_port, http_port=http_port)
            try:
                handle = engine.start(node)
            except EngineError as exc:
                history.record(store, node.id, "verify", False,
                               detail=f"引擎启动失败: {exc}")
                return VerifyOutcome(ok=False, reason=f"引擎启动失败: {exc}")
            try:
                outcome = verify_exit(
                    node, make_real_probe(proxy=engine.http_proxy_url),
                    stable=stats.stable)
            finally:
                engine.stop(handle)
        else:
            outcome = verify_exit(node, probe_factory(), stable=stats.stable)
        if outcome.ok:
            apply_outcome(node, outcome)
            store.upsert_node(node)
            history.record(store, node.id, "verify", True,
                           latency_ms=outcome.latency_ms,
                           exit_ip=node.exit_profile.ip, detail="验出口通过")
            # U6 §5.4：验真成功清「容灾换下」标记（无标记 no-op；与 GUI 同口径）。
            failover.clear_mark_on_link_success(store, node.id, True)
        else:
            history.record(store, node.id, "verify", False,
                           detail=outcome.reason)
        return outcome
    return _fn


def cmd_lists(store: Store, args, verify_fn, settings: Settings = None) -> int:
    """lists recheck（task28）：白名单视图与隔离区节点到期复查。
    --dry-run 只列计划不实测；真实跑逐节点处置并落 kind="recheck" 样本。
    处置只提示不改人工标记：变脏移出视图不拉黑、黑名单可连只提示是否
    放回（绝不自动放回）。"""
    if args.lists_cmd != "recheck":
        return _err(f"未知 lists 子命令: {args.lists_cmd}")
    if settings is None:
        settings = Settings()
    try:
        plan = list_recheck.plan_recheck(store, settings)
    except ValueError as exc:    # list_recheck_days 非法/越界：显式报错不夹取
        return _err(str(exc))
    scope_txt = {"whitelist": "白名单", "quarantine": "隔离区"}
    print(f"名单复查：周期每 {settings.list_recheck_days} 天"
          f"（本轮计划 {len(plan)} 个）")
    if not plan:
        print("没有到期的复查对象（白名单视图与隔离区均无）")
        return 0
    for item in plan:
        mark = " [跳过:OpenVPN]" if item["skip"] else ""
        ban = " · 已拉黑" if item["banned"] else ""
        last = ("未验真" if item["last_verified_at"] is None else
                time.strftime("%Y-%m-%d %H:%M",
                              time.localtime(item["last_verified_at"])))
        print(f"  {item['node_id']}  [{scope_txt[item['scope']]}{ban}]{mark}"
              f"  上次验真：{last}")
    if args.dry_run:
        print("（--dry-run：只列计划，未实测）")
        return 0
    report = list_recheck.run_recheck(store, settings, verify_fn)
    disp_txt = {
        "skip": "跳过", "missing": "不在库", "passed": "通过",
        "dirty_out": "变脏移出", "view_lost": "移出视图",
        "recheck_failed": "复查失败", "quarantine_cleared": "隔离摘牌",
        "still_quarantined": "仍污染", "banned_recoverable": "可连待确认",
    }
    for item in report["items"]:
        print(f"  [{disp_txt.get(item['disposition'], item['disposition'])}] "
              f"{item['node_id']}：{item['reason']}")
    c = report["counts"]
    print(f"复查完成：计划 {report['planned']} · 实测 {report['verified']}"
          f" · 跳过 {c['skip']} · 通过 {c['passed']}"
          f" · 变脏移出 {c['dirty_out']} · 移出视图 {c['view_lost']}"
          f" · 复查失败 {c['recheck_failed']}"
          f" · 隔离摘牌 {c['quarantine_cleared']}"
          f" · 仍污染 {c['still_quarantined']}"
          f" · 黑名单可连待确认 {c['banned_recoverable']}")
    if c["dirty_out"]:
        print(f"⚠ {c['dirty_out']} 个白名单节点复查变脏"
              "（已移出白名单视图；不自动拉黑，是否拉黑由人工决定）")
    if c["banned_recoverable"]:
        print("⚠ 有黑名单节点复查可连，是否放回请人工确认"
              "（不会自动放回；人工解除可用 ban <node_id> --off）")
    return 0


def cmd_list(store: Store, args) -> int:
    """默认视图 = 按来源分组的三页（来源第一维度）；场景只是页内过滤。"""
    from ..core.history import sorted_nodes
    from ..core.model import SOURCE_LABELS, SOURCES
    nodes = store.list_nodes(scene=args.scene, include_banned=args.all)
    if args.source:
        nodes = [n for n in nodes if n.source == args.source]
    # M2.5 污染隔离：默认隐藏隔离节点（数据不删），末尾汇总一行报数
    hidden = [n for n in nodes if n.extra.get("quarantined") == "1"]
    if not args.show_quarantined:
        nodes = [n for n in nodes if n.extra.get("quarantined") != "1"]
    else:
        hidden = []
    if args.source:
        groups = [(args.source, [n for n in nodes if n.source == args.source])]
    else:
        groups = [(src, [n for n in nodes if n.source == src]) for src in SOURCES]
        known = {src for src, _ in groups}
        for extra_src in sorted({n.source for n in nodes} - known):
            groups.append((extra_src, [n for n in nodes if n.source == extra_src]))
    total = 0
    for src, members in groups:
        label = SOURCE_LABELS.get(src, src)
        print(f"== {src}（{label}）：{len(members)} 个 ==")
        if not members:
            print("  （空）")
        for n in sorted_nodes(store, members):
            print("  " + _fmt_node(n))
        total += len(members)
    if total == 0 and not hidden:
        print("没有符合条件的节点。先用 intake 导入。")
    elif total > 0:
        print(f"共 {total} 个")
    if hidden:
        print(f"（另有 {len(hidden)} 个污染节点已隔离，--show-quarantined 查看）")
    return 0


def cmd_flag(store: Store, args, flag: str) -> int:
    setter = store.set_pinned if flag == "pin" else store.set_banned
    value = not args.off
    if not setter(args.node_id, value):
        return _err(f"节点不存在: {args.node_id}")
    print(f"已{'设置' if value else '取消'} {flag}: {args.node_id}")
    return 0


def cmd_verify(store: Store, args, probe_factory) -> int:
    node = store.get_node(args.node_id)
    if node is None:
        return _err(f"节点不存在: {args.node_id}（先用 intake 导入）")
    stats = history.stats_for(store, node.id)
    outcome = verify_exit(node, probe_factory(), stable=stats.stable)
    if not outcome.ok:
        history.record(store, node.id, "verify", False, detail=outcome.reason)
        return _err(f"验出口失败: {outcome.reason}")
    apply_outcome(node, outcome)
    store.upsert_node(node)
    history.record(store, node.id, "verify", True,
                   exit_ip=node.exit_profile.ip, detail="验出口通过")
    # U6 §5.4：验真成功清「容灾换下」标记（无标记 no-op；CLI 与 GUI 同口径）。
    failover.clear_mark_on_link_success(store, node.id, True)
    ep = node.exit_profile
    print(f"出口 {ep.ip} / {ep.country}({ep.country_code}) / {ep.network_type} "
          f"org={ep.org} 干净={ep.is_clean} 脏标记={sorted(ep.dirty) or '-'} "
          f"场景={sorted(node.scenes) or '-'} muse={'可' if outcome.muse_ok else '拒/未测'}")
    return 0


def _build_orchestrator(store: Store, probe_factory, engine_factory=None,
                        settings: Settings = None,
                        settings_file=None) -> Orchestrator:
    """engines=None 时构造默认引擎束。task24 §8-1 接线：
    - SingBoxEngine 传设置端口（settings=None 时不传 → 引擎模块默认端口
      兜底，§8-1）；usage_provider（task17）原样保留。
    - out_dir 从派生基准目录（derived_data_dir：与库同目录——库路径已按
      §8-3 优先级解析，无覆盖时即 settings.data_dir）的 ovpn_out 派生，
      现行「同目录派生」口径不变。
    - settings_file = main() 解析出的 settings.json 落点（§4.1：显式覆盖
      目录，否则内置默认目录——不跟随 data_dir 设置）：给出时供给器按它
      现读统一设置（与设置页/③区同一份文件）；不给时维持 task17 的
      「与库同目录」推导（独立调用方兼容）。外部注入 engine_factory 时
      不介入（测试/显式覆盖优先）。"""
    engines = {}
    if engine_factory is not None:
        engines = engine_factory()
    else:
        out_dir = os.path.join(derived_data_dir(store.path, settings),
                               "ovpn_out")
        # U2（task17）：使用设置供给器——每次连接现读（下次连接生效）；
        # 设置/名单损坏如实抛 ValueError，经引擎包成 EngineError 显式失败，
        # 不静默按默认值连。覆盖目录 = db 所在目录（task12 §8-3 优先级链）；
        # settings_path/blocklist_path 把目录推导成文件路径（load_* 的
        # path 参是文件路径，传目录会报 Is a directory，见审查注记）。
        # task24 §4.1：settings.json 落点不跟随 data_dir 设置——main() 把
        # 解析出的落点经 settings_file 传入，保证供给器读到与设置页/③区
        # 同一份文件；未传（独立调用/测试）时沿用 task17 同目录推导。
        def _usage_provider():
            data_dir = os.path.dirname(os.path.abspath(store.path or "client.db"))
            if settings_file is not None:
                current = settings_store.load_settings(settings_file)
            else:
                current = settings_store.load_settings(
                    settings_store.settings_path(data_dir))
            usage = UsageSettings(**current.usage_dict())
            blocklist = site_blocklist.load_blocklist(
                site_blocklist.blocklist_path(data_dir))
            return usage, blocklist

        def _ruleset_state_provider():
            # U3（task18）：每次连接现读规则集来源登记与缓存状态（「下次
            # 连接生效」同款口径）。登记损坏如实抛 ValueError → 引擎包成
            # EngineError 显式失败。数据目录推导同 _usage_provider。
            data_dir = os.path.dirname(os.path.abspath(store.path or "client.db"))
            return ruleset_cache.current_ruleset_state(data_dir)

        if settings is not None:
            singbox = SingBoxEngine(usage_provider=_usage_provider,
                                    ruleset_state_provider=_ruleset_state_provider,
                                    socks_port=settings.socks_port,
                                    http_port=settings.http_port)
        else:
            singbox = SingBoxEngine(usage_provider=_usage_provider,
                                    ruleset_state_provider=_ruleset_state_provider)
        engines = {"openvpn": OpenVPNEngine(out_dir=out_dir)}
        for proto in SINGBOX_PROTOCOLS:
            engines[proto] = singbox
    probe = probe_factory()

    def verifier(node):
        # sing-box 引擎是本地代理模式：流量必须经引擎自己的代理出网，
        # 直连验出口测到的是用户本机 IP（假连当真连，P0）。按引擎类型分流、
        # 不看协议名；proxy 取引擎实例自己的 http_proxy_url（端口可能被
        # 构造参数改过）。走 HTTP 入站而非 SOCKS：探针基于 urllib、只会说
        # HTTP 代理（2026-11-20 真机验收实测：SOCKS 地址给 urllib 必全灭）。
        # OpenVPN 是 TUN/隧道模式，维持现状：用 probe_factory() 造的那一个 probe。
        engine = engines.get(node.protocol)
        if isinstance(engine, SingBoxEngine):
            return verify_exit(node, make_real_probe(proxy=engine.http_proxy_url))
        return verify_exit(node, probe)

    return Orchestrator(store, engines, verifier)


def cmd_connect(store: Store, args, probe_factory, engine_factory=None,
                settings: Settings = None, settings_file=None) -> int:
    node = store.get_node(args.node_id)
    if node is not None and node.extra.get("quarantined") == "1":
        # 隔离节点被手动点名连接：警告但不阻断（用户点名即视为知情，M2.5 规格）
        print(f"⚠ 该节点已被标记为污染节点（{node.extra.get('quarantine_reason', '')}），"
              "仍按你的点名连接。")
    orch = _build_orchestrator(store, probe_factory, engine_factory,
                               settings=settings,
                               settings_file=settings_file)
    # 接管方式（task6）：CLI 每次连接显式给 --mode；默认 proxy（现行）。
    # tun 模式下引擎 start() 会先过提权前提检查——提权启动器未实现时
    # 显式报错（退出码 1，经 orchestrator 的 error 事件），不静默回退。
    if node is not None:
        engine = orch.engines.get(node.protocol)
        if isinstance(engine, SingBoxEngine):
            engine.takeover_mode = getattr(args, "mode", "proxy") or "proxy"
    # 健康预检（task5）：①端口被占 → 报错退出码 2，不发起注定失败的连接；
    # ②系统代理冲突 → 只警告一行，照常连接（用户点名优先）。
    if node is not None:
        engine = orch.engines.get(node.protocol)
        if isinstance(engine, SingBoxEngine):
            occupied = check_ports_free((engine.socks_port, engine.http_port))
            if occupied:
                return _err(
                    f"引擎端口被占用，启动被挡：{describe_occupied(occupied)}。"
                    "关掉占用它的程序（其他代理软件？）或改它的端口，再重试。")
            our_port = engine.http_port
        else:
            # OpenVPN 是 TUN 模式、不占引擎端口：只查②，参照端口改用
            # 设置值（task24 §8-1 端口检查兜底点改读设置；无 settings →
            # 模块默认端口兜底）。
            our_port = (settings.http_port if settings is not None
                        else DEFAULT_HTTP_PORT)
        conflict = proxy_conflict(read_system_proxy(), our_port)
        if conflict["conflict"]:
            print(f"[警告] {conflict['reason']}客户端不会改你的系统代理，"
                  "也不会切换节点。")
    try:
        ev = orch.connect(args.node_id)
    except OrchestratorError as exc:
        return _err(str(exc))
    print(f"[{ev.kind}] {ev.node_id} {ev.detail}")
    if ev.kind == "connected":
        # M1 CLI 是单次进程形态：完成「引擎启动 + 验出口」全流程验证后随即
        # 干净断开，避免留下孤儿隧道进程。常驻连接（托盘）在 M3。
        orch.disconnect()
        print("（CLI 单次形态：验证通过后已断开，常驻连接在 M3 提供）")
    return 0 if ev.kind == "connected" else 1


def cmd_restore_names(store: Store, args) -> int:
    """把被实测改名的节点换回原名（extra["orig_name"]），并清除原名标记。
    隔离标记不动（改名与隔离是两回事）。"""
    restored = 0
    for n in store.list_nodes(include_banned=True):
        orig = n.extra.get("orig_name")
        if orig:
            n.name = orig
            del n.extra["orig_name"]
            store.upsert_node(n)
            restored += 1
    print(f"已恢复 {restored} 个节点的原名")
    return 0


def _collect_check_env(store: Store, settings: Settings = None) -> dict:
    """doctor / cmd_status 共享的体检 env 组装（work7/DESIGN §5.2「抽
    _collect_check_env(store)」）。组装本体在 core/diag_collect（task22
    落地的收集器）：探测全部复用 core/health 现成函数与引擎端口常量，
    无第二套阈值；这里只做 CLI 侧的粘合。
    task24 §8-1：体检参照端口改读设置值——给出 settings 时用设置端口
    构造只读引擎替身（构造器只设属性），收集器的占用/系统代理探测按
    设置端口判定；无 settings → 收集器内部按模块默认端口兜底。"""
    engines = None
    if settings is not None:
        engines = {"settings-ports": SingBoxEngine(
            socks_port=settings.socks_port,
            http_port=settings.http_port)}
    return diag_collect.collect_check_env(store, engines=engines)


def cmd_status(store: Store, args, settings: Settings = None) -> int:
    total = store.count_nodes()
    print(f"节点库: {total} 个节点（db={store.path}）")
    # 一次性体检（task5）：引擎端口占用 + 系统代理指向判读。探测 env 与
    # doctor 共享（_collect_check_env → core/diag_collect → core/health）；
    # 参照端口改用设置值（task24 §8-1 端口检查兜底点改读设置）。
    env = _collect_check_env(store, settings=settings)
    occupied = {item["port"]: item for item in env["ports_occupied"]}
    for port, label in ((DEFAULT_HTTP_PORT, "HTTP 入站"),
                        (DEFAULT_SOCKS_PORT, "SOCKS 入站")):
        item = occupied.get(port)
        if item is None:
            print(f"端口 {port}（{label}）：空闲")
        else:
            print(f"端口 {port}（{label}）：{describe_occupied([item])}")
    print(f"系统代理：{env['proxy_conflict']['reason']}")
    print("会话: CLI 为单次命令形态，常驻会话状态在 M3 托盘提供；"
          "connect 命令在本进程内完成 引擎启动+验出口 全流程校验。")
    print("接管方式: 默认系统代理（proxy）；TUN 模式（connect --mode tun）"
          "配置生成已就绪，提权启动器（pkexec + root wrapper）待真机实现，"
          "当前版本会显式报错、不会静默回退。")
    return 0


def cmd_doctor(store: Store, args, settings: Settings = None) -> int:
    """一键自检（work7/DESIGN §4）：env 由 _collect_check_env 组装（探测
    全部复用 core/health 现成函数），判读全在 diag.run_checks 纯函数。
    退出码口径（work7/DONE.md §4-7）：全绿 0、有 fail 1；env 缺键是
    「判不了」≠ fail。诊断只呈现结果，不触发任何连接动作（铁律）。
    task24 §8-1：体检参照端口随设置值（cmd_status 同款）。"""
    results = diag.run_checks(_collect_check_env(store, settings=settings))
    print(diag.render_checks(results))
    return 0 if all(r.ok for r in results) else 1


def cmd_export_diag(store: Store, args) -> int:
    """导出脱敏诊断包（work7/DESIGN §2；材料收集在 core/diag_collect）。
    输出红线（work7/DONE.md §4-6）：只报包路径与文件数，不回显任何节点
    信息——与 cmd_import_subscription 不回显订阅地址同口径。退出码恒 0，
    失败走 [错误] 行 = 2。"""
    out_path = os.path.expanduser(args.out or "") \
        or diag_collect.default_bundle_path()
    try:
        out_path, written = diag_collect.build_diag_bundle(store, out_path)
    except Exception as exc:   # noqa: BLE001 - 导出失败给人话，不让包半途落地
        return _err(f"导出诊断包失败: {exc}")
    print(f"诊断包已导出：{out_path}（{len(written)} 个文件）")
    return 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gate-client", description="自研代理客户端（M1 核心 CLI）")
    # --db 不给默认值：留 None 让 main() 按 §8-3 四档优先级解析
    #（--db 显式 > GATE_CLIENT_DB > settings.data_dir > 内置默认）；
    # DEFAULT_DB 常量保留作内置默认的展示口径。
    ap.add_argument("--db", default=None,
                    help="本地数据库路径（默认按 统一设置 > GATE_CLIENT_DB "
                         f"环境变量 > 内置默认 {DEFAULT_DB} 解析）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("intake", help="收纳节点：来源文件/链接 → 本地库")
    p.add_argument("--pipeline-data", help="vpngate 流水线 data.json")
    p.add_argument("--pipeline-nodes", help="流水线 nodes_*.txt / subscription.txt")
    p.add_argument("--feed", help="VPN Gate feed 文件（CSV 或镜像 JSON）")
    p.add_argument("--link", action="append", help="单个分享链接（可重复）")
    p.add_argument("--ovpn", action="append", help=".ovpn 文件（可重复）")

    p = sub.add_parser("import-subscription",
                       help="导入订阅（本地文件或订阅地址，二选一）→ 本地库，节点归手动导入页")
    p.add_argument("--file", help="本地订阅文件（整段 base64 或明文链接列表）")
    p.add_argument("--url", help="订阅地址（http/https）")
    p.add_argument("--name", help="订阅名（可选，写进节点 extra 做溯源）")

    p = sub.add_parser("list", help="列出节点（默认按来源分三页输出）")
    p.add_argument("--source", choices=list(SOURCES), help="只看某一页")
    p.add_argument("--scene", choices=list(SCENES), help="页内按场景过滤")
    p.add_argument("--all", action="store_true", help="包含已拉黑节点")
    p.add_argument("--show-quarantined", action="store_true",
                   help="显示已被隔离的污染节点（带 ☠ 标记）")

    for flag in ("pin", "ban"):
        p = sub.add_parser(flag, help=f"{'置顶' if flag == 'pin' else '拉黑'}节点（--off 取消）")
        p.add_argument("node_id")
        p.add_argument("--off", action="store_true")

    p = sub.add_parser("verify", help="对指定节点验出口（本机真实测量）")
    p.add_argument("node_id")
    p.add_argument("--proxy", help="经本地代理测（sing-box 代理模式用），如 127.0.0.1:1080")

    p = sub.add_parser("connect", help="连接指定节点（显式点选，绝不自动切换）")
    p.add_argument("node_id")
    p.add_argument("--mode", choices=["proxy", "tun"], default="proxy",
                   help="接管方式：proxy=系统代理（默认），tun=TUN 接管全机流量"
                        "（提权启动器未实现时显式报错，不静默回退）")

    sub.add_parser("disconnect", help="断开（CLI 形态下无常驻会话，仅提示）")
    sub.add_parser("restore-names", help="把按实测出口改名的节点恢复原名")
    sub.add_parser("status", help="查看节点库与会话说明")
    sub.add_parser("doctor",
                   help="一键自检：9 项检查逐项判读（只读，不连不测不联网）")
    p = sub.add_parser("export-diag",
                       help="导出脱敏诊断包（zip），输出只报包路径与文件数")
    p.add_argument("--out",
                   help="包输出路径（默认 ~/gate-client-diag-<UTC时间戳>.zip）")

    p = sub.add_parser("sources", help="来源登记：登记可续期的订阅/feed，一键更新")
    sp = p.add_subparsers(dest="sources_cmd", required=True)
    sp.add_parser("list", help="列出已登记来源（地址只显示域名，不回显全址）")
    p_add = sp.add_parser("add", help="登记一个来源")
    p_add.add_argument("--type", choices=["subscription", "feed"],   # feed_mirror 已废除（task23 落页）
                       required=True)
    p_add.add_argument("--address", help="订阅/feed 地址（feed 可留空用官方默认）")
    p_add.add_argument("--name", help="来源名（可选）")
    p_up = sp.add_parser("update", help="更新一条已登记来源（真联网拉取）")
    p_up.add_argument("entry_id")
    sp.add_parser("update-all", help="更新全部已启用的登记来源（真联网拉取）")
    p_rm = sp.add_parser("remove", help="删除一条登记（节点不动）")
    p_rm.add_argument("entry_id")
    p_en = sp.add_parser("enable", help="启用一条登记")
    p_en.add_argument("entry_id")
    p_dis = sp.add_parser("disable", help="停用一条登记")
    p_dis.add_argument("entry_id")
    sp.add_parser("collect",
                  help="手动跑一轮自动搜集：读种子配置 → 拉源抽候选 → "
                       "验真 → 入库（只报数字，不回显任何地址；未配置种子"
                       "时提示未启用并退出 0）")

    # 名单复查（task28 / work10 §6.3）：cmd_sources 旁的名单操作入口。
    p = sub.add_parser("lists", help="名单复查：白名单视图/隔离区节点到期复查"
                                     "（周期 list_recheck_days，默认 3 天）")
    lsp = p.add_subparsers(dest="lists_cmd", required=True)
    p_rc = lsp.add_parser("recheck",
                          help="手动触发一轮名单复查（逐节点处置报告）")
    p_rc.add_argument("--dry-run", action="store_true",
                      help="只列本轮复查计划，不实测")
    return ap


def main(argv=None, probe_factory=None, engine_factory=None, fetcher=None) -> int:
    args = build_parser().parse_args(argv)
    if probe_factory is None:
        proxy = getattr(args, "proxy", None)
        probe_factory = lambda: make_real_probe(proxy=proxy)  # noqa: E731
    # —— 统一设置（task24 §8-3 路径优先级 + §4.2 CLI 报错退出码 2）——
    env_db = os.environ.get("GATE_CLIENT_DB")
    sfile = settings_file_path(explicit_db=args.db, env_db=env_db)
    try:
        settings = _load_settings_readonly(args.db)
    except (SettingsCorruptError, SettingsVersionError, SettingsError) as exc:
        return _err(f"设置文件读取失败：{sfile}\n{exc}\n"
                    "（修正或删除该文件后重试；删除即回到默认设置）")
    # 库路径四档优先级（§8-3）：--db 显式 > GATE_CLIENT_DB > settings.data_dir
    # > 内置默认。out_dir / sources.json 等派生落点随 store.path 同目录派生
    # （§8-1，口径不变），settings.data_dir 档由此自动生效。
    store = Store(resolve_db_path(explicit_db=args.db, env_db=env_db,
                                  settings=settings))
    try:
        # task23 落页：启动检测四页时代存量（sources.json 含 feed_mirror 残留
        # ∨ 库内旧两键节点）→ 一次性迁移，幂等、误触发无害、不联网
        # （work8/DESIGN §8-6；实现在 sources/landing.py，只调 merge_proto）。
        migration = run_startup_migration(store)
        if migration["triggered"]:
            print("已迁移旧版来源数据：openvpn / vpngate_feed 两页并入 "
                  "vpngate，登记表 feed_mirror 条目已并入 feed。")
        if args.cmd == "intake":
            return cmd_intake(store, args)
        if args.cmd == "import-subscription":
            return cmd_import_subscription(store, args, fetcher=fetcher)
        if args.cmd == "list":
            return cmd_list(store, args)
        if args.cmd in ("pin", "ban"):
            return cmd_flag(store, args, args.cmd)
        if args.cmd == "verify":
            return cmd_verify(store, args, probe_factory)
        if args.cmd == "connect":
            return cmd_connect(store, args, probe_factory, engine_factory,
                               settings=settings, settings_file=sfile)
        if args.cmd == "disconnect":
            print("CLI 形态下没有常驻会话可断开（连接在 connect 进程内管理）；"
                  "常驻断开在 M3 托盘提供。")
            return 0
        if args.cmd == "restore-names":
            return cmd_restore_names(store, args)
        if args.cmd == "status":
            return cmd_status(store, args, settings=settings)
        if args.cmd == "doctor":
            return cmd_doctor(store, args, settings=settings)
        if args.cmd == "export-diag":
            return cmd_export_diag(store, args)
        if args.cmd == "sources":
            return cmd_sources(store, args, fetcher=fetcher,
                               engine_factory=engine_factory,
                               settings_file=sfile)
        if args.cmd == "lists":
            return cmd_lists(store, args,
                             verify_fn=_recheck_verify_fn(
                                 store, probe_factory,
                                 RECHECK_SOCKS_PORT, RECHECK_HTTP_PORT),
                             settings=settings)
        return _err(f"未知命令: {args.cmd}")
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
