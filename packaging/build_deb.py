#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gate-client deb 打包脚本原型（task13 · packaging 侧，deb 专案版）。

口径（对齐磁盘最新 task13.md，路线已定 deb，2026-11-22 主人亲定）：
- 输入 = client-dev 的 client/ 树，**只读**，绝不改原件（树里的编辑器
  备份/临时文件在 staging 副本里排除，源树原件不删不移）。
- 输出 = <out>/（默认 build_deb.py 同级目录的 ../dist/，即 work13/dist/）：
    staging-deb/                    deb staging（DEBIAN/ + 安装布局全摆好）
    gate-client_<ver>-1_all.deb     dpkg-deb --build 产物
- 安装布局（依据见 DESIGN.md §3）：
    /usr/bin/gate-client            CLI 包装脚本
    /usr/bin/gate-client-gui        GUI 包装脚本
    /usr/share/gate-client/client/  程序本体（清洗后的 client/ 树）
    /usr/share/applications/gate-client.desktop
- 标准库 only，不 import client 包（对源码只做字节级清洗，不执行其逻辑）。
- 全程离线：不联网、不 pip install、不 apt install、不 sudo、不 dpkg -i、
  不写任何系统目录（dpkg-deb --build 只产出 deb 文件，不装）。
- 版本号一律读 <client-dev>/client/VERSION，不在脚本里手写。

打包期清洗（本机痕迹去除，依据 DESIGN.md §2.5）：client 源码树里有 3 处
原型期遗留的作者机器绝对路径（try/except ImportError 的死回落分支），
打包时把路径字面量换成 ``os.devnull``——deb 安装形态下，包内相对导入
必然成功，这些回落分支永不可达，可观测行为不变；清洗后在 staging 里
全树复扫 ``/home/`` 归零，并 py_compile 校验语法。
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import os
import py_compile
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------------------
# 设计常量（DESIGN.md §3/§4 的机器可读锚点，测试逐项对账）

PKG_NAME = "gate-client"
DEB_REVISION = "-1"          # Debian 修订号：打包元数据，与上游版本号无关
PY3_MIN = "3.7"              # 代码语法实测下限（DESIGN.md §4.2：无 3.8+ 特性）
DEPENDS = "python3 (>= 3.7), python3-yaml"
SUGGESTS = "python3-venv"
MAINTAINER = "gate-client local build <local@invalid>"
ARCH = "all"

# deb 内程序根（包装脚本以 $0 相对解析同一根，任意 -x prefix 均可运行）
APP_ROOT_DEB = "/usr/share/gate-client"

# 用户数据目录：DEBIAN 维护脚本与包装脚本的禁区（红线）。
USER_DATA_DIR = "~/.local/share/gate-client"
CONFIG_DATA_DIR = "~/.config/gate-client"

# 打包确定性的固定时间戳（2000-01-01 UTC）：staging 文件 mtime 与
# dpkg-deb 的 ar/tar 时间都归一到它（SOURCE_DATE_EPOCH），两次构建产物
# 字节一致（幂等，测试复核）。
FIXED_EPOCH = 946684800

# 客户端自报订阅/链接字样的 IP 白名单（REVIEW_FAIL 2026-11-22 裁决·方案 A：
# 由逐个枚举改为「按类放行文档段 + 既有逐条豁免」）。
#
# ① RFC 5737 文档段按类放行（TEST-NET-1/2/3）：文档段**按定义永不可能是
#    真实节点**，逐个枚举是设计缺陷——每来一个新文档地址就撞一次墙
#    （task15 集成实录：faults.py 的 203.0.113.7 / 198.51.100.7 被拦）。
# ② 非文档段的既有豁免逐条保留、不按类放开（放开=防线放水）：公共 DNS
#    预设（usage_model 同款口径）、回环/未指定地址、TUN 默认网段
#    （172.19.0.0/24，RFC1918 私有段）、Chrome UA 版本号被 IP 正则误配
#    的形态（126.0.0.0 / 140.0.0.0 不是地址）。
# 防线本意不变：白名单之外的任何 IPv4 一律判失败（拦一切真实 IP）。
_DOC_NETS = tuple(ipaddress.ip_network(n)
                  for n in ("192.0.2.0/24", "198.51.100.0/24",
                            "203.0.113.0/24"))
