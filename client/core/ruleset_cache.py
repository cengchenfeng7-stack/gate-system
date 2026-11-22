#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
规则集缓存管理（core/ruleset_cache.py）—— U3：缓存落盘 / 状态报告 / 立即更新
========================================================================
task18 · 阶段五·使用界面线 实现第四段。work10/DESIGN.md §8.3 更新方式的
缓存侧落地：缓存目录解析、逐 tag 状态报告、下载落盘（临时文件 + 原子
替换）、「立即更新」core 函数（界面无关）。

依赖口径：只用标准库 + 同包 ruleset_source（tag 常量与校验单一事实源）；
不依赖 PySide6、不 import client 包其他模块、**本模块自身不访问网络**——
下载器是注入参数（fetcher 函数），离线测试用假 fetcher 给假 .srs 字节。

缓存目录（§8.3）：`~/.local/share/gate-client/rulesets/`（与 ui/cli.py
DEFAULT_DB 同族数据目录；数据目录字面量单一来源 =
ruleset_source.DEFAULT_DATA_DIR，测试锁定同族）。路径可注入以便测试
（data_dir 参数注入以便测试，含来源登记与缓存两层落点）。

宁旧勿缺（简报红线，§8.3）：下载落盘一律「写满临时文件 → os.replace
原子替换」；任何失败路径（fetcher 抛错、空内容、写盘失败）都**不动已有
缓存文件一个字节**，清理临时文件并返回明确失败结果——不抛裸异常给界面层。
内核侧配合口径（§13-E 核实，work18/DESIGN.md）：local 条目文件被修改后
内核自动重载（sing-box ≥1.10），运行中引擎无需重启即吃到新缓存。

立即更新（界面无关 core 函数）：
- local 条目 = 经 fetcher 重新取材 → 原子落盘 → 逐 tag 结果回报；
- remote 条目 = 内核按 update_interval 自动拉新为既定机制，客户端**没有**
  触发内核立即拉新的接口（§13-E 核实：文档无此 API）——本函数对 remote
  条目如实回报 remote_kernel_managed（无动作），绝不假装更新成功。

铁律对齐：本模块没有任何节点选择/切换/优选逻辑——它只把已核定来源的
字节搬进缓存目录并报告状态；拦截语义由 usage_model/plan_ruleset_parts
的 reject 规则承担。
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .ruleset_source import (
    KNOWN_RULESET_TAGS,
    SOURCE_LOCAL,
    SOURCE_REMOTE,
    load_sources,
    resolve_ruleset_state,
    sources_path,
)

# 缓存目录名（数据目录下的派生落点，settings_proto §3.2「rulesets 等派生
# 落点跟随有效数据目录」口径）
CACHE_DIRNAME = "rulesets"
_DEFAULT_DATA_DIR = "~/.local/share/gate-client"

__all__ = [
    "CACHE_DIRNAME",
    "RulesetCacheError",
    "cache_dir",
    "cache_status",
    "current_ruleset_state",
    "tag_cache_path",
    "update_rulesets",
    "update_tag",
]


class RulesetCacheError(ValueError):
    """缓存层非法（tag 未知/目录状态异常）。报错不静默吞。
    下载/落盘失败不抛——走 update_tag 的明确失败结果（界面层口径）。"""


def cache_dir(data_dir=None) -> Path:
    """缓存目录：显式 data_dir（覆盖目录）> 内置默认目录下的 rulesets/
    （与 DEFAULT_DB 同族数据目录）。路径可注入以便测试。"""
    if data_dir is not None:
        return Path(data_dir) / CACHE_DIRNAME
    return Path(_DEFAULT_DATA_DIR).expanduser() / CACHE_DIRNAME


def tag_cache_path(tag: str, data_dir=None) -> Path:
    """tag → 缓存文件路径（<缓存目录>/<tag>.srs）。tag 只认已知常量。"""
    if tag not in KNOWN_RULESET_TAGS:
        raise RulesetCacheError(
            f"tag 只认 {'/'.join(KNOWN_RULESET_TAGS)}，得到 {tag!r}")
    return cache_dir(data_dir) / f"{tag}.srs"


def cache_status(data_dir=None, tags=None) -> dict:
    """逐 tag 状态报告（界面「立即更新」状态行的数据源）。

    返回 {tag: {tag, path, present, size_bytes, last_updated}}——present
    为文件在/不在，size_bytes 为字节数，last_updated 为 mtime epoch 秒
    （文件不在时两者为 None）。目录不存在 = 全部 absent（首次运行，
    平滑起步）；单文件 stat 的意外 OSError 如实报错（不静默当不在）。
    """
    base = cache_dir(data_dir)
    tags = tuple(KNOWN_RULESET_TAGS) if tags is None else tuple(tags)
    for tag in tags:
        if tag not in KNOWN_RULESET_TAGS:
            raise RulesetCacheError(
                f"cache_status 的 tags 含未知 tag {tag!r}（只认 "
                f"{'/'.join(KNOWN_RULESET_TAGS)}）")
    report = {}
    for tag in tags:
        path = base / f"{tag}.srs"
        try:
            stat = path.stat()
        except (FileNotFoundError, NotADirectoryError):
            present, size, mtime = False, None, None
        except OSError as exc:
            raise RulesetCacheError(
                f"缓存文件状态读取失败: {path}——{exc}") from exc
        else:
            present, size, mtime = True, stat.st_size, stat.st_mtime
        report[tag] = {
            "tag": tag,
            "path": str(path),
            "present": present,
            "size_bytes": size if present else None,
            "last_updated": mtime if present else None,
        }
    return report


