"""EIT present/following measurement (spec/dvb-gateway.md "Real programme starts from the broadcast", T14 phase 1):
TS packet reader, EIT p/f parsing, DVB text, tracker, store bounds, the dvb-starts report, and the recorder's pump
feeding ffmpeg byte-identically with the sniffer on."""
import json
import random
import tempfile
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from cztvhub.dvb import eit, starts
from cztvhub.dvb.buffer import BufferStore
from cztvhub.dvb.fake import FAKE_EIT, EitInjector, FakeTvheadend
from cztvhub.dvb.eit import (EIT_PID, TS, EitSniffer, EitStore, PresentTracker, TsReader, bcd_seconds,
                             build_eit_pf, build_pat, build_section, crc32_mpeg, decode_text, mjd_date,
                             packetize, parse_eit_pf, start_epoch)
from cztvhub.dvb.recorder import PROBE_BYTES, ChannelRecorder
from cztvhub.dvb.tvheadend import EpgEvent, TvhError

T0 = int(datetime(2026, 10, 24, 18, 0, tzinfo=timezone.utc).timestamp())
CH = "c" * 32


def utc(*args):
    return int(datetime(*args, tzinfo=timezone.utc).timestamp())


def pack_tight(pid, sections, cc=0):
    """Sections back to back across packets: a packet where a section starts gets PUSI and a pointer_field
    to it, so the tail of the previous section comes first (EN 300 468 / ISO 13818-1)."""
    stream = b"".join(sections)
    starts_at, pos = [], 0
    for s in sections:
        starts_at.append(pos)
        pos += len(s)
    out, pos = bytearray(), 0
    while pos < len(stream):
        first = next((s for s in starts_at if pos <= s < pos + TS - 5), None)
        if first is not None:
            payload = bytes((first - pos,)) + stream[pos:pos + TS - 5]
            pos += TS - 5
        else:
            payload = stream[pos:pos + TS - 4]
            pos += TS - 4
        out += bytes((0x47, (0x40 if first is not None else 0) | pid >> 8, pid & 0xFF, 0x10 | cc))
        out += payload + b"\xff" * (TS - 4 - len(payload))
        cc = (cc + 1) & 0x0F
    return bytes(out)


def filler(pid, n, seed=1):
    rnd = random.Random(seed)
    out = bytearray()
    for i in range(n):
        out += bytes((0x47, pid >> 8, pid & 0xFF, 0x10 | (i & 0x0F))) + bytes(rnd.getrandbits(8) for _ in range(184))
    return bytes(out)


def pf(sid, event_id, start, title, running=4, number=0, duration=1800, encoded=None):
    name = encoded if encoded is not None else b"\x15" + title.encode("utf-8")
    return build_eit_pf(sid, number, [(event_id, start, duration, running, name)])


def chunks(data, sizes):
    out, i, k = [], 0, 0
    while i < len(data):
        n = sizes[k % len(sizes)]
        out.append(data[i:i + n])
        i, k = i + n, k + 1
    return out


class CrcAndTimeTest(unittest.TestCase):
    def test_crc_mpeg2(self):
        self.assertEqual(crc32_mpeg(b"123456789"), 0x0376E6E7)  # CRC-32/MPEG-2 check value
        section = build_pat({1: 0x1000})
        self.assertEqual(crc32_mpeg(section), 0)
        self.assertNotEqual(crc32_mpeg(section[:-1] + bytes((section[-1] ^ 1,))), 0)

    def test_mjd_and_bcd(self):
        # EN 300 468 Annex C example: 93/10/13 12:45:00 is coded as 0xC079124500
        self.assertEqual(start_epoch(bytes.fromhex("C079124500")), utc(1993, 10, 13, 12, 45))
        # 0xE4D0 = 58576 days after 1858-11-17; 2000-01-01 is MJD 51544, + 7032 days = 2019-04-03 (counted by hand)
        self.assertEqual(mjd_date(0xE4D0), date(2019, 4, 3))
        self.assertEqual(mjd_date(0xEF99), date(2026, 10, 24))  # 51544 + 9497 (2000..2025) + 296
        self.assertEqual(start_epoch(bytes.fromhex("EF99183005")), utc(2026, 10, 24, 18, 30, 5))
        self.assertEqual(bcd_seconds(bytes.fromhex("014530")), 3600 + 45 * 60 + 30)
        self.assertIsNone(bcd_seconds(bytes.fromhex("011A00")))  # not BCD
        self.assertIsNone(bcd_seconds(bytes.fromhex("016000")))  # 60 minutes
        self.assertIsNone(start_epoch(b"\xff" * 5))  # undefined start (NVOD)
        self.assertIsNone(start_epoch(bytes.fromhex("EF99250000")))  # hour 25
        self.assertEqual(eit.encode_start(T0), bytes.fromhex("EF99180000"))


