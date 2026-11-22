# gate-client deb 安装包设计（task13 · 阶段四·产品化 第六段，deb 专案版）

本文档面向审查方：不看原型代码也能明白**打包对象是什么、deb 包怎么摆、
control 与维护脚本怎么定、升级与迁移怎么定口径**，并能按 §10 复现手册
直接复现打包与全部自检。

- 路线已定（主人 2026-11-22 亲口定）：**安装包只走 deb**；zipapp 与
  PyInstaller 不做原型，只在 §7/§8 各留一小节存档「为何不选 + 复活
  条件」。
- 盘点对象：client-dev（本机快照，`client/VERSION` = **0.17.0**——本
  交付物首轮按 0.16.0 提交，集成时 client-dev 已升至 0.17.0；本文行号
  按 0.17.0 快照核对，v0.17.0 集成未动 §1 所引的任何落点）。
- 原型：`packaging/build_deb.py`（本文 §3–§5 的机械落地，标准库 only，
  不 import client 包，离线构建）+ `test_packaging.py`（25 用例）。
- 红线对齐：安装包只管装，不管自动更新、不管 sing-box 升级、不管节点
  优选/自动切换/定时拉新——**永不自动切换节点**（铁律，包内不含任何
  此类逻辑，client-dev 树本身就没有，打包不做增量）。
- 本机信息隔离：产物内无作者 home 目录字样、无订阅内容/节点地址/密钥、
  维护脚本零触碰用户数据（六项自检全过，§10.1）。

---

## 0. 盘点方法与实测环境

逐文件实测（grep + AST 全树扫描 + 构建实测），凡写进本文的结论都给出处
（行号或实测命令）；**测不到的如实标「未核实/未实测」，不编造**。

### 0.1 构建/验证 VM 环境（全部实测）

| 项 | 实测结果 | 实测命令 |
|---|---|---|
| 发行版 | Ubuntu 24.04.5 LTS (noble) | `cat /etc/os-release` |
| Python | 3.12.3 | `python3 --version` |
| PyYAML | 在场（系统包）6.0.1-2build2，noble/main 有源 | `python3 -c "import yaml"`；`apt-cache policy python3-yaml` |
| python3-venv | 在场 3.12.3-0ubuntu2.1，noble/universe 有源 | `apt-cache policy python3-venv` |
| PySide6 | **不在场**（整个文件系统无 PySide6 目录；apt 源 noble **无 python3-pyside6 包**） | `python3 -c "import PySide6"` → ModuleNotFoundError；`find / -maxdepth 6 -name PySide6` → 空；`apt-cache policy python3-pyside6` → 无候选；`apt-cache search pyside` → 只有 PySide2 系 |
| dpkg 工具 | dpkg-deb 1.22.6 / dpkg / ar 在场；fakeroot、lintian **不在场** | `which dpkg-deb dpkg ar fakeroot lintian` |
| PyInstaller | 不在场（§8 存档小节相关） | `python3 -c "import PyInstaller"` → ModuleNotFoundError |
| 网络构建限制 | 全程离线：不联网、不 pip install、不 apt install、不 sudo、**不 dpkg -i**、不写 /usr /etc | task13 红线；dpkg-deb --build 只产出 deb 文件、不安装；测试与安装模拟落点全在 work13/ 内临时目录 |

### 0.2 目标机口径

简报口径：TX-6PRO 为 Ubuntu。**该说法目前只有简报一个来源**（在
zcode-tasks 全语料里只有 task13.md 提到 TX-6PRO，无第二来源交叉核实），
本 VM 无法核实目标机的发行版版本——按「Ubuntu + python3 + dpkg 在场」
设定打包目标，实际版本号与解释器版本**待迁移时上机实测**（未解决项 §11）。

---

## 1. 打包对象盘点

### 1.1 入口与版本号来源

| 项 | 落点 | 说明 |
|---|---|---|
| CLI 入口 | `client/ui/cli.py:444` `main()`；`python -m client.ui.cli` | argparse 定义在 `cli.py:380-441`（`build_parser`），`prog="gate-client"`（`cli.py:381`） |
| CLI `--version` | **现在没有**：`cli.py:380-441` 全量核对，只有 `--db` + 子命令，无版本旗标 | 版本展示由**包侧包装脚本**补齐（§3.4），不改 client-dev |
| GUI 入口 | `client/ui/app.py:1430-1446` `main()`（Qt 在场分支）；无 Qt 降级分支 `app.py:1450-1452`：打印 INSTALL_HINT（`app.py:151-153`）退出码 2 | 「不假装能跑」口径已有现成实现，包侧直接复用 |
| GUI 桌面对齐 | `app.py:1437` `setApplicationName("gate-client")`，注释明言「WM_CLASS 对齐 .desktop 的 StartupWMClass」 | .desktop 必须写 `StartupWMClass=gate-client`（§3.5） |
| 版本号唯一来源 | `client/VERSION`（内容随版本走，当前 `0.17.0`）；`client/__init__.py` 为空、无版本常量；diag 的 `DIAG_VERSION`（`core/diag.py:29`）是诊断工具自身版本，**不是**客户端版本 | **版本号一律读 client/VERSION，不许手写进脚本**：构建期由 build_deb.py 读文件拼 control `Version: <ver>-1`；运行期由包装脚本 `--version` 读 `/usr/share/gate-client/client/VERSION`。测试把关：build_deb.py 里出现 client/VERSION 当前字面量即判红（动态探针，升级不破测试） |

