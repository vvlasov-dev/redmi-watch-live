"""Sleep / lucid HTTP routes. Registered into core.router at import.

Handlers take the live request handler `h` and use h._send / h._read_json.
The side-effect functions (session control, cue queue, lucid arm) still live in
dashboard for now; they move here in a later step. Import this module from the
composition root (service.py) to trigger registration.
"""
import json

import dashboard
from core import router


def _ok(h):
    h._send(json.dumps({"ok": True}))


def cue(h):
    dashboard.queue_command({"kind": "cue"})
    _ok(h)


def hrcfg_get(h):
    dashboard.queue_command({"kind": "hr_config_get"})
    _ok(h)


def advanced_on(h):
    dashboard.queue_command({"kind": "hr_config_get"})   # refresh first
    dashboard.queue_command({"kind": "advanced_on"})
    _ok(h)


def lucid_on(h):
    dashboard.LUCID["arm"](True)
    _ok(h)


def lucid_off(h):
    dashboard.LUCID["arm"](False)
    _ok(h)


def sleep_start(h):
    dashboard.start_sleep_session()
    dashboard.LUCID["arm"](True)   # recording implies REM cues — one button
    dashboard.request_sync()       # kick off an immediate harvest baseline
    _ok(h)


def sleep_stop(h):
    dashboard.stop_sleep_session(manual=True)
    dashboard.LUCID["arm"](False)
    _ok(h)


# order preserved from the old monolithic chain (specific prefixes first)
router.register("POST", "/cue", cue)
router.register("POST", "/health/hrcfg_get", hrcfg_get)
router.register("POST", "/health/advanced_on", advanced_on)
router.register("POST", "/lucid/on", lucid_on)
router.register("POST", "/lucid/off", lucid_off)
router.register("POST", "/sleep/start", sleep_start)
router.register("POST", "/sleep/stop", sleep_stop)
