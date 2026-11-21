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

注：connect 的会话状态只在本进程内有效（M1 CLI 形态）；常驻守护/托盘在 M3。
verify 默认走真实 probe（本机测量）；测试通过 main(probe_factory=...) 注入假 probe。
import-subscription 的输出只报汇总数字与格式，不回显订阅地址、不打印节点链接原文。
"""

from __future__ import annotations

import argparse
import os
import sys

from ..core import history
from ..core.model import SCENES, SOURCES
from ..core.orchestrator import Orchestrator, OrchestratorError
from ..core.store import Store
from ..core.verify import apply_outcome, make_real_probe, verify_exit
from ..engines.openvpn_engine import OpenVPNEngine
from ..engines.singbox_engine import SINGBOX_PROTOCOLS, SingBoxEngine
from ..sources import feed as feed_src
from ..sources import format_import as format_src
from ..sources import manual as manual_src
from ..sources import pipeline as pipeline_src
from ..sources import subscription as subscription_src

DEFAULT_DB = os.environ.get(
    "GATE_CLIENT_DB",
    os.path.join(os.path.expanduser("~"), ".local", "share", "gate-client", "client.db"))


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
    print(f"已收纳 {len(res.nodes)} 个节点（跳过坏行 {res.skipped}，"
          f"格式 {res.format}，库内共 {store.count_nodes()} 个）")
    return 0


def cmd_list(store: Store, args) -> int:
    """默认视图 = 按来源分组的四页（来源第一维度）；场景只是页内过滤。"""
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


def _build_orchestrator(store: Store, probe_factory, engine_factory=None) -> Orchestrator:
    engines = {}
    if engine_factory is not None:
        engines = engine_factory()
    else:
        out_dir = os.path.join(os.path.dirname(os.path.abspath(store.path or "client.db")),
                               "ovpn_out")
        singbox = SingBoxEngine()
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


def cmd_connect(store: Store, args, probe_factory, engine_factory=None) -> int:
    node = store.get_node(args.node_id)
    if node is not None and node.extra.get("quarantined") == "1":
        # 隔离节点被手动点名连接：警告但不阻断（用户点名即视为知情，M2.5 规格）
        print(f"⚠ 该节点已被标记为污染节点（{node.extra.get('quarantine_reason', '')}），"
              "仍按你的点名连接。")
    orch = _build_orchestrator(store, probe_factory, engine_factory)
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


def cmd_status(store: Store, args) -> int:
    total = store.count_nodes()
    print(f"节点库: {total} 个节点（db={store.path}）")
    print("会话: CLI 为单次命令形态，常驻会话状态在 M3 托盘提供；"
          "connect 命令在本进程内完成 引擎启动+验出口 全流程校验。")
    return 0


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="gate-client", description="自研代理客户端（M1 核心 CLI）")
    ap.add_argument("--db", default=DEFAULT_DB, help=f"本地数据库路径（默认 {DEFAULT_DB}）")
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

    p = sub.add_parser("list", help="列出节点（默认按来源分四页输出）")
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

    sub.add_parser("disconnect", help="断开（CLI 形态下无常驻会话，仅提示）")
    sub.add_parser("restore-names", help="把按实测出口改名的节点恢复原名")
    sub.add_parser("status", help="查看节点库与会话说明")
    return ap


def main(argv=None, probe_factory=None, engine_factory=None, fetcher=None) -> int:
    args = build_parser().parse_args(argv)
    if probe_factory is None:
        proxy = getattr(args, "proxy", None)
        probe_factory = lambda: make_real_probe(proxy=proxy)  # noqa: E731
    store = Store(args.db)
    try:
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
            return cmd_connect(store, args, probe_factory, engine_factory)
        if args.cmd == "disconnect":
            print("CLI 形态下没有常驻会话可断开（连接在 connect 进程内管理）；"
                  "常驻断开在 M3 托盘提供。")
            return 0
        if args.cmd == "restore-names":
            return cmd_restore_names(store, args)
        if args.cmd == "status":
            return cmd_status(store, args)
        return _err(f"未知命令: {args.cmd}")
    finally:
        store.close()


if __name__ == "__main__":
    sys.exit(main())
