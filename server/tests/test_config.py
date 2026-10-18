import tempfile
import unittest
from pathlib import Path

from cztvhub.config import Config, ConfigError, load_config, parse_config

KEY = "k" * 32


class ConfigTest(unittest.TestCase):
    def test_no_file_is_ct_only(self):
        cfg = load_config(None)
        self.assertEqual((cfg.host, cfg.port, cfg.dvb.enabled), ("0.0.0.0", 8098, False))

    def test_full_example_from_docs(self):
        cfg = parse_config(f"""
[server]
host = 0.0.0.0          ; or the LAN + tailscale addresses
port = 8099

[dvb]
enabled = true
key = {KEY}       ; apps send "Authorization: Bearer <key>"
retention_days = 5
record_channels = ČT1 HD, Nova HD
hls_dir = /tmp/hls  ; on the recordings disk
idle_seconds = 45

[tvheadend]
url = http://127.0.0.1:9981/
user = cztvhub
password = se;cret
""")
        self.assertEqual(cfg.port, 8099)
        self.assertTrue(cfg.dvb.enabled)
        self.assertEqual(cfg.dvb.key, KEY)
        self.assertEqual(cfg.dvb.retention_days, 5)
        self.assertEqual(cfg.dvb.record_channels, ["ČT1 HD", "Nova HD"])
        self.assertEqual(cfg.dvb.hls_dir, Path("/tmp/hls"))
        self.assertEqual(cfg.dvb.idle_seconds, 45)
        self.assertEqual(cfg.tvheadend.url, "http://127.0.0.1:9981")
        self.assertEqual(cfg.tvheadend.password, "se;cret")  # inline comments need a preceding space

    def test_retention_is_clamped(self):
        for value, expected in (("0", 1), ("-3", 1), ("3", 3), ("14", 14), ("30", 14)):
            cfg = parse_config(f"[dvb]\nretention_days = {value}\n")
            self.assertEqual(cfg.dvb.retention_days, expected, value)

    def test_defaults(self):
        cfg = parse_config("[dvb]\n")
        self.assertEqual(cfg.dvb.retention_days, 3)
        self.assertIsNone(cfg.dvb.record_channels)
        self.assertEqual(cfg.dvb.idle_seconds, 30)

    def test_record_channels_all(self):
        self.assertIsNone(parse_config("[dvb]\nrecord_channels = all\n").dvb.record_channels)
        self.assertIsNone(parse_config("[dvb]\nrecord_channels = ALL\n").dvb.record_channels)

    def test_enabled_needs_long_key(self):
        with self.assertRaises(ConfigError):
            parse_config("[dvb]\nenabled = true\nkey = short\n")
        with self.assertRaises(ConfigError):
            parse_config("[dvb]\nenabled = true\n")
        parse_config("[dvb]\nenabled = false\nkey = short\n")

    def test_bad_values(self):
        with self.assertRaises(ConfigError):
            parse_config("[server]\nport = abc\n")
        with self.assertRaises(ConfigError):
            parse_config("[dvb]\nenabled = maybe\n")
        with self.assertRaises(ConfigError):
            parse_config("not ini")

    def test_load_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "c.conf"
            path.write_text("[server]\nport = 9000\n", encoding="utf-8")
            self.assertEqual(load_config(path).port, 9000)
            with self.assertRaises(ConfigError):
                load_config(Path(tmp) / "missing.conf")
        self.assertIsInstance(load_config(None), Config)
