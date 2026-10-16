#!/usr/bin/env python3
"""
vpn_dial.py 离线测试 —— 不需要网络、不需要安装 OpenVPN、不需要管理员权限。
用法: python test_vpn_dial.py        （全绿才算通过，规则 1）

「OpenVPN」用一个 Python 小脚本假扮：它按配置里的 `# MODE=xxx` 打印不同的日志，
所以 Popen / 读日志线程 / 超时 / 掐进程 这些都是真的在跑，不是纯 mock。
"""
import base64
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import vpngate as vp          # noqa: E402
import vpn_dial as vd         # noqa: E402


# ---------------------------------------------------------------------------
# 测试夹具
# ---------------------------------------------------------------------------
def wfile(path, data, binary=False):
    with open(path, "wb" if binary else "w", **({} if binary else {"encoding": "utf-8"})) as f:
        f.write(data)


def rfile(path, binary=False):
    with open(path, "rb" if binary else "r", **({} if binary else {"encoding": "utf-8"})) as f:
        return f.read()


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def make_cfg(mode="ok", proto="tcp", port=443, extra=""):
    return (
        f"# MODE={mode}\n"
        "dev tun\n"
        f"proto {proto}\n"
        f"remote 1.2.3.4 {port}\n"
        "cipher AES-128-CBC\n"
        "auth SHA1\n"
        "resolv-retry infinite\n"
        "nobind\n"
        "persist-key\n"
        "persist-tun\n"
        "client\n"
        "verb 3\n"
        "<ca>\n-----BEGIN CERTIFICATE-----\nMIIFAKEFAKEFAKE\n-----END CERTIFICATE-----\n</ca>\n"
        f"{extra}"
    )


CSV_HEADER = ("#HostName,IP,Score,Ping,Speed,CountryLong,CountryShort,NumVpnSessions,Uptime,"
              "TotalUsers,TotalTraffic,LogType,Operator,Message,OpenVPN_ConfigData_Base64")


def csv_line(host, score, country, cc, cfg_text, ping=20, speed=50000000, sessions=5, uptime=7200000):
    cfg = b64(cfg_text) if cfg_text else ""
    return f"{host},9.9.9.9,{score},{ping},{speed},{country},{cc},{sessions},{uptime},10,100,2weeks,op,msg,{cfg}"


def make_feed(lines):
    return "*vpn_servers\n" + CSV_HEADER + "\n" + "\n".join(lines) + "\n*\n"


FAKE_OPENVPN = r'''
import sys, time
def out(s):
    print(s, flush=True)
cfg = sys.argv[sys.argv.index("--config") + 1]
text = open(cfg, encoding="utf-8").read()
mode = text.split("MODE=")[1].split()[0]
# 验证 auth-user-pass 指向的凭证文件，能在 cwd 下按相对名读到（对应修复 F5）
for ln in text.splitlines():
    if ln.startswith("auth-user-pass "):
        try:
            open(ln.split(None, 1)[1].strip(), encoding="utf-8").read()
        except Exception as e:
            out("Options error: cannot open creds: %s" % e)
            sys.exit(1)
out("OpenVPN fake start, mode=" + mode)
if mode == "ok":
    out("Initialization Sequence Completed"); time.sleep(0.3); sys.exit(0)
elif mode == "never":
    time.sleep(60)
elif mode == "witherrors":
    out("Initialization Sequence Completed With Errors"); time.sleep(60)
elif mode == "drop":
    out("Initialization Sequence Completed"); time.sleep(0.2)
    out("Connection reset, restarting [0]"); time.sleep(60)
elif mode == "dropexit":
    out("Initialization Sequence Completed"); time.sleep(0.2); sys.exit(3)
elif mode == "authfail":
    out("AUTH: Received control message: AUTH_FAILED"); time.sleep(60)
elif mode == "tlsfail":
    out("TLS Error: TLS key negotiation failed to occur within 60 seconds (check your network connectivity)")
    time.sleep(60)
elif mode == "exit1":
    sys.exit(1)
elif mode == "noadapter":
    out("There are no TAP-Windows adapters on this system."); time.sleep(60)
elif mode == "noise":
    out("TLS Error: cannot locate HMAC in incoming packet from [AF_INET]5.5.5.5:443")
    out("Initialization Sequence Completed"); time.sleep(0.2); sys.exit(0)
'''


