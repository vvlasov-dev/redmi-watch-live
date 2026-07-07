"""Xiaomi activity-file history (SpO2 / stress / vitality / HR-summary / steps).

Port of Gadgetbridge's XiaomiActivityFileId + XiaomiActivityFileFetcher +
DailySummaryParser (AGPLv3) to standalone Python.

Flow (driven by client.py):
  1. client sends {8,1} fetch-today and {8,2} fetch-past
  2. watch replies (protobuf channel) with lists of 7-byte file IDs
  3. for each file ID client sends {8,3} request; the watch streams the file
     bytes over SPP activity channel 5 (encrypted) as numbered chunks
  4. chunks are reassembled here, CRC32-verified, parsed, and {8,5}-acked

The daily-summary file is a single fixed-offset record that yields, in one
shot, all the aggregate health tiles the dashboard shows.
"""
import struct
import zlib

# XiaomiActivityFileId type/subtype/detail codes
TYPE_ACTIVITY = 0
SUB_ACTIVITY_DAILY = 0x00
SUB_ACTIVITY_SLEEP_STAGES = 0x03
SUB_ACTIVITY_SLEEP = 0x08
DETAIL_DETAILS = 0
DETAIL_SUMMARY = 0x01

# sleep stage packet marker (bytes FB FA FC FF == 0xFFFCFAFB read little-endian)
_STAGE_MARKER = b"\xfb\xfa\xfc\xff"
_STAGE_NAMES = {0: "awake", 1: "light", 2: "deep", 3: "rem", 4: "awake"}  # decodeStage raw->name


def parse_file_id(b7: bytes) -> dict:
    """Decode a 7-byte XiaomiActivityFileId."""
    ts, tz, ver, flags = struct.unpack_from("<IBBB", b7, 0)
    return {
        "ts": ts,
        "tz": tz,
        "version": ver,
        "type": (flags >> 7) & 1,
        "subtype": (flags & 127) >> 2,
        "detail": flags & 3,
        "raw": bytes(b7[:7]),
    }


def is_daily_summary(fid: dict) -> bool:
    return (fid["type"] == TYPE_ACTIVITY and fid["subtype"] == SUB_ACTIVITY_DAILY
            and fid["detail"] == DETAIL_SUMMARY)


def is_daily_details(fid: dict) -> bool:
    return (fid["type"] == TYPE_ACTIVITY and fid["subtype"] == SUB_ACTIVITY_DAILY
            and fid["detail"] == DETAIL_DETAILS)


def is_sleep(fid: dict) -> bool:
    return fid["type"] == TYPE_ACTIVITY and fid["subtype"] == SUB_ACTIVITY_SLEEP


