"""Lucid / REM cue engine.

Sends gentle wrist cues when the watch's own in-progress sleep file shows REM
happening RIGHT NOW (validated 2026-07-03: interim sleep files arrive all night
every ~7 min; from mid-night they carry live cumulative stage minutes, so a
growing `rem` counter between two files = REM in progress).

Safety-first design:
  - default DISABLED; the user arms it per night from the dashboard
  - hard stop conditions checked before anything else:
      woke up (fresh sleep file with awake flag) / cue limit / time window end
  - the decision core is a pure function (testable without hardware)

The engine only reads dashboard state and enqueues "cue" commands; the client
delivers them as quiet notification buzzes.
"""
import threading
import time

import dashboard
import store

# ---- config (UI-editable via /lucid endpoints) ----
CFG = {
    "enabled": False,          # master switch, re-armed each night by the user
    "min_asleep_min": 240,     # don't cue before this much sleep (deep-sleep protection)
    "max_cues": 3,             # per night
    "cue_gap_min": 25,         # minimum minutes between cues
    "window_end_hour": 10,     # local hour after which no cues are sent
    "fresh_sec": 900,          # a sleep file older than this is not "now"
}

# ---- nightly automation: session auto-start/stop ----
AUTO = {
    "enabled": True,
    "start_from_hour": 22, "start_from_min": 30,   # window 22:30 → 03:00
    "stop_from_hour": 11,                          # auto-stop window 11:00 → 13:00
}

# ---- smart wake: guaranteed alarm ~6h after sleep onset ----
WAKE = {
    "enabled": True,
    "after_min": 360,          # target: wake after this much cumulative sleep
    "window_min": 45,          # look for a light/REM moment within this window
    "backup_extra_min": 60,    # hardware watch alarm at after+extra (past the smart
                               # window so it's a true failsafe, not a pre-empt)
    "cue_gap_sec": 60,         # escalation pacing
    "max_soft": 4,             # gentle buzzes before sirens
    "max_sirens": 3,           # find-device alerts (loud) max
}

# ---- runtime state (mirrored into /state for the UI) ----
ST = {
    "armed": False, "reason": "выключено", "rem_live": False,
    "cues": [], "last_check": 0,
}

LOG = lambda m: None   # service.py routes this into service.log


_EST_CACHE = {"ts": 0, "est": None, "mins": []}


def get_night_hr(now, session_start):
    """[[ts, hr]] per-minute for the session window, from realtime samples."""
    night_estimate(now, session_start)   # refreshes the shared cache
    return [[m["ts"], m["hr"]] for m in _EST_CACHE.get("mins") or []]


def night_estimate(now, session_start):
    """QUIET-NIGHT sleep estimate from the realtime HR stream persisted in
    store.samples — the watch is never polled. Returns
    {onset, asleep_min, awake_now_hint} or None (not asleep yet)."""
    if now - _EST_CACHE["ts"] < 60:
        return _EST_CACHE["est"]
    try:
        mins = store.hr_minutes_from_samples(session_start - 3600)
    except Exception:
        return _EST_CACHE["est"]
    _EST_CACHE["mins"] = mins
    est = None
    if len(mins) >= 20:
        rest = (dashboard.S.get("health") or {}).get("hr_resting") or 55
        thr_on, thr_wake = rest + 8, rest + 16
        # onset: first run of 15 quiet minutes (HR <= rest+8, no step growth)
        onset = None
        run_start, run = None, 0
        prev_steps = None
        for m in mins:
            quiet = m["hr"] <= thr_on and (prev_steps is None or (m["steps"] or 0) <= prev_steps)
            prev_steps = m["steps"] if m["steps"] is not None else prev_steps
            if quiet:
                run += 1
                if run_start is None:
                    run_start = m["ts"]
                if run >= 15:
                    onset = run_start
                    break
            else:
                run, run_start = 0, None
        if onset:
            asleep = 0
            prev_steps = None
            for m in mins:
                if m["ts"] < onset:
                    prev_steps = m["steps"]
                    continue
                moving = prev_steps is not None and (m["steps"] or 0) > prev_steps
                prev_steps = m["steps"] if m["steps"] is not None else prev_steps
                if m["hr"] <= thr_wake and not moving:
                    asleep += 1
            recent = [m for m in mins if now - m["ts"] <= 10 * 60]
            awake_hint = bool(recent) and (sum(m["hr"] for m in recent) / len(recent) >= thr_wake)
            est = {"onset": onset, "asleep_min": asleep, "awake_hint": awake_hint}
    _EST_CACHE.update(ts=now, est=est)
    return est