class TmpDirCase(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.tmp = self._td.name
        self.addCleanup(self._td.cleanup)
        self.fake = os.path.join(self.tmp, "fake_openvpn.py")
        with open(self.fake, "w", encoding="utf-8") as f:
            f.write(FAKE_OPENVPN)
        # 隔离全局黑/置顶名单，避免读到真实的 ban.txt / pin.txt 影响结果
        p1 = mock.patch.object(vp, "BAN_HOSTS", set())
        p2 = mock.patch.object(vp, "PIN_HOSTS", set())
        p1.start(); p2.start()
        self.addCleanup(p1.stop); self.addCleanup(p2.stop)

    def fake_cmd(self):
        return [sys.executable, "-u", self.fake]


# ---------------------------------------------------------------------------
# F1: feed 读取
# ---------------------------------------------------------------------------
class TestFeedParsing(TmpDirCase):
    def feed_text(self):
        return make_feed([
            csv_line("vpn100", 900, "Japan", "JP", make_cfg("ok")),
            csv_line("vpn200.opengw.net", 800, "Korea", "KR", make_cfg("ok", "udp", 1194)),
            csv_line("vpn300", 700, "Peru", "PE", ""),            # 没有配置的行
        ])

    def write_bytes(self, name, raw):
        p = os.path.join(self.tmp, name)
        wfile(p, raw, binary=True)
        return p

    def test_plain_csv(self):
        p = self.write_bytes("feed.csv", self.feed_text().encode("utf-8"))
        rows = vd.load_local_feed(p)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]["host"], "vpn100")

    def test_utf8_bom(self):
        p = self.write_bytes("feed.csv", b"\xef\xbb\xbf" + self.feed_text().encode("utf-8"))
        self.assertEqual(len(vd.load_local_feed(p)), 3)

    def test_utf16_from_powershell_redirect(self):
        p = self.write_bytes("feed.csv", self.feed_text().encode("utf-16"))   # 带 BOM
        self.assertEqual(len(vd.load_local_feed(p)), 3)

    def test_crlf_lines(self):
        p = self.write_bytes("feed.csv", self.feed_text().replace("\n", "\r\n").encode("utf-8"))
        self.assertEqual(len(vd.load_local_feed(p)), 3)

    def test_mirror_json(self):
        data = [{"servers": [{"hostname": "vpn900", "ip": "8.8.8.8", "score": 5, "ping": 10, "speed": 1000000,
                              "countrylong": "Japan", "countryshort": "jp", "numvpnsessions": 3,
                              "uptime": 100, "openvpn_configdata_base64": b64(make_cfg())}]}]
        p = self.write_bytes("feed.json", json.dumps(data).encode("utf-8"))
        rows = vd.load_local_feed(p)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["country_short"], "JP")

    def test_garbage_raises(self):
        p = self.write_bytes("feed.csv", b"<html>Access denied</html>")
        with self.assertRaises(RuntimeError):
            vd.load_local_feed(p)

    def test_bad_json_raises(self):
        p = self.write_bytes("feed.csv", b"{not json")
        with self.assertRaises(RuntimeError):
            vd.load_local_feed(p)

    def test_rows_to_feed_configs(self):
        p = self.write_bytes("feed.csv", self.feed_text().encode("utf-8"))
        cfgs = vd.rows_to_feed_configs(vd.load_local_feed(p))
        self.assertEqual(set(cfgs), {"vpn100.opengw.net", "vpn200.opengw.net"})   # 补后缀 + 去掉无配置行


class FakeResp:
    def __init__(self, content=b"", status=200):
        self.content, self.status = content, status

    def raise_for_status(self):
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


class FakeSession:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def get(self, url, timeout=None):
        self.calls.append(url)
        r = self.mapping[url]
        if isinstance(r, Exception):
            raise r
        return r


