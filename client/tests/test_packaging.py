#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""task13 deb 打包测试（假数据 only，不联网，不 sudo，不 dpkg -i，不写系统目录）。

集成版：由 work13/test_packaging.py 适配而来（集成时改动见末尾「集成适配」注）。
跑法：cd client-dev && python3 -m unittest client.tests.test_packaging。
路径一律按 client-dev 树解析：packaging/ 下找 build_deb.py 与 DESIGN.md；
临时构建/安装模拟目录落在 client-dev 根下（用完即删）；版本号一律读
client/VERSION 当前值，不写死。

覆盖口径（对齐磁盘最新 task13.md 交付物 3，deb 专案版）：
1. control 字段断言：包名/版本（读自 client/VERSION）/依赖清单与 DESIGN 一致；
2. 打包脚本幂等：连跑两次，.deb 产物 sha256 与内容清单一致；
3. 安装模拟：dpkg-deb -x 解到 client-dev/ 内临时 prefix，解出的树里
   CLI --help 能跑通、版本报 client/VERSION 当前值；
4. GUI 启动冒烟：QT_QPA_PLATFORM=offscreen，PySide6 在场才跑；本 VM
   实测无 PySide6 → skip 并在 skip 消息里记原因（不伪造通过）；
5. 六项自检在产物上复核（build_deb.deb_selfcheck + 独立复扫双保险）；
6. DEBIAN 维护脚本不触碰用户数据（禁区 token + 路径白名单）。
7. 硬化回归：staging 排除编辑器备份/临时文件（REVIEW_FAIL 裁决方案 2），
   形态防线仍拦真正非法的数据文件。

本文件不 import client 包（入口可定位性用子进程对解包树复核），
只 import packaging/build_deb.py（标准库 only，另有 AST 审计用例把关）。
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]  # client-dev 根
PACKAGING = ROOT / "packaging"
sys.path.insert(0, str(PACKAGING))
import build_deb  # noqa: E402  packaging/build_deb.py，标准库 only

CLIENT_DEV = Path(os.environ.get("GATE_CLIENT_DEV", ROOT)).resolve()
DESIGN = PACKAGING / "DESIGN.md"

# PySide6 在场与否：find_spec 只探不 import，不改测试进程状态。
_HAS_PYSIDE6 = importlib.util.find_spec("PySide6") is not None

# 维护脚本禁区（测试侧独立清单，与 build_deb.deb_selfcheck 互为印证）
_USER_DATA_TOKENS = ("/home/", "/root/", "$HOME", "~/.local", "~/.config",
                     ".local/share/gate-client", ".config/gate-client",
                     "client.db", "settings.json", "sources.json")
_ALLOWED_SCRIPT_PATHS = ("/usr/share/gate-client", "/dev/null")
_PATH_RE = re.compile(r'(/[A-Za-z0-9_./-]+)')


def _run_build(out_dir: Path) -> subprocess.CompletedProcess:
    out_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [sys.executable, str(PACKAGING / "build_deb.py"),
         "--client-dev", str(CLIENT_DEV), "--out", str(out_dir)],
        capture_output=True, text=True, timeout=300,
    )


def _deb_path(out_dir: Path) -> Path:
    version = build_deb.read_version(CLIENT_DEV)
    return out_dir / f"{build_deb.PKG_NAME}_{version}{build_deb.DEB_REVISION}_{build_deb.ARCH}.deb"


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True,
                          timeout=120, **kw)


def _strip_sh_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


# ---------------------------------------------------------------------------
# 共享构建：模块级一次，全部用例复用（产物落在 work13 内的临时目录）。

_BUILD_DIR: Path | None = None


def setUpModule():
    global _BUILD_DIR
    _BUILD_DIR = Path(tempfile.mkdtemp(prefix=".test-build.", dir=ROOT))
    proc = _run_build(_BUILD_DIR)
    if proc.returncode != 0:
        raise RuntimeError(f"构建失败:\n{proc.stdout}\n{proc.stderr}")