### 1.2 第三方依赖复核（AST 全树扫描 + import 链核对）

用 AST 扫全树（`ast.walk` 所有 Import/ImportFrom，对照
`sys.stdlib_module_names`），非 tests 代码的第三方 import 全集——**没有
漏盘**：

| 依赖 | import 点 | 加载时机 | deb 口径 |
|---|---|---|---|
| PyYAML (`yaml`) | `sources/clash_import.py:35`（**顶层**）；`sources/subscription.py:85`（懒加载，在 `_try_clash_import` 内，`subscription.py:77-95`） | Clash/YAML 订阅导入**功能执行时**才触发（`format_import.py:147-186` `_get_clash_importer` 也是懒加载并缓存）；CLI/GUI 启动不碰 yaml（实测：`python3 -m client.ui.cli --help` exit 0） | **进 Depends：`python3-yaml`**（功能级依赖；noble/main 实测有源，VM 已装 6.0.1） |
| PySide6 | `ui/app.py:138-140`（try/except ImportError → `_QT_OK=False`，不装不炸）；`ui/app.py:181`（懒加载 QPainter） | GUI 启动时；CLI 全程不需要 | **不进 Depends**：noble apt 查无 `python3-pyside6`（§0.1 实测）→ **包名待本机核实，不编包名**；GUI 依赖走 venv + `GATE_CLIENT_GUI_PYTHON`（§3.4），Suggests 指路 `python3-venv` |
| vpngate | `engines/_vpngate_shim.py:27-49` 垫片 `install()`；`engines/openvpn_engine.py:29-34` **延迟加载**（connect 时才走）；`engines/_vpn_dial.py:46-53` import 失败仅在垫片未装时可达 | OpenVPN 拨号执行时；垫片把 `vpngate` 名字补齐（SSTP_USER/PASS 等），vendored 逻辑不变 | **零安装要求**：不进 Depends、包内已含垫片，依赖叙事如实写「无此依赖」 |

**结论：Depends 只写 `python3` + `python3-yaml`（§4）；PySide6 是 GUI 级
依赖，apt 落空，见 §4 逐字段依据。client-dev 没有 requirements.txt/
pyproject.toml/setup.py（实测根目录只有 ROADMAP.md、pkg-registry、
task*.md、work*/ 与 client/）——依赖清单即本表，补进 DESIGN 而不是发明
新文件。**

### 1.3 用户数据（必须留在包外）

| 数据 | 落点 | 出处 |
|---|---|---|
| 节点库 | `~/.local/share/gate-client/client.db`（`GATE_CLIENT_DB` 环境变量可覆盖） | `ui/cli.py:46-48` |
| 统一设置 | `~/.local/share/gate-client/settings.json` | `core/settings_proto.py:78`（定稿口径 `:67`） |
| 来源登记表 | 与库同目录 `sources.json` | `ui/cli.py:132-135`、`ui/app.py:1379-1382` |
| OpenVPN 导出目录 | 与库同目录 `ovpn_out/` | `ui/cli.py:270`、`ui/app.py:362` |
| GUI QSettings | `~/.config/gate-client/gui.conf`（org/app = "gate-client"/"gui"） | `ui/app.py:480,509,515,1171` |

全部在 `$HOME` 下。**包内容与包安装/升级/卸载/purge 动作不覆盖、不删除、
不做任何格式迁移（§5.2、§6.1）**；用户数据库、settings.json、sing-box
二进制一律不打进包里（task13 红线；六项自检确认包内只有程序文件形态，
§1.6）。

### 1.4 sing-box 外部二进制口径（不进 Depends，包外声明与检测）

代码现状已自带完整的运行时解析链（`engines/singbox_engine.py`）：

- 解析顺序 `:126-142` `find_binary()`：构造参数 → 环境变量
  `GATE_CLIENT_SINGBOX_BIN`（`:59`）→ `~/.local/bin/sing-box`（`:60`，
  存在才算）→ `shutil.which("sing-box")`（`:142`）；fork 同构
  `sing-box-lx`（`:61-62,800-811`）。
- **装后找不到内核时如何如实报错**：`_missing_binary_error`（`:145-153`）
  抛 EngineError，文案明示「sing-box v1.14.4（钉版），放到
  ~/.local/bin/sing-box」——CLI `connect` 与 GUI 连接按钮走同一
  SingBoxEngine 错误路径，**显式报错、不静默回退**。

不进 Depends 的依据：外部二进制、钉版口径（v1.14.4）、apt 源里的
sing-box 版本不可控——内核版本留给用户自管，包只管把客户端装好。