_IP_EXPLICIT = frozenset({
    "0.0.0.0", "127.0.0.1", "1.1.1.1", "8.8.8.8", "9.9.9.9",
    "172.19.0.0", "172.19.0.1", "172.19.0.5",
    "126.0.0.0", "140.0.0.0",
})


def ip_allowed(ip_str) -> bool:
    """IPv4 字符串是否放行：RFC 5737 文档段按类 + 逐条豁免集。

    解析不出 / 非 IPv4 一律不放行（不猜）。两个消费点
    （deb_selfcheck 第 5 项、test_packaging 独立复扫）经
    _IP_WHITELIST 的 ``in`` 运算走本函数，白名单改这一处即同过。"""
    try:
        addr = ipaddress.ip_address(str(ip_str).strip())
    except ValueError:
        return False
    if addr.version != 4:
        return False
    if any(addr in net for net in _DOC_NETS):
        return True
    return str(addr) in _IP_EXPLICIT


class _IpWhitelist:
    """白名单容器：``in`` 运算 = ip_allowed()。

    保持旧的 ``m.group(0) not in _IP_WHITELIST`` 消费写法不变
    （deb_selfcheck 与 test_packaging 独立复扫都这么读）。"""

    __slots__ = ()

    def __contains__(self, item) -> bool:
        return ip_allowed(item)

    def __repr__(self) -> str:
        return (f"_IpWhitelist(doc_nets={[str(n) for n in _DOC_NETS]}, "
                f"explicit={sorted(_IP_EXPLICIT)})")


_IP_WHITELIST = _IpWhitelist()
_IP_RE = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
# 链接 scheme + 真载荷（≥12 个 base64/URL 常用字符）。源码里的格式示例
# （如 vmess://<base64、socks5://127.0.0.1:{port}）含 < { ( " 等非载荷
# 字符，落不进本正则；真实订阅链接会命中。
_SCHEME_RE = re.compile(
    r"\b(?:vless|vmess|trojan|hysteria2?|tuic|ssr?|socks5?)://"
    r"[A-Za-z0-9+/_@:?=&.-]{12,}")
_SECRET_MARKERS = ("PRIVATE KEY", "BEGIN RSA", "BEGIN EC",
                   "BEGIN OPENSSH", "ssh-rsa ")

# 打包期清洗表：相对路径 → (原文, 替换)。3 处全部是 try/except ImportError
# 的原型期回落默认值（format_import.py:47,173、clash_import.py:43）。
# os.devnull="/dev/null"：isdir/isfile 恒 False → 回落分支行为 = 目标机上
# 原默认路径不存在时的行为，完全一致。
_SCRUBS = {
    "client/sources/format_import.py": [
        ('"/home/hatch/workspace/vpn-project/client-dev"', "os.devnull"),
        ('"/home/hatch/workspace/zcode-tasks/work1"', "os.devnull"),
    ],
    "client/sources/clash_import.py": [
        ('"/home/hatch/workspace/vpn-project/client-dev"', "os.devnull"),
    ],
}