def tearDownModule():
    if _BUILD_DIR is not None:
        shutil.rmtree(_BUILD_DIR, ignore_errors=True)


class TestControlFields(unittest.TestCase):
    """control 字段断言：包名/版本读自 VERSION/依赖清单与 DESIGN 一致。"""

    @classmethod
    def setUpClass(cls):
        cls.deb = _deb_path(_BUILD_DIR)
        cls.fields = {}
        for field in ("Package", "Version", "Architecture",
                      "Depends", "Suggests", "Maintainer"):
            cls.fields[field] = _run(
                ["dpkg-deb", "-f", str(cls.deb), field]).stdout.strip()

    def test_package_and_architecture(self):
        self.assertEqual(self.fields["Package"], "gate-client")
        self.assertEqual(self.fields["Architecture"], "all")

    def test_version_read_from_client_version_not_handwritten(self):
        src_ver = (CLIENT_DEV / "client" / "VERSION").read_text(
            encoding="utf-8").strip()
        self.assertRegex(src_ver, r"\d+\.\d+\.\d+",
                         "client/VERSION 应为语义版本（当前值动态读取，不写死）")
        self.assertEqual(self.fields["Version"], f"{src_ver}-1")
        script = (PACKAGING / "build_deb.py").read_text(encoding="utf-8")
        self.assertNotIn(src_ver, script,
                         "build_deb.py 里出现手写版本号（必须读 client/VERSION）")

    def test_depends_matches_design_and_measurement(self):
        self.assertEqual(self.fields["Depends"], build_deb.DEPENDS)
        self.assertIn("python3-yaml", self.fields["Depends"])
        self.assertIn("python3", self.fields["Depends"])
        # 依赖只写实测到的：python3-yaml 在 noble/main 有源（apt-cache 实测），
        # python3-pyside6 在 noble 查无此包（不许编包名）→ Depends 不含 pyside。
        self.assertNotIn("pyside", self.fields["Depends"].lower())

    def test_design_md_records_the_same_control_values(self):
        design_text = DESIGN.read_text(encoding="utf-8")
        self.assertIn(f"Package: {build_deb.PKG_NAME}", design_text)
        self.assertIn(f"Architecture: {build_deb.ARCH}", design_text)
        self.assertIn(f"Depends: {build_deb.DEPENDS}", design_text)
        self.assertIn(f"Suggests: {build_deb.SUGGESTS}", design_text)

    def test_maintainer_is_non_identifying_placeholder(self):
        self.assertIn("local@invalid", self.fields["Maintainer"])


