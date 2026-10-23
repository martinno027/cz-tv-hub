import unittest

from cztvhub.dvb import diag

PLAYLIST = """#EXTM3U
#EXT-X-TARGETDURATION:2
#EXT-X-MEDIA-SEQUENCE:4
#EXTINF:2.000000,
/dvb/hls/x/seg_00004.m4s
#EXTINF:1.960000,
/dvb/hls/x/seg_00005.m4s
"""


class DiagTest(unittest.TestCase):
    def test_parse_playlist(self):
        self.assertEqual(diag.parse_playlist(PLAYLIST),
                         (2, [("/dvb/hls/x/seg_00004.m4s", 2.0), ("/dvb/hls/x/seg_00005.m4s", 1.96)]))

    def test_segment_log(self):
        log = diag.SegmentLog()
        self.assertEqual(log.feed(10.0, PLAYLIST), 2)
        self.assertEqual(log.feed(11.0, PLAYLIST), 0)
        self.assertEqual(log.feed(13.0, PLAYLIST.replace("TARGETDURATION:2", "TARGETDURATION:4")
                                  + "#EXTINF:4.0,\n/dvb/hls/x/seg_00006.m4s\n"), 1)
        self.assertEqual(log.targets, [2, 4])
        self.assertEqual(log.max_gap(), 3.0)

    def test_simulated_stalls(self):
        steady = [(t * 2.0, 2.0) for t in range(30)]
        self.assertEqual(diag.simulate_stalls(steady, 6), (0, 0.0))
        # a 9 s gap in production: 6 s of buffer runs dry, 12 s does not
        hiccup = steady[:10] + [(t * 2.0 + 9, 2.0) for t in range(10, 30)]
        stalls, longest = diag.simulate_stalls(hiccup, 6)
        self.assertEqual(stalls, 1)
        self.assertGreater(longest, 0)
        self.assertEqual(diag.simulate_stalls(hiccup, 12)[0], 0)
        self.assertEqual(diag.simulate_stalls([], 6), (0, 0.0))

    def test_tuner_counters(self):
        out = diag.tuner_counters([{"input": "T1", "signal": 49151, "signal_scale": 1, "te": 3, "cc": 1, "unc": 0}])
        self.assertEqual(out["T1"]["te"], 3)
        self.assertEqual(diag._level(49151, 1), "74 %")
        self.assertEqual(diag._level(-45000, 2), "-45.0 dB")


if __name__ == "__main__":
    unittest.main()
