"""Real programme starts from the broadcast: EIT present/following (spec/dvb-gateway.md "Real programme starts").

Phase 1 only measures. The recorder's pump hands every chunk of TVHeadend's MPEG-TS to an ``EitSniffer``, which
looks at the 4-byte TS headers only and copies just the packets of PID 0x00 (PAT) and 0x12 (EIT), a few per second.
From the EIT p/f actual table (``table_id 0x4E``, section 0 = the present event) of our own service, a
``PresentTracker`` notes every change of the present event in the ``EitStore`` (``<state_dir>/eit-starts.jsonl``).
Everything a broadcaster sends is parsed defensively: bad CRC, odd lengths, unknown charsets never raise.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Callable, Iterable

TS = 188
SYNC = 0x47
PAT_PID, EIT_PID = 0x00, 0x12
PAT_TABLE, EIT_PF_ACTUAL = 0x00, 0x4E
MAX_SECTION = 4096
SHORT_EVENT = 0x4D
NO_EIT_SECONDS = 300.0  # a stream without our present event this long is logged once and marked in the store
FILE = "eit-starts.jsonl"
MAX_CHANNEL_BYTES = 2 * 1024 * 1024
PRUNE_SECONDS = 3600.0
PRUNE_BYTES = 256 * 1024  # appended since the last prune; the bound holds between hourly prunes too
MJD_EPOCH = date(1858, 11, 17)
PREFERRED_LANGUAGES = ("ces", "cze")


# -- CRC and times ------------------------------------------------------------------------------------------------
def _crc_table() -> list[int]:
    table = []
    for i in range(256):
        c = i << 24
        for _ in range(8):
            c = ((c << 1) ^ 0x04C11DB7) if c & 0x80000000 else (c << 1)
        table.append(c & 0xFFFFFFFF)
    return table


_CRC = _crc_table()


def crc32_mpeg(data: bytes) -> int:
    """CRC-32/MPEG-2 (poly 0x04C11DB7, init 0xFFFFFFFF, not reflected); 0 over a section including its CRC."""
    crc = 0xFFFFFFFF
    for b in data:
        crc = ((crc << 8) & 0xFFFFFFFF) ^ _CRC[((crc >> 24) ^ b) & 0xFF]
    return crc


def _bcd(b: int) -> int | None:
    hi, lo = b >> 4, b & 0x0F
    return None if hi > 9 or lo > 9 else hi * 10 + lo


def bcd_seconds(data: bytes) -> int | None:
    """hh mm ss in BCD (EN 300 468 duration / UTC part of start_time); None when not valid BCD."""
    parts = [_bcd(b) for b in data[:3]]
    if len(parts) < 3 or None in parts or parts[1] > 59 or parts[2] > 59:
        return None
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def mjd_date(mjd: int) -> date:
    return date.fromordinal(MJD_EPOCH.toordinal() + mjd)


def start_epoch(data: bytes) -> int | None:
    """40-bit start_time: 16-bit MJD + 24-bit BCD UTC. All ones = undefined (None)."""
    if len(data) < 5 or data[:5] == b"\xff" * 5:
        return None
    seconds = bcd_seconds(data[2:5])
    if seconds is None or seconds >= 86400:
        return None
    try:
        day = mjd_date((data[0] << 8) | data[1])
    except (ValueError, OverflowError):
        return None
    return int(datetime(day.year, day.month, day.day, tzinfo=timezone.utc).timestamp()) + seconds


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(int(epoch), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- DVB text (EN 300 468 Annex A) --------------------------------------------------------------------------------
ISO8859 = {0x01: 5, 0x02: 6, 0x03: 7, 0x04: 8, 0x05: 9, 0x06: 10, 0x07: 11, 0x09: 13, 0x0A: 14, 0x0B: 15}
# ISO/IEC 6937 (the default table): 0xC1..0xCF are non-spacing diacritics placed before the letter
DIACRITICS = {0xC1: "\u0300", 0xC2: "\u0301", 0xC3: "\u0302", 0xC4: "\u0303", 0xC5: "\u0304", 0xC6: "\u0306",
              0xC7: "\u0307", 0xC8: "\u0308", 0xCA: "\u030a", 0xCB: "\u0327", 0xCD: "\u030b", 0xCE: "\u0328",
              0xCF: "\u030c"}
ISO6937 = dict(zip(range(0xA0, 0x100), (
    "\u00a0¡¢£$¥#§¤‘“«←↑→↓"
    "°±²³×µ¶·÷’”»¼½¾¿"
    "\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd\ufffd"
    "―¹®©™♪¬¦\ufffd\ufffd\ufffd\ufffd⅛⅜⅝⅞"
    "ΩÆĐªĦ\ufffdĲĿŁØŒºÞŦŊŉ"
    "ĸæđðħıĳŀłøœßþŧŋ\u00ad")))
CONTROL_RE = re.compile("[\u0080-\u009f\ue080-\ue09f]")  # DVB emphasis/line-break codes (also in Unicode tables)


def decode_text(data: bytes) -> str:
    """A DVB string to text: leading charset byte(s) or the ISO 6937 default; never raises."""
    if not data:
        return ""
    first = data[0]
    try:
        if first >= 0x20:
            text = _decode_6937(data)
        elif first in ISO8859:
            text = data[1:].decode(f"iso8859-{ISO8859[first]}", "replace")
        elif first == 0x10 and len(data) >= 3:
            part = (data[1] << 8) | data[2]
            text = data[3:].decode(f"iso8859-{part}", "replace") if 1 <= part <= 16 and part != 12 \
                else data[3:].decode("latin-1")
        elif first in (0x11, 0x14):
            text = data[1:len(data) - (len(data) - 1) % 2].decode("utf-16-be", "replace")
        elif first == 0x13:
            text = data[1:].decode("gb2312", "replace")
        elif first == 0x12:
            text = data[1:].decode("euc-kr", "replace")
        elif first == 0x15:
            text = data[1:].decode("utf-8", "replace")
        else:  # 0x1F (compressed) and reserved values: unknown, keep what is plainly readable
            text = _decode_6937(data[1:])
    except (LookupError, ValueError):
        text = data.decode("latin-1")
    return " ".join(CONTROL_RE.sub(" ", text).split())


def _decode_6937(data: bytes) -> str:
    out, mark = [], ""
    for b in data:
        if b in DIACRITICS:
            mark = DIACRITICS[b]
            continue
        ch = chr(b) if b < 0xA0 else ISO6937.get(b, "\ufffd")
        out.append(unicodedata.normalize("NFC", ch + mark) if mark else ch)
        mark = ""
    return "".join(out)


def encode_6937(text: str) -> bytes:
    """Czech text in the ISO 6937 default table (for tests and the fake TVHeadend)."""
    out = bytearray()
    reverse = {v: k for k, v in DIACRITICS.items()}
    for ch in text:
        if ord(ch) < 0x80:
            out.append(ord(ch))
            continue
        base, *marks = unicodedata.normalize("NFD", ch)
        if len(marks) == 1 and marks[0] in reverse and ord(base) < 0x80:
            out += bytes((reverse[marks[0]], ord(base)))
        else:
            out.append(ord("?"))
    return bytes(out)


# -- sections -----------------------------------------------------------------------------------------------------
@dataclass
class Event:
    event_id: int
    start: int | None  # epoch seconds UTC as the broadcaster states it
    duration: int | None
    running: int  # running_status: 0 undefined, 1 not running, 2 starts soon, 3 pausing, 4 running
    title: str


@dataclass
class PfSection:
    service_id: int
    section_number: int  # 0 = present, 1 = following
    events: list[Event]


def parse_pat(section: bytes) -> dict[int, int] | None:
    """{program_number: PMT PID} of a current PAT (CRC already checked), without the network PID."""
    if len(section) < 12 or section[0] != PAT_TABLE or not section[5] & 0x01:
        return None
    programs = {}
    for i in range(8, len(section) - 4 - 3, 4):
        number = (section[i] << 8) | section[i + 1]
        if number:
            programs[number] = ((section[i + 2] & 0x1F) << 8) | section[i + 3]
    return programs


def parse_eit_pf(section: bytes) -> PfSection | None:
    """EIT present/following actual (CRC already checked); None for anything else or a malformed section."""
    if len(section) < 18 or section[0] != EIT_PF_ACTUAL or not section[5] & 0x01:
        return None
    service_id = (section[3] << 8) | section[4]
    events, i, end = [], 14, len(section) - 4
    while i + 12 <= end:
        event_id = (section[i] << 8) | section[i + 1]
        start = start_epoch(section[i + 2:i + 7])
        duration = None if section[i + 7:i + 10] == b"\xff\xff\xff" else bcd_seconds(section[i + 7:i + 10])
        running = section[i + 10] >> 5
        loop = ((section[i + 10] & 0x0F) << 8) | section[i + 11]
        descriptors = section[i + 12:min(i + 12 + loop, end)]
        events.append(Event(event_id, start, duration, running, _event_name(descriptors)))
        i += 12 + loop
    return PfSection(service_id, section[6], events)


def _event_name(descriptors: bytes) -> str:
    names: list[tuple[str, str]] = []
    i = 0
    while i + 2 <= len(descriptors):
        tag, length = descriptors[i], descriptors[i + 1]
        body = descriptors[i + 2:i + 2 + length]
        if tag == SHORT_EVENT and len(body) >= 4:
            language = body[:3].decode("latin-1").lower()
            names.append((language, decode_text(body[4:4 + body[3]])))
        i += 2 + length
    for language, name in names:
        if language in PREFERRED_LANGUAGES and name:
            return name
    return next((name for _, name in names if name), "")


# -- TS packets ---------------------------------------------------------------------------------------------------
class _Assembler:
    """The section in progress on one PID: its first 3 bytes, then the bytes (wanted table) or just a count."""
    __slots__ = ("cc", "active", "head", "total", "got", "keep", "buf")

    def __init__(self) -> None:
        self.cc: int | None = None
        self.active = False
        self.head = b""
        self.total = self.got = 0
        self.keep = False
        self.buf = bytearray()

    def begin(self, keep: bool) -> None:
        self.active, self.head, self.total, self.got, self.keep = True, b"", 0, 0, keep
        self.buf = bytearray()


class TsReader:
    """Complete, CRC-checked sections of the wanted (PID, table_id) pairs from TS chunks cut anywhere.

    Only the TS header of every packet is looked at; packets of other PIDs are skipped without a copy. PIDs in
    ``watch`` are only noted in ``seen`` (which PMT the stream really carries)."""

    def __init__(self, tables: dict[int, int]):
        self.tables = tables
        self.watch: set[int] = set()
        self.seen: set[int] = set()
        self.crc_errors = 0
        self._tail = b""
        self._asm = {pid: _Assembler() for pid in tables}

    def feed(self, chunk: bytes) -> list[tuple[int, bytes]]:
        out: list[tuple[int, bytes]] = []
        data, i = chunk, 0
        if self._tail:
            if self._tail[0] == SYNC and len(self._tail) + len(chunk) >= TS:
                need = TS - len(self._tail)
                packet = self._tail + chunk[:need]  # the one packet cut by the chunk boundary
                pid = ((packet[1] & 0x1F) << 8) | packet[2]
                if pid in self.tables:
                    self._packet(packet, out)
                elif pid in self.watch:
                    self.seen.add(pid)
                i = need
            else:  # still short, or out of sync: join (small and rare)
                data = self._tail + chunk
            self._tail = b""
        n = len(data)
        tables, watch = self.tables, self.watch
        while i + TS <= n:
            if data[i] != SYNC:
                i = self._resync(data, i)
                if i < 0:
                    self._tail = bytes(data[max(0, n - TS + 1):])
                    return out
                continue
            pid = ((data[i + 1] & 0x1F) << 8) | data[i + 2]
            if pid in tables:
                self._packet(data[i:i + TS], out)
            elif pid in watch:
                self.seen.add(pid)
            i += TS
        self._tail = bytes(data[i:])
        return out

    @staticmethod
    def _resync(data: bytes, i: int) -> int:
        n = len(data)
        k = data.find(b"\x47", i + 1)
        while k >= 0:
            if k + TS >= n or data[k + TS] == SYNC:
                return k
            k = data.find(b"\x47", k + 1)
        return -1

    def _packet(self, p: bytes, out: list[tuple[int, bytes]]) -> None:
        if p[1] & 0x80:  # transport_error_indicator
            return
        pid = ((p[1] & 0x1F) << 8) | p[2]
        asm = self._asm[pid]
        control, cc = (p[3] >> 4) & 0x3, p[3] & 0x0F
        if not control & 0x1:  # no payload
            return
        if asm.cc is not None:
            if cc == asm.cc:  # duplicate packet
                return
            if cc != (asm.cc + 1) & 0x0F:
                asm.active = False  # lost a packet: the section in progress is incomplete
        asm.cc = cc
        offset = 4
        if control == 0x3:
            offset += 1 + p[4]
        if offset >= TS:
            return
        payload = p[offset:]
        if p[1] & 0x40:  # payload_unit_start: pointer_field, then the tail of the previous section
            pointer = payload[0]
            if 1 + pointer > len(payload):
                asm.active = False
                return
            if asm.active:
                self._take(pid, asm, payload[1:1 + pointer], out)
            asm.active = False
            pos = 1 + pointer
            while pos < len(payload) and payload[pos] != 0xFF:
                asm.begin(payload[pos] == self.tables[pid])
                pos += self._take(pid, asm, payload[pos:], out)
                if asm.active:
                    break  # continues in the next packet
        elif asm.active:
            self._take(pid, asm, payload, out)

    def _take(self, pid: int, asm: _Assembler, data: bytes, out: list[tuple[int, bytes]]) -> int:
        """Add bytes to the section in progress; returns how many belonged to it."""
        used = 0
        if not asm.total:
            used = min(3 - len(asm.head), len(data))
            asm.head += data[:used]
            if len(asm.head) < 3:
                return used
            asm.total = 3 + (((asm.head[1] & 0x0F) << 8) | asm.head[2])
            if asm.total > MAX_SECTION:
                asm.active = False
                return len(data)
            asm.got = 3
            if asm.keep:
                asm.buf += asm.head
        part = data[used:used + asm.total - asm.got]
        asm.got += len(part)
        if asm.keep:
            asm.buf += part
        if asm.got >= asm.total:
            asm.active = False
            if asm.keep:
                section = bytes(asm.buf)
                if crc32_mpeg(section) == 0:
                    out.append((pid, section))
                else:
                    self.crc_errors += 1
        return used + len(part)


# -- per channel: what is present ---------------------------------------------------------------------------------
class PresentTracker:
    """Notes every change of a channel's present event (across recorder runs) and the absence of EIT."""

    def __init__(self, channel: str, write: Callable[[dict], None], log: Callable[[str], None] = print):
        self.channel, self.write, self.log = channel, write, log
        self._last: tuple | None = None
        self._run_started: float | None = None
        self._fresh = True
        self._found = False
        self._warned = False
        self.others: set[int] = set()

    def new_run(self) -> None:
        """A new TVHeadend stream: the first present event seen was already running when we tuned."""
        self._fresh, self._run_started, self._found = True, None, False
        self.others = set()

    def section(self, pf: PfSection, own: int | None, now: float) -> None:
        if own is None or pf.service_id != own:
            self.others.add(pf.service_id)
            return
        if pf.section_number != 0:
            return
        self._found = True
        present = pf.events[0] if pf.events else None
        if present is None:  # between events: nothing present, no line
            return
        key = (present.event_id, present.start, present.duration, present.running)
        fresh, self._fresh = self._fresh, False
        if key == self._last:
            return
        self._last = key
        record = {"channel": self.channel, "eventId": present.event_id}
        if present.title:
            record["title"] = present.title
        record.update({"eitStart": iso(present.start) if present.start is not None else None,
                       "eitDuration": present.duration, "seenAt": iso(now), "running": present.running})
        if fresh:
            record["atTune"] = True  # seenAt is when we tuned, not when the event began
        self.write(record)

    def tick(self, now: float, own: int | None) -> None:
        if self._run_started is None:
            self._run_started = now
        if self._found or self._warned or now - self._run_started < NO_EIT_SECONDS:
            return
        self._warned = True
        if not self.others:
            reason = "none"  # no EIT p/f of any service: not sent, or TVHeadend left it out
        elif own is None:
            reason = "unknownService"  # EIT of several services and the PAT does not tell which is ours
        else:
            reason = "otherServices"  # EIT only of other services (rewritten service id?)
        self.log(f"dvb: eit: no present/following on {self.channel} ({reason})")
        record = {"channel": self.channel, "seenAt": iso(now), "noEit": reason}
        if self.others:
            record["services"] = sorted(self.others)
        if own is not None:
            record["serviceId"] = own
        self.write(record)