class TestArtifactSixChecks(unittest.TestCase):
    """六项自检（包名/版本/入口/无绝对路径/无订阅内容/脚本不碰用户数据）。"""

    @classmethod
    def setUpClass(cls):
        cls.deb = _deb_path(_BUILD_DIR)
        cls.report = build_deb.deb_selfcheck(cls.deb, CLIENT_DEV)

    def test_six_checks_all_pass(self):
        detail = {k: v["detail"] for k, v in self.report["checks"].items()}
        self.assertTrue(self.report["passed"], f"自检未过: {detail}")
        self.assertEqual(set(self.report["checks"]),
                         {"fields", "entries", "version", "no_abs_paths",
                          "no_subscription", "scripts_safe"})

    def test_no_author_home_paths_independent_scan(self):
        """独立复扫：-x/-e 解包后逐文件查 /home/、/root/（含 DEBIAN 脚本）。"""
        with tempfile.TemporaryDirectory(prefix=".sixcheck.", dir=ROOT) as td:
            _run(["dpkg-deb", "-x", str(self.deb), td])
            _run(["dpkg-deb", "-e", str(self.deb), str(Path(td) / "ctrl")])
            bad = [p.name for p in Path(td).rglob("*")
                   if p.is_file() and (b"/home/" in p.read_bytes()
                                       or b"/root/" in p.read_bytes())]
        self.assertEqual(bad, [], f"产物残留作者 home 字样: {bad}")

    def test_no_subscription_or_secret_strings_independent_scan(self):
        """独立复扫：链接载荷/白名单外 IP/密钥字样（不信 build 的实现）。"""
        scheme_re = re.compile(
            r"\b(?:vless|vmess|trojan|hysteria2?|tuic|ssr?|socks5?)://"
            r"[A-Za-z0-9+/_@:?=&.-]{12,}")
        ip_re = re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b")
        hits = []
        with tempfile.TemporaryDirectory(prefix=".subcheck.", dir=ROOT) as td:
            _run(["dpkg-deb", "-x", str(self.deb), td])
            for p in Path(td).rglob("*"):
                if not p.is_file():
                    continue
                text = p.read_text(encoding="utf-8", errors="replace")
                hits += [f"{p.name}: {m.group(0)[:40]}"
                         for m in scheme_re.finditer(text)]
                hits += [f"{p.name}: IP {m.group(0)}"
                         for m in ip_re.finditer(text)
                         if m.group(0) not in build_deb._IP_WHITELIST]
                hits += [f"{p.name}: {marker}"
                         for marker in build_deb._SECRET_MARKERS
                         if marker in text]
        self.assertEqual(hits, [], f"疑似订阅/密钥字样: {hits}")

    def test_staged_scrub_sites_neutralized_and_source_untouched(self):
        """打包期清洗落实（staging 3 处 os.devnull）；client-dev 原样未动。"""
        staging = _BUILD_DIR / "staging-deb" / "usr" / "share" / "gate-client"
        fi = (staging / "client" / "sources" / "format_import.py").read_text(
            encoding="utf-8")
        ci = (staging / "client" / "sources" / "clash_import.py").read_text(
            encoding="utf-8")
        self.assertNotIn("/home/", fi + ci)
        self.assertEqual(fi.count("os.devnull"), 2)
        self.assertEqual(ci.count("os.devnull"), 1)
        src_fi = (CLIENT_DEV / "client" / "sources" /
                  "format_import.py").read_text(encoding="utf-8")
        src_ci = (CLIENT_DEV / "client" / "sources" /
                  "clash_import.py").read_text(encoding="utf-8")
        self.assertIn("/home/hatch/workspace/vpn-project/client-dev", src_fi)
        self.assertIn("/home/hatch/workspace/zcode-tasks/work1", src_fi)
        self.assertIn("/home/hatch/workspace/vpn-project/client-dev", src_ci)