### 1.5 打包输入里的本机痕迹（打包期清洗）

全树 grep `/home/`（非 tests），恰有 3 处，全部是原型期 try/except
ImportError 的**回落默认值**：

| 处 | 落点 | 内容 |
|---|---|---|
| 1 | `sources/format_import.py:47` | `os.environ.get("GATE_CLIENT_DEV", "/home/hatch/workspace/vpn-project/client-dev")` |
| 2 | `sources/format_import.py:173` | `os.environ.get("GATE_TASK1_DIR", "/home/hatch/workspace/zcode-tasks/work1")` |
| 3 | `sources/clash_import.py:43` | 同 1 |

行为影响分析（清洗 = 把路径字面量换成 `os.devnull`，即 `/dev/null`）：

- 处 1、3：`from . import manual` / `from ..core.model import Node` 等包内
  相对导入在 deb 安装形态下**必然成功**，回落分支不可达；即使强行使之
  可达，`os.path.isdir("/dev/null")` = False 与「目标机上原作者路径不存在」
  时的行为逐字一致。
- 处 2：`_get_clash_importer` 的第 1 顺序（包内同级 `clash_import`）在包
  内必然成功；`os.path.isfile("/dev/null/clash_import.py")` = False，同样
  与「目标机无 work1 目录」一致。

清洗在 staging 副本上做（**client-dev 只读不动**，测试用例
test_staged_scrub_sites_neutralized_and_source_untouched 抽查源树原样保留），
清洗后 staging 全树复扫 `/home/` 必须归零 + 逐文件 py_compile 语法校验，
任一失败构建即报错。实测：清洗计数 3/3，全过。

### 1.6 打包输入的取舍

| 处理 | 对象 | 理由 |
|---|---|---|
| 不打进包 | `client/tests/**` | 开发测试不进产品包；非 tests 代码对 client.tests **零引用**（实测 grep）；client-dev 树仍是测试的家 |
| 不打进包 | `__pycache__/`、`*.pyc` | 解释器缓存，污染产物且含机器路径痕迹（字节码预编译改由 postinst 在装机时做，§5.1） |
| 不打进包 | **编辑器备份/临时文件**：`*.bak*`、`*~`、`*.orig`、`*.rej`、`*.swp`/`.*.swp`、`*.swx`/`.*.swx`、`*.tmp*`、`#*#`、`.DS_Store` | **审查实录（REVIEW_FAIL.md 2026-11-22）**：目标机树里出现 8 个 `.bak-20261121-*` 改动前快照，首轮形态防线把它们判非法、断掉构建——裁决选方案 2 硬化：staging 排除这类文件（**源树原件一律不删不移**，备份是用户的），打包器对树里出现备份/临时文件有预期内处理。文件形态自检保持原样，真正非法的数据文件仍被拦（回归用例双保险） |
| 不打进包 | 用户数据、settings.json、sing-box、订阅/feed 文件 | 红线；产物内只许 `.py`/`.md`/`.desktop` 与无后缀文件（包装脚本、client/VERSION）四种形态（六项自检的结构级防线） |
| 打进包 | `client/`（含 README.md、CHANGELOG.md、VERSION） | 产品本体；文档对用户有用，实测无本机路径 |

排除的边界安全性有回归用例把关：夹具树注入 12 个 decoy（`.bak-20261121-*`
同款形态 ×4、`~`、`.orig`、`.swp`、隐名 `.swp`、`.tmp`、`.rej`、
`VERSION.bak`、Emacs `#name#`）→ staging 零混入、真文件（清洗点/入口/
VERSION）零误伤、deb 照常打出且六项自检全过；另注入真正非法的
`nodes_export.json` → 仍被形态防线拦下。staging 与 client-dev client/
树 diff 实测仅 2 个清洗文件不同（排除表没碰任何真文件）。

---

## 2. python3 版本下限实测（Depends 依据）

简报要求「python3 版本下限按代码语法实测」。AST 级逐项扫描（非 tests
源码）：

| 特性（引入版本） | 实测方法 | 命中 |
|---|---|---|
| match/case（3.10） | `ast.Match` | **0** |
| 海象 `:=`（3.8） | `ast.NamedExpr` | **0** |
| 仅限位置参数 `/`（3.8） | `posonlyargs` | **0** |
| 内建泛型注解 `list[str]` 等（3.9 运行时） | 无 `__future__ annotations` 文件里的 `Subscript` | **0**（26 个文件都带 `from __future__ import annotations`，注解全部惰性） |
| `X \| Y` 注解（3.10 运行时） | 无 future 文件里的 `BinOp(BitOr)` 注解 | **0** |
| 自述 f-string `f"{x=}"`（3.8） | 源码正则 `{...=}` 形态 | **0** |
| 版本门槛 stdlib（zoneinfo 3.9 / tomllib 3.11 等） | import 名单比对 | **0** |

已知在用的最高门槛特性 = dataclasses + `from __future__ import
annotations`（均为 3.7 引入）→ **语法下限 = 3.7**。