class TextTest(unittest.TestCase):
    def test_default_table_iso6937_with_czech_diacritics(self):
        self.assertEqual(decode_text(b"Sout\xcfe\xcfz"), "Soutěž")  # 0xCF caron before the letter
        self.assertEqual(decode_text(b"P\xcaulnoc v kulturn\xc2im"), "Půlnoc v kulturním")
        self.assertEqual(decode_text(b"\xcfCas \xd3 \xe8\xf9"), "Čas © Łø")
        self.assertEqual(decode_text(eit.encode_6937("Žluťoučký kůň úpěl")), "Žluťoučký kůň úpěl")
        self.assertEqual(len(eit.ISO6937), 96)

    def test_charset_bytes(self):
        self.assertEqual(decode_text(b"\x15" + "Příliš žluťoučký".encode()), "Příliš žluťoučký")
        self.assertEqual(decode_text(b"\x10\x00\x02" + "Žluťoučký kůň".encode("iso8859-2")), "Žluťoučký kůň")
        self.assertEqual(decode_text(b"\x05\xc7a"), "Ça")  # 0x05 = ISO 8859-9
        self.assertEqual(decode_text(b"\x01\xb2"), "В")  # 0x01 = ISO 8859-5
        self.assertEqual(decode_text(b"\x11" + "Čas".encode("utf-16-be")), "Čas")

    def test_control_codes_and_whitespace(self):
        self.assertEqual(decode_text(b"\x86Zpr\xc2avy\x87\x8adnes  "), "Zprávy dnes")
        self.assertEqual(decode_text(b"\x15A\xee\x82\x8aB"), "A B")  # U+E08A CR/LF in a Unicode table

    def test_garbage_never_raises(self):
        rnd = random.Random(7)
        samples = [b"", b"\x10\x00\x0c\xe0", b"\x10\x00", b"\x11\x00", b"\x1f\x01ab", b"\x08abc", b"\x13\xff\xfe",
                   b"\x12\xff", b"\xcf", b"\xc1\xc2\xc3"] + [bytes(rnd.getrandbits(8) for _ in range(40))
                                                           for _ in range(300)]
        for data in samples:
            self.assertIsInstance(decode_text(data), str)