class TestStagingExcludesBackupTemp(unittest.TestCase):
    """硬化回归（REVIEW_FAIL.md 2026-11-22 裁决·方案 2）：
    staging 排除编辑器备份/临时文件，打包器对树里出现这类文件有预期内处理。

    夹具树 = client-dev/ 内临时目录拷贝 client-dev 的 client/ 树再注入 decoy；
    **绝不删除/移动/触碰真实树上的任何 .bak 文件**（裁决红线）。同时证明
    文件形态自检（no_subscription）没有被放宽——真正非法的数据文件照样
    被它拦下。
    """

    # 与审查实录同款的 .bak-20261121-* 形态 + 各类备份/临时 decoy
    DECOYS = {
        "client/core/history.py.bak-20261121-latfix": "# decoy\n",
        "client/core/store.py.bak-20261121-delete": "# decoy\n",
        "client/engines/singbox_engine.py.bak-20261121-lifecycle": "# decoy\n",
        "client/ui/app.py.bak-20261121-delete": "# decoy\n",
        "client/core/history.py~": "# decoy\n",
        "client/core/model.py.orig": "# decoy\n",
        "client/core/store.py.swp": "# decoy\n",
        "client/ui/.app.py.swp": "# decoy\n",
        "client/core/verify.py.tmp": "# decoy\n",
        "client/sources/manual.py.rej": "# decoy\n",
        "client/VERSION.bak": "0.0.0-decoy\n",
        "client/#notes.py#": "# decoy\n",
    }

    @staticmethod
    def _make_fixture(td: Path, extra: dict) -> Path:
        """拷 client-dev 的 client/ 树做底，注入 decoy（只写临时目录）。"""
        fixture_dev = Path(td) / "client-dev"
        shutil.copytree(CLIENT_DEV / "client", fixture_dev / "client",
                        ignore=shutil.ignore_patterns("__pycache__"))
        for rel, text in extra.items():
            path = fixture_dev / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        return fixture_dev

    def test_decoys_never_reach_staging_and_deb_still_builds(self):
        with tempfile.TemporaryDirectory(prefix=".bakfix.", dir=ROOT) as td:
            fixture_dev = self._make_fixture(td, self.DECOYS)
            staging = Path(td) / "staging"
            info = build_deb.stage_tree(fixture_dev, staging)
            self.assertEqual(info["scrubbed"], 3,
                             "夹具树 = 真实树拷贝，清洗点应同为 3 处")
            app_root = staging / "usr" / "share" / "gate-client"
            staged = {p.relative_to(app_root).as_posix()
                      for p in app_root.rglob("*")}
            for rel in self.DECOYS:
                self.assertNotIn(rel, staged, f"decoy {rel} 混进 staging")
            # 排除只删杂文件不误伤：真文件（清洗点/入口/VERSION）一个不少
            for keep in ("client/core/history.py", "client/core/model.py",
                         "client/ui/app.py", "client/ui/cli.py",
                         "client/VERSION"):
                self.assertIn(keep, staged, f"真文件 {keep} 被误排除")
            # deb 照常打出且六项自检全过（形态防线不再被 decoy 触发）
            build_deb.write_launchers(staging)
            build_deb.write_desktop(staging)
            deb = build_deb.build_deb(staging, Path(td) / "out", fixture_dev)
            self.assertTrue(deb.is_file())
            report = build_deb.deb_selfcheck(deb, fixture_dev)
            self.assertTrue(report["passed"],
                            f"decoy 清除后自检应全过: {report['checks']}")

    def test_file_shape_check_still_rejects_illegal_files(self):
        """形态防线不放水：非备份形态的真非法文件（数据文件）仍被拦下。"""
        with tempfile.TemporaryDirectory(prefix=".shapechk.", dir=ROOT) as td:
            fixture_dev = self._make_fixture(
                td, {"client/nodes_export.json": '{"fake": true}\n'})
            staging = Path(td) / "staging"
            build_deb.stage_tree(fixture_dev, staging)  # .json 不在排除表
            deb = build_deb.build_deb(staging, Path(td) / "out", fixture_dev)
            report = build_deb.deb_selfcheck(deb, fixture_dev)
            self.assertFalse(report["passed"],
                             "非法数据文件应被形态防线拦下")
            self.assertFalse(report["checks"]["no_subscription"]["ok"])
            self.assertIn("非法文件形态",
                          report["checks"]["no_subscription"]["detail"])