class TestOnlineFeed(TmpDirCase):
    def good(self):
        return make_feed([csv_line("vpn1", 1, "Japan", "JP", make_cfg())]).encode("utf-8")

    def test_api_success_saves_raw(self):
        save = os.path.join(self.tmp, "sub", "feed.csv")
        s = FakeSession({vp.VPNGATE_API: FakeResp(self.good())})
        rows, src = vd.fetch_online_feed(save, session=s)
        self.assertEqual(src, "官方 API")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rfile(save, binary=True), self.good())
        self.assertFalse(os.path.exists(save + ".tmp"))

    def test_fallback_to_mirror(self):
        save = os.path.join(self.tmp, "feed.csv")
        mirror = json.dumps([{"hostname": "m1", "ip": "1.1.1.1", "openvpn_configdata_base64": b64(make_cfg())}]).encode()
        s = FakeSession({vp.VPNGATE_API: ConnectionError("blocked"), vp.VPNGATE_MIRROR: FakeResp(mirror)})
        rows, src = vd.fetch_online_feed(save, session=s)
        self.assertEqual(src, "镜像")
        self.assertEqual(len(rows), 1)

    def test_both_fail_keeps_old_file(self):
        save = os.path.join(self.tmp, "feed.csv")
        wfile(save, b"OLD", binary=True)
        s = FakeSession({vp.VPNGATE_API: ConnectionError("blocked"), vp.VPNGATE_MIRROR: FakeResp(b"", 500)})
        with self.assertRaises(RuntimeError):
            vd.fetch_online_feed(save, session=s)
        self.assertEqual(rfile(save, binary=True), b"OLD")       # 旧 feed 没被弄坏

    def test_html_error_page_not_saved(self):
        save = os.path.join(self.tmp, "feed.csv")
        s = FakeSession({vp.VPNGATE_API: FakeResp(b"<html>blocked</html>"), vp.VPNGATE_MIRROR: FakeResp(b"[]")})
        with self.assertRaises(RuntimeError):
            vd.fetch_online_feed(save, session=s)
        self.assertFalse(os.path.exists(save))


class TestResolveFeed(TmpDirCase):
    def good_text(self):
        return make_feed([csv_line("vpn1", 1, "Japan", "JP", make_cfg())])

    def test_explicit_file_ok(self):
        p = os.path.join(self.tmp, "my.csv")
        wfile(p, self.good_text())
        cfgs, desc = vd.resolve_feed(p, refresh=False)
        self.assertIn("vpn1.opengw.net", cfgs)
        self.assertIn("本地文件", desc)

    def test_explicit_missing_dies_without_going_online(self):
        with mock.patch.object(vd, "fetch_online_feed") as fo, self.assertRaises(SystemExit):
            vd.resolve_feed(os.path.join(self.tmp, "nope.csv"), refresh=False)
        fo.assert_not_called()

    def test_explicit_unparsable_dies(self):
        p = os.path.join(self.tmp, "bad.csv")
        wfile(p, "<html>no</html>")
        with self.assertRaises(SystemExit):
            vd.resolve_feed(p, refresh=False)

    def test_default_path_used_when_present(self):
        wfile(os.path.join(self.tmp, "feed.csv"), self.good_text())
        with mock.patch.object(vp, "REPO_DIR", self.tmp), mock.patch.object(vd, "fetch_online_feed") as fo:
            cfgs, _ = vd.resolve_feed("", refresh=False)
        fo.assert_not_called()                 # 本地为主：有本地文件就不碰网络
        self.assertEqual(len(cfgs), 1)

    def test_default_missing_goes_online(self):
        row = vd.parse_feed_text(self.good_text())
        with mock.patch.object(vp, "REPO_DIR", self.tmp), \
                mock.patch.object(vd, "fetch_online_feed", return_value=(row, "官方 API")) as fo:
            cfgs, desc = vd.resolve_feed("", refresh=False)
        fo.assert_called_once()
        self.assertIn("在线", desc)

    def test_default_missing_and_online_fails_dies(self):
        with mock.patch.object(vp, "REPO_DIR", self.tmp), \
                mock.patch.object(vd, "fetch_online_feed", side_effect=RuntimeError("blocked")), \
                self.assertRaises(SystemExit):
            vd.resolve_feed("", refresh=False)

    def test_refresh_failure_falls_back_to_local(self):
        p = os.path.join(self.tmp, "feed.csv")
        wfile(p, self.good_text())
        with mock.patch.object(vd, "fetch_online_feed", side_effect=RuntimeError("blocked")):
            cfgs, desc = vd.resolve_feed(p, refresh=True)
        self.assertIn("本地文件", desc)

    def test_stale_feed_warns(self):
        p = os.path.join(self.tmp, "feed.csv")
        wfile(p, self.good_text())
        old = time.time() - 3 * 24 * 3600
        os.utime(p, (old, old))
        logs = []
        with mock.patch.object(vd, "log", lambda m="": logs.append(m)):
            vd.resolve_feed(p, refresh=False)
        self.assertTrue(any("过期" in m for m in logs))

    def test_feed_without_any_config_dies(self):
        p = os.path.join(self.tmp, "feed.csv")
        wfile(p, make_feed([csv_line("v1", 1, "Japan", "JP", "")]))
        with self.assertRaises(SystemExit):
            vd.resolve_feed(p, refresh=False)


