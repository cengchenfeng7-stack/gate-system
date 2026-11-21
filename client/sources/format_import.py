#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
统一格式识别导入原型（sources/format_import.py 阶段二前置）
==========================================================
一个入口吃任意文本/文件，自动识别四种来源格式并分发到现有解析实现：

  1. 整段 base64 订阅（可换行包装，可带注释头 / BOM / CRLF / 夹杂说明行）
  2. 明文链接列表（vless / ss / trojan / sstp / vmess，一行一条）
  3. .txt 文件——内容上与明文列表同一分支，额外兼容 manual.py 既有的
     nodes_*.txt「入口#标签$链接」行惯例（差异在容错，不在判定）
  4. Clash YAML —— 整体转交 task1 直导入原型 clash_import.import_clash_text

做法：先逐行分类，再聚合判定，替换 parse_subscription_text 里「先整段解
base64、解不开就整篇当链接逐行碰」的启发式。读码 + 实测确认的旧行为失败
模式（混合 b64+明文整块丢失、注释头挡住 base64、$ 行全被跳过）见
work2/DESIGN.md §2，本模块逐条消除。

复用不重造：base64 容错解码仍用 subscription.decode_subscription_text，
单链接解析仍用 manual.parse_link，Clash 仍用 clash_import；本模块只做
识别、行归一化（$ 行）、跳过计数与结果归一。

- 归属沿用 D1（2026-11-19）：source 默认 "manual" 归手动导入页，不新增来源；
  subscription_url / subscription_name 写进 node.extra 溯源。
- 按 node.id 批内去重（先出现者胜，保持原顺序）——口径同 subscription.py。
- 识别不了 → 显式 ValueError（绝不返回静默空结果）；部分坏行计数跳过不中断。
- 纯本地解析，不发任何网络请求；订阅 URL 拉取仍走 subscription.py 上游
  （fetch/import_subscription_url），本入口只吃内容。
- 铁律：不含任何自动测速/自动选择逻辑（Clash 的 proxy-groups 分组由
  clash_import 一律不导入，本项目节点只由用户手动固定）。