class TestContentsAndEntryLocatability(unittest.TestCase):
    """内容清单 + 入口可定位 + md5sums 完整性。"""

    @classmethod
    def setUpClass(cls):
        cls.deb = _deb_path(_BUILD_DIR)
        cls.listing = _run(["dpkg-deb", "-c", str(cls.deb)]).stdout

    def test_contents_complete(self):
        for expect in ("./usr/bin/gate-client", "./usr/bin/gate-client-gui",
                       "./usr/share/gate-client/client/VERSION",
                       "./usr/share/gate-client/client/ui/cli.py",
                       "./usr/share/gate-client/client/ui/app.py",
                       "./usr/share/gate-client/client/ui/__init__.py",
                       "./usr/share/applications/gate-client.desktop"):
            self.assertIn(expect, self.listing, f"deb 缺 {expect}")
        self.assertNotIn("./usr/share/gate-client/client/tests/",
                         self.listing, "开发测试不许进包")

    def test_debian_meta_complete(self):
        with tempfile.TemporaryDirectory(prefix=".meta.", dir=ROOT) as td:
            _run(["dpkg-deb", "-e", str(self.deb), td])
            names = {p.name for p in Path(td).iterdir()}
        self.assertTrue({"control", "md5sums"} <= names, f"DEBIAN 缺元文件: {names}")
        self.assertTrue(set(build_deb._MAINTAINER_SCRIPTS) <= names)

    def test_md5sums_match_extracted_files(self):
        with tempfile.TemporaryDirectory(prefix=".md5x.", dir=ROOT) as td:
            data = Path(td) / "data"
            ctrl = Path(td) / "ctrl"
            _run(["dpkg-deb", "-x", str(self.deb), str(data)])
            _run(["dpkg-deb", "-e", str(self.deb), str(ctrl)])
            for line in (ctrl / "md5sums").read_text(encoding="utf-8").splitlines():
                digest, rel = line.split(None, 1)
                target = data / rel.strip()
                self.assertTrue(target.is_file(), f"md5sums 指向缺失文件 {rel}")
                self.assertEqual(
                    hashlib.md5(target.read_bytes()).hexdigest(), digest,
                    f"md5 不一致: {rel}")

    def test_entry_modules_importable_from_extracted_tree(self):
        """CLI 与 GUI 入口可定位：子进程对解包树 import（测试进程不 import client）。"""
        with tempfile.TemporaryDirectory(prefix=".entry.", dir=ROOT) as td:
            _run(["dpkg-deb", "-x", str(self.deb), td])
            app_root = str(Path(td) / "usr" / "share" / "gate-client")
            env = dict(os.environ, PYTHONPATH=app_root)
            proc = _run([sys.executable, "-c", (
                "import client.ui.cli, client.ui.app; "
                "assert callable(client.ui.cli.main); "
                "assert callable(client.ui.app.main); "
                "print('entries-ok')")], env=env)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("entries-ok", proc.stdout)


class TestIdempotency(unittest.TestCase):
    """连跑两次构建：.deb 内容清单一致（实测还做到了字节级一致）。"""

    def test_two_builds_identical(self):
        with tempfile.TemporaryDirectory(prefix=".idem.", dir=ROOT) as td:
            out1, out2 = Path(td) / "run1", Path(td) / "run2"
            self.assertEqual(_run_build(out1).returncode, 0)
            self.assertEqual(_run_build(out2).returncode, 0)
            deb1, deb2 = _deb_path(out1), _deb_path(out2)
            l1 = _run(["dpkg-deb", "-c", str(deb1)]).stdout
            l2 = _run(["dpkg-deb", "-c", str(deb2)]).stdout
            self.assertEqual(l1, l2, "两次构建的内容清单不一致")
            h1 = hashlib.sha256(deb1.read_bytes()).hexdigest()
            h2 = hashlib.sha256(deb2.read_bytes()).hexdigest()
            self.assertEqual(
                h1, h2,
                "字节级不一致（确定性机制: 固定 mtime + "
                f"SOURCE_DATE_EPOCH={build_deb.FIXED_EPOCH}）")


