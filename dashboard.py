"""Rich live dashboard for the Redmi Watch (Vercel/Geist-style UI).

Keeps an in-memory session: latest sample, running HR stats, HR-zone time,
and a capped time series. Serves a single-page app + JSON endpoints:
  GET /        -> dashboard HTML
  GET /state   -> full state (latest, stats, series, device)
  GET /data    -> latest sample only (compat)
"""
import json
import os
import time

import store
from core import router
from core import watch_io
from core.state import S, _lock, _SESS_FILE, _save_sleep_session  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_HR = 190
MODE = "live"
DEVICE = {"model": "Redmi Watch 5 Active", "mac": "", "port": ""}
STORE_ON = False
_throttle = {"last": 0}
_VENDOR_MAP = {
    "https://unpkg.com/@babel/standalone@7.29.0/babel.min.js": "/vendor/babel.min.js",
    "https://unpkg.com/react@18.3.1/umd/react.production.min.js": "/vendor/react.production.min.js",
    "https://unpkg.com/react-dom@18.3.1/umd/react-dom.production.min.js": "/vendor/react-dom.production.min.js",
}


def configure(max_hr=190, device=None, db_path=None, mode="live"):
    global MAX_HR, STORE_ON, MODE
    MAX_HR = max_hr or 190
    MODE = mode or "live"
    if device:
        DEVICE.update(device)
    db_path = db_path or os.path.join(HERE, "history.db")
    try:
        store.init(db_path)
        store.prune()          # raw samples 14d, minutes 90d; aggregates forever
        _load_persisted()
        STORE_ON = True
    except Exception:
        STORE_ON = False


def _today0():
    lt = time.localtime()
    return int(time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1)))


def _load_persisted():
    with _lock:
        S["days"] = store.load_days(400)
        if S["days"]:
            S["health"] = S["days"][-1]
            S["health_ts"] = int(time.time())
        sl = store.load_sleep_latest()
        if sl:
            S["sleep"] = sl
        S["sleeps"] = store.load_sleeps(200)
        for r in store.load_recent_samples(int(time.time()) - 6 * 3600, limit=5000):
            S["series"].append(r)
        # the day charts show TODAY: seed strictly from local midnight, else
        # day0 in the series derives from yesterday and the axis lies
        S["day_minutes"] = store.load_minutes(_today0(), limit=2000)


def _zone(hr):
    p = hr / float(MAX_HR)
    if p < 0.5: return 0
    if p < 0.6: return 1
    if p < 0.7: return 2
    if p < 0.85: return 3
    return 4


def push_sample(s):
    with _lock:
        t = int(s.get("ts", time.time()))
        hr = int(s.get("heartRate", 0) or 0)
        steps = int(s.get("steps", 0) or 0)
        cal = int(s.get("calories", 0) or 0)
        standing = int(s.get("standingHours", 0) or 0)
        if S["session_start"] is None:
            S["session_start"] = t
            S["steps_first"] = steps
        S["latest"] = {"ts": t, "heartRate": hr, "steps": steps,
                       "calories": cal, "standingHours": standing}
        if hr > 0:
            S["last_hr_ts"] = t
            S["last_hr"] = hr
            S["hr_min"] = hr if S["hr_min"] is None else min(S["hr_min"], hr)
            S["hr_max"] = hr if S["hr_max"] is None else max(S["hr_max"], hr)
            S["hr_sum"] += hr
            S["hr_n"] += 1
            if S["last_ts"]:
                dt = min(t - S["last_ts"], 5)
                if dt > 0:
                    S["zone_sec"][_zone(hr)] += dt
        S["steps_last"] = steps
        S["cal_last"] = cal
        S["standing_last"] = standing
        S["series"].append({"t": t, "hr": hr, "steps": steps, "cal": cal})
        S["count"] += 1
        S["last_ts"] = t
        if STORE_ON and t - _throttle["last"] >= 5:
            _throttle["last"] = t
            try:
                store.add_sample(t, hr, steps, cal)
            except Exception:
                pass


def push_battery(level, charging):
    with _lock:
        S["battery"] = {"level": int(level), "charging": bool(charging)}


def push_daily(summary):
    """Store a parsed daily-summary record. The most recent (largest date_ts)
    becomes the headline `health`; keep the last ~30 days for trends."""
    with _lock:
        day_ts = summary.get("date_ts", 0)
        days = [d for d in S["days"] if d.get("date_ts") != day_ts]
        days.append(summary)
        days.sort(key=lambda d: d.get("date_ts", 0))
        S["days"] = days[-30:]
        S["health"] = S["days"][-1]
        S["health_ts"] = int(time.time())
    if STORE_ON:
        try:
            store.upsert_daily(summary)
        except Exception:
            pass


def push_sleep(sleep):
    finished = sleep.get("is_awake") is not False   # in-progress files have is_awake=False
    with _lock:
        # keep the most recent night (largest date_ts) for the live card
        if S["sleep"] is None or sleep.get("date_ts", 0) >= S["sleep"].get("date_ts", 0):
            S["sleep"] = sleep
        # the watch may split one night into several blocks (it saw a wake and
        # started a new record). Keep every block of the running session so the
        # card can present the MERGED night.
        sess = S["sleep_session"]
        if sleep.get("bed_ts") and (sess["active"] or time.time() - sess.get("start_ts", 0) < 14 * 3600):
            sess.setdefault("blocks", {})[str(sleep["bed_ts"])] = sleep
            _save_sleep_session()
        # nights HISTORY takes finished records only, keyed by bed_ts so a
        # multi-block night keeps all its blocks
        if finished:
            key = sleep.get("bed_ts") or sleep.get("date_ts", 0)
            S["sleeps"] = [s for s in S["sleeps"]
                           if (s.get("bed_ts") or s.get("date_ts", 0)) != key]
            S["sleeps"].append(sleep)
            S["sleeps"].sort(key=lambda s: s.get("date_ts", 0))
            S["sleeps"] = S["sleeps"][-200:]
        # HARVEST: a sleep file arrived — is it in-progress (is_awake False) or finished?
        # cur_stage = the LAST block in the timeline = the current phase right now
        # (this is how we read live REM: poll the file, look at cur_stage).
        _stg = sleep.get("stages") or []
        _cur = _stg[-1][0] if _stg else None
        _record_sleep_probe("sleep_file", {
            "is_awake": sleep.get("is_awake"), "asleep_min": sleep.get("asleep_min"),
            "deep": sleep.get("deep_min"), "light": sleep.get("light_min"),
            "rem": sleep.get("rem_min"), "stages": len(_stg),
            "cur_stage": _cur,
            "bed_ts": sleep.get("bed_ts"), "wake_ts": sleep.get("wake_ts")})
    if STORE_ON and finished:
        try:
            store.upsert_sleep(sleep)
        except Exception:
            pass