"""
from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field

# 现有实现导入：集成进 client 包后走包内同级导入；原型期（本目录单独跑）
# 回落 client-dev 树（可用环境变量 GATE_CLIENT_DEV 覆盖路径）——约定同
# work1 的 clash_import.py 原型，集成后回落分支自然不触发。
try:
    from . import manual
    from .subscription import decode_subscription_text
except ImportError:
    _CLIENT_DEV = os.environ.get(
        "GATE_CLIENT_DEV", "/home/hatch/workspace/vpn-project/client-dev")
    if os.path.isdir(_CLIENT_DEV):
        sys.path.insert(0, _CLIENT_DEV)
    from client.sources import manual
    from client.sources.subscription import decode_subscription_text


@dataclass
class FormatImportResult:
    """统一导入结果：四个分支归一成一种形状，UI/CLI 一套提示逻辑即可消费。
    计数口径（详细定义见 DESIGN.md §6）：
    - total   输入侧实质条目数：链接类分支 = 非空非注释行数；clash = proxies
              条目数。b64 解码后一行可展开多条链接，total 不以产出节点数为上界。
    - skipped 坏行/坏条目数（不中断整批，只计数）。空行/# 注释行不计入。
    - skipped_reasons / skipped_types 诊断计数，对齐 clash_import 口径。
    """
    nodes: list                                    # list[Node]，按 id 批内去重
    format: str                                    # "base64"|"plain"|"mixed"|"clash_yaml"
    total: int
    skipped: int
    skipped_reasons: dict = field(default_factory=dict)
    skipped_types: dict = field(default_factory=dict)  # 仅 clash 分支非空


# ---------------- 逐行分类（识别的地基） ----------------

# Clash YAML 的决定性签名：行首 proxies:。链接行必含 "://"（本行没有），
# b64 行字母表里没有冒号（本行有），故该签名在另两类格式中不可能出现，
# 误报面≈0。proxy-providers: / proxy-groups: 不匹配，不算证据。
_CLASH_SIG_RE = re.compile(r"proxies\s*:")

# b64 行样式：与 subscription.decode_subscription_text 的 _B64_CORE_RE 同一
# 字符集规则（标准 + urlsafe 并集），`=` 只允许作结尾 padding。行内空白先
# 去除（容忍空格混杂的换行包装）。含 "://" 的行在更早的 link 判定分支截住，
# 到不了这里（":" 不在 b64 字符集里，两类互斥由构造保证）。
_B64ISH_RE = re.compile(r"[A-Za-z0-9+/\-_]+=*\Z")


def _strip_bom(text: str) -> str:
    """去句首 BOM（utf-8 非 utf-8-sig 读文件会残留，污染首行判定）。
    与 decode_subscription_text 同款处理，但要在逐行分类之前做。"""
    return text[1:] if text.startswith("\ufeff") else text


def _classify_lines(text: str) -> list:
    """逐行分类，返回按原顺序的 (kind, line) 列表，kind ∈：
    - blank   空白行（任何格式都不计入实质行）
    - comment # 注释行（b64 订阅说明头、链接 txt 的注释、YAML 注释都归这）
    - clash   行首 proxies: ——Clash YAML 决定性签名
    - link    含 "://" 的行（暂不支持的链接也算 link 行，坏行交给解析层计数）
    - b64     去除行内空白后整体匹配 b64 字符集（`=` 只许结尾 padding）
    - other   其余（说明文字、乱码、JSON……）
    纯字母数字的短行（日期、口令残片）也会命中 b64 样式——只影响 format
    标签与原因归类，不影响正确性：解不出链接时一律计数跳过（DESIGN §4）。"""
    lines = []
    for raw in text.splitlines():
        s = raw.strip()
        if not s:
            lines.append(("blank", s))
        elif s.startswith("#"):
            lines.append(("comment", s))
        elif _CLASH_SIG_RE.match(s):
            lines.append(("clash", s))
        elif "://" in s:
            lines.append(("link", s))
        elif _B64ISH_RE.fullmatch("".join(s.split())):
            lines.append(("b64", s))
        else:
            lines.append(("other", s))
    return lines


def detect_format(text: str) -> str:
    """只做结构判定，不解析（解析交给各分支的现有实现）。返回：
    - "clash_yaml"  有 proxies: 签名行（判定顺序第一，见 DESIGN §4）
    - "mixed"       链接行与 b64 样式行并存（b64 夹明文）
    - "plain"       只有链接行（明文链接列表 / txt 文件）
    - "base64"      只有 b64 样式行（整段订阅）
    - "unknown"     什么都没有（空 / 仅注释 / 纯乱码）——import_any_text
                    会据此显式报错；detect 本身不抛，方便 UI 先行预览。"""
    kinds = {k for k, _ in _classify_lines(_strip_bom(text))}
    if "clash" in kinds:
        return "clash_yaml"
    if "link" in kinds and "b64" in kinds:
        return "mixed"
    if "link" in kinds:
        return "plain"
    if "b64" in kinds:
        return "base64"
    return "unknown"


# ---------------- Clash 分支：转交 task1 直导入原型 ----------------

# 惰性加载缓存：成功加载的 import_clash_text / 加载失败原因（测试可注入
# 模拟失败）。Clash 依赖只在真正需要时加载，加载不到绝不静默吞掉。
_CLASH_IMPORTER = None
_CLASH_IMPORT_ERROR = None


def _get_clash_importer():
    """加载 Clash 导入函数，顺序（见 DESIGN §7 衔接方式）：
    1. 包内同级（集成位 client/sources/clash_import.py）；
    2. 绝对导入 client.sources.clash_import（task1 原型已原样落入 client-dev）；
    3. task1 原型目录 work1/clash_import.py（可用 GATE_TASK1_DIR 覆盖）；
    三处都拿不到 → 把根因存下来抛 ImportError，Clash 分支据此显式报错。
    结果缓存，进程内只加载一次。"""
    global _CLASH_IMPORTER, _CLASH_IMPORT_ERROR
    if _CLASH_IMPORTER is not None:
        return _CLASH_IMPORTER
    if _CLASH_IMPORT_ERROR is not None:
        raise _CLASH_IMPORT_ERROR
    err = None
    try:
        from .clash_import import import_clash_text  # 集成后走这
        _CLASH_IMPORTER = import_clash_text
        return _CLASH_IMPORTER
    except ImportError as exc:
        err = exc
    try:
        from client.sources.clash_import import import_clash_text
        _CLASH_IMPORTER = import_clash_text
        return _CLASH_IMPORTER
    except ImportError as exc:
        err = exc
    work1 = os.environ.get("GATE_TASK1_DIR",
                           "/home/hatch/workspace/zcode-tasks/work1")
    fallback = os.path.join(work1, "clash_import.py")
    if os.path.isfile(fallback):
        try:
            if work1 not in sys.path:
                sys.path.insert(0, work1)
            import clash_import as _task1
            _CLASH_IMPORTER = _task1.import_clash_text
            return _CLASH_IMPORTER
        except ImportError as exc:
            err = exc
    _CLASH_IMPORT_ERROR = ImportError(
        "Clash YAML 导入分支加载失败：集成位 client.sources.clash_import 与 "
        f"task1 原型（{fallback}）均不可用（含 PyYAML 依赖），根因：{err}")
    raise _CLASH_IMPORT_ERROR


def _import_clash_text(text, source, subscription_url, subscription_name):
    """Clash 分支本体：整体转交 import_clash_text，本模块不碰 YAML 逻辑。
    yaml 解析失败 → import_clash_text 原样抛 ValueError（显式报错口径）。"""
    import_clash_text = _get_clash_importer()
    cr = import_clash_text(text, source=source,
                           subscription_url=subscription_url,
                           subscription_name=subscription_name)
    return FormatImportResult(nodes=cr.nodes, format="clash_yaml",
                              total=cr.total, skipped=cr.skipped,
                              skipped_reasons=cr.skipped_reasons,
                              skipped_types=cr.skipped_types)


# ---------------- 链接行解析（明文 / b64 解码后共用） ----------------

def _reason_key(line: str, exc: ValueError) -> str:
    """parse_link 的 ValueError 归一成稳定的原因键（链接原文不进键，避免
    原因字典爆炸）：
    - "不是分享链接: <原文>" → 固定键 "不是分享链接"
    - http(s) 链接行 → 提示这是订阅地址，拉取要走 subscription.py 上游
    - "暂不支持的链接类型: xxx://" → 保留原话（本身不含原文，可按协议聚合）"""
    msg = str(exc)
    if msg.startswith("不是分享链接"):
        return "不是分享链接"
    scheme = line.split("://", 1)[0].lower() if "://" in line else ""
    if scheme in ("http", "https"):
        return ("http(s) 行是订阅地址不是节点链接"
                "（本入口只解析内容；拉取订阅走 import_subscription_url）")
    return msg


def _walk_links(lines, source):
    """链接行 → (nodes, skipped, reasons)。空行/# 注释行静默跳过不计坏行
    （口径同 parse_subscription_text）；nodes_*.txt 的「入口#标签$链接」行
    先做 $ 归一化——最后一个 $ 之后含 "://" 才切（同一惯例见
    manual.parse_links_text，但加了 :// 守卫：凭证里恰好含 $ 的链接不会被
    误切，老实现对这类行是直接切坏）。parse_link 的 ValueError 计 skipped
    并归因，不中断整批。"""
    nodes, skipped, reasons = [], 0, {}
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        ln = s
        if "$" in ln:
            tail = ln.rsplit("$", 1)[1]
            if "://" in tail:
                ln = tail
        try:
            nodes.append(manual.parse_link(ln, source=source))
        except ValueError as exc:
            key = _reason_key(s, exc)
            skipped += 1
            reasons[key] = reasons.get(key, 0) + 1
    return nodes, skipped, reasons


def _dedup(nodes):
    """按 node.id 批内去重（先出现者胜，保持原顺序）——口径同
    subscription.parse_subscription_text 与 clash_import。"""
    seen, out = set(), []
    for n in nodes:
        if n.id not in seen:
            seen.add(n.id)
            out.append(n)
    return out


def _merge_reasons(dst: dict, src: dict) -> None:
    for k, v in src.items():
        dst[k] = dst.get(k, 0) + v


def _unrecognized_message(other_lines, text) -> str:
    """「什么结构都没有」的报错文案：给出行数与样例，形似 JSON / 非 UTF-8
    时附提示，帮用户对号入座（旧启发式在这种输入下只给静默 0 节点）。"""
    if not other_lines:
        return ("导入内容只有空行/注释，没有可导入的链接、base64 订阅或 "
                "Clash 配置")
    msg = (f"无法识别导入内容：没有链接行、base64 行或 Clash proxies: 签名"
           f"（非空非注释实质行 {len(other_lines)} 行，"
           f"如「{other_lines[0][:24]}…」）")
    t = text.lstrip()
    if t.startswith("{") and t.rstrip().endswith("}"):
        msg += "；形似 JSON：sing-box 等客户端配置暂不支持导入"
    elif "\x00" in text:
        msg += "；文本含 NUL 字节：可能不是 UTF-8 编码（如 UTF-16 存的文件）"
    return msg


# ---------------- 统一入口 ----------------

def import_any_text(text: str, source: str = "manual",
                    subscription_url: str = "",
                    subscription_name: str = "") -> FormatImportResult:
    """统一导入入口：任意文本 → 识别格式 → 分发到现有解析实现 → 归一结果。

    - 识别不了 → ValueError（空内容 / 只有注释 / 纯乱码 / 整段 b64 样式但
      解不出链接），绝不静默返回 0 节点；
    - 部分坏行（不支持协议、格式坏、夹杂说明行）→ 计数跳过不中断整批；
    - 纯本地解析，不发网络请求；只解析不测速不选择节点。"""
    text = _strip_bom(text)
    if not text.strip():
        raise ValueError("导入内容为空")

    lines = _classify_lines(text)
    kinds = {k for k, _ in lines}

    # 分支一：Clash YAML —— 行首 proxies: 签名即转 task1 直导入，本模块不碰 YAML
    if "clash" in kinds:
        return _import_clash_text(text, source=source,
                                  subscription_url=subscription_url,
                                  subscription_name=subscription_name)

    link_lines = [s for k, s in lines if k == "link"]
    b64_lines = [s for k, s in lines if k == "b64"]
    other = [s for k, s in lines if k == "other"]

    # 分支五：结构上什么都认不出 → 显式报错（旧行为是静默 0 节点 + 计数）
    if not link_lines and not b64_lines:
        raise ValueError(_unrecognized_message(other, text))

    nodes, skipped, reasons = [], 0, {}

    # 分支二/四的 b64 组：只把 b64 行拼回整段交 decode_subscription_text。
    # 注释头 / 明文行 / 说明行已被分类剥走，不再污染字符集判定——旧行为的
    # 失败模式（混合 b64+明文、注释头挡 b64）由这一步消除。
    if b64_lines:
        decoded, b64fmt = decode_subscription_text("\n".join(b64_lines))
        if b64fmt == "base64":
            dn, dsk, drs = _walk_links(decoded.splitlines(), source)
            nodes += dn
            skipped += dsk
            _merge_reasons(reasons, drs)
        elif link_lines:
            # 混合输入里 b64 组报废 → 只计数跳过，明文行照常导入
            skipped += len(b64_lines)
            reasons["base64 解码失败或解码后不含链接"] = len(b64_lines)
        else:
            raise ValueError(
                f"内容为 base64 样式但解不出链接订阅（{len(b64_lines)} 行，"
                "解码失败或解码后不含 ://），无法当作链接列表导入")

    # 分支二/三：明文链接行（txt 文件与明文列表同一处理，含 $ 行归一化）
    if link_lines:
        ln_, lsk, lrs = _walk_links(link_lines, source)
        nodes += ln_
        skipped += lsk
        _merge_reasons(reasons, lrs)

    if other:
        skipped += len(other)
        reasons["无法识别的行"] = len(other)

    fmt = "mixed" if (b64_lines and link_lines) \
        else ("base64" if b64_lines else "plain")
    nodes = _dedup(nodes)
    if subscription_url:
        for n in nodes:
            n.extra["subscription_url"] = subscription_url
    if subscription_name:
        for n in nodes:
            n.extra["subscription_name"] = subscription_name
    return FormatImportResult(
        nodes=nodes, format=fmt,
        total=len(link_lines) + len(b64_lines) + len(other),
        skipped=skipped, skipped_reasons=reasons)


def import_any_file(path: str, **kwargs) -> FormatImportResult:
    """读本地文件（utf-8, errors='replace'——口径同 load_subscription_file /
    import_clash_file；BOM 残留由 import_any_text 剥）后调 import_any_text。
    只读文件，不发网络请求。"""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    return import_any_text(text, **kwargs)
