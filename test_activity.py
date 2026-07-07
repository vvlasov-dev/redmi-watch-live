"""Regression tests for the activity-file parsers (no hardware needed).

Two layers:
  1. Synthetic fixtures for the fixed-offset parsers (fileId, daily summary,
     sleep) + the chunk fetcher (assembly, CRC32, callbacks, rejection paths).
  2. Real-hardware regression: if captures/*.bin exist (dumped by the running
     service), parse each and assert it doesn't crash and values are in range.
     This covers the bit-stream details parser, which is unsafe to hand-encode.

Run:  python test_activity.py
"""
import os
import struct
import zlib

import activity as A

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"[ OK ] {name}")
    else:
        fail += 1
        print(f"[FAIL] {name} {extra}")


def file_id(ts, tz, ver, typ, sub, detail):
    flags = ((typ & 1) << 7) | ((sub & 0x1F) << 2) | (detail & 3)
    return struct.pack("<IBBB", ts, tz, ver, flags)


def with_crc(fid, body):
    data = fid + bytes(body)
    return data + struct.pack("<I", zlib.crc32(data) & 0xFFFFFFFF)


# ---------------- fileId roundtrip ----------------
raw = file_id(1782800000, 8, 5, typ=0, sub=8, detail=1)
fid = A.parse_file_id(raw)
check("fileId roundtrip", fid["ts"] == 1782800000 and fid["tz"] == 8 and fid["version"] == 5
      and fid["type"] == 0 and fid["subtype"] == 8 and fid["detail"] == 1, fid)
check("is_sleep classify", A.is_sleep(A.parse_file_id(file_id(1, 0, 5, 0, 8, 0))))
check("is_daily_summary classify", A.is_daily_summary(A.parse_file_id(file_id(1, 0, 5, 0, 0, 1))))
check("is_daily_details classify", A.is_daily_details(A.parse_file_id(file_id(1, 0, 4, 0, 0, 0))))


# ---------------- daily summary (v5) ----------------
def build_daily(steps, hr_rest, hr_max, hr_min, hr_avg, s_avg, s_max, s_min,
                standing_mask, calories, sp_max, sp_min, sp_avg, vitality):
    fid = file_id(1782800000, 8, 5, 0, 0, 1)
    b = bytearray()
    b += b"\x00"                       # padding
    b += b"\x00\x00\x00\x00"           # header (4 for v5)
    b += struct.pack("<i", steps)
    b += bytes([0, 0, 0])              # unk1..3
    b += bytes([hr_rest, hr_max]) + struct.pack("<i", 1782800100)
    b += bytes([hr_min]) + struct.pack("<i", 1782800200)
    b += bytes([hr_avg, s_avg, s_max, s_min])
    b += struct.pack("<I", standing_mask)[:3]
    b += struct.pack("<h", calories)
    b += bytes([0, 0, 0])              # unk7..9
    b += bytes([sp_max]) + struct.pack("<i", 1782800300)
    b += bytes([sp_min]) + struct.pack("<i", 1782800400)
    b += bytes([sp_avg])
    b += struct.pack("<h", 12) + struct.pack("<h", 34) + bytes([1, 2, 3, 4]) + struct.pack("<h", vitality)
    return with_crc(fid, b)


data = build_daily(8423, 58, 141, 54, 72, 36, 88, 12, 0b111011, 648, 99, 94, 97, 82)
s = A.parse_daily_summary(A.parse_file_id(data[:7]), data)
check("daily steps", s["steps"] == 8423)
check("daily hr avg/max/min/rest", (s["hr_avg"], s["hr_max"], s["hr_min"], s["hr_resting"]) == (72, 141, 54, 58))
check("daily stress avg/max/min", (s["stress_avg"], s["stress_max"], s["stress_min"]) == (36, 88, 12))
check("daily calories", s["calories"] == 648)
check("daily spo2 avg/max/min", (s["spo2_avg"], s["spo2_max"], s["spo2_min"]) == (97, 99, 94))
check("daily vitality", s["vitality"] == 82)
check("daily standing hours (bit count)", s["standing_hours"] == bin(0b111011).count("1"))


# ---------------- sleep (v5) ----------------
def stage_packet(ts, ptype, body):
    return (b"\xfb\xfa\xfc\xff" + bytes([17]) + struct.pack("<q", ts) +
            bytes([0, ptype]) + bytes([(len(body) >> 8) & 0xFF, len(body) & 0xFF]) + body)