运行实测：client-dev 自带测试套件在 VM 的 3.12.3 上
`python3 -m unittest discover -s client/tests -t .` → **752 tests OK
（skipped 47，均为无 PySide6 的 GUI 测试）**（0.17.0 快照复测）。

→ `Depends: python3 (>= 3.7)`：下限来自语法实测；3.12.3 是唯一运行实测
点（旧版运行未测，§11 未解决项如实标注）。

---

## 3. deb 包设计

### 3.1 包名、版本、架构

| 字段 | 值 | 依据 |
|---|---|---|
| Package | `gate-client` | 与 CLI `prog`（`cli.py:381`）、`setApplicationName`（`app.py:1437`）同源；deb 包名规范（小写、无空格）；`/usr/bin/gate-client` 命令名与包名一致 |
| Version | `<client/VERSION>-1`，当前 `0.17.0-1` | 上游版本 = `client/VERSION`（唯一来源，构建脚本读文件）；`-1` 是 Debian 修订号（打包元数据，与上游版本无关）；不手写版本号进脚本 |
| Architecture | `all` | 纯 Python + 标准库/PyYAML，无架构相关内容；sing-box 等二进制不进包（§1.4） |
| Section / Priority | `net` / `optional` | 常规归类 |
| Maintainer | `gate-client local build <local@invalid>` | 占位、非本机信息（红线：产物不含本机信息）；集成方上真名真邮箱时替换 |
| conffiles | **无** | 包内零配置文件——用户配置全在 `$HOME`（settings.json、QSettings），包不声明任何 $HOME 文件 → 升级/卸载无 conffile 询问 |

### 3.2 安装布局（`/usr/lib` vs `/usr/share` 取舍）

```
/usr/bin/gate-client                    CLI 包装脚本（可执行）
/usr/bin/gate-client-gui                GUI 包装脚本（可执行）
/usr/share/gate-client/client/…         程序本体（清洗后的 client/ 树，含 VERSION）
/usr/share/applications/gate-client.desktop
DEBIAN/control · DEBIAN/md5sums · DEBIAN/{preinst,postinst,prerm,postrm}
```

取舍依据：

- 程序本体放 **`/usr/share/gate-client/`**，不放 `/usr/lib/gate-client/`：
  Architecture: all、纯 Python 代码 = 架构无关可共享数据，FHS 口径
  /usr/share 正是「架构无关数据」；`/usr/lib/<pkg>` 传统上是架构相关库/
  插件位，arch: all 的包放那里名不正。
- 不放 `/usr/lib/python3/dist-packages/`：那是「向系统 python 注册可复用
  库」的位置（dh_python3/py3compile 生态）；本程序是**应用**不是库，不
  注册进系统 python 命名空间，也避免让 deb 构建依赖 debhelper 生态
  （VM 里没有）。
- 包装脚本放 `/usr/bin/`：PATH 内、deb 惯例；脚本内容以 `$0` 相对解析
  程序根（§3.4），无绝对机器路径。
- `.desktop` 放 `/usr/share/applications/`：桌面入口标准位。

### 3.3 程序根与防劫持口径

包装脚本把程序根解析为 `$0` 相对路径：装在 `/` 时 = `/usr/share/gate-client`；
被 `dpkg-deb -x` 解到任意 prefix 时同样成立 → **安装模拟免 chroot**，
这是本任务能在线验证 deb 内容的关键设计。

`-m` 方式运行会把**当前目录**加进 `sys.path[0]`，存在被同名 `client`
包劫持的风险 → 包装脚本统一导出 `PYTHONSAFEPATH=1`（Python 3.11+ 生效，
noble 3.12 在场即有效；旧解释器上该变量被忽略、行为同现状，差异如实
记录）。

### 3.4 入口命令设计（`/usr/bin/gate-client`、`/usr/bin/gate-client-gui`）

**CLI（`gate-client`）**：

```sh
#!/bin/sh
set -e
APP_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../share/gate-client" && pwd)
if [ "$#" -ge 1 ]; then
    case "$1" in
        --version|-V)
            printf 'gate-client %s\n' "$(cat "$APP_ROOT/client/VERSION")"
            exit 0 ;;
    esac
fi
PYTHON="${GATE_CLIENT_PYTHON:-python3}"
PYTHONPATH="$APP_ROOT${PYTHONPATH:+:$PYTHONPATH}"
PYTHONSAFEPATH=1
export PYTHONPATH PYTHONSAFEPATH
exec "$PYTHON" -m client.ui.cli "$@"
```

- `--version`/`-V` 在转发前拦截：client-dev 的 argparse 没有版本旗标
  （§1.1），红线又不许改 client-dev；版本读程序本体的 `client/VERSION`
  （唯一来源）。
- 其余参数原样转发；`--help` 行为与 `python -m client.ui.cli --help`
  完全一致（实测 exit 0）。
- `GATE_CLIENT_PYTHON` 可指认解释器（默认 `python3`）。