def current_ruleset_state(data_dir=None) -> dict:
    """来源登记 × 缓存状态 → 逐 tag 收口状态（供给器接线的单点入口）。

    现读 ruleset_source.load_sources + cache_status（路径可注入），
    经 ruleset_source.resolve_ruleset_state 合并。来源登记文件损坏如实
    抛 RulesetSourceError（ValueError 族）——供给器接线把非法状态上抛给
    引擎包成 EngineError 显式失败，不静默按「未核定」连（task17 供给器
    同款口径）。
    """
    sources = load_sources(sources_path(data_dir))
    return resolve_ruleset_state(sources, cache_status(data_dir))


# ---------------------------------------------------------------------------
# 下载落盘（宁旧勿缺：临时文件 + 原子替换；失败不动旧文件一个字节）
# ---------------------------------------------------------------------------
def _default_fetcher(tag: str, entry: dict) -> bytes:
    """默认取材器：读主人核定来源的本地 .srs 文件字节（local 条目）。
    remote 条目不走客户端下载（内核托管），到不了这里。"""
    with open(entry["path"], "rb") as f:
        return f.read()


def update_tag(tag: str, fetcher=None, data_dir=None,
               source_entry=None) -> dict:
    """单个 tag 的「立即更新」（界面无关 core 函数）。

    - local 已核定条目：fetcher(tag, entry) → bytes（默认 = 读来源文件）
      → 非空校验 → 临时文件写满 → os.replace 原子替换缓存文件。
      任何失败返回明确失败结果（ok=False + kind + message），已有缓存
      文件字节不动（宁旧勿缺红线）；不抛裸异常给界面层。
    - remote 条目：内核托管（§13-E：客户端无触发接口）→ 如实回报
      remote_kernel_managed，无任何文件动作。
    - 未核定（空槽）：回报 no_source，无动作。

    fetcher 为注入参数（离线测试给假 .srs 字节；真接线默认读来源文件，
    主人侧未来换取材方式不动本模块）。tag 非法 = 编程错误，抛
    RulesetCacheError；下载/落盘失败 = 运行结果，返回不抛。
    """
    if tag not in KNOWN_RULESET_TAGS:
        raise RulesetCacheError(
            f"tag 只认 {'/'.join(KNOWN_RULESET_TAGS)}，得到 {tag!r}")
    entry = (source_entry if source_entry is not None
             else load_sources(sources_path(data_dir)).get(tag))
    result = {
        "tag": tag, "ok": False, "kind": None,
        "path": None, "size_bytes": None, "last_updated": None,
        "message": "",
    }
    if not entry or entry.get("type") not in (SOURCE_LOCAL, SOURCE_REMOTE):
        result["kind"] = "no_source"
        result["message"] = (
            f"{tag} 来源未核定（空槽）：无下载动作；先经 "
            "ruleset_source.approve_source 核定来源")
        return result
    if entry["type"] == SOURCE_REMOTE:
        result["kind"] = "remote_kernel_managed"
        result["message"] = (
            f"{tag} 为远端条目：内核按 update_interval 自动拉新"
            "（缺省 1d）；客户端没有触发内核立即拉新的接口"
            "（work18/DESIGN.md §13-E）——本次无动作，"
            "等内核周期或重启引擎生效")
        return result

    base_path = tag_cache_path(tag, data_dir)
    result["path"] = str(base_path)
    fetcher = _default_fetcher if fetcher is None else fetcher
    try:
        data = fetcher(tag, entry)
    except BaseException as exc:              # noqa: BLE001 —— 界面层要的是结果不是栈
        result["kind"] = "fetch_failed"
        result["message"] = f"{tag} 取材失败（旧缓存保持不动）：{exc!r}"
        return result
    if not isinstance(data, (bytes, bytearray)) or not data:
        result["kind"] = "fetch_failed"
        result["message"] = (
            f"{tag} 取材结果为空或非字节（拒写——宁旧勿缺，旧缓存保持不动）")
        return result

    tmp_name = None
    try:
        base_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=base_path.name + ".",
                                        suffix=".tmp",
                                        dir=str(base_path.parent))
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_name, base_path)       # 原子替换：成功才可见
    except BaseException as exc:              # noqa: BLE001
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)           # 失败清临时文件，旧文件从未被动
            except OSError:
                pass
        result["kind"] = "write_failed"
        result["message"] = f"{tag} 缓存落盘失败（旧缓存保持不动）：{exc!r}"
        return result
    stat = base_path.stat()
    result.update(ok=True, kind="updated", size_bytes=stat.st_size,
                  last_updated=stat.st_mtime,
                  message=f"{tag} 缓存已更新（{stat.st_size} 字节，原子替换）")
    return result


def update_rulesets(fetcher=None, data_dir=None, sources=None,
                    tags=None) -> dict:
    """「立即更新」按钮的 core 函数：逐 tag 触发更新并回报结果。

    - 默认对全部已知 tag 回报（未核定 = no_source、远端 = 内核托管，
      都如实列出，不给界面留盲区）；tags 可注入子集。
    - sources 可注入（缺省现读登记文件）；fetcher 注入取材器
      （离线测试假字节；真接线缺省读 local 来源文件）。
    - 单 tag 的失败不中断其余 tag（逐 tag 独立回报）。
    返回 {tag: update_tag 结果}。tag 非法抛 RulesetCacheError（编程错误）。
    """
    tags = tuple(KNOWN_RULESET_TAGS) if tags is None else tuple(tags)
    if sources is None:
        sources = load_sources(sources_path(data_dir))  # 现读一次，逐 tag 复用
    results = {}
    for tag in tags:
        results[tag] = update_tag(tag, fetcher=fetcher,
                                  data_dir=data_dir,
                                  source_entry=sources.get(tag))
    return results
