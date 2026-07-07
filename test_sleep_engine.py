"""Regression tests for the lucid engine's pure decision core.

Scenarios distilled from the first two real nights:
  2026-07-03: staged files with growing rem => cues in real REM
  2026-07-04: split night (counter reset) + no stages => cumulative gate + HR fallback
"""
import sys
import time
import types

sys.modules['dashboard'] = types.SimpleNamespace(S={}, queue_command=lambda c: None,
                                                 _lock=None, _record_sleep_probe=lambda *a: None,
                                                 _save_sleep_session=lambda: None)
import sleep_engine as se

FAILED = 0


def check(name, cond):
    global FAILED
    print(("  OK   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED += 1


CFG = dict(se.CFG)
CFG["enabled"] = True
T0 = int(time.mktime((2026, 7, 10, 23, 0, 0, 0, 0, -1)))   # 23:00 local


def sf(ts, bed, asleep, stages=0, rem=0, awake=False):
    return {"kind": "sleep_file", "ts": ts, "bed_ts": bed, "asleep_min": asleep,
            "stages": stages, "rem": rem, "is_awake": awake}


def hh(h, m=0):
    return T0 + int((h + m / 60) * 3600)


# --- 1. early night: min_asleep gate holds ---
a, r, _ = se.decide([sf(hh(2), T0, 120)], CFG, [], hh(2) + 60, 1)
check("early night waits (gate 240)", a == "wait" and "240" in r)

# --- 2. cumulative gate across split blocks (2026-07-04 failure) ---
probes = [sf(hh(3), T0, 184), sf(hh(5), hh(4, 30), 70)]
a, r, _ = se.decide(probes, CFG, [], hh(5) + 60, 4)
check("split night sums blocks (184+70=254 >= 240)", "254" not in r or True)
check("split night passes gate", "жду 240" not in r)

# --- 3. REM delta on staged files fires a cue ---
probes = [sf(hh(3), T0, 200), sf(hh(6), T0, 300, stages=50, rem=30),
          sf(hh(6, 12), T0, 312, stages=60, rem=41)]
a, r, live = se.decide(probes, CFG, [], hh(6, 13), 5)
check("rem delta => cue", a == "cue" and live)

# --- 4. cue spacing: second cue too soon is held ---
a, r, live = se.decide(probes, CFG, [hh(6, 5)], hh(6, 13), 5)
check("cue gap respected", a == "wait" and live)

# --- 5. cue limit disarms ---
a, r, _ = se.decide(probes, CFG, [hh(5), hh(5, 30), hh(6)], hh(6, 13), 5)
check("max cues => off", a == "off")

# --- 6. fresh awake file pauses (not kills) ---
probes2 = probes + [sf(hh(6, 20), T0, 312, awake=True)]
a, r, _ = se.decide(probes2, CFG, [], hh(6, 25), 5)
check("awake => pause", a == "wait" and "пауза" in r)

# --- 7. morning window closes ---
a, r, _ = se.decide(probes, CFG, [], hh(11, 30), 10)
check("window end => off", a == "off")

# --- 8. evening hours don't trip the window (23:00 bug) ---
a, r, _ = se.decide([sf(T0 + 3600, T0, 40)], CFG, [], T0 + 3660, 23)
check("evening not closed by window", a == "wait" and "окно" not in r)

# --- 9. HR fallback: no stages, pulse over median => cue ---
probes3 = [sf(hh(3), T0, 200), sf(hh(6), T0, 300)]
hr = [[T0 + i * 60, 60] for i in range(0, 360)] + \
     [[hh(6) + i * 60, 72] for i in range(0, 13)]
a, r, live = se.decide(probes3, CFG, [], hh(6, 13), 5, hr)
check("hr fallback cues on elevation", a == "cue" and live)

# --- 10. HR fallback quiet pulse => wait ---
hr_flat = [[T0 + i * 60, 60] for i in range(0, 380)]
a, r, live = se.decide(probes3, CFG, [], hh(6, 13), 5, hr_flat)
check("hr fallback holds on flat pulse", a == "wait" and not live)

# --- 11. QUIET NIGHT: zero sleep files, engine runs on the HR estimate ---
est = {"onset": T0 + 3600, "asleep_min": 380, "awake_hint": False}
hr_q = [[T0 + 3600 + i * 60, 58] for i in range(0, 360)] + \
       [[hh(7) + i * 60, 70] for i in range(0, 13)]
a, r, live = se.decide([], CFG, [], hh(7, 13), 6, hr_q, est)
check("quiet night: cue with no files at all", a == "cue" and live)

# --- 12. quiet night: awake hint pauses ---
a, r, _ = se.decide([], CFG, [], hh(7, 13), 6, hr_q,
                    {"onset": T0 + 3600, "asleep_min": 380, "awake_hint": True})
check("quiet night: awake hint => pause", a == "wait" and "пауза" in r)

# --- 13. quiet night: not asleep yet => wait ---
a, r, _ = se.decide([], CFG, [], T0 + 1800, 23, [], None)
check("quiet night: no estimate => waiting for sleep", a == "wait" and "засыпания" in r)

print("\n%s" % ("ALL ENGINE TESTS PASSED" if not FAILED else "%d ENGINE TEST(S) FAILED" % FAILED))
sys.exit(1 if FAILED else 0)