def build_sleep(bed, wake, deep, light, rem, awake, stages):
    fid = file_id(1782800000, 8, 5, 0, 8, 0)
    b = bytearray()
    b += b"\x00" + b"\x00\x00"         # padding + header(2 for v5)
    b += bytes([0])                    # isAwake
    b += struct.pack("<i", bed) + struct.pack("<i", wake)
    summ = bytes([0x10]) + struct.pack(">HHHHH", deep + light + rem, awake, light, rem, deep) + bytes([0x10, 0])
    b += stage_packet(bed, 16, summ)
    stg = b"".join(struct.pack(">H", (st << 12) | mn) for st, mn in stages)
    b += stage_packet(bed, 17, stg)
    return with_crc(fid, b)


sd = build_sleep(1782760000, 1782784800, deep=90, light=210, rem=70, awake=28,
                 stages=[(2, 40), (1, 60), (3, 25), (1, 55), (2, 50), (0, 10), (1, 40)])
sl = A.parse_sleep(A.parse_file_id(sd[:7]), sd)
check("sleep durations", (sl["deep_min"], sl["light_min"], sl["rem_min"], sl["awake_min"]) == (90, 210, 70, 28))
check("sleep asleep total", sl["asleep_min"] == 370)
check("sleep in-bed minutes", sl["in_bed_min"] == (1782784800 - 1782760000) // 60)
check("sleep stage timeline", sl["stages"][0] == ["deep", 40] and len(sl["stages"]) == 7)


# ---- mid-night syncs duplicate packets: LAST type17 must win (2026-07-06 bug) ----
def build_sleep_dup(bed, wake):
    fid = file_id(1782800000, 8, 5, 0, 8, 0)
    b = bytearray()
    b += b"\x00" + b"\x00\x00" + bytes([0])
    b += struct.pack("<i", bed) + struct.pack("<i", wake)
    summ1 = bytes([0x10]) + struct.pack(">HHHHH", 100, 5, 60, 20, 20) + bytes([0x14, 0])
    stg1 = b"".join(struct.pack(">H", (st << 12) | mn) for st, mn in [(1, 60), (2, 20), (3, 20)])
    b += stage_packet(bed, 16, summ1) + stage_packet(bed, 17, stg1)
    summ2 = bytes([0x10]) + struct.pack(">HHHHH", 200, 10, 120, 40, 40) + bytes([0x14, 0])
    stg2 = b"".join(struct.pack(">H", (st << 12) | mn) for st, mn in [(1, 120), (2, 40), (3, 40)])
    b += stage_packet(bed, 16, summ2) + stage_packet(bed, 17, stg2)
    return with_crc(fid, b)


sd2 = A.parse_sleep(A.parse_file_id(build_sleep_dup(1782760000, 1782784800)[:7]),
                    build_sleep_dup(1782760000, 1782784800))
check("dup syncs: last summary wins", sd2["asleep_min"] == 200 and sd2["rem_min"] == 40)
check("dup syncs: last timeline wins (no summing)",
      sum(m for _, m in sd2["stages"]) == 200 and len(sd2["stages"]) == 3)


# ---- has_rem=0: zeros are ABSENCE, not facts ----
def build_sleep_norem(bed, wake):
    fid = file_id(1782800000, 8, 5, 0, 8, 0)
    b = bytearray()
    b += b"\x00" + b"\x00\x00" + bytes([0])
    b += struct.pack("<i", bed) + struct.pack("<i", wake)
    summ = bytes([0x1e]) + struct.pack(">HHHHH", 600, 39, 600, 0, 0) + bytes([0x06, 0])
    b += stage_packet(bed, 16, summ)
    return with_crc(fid, b)


sd3 = A.parse_sleep(A.parse_file_id(build_sleep_norem(1782760000, 1782796000)[:7]),
                    build_sleep_norem(1782760000, 1782796000))
check("no-REM flag: rem is None (not 0)", sd3["rem_min"] is None and sd3["deep_min"] is None)
check("no-REM flag: has_rem False, asleep kept", sd3["has_rem"] is False and sd3["asleep_min"] == 600)


# ---------------- sleep stages file (subtype 3, v2) ----------------
def build_stages_file(bed, wake):
    fid = file_id(1782800000, 8, 2, 0, 3, 0)   # subtype 3, version 2
    b = bytearray()
    b += b"\x00"                       # padding
    b += b"\xff\xff" + b"\x00" * 5     # unk1
    b += struct.pack("<h", 370)        # sleepDuration
    b += struct.pack("<i", bed) + struct.pack("<i", wake)
    b += b"\x00" * 3                   # unk2
    b += struct.pack("<hhhh", 90, 210, 70, 15)  # deep light rem awake
    b += b"\x00"                       # unk3
    for i, ph in enumerate([1, 2, 1, 3, 0, 1]):
        b += struct.pack("<i", bed + i * 3600) + bytes([ph])
    return with_crc(fid, b)


fid3 = A.parse_file_id(build_stages_file(1782760000, 1782781600)[:7])
check("stages fid classify", A.is_sleep_stages(fid3))
st3 = A.parse_sleep_stages(fid3, build_stages_file(1782760000, 1782781600))
check("stages file: totals", (st3["deep_min"], st3["light_min"], st3["rem_min"]) == (90, 210, 70))
check("stages file: timeline blocks", st3["stages"][0] == ["light", 60] and st3["stages"][1] == ["deep", 60])
check("stages file: authoritative flags", st3["has_rem"] is True and st3["src"] == "stages_file")


# ---------------- fetcher: chunk assembly + CRC + callbacks ----------------
got = {}
f = A.ActivityFetcher(
    request_file=lambda r: got.setdefault("req", []).append(r),
    ack_file=lambda r: got.setdefault("ack", []).append(r),
    on_daily=lambda x: got.__setitem__("daily", x),
    on_complete=lambda: got.__setitem__("done", True),
    log=lambda *a: None,
)
f.handle_file_ids(1, A.parse_file_id(data[:7])["raw"])  # today: one summary file
check("fetcher requested the file", bool(got.get("req")))
half = len(data) // 2
f.add_chunk(struct.pack("<HH", 2, 1) + data[:half])
f.add_chunk(struct.pack("<HH", 2, 2) + data[half:])
check("fetcher parsed daily via chunks", got.get("daily", {}).get("vitality") == 82)
check("fetcher acked the file", bool(got.get("ack")))
f.handle_file_ids(2, b"")  # past: empty -> completes
check("fetcher signalled complete", got.get("done") is True)

# rejection paths
rej = {"daily": False}
f2 = A.ActivityFetcher(lambda r: None, lambda r: None,
                       on_daily=lambda x: rej.__setitem__("daily", True),
                       on_complete=lambda: None, log=lambda *a: None)
f2.handle_file_ids(1, A.parse_file_id(data[:7])["raw"])
bad = bytearray(data); bad[10] ^= 0xFF  # corrupt payload -> CRC fails
f2.add_chunk(struct.pack("<HH", 1, 1) + bytes(bad))
check("fetcher rejects bad CRC (no parse)", rej["daily"] is False)


# ---------------- real-hardware capture regression ----------------
cap_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "captures")
caps = sorted(f for f in os.listdir(cap_dir)) if os.path.isdir(cap_dir) else []
if not caps:
    print("[ -- ] no captures/ fixtures yet (run the service + sync to gather real files)")