class TestMaintainerScriptsSafe(unittest.TestCase):
    """DEBIAN 维护脚本不触碰用户数据（红线）。"""

    @classmethod
    def setUpClass(cls):
        cls.deb = _deb_path(_BUILD_DIR)
        cls.ctrl = None

    @classmethod
    def tearDownClass(cls):
        if cls.ctrl is not None:
            shutil.rmtree(cls.ctrl, ignore_errors=True)

    @classmethod
    def _extract_ctrl(cls):
        if cls.ctrl is None:
            cls.ctrl = Path(tempfile.mkdtemp(prefix=".ctrl.", dir=ROOT))
            proc = _run(["dpkg-deb", "-e", str(cls.deb), str(cls.ctrl)])
            assert proc.returncode == 0, proc.stderr

    def test_all_four_scripts_present_and_executable(self):
        self._extract_ctrl()
        for name in ("preinst", "postinst", "prerm", "postrm"):
            path = self.ctrl / name
            self.assertTrue(path.is_file(), f"缺维护脚本 {name}")
            self.assertTrue(path.stat().st_mode & 0o111,
                            f"{name} 不可执行（dpkg 要求可执行）")

    def test_scripts_never_reference_user_data(self):
        """禁区 token（剥注释后的执行面）：用户数据路径/文件名一律不许出现。"""
        self._extract_ctrl()
        for name in ("preinst", "postinst", "prerm", "postrm"):
            text = _strip_sh_comments(
                (self.ctrl / name).read_text(encoding="utf-8"))
            for token in _USER_DATA_TOKENS:
                self.assertNotIn(token, text,
                                 f"{name} 执行面引用禁区 token {token!r}")

    def test_scripts_only_touch_package_owned_paths(self):
        self._extract_ctrl()
        for name in ("postinst", "postrm"):  # 只有这两个含路径动作
            text = _strip_sh_comments(
                (self.ctrl / name).read_text(encoding="utf-8"))
            text = text.replace("$APP_ROOT", build_deb.APP_ROOT_DEB)
            for m in _PATH_RE.finditer(text):
                self.assertTrue(
                    m.group(1).startswith(_ALLOWED_SCRIPT_PATHS),
                    f"{name} 引用越权路径 {m.group(1)!r}")