**GUI（`gate-client-gui`）**：同一程序根解析 + 解释器候选链——PySide6
的 Ubuntu 包名待本机核实（noble 实测无），PySide6 只能活在 pip venv
（TX-6PRO 现状 `~/gate-client/.venv-gui`，简报口径）：

1. `GATE_CLIENT_GUI_PYTHON` 环境变量（装了 PySide6 的解释器）；
2. `~/.venv-gui/bin/python3`；
3. `~/gate-client/.venv-gui/bin/python3`（TX-6PRO 现状路径）；
4. `python3` 兜底：无 PySide6 → client.ui.app 打印 INSTALL_HINT
   （`app.py:151-153`）退出码 2——**不假装能跑**。

候选探测 = `"$cand" -c "import PySide6"`；全落空时兜底前打一条 stderr
提示。实测（本 VM，无 PySide6）：`gate-client-gui` → 提示 + INSTALL_HINT
+ exit 2。

### 3.5 .desktop 桌面入口（图标未解决项口径）

```ini
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
```

- `StartupWMClass=gate-client`：对齐 `app.py:1437`（代码注释明言此对齐）。
- `Icon=` 有意空缺并留补齐口径：图标是全项目未解决项，不现编（红线）。

---

## 4. control 文件设计（逐字段给值与依据）

```ini
Package: gate-client
Version: 0.17.0-1
Section: net
Priority: optional
Architecture: all
Depends: python3 (>= 3.7), python3-yaml
Suggests: python3-venv
Maintainer: gate-client local build <local@invalid>
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
 用户数据（client.db、settings.json）在 ~/.local/share/gate-client，
 本包升级/卸载/purge 一律不碰。
```

逐字段依据：

| 字段 | 值 | 依据 |
|---|---|---|
| Depends: python3 (>= 3.7) | `>= 3.7` 是**语法实测下限**（§2：无任何 3.8+ 特性）；3.12.3 为唯一运行实测点 | 简报「版本下限按代码语法实测」 |
| Depends: python3-yaml | PyYAML 是 CLI 功能面依赖（订阅/Clash YAML 导入，`clash_import.py:35` 顶层 import）；noble/main 实测有源 6.0.1-2build2、VM 已装 | §1.2 |
| Suggests: python3-venv | GUI 依赖 PySide6 的落点是 pip venv（noble 无 python3-pyside6）；`python3-venv` noble/universe 实测在场 3.12.3-0ubuntu2.1 | §0.1、§3.4 |
| 不写 PySide6 进 Depends | noble apt 查无 `python3-pyside6`（`apt-cache policy` 无候选、`apt-cache search pyside` 只有 PySide2 系）→ **包名待本机核实，不许编包名**；PyQt6 系虽在源里但 app.py:138-140 写死 PySide6 绑定，不构成替代 | §0.1 |
| 不写 sing-box 进 Depends | 外部二进制、钉版 v1.14.4、apt 版本不可控；检测口径与报错行为见 §1.4 | 简报 + `singbox_engine.py:126-153` |
| Maintainer 占位 | `local@invalid` 非本机信息；集成时替换为真实维护者 | 红线 |
| Description | 如实写明 GUI 依赖现状、sing-box 口径、用户数据不碰口径 | 本文 §1.3/§1.4 |

---

## 5. maintainer scripts 设计

四个脚本全部随包生成（DEBIAN/ 下、可执行），原型即设计稿的机械落地；
**dpkg -i 属红线未跑，实装验证待隔离环境**（未解决项 §11）。全部脚本
的禁区（红线 + 六项自检 #6 把关）：不引用、不触碰
`~/.local/share/gate-client/` 与 `~/.config/gate-client/` 下的任何用户
数据（脚本执行面扫描：用户数据路径/文件名 token 一律不许出现，路径
白名单 = `/usr/share/gate-client` 与 `/dev/null`）。

### 5.1 各脚本职责

| 脚本 | 做什么 | 不做什么 |
|---|---|---|
| **preinst** | 刻意 no-op（`exit 0`） | 升级是 dpkg 原地文件替换，无前置迁移；无服务要停；不碰用户数据 |
| **postinst** | ① `python3 -m compileall -q /usr/share/gate-client/client`：装机时预编译字节码（程序根 root 所有，用户首启免编译、也不出现用户写不进缓存的权限问题）；② `update-desktop-database -q`（在场的机器才执行） | 都带 `command -v` 守卫与 `|| true`，失败不阻塞安装；不联网；不做任何自动更新动作；不碰用户数据 |
| **prerm** | 刻意 no-op | CLI/GUI 无常驻会话/守护要停（连接进程自管生命周期）；不碰用户数据 |
| **postrm** | 只清程序根内 dpkg 不认领的残留：`find /usr/share/gate-client -name __pycache__ -delete` + 空目录 `rmdir` | 不碰用户数据；不删 `~/.local/…`、`~/.config/…` 任何内容 |

### 5.2 purge 口径之争：默认 purge 也**不删**用户数据

**口径：无论 remove 还是 purge，用户数据一律不删。** 理由：