class ReaderTest(unittest.TestCase):
    def long_section(self, event_id=7, title="Dlouhý název " * 12):
        name = b"\x15" + title.encode("utf-8")
        return build_eit_pf(1, 0, [(event_id, T0, 1800, 4, name), (event_id + 1, T0 + 1800, 1800, 1, name)])

    def test_section_across_packets_any_chunk_size(self):
        section = self.long_section()
        self.assertGreater(len(section), 2 * TS)  # really spans three packets
        packets, _ = packetize(EIT_PID, section)
        stream = filler(0x100, 3) + packets + filler(0x100, 2)
        for sizes in ([1], [7], [187], [189], [5, 400, 3], [len(stream)]):
            reader = TsReader({EIT_PID: 0x4E})
            found = [s for c in chunks(stream, sizes) for _, s in reader.feed(c)]
            self.assertEqual(found, [section], sizes)
        parsed = parse_eit_pf(section)
        self.assertEqual((parsed.service_id, parsed.section_number), (1, 0))
        self.assertEqual(parsed.events[0].event_id, 7)
        self.assertEqual(parsed.events[0].start, T0)
        self.assertEqual(parsed.events[0].duration, 1800)
        self.assertEqual(parsed.events[0].running, 4)
        self.assertEqual(parsed.events[0].title, ("Dlouhý název " * 12).strip())

    def test_pointer_field_and_several_sections(self):
        a, b, c = self.long_section(1), pf(1, 2, T0, "Krátký"), pf(1, 3, T0, "Další")
        stream = pack_tight(EIT_PID, [a, b, c])
        reader = TsReader({EIT_PID: 0x4E})
        found = [s for chunk in chunks(stream, [33, 150]) for _, s in reader.feed(chunk)]
        self.assertEqual(found, [a, b, c])

    def test_bad_crc_other_pids_tables_and_lost_packets_ignored(self):
        good = pf(1, 5, T0, "Zprávy")
        bad = bytearray(pf(1, 6, T0, "Rozbité"))
        bad[20] ^= 0xFF
        schedule = build_section(0x50, 1, bytes(40))  # EIT schedule on PID 0x12: not wanted
        other_pid, _ = packetize(0x13, good)
        long_one, _ = packetize(EIT_PID, self.long_section(9), cc=3)
        lost = long_one[:TS] + long_one[2 * TS:]  # middle packet missing: CC jumps
        stream = (other_pid + packetize(EIT_PID, bytes(bad))[0] + packetize(EIT_PID, schedule, cc=1)[0]
                  + packetize(EIT_PID, good, cc=2)[0] + lost)
        reader = TsReader({EIT_PID: 0x4E})
        found = [s for chunk in chunks(stream, [100]) for _, s in reader.feed(chunk)]
        self.assertEqual(found, [good])
        self.assertEqual(reader.crc_errors, 1)

    def test_adaptation_field_error_flag_and_resync(self):
        section = pf(1, 5, T0, "Zprávy")
        packet, _ = packetize(EIT_PID, section)
        body = packet[4:4 + 1 + len(section)]
        af = bytes((0x47, 0x40, 0x12, 0x30, 9)) + bytes((0x10,)) + b"\xff" * 8  # adaptation field, 10 bytes
        with_af = af + body + b"\xff" * (TS - len(af) - len(body))
        errored = bytes((0x47, 0xC0)) + packet[2:]  # transport_error_indicator
        stream = b"\x00\x47garbage" + filler(0x100, 2) + errored + with_af
        reader = TsReader({EIT_PID: 0x4E})
        found = [s for chunk in chunks(stream, [61]) for _, s in reader.feed(chunk)]
        self.assertEqual(found, [section])


