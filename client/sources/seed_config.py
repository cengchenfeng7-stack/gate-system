#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
种子源配置加载（task25 其二：task9 欠账「种子清单结构 + 账本落位」收口）
========================================================================

只收口「结构与落位」，不收口「内容」：
- 真实种子清单由主会话在集成时给定——本模块不内置、不编造任何种子
  地址（简报红线）；配置文件里有什么就加载什么，没有就是「未启用」。
- 不做定时/自动联网：本模块只读本地配置文件，搜集触发形态不在本段
  （task9 DESIGN §7.3）。

交付三件：
1. 种子配置文件结构与落位：`collect_seeds.json`，与 sources.json /
   collect_ledger.json 同目录（由节点库路径推导，口径同
   core/backup_group.py 与 ui/cli.py `_registry_path`）。包裹层
   {"version": 1, "seeds": [条目...]}；条目 schema 见 load_seed_config
   docstring。结构错误显式 ValueError（与 collector.load_ledger 既有
   口径一致：绝不静默吞，坏配置装作没配会把停用/清单状态洗掉）。
2. collect_ledger.json 最终路径落点：COLLECT_LEDGER_FILENAME 常量 +
   collect_ledger_path() 推导——账本文件名全仓只有这一处可改
   （task9 DESIGN §2.1 建议 collect_ledger.json，§2.1「集成落位：
   与登记表 sources.json 同目录」就此定稿）。collector.load_ledger /
   save_ledger 仍收显式路径参数（零改动），集成时传本函数结果即可。
3. load_seed_config()：读配置 → 校验 → 产出 collector.run_collection
   能直接吃的 seeds 列表（每条 {"id", "category", "address",
   "enabled"}，恰为 run_collection 结构校验所需的字段形状）。
   三态：缺文件 → 空清单 + STATUS_NOT_CONFIGURED（没配种子不是错误，
   是未启用）；文件在但 seeds 为空 → 空清单 + STATUS_EMPTY；
   正常 → 清单 + STATUS_OK；损坏/非法 → ValueError 上抛。

- 只用标准库；不 import client 包、不依赖 PySide6、零网络调用；
- 示例与测试一律 RFC 2606 保留域名（*.example）；
- 铁律边界：本模块不含任何搜集触发/测速/优选/自动切换语义。
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlsplit

# ---------------- 文件名常量（落点全仓唯一可改处） ----------------
SEED_CONFIG_FILENAME = "collect_seeds.json"   # 与 collect_ledger.json 成对
COLLECT_LEDGER_FILENAME = "collect_ledger.json"   # task9 DESIGN §2.1 定稿
SEED_CONFIG_VERSION = 1

# ---------------- load_seed_config 三态状态字 ----------------
STATUS_NOT_CONFIGURED = "not_configured"   # 缺文件：未启用（不是错误）
STATUS_EMPTY = "empty"                     # 文件在、seeds 为空清单
STATUS_OK = "ok"                           # 加载成功


def seed_config_path(store_path: str = "") -> str:
    """由节点库路径推导 collect_seeds.json 的落点。

    与 ui/cli.py `_registry_path`（sources.json）、core/backup_group.py
    `backup_group_path` 同一推导口径：dirname(abspath(store_path 或
    "client.db")) + 文件名——种子配置、健康账本、登记表永远待在一起
    （task9 DESIGN §2.1「与 sources.json 同目录」口径）。"""
    base = os.path.dirname(os.path.abspath(store_path or "client.db"))
    return os.path.join(base, SEED_CONFIG_FILENAME)


def collect_ledger_path(store_path: str = "") -> str:
    """由节点库路径推导 collect_ledger.json 的落点（同上推导口径）。

    集成形态：collector.load_ledger / save_ledger 的 path 参数传本函数
    返回值即可，账本文件名改动只动 COLLECT_LEDGER_FILENAME 一处。"""
    base = os.path.dirname(os.path.abspath(store_path or "client.db"))
    return os.path.join(base, COLLECT_LEDGER_FILENAME)


