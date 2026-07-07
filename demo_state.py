"""Synthetic /state for the /demo page: a strong, active day worth screenshotting.

Morning run, full activity rings, a great structured night with lucid cues,
green insight deltas, week history. Deterministic (seeded) so the page is
stable between reloads.
"""
import math
import random
import time


def _day0():
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def build():
    rnd = random.Random(42)
    day0 = _day0()
    now = day0 + int(21.75 * 3600)          # frozen "evening" for a full-day look

    # ---- day HR curve: sleep valley → morning run → active day → evening gym ----
    hr_pts = []
    t = day0
    while t < now:
        h = (t - day0) / 3600
        if h < 7.1:                          # sleep
            base = 50 + 4 * math.sin(h / 1.4 * math.pi) + (3 if h > 5 else 0)
        elif h < 8.0:                        # wake, breakfast
            base = 68 + (h - 7.1) * 10
        elif 8.0 <= h < 8.2:                 # walking to the park: gentle rise
            base = 74 + (h - 8.0) / 0.2 * 14
        elif 8.2 <= h <= 8.95:               # RUN: ramp → steady 135-148 → finish kick
            p = (h - 8.2) / 0.75
            if p < 0.09:      base = 88 + p / 0.09 * 48          # ~4 min to steady state
            elif p < 0.85:    base = 138 + 8 * math.sin(p * 9) + rnd.uniform(-3, 3)
            else:             base = 150 + (p - 0.85) / 0.15 * 13  # finish ~163
        elif 8.95 < h < 9.5:                 # exponential recovery after the run
            base = 76 + 84 * math.exp(-(h - 8.95) / 0.13)
        elif 18.3 <= h < 18.5:               # gym warmup
            base = 72 + (h - 18.3) / 0.2 * 46
        elif 18.5 <= h <= 19.5:              # gym: gentle set/rest waves 108-140
            base = 124 + 15 * math.sin((h - 18.5) * math.pi * 2) + rnd.uniform(-4, 4)
        elif 19.5 < h < 19.85:               # post-gym recovery
            base = 74 + 58 * math.exp(-(h - 19.5) / 0.14)
        else:
            base = 66 + 7 * math.sin(h * 1.1) + rnd.uniform(-3, 3)
        hr_pts.append([t, int(max(44, min(166, base + rnd.uniform(-2, 2))))])
        t += 180

    # ---- steps per hour: run + lunch walk + gym + errands ----
    steps_hourly = [0] * 24
    for h, v in {7: 620, 8: 4180, 9: 480, 10: 350, 11: 410, 12: 1350, 13: 780,
                 14: 420, 15: 510, 16: 640, 17: 830, 18: 1930, 19: 1240,
                 20: 690, 21: 410}.items():
        steps_hourly[h] = v
    steps = sum(steps_hourly)                # ~14.8k

    # ---- HR zones (minutes) ----
    zone_min = [520, 96, 44, 52, 14]
    active_min = 118

    # ---- sparse periodic measurements ----
    stress_pts = [[day0 + int(h * 3600), v] for h, v in
                  [(0.4, 18), (2.5, 14), (5, 16), (7.6, 22), (9.1, 31), (10.4, 27),
                   (12.2, 38), (13.5, 42), (15.1, 33), (16.8, 29), (18.2, 36),
                   (19.6, 24), (20.9, 21), (21.5, 19)]]
    spo2_pts = [[day0 + int(h * 3600), v] for h, v in
                [(0.8, 97), (2.2, 96), (4.1, 97), (6.3, 98), (8.9, 98), (11.4, 99),
                 (13.8, 98), (16.2, 97), (18.9, 98), (21.2, 99)]]

    # ---- last night: clean 90-min cycles, chunky stages ----
    bed = day0 - int(0.65 * 3600)            # 23:21 yesterday
    stages = []
    for cyc in range(5):
        light1 = 24 + rnd.randint(-4, 4)
        deep = (26 if cyc < 3 else 10) + rnd.randint(-3, 3)
        light2 = 18 + rnd.randint(-3, 3)
        rem = (12 + cyc * 5) + rnd.randint(-2, 2)
        stages += [["light", light1], ["deep", deep], ["light", light2], ["rem", rem]]
        if cyc == 2:
            stages.append(["awake", 3])
    asleep = sum(m for s, m in stages if s != "awake")
    awake = sum(m for s, m in stages if s == "awake") + 9
    wake_ts = bed + (asleep + awake) * 60
    deep_m = sum(m for s, m in stages if s == "deep")
    rem_m = sum(m for s, m in stages if s == "rem")
    light_m = sum(m for s, m in stages if s == "light")
    sleep = {"date_ts": day0, "bed_ts": bed, "wake_ts": wake_ts, "is_awake": True,
             "asleep_min": asleep, "deep_min": deep_m, "light_m": light_m,
             "light_min": light_m, "rem_min": rem_m, "awake_min": awake,
             "stages": stages, "inbed_min": asleep + awake}

    # ---- lucid session: probes + 2 cues landed in morning REM ----
    probes = []
    pt = bed + 40 * 60
    n_file = 0
    while pt < wake_ts:
        n_file += 1
        probes.append({"ts": pt, "kind": "sleep_file", "is_awake": False,
                       "asleep_min": min(asleep, (pt - bed) // 60),
                       "stages": n_file * 12, "rem": min(rem_m, n_file * 6),
                       "bed_ts": bed, "wake_ts": pt})
        pt += 25 * 60
    cue1 = day0 + int(5.7 * 3600)
    cue2 = day0 + int(6.85 * 3600)
    probes.append({"ts": cue1, "kind": "cue", "n": 1})
    probes.append({"ts": cue2, "kind": "cue", "n": 2})
    probes.sort(key=lambda p: p["ts"])

    # ---- week history ----
    days = []
    week_steps = [9840, 11230, 8420, 12660, 10480, 13910, steps]
    week_rest = [51, 49, 50, 48, 49, 47, 47]
    week_vit = [76, 79, 78, 83, 84, 88, 91]
    for i in range(7):
        d0 = day0 - (6 - i) * 86400
        days.append({"date_ts": d0, "steps": week_steps[i], "calories": 520 + i * 30,
                     "hr_avg": 67, "hr_resting": week_rest[i], "hr_max": 161,
                     "spo2_avg": 98, "stress_avg": 27 + (i % 3) * 3,
                     "vitality": week_vit[i], "standing_hours": 11})
    sleeps = []
    for i in range(7):
        d0 = day0 - (6 - i) * 86400
        b = d0 - int((0.55 + rnd.uniform(-0.3, 0.25)) * 3600)
        a = 430 + rnd.randint(-35, 30)
        sleeps.append({"date_ts": d0, "bed_ts": b, "wake_ts": b + (a + 20) * 60,
                       "asleep_min": a, "deep_min": 90 + rnd.randint(-12, 12),
                       "rem_min": 96 + rnd.randint(-10, 12)})
    sleeps[-1] = {k: sleep.get(k) for k in
                  ("date_ts", "bed_ts", "wake_ts", "asleep_min", "deep_min", "rem_min")}

    series = [{"t": now - 240 + i * 2, "hr": 62 + int(3 * math.sin(i / 6)), "steps": steps,
               "cal": 736} for i in range(120)]

    return {
        "now": now, "mode": "live", "session_start": day0 + 6 * 3600,
        "connected": True, "on_wrist": True, "max_hr": 190,
        "latest": {"ts": now, "heartRate": 63, "steps": steps, "calories": 736,
                   "standingHours": 12},
        "stats": {"hr_cur": 63, "hr_min": 46, "hr_max": 164, "hr_avg": 71,
                  "steps_now": steps, "calories": 736, "duration_sec": 56000},
        "series": series,
        "battery": {"level": 74, "charging": False},
        "health": {"date_ts": day0, "steps": steps, "calories": 736, "hr_avg": 71,
                   "hr_resting": 47, "hr_max": 164, "hr_min": 46,
                   "spo2_avg": 98, "spo2_min": 96, "spo2_max": 99,
                   "stress_avg": 26, "stress_min": 14, "stress_max": 42,
                   "vitality": 91, "standing_hours": 12},
        "health_ts": now, "days": days, "sleep": sleep, "sleeps": sleeps,
        "device_state": {"asleep": False, "worn": True},
        "device_state_ts": now,
        "lucid": {"enabled": False, "armed": False, "reason": "выключено",
                  "rem_live": False, "cues_sent": 0,
                  "cfg": {"min_asleep_min": 240, "max_cues": 3,
                          "cue_gap_min": 25, "window_end_hour": 10}},
        "sleep_session": {"active": False, "start_ts": bed - 10 * 60,
                          "cues_sent": 2, "probe_count": len(probes),
                          "probes": probes},
        "last_sync": now - 400,
        "series_day": {
            "day0": day0, "day_start": day0, "day_end": now,
            "hr_pts": hr_pts, "stress_pts": stress_pts, "spo2_pts": spo2_pts,
            "hr": [p[1] for p in hr_pts][-140:],
            "spo2": [p[1] for p in spo2_pts],
            "stress": [p[1] for p in stress_pts],
            "steps_hourly": steps_hourly,
            "hr_by_hour": [None] * 24, "zone_min": zone_min,
            "active_min": active_min,
        },
    }