# ---------------------------------------------------------------------------
# F5/F6: .ovpn 生成
# ---------------------------------------------------------------------------
class TestOvpnGeneration(TmpDirCase):
    DANGER = (
        "script-security 2\n"
        'up "cmd /c calc.exe"\n'
        "down evil.bat\n"
        "route-up x.bat\n"
        "tls-verify bad.exe\n"
        "plugin foo.dll\n"
        "management 127.0.0.1 7505\n"
        "management-client\n"
        "log C:/Windows/important.log\n"
        "writepid C:/x.pid\n"
        "config other.ovpn\n"
        "auth-user-pass-verify v.bat via-env\n"
    )

    def test_dangerous_directives_stripped(self):
        text, removed = vd.sanitize_ovpn(make_cfg(extra=self.DANGER), "vpngate_creds.txt")
        low = [ln.strip().lower() for ln in text.splitlines()]
        for bad in ("up ", "down ", "route-up", "tls-verify", "plugin", "management", "log ", "writepid",
                    "config ", "auth-user-pass-verify"):
            self.assertFalse(any(ln.startswith(bad) for ln in low), f"{bad!r} 没被剥掉")
        self.assertEqual(len(removed), 12)
        # 只能剩我们自己加的那一条 script-security 1
        self.assertEqual([ln for ln in low if ln.startswith("script-security")], ["script-security 1"])

    def test_normal_directives_and_pem_kept(self):
        text, _ = vd.sanitize_ovpn(make_cfg(port=995), "vpngate_creds.txt")
        for keep in ("dev tun", "proto tcp", "remote 1.2.3.4 995", "persist-tun", "client",
                     "<ca>", "MIIFAKEFAKEFAKE", "</ca>"):
            self.assertIn(keep, text)

    def test_comments_not_treated_as_directives(self):
        text, removed = vd.sanitize_ovpn("# up is just a word\n;log nothing\nclient\n", "c.txt")
        self.assertEqual(removed, [])
        self.assertIn("# up is just a word", text)

    def test_inline_and_bare_auth_user_pass_replaced(self):
        cfg = make_cfg(extra="<auth-user-pass>\nsomeone\nsecret\n</auth-user-pass>\nauth-user-pass\n")
        text, _ = vd.sanitize_ovpn(cfg, "vpngate_creds.txt")
        self.assertNotIn("someone", text)
        self.assertNotIn("secret", text)
        self.assertEqual(text.count("auth-user-pass"), 1)
        self.assertTrue(text.rstrip().endswith("auth-user-pass vpngate_creds.txt"))

    def test_orphan_open_tag_without_close(self):
        text, _ = vd.sanitize_ovpn(make_cfg(extra="<auth-user-pass>\nclient\n"), "c.txt")
        self.assertEqual(text.count("auth-user-pass"), 1)

    def test_write_files_in_chinese_and_space_dir(self):
        out = os.path.join(self.tmp, "稀奇 玩意", "ovpn_out")
        node = {"host": "vpn123.opengw.net", "port": 443}
        ovpn, creds = vd.write_node_files(node, b64(make_cfg(extra=self.DANGER)), out)
        text = rfile(ovpn)
        self.assertNotIn("\\", text)                                    # 没有反斜杠
        self.assertTrue(all(ord(c) < 128 for c in text), "配置里不应出现非 ASCII 字符/中文路径")
        self.assertIn("auth-user-pass vpngate_creds.txt", text)         # 相对文件名
        self.assertEqual(rfile(creds), f"{vp.SSTP_USER}\n{vp.SSTP_PASS}\n")
        self.assertEqual(os.path.basename(ovpn), "node_vpn123.opengw.net_443.ovpn")

    def test_corrupt_config_raises_runtime_error(self):
        for bad in ("!!!not base64!!!" * 3 + "=", b64("hello world, this is not an ovpn"), ""):
            with self.assertRaises(RuntimeError, msg=bad[:20]):
                vd.write_node_files({"host": "h.opengw.net", "port": 1}, bad, self.tmp)
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith(".ovpn")], [])   # 没留下垃圾文件

    def test_build_cmd_only_relative_name(self):
        cmd = vd.build_cmd(r"C:\Program Files\OpenVPN\bin\openvpn.exe", "node_x_443.ovpn")
        self.assertEqual(cmd[1:], ["--config", "node_x_443.ovpn"])
        cmd2 = vd.build_cmd(["py", "-u", "fake.py"], "a.ovpn")
        self.assertEqual(cmd2, ["py", "-u", "fake.py", "--config", "a.ovpn"])


