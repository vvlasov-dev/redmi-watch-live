"""Always-on supervisor: rich live dashboard + auto-reconnect to the watch.

Single-instance, logs to service.log, reads config.json from its own folder.
Run hidden with pythonw.exe service.py
"""
import json
import logging
import os
import socket
import sys
import time
from logging.handlers import RotatingFileHandler

import core.client as c
import dashboard
import notify
import features.sleep.engine as sleep_engine
import features.sleep.routes  # noqa: F401 — registers /sleep,/lucid,/cue,/health routes
import features.todos.routes  # noqa: F401 — registers /todos routes

import re

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- logging FIRST, so even config errors are visible under pythonw ----
_logger = logging.getLogger("rwlive")
_logger.setLevel(logging.INFO)
if not _logger.handlers:
    _h = RotatingFileHandler(os.path.join(HERE, "service.log"),
                             maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
    _logger.addHandler(_h)


def log(m):
    _logger.info(m)


# route the client's internal logs (AUTHENTICATED, sent notification, stalls, …)
# into the service log — client.log() prints to stdout, which is lost under pythonw
c.log = lambda m, *a: log(m % a if a else m)


# ---- config load + validation ----
try:
    with open(os.path.join(HERE, "config.json"), encoding="utf-8-sig") as f:
        cfg = json.load(f)
except Exception as e:
    log("FATAL: cannot read config.json: %s" % e)
    sys.exit(1)

COM = cfg.get("com_port", "COM8")
KEY = str(cfg.get("auth_key", "")).strip()
HTTP_PORT = int(cfg.get("http_port", 8765))
HOST = cfg.get("host", "0.0.0.0")
CSV = cfg.get("csv")
MAX_HR = int(cfg.get("max_hr", 190))
SYNC_INTERVAL = int(cfg.get("sync_interval_sec", 1800))   # auto-sync every 30 min
NOTIFY = bool(cfg.get("notifications", True))
BATTERY_LOW = int(cfg.get("battery_low_pct", 20))
MODE = str(cfg.get("mode", "live")).lower()               # "live" or "sync"
LIVE = MODE != "sync"

_notified_low = {"done": False}

_k = KEY[2:] if KEY.lower().startswith("0x") else KEY
if not re.fullmatch(r"[0-9a-fA-F]{32}", _k):
    log("WARNING: auth_key doesn't look like 32 hex chars (%r) — watch auth will fail" % KEY)


def _com_ports():
    try:
        from serial.tools import list_ports
        return [(p.device, p.description or "") for p in list_ports.comports()]
    except Exception as e:
        log("could not list COM ports: %s" % e)
        return []


def _resolve_com(configured):
    """Return a usable COM port, auto-picking a Bluetooth serial port if the
    configured one is missing or set to 'auto'. Logs clearly on mismatch."""
    ports = _com_ports()
    names = [d for d, _ in ports]
    if configured and configured.upper() != "AUTO" and configured in names:
        return configured
    cands = [d for d, desc in ports
             if "bluetooth" in desc.lower() or "standard serial over bluetooth" in desc.lower()]
    if configured and configured.upper() != "AUTO" and configured not in names:
        log("configured COM %s not present. Available ports: %s" % (configured, ports))
    if cands:
        log("auto-selected Bluetooth serial port %s (candidates: %s)" % (cands[0], cands))
        return cands[0]
    return configured  # last resort: try it anyway


# single instance
_lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
try:
    _lock.bind(("127.0.0.1", 58765))
    _lock.listen(1)
except OSError:
    log("another instance already running; exiting")
    sys.exit(0)

dashboard.load_watch_rids()      # remember which watch reminders are ours
dashboard.load_sleep_session()   # resume an in-flight sleep session after restart
dashboard.LUCID["snapshot"] = sleep_engine.snapshot
dashboard.LUCID["arm"] = sleep_engine.arm
sleep_engine.LOG = log
sleep_engine.start()

# background two-way todos<->watch sync (pushes PC edits, polls wrist completions);
# silent during sleep (quiet night) and when disconnected
import threading as _threading
import features.todos.engine as _todos


def _todos_sync_loop():
    while True:
        try:
            _todos.tick(dashboard.is_connected(), not dashboard.sync_allowed())
        except Exception as e:
            log("todos sync error: %s" % e)
        time.sleep(20)


_threading.Thread(target=_todos_sync_loop, daemon=True).start()
dashboard.configure(max_hr=MAX_HR, mode=MODE, device={
    "model": cfg.get("model", "Redmi Watch 5 Active"),
    "mac": cfg.get("mac", ""),
    "port": COM,
})

# retry the HTTP bind (a just-killed instance may still hold the port briefly)
_httpd = None
for _attempt in range(12):
    try:
        _httpd = dashboard.serve(HTTP_PORT, host=HOST)
        log("dashboard up on http://%s:%d" % (HOST, HTTP_PORT))
        break
    except OSError as e:
        log("bind port %d failed (attempt %d/12): %s" % (HTTP_PORT, _attempt + 1, e))
        time.sleep(2)
if _httpd is None:
    log("FATAL: could not bind dashboard port %d after retries; exiting" % HTTP_PORT)
    sys.exit(1)


def _notify(title, message):
    if NOTIFY:
        try:
            notify.toast(title, message)
        except Exception as e:
            log("notify error: %s" % e)


def on_sample(s):
    dashboard.push_sample(s)
    log("HR=%s steps=%s cal=%s" % (s.get("heartRate"), s.get("steps"), s.get("calories")))
    if CSV:
        try:
            newfile = not os.path.exists(CSV)
            with open(CSV, "a", encoding="utf-8") as fp:
                if newfile:
                    fp.write("timestamp,heartRate,steps,calories\n")
                fp.write("%d,%s,%s,%s\n" % (int(s.get("ts", time.time())),
                                            s.get("heartRate"), s.get("steps"), s.get("calories")))
        except Exception as e:
            log("csv error: %s" % e)


def on_battery(level, charging):
    dashboard.push_battery(level, charging)
    log("battery %s%% charging=%s" % (level, charging))
    if level <= BATTERY_LOW and not charging and not _notified_low["done"]:
        _notified_low["done"] = True
        _notify("Часы: низкий заряд", "Заряд %d%% — поставь на зарядку." % level)
    if level > BATTERY_LOW or charging:
        _notified_low["done"] = False


def on_daily(summary):
    dashboard.push_daily(summary)
    log("daily summary: steps=%s hr_avg=%s spo2=%s stress=%s vitality=%s" % (
        summary.get("steps"), summary.get("hr_avg"),
        summary.get("spo2_avg"), summary.get("stress_avg"), summary.get("vitality")))


def on_device_state(st):
    dashboard.push_device_state(st)


def on_hr_config(cfg):
    dashboard.push_hr_config(cfg)


def on_sleep(sleep):
    dashboard.push_sleep(sleep)
    log("sleep: asleep=%smin deep=%s light=%s rem=%s awake=%s stages=%s" % (
        sleep.get("asleep_min"), sleep.get("deep_min"), sleep.get("light_min"),
        sleep.get("rem_min"), sleep.get("awake_min"), len(sleep.get("stages") or [])))


def on_details(details):
    dashboard.push_details(details)
    mins = details.get("minutes") or []
    hrn = sum(1 for m in mins if m.get("hr"))
    log("details: %d minutes (%d with hr)" % (len(mins), hrn))


def on_sync(res):
    dashboard.mark_sync()
    h = dashboard.S.get("health") or {}
    bat = res.get("battery") or {}
    parts = []
    if h.get("steps") is not None:
        parts.append("%s шагов" % h.get("steps"))
    if h.get("hr_avg"):
        parts.append("пульс ~%s" % h.get("hr_avg"))
    if h.get("spo2_avg"):
        parts.append("SpO₂ %s%%" % h.get("spo2_avg"))
    if bat.get("level") is not None:
        parts.append("батарея %s%%" % bat.get("level"))
    msg = " · ".join(parts) if parts else "данные обновлены"
    log("SYNC complete: %s (days=%s)" % (msg, res.get("days")))
    _notify("Синхронизация часов завершена", msg)


_connected = {"state": False}


def _watch_connected_check(cl):
    # called from the main loop: emit connect/disconnect notifications
    if cl.authenticated and not _connected["state"]:
        _connected["state"] = True
        if LIVE:  # in sync-only the sync-complete toast is enough
            _notify("Часы подключены", "Redmi Watch 5 Active · синхронизация запущена")


# ---- keep the PC awake while the service runs (root cause of BT kernel hangs:
# Windows sleeps mid-connection and the serial read never returns) ----
try:
    import ctypes
    ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
    ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    log("keep-awake armed (system sleep blocked while service runs; display may off)")
except Exception as e:
    log("keep-awake failed: %s" % e)


# ---- self-healing: if LIVE data goes silent too long the serial stack is
# likely wedged (kernel-stuck read survives client-level stall detection).
# Hard-exit; the watchdog task restarts us within 5 min on a fresh port. ----
def _health_monitor():
    grace = time.time() + 300          # allow startup/handshake time
    while True:
        time.sleep(30)
        try:
            if not LIVE:
                continue
            # during the DARK sleep window we deliberately stop streaming, so
            # "no data" is expected, not a fault — don't restart.
            if not dashboard.stream_allowed():
                grace = time.time() + 600
                continue
            last = dashboard.S.get("latest", {}).get("ts") or 0
            if last:
                grace = max(grace, last + 240)
            if time.time() > grace:
                log("HEALTH: no data for >240s and past grace — hard restart via os._exit")
                os._exit(2)
        except Exception:
            pass


import threading as _thr
_thr.Thread(target=_health_monitor, daemon=True).start()

log("service mode = %s (interval=%ds)" % (MODE, SYNC_INTERVAL))

while True:
    try:
        com = _resolve_com(COM)
        log("connecting to watch on %s ..." % com)
        cl = c.LiveClient(com, KEY, on_sample=on_sample, on_battery=on_battery,
                          on_daily=on_daily, on_sync=on_sync, on_sleep=on_sleep,
                          on_details=on_details, on_device_state=on_device_state,
                          on_hr_config=on_hr_config,
                          on_reminder_ack=dashboard.push_reminder_ack,
                          on_reminders_list=dashboard.push_reminders_list,
                          should_sync=dashboard.take_sync_request,
                          sync_gate=dashboard.sync_allowed,
                          stream_gate=dashboard.stream_allowed,
                          take_notifications=dashboard.take_notifications,
                          take_commands=dashboard.take_commands,
                          capture_dir=os.path.join(HERE, "captures"),
                          sync_interval=SYNC_INTERVAL, live=LIVE, debug=False)
        # wrap run so we can detect the connect transition
        import threading
        stop = threading.Event()

        def _watcher():
            while not stop.is_set():
                _watch_connected_check(cl)
                time.sleep(1)
        threading.Thread(target=_watcher, daemon=True).start()
        try:
            cl.run()
        finally:
            stop.set()
    except Exception as e:
        log("client error: %s" % e)
    if _connected["state"]:
        _connected["state"] = False
        if LIVE:
            _notify("Часы отключены", "Связь потеряна — переподключение…")
    if LIVE:
        time.sleep(8)                # live mode: reconnect quickly
    else:
        # sync-only: channel is now free for the phone; wait for the next cycle,
        # but break early if the UI requests a manual sync
        log("sync-only: sleeping %ds (channel free for phone)" % SYNC_INTERVAL)
        waited = 0
        while waited < SYNC_INTERVAL:
            if dashboard.take_sync_request():
                log("manual sync requested; reconnecting now")
                break
            time.sleep(1)
            waited += 1