def push_details(details):
    mins = details.get("minutes") or []
    if not mins:
        return
    if STORE_ON:
        try:
            store.upsert_minutes(mins)
        except Exception:
            pass
    with _lock:
        # MERGE with what we already have — an activity file that starts
        # mid-day must not erase the night's minutes from the day charts
        day0 = _today0()
        merged = {m["ts"]: m for m in S["day_minutes"] if m.get("ts", 0) >= day0}
        for m in mins:
            if m.get("ts", 0) >= day0:
                merged[m["ts"]] = m
        S["day_minutes"] = sorted(merged.values(), key=lambda m: m["ts"])
        last = mins[-1] if mins else {}
        _record_sleep_probe("details", {"minutes": len(mins),
                                        "last_min_ts": last.get("ts"),
                                        "last_hr": last.get("hr")})


def push_hr_config(cfg):
    with _lock:
        S["hr_config"] = cfg
        S["hr_config_ts"] = int(time.time())


def push_device_state(st):
    with _lock:
        S["device_state"] = st
        S["device_state_ts"] = int(time.time())
        sess = S["sleep_session"]
        if sess["active"]:
            sess["probes"].append({"ts": int(time.time()), "kind": "devstate",
                                   "asleep": st.get("asleep"), "worn": st.get("worn")})


# wired by service.py to sleep_engine (registration avoids a circular import)
LUCID = {"snapshot": lambda: None, "arm": lambda e: None}

def load_sleep_session():
    """Restore the sleep session after a service restart.

    Active + fresh (<14h): resume recording.  Active + stale: close it out.
    Inactive: restore anyway — the probes power the morning-review panel."""
    try:
        if not os.path.exists(_SESS_FILE):
            return
        with open(_SESS_FILE, encoding="utf-8") as f:
            sess = json.load(f)
        if sess.get("active") and time.time() - sess.get("start_ts", 0) >= 14 * 3600:
            sess["active"] = False   # stale: close it out
        with _lock:
            S["sleep_session"] = sess
    except Exception:
        pass


def start_sleep_session():
    with _lock:
        # carry recent night blocks over — a session restart (or a daytime test
        # session) must not wipe the merged-night card
        old_blocks = (S["sleep_session"].get("blocks") or {})
        keep = {k: v for k, v in old_blocks.items()
                if time.time() - (v.get("wake_ts") or 0) < 14 * 3600}
        S["sleep_session"] = {"active": True, "start_ts": int(time.time()),
                              "last_harvest": 0, "probes": [], "cues_sent": 0,
                              "blocks": keep}
        _save_sleep_session()


def stop_sleep_session(manual=False):
    with _lock:
        S["sleep_session"]["active"] = False
        if manual:
            # auto-night must not restart a session the user just stopped
            S["sleep_session"]["manual_stop_ts"] = int(time.time())
        _save_sleep_session()
    # finalize OUTSIDE the lock (Lock is not re-entrant; finalize re-acquires it)
    return finalize_night()


def finalize_night():
    """Persist the finished night even when the watch never flips is_awake.

    The watch sets is_awake=True only when IT closes its own sleep record, which
    it frequently never does — so push_sleep's finished-gate never fires and the
    night is lost from history (2026-07-09/10: REM=61/deep=59 measured live,
    never stored). Our engine KNOWS the night is over when it stops the session
    (wake alarm done), and the totals are final by then, so we store the merged
    night here regardless of the flag. Returns the stored record (or None)."""
    if not STORE_ON:
        return None
    with _lock:
        rec = _merged_night() or S.get("sleep")
        rec = dict(rec) if rec else None
    if not rec or not rec.get("bed_ts") or not rec.get("wake_ts") \
            or not rec.get("asleep_min"):
        return None
    rec["is_awake"] = True          # our engine closed the night
    try:
        store.upsert_sleep(rec)
    except Exception:
        return None
    with _lock:                     # mirror into the in-memory history for /state
        key = rec.get("bed_ts")
        S["sleeps"] = [s for s in S["sleeps"]
                       if (s.get("bed_ts") or s.get("date_ts", 0)) != key]
        S["sleeps"].append(rec)
        S["sleeps"].sort(key=lambda s: s.get("date_ts", 0))
        S["sleeps"] = S["sleeps"][-200:]
    return rec


def _record_sleep_probe(kind, meta):
    sess = S["sleep_session"]
    if not sess["active"]:
        return
    row = {"ts": int(time.time()), "kind": kind}
    row.update(meta)
    sess["probes"].append(row)
    sess["probes"] = sess["probes"][-500:]
    _save_sleep_session()


