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

注：connect 的会话状态只在本进程内有效（M1 CLI 形态）；常驻守护/托盘在 M3。
verify 默认走真实 probe（本机测量）；测试通过 main(probe_factory=...) 注入假 probe。
import-subscription 的输出只报汇总数字与格式，不回显订阅地址、不打印节点链接原文。
"""

from __future__ import annotations

import argparse
import os
import sys

from ..core import diag
from ..core import diag_collect
from ..core import history
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
from ..core.verify import apply_outcome, make_real_probe, verify_exit
from ..engines.openvpn_engine import OpenVPNEngine
from ..engines.singbox_engine import (DEFAULT_HTTP_PORT, DEFAULT_SOCKS_PORT,
                                      SINGBOX_PROTOCOLS, SingBoxEngine)
from ..sources import feed as feed_src
from ..sources import format_import as format_src
from ..sources import manual as manual_src
from ..sources import pipeline as pipeline_src
from ..sources import source_registry as registry_src
from ..sources import subscription as subscription_src
from ..sources.landing import run_startup_migration
from urllib.parse import urlsplit

DEFAULT_DB = os.environ.get(
    "GATE_CLIENT_DB",
    os.path.join(os.path.expanduser("~"), ".local", "share", "gate-client", "client.db"))


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
    """登记表位置：与节点库同目录的 sources.json（GUI/CLI 共用同一份）。"""
    return os.path.join(
        os.path.dirname(os.path.abspath(store.path or "client.db")),
        "sources.json")


def _registry(store: Store):
    return registry_src.SourceRegistry(_registry_path(store))


def cmd_sources(store: Store, args, fetcher=None) -> int:
    """来源登记命令：list/add/update/update-all/remove/enable/disable。
    fetcher 为测试注入点（默认真联网拉取）。"""
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
    return _err(f"未知 sources 子命令: {args.sources_cmd}")


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

        if settings is not None:
            singbox = SingBoxEngine(usage_provider=_usage_provider,
                                    socks_port=settings.socks_port,
                                    http_port=settings.http_port)
        else:
            singbox = SingBoxEngine(usage_provider=_usage_provider)
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
            return cmd_sources(store, args, fetcher=fetcher)
        return _err(f"未知命令: {args.cmd}")
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