# staging 排除项：解释器缓存、开发测试、编辑器备份/临时文件不进产品包
# （依据 DESIGN.md §1.6）。备份/临时形态按文件名 fnmatch 匹配——审查实录
# （REVIEW_FAIL.md，2026-11-22）里目标机树出现过 *.bak-20261121-* 改动前
# 快照，打包器对此要有预期内处理：staging 直接排除、不进包；**源树原件
# 一律不动**（不删、不移、不改）。文件形态自检（no_subscription）保持
# 原样，真正非法的数据文件（.json/.ovpn/订阅等）仍会被它拦下。
_EXCLUDE_NAMES = (
    "__pycache__",
    "*.pyc", "*.pyo",
    # —— 编辑器备份/临时文件形态 ——
    "*.bak*",    # .bak、.bak-YYYYMMDD-<tag> 等备份快照（含裸 .bak）
    "*~",        # 经典波浪号备份
    "*.orig",    # patch/合并残留
    "*.rej",     # patch 拒绝块
    "*.swp", ".*.swp",   # Vim 交换档（明名/隐名）
    "*.swx", ".*.swx",   # Vim 交换档退出残留
    "*.tmp*",    # 临时文件（.tmp、.tmp-123 等）
    "#*#",       # Emacs 自动保存 #name#
    ".DS_Store", # macOS Finder 元数据
)
_EXCLUDE_IGNORE = shutil.ignore_patterns(*_EXCLUDE_NAMES)

# 产物内允许的文件形态（结构级防线：不许夹带数据文件）。"" 为无后缀的
# 包装脚本（usr/bin/gate-client*）与 client/VERSION。
_ALLOWED_SUFFIXES = {".py", ".md", ".desktop", ""}

# ---------------------------------------------------------------------------
# 包装脚本与维护脚本模板（deb 打包侧产物，不属于 client 源码树）

_CLI_LAUNCHER = '''\
#!/bin/sh
# gate-client CLI 启动器（task13 deb 打包侧产物）。
# 程序根按 $0 相对解析：装在 / 时解析到 /usr/share/gate-client；
# 被以 dpkg-deb -x 解包到任意 prefix 时同样成立（安装模拟免 chroot）。
# --version 读程序本体的 client/VERSION（版本唯一来源），不手写。
set -e
APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../share/gate-client" && pwd)
if [ "$#" -ge 1 ]; then
    case "$1" in
        --version|-V)
            printf 'gate-client %s\\n' "$(cat "$APP_ROOT/client/VERSION")"
            exit 0
            ;;
    esac
fi
PYTHON="${GATE_CLIENT_PYTHON:-python3}"
PYTHONPATH="$APP_ROOT${PYTHONPATH:+:$PYTHONPATH}"
PYTHONSAFEPATH=1
export PYTHONPATH PYTHONSAFEPATH
exec "$PYTHON" -m client.ui.cli "$@"
'''

_GUI_LAUNCHER = '''\
#!/bin/sh
# gate-client GUI 启动器（task13 deb 打包侧产物）。
# PySide6 的 Ubuntu 包名待本机核实（Ubuntu 24.04 apt 源实测无
# python3-pyside6），解释器候选链解决 GUI 依赖：
#   1. GATE_CLIENT_GUI_PYTHON 环境变量（装了 PySide6 的解释器）；
#   2. ~/.venv-gui/bin/python3（TX-6PRO 手工安装现状）；
#   3. ~/gate-client/.venv-gui/bin/python3（同上，源码树内 venv）；
#   4. python3（无 PySide6 → client.ui.app 打印安装指引退出 2，不假装能跑）。
set -e
APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../share/gate-client" && pwd)
PYTHONPATH="$APP_ROOT${PYTHONPATH:+:$PYTHONPATH}"
PYTHONSAFEPATH=1
export PYTHONPATH PYTHONSAFEPATH
if [ -n "$GATE_CLIENT_GUI_PYTHON" ]; then
    exec "$GATE_CLIENT_GUI_PYTHON" -m client.ui.app "$@"
fi
for cand in "$HOME/.venv-gui/bin/python3" \\
            "$HOME/gate-client/.venv-gui/bin/python3" \\
            python3; do
    if "$cand" -c "import PySide6" >/dev/null 2>&1; then
        exec "$cand" -m client.ui.app "$@"
    fi
done
echo "gate-client-gui: 候选解释器均无 PySide6，回退 python3（GUI 将打印安装指引并退出）" >&2
exec python3 -m client.ui.app "$@"
'''