else:
    for fn in caps:
        blob = open(os.path.join(cap_dir, fn), "rb").read()
        fid = A.parse_file_id(blob[:7])
        crc = zlib.crc32(blob[:-4]) & 0xFFFFFFFF
        exp = struct.unpack_from("<I", blob, len(blob) - 4)[0]
        check(f"capture {fn}: CRC32 valid", crc == exp)
        try:
            if A.is_daily_summary(fid):
                r = A.parse_daily_summary(fid, blob)
                sane = r and 0 <= (r["spo2_avg"] or 0) <= 100 and 0 <= (r["hr_avg"] or 0) <= 255 and r["steps"] >= 0
                check(f"capture {fn}: daily summary sane", bool(sane), r)
            elif A.is_sleep(fid):
                r = A.parse_sleep(fid, blob)
                check(f"capture {fn}: sleep sane", r and r["asleep_min"] >= 0, r)
            elif A.is_daily_details(fid):
                r = A.parse_daily_details(fid, blob)
                mins = (r or {}).get("minutes") or []
                hrok = all(m["hr"] is None or 0 <= m["hr"] <= 255 for m in mins)
                spok = all(m["spo2"] is None or 0 <= m["spo2"] <= 100 for m in mins)
                check(f"capture {fn}: {len(mins)} minutes, values in range", bool(mins) and hrok and spok)
            else:
                print(f"[ -- ] capture {fn}: type={fid['type']} sub={fid['subtype']} (no parser, skipped)")
        except Exception as e:
            check(f"capture {fn}: parses without crashing", False, repr(e))


print(f"\n{ok} passed, {fail} failed")
raise SystemExit(1 if fail else 0)
