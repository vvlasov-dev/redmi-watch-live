"""Shared in-memory state and its persistence helper.

Single source of truth: S holds all live data; _lock guards every mutation.
dashboard.py re-exports S, _lock, _save_sleep_session so existing callers keep
working without changes during the migration to vertical feature slices.
"""
import json
import os
import threading
from collections import deque

_lock = threading.Lock()

_SESS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".sleep_session.json"
)

S = {
    "session_start": None, "count": 0, "last_ts": 0, "last_hr_ts": 0,
    "hr_min": None, "hr_max": None, "hr_sum": 0, "hr_n": 0,
    "steps_first": None, "steps_last": 0, "cal_last": 0, "standing_last": 0,
    "latest": {"ts": 0, "heartRate": 0, "steps": 0, "calories": 0, "standingHours": 0},
    "series": deque(maxlen=5000),
    "zone_sec": [0, 0, 0, 0, 0],
    "battery": None,
    "health": None,
    "health_ts": 0,
    "days": [],
    "sleep": None,
    "sleeps": [],
    "device_state": None,
    "device_state_ts": 0,
    "hr_config": None,
    "hr_config_ts": 0,
    "sleep_session": {"active": False, "start_ts": 0, "last_harvest": 0,
                      "probes": [], "cues_sent": 0},
    "day_minutes": [],
    "last_sync": 0,
}


def _save_sleep_session():
    try:
        with open(_SESS_FILE, "w", encoding="utf-8") as f:
            json.dump(S["sleep_session"], f, ensure_ascii=False)
    except Exception:
        pass