# ---------------------------------------------------------------------------
# 候选挑选
# ---------------------------------------------------------------------------
class TestCandidates(TmpDirCase):
    def feed(self):
        rows = vd.parse_feed_text(make_feed([
            csv_line("vpnA", 100, "Japan", "JP", make_cfg(port=443)),
            csv_line("vpnB", 500, "Korea", "KR", make_cfg(port=995)),
            csv_line("vpnC", 300, "Peru", "PE", make_cfg("ok", "udp", 1194)),
        ]))
        return vd.rows_to_feed_configs(rows)

    def metrics(self):
        return {
            "vpnA.opengw.net": {"host": "vpnA.opengw.net", "port": 443, "support_ready": True, "composite_score": 300},
            "vpnB.opengw.net": {"host": "vpnB.opengw.net", "port": 995, "support_ready": True, "composite_score": 100},
            "vpnC.opengw.net": {"host": "vpnC.opengw.net", "port": 1194, "support_ready": False, "composite_score": 50},
            "vpnZ.opengw.net": {"host": "vpnZ.opengw.net", "port": 443, "support_ready": True, "composite_score": 1},  # feed 里没有
        }

    def hosts(self, cands):
        return [n["host"] for n, _ in cands]

    def test_scene_filter_and_sort(self):
        c = vd.build_candidates("support", self.metrics(), self.feed(), 8)
        self.assertEqual(self.hosts(c), ["vpnB.opengw.net", "vpnA.opengw.net"])   # C 不达标，Z 不在 feed

    def test_scene_all(self):
        c = vd.build_candidates("all", self.metrics(), self.feed(), 8)
        self.assertEqual(self.hosts(c), ["vpnC.opengw.net", "vpnB.opengw.net", "vpnA.opengw.net"])

    def test_ban_removes_and_pin_goes_first(self):
        with mock.patch.object(vp, "BAN_HOSTS", {"vpnb.opengw.net"}), \
                mock.patch.object(vp, "PIN_HOSTS", {"vpna.opengw.net:443"}):
            c = vd.build_candidates("all", self.metrics(), self.feed(), 8)
        self.assertEqual(self.hosts(c), ["vpnA.opengw.net", "vpnC.opengw.net"])

    def test_limit(self):
        self.assertEqual(len(vd.build_candidates("all", self.metrics(), self.feed(), 1)), 1)

    def test_fallback_by_feed_score_with_proto_port(self):
        c = vd.build_candidates("support", {}, self.feed(), 8)
        self.assertEqual(self.hosts(c), ["vpnB.opengw.net", "vpnC.opengw.net", "vpnA.opengw.net"])
        by = {n["host"]: n for n, _ in c}
        self.assertEqual((by["vpnB.opengw.net"]["proto"], by["vpnB.opengw.net"]["port"]), ("tcp", 995))
        self.assertEqual((by["vpnC.opengw.net"]["proto"], by["vpnC.opengw.net"]["port"]), ("udp", 1194))
        self.assertEqual(by["vpnA.opengw.net"]["uptime_hours"], 2.0)

    def test_fallback_respects_ban(self):
        with mock.patch.object(vp, "BAN_HOSTS", {"vpnb.opengw.net"}):
            c = vd.build_candidates("support", {}, self.feed(), 8)
        self.assertNotIn("vpnB.opengw.net", self.hosts(c))

    def test_load_metrics(self):
        p = os.path.join(self.tmp, "data.json")
        wfile(p, json.dumps({"available": [{"host": "x.opengw.net", "port": 1}, {"nohost": 1}]}))
        self.assertEqual(list(vd.load_metrics(p)), ["x.opengw.net"])
        self.assertEqual(vd.load_metrics(os.path.join(self.tmp, "missing.json")), {})