1. **dpkg 语义本来就删不到它**：purge 的定义是「删包自有的文件 + conffiles」；
   本包无 conffiles、不声明任何 `$HOME` 文件，用户数据不归包所有 →
   dpkg purge 物理上碰不到 `~/.local/share/gate-client/`。
2. **维护脚本不越权**：四个脚本对其零引用（六项自检 #6 的禁区 token +
   路径白名单机械把关，防手滑写 `rm -rf $HOME/...`）。
3. **产品语义**：节点库与设置是用户资产（来源登记、手工固定的节点、
   settings 偏好），卸载重装不应丢数据——与升级口径（§6.1）同源：
   「只许保留，不许删、不许覆盖、不许迁移格式」。

需要彻底清数据的用户，手动删 `~/.local/share/gate-client/` 与
`~/.config/gate-client/`（文档口径，包不代劳）。

---

## 6. 升级口径与手工安装迁移

### 6.1 旧版 deb 覆盖装新版

- 程序文件替换：dpkg 按 `Version` 比较（`0.17.0-1` → `0.18.0-1`…），
  新版数据 tarball 原地覆盖 `/usr/share/gate-client/` 与 `/usr/bin/`
  包自有文件；md5sums 随包更新；无 conffiles → 无 conffile 询问。
- **用户数据三不动（铁律）**：`~/.local/share/gate-client/` 全目录
  （client.db、settings.json、sources.json、ovpn_out/，§1.3）与
  `~/.config/gate-client/gui.conf`——升级、重装、卸载、purge 一律
  **不删、不覆盖、不做任何格式迁移**（settings.json 的版本兼容归
  task12 `settings_proto.py` 的 SETTINGS_FILE_VERSION 机制管，安装包
  不插手）。
- 降级：同一路径替换、同样不碰数据；是否允许降级属部署侧 dpkg 策略
  （`--refuse-downgrade` 等），安装包不管。

### 6.2 旧版手工安装（`~/gate-client/` 源码树 + `.venv-gui`）向 deb 迁移

现状（简报口径，**未上机实测**——TX-6PRO 不在本 VM，步骤按设计稿给出，
上机实测列入未解决项）：源码树 `~/gate-client/`、venv
`~/gate-client/.venv-gui`、两份 shell 启动脚本（`gate-client-gui.sh`、
`~/桌面/gate-client-图形界面.sh`）、CLI 手工 `python -m client.ui.cli`。

迁移步骤：

1. **数据原地不动**：`~/.local/share/gate-client/` 与
   `~/.config/gate-client/` 一个字节都不碰（§6.1；数据目录不变论证：
   默认落点写死在代码 `ui/cli.py:46-48`、`settings_proto.py:67/78`，
   deb 不改代码 → 默认数据目录与手工安装期完全一致，无缝接管）。
2. **装包**（用户机上的安装动作，不属于本 VM 红线范畴）：
   `sudo dpkg -i gate-client_0.17.0-1_all.deb`；缺依赖时
   `sudo apt-get install -f` 补齐（python3-yaml）。
3. **GUI 解释器**：`.venv-gui` **保留**——它不再服务旧源码树，而是成为
   启动器候选链第 2/3 项（`~/.venv-gui/bin/python3` 或
   `~/gate-client/.venv-gui/bin/python3`，§3.4），或经
   `GATE_CLIENT_GUI_PYTHON` 指认。
4. **旧启动脚本去留**：`gate-client-gui.sh` 与桌面 .sh **退役**，由
   `/usr/bin/gate-client-gui` + `/usr/share/applications/gate-client.desktop`
   接替；迁移验收（.desktop 能拉起、托盘在）后先归档备份再删，留一个
   版本周期的回滚窗口。
5. **旧源码树去留**：`~/gate-client/` 的 `client/` 部分**退役**（CLI
   全改走 `gate-client` 命令；`--db`/`GATE_CLIENT_DB` 口径不变）；树内
   `.venv-gui` **不随树退役**（第 3 步）。确认一个版本周期无误后归档/
   删除源码树。
6. **回滚方案**：`sudo dpkg -r gate-client` 只删包自有文件（§5.2 口径，
   用户数据零涉及）→ 恢复归档的旧启动脚本即可回到手工安装形态。

**未实测声明**：以上第 2-6 步是设计稿，TX-6PRO 上的实际 venv 结构、
桌面会话对 .desktop 的收录、发行版版本都未核实（§0.2），上机迁移时
逐条实测后把偏差写回本文。

---

## 7. 存档：zipapp 为何不选 + 复活条件

**为何不选**：按旧版三选一简报，zipapp 曾在本会话做成完整原型并实测
闭环（stdlib zipapp，185 KB pyz，四项自检全过，prefix 安装模拟全通，
构建字节级幂等）——**技术可行性没问题**，但在「包管理生态」维度全面
弱于 deb：无依赖声明与版本追踪（dpkg 数据库才有 `dpkg-query -W`、
`apt` 体系）、无系统级安装/升级/卸载语义、GUI 的 PySide6 依赖仍要
venv 自管（与 deb 现状相同，却少了 deb 的其余全部能力）、桌面集成要
手工拷 .desktop。2026-11-22 主人亲定只走 deb 后，zipapp 的相对优势
（免 root、便携）不被需要。**按路线更正，zipapp 原型产物已从 work13/
删除，不保留**；其打包期清洗逻辑（§1.5 清洗表）与自检思路已原样并入
deb 原型。

