#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
使用设置合并（core/usage_merge.py）—— U2：usage_model 片段并入引擎配置
========================================================================
task17 · 阶段五·使用界面线 实现第二段。work10/DESIGN.md §4.5 五条顺序
契约的引擎侧机械落地。纯标准库 + 同包 usage_model；不依赖 PySide6、
不碰网络、不读文件——纯 dict 变换（与 core/tun_config.py 的
build_tun_config 同一族：纯函数合并，绝不改动调用方的原 dict）。

职责：
- merge_usage_config(config, fragment)：把 usage_model.build_route_config
  产出的路由/DNS 片段并入一份完整引擎配置（singbox_engine.build_config
  的产物形态），返回新 dict。

五条顺序契约（work10/DESIGN.md §4.5，落实处见函数体编号注释）：
1. TUN 场景：tun_config.build_tun_config 的 sniff/hijack-dns 保持最前置，
   现状不动——本函数把 usage 规则插在既有 resolve 规则之前，tun 前置
   规则在 resolve 之前、天然也在 usage 规则之前（集成点在 build_config
   的 TUN 合并之后，见 work17/DESIGN.md 集成改动点清单）；
2. usage 的 reject/route 规则插在既有 resolve 规则之前（片段自带
   insert_before="resolve" 提示，先拦截后解析，被拦域名不做无谓解析）；
3. dns_patch 只写回 remote-dns 的 server 字段与 resolve 规则的 strategy
   字段（strategy=None 表示移除该字段），其余 DNS 字段一律不动
   （detour=node-out / dns.final=local-dns / server_port 防泄漏口径不动，
   work10 §4.4）；
4. mode≠global 时才追加 direct-out 出站；global 模式不加（保持单节点
   单出站现状，铁律）；
5. route.final 仅 direct 模式改写，其余模式不动。