# ---------------------------------------------------------------------------
# F2/F4: 日志判定
# ---------------------------------------------------------------------------
class TestClassify(unittest.TestCase):
    def c(self, line, connected=False):
        return vd.classify_line(line.lower(), connected)

    def test_connected(self):
        self.assertEqual(self.c("Wed Oct 14 10:00:00 2026 Initialization Sequence Completed"), "connected")

    def test_completed_with_errors_is_env_not_connected(self):
        self.assertEqual(self.c("Initialization Sequence Completed With Errors"), "env")

    def test_auth_failed(self):
        line = "AUTH: Received control message: AUTH_FAILED"
        self.assertEqual(self.c(line, False), "failed")
        self.assertEqual(self.c(line, True), "dropped")

    def test_restart_lines(self):
        self.assertEqual(self.c("Connection reset, restarting [0]", True), "dropped")
        self.assertEqual(self.c("SIGUSR1[soft,tls-error] received, process restarting", False), "failed")
        self.assertEqual(self.c("[x] Inactivity timeout (--ping-restart), restarting", True), "dropped")

    def test_tls_negotiation_failed(self):
        self.assertEqual(self.c("TLS Error: TLS key negotiation failed to occur within 60 seconds", False), "failed")

    def test_unrelated_tls_noise_ignored(self):
        self.assertIsNone(self.c("TLS Error: cannot locate HMAC in incoming packet from [AF_INET]1.2.3.4:443"))

    def test_env_adapter_errors(self):
        self.assertEqual(self.c("There are no TAP-Windows adapters on this system."), "env")
        self.assertEqual(self.c("All TAP-Windows adapters on this system are currently in use or disabled."), "env")

    def test_normal_lines(self):
        for line in ("OpenVPN 2.6.8 x86_64-w64-mingw32", "TCP connection established with [AF_INET]1.2.3.4:443",
                     "PUSH: Received control message: 'PUSH_REPLY,route-gateway 10.211.0.1'"):
            self.assertIsNone(self.c(line))

    def test_decode_line_never_raises(self):
        self.assertEqual(vd.decode_line("拒绝访问".encode("utf-8")), "拒绝访问")
        self.assertIsInstance(vd.decode_line("拒绝访问".encode("gbk")), str)
        self.assertIsInstance(vd.decode_line(b"\xff\xfe\x00"), str)

    def test_is_admin_returns_bool_or_none(self):
        self.assertIn(vd.is_admin(), (True, False, None))