def cum_asleep(sf):
    """Cumulative asleep minutes across the night's blocks (counters restart
    per block when the watch splits the night)."""
    per = {}
    for p in sf:
        b = p.get("bed_ts") or 0
        per[b] = max(per.get(b, 0), p.get("asleep_min") or 0)
    return sum(per.values()), (min(per.keys()) if per else None)


def decide(probes, cfg, cues, now, local_hour, hr_pts=None, est=None):
    """Pure decision core.  Returns (action, reason, rem_live):
    action ∈ {"cue", "wait", "off"}.
    hr_pts: optional [[ts, hr], ...] minute HR — the pulse REM fallback.
    est: optional QUIET-NIGHT estimate {onset, asleep_min, awake_hint} — lets
    the engine run with ZERO sleep files (we no longer poll the watch at night:
    every fetch got logged as a 1-min awakening, 14/14 correlation 2026-07-07)."""
    if len(cues) >= cfg["max_cues"]:
        return "off", "лимит сигналов за ночь (%d)" % cfg["max_cues"], False
    # morning cutoff only: evening/night hours (before midnight) are handled by
    # the min_asleep gate, not the clock
    if cfg["window_end_hour"] <= local_hour < 20:
        return "off", "окно времени закрыто (после %02d:00)" % cfg["window_end_hour"], False

    sf = [p for p in probes if p.get("kind") == "sleep_file"]
    if not sf and not est:
        return "wait", "жду засыпания (по пульсу)", False

    if sf:
        last = sf[-1]
        # --- pause while awake (a brief 2am wake must not cancel morning cues) ---
        if last.get("is_awake") is True:
            return "wait", "не спишь — пауза", False
        if now - last["ts"] > cfg["fresh_sec"] and not est:
            return "wait", "файл сна устарел (%d мин)" % ((now - last["ts"]) // 60), False
    elif est and est.get("awake_hint"):
        return "wait", "не спишь (по пульсу) — пауза", False

    # --- gates: cumulative sleep across blocks, or the HR estimate ---
    asleep_files, night_start_files = cum_asleep(sf)
    asleep = max(asleep_files, (est or {}).get("asleep_min") or 0)
    night_start = night_start_files or (est or {}).get("onset") or now
    if asleep < cfg["min_asleep_min"]:
        return "wait", "спал суммарно %d мин, жду %d" % (asleep, cfg["min_asleep_min"]), False

    # --- live REM: rem-minute counter grew between the last two staged files
    # of the CURRENT block. Files with stages but rem==0 all night must NOT
    # count as "staged" — otherwise they block the HR fallback (2026-07-06). ---
    cur_bed = (sf[-1].get("bed_ts") or 0) if sf else 0
    staged = [p for p in sf if (p.get("stages") or 0) > 0 and (p.get("rem") or 0) > 0
              and (p.get("bed_ts") or 0) == cur_bed]
    rem_live = False
    if len(staged) >= 2:
        d_rem = (staged[-1].get("rem") or 0) - (staged[-2].get("rem") or 0)
        rem_live = d_rem > 0 and now - staged[-1]["ts"] < cfg["fresh_sec"]
    if not staged:
        # WE CUE ONLY ON REAL WATCH REM. Pulse can't detect REM on this hardware
        # (validated 2026-07-08: REM vs non-REM HR differ ~1 bpm, 16-26%
        # precision = noise), so without the watch's own REM stages there is no
        # trustworthy signal — and we do NOT buzz on a guess.
        return "wait", "нет REM-стадий от часов — сигнал не шлю", False
    if not rem_live:
        return "wait", "не REM (по стадиям от часов)", False

    # --- cue spacing ---
    if cues and now - cues[-1] < cfg["cue_gap_min"] * 60:
        return "wait", "REM идёт, пауза между сигналами", True
    return "cue", "REM сейчас — отправляю мягкий сигнал", True


def _auto_tick(now):
    """Nightly automation: start the session in the evening, stop late morning."""
    if not AUTO["enabled"]:
        return
    sess = dashboard.S.get("sleep_session") or {}
    lt = time.localtime(now)
    hm = lt.tm_hour * 60 + lt.tm_min
    start_hm = AUTO["start_from_hour"] * 60 + AUTO["start_from_min"]
    in_evening = hm >= start_hm or lt.tm_hour < 3
    if in_evening and not sess.get("active"):
        # respect a manual stop made tonight
        ms = sess.get("manual_stop_ts") or 0
        if now - ms > 10 * 3600:
            LOG("auto-night: starting sleep session (evening window)")
            dashboard.start_sleep_session()
            arm(True)
            dashboard.request_sync()
        return
    # auto-stop: AWAKE-CONFIRMED, not wall-clock (the user's schedule drifts:
    # bedtimes 00:57 / 03:15 / 07:50 — a fixed 11:00 stop would kill tracking
    # mid-sleep). Requires: daytime hours + slept 4h+ + awake by HR for 20 min.
    if sess.get("active") and 9 <= lt.tm_hour < 20 and _is_night_session(sess, now):
        wk = sess.get("wake") or {}
        est = night_estimate(now, sess.get("start_ts") or now)
        slept_enough = est and now - (est.get("onset") or now) > 4 * 3600
        awake = wk.get("done") or (est and est.get("awake_hint") and slept_enough)
        if awake:
            if not wk.get("night_over"):
                sess.setdefault("wake", {})["night_over"] = True
                sess["wake"]["night_over_ts"] = now
                dashboard._save_sleep_session()
                LOG("auto-night: wake confirmed — syncing the night file")
                dashboard.request_sync()
                return
            if now - (wk.get("night_over_ts") or 0) > 240:
                LOG("auto-night: stopping sleep session (night file pulled)")
                dashboard.stop_sleep_session()
                arm(False)


def _awake_now(sess, now):
    """Is the user demonstrably awake right now? (steps moved / HR spike / file flag)"""
    sf = [p for p in (sess.get("probes") or []) if p.get("kind") == "sleep_file"]
    if sf and sf[-1].get("is_awake") is True and now - sf[-1]["ts"] < 600:
        return True
    wk = sess.get("wake") or {}
    latest = dashboard.S.get("latest") or {}
    if wk.get("steps0") is not None and (latest.get("steps") or 0) > wk["steps0"] + 15:
        return True
    series = list(dashboard.S.get("series") or [])[-40:]
    hrs = [s.get("hr") for s in series if s.get("hr")]
    if len(hrs) >= 10:
        base = (wk.get("hr_med") or 60)
        recent = sum(hrs[-10:]) / 10
        if recent >= base + 18:
            return True
    return False


def _is_night_session(sess, now):
    """True only for a real overnight session — a daytime MANUAL test session
    must never drive the smart alarm (2026-07-07: fired soft buzzes at 14:38)."""
    st = sess.get("start_ts") or now
    h = time.localtime(st).tm_hour
    return h >= 21 or h <= 6


def _wake_tick(now):
    """Guaranteed smart alarm: fire after ~WAKE.after_min of cumulative sleep,
    prefer a light/REM moment, escalate until demonstrably awake.
    A one-off hardware watch alarm at after+extra backs the whole thing up."""
    if not WAKE["enabled"]:
        return
    sess = dashboard.S.get("sleep_session") or {}
    if not sess.get("active") or not _is_night_session(sess, now):
        return
    wk = sess.setdefault("wake", {})
    if wk.get("done"):
        return
    sf = [p for p in (sess.get("probes") or []) if p.get("kind") == "sleep_file"]
    asleep_f, night_start = cum_asleep(sf)
    est = night_estimate(now, sess.get("start_ts") or now)
    # STABLE anchor = the moment sleep was confirmed (set once, survives the dark
    # window). est.onset drifts across the dark gap and gave wrong alarm timing
    # (2026-07-09: fired 07:40 instead of ~6h, backup mis-placed at 05:36).
    anchor = sess.get("asleep_confirmed_ts") or night_start or (est or {}).get("onset")
    if not anchor:
        return
    # asleep by CLOCK from the anchor (accurate while dark, no samples), floored
    # by whatever the file/estimate report
    asleep = max(asleep_f, (est or {}).get("asleep_min") or 0, int((now - anchor) // 60))
    # one-off hardware backup alarm — placed AFTER the smart window so it's a true
    # failsafe (rings only if the smart wake failed / PC died). Can't be deleted
    # (no alarm-list cmd), so it must not pre-empt the smart alarm.
    if not wk.get("backup_set"):
        tgt = anchor + (WAKE["after_min"] + WAKE["backup_extra_min"]) * 60
        lt = time.localtime(tgt)
        dashboard.queue_command({"kind": "alarm", "hour": lt.tm_hour,
                                 "minute": lt.tm_min, "repeat": "once"})
        wk["backup_set"] = True
        wk["backup_hm"] = "%02d:%02d" % (lt.tm_hour, lt.tm_min)
        LOG("wake: hardware backup alarm queued for %s (anchor %s)"
            % (wk["backup_hm"], time.strftime("%H:%M", time.localtime(anchor))))
        dashboard._save_sleep_session()
    # arm the smart fire
    overdue = now >= anchor + (WAKE["after_min"] + WAKE["window_min"]) * 60
    ready = asleep >= WAKE["after_min"] or overdue
    night_start = anchor
    if not ready and not wk.get("firing"):
        return
    if not wk.get("firing"):
        # prefer a light/REM moment inside the window; force at window end
        nice_moment = ST.get("rem_live") or overdue or asleep >= WAKE["after_min"] + WAKE["window_min"]
        # light phase heuristic: recent HR at/above night median (not deep)
        if not nice_moment:
            hr_pts = [[m.get("ts"), m.get("hr")] for m in (dashboard.S.get("day_minutes") or [])
                      if m.get("ts") and m.get("hr") and m["ts"] >= night_start]
            if len(hr_pts) >= 30:
                vals = sorted(v for _, v in hr_pts)
                med = vals[len(vals) // 2]
                recent = [v for t, v in hr_pts if now - t <= 12 * 60]
                if recent and sum(recent) / len(recent) >= med + 2:
                    nice_moment = True
        if not nice_moment:
            return
        latest = dashboard.S.get("latest") or {}
        series = list(dashboard.S.get("series") or [])[-120:]
        hrs = sorted(s.get("hr") for s in series if s.get("hr"))
        wk.update(firing=True, started=now, soft=0, sirens=0, last_sig=0,
                  steps0=latest.get("steps") or 0,
                  hr_med=(hrs[len(hrs) // 2] if hrs else 60))
        LOG("wake: FIRING smart alarm (asleep=%d min)" % asleep)
        dashboard._save_sleep_session()
    # escalation loop
    if _awake_now(sess, now):
        dashboard.queue_command({"kind": "vibrate_stop"})
        h, m = divmod(asleep, 60)
        dashboard.queue_notification("Доброе утро", "Ты проснулся. Спал %dч %02dм. Резервный будильник на %s прозвонит разово - смахни его." % (h, m, wk.get("backup_hm", "?")))
        wk["done"] = True
        wk["night_over"] = True     # quiet night ends: syncs allowed again
        LOG("wake: user awake — sequence complete, pulling the night file")
        dashboard._save_sleep_session()
        dashboard.request_sync()
        return
    if now - (wk.get("last_sig") or 0) < WAKE["cue_gap_sec"]:
        return
    wk["last_sig"] = now
    if wk.get("soft", 0) < WAKE["max_soft"]:
        wk["soft"] = wk.get("soft", 0) + 1
        dashboard.queue_command({"kind": "cue"})
        LOG("wake: soft buzz %d/%d" % (wk["soft"], WAKE["max_soft"]))
    elif wk.get("sirens", 0) < WAKE["max_sirens"]:
        wk["sirens"] = wk.get("sirens", 0) + 1
        dashboard.queue_command({"kind": "vibrate"})
        LOG("wake: SIREN %d/%d" % (wk["sirens"], WAKE["max_sirens"]))
    else:
        # escalation exhausted — hardware alarm remains as the final backstop
        wk["done"] = True
        LOG("wake: escalation exhausted, hardware alarm is the backstop")
    dashboard._save_sleep_session()


def _tick():
    now = int(time.time())
    ST["last_check"] = now
    try:
        _auto_tick(now)
    except Exception as e:
        LOG("auto-night error: %s" % e)
    try:
        _wake_tick(now)
    except Exception as e:
        LOG("wake error: %s" % e)
    sess = dashboard.S.get("sleep_session") or {}
    if not CFG["enabled"] or not sess.get("active"):
        ST.update(armed=False, rem_live=False,
                  reason="выключено" if not CFG["enabled"] else "нет записи сна")
        return
    est = night_estimate(now, sess.get("start_ts") or now)
    # quiet-night sync gate only for real overnight sessions — a daytime test
    # session sitting quietly must not block data collection
    sess["sleeping_now"] = bool(est) and _is_night_session(sess, now)
    if sess["sleeping_now"] and not sess.get("asleep_confirmed_ts"):
        # first confirmed sleep — after this the realtime stream may go dark so
        # the watch stages REM itself (dashboard.stream_allowed reads this)
        sess["asleep_confirmed_ts"] = now
        dashboard._save_sleep_session()
    hr_pts = get_night_hr(now, sess.get("start_ts") or now)
    if not hr_pts:
        hr_pts = [[m.get("ts"), m.get("hr")] for m in (dashboard.S.get("day_minutes") or [])
                  if m.get("ts") and m.get("hr")]
    action, reason, rem_live = decide(sess.get("probes") or [], CFG, ST["cues"],
                                      now, time.localtime(now).tm_hour, hr_pts, est)
    if reason != ST["reason"]:
        LOG("lucid: %s -> %s" % (action, reason))   # night must be debuggable next morning
    ST.update(armed=(action != "off"), reason=reason, rem_live=rem_live)
    if action == "off":
        CFG["enabled"] = False      # disarm for the rest of the night
        _mirror()
        return
    if action == "cue":
        ST["cues"].append(now)
        LOG("lucid: CUE #%d sent" % len(ST["cues"]))
        dashboard.queue_command({"kind": "cue"})
        _mirror()
        try:
            with dashboard._lock:
                dashboard._record_sleep_probe("cue", {"n": len(ST["cues"])})
        except Exception:
            pass


def _mirror():
    """Persist enabled+cues into the session file: a mid-night service restart
    must not disarm the engine or reset the cue limit."""
    try:
        with dashboard._lock:
            dashboard.S["sleep_session"]["lucid"] = {"enabled": CFG["enabled"],
                                                     "cues": list(ST["cues"])}
            dashboard._save_sleep_session()
    except Exception:
        pass


def restore():
    try:
        sess = dashboard.S.get("sleep_session") or {}
        lu = sess.get("lucid") or {}
        if sess.get("active") and lu.get("enabled"):
            CFG["enabled"] = True
            ST["cues"] = list(lu.get("cues") or [])
            ST["reason"] = "взведено (восстановлено после рестарта)"
            LOG("lucid: re-armed after restart, cues so far: %d" % len(ST["cues"]))
    except Exception:
        pass


def arm(enabled):
    CFG["enabled"] = bool(enabled)
    if enabled:
        ST["cues"] = []
        ST["reason"] = "взведено"
    else:
        ST.update(armed=False, reason="выключено", rem_live=False)
    _mirror()


def snapshot():
    sess = dashboard.S.get("sleep_session") or {}
    wk = sess.get("wake") or {}
    return {"enabled": CFG["enabled"], "armed": ST["armed"], "reason": ST["reason"],
            "rem_live": ST["rem_live"], "cues_sent": len(ST["cues"]),
            "auto_night": AUTO["enabled"],
            "wake": {"enabled": WAKE["enabled"], "after_min": WAKE["after_min"],
                     "backup_hm": wk.get("backup_hm"), "firing": bool(wk.get("firing")),
                     "done": bool(wk.get("done"))},
            "cfg": {k: CFG[k] for k in ("min_asleep_min", "max_cues", "cue_gap_min", "window_end_hour")}}


def start():
    restore()
    def loop():
        while True:
            try:
                _tick()
            except Exception:
                pass
            time.sleep(30)
    threading.Thread(target=loop, daemon=True).start()