class EitSniffer:
    """One recorder run: TS chunks → PAT (which service is ours) and EIT p/f → the channel's tracker."""

    def __init__(self, tracker: PresentTracker, clock: Callable[[], float] = time.time):
        self.tracker, self.clock = tracker, clock
        self.reader = TsReader({PAT_PID: PAT_TABLE, EIT_PID: EIT_PF_ACTUAL})
        self.programs: dict[int, int] = {}
        tracker.new_run()

    def own_service(self) -> int | None:
        """TVHeadend's pass profile rewrites the PAT to our one service; with an unrewritten PAT the service
        whose PMT really appears in the stream."""
        if len(self.programs) == 1:
            return next(iter(self.programs))
        present = [sid for sid, pmt in self.programs.items() if pmt in self.reader.seen]
        return present[0] if len(present) == 1 else None

    def feed(self, chunk: bytes) -> None:
        now = self.clock()
        for pid, section in self.reader.feed(chunk):
            if pid == PAT_PID:
                programs = parse_pat(section)
                if programs:
                    self.programs = programs
                    self.reader.watch = set(programs.values()) if len(programs) > 1 else set()
            else:
                pf = parse_eit_pf(section)
                if pf is not None:
                    self.tracker.section(pf, self.own_service(), now)
        self.tracker.tick(now, self.own_service())