def parse_sleep(fid: dict, data: bytes):
    """Port of SleepDetailsParser (durations + stage timeline).

    bedTime/wakeupTime sit at a fixed early offset; the per-stage durations and
    the hypnogram come from marker-delimited packets (type 16 = summary,
    type 17 = stage transitions), which we locate by scanning for the
    FB FA FC FF marker rather than walking the fragile variable-length header.
    """
    ver = fid["version"]
    header_size = 2 if ver >= 5 else 1
    off = 7 + 1 + header_size  # fileId(7) + padding(1) + header
    if off + 9 > len(data):
        return None
    is_awake = data[off]; off += 1
    bed_ts = struct.unpack_from("<i", data, off)[0]; off += 4
    wake_ts = struct.unpack_from("<i", data, off)[0]; off += 4
    in_bed_min = max(0, (wake_ts - bed_ts) // 60) if (wake_ts and bed_ts) else 0

    asleep_min = None
    deep = light = rem = awake = None
    has_rem = has_stage = False
    stages = []

    # scan for stage packets.  The file accumulates a NEW copy of the summary
    # and stage packets on every mid-night sync, so only the LAST type-16 and
    # LAST type-17 packet reflect the night (summing all of them counted the
    # timeline 4-15x over: 2026-07-06 failure).
    i = data.find(_STAGE_MARKER)
    while i != -1 and i + 17 <= len(data):
        p = i + 4                      # after marker
        # header_len(1) ts(8) parity(1) type(1) dataLen(2 big-endian)
        p += 1
        p += 8                          # ts
        p += 1                          # parity
        if p + 3 > len(data):
            break
        ptype = data[p]; p += 1
        data_len = (data[p] << 8) | data[p + 1]; p += 2
        # flag-only packets carry no body
        if ptype in (0x2, 0x3, 0x9, 0xc, 0xd, 0xe, 0xf):
            i = data.find(_STAGE_MARKER, p)
            continue
        body = data[p:p + data_len]
        nxt = p + data_len
        if ptype == 16 and len(body) >= 13:
            # big-endian: data_0 u8, sleep,wake,light,rem,deep (u16 each), data_1 u8
            sleep_duration = struct.unpack_from(">H", body, 1)[0]
            wake_duration = struct.unpack_from(">H", body, 3)[0]
            light_duration = struct.unpack_from(">H", body, 5)[0]
            rem_duration = struct.unpack_from(">H", body, 7)[0]
            deep_duration = struct.unpack_from(">H", body, 9)[0]
            data_1 = body[11]
            # the watch SAYS whether this file carries REM/stage data at all —
            # without these flags a night the watch didn't stage shows rem=0,
            # which reads as a (false) fact
            has_rem = (data_1 >> 4) & 1 == 1
            has_stage = (data_1 >> 2) & 1 == 1
            asleep_min = sleep_duration
            deep, light, rem, awake = deep_duration, light_duration, rem_duration, wake_duration
        elif ptype == 17:
            pkt = []
            for k in range(0, (data_len // 2) * 2, 2):
                if k + 2 > len(body):
                    break
                val = struct.unpack_from(">H", body, k)[0]
                raw_stage = val >> 12
                offset_min = val & 0xFFF
                name = _STAGE_NAMES.get(raw_stage)
                if name and offset_min > 0:
                    pkt.append([name, offset_min])
            if pkt:
                stages = pkt            # REPLACE: the last packet wins
        i = data.find(_STAGE_MARKER, nxt if nxt > i else i + 4)

    # if no type-16 summary but we have stages, derive stage totals from timeline
    if asleep_min is None and stages:
        agg = {}
        for name, mins in stages:
            agg[name] = agg.get(name, 0) + mins
        deep = agg.get("deep", 0); light = agg.get("light", 0)
        rem = agg.get("rem", 0); awake = agg.get("awake", 0)
        asleep_min = deep + light + rem
        has_stage = True
        has_rem = rem > 0

    # honesty: when the watch flags "no REM data", zeros are absence, not facts
    if asleep_min is not None and not has_rem:
        if not rem:
            rem = None
        if not deep:
            deep = None

    total = asleep_min if asleep_min else in_bed_min
    return {
        "date_ts": fid["ts"],
        "bed_ts": bed_ts, "wake_ts": wake_ts,
        "in_bed_min": in_bed_min,
        "asleep_min": total,
        "deep_min": deep, "light_min": light, "rem_min": rem, "awake_min": awake,
        "stages": stages,
        "has_rem": has_rem, "has_stage": has_stage,
        "is_awake": bool(is_awake),
    }


def is_sleep_stages(fid: dict) -> bool:
    return fid["type"] == TYPE_ACTIVITY and fid["subtype"] == SUB_ACTIVITY_SLEEP_STAGES


_PHASE_NAMES = {0: "awake", 1: "light", 2: "deep", 3: "rem", 4: "awake"}


def parse_sleep_stages(fid: dict, data: bytes):
    """Port of SleepStagesParser (subtype 3, version 2): the dedicated stages
    file — authoritative deep/light/REM totals + [ts, phase] change samples."""
    if fid.get("version") != 2 or len(data) < 44:
        return None
    off = 7 + 1            # fileId + padding
    off += 7               # unk1
    sleep_duration = struct.unpack_from("<h", data, off)[0]; off += 2
    bed_ts = struct.unpack_from("<i", data, off)[0]; off += 4
    wake_ts = struct.unpack_from("<i", data, off)[0]; off += 4
    off += 3               # unk2
    deep = struct.unpack_from("<h", data, off)[0]; off += 2
    light = struct.unpack_from("<h", data, off)[0]; off += 2
    rem = struct.unpack_from("<h", data, off)[0]; off += 2
    awake = struct.unpack_from("<h", data, off)[0]; off += 2
    off += 1               # unk3
    if not bed_ts or not wake_ts or not sleep_duration:
        return None
    samples = []
    end = len(data) - 4    # trailing crc32
    while off + 5 <= end:
        ts = struct.unpack_from("<i", data, off)[0]; off += 4
        phase = data[off]; off += 1
        samples.append([ts, phase])
    # phase-change samples -> [name, minutes] blocks
    stages = []
    for i in range(len(samples)):
        ts, ph = samples[i]
        nxt = samples[i + 1][0] if i + 1 < len(samples) else wake_ts
        mins = max(0, (nxt - ts) // 60)
        name = _PHASE_NAMES.get(ph)
        if name and mins:
            stages.append([name, mins])
    return {
        "date_ts": fid["ts"],
        "bed_ts": bed_ts, "wake_ts": wake_ts,
        "in_bed_min": max(0, (wake_ts - bed_ts) // 60),
        "asleep_min": sleep_duration,
        "deep_min": deep, "light_min": light, "rem_min": rem, "awake_min": awake,
        "stages": stages,
        "has_rem": rem > 0, "has_stage": bool(stages), "src": "stages_file",
        "is_awake": True,
    }


def parse_daily_summary(fid: dict, data: bytes):
    """Port of DailySummaryParser.parse. Returns a dict of daily metrics or None.

    `data` is the full reassembled file: [0..6]=fileId, [7]=padding(0),
    then header, then the record, then trailing 4-byte CRC32.
    """
    ver = fid["version"]
    if ver == 3:
        header_size = 3
    elif ver == 5:
        header_size = 4
    else:
        return None

    off = 7 + 1 + header_size  # skip fileId(7) + padding(1) + header

    def u8():
        nonlocal off
        v = data[off]
        off += 1
        return v

    def i16():
        nonlocal off
        v = struct.unpack_from("<h", data, off)[0]
        off += 2
        return v

    def i32():
        nonlocal off
        v = struct.unpack_from("<i", data, off)[0]
        off += 4
        return v

    steps = i32()
    u8(); u8(); u8()                       # unk1..3
    hr_resting = u8()
    hr_max = u8(); hr_max_ts = i32()
    hr_min = u8(); hr_min_ts = i32()
    hr_avg = u8()
    stress_avg = u8(); stress_max = u8(); stress_min = u8()
    st = data[off:off + 3]; off += 3
    standing_mask = (st[0] | (st[1] << 8) | (st[2] << 16)) & 0xFFFFFF
    standing_hours = bin(standing_mask).count("1")
    calories = i16()
    u8(); u8(); u8()                       # unk7..9
    spo2_max = u8(); spo2_max_ts = i32()
    spo2_min = u8(); spo2_min_ts = i32()
    spo2_avg = u8()

    vitality = None
    training_load_day = training_load_week = None
    if ver > 3:
        training_load_day = i16()
        training_load_week = i16()
        u8()                               # trainingLoadLevel
        u8(); u8(); u8()                   # vitalityIncrease light/moderate/high
        vitality = i16()

    return {
        "date_ts": fid["ts"],
        "steps": steps,
        "calories": calories,
        "standing_hours": standing_hours,
        "standing_mask": standing_mask,
        "hr_resting": hr_resting, "hr_max": hr_max, "hr_min": hr_min, "hr_avg": hr_avg,
        "hr_max_ts": hr_max_ts, "hr_min_ts": hr_min_ts,
        "stress_avg": stress_avg, "stress_max": stress_max, "stress_min": stress_min,
        "spo2_max": spo2_max, "spo2_min": spo2_min, "spo2_avg": spo2_avg,
        "spo2_max_ts": spo2_max_ts, "spo2_min_ts": spo2_min_ts,
        "vitality": vitality,
        "training_load_day": training_load_day, "training_load_week": training_load_week,
    }


class _Complex:
    """Port of Gadgetbridge XiaomiComplexActivityParser: a header-nibble-driven
    bit-group reader over the daily-details minute records."""

    def __init__(self, header, buf, pos):
        self.header = header
        self.buf = buf
        self.pos = pos
        self.group = -1
        self.bits = 0
        self.val = 0

    def reset(self):
        self.group = -1
        self.bits = 0
        self.val = 0

    def _nibble(self):
        hb = self.group // 2
        if self.group % 2 == 0:
            return (self.header[hb] & 0xF0) >> 4
        return self.header[hb] & 0x0F

    def _consume(self, n):
        if n == 8:
            v = self.buf[self.pos]; self.pos += 1; return v
        if n == 16:
            v = self.buf[self.pos] | (self.buf[self.pos + 1] << 8); self.pos += 2; return v
        if n == 32:
            v = struct.unpack_from("<i", self.buf, self.pos)[0]; self.pos += 4; return v
        raise ValueError("bits %d" % n)

    def next_group(self, n):
        self.group += 1
        if self.group >= len(self.header) * 2:
            self._consume(n)
            return False
        if (self._nibble() & 8) == 0:
            return False
        self.bits = n
        self.val = self._consume(n)
        return (self._nibble() & 8) != 0

    def valid(self, idx):
        return (self._nibble() & (1 << (2 - idx))) != 0

    def get(self, idx, n):
        shift = self.bits - idx - n
        return (self.val & (((1 << n) - 1) << shift)) >> shift


def parse_daily_details(fid: dict, data: bytes):
    """Port of DailyDetailsParser: per-minute steps/HR/SpO2/stress for a day."""
    ver = fid["version"]
    header_size = {1: 4, 2: 4, 3: 5, 4: 6}.get(ver)
    if header_size is None:
        return None
    end = len(data) - 4  # drop trailing CRC
    pos = 7 + 1  # fileId + padding
    header = data[pos:pos + header_size]
    pos += header_size
    base = fid["ts"]
    cp = _Complex(header, data, pos)
    minutes = []
    minute = 0
    while cp.pos < end and minute < 1600:
        cp.reset()
        s = {"ts": base + minute * 60, "steps": None, "cal": None,
             "hr": None, "spo2": None, "stress": None}
        include_extra = 0
        if cp.next_group(16):
            if cp.valid(1):
                include_extra = cp.get(1, 1)
            if cp.valid(2):
                s["steps"] = cp.get(2, 14)
        if cp.next_group(8):
            if cp.valid(1):
                s["cal"] = cp.get(2, 6)
        cp.next_group(8)
        if cp.next_group(16):
            pass  # distance
        if cp.next_group(8):
            if cp.valid(0):
                s["hr"] = cp.get(0, 8)
        if cp.next_group(8):
            pass  # energy
        cp.next_group(16)
        if ver >= 3:
            if cp.next_group(8):
                if cp.valid(0):
                    s["spo2"] = cp.get(0, 8)
            if cp.next_group(8):
                if cp.valid(0):
                    st = cp.get(0, 8)
                    if st != 255:
                        s["stress"] = st
        if include_extra == 1:
            cp.pos += 1
        if ver >= 4:
            cp.next_group(16)
            cp.next_group(16)
        minutes.append(s)
        minute += 1
    return {"date_ts": fid["ts"], "minutes": minutes}


class ActivityFetcher:
    """Reassembles activity-file chunks and parses them.

    Callbacks provided by the caller (client.py):
      request_file(file_id_raw_7b) -> send {8,3} for that file
      ack_file(file_id_raw_7b)     -> send {8,5} ack
      on_daily(summary_dict)       -> a daily summary was parsed
      on_complete()                -> nothing left to fetch (sync finished)
    """

    def __init__(self, request_file, ack_file, on_daily, on_complete, on_sleep=None,
                 on_details=None, capture_dir=None, log=print):
        self.request_file = request_file
        self.ack_file = ack_file
        self.on_daily = on_daily
        self.on_complete = on_complete
        self.on_sleep = on_sleep or (lambda s: None)
        self.on_details = on_details or (lambda d: None)
        self.capture_dir = capture_dir  # if set, dump raw verified files as regression fixtures
        self.log = log
        self.queue = []
        self.buf = bytearray()
        self.fetching = False
        self.awaiting_past = False
        self.idle_hold = False
        self.current = None

    def busy(self):
        return self.fetching

    def handle_file_ids(self, subtype, ids_bytes):
        """subtype 1 = today response, 2 = past response."""
        if not ids_bytes or (len(ids_bytes) % 7) != 0:
            if ids_bytes:
                self.log("activity: bad fileId list length %d" % len(ids_bytes))
        else:
            for i in range(0, len(ids_bytes), 7):
                fid = parse_file_id(ids_bytes[i:i + 7])
                if fid["ts"] == 0 and fid["version"] == 0:
                    continue
                self.queue.append(fid)

        if subtype == 1:
            self.awaiting_past = True
        elif subtype == 2:
            self.awaiting_past = False

        if not self.fetching:
            self.fetching = True
            self._next()
        elif self.idle_hold:
            self.idle_hold = False
            self._next()

    def add_chunk(self, chunk: bytes):
        """A raw (decrypted) activity chunk: total u16 @0, num u16 @2, payload @4."""
        if len(chunk) < 4:
            return
        total = struct.unpack_from("<H", chunk, 0)[0]
        num = struct.unpack_from("<H", chunk, 2)[0]
        if num == 1:
            self.buf = bytearray()
        self.buf.extend(chunk[4:])
        if num != total:
            return

        data = bytes(self.buf)
        self.buf = bytearray()
        if len(data) < 13:
            self.log("activity: file too short (%d)" % len(data))
            self._next()
            return
        crc = zlib.crc32(data[:-4]) & 0xFFFFFFFF
        exp = struct.unpack_from("<I", data, len(data) - 4)[0]
        if crc != exp:
            self.log("activity: CRC mismatch %08X!=%08X" % (crc, exp))
            self._next()
            return

        fid = parse_file_id(data[:7])
        self._capture(fid, data)
        try:
            self.ack_file(fid["raw"])
        except Exception as e:
            self.log("activity: ack error %s" % e)
        try:
            if is_daily_summary(fid):
                s = parse_daily_summary(fid, data)
                if s:
                    self.on_daily(s)
            elif is_sleep(fid):
                s = parse_sleep(fid, data)
                if s:
                    self.on_sleep(s)
            elif is_sleep_stages(fid):
                s = parse_sleep_stages(fid, data)
                if s:
                    self.on_sleep(s)   # authoritative stages record, same sink
            elif is_daily_details(fid):
                s = parse_daily_details(fid, data)
                if s:
                    self.on_details(s)
        except Exception as e:
            self.log("activity: parse error %s" % e)
        self._next()

    def _capture(self, fid, data):
        """Persist the latest verified raw file per (type,subtype,detail,version)
        so we have real-hardware regression fixtures. Overwrites, so it stays tiny."""
        if not self.capture_dir:
            return
        try:
            import os
            os.makedirs(self.capture_dir, exist_ok=True)
            name = "t%d_s%02d_d%d_v%d.bin" % (fid["type"], fid["subtype"], fid["detail"], fid["version"])
            with open(os.path.join(self.capture_dir, name), "wb") as f:
                f.write(data)
        except Exception as e:
            self.log("activity: capture error %s" % e)

    def _next(self):
        self.buf = bytearray()
        if not self.queue:
            if self.awaiting_past:
                self.idle_hold = True
                return
            self._complete()
            return
        self.current = self.queue.pop(0)
        try:
            self.request_file(self.current["raw"])
        except Exception as e:
            self.log("activity: request error %s" % e)
            self._next()

    def _complete(self):
        if not self.fetching:
            return  # already completed; keep on_complete idempotent
        self.fetching = False
        self.idle_hold = False
        self.current = None
        try:
            self.on_complete()
        except Exception as e:
            self.log("activity: complete cb error %s" % e)
