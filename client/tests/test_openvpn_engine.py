#!/usr/bin/env python3
"""engines/openvpn_engine 离线测试：不依赖 openvpn 二进制、不拨号。"""
import base64
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from client.core.model import Node
from client.engines.base import EngineError
from client.engines.openvpn_engine import OpenVPNEngine


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


OVPN = ("dev tun\nproto tcp\nremote 1.2.3.4 443\nclient\nverb 3\n"
        "up /tmp/evil.sh\nscript-security 2\n"
        "<ca>\n-----BEGIN CERTIFICATE-----\nMIIFAKE\n-----END CERTIFICATE-----\n</ca>\n")


def b64_node():
    return Node(id="openvpn:1.2.3.4:443", name="n", protocol="openvpn", source="feed",
                host="vpn123456.opengw.net", port=443, config_ref=b64(OVPN),
                extra={"config_format": "b64"})


class TestOpenVPNEngine(unittest.TestCase):
    def test_missing_binary_raises_engine_error_not_stack(self):
        with tempfile.TemporaryDirectory() as d:
            eng = OpenVPNEngine(out_dir=d, binary="")
            with self.assertRaises(EngineError) as ctx:
                eng.start(b64_node())
            self.assertIn("openvpn", str(ctx.exception).lower())

    def test_render_config_sanitizes_dangerous_directives(self):
        with tempfile.TemporaryDirectory() as d:
            eng = OpenVPNEngine(out_dir=d, binary="")
            text = eng.render_config(b64_node())
            self.assertNotIn("/tmp/evil.sh", text)          # up 指令被剥掉
            self.assertIn("script-security 1", text)        # vendored sanitize 的收尾
            self.assertIn("auth-user-pass vpngate_creds.txt", text)
            self.assertIn("remote 1.2.3.4 443", text)

    def test_render_config_text_format(self):
        node = Node(id="openvpn:1.2.3.4:443", name="n", protocol="openvpn",
                    source="manual", host="1.2.3.4", port=443, config_ref=OVPN,
                    extra={"config_format": "text"})
        with tempfile.TemporaryDirectory() as d:
            eng = OpenVPNEngine(out_dir=d, binary="")
            self.assertIn("remote 1.2.3.4 443", eng.render_config(node))

    def test_status_of_none_handle(self):
        with tempfile.TemporaryDirectory() as d:
            eng = OpenVPNEngine(out_dir=d, binary="")
            self.assertEqual(eng.status(None), "stopped")
            eng.stop(None)  # 不抛


if __name__ == "__main__":
    unittest.main()