_DESKTOP = '''\
[Desktop Entry]
Type=Application
Name=Gate Client
GenericName=代理客户端
Comment=自研代理客户端图形界面（CLI: gate-client）
Exec=gate-client-gui
Terminal=false
Categories=Network;Utility;
StartupWMClass=gate-client
# Icon= 未解决项：仓库无图标文件（task13 红线不许现编图片）。
# 补齐后把图标装进 /usr/share/icons/hicolor/<尺寸>/apps/gate-client.png
# 并在此行填 Icon=gate-client。
'''

# control（逐字段依据见 DESIGN.md §4）
_DEB_CONTROL = '''\
Package: {pkg}
Version: {version}{revision}
Section: net
Priority: optional
Architecture: {arch}
Depends: {depends}
Suggests: {suggests}
Maintainer: {maintainer}
Description: 自研代理客户端（CLI + GUI）
 纯 Python 标准库实现：节点库、来源收纳、验出口、连接（显式点选，
 绝不自动切换节点）。CLI 入口 /usr/bin/gate-client，GUI 入口
 /usr/bin/gate-client-gui，程序本体 /usr/share/gate-client/client/。
 .
 图形界面需要 PySide6：Ubuntu 24.04 (noble) apt 源实测无
 python3-pyside6，包名待本机核实；建议 pip 装进 venv 后以
 GATE_CLIENT_GUI_PYTHON 指给 /usr/bin/gate-client-gui。
 sing-box 内核不在本包内：运行时按 GATE_CLIENT_SINGBOX_BIN ->
 ~/.local/bin/sing-box -> PATH 顺序解析；找不到内核时 CLI/GUI 显式
 报错并给出安装指引（不会静默回退）。
 用户数据（client.db、settings.json）在 {user_data_dir}，本包升级/
 卸载/purge 一律不碰。
'''

# maintainer scripts（设计依据 DESIGN.md §5；实装验证待真机 dpkg -i）
_PREINST = '''\
#!/bin/sh
# preinst（task13 原型）：刻意 no-op。
# 升级是 dpkg 的原地文件替换，无前置迁移、无服务要停（CLI/GUI 均无常驻
# 守护）；红线：不许碰用户数据目录（~/.local/share/gate-client、
# ~/.config/gate-client），本脚本对其零引用。
set -e
exit 0
'''

_POSTINST = '''\
#!/bin/sh
# postinst（task13 原型）：只做包内文件的后处理，全部限程序根内。
# 1. 预编译字节码：用户首启免编译（程序根 root 所有，用户运行期写不进）。
# 2. 刷新桌面数据库（在场的机器上才执行）。
# 红线：不碰用户数据目录；无联网；无自动更新动作。
set -e
APP_ROOT=/usr/share/gate-client
if command -v python3 >/dev/null 2>&1; then
    python3 -m compileall -q "$APP_ROOT/client" || true
fi
if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database -q || true
fi
exit 0
'''

_PRERM = '''\
#!/bin/sh
# prerm（task13 原型）：刻意 no-op。
# CLI/GUI 无常驻会话要停（连接进程自管生命周期）；用户数据零引用。
set -e
exit 0
'''

_POSTRM = '''\
#!/bin/sh
# postrm（task13 原型）：只清理程序根内 dpkg 不认领的字节码缓存残留。
# purge 口径（DESIGN.md §5.2）：即使用 purge，也不删用户数据——
# 用户数据目录本就不归本包所有（无 conffiles、无 $HOME 文件），
# dpkg purge 只删包自有文件；本脚本对 ~/.local/share/gate-client、
# ~/.config/gate-client 零引用。
set -e
APP_ROOT=/usr/share/gate-client
if [ -d "$APP_ROOT" ]; then
    find "$APP_ROOT" -depth -type d -name __pycache__ \\
        -exec rm -rf {} + 2>/dev/null || true
    find "$APP_ROOT" -depth -type d -empty -exec rmdir {} + 2>/dev/null || true
fi
exit 0
'''