def _merged_night():
    """Merge same-night sleep blocks (gap < 3h) into one record for the card."""
    sess = S["sleep_session"]
    blocks = list((sess.get("blocks") or {}).values())
    if S["sleep"] and S["sleep"].get("bed_ts") and \
       str(S["sleep"]["bed_ts"]) not in (sess.get("blocks") or {}):
        blocks.append(S["sleep"])
    blocks = [b for b in blocks if b.get("bed_ts") and b.get("wake_ts")]
    if not blocks:
        return None
    blocks.sort(key=lambda b: b["bed_ts"])
    merged = None
    for b in blocks:
        if merged and b["bed_ts"] - merged["wake_ts"] < 3 * 3600:
            gap_min = max(0, (b["bed_ts"] - merged["wake_ts"]) // 60)
            for k in ("asleep_min", "deep_min", "light_min", "rem_min", "awake_min"):
                merged[k] = (merged.get(k) or 0) + (b.get(k) or 0)
            merged["awake_min"] = (merged.get("awake_min") or 0) + gap_min
            st1, st2 = merged.get("stages") or [], b.get("stages") or []
            if st1 or st2:
                merged["stages"] = st1 + ([["awake", gap_min]] if gap_min else []) + st2
            merged["wake_ts"] = b["wake_ts"]
            merged["is_awake"] = b.get("is_awake")
            merged["blocks"] = merged.get("blocks", 1) + 1
        else:
            merged = dict(b)
    # sanity-check against minute HR: the watch sometimes freezes a block's
    # asleep counter (2026-07-04: 95 min for a 4.7h block). Estimate
    # SUBTRACTIVELY — time in bed minus clearly-awake minutes (steps or HR
    # spike) minus inter-block gaps — so REM's elevated pulse stays counted.
    try:
        mins = store.load_minutes(merged["bed_ts"] - 60, limit=1500)
        win = [m for m in mins if merged["bed_ts"] <= m["ts"] <= merged["wake_ts"]]
        hrs = sorted(m["hr"] for m in win if m.get("hr"))
        if len(hrs) >= 60:
            med = hrs[len(hrs) // 2]
            gaps = []   # inter-block wake gaps reported by the watch
            bl = sorted(blocks, key=lambda x: x["bed_ts"])
            for i in range(1, len(bl)):
                gaps.append((bl[i - 1]["wake_ts"], bl[i]["bed_ts"]))
            def in_gap(ts):
                return any(g0 <= ts <= g1 for g0, g1 in gaps)
            awake = sum(1 for m in win
                        if not in_gap(m["ts"])
                        and ((m.get("steps") or 0) > 0
                             or (m.get("hr") and m["hr"] >= med + 15)))
            gap_min = sum(int((g1 - g0) // 60) for g0, g1 in gaps)
            span = int((merged["wake_ts"] - merged["bed_ts"]) // 60)
            est = span - awake - gap_min
            watch_total = merged.get("asleep_min") or 0
            merged["inbed_min"] = span
            if watch_total >= 0.7 * max(1, span - gap_min):
                # watch total is plausible (it sees restless-but-still wake that
                # HR can't) — keep it primary, expose the HR ceiling as a note
                if est >= watch_total + 45:
                    merged["asleep_hr_min"] = est
            elif est >= watch_total + 45:
                # broken/frozen counter — HR estimate takes over
                merged["asleep_watch_min"] = watch_total
                merged["asleep_min"] = est
                merged["awake_hr_min"] = awake + gap_min
                merged["est"] = True
    except Exception:
        pass
    return merged


def _month_agg(days):
    """Monthly aggregates for year-scale trends (steps avg/day, resting HR, vitality)."""
    out = {}
    for d in days:
        ts = d.get("date_ts") or 0
        if not ts:
            continue
        lt = time.localtime(ts)
        key = (lt.tm_year, lt.tm_mon)
        m = out.setdefault(key, {"year": lt.tm_year, "mon": lt.tm_mon, "n": 0,
                                 "steps": 0, "rest": [], "vit": []})
        m["n"] += 1
        m["steps"] += d.get("steps") or 0
        if d.get("hr_resting"):
            m["rest"].append(d["hr_resting"])
        if d.get("vitality"):
            m["vit"].append(d["vitality"])
    res = []
    for key in sorted(out.keys()):
        m = out[key]
        res.append({"year": m["year"], "mon": m["mon"], "days": m["n"],
                    "steps_avg": round(m["steps"] / m["n"]) if m["n"] else 0,
                    "hr_resting": round(sum(m["rest"]) / len(m["rest"])) if m["rest"] else None,
                    "vitality": round(sum(m["vit"]) / len(m["vit"])) if m["vit"] else None})
    return res[-12:]


def _merge_nights(lst):
    """History view: chain blocks of the same night (gap < 3h) into one record
    so insights compare real nights, not fragments."""
    out = []
    rest = [s for s in lst if s.get("bed_ts") and s.get("wake_ts")]
    for b in sorted(rest, key=lambda s: s["bed_ts"]):
        if out and b["bed_ts"] - out[-1]["wake_ts"] < 3 * 3600:
            m = out[-1]
            gap = max(0, (b["bed_ts"] - m["wake_ts"]) // 60)
            for k in ("asleep_min", "deep_min", "light_min", "rem_min", "awake_min"):
                m[k] = (m.get(k) or 0) + (b.get(k) or 0)
            m["awake_min"] = (m.get("awake_min") or 0) + gap
            m["wake_ts"] = b["wake_ts"]
        else:
            out.append(dict(b))
    return out


def mark_sync():
    with _lock:
        S["last_sync"] = int(time.time())


# ---- manual sync request (from the UI button / page load) ----
_sync_req = {"pending": False, "last": 0.0}


def request_sync(min_gap=8):
    """Queue a sync; debounced so page reloads don't hammer the watch."""
    with _lock:
        now = time.time()
        if now - _sync_req["last"] < min_gap:
            return False
        _sync_req["pending"] = True
        _sync_req["last"] = now
        return True


def sync_allowed():
    """QUIET NIGHT: every activity fetch is logged by the watch as a 1-minute
    awakening (14/14 correlation, 2026-07-07) and breaks its stage detection.
    While the user is asleep we do NOT touch the watch's files at all; the
    engine flips night_over after wake, and one sync pulls the whole night."""
    with _lock:
        sess = S["sleep_session"]
        if not sess.get("active"):
            return True
        if (sess.get("wake") or {}).get("night_over"):
            return True
        # quiet only once actually ASLEEP (engine's HR estimate): an active
        # evening session before sleep onset may still sync freely
        return not sess.get("sleeping_now")


def stream_allowed():
    """Realtime HR streaming gate — CLOCK based (robust, no dependency on the
    sample-fed estimate). Go DARK for the middle of the night so the watch runs
    its OWN sleep-HR analysis (advancedMonitoring is ON, but streaming seems to
    starve its stage detection → has_rem=0). Stream the first 25 min (capture
    onset) and after 5.5 h (the smart-alarm / wake window)."""
    with _lock:
        sess = S["sleep_session"]
        if not sess.get("active") or not _is_night(sess):
            return True
        wk = sess.get("wake") or {}
        if wk.get("night_over") or wk.get("firing"):
            return True
        # Realtime STREAMING kills the watch's REM staging (proven), so once
        # asleep we stay DARK for the WHOLE night — we read the sleep FILE
        # instead (polling the file is safe; only streaming wasn't). Streaming
        # resumes only after the night is over.
        ac = sess.get("asleep_confirmed_ts")  # set by the engine once actually asleep
        if not ac:
            return True                       # awake / not asleep yet — keep streaming
        return False                          # asleep -> dark; file polling gives REM


def _is_night(sess):
    st = sess.get("start_ts") or 0
    if not st:
        return False
    h = time.localtime(st).tm_hour
    return h >= 21 or h <= 6


def take_sync_request():
    with _lock:
        p = _sync_req["pending"]
        _sync_req["pending"] = False
        sess = S["sleep_session"]
        quiet = (sess.get("active") and sess.get("sleeping_now")
                 and not (sess.get("wake") or {}).get("night_over"))
        if quiet:
            return p           # only an explicit user press syncs during sleep
        # backfill: if per-minute history lags behind "now", pull it from the
        # watch so live-connection gaps in the day HR curve get filled
        mins = S.get("day_minutes") or []
        last_min = mins[-1].get("ts", 0) if mins else 0
        if (time.time() - last_min > 1200) and (time.time() - S.get("last_sync", 0) > 720):
            p = True
        return p


# ---- watch notifications queued from /notify (sent when the watch is connected) ----
# The watch font renders Latin/Cyrillic/digits/basic punctuation but NOT emoji,
# dingbats, arrows or pictographs — those show as boxes/????. Strip them.
import re as _re
_UNSAFE = _re.compile(
    "[\U0001F000-\U0001FAFF"     # emoji & pictographs
    "\U00002600-\U000027BF"      # misc symbols + dingbats (✓ ☺ …)
    "\U00002190-\U000021FF"      # arrows (⟳ etc.)
    "\U00002B00-\U00002BFF"      # misc symbols/arrows
    "\U0001F1E6-\U0001F1FF"      # flags
    "\U0000FE00-\U0000FE0F"      # variation selectors
    "\U0000200B-\U0000200D"      # zero-width chars
    "\U00002122\U00002139\U000024C2]",  # ™ ℹ Ⓜ
    flags=_re.UNICODE)


def _watch_safe(s):
    s = _UNSAFE.sub("", str(s or ""))
    return _re.sub(r"\s{2,}", " ", s).strip()


def queue_notification(title, body, app="Claude Code", tag=None):
    watch_io.enqueue_notification({"title": _watch_safe(title)[:64],
                                   "body": _watch_safe(body)[:400],
                                   "app": _watch_safe(app)[:32] or "Claude Code"},
                                  tag=tag)


def take_notifications():
    return watch_io.take_notifications()


# ---- device commands (vibrate / set alarm) — priority applied by watch_io ----
def queue_command(spec):
    watch_io.enqueue_command(spec)


def take_commands():
    return watch_io.take_commands()


def _zone_idx(hr):
    p = hr / float(MAX_HR or 190)
    if p < 0.5: return 0
    if p < 0.6: return 1
    if p < 0.7: return 2
    if p < 0.85: return 3
    return 4


def _build_day_series(mins):
    empty = {"hr": [], "spo2": [], "stress": [], "steps_hourly": [0] * 24,
             "hr_by_hour": [None] * 24, "zone_min": [0] * 5,
             "active_min": 0, "day_start": 0, "day_end": 0, "day0": 0,
             "hr_pts": [], "stress_pts": [], "spo2_pts": []}
    if not mins:
        return empty

    def ds(key, maxn=90):
        vals = [m.get(key) for m in mins if m.get(key)]
        if not vals:
            return []
        step = max(1, len(vals) // maxn)
        return vals[::step][:maxn]

    steps_hourly = [0] * 24
    hr_sum = [0] * 24
    hr_cnt = [0] * 24
    zone_min = [0, 0, 0, 0, 0]
    active_min = 0
    for m in mins:
        lt = time.localtime(m["ts"])
        h = lt.tm_hour
        s = m.get("steps") or 0
        if s:
            steps_hourly[h] += s
            active_min += 1
        hr = m.get("hr") or 0
        if hr:
            hr_sum[h] += hr
            hr_cnt[h] += 1
            zone_min[_zone_idx(hr)] += 1  # ~1 sample per minute
    hr_by_hour = [round(hr_sum[i] / hr_cnt[i]) if hr_cnt[i] else None for i in range(24)]

    # timestamped downsampled points (for time-accurate charts + hover tooltips).
    # Bucket-average over fixed time windows instead of every-Nth: sparse
    # stretches (few HR samples per hour) must keep their points, otherwise the
    # day curve shows false gaps.
    def dspairs(key, maxn=420):
        pts = [[m["ts"], m.get(key)] for m in mins if m.get(key)]
        if not pts:
            return []
        if len(pts) <= maxn:
            return pts
        span = pts[-1][0] - pts[0][0] or 1
        bucket = max(60, span // maxn)
        out, acc, n, b0 = [], 0, 0, pts[0][0]
        for ts, v in pts:
            if ts - b0 >= bucket and n:
                out.append([b0 + bucket // 2, round(acc / n)])
                acc, n, b0 = 0, 0, ts
            acc += v
            n += 1
        if n:
            out.append([b0 + bucket // 2, round(acc / n)])
        return out

    day_start = mins[0]["ts"]
    lt0 = time.localtime(day_start)
    day0 = day_start - (lt0.tm_hour * 3600 + lt0.tm_min * 60 + lt0.tm_sec)  # local midnight

    return {
        "hr": ds("hr"), "spo2": ds("spo2"), "stress": ds("stress"),
        "steps_hourly": steps_hourly, "hr_by_hour": hr_by_hour,
        "zone_min": zone_min, "active_min": active_min,
        "day_start": day_start, "day_end": mins[-1]["ts"], "day0": day0,
        "hr_pts": dspairs("hr"), "stress_pts": dspairs("stress"), "spo2_pts": dspairs("spo2"),
    }


def snapshot():
    with _lock:
        now = int(time.time())
        st_first = S["steps_first"] or 0
        return {
            "now": now,
            "mode": MODE,
            "session_start": S["session_start"],
            # 25s: the realtime stream legitimately pauses ~12-15s between
            # quiet-down and re-arm now that we don't blast re-arms 24/7
            "connected": (now - S["last_ts"]) < 25 if S["last_ts"] else False,
            # hysteresis: HR sensors pause for a minute or two mid-wear; only
            # call the watch "off wrist" after 3 min of zero pulse
            "on_wrist": (now - S.get("last_hr_ts", 0)) < 180,
            "max_hr": MAX_HR,
            "latest": S["latest"],
            "stats": {
                "hr_min": S["hr_min"] or 0,
                "hr_max": S["hr_max"] or 0,
                "hr_avg": round(S["hr_sum"] / S["hr_n"]) if S["hr_n"] else 0,
                # hold the last live value through the stream's quiet+re-arm
                # pauses (~15s) so the hero number doesn't flicker to 0
                "hr_cur": S["latest"]["heartRate"] or (S.get("last_hr", 0) if now - S.get("last_hr_ts", 0) < 60 else 0),
                "samples": S["count"],
                "steps_total": max(0, S["steps_last"] - st_first),
                "steps_now": S["steps_last"],
                "calories": S["cal_last"],
                "standing": S["standing_last"],
                "zone_sec": S["zone_sec"],
            },
            "device": DEVICE,
            "series": list(S["series"]),
            "battery": S["battery"],
            "health": S["health"],
            "health_ts": S["health_ts"],
            "days": S["days"],
            "months": _month_agg(S["days"]),
            "sleep": _merged_night() or S["sleep"],
            "device_state": S["device_state"],
            "device_state_ts": S["device_state_ts"],
            "hr_config": S["hr_config"],
            "lucid": LUCID["snapshot"](),
            "sleep_session": {"active": S["sleep_session"]["active"],
                              "start_ts": S["sleep_session"]["start_ts"],
                              "cues_sent": S["sleep_session"]["cues_sent"],
                              "probe_count": len(S["sleep_session"]["probes"]),
                              "probes": S["sleep_session"]["probes"][-60:]},
            "last_sync": S["last_sync"],
            "series_day": _build_day_series(S["day_minutes"]),
            "sleeps": [{"date_ts": s.get("date_ts"), "asleep_min": s.get("asleep_min"),
                        "deep_min": s.get("deep_min"), "rem_min": s.get("rem_min"),
                        "bed_ts": s.get("bed_ts"), "wake_ts": s.get("wake_ts")}
                       for s in _merge_nights(S["sleeps"])],
            "_notif_pending": watch_io.pending()["notif"],
        }


# ---------- HTTP routes (registered into core.router; order = specific first) ----------
# Handlers take the live request handler `h` and use h._send / h._read_json.

# ---- POST: watch-io (notify / vibrate / alarm) — becomes core/watch_io in a later step ----
def _r_notify(h):
    p = h._read_json()
    queue_notification(p.get("title", "Claude Code"), p.get("body", ""), p.get("app", "Claude Code"))
    h._send(json.dumps({"ok": True}))


def _r_vibrate_stop(h):
    queue_command({"kind": "vibrate_stop"})
    h._send(json.dumps({"ok": True}))


def _r_vibrate(h):
    queue_command({"kind": "vibrate"})
    h._send(json.dumps({"ok": True}))


def _r_alarm_delete(h):
    queue_command({"kind": "delete_alarms"})
    h._send(json.dumps({"ok": True}))


def _r_alarm(h):
    p = h._read_json()
    queue_command({"kind": "alarm", "hour": int(p.get("hour", 7)),
                   "minute": int(p.get("minute", 0)),
                   "repeat": p.get("repeat", "once"),
                   "enabled": bool(p.get("enabled", True))})
    h._send(json.dumps({"ok": True}))


# ---- GET: core (state / data / sync / export / static) ----
def _r_state_demo(h):
    try:
        import importlib
        import demo_state
        importlib.reload(demo_state)   # demo tweaks apply without a service restart
        h._send(json.dumps(demo_state.build()))
    except Exception as e:
        h._send(json.dumps({"error": str(e)}))


def _r_state(h):
    h._send(json.dumps(snapshot()))


def _r_sync(h):
    queued = request_sync()
    h._send(json.dumps({"ok": True, "queued": queued}))


def _r_export(h):
    cols = ["date_ts", "date", "steps", "calories", "hr_resting", "hr_avg", "hr_max", "hr_min",
            "spo2_avg", "spo2_max", "spo2_min", "stress_avg", "stress_max", "stress_min",
            "standing_hours", "vitality"]
    lines = [",".join(cols)]
    with _lock:
        days = list(S["days"])
    for d in days:
        ts = d.get("date_ts") or 0
        date = time.strftime("%Y-%m-%d", time.localtime(ts)) if ts else ""
        row = [str(ts), date] + [str(d.get(c, "") if d.get(c) is not None else "") for c in cols[2:]]
        lines.append(",".join(row))
    body = ("\n".join(lines) + "\n").encode("utf-8")
    fname = "redmi_watch_history_%s.csv" % time.strftime("%Y%m%d")
    h.send_response(200)
    h.send_header("Content-Type", "text/csv; charset=utf-8")
    h.send_header("Content-Disposition", 'attachment; filename="%s"' % fname)
    h.send_header("Content-Length", str(len(body)))
    h.send_header("Access-Control-Allow-Origin", "*")
    h.end_headers()
    h.wfile.write(body)


def _r_data(h):
    h._send(json.dumps(S["latest"]))


def _r_support_js(h):
    try:
        with open(os.path.join(HERE, "support.js"), "r", encoding="utf-8") as f:
            js = f.read()
        for a, b in _VENDOR_MAP.items():
            js = js.replace(a, b)
        h._send(js, "application/javascript; charset=utf-8")
    except OSError:
        h._send("// support.js missing", "application/javascript")


def _r_vendor(h):
    name = os.path.basename(h.path.split("?")[0])
    fp = os.path.join(HERE, "vendor", name)
    if os.path.isfile(fp):
        with open(fp, "rb") as f:
            h._send(f.read(), "application/javascript; charset=utf-8")
    else:
        h.send_response(404)
        h.end_headers()


def _r_index(h):
    """Default GET: serve the SPA. Registered as router's catch-all so feature
    GET routes added later still win over it."""
    try:
        with open(os.path.join(HERE, "index.dc.html"), "rb") as f:
            h._send(f.read(), "text/html; charset=utf-8")
    except OSError:
        h._send(PAGE, "text/html; charset=utf-8")


router.register("POST", "/notify", _r_notify)
router.register("POST", "/vibrate/stop", _r_vibrate_stop)
router.register("POST", "/vibrate", _r_vibrate)
router.register("POST", "/alarm/delete", _r_alarm_delete)
router.register("POST", "/alarm", _r_alarm)
router.register("GET", "/state_demo", _r_state_demo)
router.register("GET", "/state", _r_state)
router.register("GET", "/sync", _r_sync)
router.register("GET", "/export", _r_export)
router.register("GET", "/data", _r_data)
router.register("GET", "/support.js", _r_support_js)
router.register("GET", "/vendor/", _r_vendor)
router.set_default_get(_r_index)


def serve(port, host="0.0.0.0"):
    return router.serve(port, host)


PAGE = r"""<!DOCTYPE html>
<html lang="ru" data-theme="dark">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Redmi Watch 5 — Live</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js"></script>
<style>
:root{
  --bg:#000; --panel:#0a0a0a; --panel2:#111114; --border:#ffffff17; --border2:#ffffff2b;
  --fg:#ededed; --muted:#8f8f96; --muted2:#606067;
  --accent:#ff4d4d; --accent2:#0ea5e9; --green:#34d399; --amber:#f59e0b; --violet:#a78bfa;
  --z0:#3b82f6; --z1:#22d3ee; --z2:#34d399; --z3:#f59e0b; --z4:#ef4444;
  --radius:16px; --shadow:0 1px 0 rgba(255,255,255,.04) inset, 0 8px 30px rgba(0,0,0,.5);
}
[data-theme="light"]{
  --bg:#fafafa; --panel:#fff; --panel2:#f4f4f5; --border:#0000000f; --border2:#00000022;
  --fg:#0a0a0a; --muted:#666; --muted2:#999; --shadow:0 1px 2px rgba(0,0,0,.06);
}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--fg);
  font-family:"Geist","Inter",-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
  -webkit-font-smoothing:antialiased}
body{background-image:radial-gradient(1200px 600px at 80% -10%, #ff4d4d12, transparent 60%),radial-gradient(900px 500px at -10% 10%, #0ea5e912, transparent 55%)}
.tnum{font-variant-numeric:tabular-nums;font-feature-settings:"tnum"}
a{color:inherit}
.wrap{max-width:1240px;margin:0 auto;padding:20px 20px 60px}
/* top bar */
.top{display:flex;align-items:center;gap:14px;flex-wrap:wrap;padding:6px 0 18px}
.brand{display:flex;align-items:center;gap:10px;font-weight:600;font-size:15px}
.logo{width:30px;height:30px;border-radius:9px;background:linear-gradient(135deg,#ff6a3d,#ff4d4d);
  display:grid;place-items:center;font-size:16px;box-shadow:0 4px 14px #ff4d4d55}
.dot{width:8px;height:8px;border-radius:50%;background:var(--muted2);box-shadow:0 0 0 0 transparent}
.dot.live{background:var(--green);animation:pdot 1.6s infinite}
.dot.warn{background:var(--amber)}
.dot.off{background:#ef4444}
@keyframes pdot{0%{box-shadow:0 0 0 0 #34d39966}70%{box-shadow:0 0 0 7px #34d39900}100%{box-shadow:0 0 0 0 #34d39900}}
.status{color:var(--muted);font-size:13px}
.spacer{flex:1}
.ctrls{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.seg{display:flex;background:var(--panel);border:1px solid var(--border);border-radius:10px;overflow:hidden}
.seg button{background:transparent;border:0;color:var(--muted);padding:7px 11px;font-size:12px;cursor:pointer;font-weight:600}
.seg button.on{background:var(--panel2);color:var(--fg)}
.btn{background:var(--panel);border:1px solid var(--border);color:var(--fg);border-radius:10px;
  padding:7px 12px;font-size:12px;font-weight:600;cursor:pointer;display:inline-flex;gap:6px;align-items:center}
.btn:hover{border-color:var(--border2)}
/* grid */
.grid{display:grid;grid-template-columns:repeat(12,1fr);gap:14px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:var(--radius);padding:16px 18px;box-shadow:var(--shadow)}
.card h3{margin:0 0 2px;font-size:12px;color:var(--muted);font-weight:600;letter-spacing:.02em;text-transform:uppercase}
.col-3{grid-column:span 3}.col-4{grid-column:span 4}.col-5{grid-column:span 5}
.col-6{grid-column:span 6}.col-7{grid-column:span 7}.col-8{grid-column:span 8}.col-12{grid-column:span 12}
@media(max-width:900px){.col-3,.col-4,.col-5,.col-6,.col-7,.col-8{grid-column:span 12}}
/* hero */
.hero{display:flex;gap:22px;align-items:center}
.heart{position:relative;width:118px;height:118px;flex:none;display:grid;place-items:center}
.heart svg{width:100%;height:100%;filter:drop-shadow(0 6px 20px #ff4d4d66)}
.heart .bpm{position:absolute;inset:0;display:grid;place-items:center;flex-direction:column}
.heart .bpm b{font-size:34px;font-weight:800;line-height:1}
.heart .bpm span{font-size:10px;color:var(--muted)}
.hero-main{flex:1;min-width:0}
.hero-main .big{font-size:64px;font-weight:800;line-height:1;letter-spacing:-.02em}
.hero-main .big small{font-size:20px;color:var(--muted);font-weight:600}
.hero-row{display:flex;gap:10px;align-items:center;margin-top:10px;flex-wrap:wrap}
.chip{font-size:12px;font-weight:700;padding:5px 10px;border-radius:999px;border:1px solid var(--border2)}
.pct{color:var(--muted);font-size:13px}
.spark{height:54px;margin-top:6px}
/* kpis */
.kpi .v{font-size:26px;font-weight:800;letter-spacing:-.01em;margin-top:6px}
.kpi .v small{font-size:13px;color:var(--muted);font-weight:600}
.kpi .sub{font-size:11px;color:var(--muted2);margin-top:3px}
.kpi{display:flex;flex-direction:column}
.kpi .top{display:flex;align-items:center;justify-content:space-between}
.kpi .ic{font-size:14px;opacity:.85}
/* zones */
.zrow{display:flex;align-items:center;gap:10px;margin:9px 0}
.zname{width:78px;font-size:12px;color:var(--muted);font-weight:600}
.zbar{flex:1;height:10px;border-radius:999px;background:var(--panel2);overflow:hidden}
.zfill{height:100%;border-radius:999px;transition:width .5s ease}
.zval{width:74px;text-align:right;font-size:12px;color:var(--muted)}
.zcur{outline:2px solid var(--border2)}
/* footer meta */
.meta{display:flex;gap:18px;flex-wrap:wrap;color:var(--muted2);font-size:12px;margin-top:16px}
.meta b{color:var(--muted)}
.hint{margin-top:8px;font-size:12px;color:var(--amber)}
canvas{max-width:100%}
</style>
</head>
<body>
<div class="wrap">
  <div class="top">
    <div class="brand"><div class="logo">⌚</div>Redmi Watch 5 Active</div>
    <span class="dot" id="dot"></span><span class="status" id="status">подключение…</span>
    <div class="spacer"></div>
    <div class="ctrls">
      <div class="seg" id="rangeSeg">
        <button data-r="60">1м</button><button data-r="300" class="on">5м</button>
        <button data-r="900">15м</button><button data-r="0">Всё</button>
      </div>
      <button class="btn" id="pauseBtn">⏸ Пауза</button>
      <button class="btn" id="csvBtn">⬇ CSV</button>
      <button class="btn" id="themeBtn">◐</button>
      <button class="btn" id="fsBtn">⛶</button>
    </div>
  </div>

  <div class="grid">
    <!-- HERO -->
    <div class="card col-8">
      <div class="hero">
        <div class="heart">
          <svg id="heartSvg" viewBox="0 0 32 29"><path fill="#ff4d4d" d="M23.6 0c-2.9 0-5.4 1.6-6.6 4C15.8 1.6 13.3 0 10.4 0 5.7 0 2 3.8 2 8.5c0 6.6 7.1 11.3 14 17.5 6.9-6.2 14-10.9 14-17.5C30 3.8 26.3 0 23.6 0z"/></svg>
          <div class="bpm"><b id="heartBpm" class="tnum">--</b><span>BPM</span></div>
        </div>
        <div class="hero-main">
          <div class="big tnum"><span id="hrBig">--</span><small> уд/мин</small></div>
          <div class="hero-row">
            <span class="chip" id="zoneChip">—</span>
            <span class="pct tnum" id="hrPct">— % от макс</span>
            <span class="pct" id="hrDelta"></span>
          </div>
          <div class="hint" id="wristHint" style="display:none">Надень часы на руку — без контакта пульс = 0</div>
        </div>
      </div>
      <div class="spark"><canvas id="sparkChart"></canvas></div>
    </div>

    <!-- session card -->
    <div class="card col-4 kpi">
      <div class="top"><h3>Сессия</h3><span class="ic">⏱</span></div>
      <div class="v tnum" id="sessTime">00:00:00</div>
      <div class="sub" id="sessMeta">образцов: 0 · 0/с</div>
      <div style="height:10px"></div>
      <div class="top"><h3>Статус</h3></div>
      <div class="sub" id="connMeta">—</div>
    </div>

    <!-- KPI row -->
    <div class="card col-3 kpi"><div class="top"><h3>Шаги</h3><span class="ic">👟</span></div><div class="v tnum" id="steps">--</div><div class="sub" id="stepsSub">каденс — /мин</div></div>
    <div class="card col-3 kpi"><div class="top"><h3>Калории</h3><span class="ic">🔥</span></div><div class="v tnum" id="cal">--</div><div class="sub">активные, ккал</div></div>
    <div class="card col-3 kpi"><div class="top"><h3>Дистанция</h3><span class="ic">📏</span></div><div class="v tnum" id="dist">--</div><div class="sub">оценка по шагам</div></div>
    <div class="card col-3 kpi"><div class="top"><h3>Стоя</h3><span class="ic">🧍</span></div><div class="v tnum" id="stand">--</div><div class="sub">часов активности</div></div>

    <div class="card col-3 kpi"><div class="top"><h3>Средний пульс</h3><span class="ic">📊</span></div><div class="v tnum" id="hrAvg">--</div><div class="sub">за сессию</div></div>
    <div class="card col-3 kpi"><div class="top"><h3>Максимум</h3><span class="ic">▲</span></div><div class="v tnum" id="hrMax" style="color:var(--accent)">--</div><div class="sub">пик пульса</div></div>
    <div class="card col-3 kpi"><div class="top"><h3>Минимум</h3><span class="ic">▼</span></div><div class="v tnum" id="hrMin" style="color:var(--accent2)">--</div><div class="sub">покой (прибл.)</div></div>
    <div class="card col-3 kpi"><div class="top"><h3>Каденс</h3><span class="ic">🏃</span></div><div class="v tnum" id="cad">--</div><div class="sub">шагов/мин (60с)</div></div>

    <!-- main HR chart -->
    <div class="card col-8"><h3>Пульс — история</h3><div style="height:260px;margin-top:8px"><canvas id="hrChart"></canvas></div></div>

    <!-- zones -->
    <div class="card col-4">
      <h3>Зоны пульса</h3>
      <div id="zones" style="margin-top:12px"></div>
    </div>

    <!-- steps chart -->
    <div class="card col-8"><h3>Шаги — динамика</h3><div style="height:200px;margin-top:8px"><canvas id="stepsChart"></canvas></div></div>

    <!-- device -->
    <div class="card col-4 kpi">
      <div class="top"><h3>Устройство</h3><span class="ic">🛰</span></div>
      <div class="sub" style="margin-top:8px;line-height:1.9">
        <div>Модель: <b id="dModel" style="color:var(--fg)">—</b></div>
        <div>MAC: <b id="dMac" style="color:var(--fg)">—</b></div>
        <div>Порт: <b id="dPort" style="color:var(--fg)">—</b></div>
        <div>Макс. пульс: <b id="dMax" style="color:var(--fg)">—</b></div>
      </div>
    </div>
  </div>

  <div class="meta">
    <div><b id="mSamples">0</b> образцов</div>
    <div>частота <b id="mRate">0</b>/с</div>
    <div>обновлено <b id="mUpd">—</b></div>
    <div>аптайм сервиса <b id="mUp">—</b></div>
    <div style="flex:1"></div>
    <div>© локальный дашборд · данные с часов, никуда не уходят</div>
  </div>
</div>

<script>
const $=id=>document.getElementById(id);
const ZN=["Покой","Разминка","Жиросжигание","Кардио","Пик"];
const ZC=["var(--z0)","var(--z1)","var(--z2)","var(--z3)","var(--z4)"];
let range=300, paused=false, lastSeries=[], prevHr=null, firstNow=null;

// theme
if(localStorage.getItem("rw_theme")==="light")document.documentElement.setAttribute("data-theme","light");
$("themeBtn").onclick=()=>{const t=document.documentElement.getAttribute("data-theme")==="light"?"dark":"light";
  document.documentElement.setAttribute("data-theme",t);localStorage.setItem("rw_theme",t);syncChartTheme();};
$("fsBtn").onclick=()=>{if(!document.fullscreenElement)document.documentElement.requestFullscreen();else document.exitFullscreen();};
$("pauseBtn").onclick=()=>{paused=!paused;$("pauseBtn").textContent=paused?"▶ Продолжить":"⏸ Пауза";};
$("rangeSeg").onclick=e=>{if(e.target.dataset.r!==undefined){range=+e.target.dataset.r;
  [...$("rangeSeg").children].forEach(b=>b.classList.toggle("on",b===e.target));draw();}};
$("csvBtn").onclick=()=>{let s="timestamp,heartRate,steps,calories\n"+lastSeries.map(p=>`${p.t},${p.hr},${p.steps},${p.cal}`).join("\n");
  const a=document.createElement("a");a.href=URL.createObjectURL(new Blob([s],{type:"text/csv"}));
  a.download="redmi_watch_session.csv";a.click();};

function gridColor(){return getComputedStyle(document.documentElement).getPropertyValue('--border')||'#ffffff17';}
function tickColor(){return getComputedStyle(document.documentElement).getPropertyValue('--muted')||'#8f8f96';}
function baseOpts(){return{responsive:true,maintainAspectRatio:false,animation:false,
  plugins:{legend:{display:false},tooltip:{intersect:false,mode:'index'}},
  scales:{x:{ticks:{color:tickColor(),maxTicksLimit:8},grid:{color:gridColor()}},
          y:{ticks:{color:tickColor()},grid:{color:gridColor()}}}};}
const hrChart=new Chart($("hrChart"),{type:'line',data:{labels:[],datasets:[{data:[],borderColor:'#ff4d4d',backgroundColor:'#ff4d4d22',fill:true,pointRadius:0,borderWidth:2,tension:.35}]},options:baseOpts()});
const stepsChart=new Chart($("stepsChart"),{type:'line',data:{labels:[],datasets:[{data:[],borderColor:'#0ea5e9',backgroundColor:'#0ea5e922',fill:true,pointRadius:0,borderWidth:2,tension:.3,stepped:false}]},options:baseOpts()});
const spark=new Chart($("sparkChart"),{type:'line',data:{labels:[],datasets:[{data:[],borderColor:'#ff4d4d',backgroundColor:'transparent',pointRadius:0,borderWidth:2,tension:.4}]},options:{responsive:true,maintainAspectRatio:false,animation:false,plugins:{legend:{display:false},tooltip:{enabled:false}},scales:{x:{display:false},y:{display:false}}}});
function syncChartTheme(){for(const c of [hrChart,stepsChart]){c.options.scales.x.grid.color=gridColor();c.options.scales.y.grid.color=gridColor();c.options.scales.x.ticks.color=tickColor();c.options.scales.y.ticks.color=tickColor();c.update();}}

const fmtHMS=s=>{s=Math.max(0,s|0);const h=(s/3600|0),m=((s%3600)/60|0),ss=s%60;
  return String(h).padStart(2,'0')+":"+String(m).padStart(2,'0')+":"+String(ss).padStart(2,'0');};
const fmtT=t=>new Date(t*1000).toLocaleTimeString('ru-RU',{hour:'2-digit',minute:'2-digit',second:'2-digit'});

function zoneOf(hr,mx){const p=hr/mx;if(p<.5)return 0;if(p<.6)return 1;if(p<.7)return 2;if(p<.85)return 3;return 4;}

function draw(){
  const now=Math.floor(Date.now()/1000);
  const cut=range?now-range:0;
  const s=lastSeries.filter(p=>p.t>=cut);
  hrChart.data.labels=s.map(p=>fmtT(p.t));
  hrChart.data.datasets[0].data=s.map(p=>p.hr||null);
  hrChart.update();
  stepsChart.data.labels=s.map(p=>fmtT(p.t));
  stepsChart.data.datasets[0].data=s.map(p=>p.steps||null);
  stepsChart.update();
  const sp=lastSeries.slice(-40);
  spark.data.labels=sp.map(_=>"");spark.data.datasets[0].data=sp.map(p=>p.hr||null);spark.update();
}

async function tick(){
  if(paused)return;
  let d;try{d=await(await fetch('/state',{cache:'no-store'})).json();}catch(e){setOff();return;}
  lastSeries=d.series||[];
  const st=d.stats||{}, mx=d.max_hr||190, hr=st.hr_cur||0;
  // status dot
  const dot=$("dot");dot.className="dot "+(d.connected?(d.on_wrist?"live":"warn"):"off");
  $("status").textContent=d.connected?(d.on_wrist?"в эфире · часы на руке":"часы подключены · не на руке"):"нет связи с часами";
  $("wristHint").style.display=(d.connected&&!d.on_wrist)?"block":"none";
  // hero
  $("hrBig").textContent=hr||"--";$("heartBpm").textContent=hr||"--";
  const z=zoneOf(hr||0,mx);
  $("zoneChip").textContent=hr?ZN[z]:"—";$("zoneChip").style.color=hr?ZC[z]:"var(--muted)";$("zoneChip").style.borderColor=hr?ZC[z]:"var(--border2)";
  $("hrPct").textContent=hr?Math.round(hr/mx*100)+" % от макс":"— % от макс";
  if(hr&&prevHr!=null){const dd=hr-prevHr;$("hrDelta").textContent=dd>0?("▲ +"+dd):(dd<0?("▼ "+dd):"—");$("hrDelta").style.color=dd>0?"var(--accent)":(dd<0?"var(--accent2)":"var(--muted)");}
  prevHr=hr||prevHr;
  // heartbeat animation speed
  const svg=$("heartSvg");if(hr>0){svg.style.animation="beat "+(60/Math.max(40,hr)).toFixed(2)+"s infinite";}else{svg.style.animation="none";}
  // kpis
  $("steps").textContent=(st.steps_now||0).toLocaleString('ru-RU');
  $("cal").textContent=(st.calories||0).toLocaleString('ru-RU');
  $("dist").innerHTML=((st.steps_total||0)*0.72/1000).toFixed(2)+' <small style="font-size:13px;color:var(--muted)">км</small>';
  $("stand").textContent=(st.standing||0);
  $("hrAvg").textContent=st.hr_avg||"--";$("hrMax").textContent=st.hr_max||"--";$("hrMin").textContent=st.hr_min||"--";
  // cadence over last 60s
  const now=Math.floor(Date.now()/1000);const win=lastSeries.filter(p=>p.t>=now-60);
  let cad=0;if(win.length>=2){const dSteps=win[win.length-1].steps-win[0].steps;const dMin=Math.max(1,(win[win.length-1].t-win[0].t))/60;cad=Math.max(0,Math.round(dSteps/dMin));}
  $("cad").textContent=cad;$("stepsSub").textContent="каденс "+cad+" /мин";
  // session
  if(d.session_start){$("sessTime").textContent=fmtHMS(now-d.session_start);}
  const rate=d.session_start&&(now-d.session_start)>0?(st.samples/(now-d.session_start)).toFixed(2):"0";
  $("sessMeta").textContent="образцов: "+(st.samples||0)+" · "+rate+"/с";
  $("connMeta").innerHTML=d.connected?'<span style="color:var(--green)">● онлайн</span>':'<span style="color:#ef4444">● офлайн</span>';
  // zones
  const zs=st.zone_sec||[0,0,0,0,0];const tot=zs.reduce((a,b)=>a+b,0)||1;
  $("zones").innerHTML=ZN.map((n,i)=>{const pc=Math.round(zs[i]/tot*100);
    return `<div class="zrow"><div class="zname">${n}</div><div class="zbar"><div class="zfill ${i===z&&hr?'zcur':''}" style="width:${pc}%;background:${ZC[i]}"></div></div><div class="zval tnum">${pc}% · ${fmtHMS(zs[i])}</div></div>`;}).join("");
  // device + meta
  const dev=d.device||{};$("dModel").textContent=dev.model||"—";$("dMac").textContent=dev.mac||"—";$("dPort").textContent=dev.port||"—";$("dMax").textContent=mx+" уд/мин";
  $("mSamples").textContent=st.samples||0;$("mRate").textContent=rate;$("mUpd").textContent=new Date().toLocaleTimeString('ru-RU');
  if(firstNow==null)firstNow=now;$("mUp").textContent=fmtHMS(now-firstNow);
  draw();
}
function setOff(){$("dot").className="dot off";$("status").textContent="сервис недоступен";}
const styleBeat=document.createElement("style");styleBeat.textContent="@keyframes beat{0%,100%{transform:scale(1)}15%{transform:scale(1.18)}30%{transform:scale(1)}45%{transform:scale(1.1)}}";document.head.appendChild(styleBeat);
tick();setInterval(tick,1000);
</script>
</body>
</html>"""