class TrackerTest(unittest.TestCase):
    def setUp(self):
        self.lines, self.logs = [], []
        self.tracker = PresentTracker(CH, self.lines.append, self.logs.append)
        self.cc = 0

    def feed(self, sniffer, *sections):
        data = b""
        for s in sections:
            packets, self.cc = packetize(EIT_PID, s, cc=self.cc)
            data += packets
        sniffer.feed(data)

    def test_writes_only_changes_and_marks_the_tune_in(self):
        clock = {"t": T0 + 100}
        sniffer = EitSniffer(self.tracker, clock=lambda: clock["t"])
        sniffer.feed(packetize(0, build_pat({1: 0x1000}))[0])
        self.feed(sniffer, pf(1, 10, T0, "Zprávy"), pf(1, 11, T0 + 1800, "Počasí", running=1, number=1),
                  pf(2, 99, T0, "Jiná služba"))
        clock["t"] += 5
        self.feed(sniffer, pf(1, 10, T0, "Zprávy"))  # unchanged: no line
        self.assertEqual(len(self.lines), 1)
        self.assertEqual(self.lines[0], {"channel": CH, "eventId": 10, "title": "Zprávy",
                                         "eitStart": "2026-10-24T18:00:00Z", "eitDuration": 1800,
                                         "seenAt": eit.iso(T0 + 100), "running": 4, "atTune": True})
        clock["t"] = T0 + 1990
        self.feed(sniffer, pf(1, 11, T0 + 1800, "Počasí", running=2))
        clock["t"] += 9
        self.feed(sniffer, pf(1, 11, T0 + 1800, "Počasí", running=4))  # same event, now running
        self.assertEqual([(l["eventId"], l["running"], "atTune" in l) for l in self.lines],
                         [(10, 4, True), (11, 2, False), (11, 4, False)])
        self.assertEqual(self.lines[2]["seenAt"], eit.iso(T0 + 1999))
        # a new TVHeadend stream: the same event is not a change, a different one is noted as tuned-in
        sniffer = EitSniffer(self.tracker, clock=lambda: clock["t"])
        sniffer.feed(packetize(0, build_pat({1: 0x1000}))[0])
        self.feed(sniffer, pf(1, 11, T0 + 1800, "Počasí"))
        self.assertEqual(len(self.lines), 3)
        sniffer = EitSniffer(self.tracker, clock=lambda: clock["t"])
        sniffer.feed(packetize(0, build_pat({1: 0x1000}))[0])
        self.feed(sniffer, pf(1, 12, T0 + 2400, "Film"))
        self.assertTrue(self.lines[3]["atTune"])
        self.assertEqual(self.logs, [])

    def test_own_service_from_an_unrewritten_pat(self):
        sniffer = EitSniffer(self.tracker, clock=lambda: T0)
        sniffer.feed(packetize(0, build_pat({1: 0x1000, 2: 0x1100, 3: 0x1200}))[0])
        self.feed(sniffer, pf(1, 10, T0, "Jedna"), pf(2, 20, T0, "Dva"))
        self.assertEqual(self.lines, [])  # which one is ours is not known yet
        sniffer.feed(filler(0x1100, 1))  # only service 2's PMT is in the stream
        self.assertEqual(sniffer.own_service(), 2)
        self.feed(sniffer, pf(1, 10, T0, "Jedna"), pf(2, 20, T0, "Dva"))
        self.assertEqual([l["title"] for l in self.lines], ["Dva"])

    def test_no_eit_logged_once_with_the_reason(self):
        for setup, reason in ((lambda s: None, "none"),
                              (lambda s: self.feed(s, pf(5, 1, T0, "Cizí")), "otherServices"),
                              (lambda s: s.feed(packetize(0, build_pat({1: 16, 2: 17}))[0])
                               or self.feed(s, pf(2, 1, T0, "Která?")), "unknownService")):
            lines, logs = [], []
            tracker = PresentTracker(CH, lines.append, logs.append)
            clock = {"t": T0}
            sniffer = EitSniffer(tracker, clock=lambda: clock["t"])
            if reason != "unknownService":
                sniffer.feed(packetize(0, build_pat({1: 0x1000}))[0])
            setup(sniffer)
            clock["t"] = T0 + 299
            sniffer.feed(filler(0x100, 1))
            self.assertEqual(logs, [], reason)
            clock["t"] = T0 + 301
            sniffer.feed(filler(0x100, 1))
            clock["t"] = T0 + 900
            sniffer.feed(filler(0x100, 1))
            self.assertEqual(logs, [f"dvb: eit: no present/following on {CH} ({reason})"])
            self.assertEqual([l.get("noEit") for l in lines], [reason])

    def test_present_found_means_no_warning(self):
        clock = {"t": T0}
        sniffer = EitSniffer(self.tracker, clock=lambda: clock["t"])
        sniffer.feed(packetize(0, build_pat({1: 0x1000}))[0])
        self.feed(sniffer, pf(1, 1, T0, "Zprávy"))
        clock["t"] += 3600
        sniffer.feed(filler(0x100, 1))
        self.assertEqual(self.logs, [])
        self.assertNotIn("noEit", json.dumps(self.lines))


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = T0
        self.days = 3
        self.store = EitStore(Path(self.tmp.name), lambda: self.days, now=lambda: self.now, max_channel_bytes=1000)

    def line(self, channel, seen, n):
        return {"channel": channel, "eventId": n, "title": "x" * 60, "seenAt": eit.iso(seen), "running": 4}

    def test_age_and_size_bounds_drop_the_oldest(self):
        for n in range(40):
            self.store.append(self.line("a", T0 - 4 * 86400 + n * 3600, n))
        self.store.append(self.line("b", T0 - 100, 1))
        self.now = T0 + 3601  # next append prunes (hourly)
        self.store.append(self.line("b", T0 + 3601, 2))
        records = self.store.read()
        a = [r["eventId"] for r in records if r["channel"] == "a"]
        b = [r["eventId"] for r in records if r["channel"] == "b"]
        self.assertEqual(b, [1, 2])  # another channel's lines are untouched
        self.assertTrue(a and a == sorted(a))
        size = sum(len(json.dumps(r, ensure_ascii=False, separators=(",", ":"))) + 1
                   for r in records if r["channel"] == "a")
        self.assertLessEqual(size, 1000)
        self.assertEqual(a[-1], 39)  # the newest kept
        oldest = eit.iso(self.now - 3 * 86400)
        self.assertTrue(all(r["seenAt"] >= oldest for r in records))
        self.assertGreater(len(a), 3)

    def test_max_days_and_a_cut_last_line(self):
        self.store.append(self.line("a", T0 - 2 * 86400, 1))
        self.store.append(self.line("a", T0 - 100, 2))
        with self.store.path.open("a", encoding="utf-8") as f:
            f.write('{"channel": "a", "eventId": 3, "seen')  # power loss mid-line
        self.days = 1
        self.store.prune()
        self.assertEqual([r["eventId"] for r in self.store.read()], [2])
        self.store.append(self.line("a", T0, 4))
        self.assertEqual([r["eventId"] for r in self.store.read()], [2, 4])

    def test_write_failure_logged_once(self):
        logs = []
        blocked = Path(self.tmp.name) / "file"
        blocked.write_text("")
        store = EitStore(blocked, lambda: 3, log=logs.append)  # state_dir is a file: cannot write
        store.append(self.line("a", T0, 1))
        store.append(self.line("a", T0, 2))
        self.assertEqual(len(logs), 1)