_MAINTAINER_SCRIPTS = {"preinst": _PREINST, "postinst": _POSTINST,
                       "prerm": _PRERM, "postrm": _POSTRM}


# ---------------------------------------------------------------------------
# 构建

def read_version(client_dev: Path) -> str:
    """版本唯一来源：<client-dev>/client/VERSION。"""
    path = client_dev / "client" / "VERSION"
    text = path.read_text(encoding="utf-8").strip()
    if not re.fullmatch(r"\d+\.\d+\.\d+", text):
        raise SystemExit(f"[打包] client/VERSION 内容异常: {text!r}")
    return text


def deb_version(client_dev: Path) -> str:
    return read_version(client_dev) + DEB_REVISION


def stage_tree(client_dev: Path, staging: Path) -> dict:
    """client/ 树 → staging/usr/share/gate-client/（清洗 + 排除）。

    排除三类：开发测试（client/tests）、解释器缓存、编辑器备份/临时
    文件（_EXCLUDE_NAMES）。排除只作用于 staging 副本，源树原件不动
    （不删、不移、不改——备份文件是用户的）。
    返回清洗计数（供日志与测试核对）。client_dev 只读。
    """
    src = client_dev / "client"
    if staging.exists():
        shutil.rmtree(staging)
    app_root = staging / "usr" / "share" / "gate-client"
    app_root.mkdir(parents=True)
    shutil.copytree(src, app_root / "client", ignore=_EXCLUDE_IGNORE)
    tests_dir = app_root / "client" / "tests"
    if tests_dir.exists():
        shutil.rmtree(tests_dir)  # 开发测试不进产品包

    scrubbed = 0
    for rel, pairs in _SCRUBS.items():
        path = app_root / rel
        text = path.read_text(encoding="utf-8")
        for old, new in pairs:
            count = text.count(old)
            if count != 1:
                raise SystemExit(
                    f"[打包] 清洗点漂移：{rel} 中 {old!r} 出现 {count} 次（应为 1），"
                    f"请先复核 client-dev 再更新清洗表")
            text = text.replace(old, new)
            scrubbed += count
        path.write_text(text, encoding="utf-8")

    # 语法兜底：清洗只动字符串字面量，仍逐文件编译校验；产物里不留 pyc。
    for py in sorted(app_root.rglob("*.py")):
        py_compile.compile(str(py), doraise=True)
    for cache in list(app_root.rglob("__pycache__")):
        shutil.rmtree(cache)

    # 全树复扫：作者 home 字样必须归零。
    hits = []
    for path in sorted(app_root.rglob("*")):
        if path.is_file() and b"/home/" in path.read_bytes():
            hits.append(str(path.relative_to(app_root)))
    if hits:
        raise SystemExit(f"[打包] 清洗后仍残留 /home/ 路径: {hits}")
    return {"scrubbed": scrubbed}


def write_launchers(staging: Path) -> None:
    """/usr/bin 包装脚本：$0 相对解析程序根，无绝对机器路径。"""
    for name, body in (("gate-client", _CLI_LAUNCHER),
                       ("gate-client-gui", _GUI_LAUNCHER)):
        path = staging / "usr" / "bin" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        os.chmod(path, 0o755)


def write_desktop(staging: Path) -> None:
    path = staging / "usr" / "share" / "applications" / "gate-client.desktop"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_DESKTOP, encoding="utf-8")
    os.chmod(path, 0o644)