占位拦截（方案 A，主会话 2026-11-22 裁决，work17/REVIEW_FAIL.md）：
usage_model.build_route_config（0.14.0 既有口径，本模块不改它）在
ad_block / malware_block 开或 rule 模式下产出带 prototype_placeholder
键的占位规则集条目（假路径 cache/rulesets/*.srs，真条目来源待 U3
主人核定）。占位条目**不进 live 配置**：真 sing-box 的严格 JSON 解码
会因未知字段拒绝整个配置（本机实测 FATAL，开广告拦截即断连），占位
path 文件也并不存在。本模块在合并边界滤除占位 rule_set 条目并连带
滤除引用其 tag 的规则——效果：U3 之前开广告/恶意拦截 = 该项无操作
（不断连、不发无效配置）；U3 落真规则、占位键消失后，拦截自然生效。
滤除经可选 notes 通道说明（不静默）：调用方传 list 即收到滤除记录，
不传则行为不变（集成点兼容）；0.14.0「占位」口径在**片段层**原样
保留（usage_model 与片段层断言都不动）。

回归红线：UsageSettings 默认值 + 空名单的片段并入后，结果必须与现行
build_config 输出深等价（零行为变化）——test_usage_wiring.py 实证。
默认片段恰好是零操作：无规则、无 rule_set、dns_patch 写回的 server
（8.8.8.8）与 strategy（ipv4_only）与引擎现行写死值逐字相同、无出站
补丁、无 final 改写。为此 merge 精确跳过一切空操作（空规则不插入、
空 rule_set 不建键），不加任何多余键。

落点口径：client/core/（片段生产者 usage_model 与同族纯合并模块
tun_config 都在这一层；引擎 build_config 尾部一行调用，改动点清单见
work17/DESIGN.md §5）。生产片段的 UsageSettings / site_blocklist 从
task12 设置存储与名单文件在连接时现读（下次连接生效口径）——供给器
接线见 work17/DESIGN.md §5 改动点 2-4。

铁律对齐：本模块没有任何节点选择/切换/优选逻辑——它只把用户显式
定好的设置翻译进路由配置；global/rule 模式下 outbounds[0] 恒为
node-out（单节点单出站语义不动，本函数只在 mode≠global 时追加
direct-out，绝不生成 urltest / selector）。
"""

from __future__ import annotations

import copy

from .usage_model import (
    DIRECT_OUT_TAG,
    PLACEHOLDER_KEY,
    SPLIT_DIRECT,
    SPLIT_GLOBAL,
    SPLIT_MODES,
)


class UsageMergeError(ValueError):
    """片段/配置非法或契约被破坏。报错不静默吞（集成点包成 EngineError）。"""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise UsageMergeError(message)


def _validate_config(config: dict) -> None:
    """引擎完整配置的形状校验（口径同 tun_config._require_route_shape 家族）。
    缺哪块点名哪块，不在半成品上动手。"""
    if not isinstance(config, dict):
        raise UsageMergeError(
            f"config 必须是 dict（引擎完整配置），得到 {type(config).__name__}")
    route = config.get("route")
    if not isinstance(route, dict) or not isinstance(route.get("rules"), list):
        raise UsageMergeError(
            "config 缺 route.rules——不是完整的引擎配置（build_config 产物应含 resolve 规则）")
    if not route.get("final"):
        raise UsageMergeError("config 缺 route.final——拒绝无出口的半成品配置")
    dns = config.get("dns")
    if not isinstance(dns, dict) or not isinstance(dns.get("servers"), list):
        raise UsageMergeError("config 缺 dns.servers——不是完整的引擎配置")
    if not isinstance(config.get("outbounds"), list):
        raise UsageMergeError("config 缺 outbounds 列表——不是完整的引擎配置")


def _validate_fragment(fragment: dict) -> None:
    """片段形状校验（build_route_config 产物契约）。只校验本函数消费的
    键；未来段位（U3 规则集等）新增的键不拒绝——允许向上演化。"""
    if not isinstance(fragment, dict):
        raise UsageMergeError(
            f"fragment 必须是 dict（build_route_config 产物），得到 {type(fragment).__name__}")
    for key in ("mode", "route", "dns_patch", "outbound_patch"):
        if key not in fragment:
            raise UsageMergeError(f"片段缺 {key}——不是 build_route_config 的产物形态")
    mode = fragment["mode"]
    if mode not in SPLIT_MODES:
        raise UsageMergeError(
            f"片段 mode 必须是 {'/'.join(SPLIT_MODES)} 之一，得到 {mode!r}")
    route = fragment["route"]
    if not isinstance(route, dict):
        raise UsageMergeError(f"片段 route 必须是 dict，得到 {type(route).__name__}")
    for key in ("insert_before", "rules", "rule_set", "final_override"):
        if key not in route:
            raise UsageMergeError(f"片段 route 缺 {key}——不是 build_route_config 的产物形态")
    # §4.5-2 的锚点只有一个：既有 resolve 规则。其他值说明片段生产者
    # 被改坏或换了他种合并策略——拒绝猜测（与空规则与否无关，恒校验）。
    if route["insert_before"] != "resolve":
        raise UsageMergeError(
            f"片段 insert_before 只支持 'resolve'（§4.5-2 唯一锚点），"
            f"得到 {route['insert_before']!r}")
    if not isinstance(route["rules"], list) or not isinstance(route["rule_set"], list):
        raise UsageMergeError("片段 route.rules / route.rule_set 必须是列表")
    if not isinstance(route["final_override"], str) and route["final_override"] is not None:
        raise UsageMergeError(
            f"片段 route.final_override 必须是字符串或 None，得到 {route['final_override']!r}")
    dns_patch = fragment["dns_patch"]
    if not isinstance(dns_patch, dict):
        raise UsageMergeError(f"片段 dns_patch 必须是 dict，得到 {type(dns_patch).__name__}")
    for key in ("remote_dns_server_tag", "remote_dns_server", "resolve_rule_strategy"):
        if key not in dns_patch:
            raise UsageMergeError(f"片段 dns_patch 缺 {key}——不是 build_route_config 的产物形态")
    if not isinstance(dns_patch["remote_dns_server_tag"], str) \
            or not dns_patch["remote_dns_server_tag"].strip():
        raise UsageMergeError(
            f"片段 dns_patch.remote_dns_server_tag 必须是非空字符串，"
            f"得到 {dns_patch['remote_dns_server_tag']!r}")
    if not isinstance(dns_patch["remote_dns_server"], str) \
            or not dns_patch["remote_dns_server"].strip():
        raise UsageMergeError(
            f"片段 dns_patch.remote_dns_server 必须是非空字符串，"
            f"得到 {dns_patch['remote_dns_server']!r}")
    if not isinstance(fragment["outbound_patch"], list):
        raise UsageMergeError(f"片段 outbound_patch 必须是列表，得到 {fragment['outbound_patch']!r}")
    # 生产侧契约复核（work10 §4.5-4/5）：片段与 mode 冲突 = 生产者被改坏，
    # 当场报错，不静默吞掉矛盾。
    if mode == SPLIT_GLOBAL and fragment["outbound_patch"]:
        raise UsageMergeError(
            "契约破坏：global 模式的片段不得携带 outbound_patch（§4.5-4："
            f"mode≠global 才追加 direct-out），得到 {fragment['outbound_patch']!r}")
    final_override = route["final_override"]
    if mode == SPLIT_DIRECT:
        if final_override != DIRECT_OUT_TAG:
            raise UsageMergeError(
                "契约破坏：direct 模式的片段 final_override 必须是 "
                f"{DIRECT_OUT_TAG!r}（§4.5-5），得到 {final_override!r}")
    elif final_override is not None:
        raise UsageMergeError(
            f"契约破坏：{mode} 模式的片段不得改写 route.final（§4.5-5："
            f"仅 direct 模式改写），得到 {final_override!r}")


def _find_resolve_index(rules: list) -> int:
    """既有 resolve 规则的下标（§4.5-2 的插入锚点）。找不到显式报错——
    引擎配置恒有 resolve 规则（singbox_engine.build_config 写死首条），
    缺失说明配置形态被改坏，不许静默改成「追加到尾部」这种猜测行为。"""
    for i, rule in enumerate(rules):
        if isinstance(rule, dict) and rule.get("action") == "resolve":
            return i
    raise UsageMergeError(
        "config 的 route.rules 里没有 action=resolve 规则——§4.5-2 的插入"
        "锚点缺失（现行引擎配置恒有该规则）；拒绝猜测插入位置")


def _insert_usage_rules(merged: dict, fragment: dict) -> None:
    """§4.5-1/2：usage 规则插在既有 resolve 规则之前。
    TUN 场景下 sniff/hijack-dns 前置规则在 resolve 之前，天然保持最前置。
    引用占位 rule_set tag 的规则已在 _live_fragment 滤除（否则会引用
    live 配置里不存在的 rule_set——同样必被内核拒绝）。"""
    usage_rules = fragment["route"]["rules"]
    if not usage_rules:
        return                                     # 空规则=零操作（默认值红线：不加键不插空）
    rules = merged["route"]["rules"]
    anchor = _find_resolve_index(rules)
    rules[anchor:anchor] = [copy.deepcopy(r) for r in usage_rules]


def _merge_rule_set(merged: dict, fragment: dict) -> None:
    """片段引用的规则集定义并入 route.rule_set。tag 重复显式报错（拒绝
    静默覆盖/重复）；片段为空时不建键——默认值红线的零操作要求。
    占位条目（prototype_placeholder 键）已在 _live_fragment 滤除，
    到达这里的都是可进 live 配置的条目。"""
    rule_set = fragment["route"]["rule_set"]
    if not rule_set:
        return
    route = merged["route"]
    existing = route.get("rule_set")
    if existing is None:
        existing = []
        route["rule_set"] = existing
    elif not isinstance(existing, list):
        raise UsageMergeError(
            f"config 的 route.rule_set 必须是列表，得到 {existing!r}")
    tags = {item.get("tag") for item in existing if isinstance(item, dict)}
    for item in rule_set:
        if isinstance(item, dict) and item.get("tag") in tags:
            raise UsageMergeError(
                f"route.rule_set 已有同名 tag {item.get('tag')!r}——拒绝静默重复")
        existing.append(copy.deepcopy(item))
        tags.add(item.get("tag"))


def _apply_dns_patch(merged: dict, fragment: dict) -> None:
    """§4.5-3：dns_patch 只写回两处——
    (a) dns.servers 中 tag=remote_dns_server_tag 条目的 server 字段
        （detour/server_port/type/tag 一律不动）；
    (b) route.rules 中 action=resolve 规则的 strategy 字段
        （None=移除该字段；现行 resolve 规则恰一条，多条同匹配也只动
        strategy 本身，语义一致）。"""
    dns_patch = fragment["dns_patch"]
    tag = dns_patch["remote_dns_server_tag"]
    servers = merged["dns"]["servers"]
    hits = [s for s in servers if isinstance(s, dict) and s.get("tag") == tag]
    if not hits:
        raise UsageMergeError(
            f"config 的 dns.servers 里没有 tag={tag!r} 的条目——§4.5-3 的"
            "写回目标缺失（现行引擎配置恒有 remote-dns）；拒绝猜测")
    if len(hits) > 1:
        raise UsageMergeError(
            f"config 的 dns.servers 里有 {len(hits)} 个 tag={tag!r} 条目——"
            "写回目标不唯一，拒绝猜测")
    hits[0]["server"] = dns_patch["remote_dns_server"]

    strategy = dns_patch["resolve_rule_strategy"]
    for rule in merged["route"]["rules"]:
        if isinstance(rule, dict) and rule.get("action") == "resolve":
            if strategy is None:
                rule.pop("strategy", None)         # None=移除（IPv6 开=放开 v4-only）
            else:
                rule["strategy"] = strategy


def _apply_outbound_patch(merged: dict, fragment: dict) -> None:
    """§4.5-4：mode≠global 时追加 direct-out 出站；global 模式不动
    outbounds（单节点单出站现状）。tag 重复显式报错。"""
    if fragment["mode"] == SPLIT_GLOBAL:
        return
    outbounds = merged["outbounds"]
    tags = {o.get("tag") for o in outbounds if isinstance(o, dict)}
    for outbound in fragment["outbound_patch"]:
        if isinstance(outbound, dict) and outbound.get("tag") in tags:
            raise UsageMergeError(
                f"outbounds 已有同名 tag {outbound.get('tag')!r}——拒绝静默重复")
        outbounds.append(copy.deepcopy(outbound))
        tags.add(outbound.get("tag"))


def _apply_final_override(merged: dict, fragment: dict) -> None:
    """§4.5-5：route.final 仅 direct 模式改写（片段 final_override 仅在
    direct 模式非 None，生产侧契约已复核）。"""
    final_override = fragment["route"]["final_override"]
    if final_override is not None:
        merged["route"]["final"] = final_override


def _live_fragment(fragment: dict, notes):
    """方案 A（主会话 2026-11-22 裁决）：占位 rule_set 条目（带
    prototype_placeholder 键）与引用其 tag 的规则在 live 配置边界滤除。

    依据（work17/REVIEW_FAIL.md 根因，已实测）：真 sing-box 的严格 JSON
    解码会因未知字段 prototype_placeholder 拒绝整个配置，且占位 path
    （cache/rulesets/*.srs）文件并不存在——占位条目进了 live 配置就是
    必被拒的死配置。滤除效果：U3 之前开广告/恶意拦截或 rule 模式 =
    对应项无操作（不断连、不发无效配置）；U3 核定真来源、删占位键后，
    条目不再被滤，拦截自然生效。0.14.0「占位」口径在片段层原样保留
    （usage_model 不改，片段层断言不动）。

    返回浅拷贝改写的片段视图（route.rules / route.rule_set 换成滤除后
    的列表；无滤除时沿用原列表，零行为变化），绝不改动入参 fragment
    （下游 helper 在并入 merged 前各自 deepcopy）。滤除说明经 notes
    通道上报（merge_usage_config 的可选参数；None=不收集，行为不变）。
    """
    rule_set = fragment["route"]["rule_set"]
    live_rule_set = []
    placeholder_tags = set()
    for entry in rule_set:
        if isinstance(entry, dict) and entry.get(PLACEHOLDER_KEY):
            placeholder_tags.add(entry.get("tag"))
        else:
            live_rule_set.append(entry)
    live_rules = []
    blocked = 0
    for rule in fragment["route"]["rules"]:
        refs = rule.get("rule_set") if isinstance(rule, dict) else None
        if isinstance(refs, (list, tuple)) and placeholder_tags.intersection(refs):
            blocked += 1
            continue
        live_rules.append(rule)
    if placeholder_tags:
        if notes is not None:
            notes.append(
                "占位规则集被拦（方案 A）：rule_set " +
                ", ".join(sorted(str(t) for t in placeholder_tags)) +
                f" 带 {PLACEHOLDER_KEY} 键未进 live 配置（真条目来源待 U3 "
                f"主人核定），引用它的 {blocked} 条规则一并滤除——占位项"
                "本次连接不生效、不断连；U3 落真规则后拦截自然生效。")
    live = dict(fragment)
    live["route"] = dict(fragment["route"])
    live["route"]["rules"] = live_rules
    live["route"]["rule_set"] = live_rule_set
    return live


def merge_usage_config(config: dict, fragment: dict, notes=None) -> dict:
    """把 build_route_config 的片段并入引擎完整配置，返回新 dict。

    参数：
    - config：singbox_engine.build_config 的产物形态（完整引擎配置 dict）；
    - fragment：usage_model.build_route_config(model, site_blocklist) 的产物；
    - notes（可选）：list 时收集滤除说明（占位拦截等，见 _live_fragment）；
      None（默认）= 不收集，行为不变——引擎集成点现行两参调用原样兼容。

    返回深拷贝合并结果，绝不改动两个入参（tun_config.build_tun_config
    同口径）。顺序契约 §4.5 五条逐条落地，见各 _apply_* 编号注释。
    合并落在 build_config 的 TUN 合并之后（集成改动点清单见
    work17/DESIGN.md §5）：TUN 的 sniff/hijack-dns 已在最前（契约 1），
    本函数的插入锚点是 resolve 规则（契约 2），dns_patch 只动两字段
    （契约 3），direct-out 只在 mode≠global 追加（契约 4），final 仅
    direct 改写（契约 5）。占位 rule_set 及引用其 tag 的规则在进入
    这五步之前先行滤除（方案 A，模块 docstring「占位拦截」节）。

    默认值零操作：UsageSettings 默认值 + 空名单的片段并入后与入参
    config 深等价（无规则插入、无 rule_set 建键、dns_patch 写回值与
    引擎现行写死值逐字相同、无出站/终局改动）——test_usage_wiring.py
    的「零行为变化」组逐协议实证。
    """
    _validate_config(config)
    _validate_fragment(fragment)
    if notes is not None and not isinstance(notes, list):
        raise UsageMergeError(
            f"notes 必须是 list（滤除说明收集通道）或 None，得到 {notes!r}")
    merged = copy.deepcopy(config)
    live = _live_fragment(fragment, notes)         # 方案 A：占位滤除
    _insert_usage_rules(merged, live)              # §4.5-1/2
    _merge_rule_set(merged, live)                  # 片段引用的规则集定义
    _apply_dns_patch(merged, fragment)             # §4.5-3
    _apply_outbound_patch(merged, fragment)        # §4.5-4
    _apply_final_override(merged, fragment)        # §4.5-5
    return merged
