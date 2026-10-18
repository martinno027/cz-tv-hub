import unittest

from cztvhub.channels import CHANNELS
from cztvhub.m3u import build_playlist


class PlaylistTest(unittest.TestCase):
    def test_header_and_entry_count(self):
        text = build_playlist("http://10.0.0.5:8098")
        lines = text.splitlines()
        self.assertEqual(lines[0], "#EXTM3U")
        self.assertEqual(sum(l.startswith("#EXTINF") for l in lines), len(CHANNELS))

    def test_urls_point_at_redirect_endpoint(self):
        text = build_playlist("http://10.0.0.5:8098/")
        self.assertIn("http://10.0.0.5:8098/ct/CH_1.m3u8", text)
        self.assertNotIn("//ct/", text)

    def test_metadata(self):
        text = build_playlist("http://h")
        self.assertIn('tvg-id="CT1.cz"', text)
        self.assertIn(",ČT1\n", text)