# -- the store ----------------------------------------------------------------------------------------------------
class EitStore:
    """``<state_dir>/eit-starts.jsonl``: one JSON line per change, at most ``max_days`` and 2 MB per channel."""

    def __init__(self, state_dir: Path, max_days: Callable[[], int], now: Callable[[], float] = time.time,
                 log: Callable[[str], None] = print, max_channel_bytes: int = MAX_CHANNEL_BYTES):
        self.path = Path(state_dir) / FILE
        self.max_days, self.now, self.log = max_days, now, log
        self.max_channel_bytes = max_channel_bytes
        self._lock = threading.Lock()
        self._pruned_at = float("-inf")
        self._appended = 0
        self._failed = False

    def append(self, record: dict) -> None:
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._lock:
            try:
                if self.now() - self._pruned_at >= PRUNE_SECONDS or self._appended >= PRUNE_BYTES:
                    self._prune()
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as f:
                    f.write(line)  # one write of one line: never interleaved with another channel's
                self._appended += len(line.encode("utf-8"))
                self._failed = False
            except OSError as exc:
                if not self._failed:
                    self.log(f"dvb: eit: cannot write {self.path}: {exc}")
                self._failed = True

    def prune(self) -> None:
        with self._lock:
            self._prune()

    def _prune(self) -> None:
        self._pruned_at, self._appended = self.now(), 0
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return
        oldest = iso(self.now() - max(1, min(14, int(self.max_days()))) * 86400)
        kept: list[str] = []
        used: dict[str, int] = {}
        for line in reversed(lines):  # newest first, so a full channel drops its oldest lines
            record = _record(line)
            if record is None or str(record.get("seenAt", "")) < oldest:
                continue
            channel = str(record.get("channel", ""))
            size = len(line.encode("utf-8")) + 1
            if used.get(channel, 0) + size > self.max_channel_bytes:
                continue
            used[channel] = used.get(channel, 0) + size
            kept.append(line)
        if len(kept) == len(lines):
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(line + "\n" for line in reversed(kept)), encoding="utf-8")
        os.replace(tmp, self.path)

    def read(self) -> list[dict]:
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError:
            return []
        return [r for r in map(_record, text.splitlines()) if r is not None]