def write_debian(staging: Path, client_dev: Path) -> None:
    """DEBIAN/：control + md5sums + 四个维护脚本。"""
    debian = staging / "DEBIAN"
    debian.mkdir(parents=True)
    version = read_version(client_dev)
    (debian / "control").write_text(
        _DEB_CONTROL.format(pkg=PKG_NAME, version=version,
                            revision=DEB_REVISION, arch=ARCH,
                            depends=DEPENDS, suggests=SUGGESTS,
                            maintainer=MAINTAINER,
                            user_data_dir=USER_DATA_DIR),
        encoding="utf-8")
    for name, body in _MAINTAINER_SCRIPTS.items():
        path = debian / name
        path.write_text(body, encoding="utf-8")
        os.chmod(path, 0o755)  # 维护脚本必须可执行


def write_md5sums(staging: Path) -> None:
    """DEBIAN/md5sums：数据文件（非 DEBIAN/）相对路径清单，dh_md5sums 等价。"""
    lines = []
    for path in sorted(staging.rglob("*")):
        if path.is_dir() or "DEBIAN" in path.parts:
            continue
        rel = path.relative_to(staging).as_posix()
        digest = hashlib.md5(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {rel}")
    (staging / "DEBIAN" / "md5sums").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def normalize_tree(root: Path, skip_scripts: bool = False) -> None:
    """staging 树 mtime/权限统一：目录 0755、普通文件 0644；幂等的前提。"""
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            os.chmod(path, 0o755)
        elif not skip_scripts and (path.parent.name == "bin" or
                                   "DEBIAN" in path.parts and
                                   path.name in _MAINTAINER_SCRIPTS):
            os.chmod(path, 0o755)
        else:
            os.chmod(path, 0o644)
        os.utime(path, (FIXED_EPOCH, FIXED_EPOCH))
    os.utime(root, (FIXED_EPOCH, FIXED_EPOCH))


def build_deb(staging: Path, out_dir: Path, client_dev: Path) -> Path:
    """dpkg-deb --build → dist/gate-client_<ver>-1_all.deb（不安装）。"""
    write_debian(staging, client_dev)
    write_md5sums(staging)
    normalize_tree(staging)
    out_dir.mkdir(parents=True, exist_ok=True)
    version = read_version(client_dev)
    deb = out_dir / f"{PKG_NAME}_{version}{DEB_REVISION}_{ARCH}.deb"
    if deb.exists():
        deb.unlink()
    env = dict(os.environ, SOURCE_DATE_EPOCH=str(FIXED_EPOCH))
    subprocess.run(
        ["dpkg-deb", "--build", "--root-owner-group", str(staging), str(deb)],
        check=True, env=env, capture_output=True, text=True)
    return deb


# ---------------------------------------------------------------------------
# 产物自检（六项，供 test_packaging.py 与审查方调用）

_SCRIPT_FORBIDDEN_TOKENS = (
    "/home/", "/root/", "$HOME", "~/.local", "~/.config",
    USER_DATA_DIR.lstrip("~"),          # .local/share/gate-client
    CONFIG_DATA_DIR.lstrip("~"),        # .config/gate-client
    "client.db", "settings.json", "sources.json",
)
_SCRIPT_ALLOWED_PATH_PREFIXES = ("/usr/share/gate-client", "/dev/null")
_PATH_RE = re.compile(r'(/[A-Za-z0-9_./-]+)')


def deb_selfcheck(deb: Path, client_dev: Path) -> dict:
    """六项自检（task13 deb 专版验收口径）：

    1 fields     dpkg-deb --info/-f 字段与设计一致（包名/版本/架构/依赖）
    2 entries    dpkg-deb --contents 入口齐全（CLI 与 GUI 入口均可定位）
    3 version    control 版本与 client/VERSION 一致（含数据文件本体核对）
    4 no_abs_paths  产物任何文件无作者 home 目录字样（/home/、/root/）
    5 no_subscription  无订阅内容/节点地址/密钥字样
    6 scripts_safe   DEBIAN 维护脚本不触碰用户数据目录
    """
    checks: dict[str, dict] = {}

    # 元数据
    fields = {}
    for field in ("Package", "Version", "Architecture", "Depends"):
        proc = subprocess.run(["dpkg-deb", "-f", str(deb), field],
                              capture_output=True, text=True, check=True)
        fields[field] = proc.stdout.strip()
    version = read_version(client_dev)
    fields_ok = (fields["Package"] == PKG_NAME
                 and fields["Version"] == version + DEB_REVISION
                 and fields["Architecture"] == ARCH
                 and "python3-yaml" in fields["Depends"])
    checks["fields"] = {
        "ok": fields_ok,
        "detail": f"Package={fields['Package']} Version={fields['Version']} "
                  f"Arch={fields['Architecture']} Depends={fields['Depends']!r}",
    }

    with tempfile.TemporaryDirectory(prefix=".selfcheck.") as td:
        datadir = Path(td) / "data"
        ctrldir = Path(td) / "control"
        subprocess.run(["dpkg-deb", "-x", str(deb), str(datadir)],
                       check=True, capture_output=True, text=True)
        subprocess.run(["dpkg-deb", "-e", str(deb), str(ctrldir)],
                       check=True, capture_output=True, text=True)

        app_root = datadir / "usr" / "share" / "gate-client"
        data_files = [p for p in sorted(datadir.rglob("*")) if p.is_file()]

        # 2) 入口齐全：CLI 与 GUI 入口模块 + 包装脚本 + VERSION + .desktop
        need = [
            "usr/bin/gate-client", "usr/bin/gate-client-gui",
            "usr/share/applications/gate-client.desktop",
            "usr/share/gate-client/client/VERSION",
            "usr/share/gate-client/client/ui/cli.py",
            "usr/share/gate-client/client/ui/app.py",
            "usr/share/gate-client/client/ui/__init__.py",
        ]
        missing = [n for n in need if not (datadir / n).is_file()]
        checks["entries"] = {
            "ok": not missing,
            "detail": (f"缺 {missing}" if missing else
                       f"{len(data_files)} 个数据文件，CLI/GUI 入口均在"),
        }

        # 3) 版本一致：control Version == client/VERSION（数据文件本体）
        pkg_ver = (app_root / "client" / "VERSION").read_text(
            encoding="utf-8").strip()
        ver_ok = pkg_ver == version and fields["Version"] == version + DEB_REVISION
        checks["version"] = {
            "ok": ver_ok,
            "detail": f"包内 client/VERSION={pkg_ver}，control Version="
                      f"{fields['Version']}，源树={version}",
        }

        # 4) 无绝对路径（作者 home 字样）：数据文件全扫
        bad = []
        for p in data_files:
            b = p.read_bytes()
            if b"/home/" in b or b"/root/" in b:
                bad.append(p.relative_to(datadir).as_posix())
        checks["no_abs_paths"] = {
            "ok": not bad,
            "detail": ("残留: " + ", ".join(sorted(bad)) if bad else
                       f"{len(data_files)} 个数据文件无 /home/、/root/ 字样"),
        }

        # 5) 无订阅内容/节点地址/密钥：结构（只许程序文件形态）+ 内容双查
        bad_shape = [p.relative_to(datadir).as_posix() for p in data_files
                     if p.suffix not in _ALLOWED_SUFFIXES]
        hits = []
        for p in data_files:
            text = p.read_text(encoding="utf-8", errors="replace")
            for m in _SCHEME_RE.finditer(text):
                hits.append(f"{p.name}: 链接字样 {m.group(0)[:48]!r}")
            for m in _IP_RE.finditer(text):
                if m.group(0) not in _IP_WHITELIST:
                    hits.append(f"{p.name}: 白名单外 IP {m.group(0)}")
            for marker in _SECRET_MARKERS:
                if marker in text:
                    hits.append(f"{p.name}: 密钥字样 {marker!r}")
        checks["no_subscription"] = {
            "ok": not bad_shape and not hits,
            "detail": (f"非法文件形态 {bad_shape}；" if bad_shape else "") +
                      ("；".join(hits[:5]) if hits else
                       "无链接载荷/白名单外 IP/密钥字样"),
        }

        # 6) DEBIAN 脚本不触碰用户数据：禁区 token + 路径白名单双查。
        # 注释（含 shebang）先剥掉——sh 本来就不执行注释，扫描执行面即可。
        def _strip_comments(text: str) -> str:
            return "\n".join(line.split("#", 1)[0] for line in text.splitlines())

        script_problems = []
        for name in _MAINTAINER_SCRIPTS:
            path = ctrldir / name
            if not path.is_file():
                script_problems.append(f"{name}: 缺脚本")
                continue
            if not os.access(path, os.X_OK) and not (
                    path.stat().st_mode & 0o111):
                script_problems.append(f"{name}: 不可执行")
            text = _strip_comments(path.read_text(encoding="utf-8"))
            # "$APP_ROOT/xxx" 先解析成绝对路径再核白名单（sh 运行时同理）
            text = text.replace("$APP_ROOT", APP_ROOT_DEB)
            for token in _SCRIPT_FORBIDDEN_TOKENS:
                if token in text:
                    script_problems.append(f"{name}: 禁区 token {token!r}")
            for m in _PATH_RE.finditer(text):
                if not m.group(1).startswith(_SCRIPT_ALLOWED_PATH_PREFIXES):
                    script_problems.append(
                        f"{name}: 越权路径 {m.group(1)!r}")
        checks["scripts_safe"] = {
            "ok": not script_problems,
            "detail": ("; ".join(script_problems[:5]) if script_problems else
                       f"{len(_MAINTAINER_SCRIPTS)} 个维护脚本无用户数据引用，"
                       "路径均在 " + "、".join(_SCRIPT_ALLOWED_PATH_PREFIXES) +
                       " 内"),
        }

    return {"passed": all(c["ok"] for c in checks.values()), "checks": checks}


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    here = Path(__file__).resolve().parent
    default_client_dev = os.environ.get(
        "GATE_CLIENT_DEV", "/home/hatch/workspace/vpn-project/client-dev")
    ap = argparse.ArgumentParser(description="gate-client deb 打包脚本原型（离线）")
    ap.add_argument("--client-dev", default=default_client_dev,
                    help="client-dev 树（只读；默认取环境变量 GATE_CLIENT_DEV）")
    ap.add_argument("--out", default=str(here.parent / "dist"),
                    help="输出目录（默认 work13/dist）")
    args = ap.parse_args(argv)

    client_dev = Path(args.client_dev).resolve()
    out_dir = Path(args.out).resolve()
    if not (client_dev / "client" / "VERSION").is_file():
        raise SystemExit(f"[打包] 找不到 client/VERSION：{client_dev}")

    version = read_version(client_dev)
    print(f"[打包] 版本（读自 client/VERSION）: {version} → deb 版本 {version}{DEB_REVISION}")

    staging = out_dir / "staging-deb"
    info = stage_tree(client_dev, staging)
    print(f"[打包] staging 完成: {staging}（清洗 {info['scrubbed']} 处本机路径）")
    write_launchers(staging)
    write_desktop(staging)
    deb = build_deb(staging, out_dir, client_dev)
    print(f"[打包] deb 产物: {deb}（{deb.stat().st_size} 字节, "
          f"sha256 {sha256_of(deb)[:16]}…）")

    report = deb_selfcheck(deb, client_dev)
    for name, check in report["checks"].items():
        flag = "PASS" if check["ok"] else "FAIL"
        print(f"[自检] {flag} {name}: {check['detail']}")
    if not report["passed"]:
        return 1
    print("[打包] 六项自检全过，产物闭环（dpkg-deb --build 不安装；"
          "dpkg -i 属红线，待隔离环境实测）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