**复活条件**：① 目标机没有 root 权限、装不了 deb；② 需要免安装便携
形态（U 盘/绿色分发）；③ 非 Debian 系兼容需求出现。届时按本文 §1.5
清洗表 + §3.4 的 $0 相对程序根设计重建即可，技术路线已被本会话验证
可行。

---

## 8. 存档：PyInstaller 为何不选 + 复活条件

**为何不选**：VM 无 PyInstaller（`python3 -c "import PyInstaller"` →
ModuleNotFoundError，`which pyinstaller` 无）且离线红线禁 pip install →
**产物做不出，无法实测**：单文件/单目录形态、体积量级（PySide6 打包进
二进制，原始资料级估算数十 MB 起——未实测，不作决策依据）、构建机绝对
路径是否内嵌（与本任务「产物无绝对路径」红线相抵触的风险）、Qt 插件
收集是否完整——全部停在纸面。简报口径「每条路线实测到能下结论的那一
步为止」下，它连第一步都迈不出去，只能如实记录缺口：需要「支持目标机
Python 的 PyInstaller 版本」+ 联网环境 `pip install pyinstaller`。

**复活条件**：① 交付对象是没有 python3 环境的机器（离线分发、无解释器
安装权）；② 明确需要单文件免解释器形态。届时装上工具后按本文六项自检
当验收门（尤其「无绝对路径」一条要对 PyInstaller 产物实测），清洗逻辑
（§1.5）同样适用于其源输入。

---

## 9. 明确不管什么（边界）

- **不管自动更新**：使用界面「自动更新」段的事（订阅/规则集定期拉新，
  `core/usage_model.py:360` 归口「来源登记制 update_entry 定时包装 +
  规则集更新按钮」）。安装包只管一次装好；包体系内没有、也不许有任何
  自更新/自拉新逻辑。
- **不管 sing-box 内核升级**：内核钉版 v1.14.4、放哪、怎么换，都是用户
  自管（§1.4）；安装包不下载、不校验、不替换任何二进制。
- **不管节点优选、自动切换、定时拉新**：包内只有 client-dev 现有逻辑的
  原样打包，零行为增量；**永不自动切换节点**（铁律，本任务未新增任何
  选择/切换代码）。
- **不联网**：构建期零网络（无 pip、无 apt、无 fetch）；产物内的联网
  能力 = client-dev 原有的订阅拉取/验出口功能，归使用界面管，与安装包
  无关。

---

## 10. 复现手册（审查方视角）

### 10.1 一次构建 + 六项自检（约 3 秒，全程离线）

```sh
cd ~/workspace/zcode-tasks/work13
python3 packaging/build_deb.py      # 或: python3 packaging/build_deb.py --client-dev <路径> --out <目录>
```

期望输出（实测 0.17.0）：

```
[打包] 版本（读自 client/VERSION）: 0.17.0 → deb 版本 0.17.0-1
[打包] staging 完成: <out>/staging-deb（清洗 3 处本机路径）
[打包] deb 产物: <out>/gate-client_0.17.0-1_all.deb（134088 字节, sha256 e1634e9eb504fc4e…）
[自检] PASS fields: Package=gate-client Version=0.17.0-1 Arch=all Depends='python3 (>= 3.7), python3-yaml'
[自检] PASS entries: 39 个数据文件，CLI/GUI 入口均在
[自检] PASS version: 包内 client/VERSION=0.17.0，control Version=0.17.0-1，源树=0.17.0
[自检] PASS no_abs_paths: 39 个数据文件无 /home/、/root/ 字样
[自检] PASS no_subscription: 无链接载荷/白名单外 IP/密钥字样
[自检] PASS scripts_safe: 4 个维护脚本无用户数据引用，路径均在 /usr/share/gate-client、/dev/null 内
[打包] 六项自检全过，产物闭环（dpkg-deb --build 不安装；dpkg -i 属红线，待隔离环境实测）。
```

产物 sha256（实测，重跑不变——固定 mtime + SOURCE_DATE_EPOCH）：

```
e1634e9eb504fc4ef787dc85a93d1bc68aaa82cf0dcf5fbf377d55fdd3a00c5e  gate-client_0.17.0-1_all.deb
```

### 10.2 产物直查（不看代码也能验）