# ---------------------------------------------------------------------------
# F2/F3/F4: 真实子进程下的监督逻辑
# ---------------------------------------------------------------------------
class TestSupervise(TmpDirCase):
    def run_mode(self, mode, timeout=3.0):
        d = os.path.join(self.tmp, "run_" + mode)
        os.makedirs(d, exist_ok=True)
        node = {"host": f"{mode}.opengw.net", "port": 443}
        ovpn, _ = vd.write_node_files(node, b64(make_cfg(mode)), d)
        import subprocess
        proc = subprocess.Popen(vd.build_cmd(self.fake_cmd(), os.path.basename(ovpn)), cwd=d,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        lines = []
        t0 = time.monotonic()
        try:
            res = vd.supervise(proc, timeout, emit=lines.append, poll=0.1)
        finally:
            was_running = proc.poll() is None
            vd.stop_process(proc, grace=3)
        return res, lines, time.monotonic() - t0, was_running, proc

    def test_connect_then_clean_exit(self):
        (kind, _detail, conn), lines, _, _, _ = self.run_mode("ok")
        self.assertEqual((kind, conn), (vd.R_EXIT_OK, True))
        self.assertTrue(any("Initialization Sequence Completed" in ln for ln in lines))

    def test_creds_relative_path_resolves_in_cwd(self):
        # 假 OpenVPN 会去读 auth-user-pass 指向的相对文件；读不到会打 Options error 并退出 1
        (kind, _d, conn), lines, _, _, _ = self.run_mode("ok")
        self.assertFalse(any("Options error" in ln for ln in lines))

    def test_timeout_kills_hanging_node(self):
        (kind, detail, conn), _, elapsed, was_running, proc = self.run_mode("never", timeout=1.5)
        self.assertEqual((kind, conn), (vd.R_TIMEOUT, False))
        self.assertLess(elapsed, 10)
        self.assertTrue(was_running)                # 超时时进程确实还挂着
        self.assertIsNotNone(proc.poll())           # stop_process 之后已被掐掉

    def test_completed_with_errors_is_env_error(self):
        (kind, _d, conn), _, elapsed, _, _ = self.run_mode("witherrors")
        self.assertEqual(kind, vd.R_ENV)
        self.assertLess(elapsed, 10)

    def test_no_adapter_is_env_error(self):
        (kind, _d, _c), _, _, _, _ = self.run_mode("noadapter")
        self.assertEqual(kind, vd.R_ENV)

    def test_drop_after_connect_detected_without_process_exit(self):
        (kind, detail, conn), _, elapsed, was_running, proc = self.run_mode("drop", timeout=10)
        self.assertEqual((kind, conn), (vd.R_DROPPED, True))
        self.assertIn("restarting", detail.lower())
        self.assertTrue(was_running)                # OpenVPN 原地重连、没退出 —— 原始版看不到这种掉线
        self.assertLess(elapsed, 8)
        self.assertIsNotNone(proc.poll())

    def test_exit_nonzero_after_connect_is_drop(self):
        (kind, detail, conn), _, _, _, _ = self.run_mode("dropexit")
        self.assertEqual((kind, conn), (vd.R_DROPPED, True))
        self.assertIn("rc=3", detail)

    def test_auth_failed_fast(self):
        (kind, _d, conn), _, elapsed, _, _ = self.run_mode("authfail", timeout=20)
        self.assertEqual((kind, conn), (vd.R_FAILED, False))
        self.assertLess(elapsed, 8)                 # 不用等满超时

    def test_tls_negotiation_failed_fast(self):
        (kind, _d, _c), _, elapsed, _, _ = self.run_mode("tlsfail", timeout=20)
        self.assertEqual(kind, vd.R_FAILED)
        self.assertLess(elapsed, 8)

    def test_exit_before_connect(self):
        (kind, detail, conn), _, _, _, _ = self.run_mode("exit1")
        self.assertEqual((kind, conn), (vd.R_FAILED, False))
        self.assertIn("rc=1", detail)

    def test_unrelated_tls_noise_does_not_fail_connection(self):
        (kind, _d, conn), _, _, _, _ = self.run_mode("noise")
        self.assertEqual((kind, conn), (vd.R_EXIT_OK, True))


# ---------------------------------------------------------------------------
# dial(): 多候选切换
# ---------------------------------------------------------------------------
class TestDial(TmpDirCase):
    def cands(self, *modes):
        return [({"host": f"n{i}_{m}.opengw.net", "port": 443}, b64(make_cfg(m)))
                for i, m in enumerate(modes, 1)]

    def dial(self, cands, **kw):
        logs = []
        with mock.patch.object(vd, "log", lambda m="": logs.append(m)):
            status = vd.dial(cands, self.fake_cmd(), os.path.join(self.tmp, "out"),
                             connect_timeout=kw.pop("connect_timeout", 3.0), pause=0, **kw)
        return status, logs

    def test_switches_until_one_works(self):
        status, logs = self.dial(self.cands("exit1", "authfail", "ok"))
        self.assertEqual(status, "ok")
        text = "\n".join(logs)
        self.assertIn("n1_exit1", text)
        self.assertIn("n2_authfail", text)
        self.assertIn("n3_ok", text)

    def test_timeout_then_next(self):
        status, logs = self.dial(self.cands("never", "ok"), connect_timeout=1.5)
        self.assertEqual(status, "ok")
        self.assertTrue(any("超时" in m for m in logs))

    def test_drop_then_next(self):
        status, logs = self.dial(self.cands("drop", "ok"), connect_timeout=10)
        self.assertEqual(status, "ok")
        self.assertTrue(any("掉线" in m for m in logs))

    def test_all_fail_prints_summary_per_host(self):
        status, logs = self.dial(self.cands("exit1", "authfail", "tlsfail"))
        self.assertEqual(status, "failed")
        text = "\n".join(logs)
        for h in ("n1_exit1", "n2_authfail", "n3_tlsfail"):
            self.assertIn(h, text.split("每个候选的原因")[1])      # 汇总里每个节点都有原因

    def test_env_error_stops_immediately(self):
        status, logs = self.dial(self.cands("witherrors", "ok"))
        self.assertEqual(status, "env_error")
        text = "\n".join(logs)
        self.assertNotIn("n2_ok", text)                         # 没有白白去试第二个
        self.assertIn("管理员", text)

    def test_missing_openvpn_binary(self):
        logs = []
        with mock.patch.object(vd, "log", lambda m="": logs.append(m)):
            status = vd.dial(self.cands("ok"), os.path.join(self.tmp, "no_such_openvpn.exe"),
                             os.path.join(self.tmp, "out"), connect_timeout=2, pause=0)
        self.assertEqual(status, "env_error")

    def test_bad_config_skipped_not_fatal(self):
        bad = ({"host": "bad.opengw.net", "port": 1}, "!!!" * 5 + "=")
        status, logs = self.dial([bad] + self.cands("ok"))
        self.assertEqual(status, "ok")
        self.assertTrue(any("配置生成失败" in m for m in logs))


# ---------------------------------------------------------------------------
# 命令行端到端（dry-run，不启动任何进程）
# ---------------------------------------------------------------------------
class TestRunCli(TmpDirCase):
    def write_feed(self):
        p = os.path.join(self.tmp, "feed.csv")
        wfile(p, make_feed([
            csv_line("vpnA", 100, "Japan", "JP", make_cfg(port=443)),
            csv_line("vpnB", 500, "Korea", "KR", make_cfg(port=995, extra="script-security 2\nup evil.bat\n")),
        ]))
        return p

    def test_dry_run_with_local_feed(self):
        feed, out = self.write_feed(), os.path.join(self.tmp, "稀奇 玩意", "out")
        with mock.patch.object(vd, "log", lambda m="": None):
            rc = vd.run(["--dry-run", "--feed-file", feed, "--out-dir", out, "--ignore-data", "--scene", "all"])
        self.assertEqual(rc, 0)
        names = sorted(os.listdir(out))
        self.assertIn("node_vpnA.opengw.net_443.ovpn", names)
        self.assertIn("node_vpnB.opengw.net_995.ovpn", names)
        self.assertIn("vpngate_creds.txt", names)
        b = rfile(os.path.join(out, "node_vpnB.opengw.net_995.ovpn"))
        self.assertNotIn("evil.bat", b)                         # 危险指令在端到端流程里也被剥掉

    def test_dry_run_writes_session_log_file(self):
        feed, out = self.write_feed(), os.path.join(self.tmp, "out")
        vd.run(["--dry-run", "--feed-file", feed, "--out-dir", out, "--ignore-data"])
        vd.set_log_file(None)
        self.assertTrue(os.path.exists(os.path.join(out, "dial.log")))
        self.assertIn("候选", rfile(os.path.join(out, "dial.log")))

    def test_missing_feed_file_exits_nonzero(self):
        with self.assertRaises(SystemExit) as cm:
            vd.run(["--dry-run", "--feed-file", os.path.join(self.tmp, "nope.csv"),
                    "--out-dir", os.path.join(self.tmp, "out")])
        self.assertEqual(cm.exception.code, 1)

    def test_scene_with_no_candidates_exits_with_hint(self):
        feed = self.write_feed()
        data = os.path.join(self.tmp, "data.json")
        wfile(data, json.dumps({"available": [{"host": "vpnA.opengw.net", "port": 443, "support_ready": False}]}))
        with self.assertRaises(SystemExit):
            vd.run(["--dry-run", "--feed-file", feed, "--data", data, "--scene", "support",
                    "--out-dir", os.path.join(self.tmp, "out")])

    def test_data_json_used_when_present(self):
        feed = self.write_feed()
        data = os.path.join(self.tmp, "data.json")
        wfile(data, json.dumps({"available": [{"host": "vpnA.opengw.net", "port": 443, "support_ready": True,
                                               "composite_score": 1}]}))
        out = os.path.join(self.tmp, "out")
        rc = vd.run(["--dry-run", "--feed-file", feed, "--data", data, "--scene", "support", "--out-dir", out])
        self.assertEqual(rc, 0)
        ovpns = [n for n in os.listdir(out) if n.endswith(".ovpn")]
        self.assertEqual(ovpns, ["node_vpnA.opengw.net_443.ovpn"])      # 只有 data.json 里达标的 A

    def test_not_admin_blocks_real_dial(self):
        feed, out = self.write_feed(), os.path.join(self.tmp, "out")
        with mock.patch.object(vd, "find_openvpn", return_value="openvpn"), \
                mock.patch.object(vd, "is_admin", return_value=False), \
                mock.patch.object(vd, "dial") as dial, \
                self.assertRaises(SystemExit):
            vd.run(["--feed-file", feed, "--out-dir", out, "--ignore-data"])
        dial.assert_not_called()

    def test_no_openvpn_installed_dies_with_hint(self):
        feed, out = self.write_feed(), os.path.join(self.tmp, "out")
        with mock.patch.object(vd, "find_openvpn", return_value=""), self.assertRaises(SystemExit):
            vd.run(["--feed-file", feed, "--out-dir", out, "--ignore-data"])

    def test_exit_code_reflects_dial_result(self):
        feed, out = self.write_feed(), os.path.join(self.tmp, "out")
        for status, code in (("ok", 0), ("failed", 1), ("env_error", 1)):
            with mock.patch.object(vd, "find_openvpn", return_value="openvpn"), \
                    mock.patch.object(vd, "is_admin", return_value=True), \
                    mock.patch.object(vd, "dial", return_value=status):
                self.assertEqual(vd.run(["--feed-file", feed, "--out-dir", out, "--ignore-data"]), code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