def _record(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None  # e.g. a line cut by a power loss
    return value if isinstance(value, dict) and isinstance(value.get("channel"), str) else None


# -- building sections (tests and the fake TVHeadend) -------------------------------------------------------------
def build_section(table_id: int, extension: int, body: bytes, version: int = 0, section_number: int = 0,
                  last_section_number: int = 0) -> bytes:
    """A long-form PSI/SI section with its CRC."""
    length = 5 + len(body) + 4
    head = bytes((table_id, 0xF0 | (length >> 8), length & 0xFF, extension >> 8, extension & 0xFF,
                  0xC1 | (version & 0x1F) << 1, section_number, last_section_number))
    section = head + body
    return section + crc32_mpeg(section).to_bytes(4, "big")


def build_pat(programs: dict[int, int], ts_id: int = 1) -> bytes:
    body = b"".join(n.to_bytes(2, "big") + (0xE000 | pid).to_bytes(2, "big") for n, pid in programs.items())
    return build_section(PAT_TABLE, ts_id, body)


def encode_start(epoch: int) -> bytes:
    moment = datetime.fromtimestamp(epoch, timezone.utc)
    mjd = moment.date().toordinal() - MJD_EPOCH.toordinal()
    return mjd.to_bytes(2, "big") + encode_bcd(moment.hour * 3600 + moment.minute * 60 + moment.second)


def encode_bcd(seconds: int) -> bytes:
    h, m, s = seconds // 3600, seconds // 60 % 60, seconds % 60
    return bytes(((v // 10) << 4 | v % 10) for v in (h, m, s))


def build_eit_pf(service_id: int, section_number: int, events: Iterable[tuple[int, int, int, int, bytes]],
                 version: int = 0, ts_id: int = 1, network_id: int = 0x20CB) -> bytes:
    """EIT p/f actual; events are (event_id, start epoch, duration s, running_status, encoded name)."""
    body = bytearray(ts_id.to_bytes(2, "big") + network_id.to_bytes(2, "big") + bytes((1, EIT_PF_ACTUAL)))
    for event_id, start, duration, running, name in events:
        short = b"ces" + bytes((len(name),)) + name + b"\x00"
        descriptors = bytes((SHORT_EVENT, len(short))) + short
        body += event_id.to_bytes(2, "big") + encode_start(start) + encode_bcd(duration)
        body += bytes(((running << 5) | (len(descriptors) >> 8), len(descriptors) & 0xFF)) + descriptors
    return build_section(EIT_PF_ACTUAL, service_id, bytes(body), version, section_number, 1)


def packetize(pid: int, section: bytes, cc: int = 0) -> tuple[bytes, int]:
    """TS packets carrying one section (pointer_field 0, 0xFF stuffing); returns (packets, next cc)."""
    out = bytearray()
    payload = b"\x00" + section
    first = True
    while payload:
        part, payload = payload[:TS - 4], payload[TS - 4:]
        out += bytes((SYNC, (0x40 if first else 0) | pid >> 8, pid & 0xFF, 0x10 | cc))
        out += part + b"\xff" * (TS - 4 - len(part))
        cc, first = (cc + 1) & 0x0F, False
    return bytes(out), cc