class TestInstallSimulation(unittest.TestCase):
    """安装模拟：dpkg-deb -x 解到 work13/ 内临时 prefix（无 sudo、无 dpkg -i）。"""

    @classmethod
    def setUpClass(cls):
        cls.prefix = Path(tempfile.mkdtemp(prefix=".prefix.", dir=ROOT))
        proc = _run(["dpkg-deb", "-x", str(_deb_path(_BUILD_DIR)),
                     str(cls.prefix)])
        assert proc.returncode == 0, proc.stderr

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.prefix, ignore_errors=True)

    def test_prefix_lives_inside_client_dev(self):
        self.assertTrue(
            ROOT in self.prefix.parents or self.prefix.parent == ROOT,
            f"安装模拟 prefix 必须落在 client-dev/ 内: {self.prefix}")

    def test_extracted_tree_layout(self):
        for rel in ("usr/bin/gate-client", "usr/bin/gate-client-gui",
                    "usr/share/gate-client/client/VERSION",
                    "usr/share/applications/gate-client.desktop"):
            self.assertTrue((self.prefix / rel).is_file(), f"缺 {rel}")
        self.assertTrue(os.access(self.prefix / "usr/bin/gate-client", os.X_OK))

    def test_extracted_cli_help_runs(self):
        proc = _run([str(self.prefix / "usr/bin/gate-client"), "--help"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage:", proc.stdout)

    def test_extracted_cli_version_reports_current_version(self):
        """版本号不写死：读 client/VERSION 当前值断言（升级不破测试）。"""
        expected = (CLIENT_DEV / "client" / "VERSION").read_text(
            encoding="utf-8").strip()
        proc = _run([str(self.prefix / "usr/bin/gate-client"), "--version"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn(expected, proc.stdout)
        self.assertIn("gate-client", proc.stdout)

    def test_module_mode_also_runs(self):
        """不借助包装脚本、直接以树方式运行：PYTHONPATH=程序根 python3 -m client.ui.cli。"""
        env = dict(os.environ,
                   PYTHONPATH=str(self.prefix / "usr/share/gate-client"))
        proc = _run([sys.executable, "-m", "client.ui.cli", "--help"], env=env)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("usage:", proc.stdout)

    @unittest.skipIf(_HAS_PYSIDE6,
                     "PySide6 在场：GUI 走真冒烟（TestGuiSmoke），"
                     "本用例只测无 Qt 回退")
    def test_extracted_gui_fallback_without_qt(self):
        """无 PySide6 → 显式打印安装指引退出 2（app.py:151-153 口径，不假装能跑）。

        追记 2 修复（REVIEW_FAIL 追记 2 · 方案 A）：子进程 HOME 指向空
        临时目录并 unset GATE_CLIENT_GUI_PYTHON。启动器候选链第 1–3 项
        （GATE_CLIENT_GUI_PYTHON → $HOME/.venv-gui/bin/python3 →
        $HOME/gate-client/.venv-gui/bin/python3）在有 PySide6 venv 的
        机器上会命中第 2/3 项、拉起真 GUI 常驻（本机 120 秒超时根因）；
        HOME 隔离后三项全落空，候选链必落到第 4 项系统 python3 → 无
        PySide6 → 打印指引退出 2。不读、不写、不动真实 $HOME 下任何
        文件；跳过条件与本用例其余断言不变。
        """
        fake_home = Path(tempfile.mkdtemp(prefix=".fakehome.", dir=ROOT))
        self.addCleanup(shutil.rmtree, fake_home, ignore_errors=True)
        env = {k: v for k, v in os.environ.items()
               if k != "GATE_CLIENT_GUI_PYTHON"}
        env["HOME"] = str(fake_home)
        proc = _run([str(self.prefix / "usr/bin/gate-client-gui")], env=env)
        self.assertEqual(proc.returncode, 2, proc.stderr)
        self.assertIn("缺少 PySide6", proc.stderr)


class TestGuiSmoke(unittest.TestCase):
    """GUI 启动冒烟：QT_QPA_PLATFORM=offscreen；PySide6 不在场 → skip 记原因。"""

    @unittest.skipUnless(
        _HAS_PYSIDE6,
        "PySide6 不在本 VM（实测: python3 -c 'import PySide6' 报 ModuleNotFoundError，"
        "且 Ubuntu 24.04 (noble) apt 源无 python3-pyside6、无 PySide6 系包），"
        "offscreen 冒烟无法执行——按简报要求如实 skip，不伪造通过")
    def test_gui_offscreen_starts_event_loop(self):
        prefix = Path(tempfile.mkdtemp(prefix=".guismoke.", dir=ROOT))
        self.addCleanup(shutil.rmtree, prefix, ignore_errors=True)
        proc_x = _run(["dpkg-deb", "-x", str(_deb_path(_BUILD_DIR)), str(prefix)])
        self.assertEqual(proc_x.returncode, 0, proc_x.stderr)
        db = prefix / "smoke-gui.db"
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen",
                   GATE_CLIENT_GUI_PYTHON=sys.executable)
        proc = subprocess.Popen(
            [str(prefix / "usr/bin/gate-client-gui"), "--db", str(db)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
        try:
            proc.wait(timeout=8)
            self.fail(f"GUI 进程提前退出 rc={proc.returncode}: "
                      f"{proc.stderr.read().decode(errors='replace')[-800:]}")
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=10)
            stderr = proc.stderr.read().decode(errors="replace")
            self.assertNotIn("Traceback", stderr,
                             f"offscreen 冒烟期间出现异常:\n{stderr[-800:]}")


class TestBuildScriptHygiene(unittest.TestCase):
    """build_deb.py 自身口径：标准库 only、不 import client 包、无手写版本。"""

    def test_stdlib_only_and_no_client_import(self):
        tree = ast.parse(
            (PACKAGING / "build_deb.py").read_text(encoding="utf-8"))
        bad = []
        for node in ast.walk(tree):
            mods = []
            if isinstance(node, ast.Import):
                mods = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                mods = [node.module.split(".")[0]]
            for m in mods:
                if m not in sys.stdlib_module_names:
                    bad.append(f"line {node.lineno}: {m}")
        self.assertEqual(bad, [], f"build_deb.py 出现非标准库 import: {bad}")

    def test_no_hardcoded_version_in_build_script(self):
        """版本字面量零写死：以 client/VERSION 当前值为探针（升级不破）。"""
        src_ver = (CLIENT_DEV / "client" / "VERSION").read_text(
            encoding="utf-8").strip()
        text = (PACKAGING / "build_deb.py").read_text(encoding="utf-8")
        self.assertNotIn(src_ver, text, "build_deb.py 不许手写版本号")

    def test_no_dpkg_install_or_sudo_calls(self):
        """红线审计（AST 级）：打包脚本与测试自身的子进程类调用里不得出现
        sudo 或真实 dpkg -i。AST 不收注释；print 等非子进程调用里的文字
        不算执行。"""
        _EXEC_FUNCS = {"run", "Popen", "call", "check_call", "check_output",
                       "system", "execv", "execve", "spawn"}
        for path in (PACKAGING / "build_deb.py",
                     Path(__file__)):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                fname = getattr(func, "attr", None) or getattr(func, "id", None)
                if fname not in _EXEC_FUNCS:
                    continue
                consts = [a.value for a in node.args
                          if isinstance(a, ast.Constant)
                          and isinstance(a.value, str)]
                joined = " ".join(consts)
                self.assertFalse(
                    any(c.strip().startswith("sudo") for c in consts),
                    f"{path.name}:{node.lineno} 出现 sudo 调用")
                self.assertNotRegex(
                    joined, r"\bdpkg\s+-i\b",
                    f"{path.name}:{node.lineno} 出现真实 dpkg -i 调用")


if __name__ == "__main__":
    unittest.main()


# 集成适配（work13 原件 → client-dev 集成版的全部改动，审查方留痕）：
# 1. WORK13 → ROOT：本文件位于 client/tests/，ROOT = parents[2] 即 client-dev 根；
#    PACKAGING = ROOT/packaging，DESIGN = PACKAGING/DESIGN.md（DESIGN.md 随
#    build_deb.py 一并落位 client-dev/packaging/，测试需读它对账 control 值）。
# 2. 版本号去写死：三处写死 0.16.0 的断言已在 work13 原件内动态化（读
#    client/VERSION 当前值）；本集成版未再改版本断言。
# 3. test_prefix_lives_inside_work13 → test_prefix_lives_inside_client_dev：
#    安装模拟临时目录改落 client-dev 根内（用完即删，红线不变）；全部临时目录
#    （.test-build/.sixcheck/.subcheck/.bakfix/.shapechk 等）一律 dir=ROOT。
# 4. test_no_dpkg_install_or_sudo_calls 的自审路径由 WORK13/test_packaging.py
#    改为 Path(__file__)（即本文件）。
# 5. CLIENT_DEV 默认值由 ~/workspace/vpn-project/client-dev 改为 ROOT
#    （环境变量 GATE_CLIENT_DEV 仍可覆盖；VM 上两者同一路径，本机上 ROOT
#    即 ~/gate-client，构建与夹具树在本机也能实跑而不报错）。
# 6. 硬化回归类 TestStagingExcludesBackupTemp 原样落位（2 用例）：夹具树拷
#    CLIENT_DEV 的 client/ 树再注入 12 个 decoy，断言 staging 零混入且形态
#    防线仍拦真正非法文件。
# 7. 追记 2 修复（方案 A）原样落位：test_extracted_gui_fallback_without_qt
#    子进程 HOME 指空临时目录（dir=ROOT）并 unset GATE_CLIENT_GUI_PYTHON，
#    断言与跳过条件不变；其余 28 个用例与原件逐行一致。
# 其余 27 个用例与原件逐行一致；build_deb.py 与 DESIGN.md 与原件 md5 一致。