# ---------------------------------------------------------------------------
# 结构校验（显式 ValueError，口径同 collector.load_ledger：不静默吞）
# ---------------------------------------------------------------------------
def _entry_problems(entry, index: int) -> list:
    """逐条校验一个种子条目，返回问题清单（空 = 合法）。

    条目 schema（task25 定稿；字段形状与校验规则）：
    - id        必填，字符串，剥两端空白后非空；全文件内唯一
                （账本按 id 建档、报告按 id 归行，重复会让档案含糊）；
    - category  必填，字符串，剥两端空白后非空。类别命名口径归
                task9 DESIGN §1.1（聚合仓库/列表站/原始直链/流水线镜像）
                与主会话的真实种子清单——本模块只校验「非空字符串」，
                不内置枚举（枚举会变成第二个漂移点）；
    - address   必填，字符串，http/https 直链且带主机名
                （task9 DESIGN §1.2 入选标准第 1 条「匿名可取」的
                机读下限；与 extract_candidates 只收 http(s) 同口径）。
                本模块只校验字段口径，不解析内容、不访问网络；
    - enabled   可选，严格 bool（缺省 True；0/1/"true" 等混入一律拒绝
                ——口径同 core/backup_group.py：布尔位不许含糊）。
    未列出的键一律拒绝（口径同 settings 族「未知键不静默吞」：手滑把
    enabled 写成 enabld 会被静默忽略，禁用意图悄悄失效——宁可报错；
    前向兼容由包裹层 version 负责）。
    """
    problems = []
    where = f"seeds[{index}]"
    if not isinstance(entry, dict):
        return [f"{where}: 条目必须是 JSON 对象，得到 "
                f"{type(entry).__name__}"]
    unknown = sorted(set(entry) - {"id", "category", "address", "enabled"})
    if unknown:
        problems.append(f"{where}: 未定义的键 {unknown}（只允许 "
                        "id/category/address/enabled）")

    sid = entry.get("id")
    if not isinstance(sid, str) or not sid.strip():
        problems.append(f"{where}: id 必填且须为非空字符串，得到 {sid!r}")
    category = entry.get("category")
    if not isinstance(category, str) or not category.strip():
        problems.append(f"{where}: category 必填且须为非空字符串，"
                        f"得到 {category!r}")
    address = entry.get("address")
    if not isinstance(address, str) or not address.strip():
        problems.append(f"{where}: address 必填且须为非空字符串，"
                        f"得到 {address!r}")
    else:
        try:
            parts = urlsplit(address.strip())
        except ValueError as exc:
            problems.append(f"{where}: address 无法解析（{exc}）")
        else:
            if parts.scheme.lower() not in ("http", "https") \
                    or not parts.netloc:
                problems.append(
                    f"{where}: address 须为 http/https 直链（DESIGN §1.2 "
                    f"匿名可取），得到 {address.strip()!r}")
    enabled = entry.get("enabled", True)
    if not isinstance(enabled, bool):
        problems.append(f"{where}: enabled 须为 bool（true/false），"
                        f"得到 {enabled!r}")
    return problems


def _validate(data, path: str) -> list:
    """校验包裹层 + 全部条目，返回问题清单（空 = 合法）。"""
    if not isinstance(data, dict):
        return [f"种子配置（{path}）顶层必须是 JSON 对象，"
                f"得到 {type(data).__name__}"]
    problems = []
    unknown = sorted(set(data) - {"version", "seeds"})
    if unknown:
        problems.append(f"种子配置（{path}）顶层未定义的键 {unknown}"
                        "（只允许 version/seeds）")
    if data.get("version") != SEED_CONFIG_VERSION:
        problems.append(f"种子配置（{path}）version 必须为 "
                        f"{SEED_CONFIG_VERSION}，得到 {data.get('version')!r}")
    seeds = data.get("seeds")
    if not isinstance(seeds, list):
        problems.append(f"种子配置（{path}）seeds 必须是条目数组，"
                        f"得到 {type(seeds).__name__}")
        return problems
    seen_ids = {}
    for i, entry in enumerate(seeds):
        problems.extend(_entry_problems(entry, i))
        if isinstance(entry, dict) and isinstance(entry.get("id"), str) \
                and entry["id"].strip():
            sid = entry["id"].strip()
            if sid in seen_ids:
                problems.append(f"seeds[{i}]: id {sid!r} 与 "
                                f"seeds[{seen_ids[sid]}] 重复（账本按 id "
                                "建档，不许重复）")
            else:
                seen_ids[sid] = i
    return problems


# ---------------------------------------------------------------------------
# 加载入口
# ---------------------------------------------------------------------------
def load_seed_config(path: str) -> tuple:
    """读种子配置 → 校验 → 产出 run_collection 能直接吃的 seeds 结构。

    返回 (seeds, status) 二元组：
    - seeds  dict 列表，每条恰为 {"id", "category", "address", "enabled"}
             （id/category/address 为剥空白后的字符串，enabled 为 bool，
             缺省补 True）——与 collector.run_collection 的种子结构校验
             （必含 id/address，可选 category/enabled）严丝合缝，可直接
             传参；本函数不发任何网络请求，fetch/parse 由调用方注入；
    - status 三态之一：
             STATUS_NOT_CONFIGURED（文件不存在：没配种子不是错误，
             是未启用——返回空清单，调用方照常走「本轮零种子」口径）；
             STATUS_EMPTY（文件在、seeds 为空清单——配置过了但一条没填）；
             STATUS_OK（加载成功）。

    损坏/结构非法一律 ValueError 显式上抛（一次报全所有问题，便于
    手工修文件；口径同 collector.load_ledger：坏配置绝不静默当空清单
    用，那会让人误以为「没配种子」，清单改动会无声丢失）：

    - 条目 schema 逐字段校验规则见 _entry_problems docstring；
    - 包裹层 {"version": 1, "seeds": [...]}，未知键拒绝；
    - JSON 语法损坏 → ValueError（含路径）。
    """
    path = os.fspath(path)
    if not os.path.exists(path):
        return [], STATUS_NOT_CONFIGURED
    with open(path, "r", encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError as exc:
            raise ValueError(f"种子配置损坏（{path}）：{exc}") from None
    problems = _validate(data, path)
    if problems:
        raise ValueError("种子配置结构不符（%s）：%s" % (path, "；".join(problems)))
    seeds = []
    for entry in data["seeds"]:
        seeds.append({
            "id": entry["id"].strip(),
            "category": entry["category"].strip(),
            "address": entry["address"].strip(),
            "enabled": entry.get("enabled", True),
        })
    return seeds, (STATUS_OK if seeds else STATUS_EMPTY)