```sh
# control 字段
dpkg-deb -f dist/gate-client_0.17.0-1_all.deb Package Version Architecture Depends
# → gate-client  0.17.0-1  all  python3 (>= 3.7), python3-yaml

# 内容清单
dpkg-deb -c dist/gate-client_0.17.0-1_all.deb | awk '{print $1, $6}'
# → ./usr/bin/gate-client ./usr/bin/gate-client-gui ./usr/share/gate-client/client/… ./usr/share/applications/gate-client.desktop

# 安装模拟（dpkg-deb -x 解包，不 dpkg -i、不 sudo；示例放 /tmp，测试套件里落在 work13/ 内）
mkdir -p /tmp/demo-prefix && dpkg-deb -x dist/gate-client_0.17.0-1_all.deb /tmp/demo-prefix
/tmp/demo-prefix/usr/bin/gate-client --version   # → gate-client 0.17.0（读包内 client/VERSION）
/tmp/demo-prefix/usr/bin/gate-client --help      # → usage: gate-client …

# 无绝对路径独立直查
python3 - <<'EOF'
import subprocess, pathlib, tempfile
td = tempfile.mkdtemp()
subprocess.run(["dpkg-deb", "-x", "dist/gate-client_0.17.0-1_all.deb", td], check=True)
for p in pathlib.Path(td).rglob("*"):
    if p.is_file():
        assert b"/home/" not in p.read_bytes() and b"/root/" not in p.read_bytes(), p
print("no author-home strings in deb")
EOF

# 维护脚本（DEBIAN/，含四个可执行脚本 + md5sums）
dpkg-deb -e dist/gate-client_0.17.0-1_all.deb /tmp/ctrl && ls /tmp/ctrl
```

### 10.3 测试套件

```sh
cd ~/workspace/zcode-tasks/work13 && python3 -m unittest
```

实测结果：**Ran 29 tests … OK (skipped=1)**。唯一 skip = GUI offscreen
冒烟，skip 消息如实记录原因：「PySide6 不在本 VM（实测 import 报
ModuleNotFoundError，且 noble apt 源无 python3-pyside6、无 PySide6 系包）
……按简报要求如实 skip，不伪造通过」。PySide6 在场的机器上该用例自动
生效（QT_QPA_PLATFORM=offscreen 起包装脚本，8 秒内仍存活且 stderr 无
Traceback 即过）。

测试覆盖对齐简报交付物 3：control 字段断言（含与 DESIGN 逐字段对账）、
幂等（内容清单 + 字节级 sha256）、安装模拟（dpkg-deb -x → work13/ 内
prefix，CLI --help/--version 报 client/VERSION 当前值）、GUI 冒烟按在场/
缺席二分、维护脚本禁区审计（独立清单 + AST 级 sudo/dpkg -i 红线审计）、
**备份/临时文件排除回归**（REVIEW_FAIL 裁决方案 2：夹具树注入 12 个
decoy → staging 零混入、真文件零误伤、deb 照常打出六自检全过；另注入
非法数据文件 → 形态防线仍拦下，未被放宽）。

---

## 11. 未解决项（如实清单）

| # | 未解决项 | 现状 | 需要什么 |
|---|---|---|---|
| 1 | **图标缺失** | 仓库无任何图标文件（实测）；.desktop `Icon=` 空缺带补齐口径；GUI 托盘用运行时画点（app.py:178-187）不受影响 | 设计/挑选一张图标文件（红线：本任务不许现编图片）后补进 hicolor 路径并填 Icon= |
| 2 | **PySide6 的 Ubuntu 依赖包名待本机核实** | noble apt 源查无 python3-pyside6（实测）；PyQt6 系不构成替代（app.py:138-140 写死 PySide6）；TX-6PRO 发行版版本未核实 | 上 TX-6PRO 查实其 Ubuntu 版本与 apt 源有无 pyside6 包；查无则 GUI 维持 venv + GATE_CLIENT_GUI_PYTHON 口径 |
| 3 | **maintainer scripts 待本机实装验证** | 四脚本已随包生成并通过禁区审计（§5.1），但 dpkg -i 属红线未跑 → preinst/postinst/prerm/postrm 的真实执行未验证 | 隔离环境（chroot/容器/专用机）dpkg -i 全流程实测：升级、卸载、purge 三种路径 + postinst 字节码落地核对 |
| 4 | **手工安装迁移步骤待本机实测** | §6.2 是设计稿；TX-6PRO 的发行版版本、venv 布局、桌面会话行为均未核实（简报单一来源） | 上 TX-6PRO 逐条实测迁移步骤，偏差写回本文 |
| 5 | **GUI offscreen 冒烟未真跑** | PySide6 缺席，用例 skip 并记原因（非伪造通过） | 在有 PySide6 的环境跑 `python3 -m unittest`（用例自动生效） |
| 6 | **deb 真实安装语义未验证** | dpkg -i 被红线挡（不写系统目录），实测止步于 dpkg-deb --build/-f/-c/-x/-e；lintian、fakeroot 不在场 → 无 lint 门禁 | 同 #3 的隔离环境实测；补 lintian 后跑 lint 清零 |
| 7 | **python3 (>= 3.7) 下限的旧版运行未测** | 3.7 来自语法实测（§2）；运行实测仅 3.12.3（0.17.0 快照 752 测试全绿） | 若确需支持旧解释器，在最低目标版本上跑 client 测试套件回填 |