def event(channel, start, minutes, title, eid=None):
    return EpgEvent(eid or f"{channel}{start}", channel, title, "", "", [], start, start + minutes * 60)


class ReportTest(unittest.TestCase):
    def setUp(self):
        self.epg = [event(CH, T0, 30, "Zprávy"), event(CH, T0 + 1800, 60, "Film"),
                    event(CH, T0 + 5400, 30, "Seriál"), event(CH, T0 + 7200, 0, "Prázdný"),
                    event(CH, T0 - 86400, 30, "Zprávy")]

    def rec(self, eid, title, eit_start, seen, running=4, **extra):
        return {"channel": CH, "eventId": eid, "title": title, "eitStart": eit.iso(eit_start), "eitDuration": 1800,
                "seenAt": eit.iso(seen), "running": running, **extra}

    def run_report(self, records, channel=None, recorded=()):
        out = []
        counted = starts.report(records, {CH: "Nova HD", "d" * 32: "Prima HD"}, set(recorded),
                                lambda ch, s, e: [p for p in self.epg if p.channel == ch], T0 - 3600,
                                T0 + 9000, out.append, channel)
        return counted, "\n".join(out)

    def test_matching_delays_and_summary(self):
        records = [
            self.rec(1, "Zprávy", T0, T0 + 5, atTune=True),  # tuned in during the news: not counted
            self.rec(2, "Film", T0 + 1800, T0 + 1800 + 100, running=2),
            self.rec(2, "Film", T0 + 1800, T0 + 1800 + 150),  # became running: this one counts (+2:30)
            self.rec(3, "Seriál (R)", T0 + 5400, T0 + 5400 - 20),  # title differs: matched by time (-0:20)
            self.rec(4, "Neznámý", T0 + 20000, T0 + 20000),  # nothing in the EPG
        ]
        counted, text = self.run_report(records)
        self.assertEqual(counted, 2)
        self.assertIn("při naladění, nepočítá se", text)
        self.assertIn("v EPG: Seriál", text)
        self.assertIn("bez pořadu v EPG", text)
        self.assertIn("+2:30", text)
        self.assertIn("-0:20", text)
        self.assertIn("zpoždění (vidět − EPG): medián +1:05, rozsah -0:20 až +2:30, do ±1 min 1/2 (50 %)", text)
        self.assertIn("EIT začátek − EPG:      medián +0:00, rozsah +0:00 až +0:00, do ±1 min 2/2 (100 %)", text)
        film = next(l for l in text.splitlines() if l.strip().startswith("Film"))
        self.assertIn("20:30:00", film)  # Prague time of the EPG start (CEST, UTC+2)

    def test_same_title_picks_the_nearest_programme(self):
        programme, same = starts.match(self.rec(1, "zprávy ", T0 + 30, T0 + 60), self.epg)
        self.assertTrue(same)
        self.assertEqual(programme.start, T0)  # not yesterday's news
        programme, same = starts.match(self.rec(1, "Prázdný", T0 + 7200, T0 + 7200), self.epg)
        self.assertFalse(same)  # zero-length EPG events are no programmes

    def test_no_eit_and_silent_recorded_channels_are_visible(self):
        records = [{"channel": CH, "seenAt": eit.iso(T0), "noEit": "otherServices", "services": [5, 6],
                    "serviceId": 1}]
        counted, text = self.run_report(records, recorded={"d" * 32})
        self.assertEqual(counted, 0)
        self.assertIn("Rewrite EIT", text)
        self.assertIn("služby v EIT: [5, 6]", text)
        self.assertIn("== Prima HD", text)
        self.assertIn("žádná změna pořadu z EIT (nahrává se? EIT neposlán?)", text)
        self.assertIn("nic k porovnání", text)

    def test_channel_filter_and_window(self):
        records = [self.rec(2, "Film", T0 + 1800, T0 + 1860), self.rec(1, "Zprávy", T0 - 86400, T0 - 86400 + 60)]
        counted, text = self.run_report(records, channel="nova hd")
        self.assertEqual(counted, 1)  # yesterday's line is outside the window
        counted, text = self.run_report(records, channel="ČT1")
        self.assertIn("nemá v posledních hodinách žádný záznam EIT", text)

    def test_occurrences_take_the_running_line(self):
        lines = [self.rec(2, "Film", T0, T0 + 1, running=1), self.rec(2, "Film", T0, T0 + 50),
                 self.rec(3, "Seriál", T0, T0 + 99), self.rec(2, "Film", T0, T0 + 200)]
        self.assertEqual([l["seenAt"] for l in starts.occurrences(lines)],
                         [eit.iso(T0 + 50), eit.iso(T0 + 99), eit.iso(T0 + 200)])

    def test_run_starts_without_tvheadend(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)

        class Client:
            def channels(self):
                raise TvhError("down")

            def epg_events(self, start, stop):
                raise TvhError("down")

        class Cfg:
            class dvb:
                state_dir = Path(tmp.name)

        out = []
        self.assertEqual(starts.run_starts(Cfg, 24, None, out.append, client=Client(), now=lambda: T0), 1)
        EitStore(Path(tmp.name), lambda: 3, now=lambda: T0).append(self.rec(2, "Film", T0 - 100, T0 - 50))
        self.assertEqual(starts.run_starts(Cfg, 24, None, out.append, client=Client(), now=lambda: T0), 0)
        text = "\n".join(out)
        self.assertIn("TVHeadend neodpovídá", text)
        self.assertIn(f"== {CH} ({CH})", text)
        self.assertIn("bez pořadu v EPG", text)


class Sink:
    def __init__(self):
        self.data = bytearray()
        self.closed = False

    def write(self, chunk):
        self.data += chunk

    def close(self):
        self.closed = True


class Proc:
    def __init__(self):
        self.stdin = Sink()

    def poll(self):
        return 0 if self.stdin.closed else None

    def terminate(self):
        self.stdin.closed = True

    def wait(self, timeout=None):
        return 0

    def kill(self):
        pass


class Source:
    def __init__(self, data, sizes):
        self.parts = chunks(data, sizes)

    def read1(self, n):
        return self.parts.pop(0) if self.parts else b""

    def read(self, n):
        return self.read1(n)

    def close(self):
        pass


def broadcast():
    """A pass-profile-like TS: PAT, PMT, 'video' with random payload, EIT p/f of ours and another service, an EIT
    schedule section and a bad-CRC section, with a programme change in the middle."""
    pat, _ = packetize(0, build_pat({1: 0x1000}))
    bad = bytearray(pf(1, 66, T0, "Rozbité"))
    bad[-1] ^= 1
    parts, cc = [pat, filler(0x1000, 1)], 0
    for i in range(60):
        parts.append(filler(0x100, 100, seed=i))
        event_id, title = (10, "Zprávy") if i < 45 else (11, "Počasí v ČR")
        for section in (pf(1, event_id, T0, title), pf(1, event_id + 1, T0 + 1800, "Další", running=1, number=1),
                        pf(2, 77, T0, "Jiná služba"), build_section(0x50, 1, bytes(300)), bytes(bad)):
            packets, cc = packetize(EIT_PID, section, cc)
            parts.append(packets)
    return b"".join(parts)


class RecorderPumpTest(unittest.TestCase):
    """The most important property: ffmpeg gets exactly the bytes TVHeadend sent, sniffer on or off."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def record(self, data, eit_store, sizes=(1000, 61, 65536, 7, 188 * 3 + 1)):
        procs, logs = [], []

        def popen(cmd, **kwargs):
            procs.append(Proc())
            return procs[-1]

        rec = ChannelRecorder(CH, BufferStore(self.root / "buffer"), lambda: Source(data, list(sizes)),
                              prober=lambda head: [{"type": "video", "codec": "hevc", "language": "",
                                                    "impaired": False}],
                              popen=popen, log=logs.append, eit=eit_store)
        with self.assertRaises(TvhError):  # the source ended: "recording ended"
            rec._run_once()
        return bytes(procs[0].stdin.data), logs

    def test_ffmpeg_gets_the_stream_byte_identical_with_the_sniffer_on(self):
        data = broadcast()
        self.assertGreater(len(data), PROBE_BYTES + 3 * 65536)  # most of it goes through the pump's chunk loop
        self.assertGreater(data.index("Počasí".encode()), PROBE_BYTES)  # the change too
        store = EitStore(self.root / "state", lambda: 3)
        fed_on, logs = self.record(data, store)
        fed_off, _ = self.record(data, None)
        self.assertEqual(fed_on, data)
        self.assertEqual(fed_off, data)
        records = store.read()
        self.assertEqual([(r["eventId"], r["title"]) for r in records], [(10, "Zprávy"), (11, "Počasí v ČR")])
        self.assertTrue(records[0]["atTune"])
        self.assertNotIn("atTune", records[1])
        self.assertEqual(logs, [])

    def test_a_failing_parser_keeps_recording_and_logs_once(self):
        data = broadcast()
        original = EitSniffer.feed
        EitSniffer.feed = lambda self, chunk: 1 / 0
        self.addCleanup(setattr, EitSniffer, "feed", original)
        fed, logs = self.record(data, EitStore(self.root / "state", lambda: 3))
        self.assertEqual(fed, data)
        self.assertEqual([l for l in logs if "eit" in l],
                         [f"dvb: eit: {CH}: parser stopped for this run: ZeroDivisionError('division by zero')"])


class FakeEitTest(unittest.TestCase):
    """The fake TVHeadend's live streams carry EIT p/f, so dvb-starts shows something without a tuner."""

    def setUp(self):
        self.fake = FakeTvheadend(Path(tempfile.gettempdir()) / "unused", port=0)

    def stream(self, index, now):
        channel = self.fake.channels[index]
        clock = {"t": now}
        inject = EitInjector(self.fake, channel, clock=lambda: clock["t"])
        ts = filler(0x100, 300)
        out = b""
        for i, chunk in enumerate(chunks(ts, [1000, 77, 5000])):
            clock["t"] = now + i * 0.4
            out += inject.feed(chunk)
        return ts, out, channel

    def test_ffmpeg_packets_unchanged_and_present_event_readable(self):
        for index in (0, 1, 2):  # name in ISO 6937, UTF-8 and ISO 8859-2
            delay = FAKE_EIT[index][0]
            ts, out, channel = self.stream(index, T0 + 10 * 3600 + delay + 5)
            packets = [out[i:i + TS] for i in range(0, len(out), TS)]
            self.assertEqual(b"".join(p for p in packets if p[2] != EIT_PID), ts[:len(ts) - len(ts) % TS])
            self.assertGreater(len(packets), len(ts) // TS)  # something was added
            lines = []
            sniffer = EitSniffer(PresentTracker(channel["uuid"], lines.append), clock=lambda: T0)
            sniffer.feed(packetize(0, build_pat({1: 0x1000}))[0] + out)
            slot = channel["_slot"]
            k = (T0 + 10 * 3600 + 5) // slot  # the programme that "really" started `delay` after its EPG start
            expected = self.fake.event_for(channel, k)
            self.assertEqual([(l["title"], l["eitStart"]) for l in lines],
                             [(expected["title"], eit.iso(expected["start"]))])

    def test_fourth_channel_has_no_eit(self):
        ts, out, _ = self.stream(3, T0)
        self.assertEqual(out, ts)


if __name__ == "__main__":
    unittest.main()
