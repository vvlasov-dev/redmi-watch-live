"""Single arbiter for everything sent TO the watch over the one BT channel.

Why this exists: there is ONE BT channel — while the PC holds the watch the
phone gets nothing — and a command flood is the suspected cause of the watch's
weekly reboots. So every feature funnels its output here instead of touching the
raw queues. watch_io applies:

  - PRIORITY, so within one client tick the urgent thing goes first:
      alarm/wake (0) > lucid cue (1) > todo mirror (2) > reality-check buzz (3)
  - DEDUP by tag, so a rapidly-refreshed mirror (e.g. todos edited 5× in a row)
    collapses to the latest card instead of buzzing five times.
  - a size cap, so a stuck producer can't grow the queue without bound.

Two sinks, both drained by client.py each loop tick (wired via service.py):
  take_commands()      -> cue / vibrate / alarm / hr_config ...
  take_notifications() -> title/body cards

Legacy callers (dashboard.queue_command / queue_notification) delegate here, so
priority applies everywhere without touching the sensitive night engine: the
priority is inferred from the command kind.
"""
import threading

# lower = more urgent (drained first within a tick)
P_ALARM = 0     # smart-alarm wake, user-pressed vibrate — must not wait
P_LUCID = 1     # REM cue — the window is fleeting
P_UI = 1        # user-initiated UI command — responsive
P_TODO = 2      # todo mirror to the watch — can wait a tick
P_DAYCUE = 3    # daytime reality-check buzz — lowest, never crowds anything out

MAX_Q = 20

_lock = threading.Lock()
_cmd_q = []      # dicts: {pri, seq, spec, tag}
_notif_q = []    # dicts: {pri, seq, note, tag}
_seq = 0


def _next():
    global _seq
    _seq += 1
    return _seq


def _infer_pri(spec):
    k = spec.get("kind")
    if k in ("alarm", "vibrate", "vibrate_stop", "delete_alarms"):
        return P_ALARM
    if k == "cue":
        # daytime reality-check carries reminder text; the silent lucid cue does not
        return P_DAYCUE if (spec.get("title") or spec.get("body")) else P_LUCID
    return P_UI


def enqueue_command(spec, priority=None, tag=None):
    if priority is None:
        priority = _infer_pri(spec)
    with _lock:
        if tag is not None:
            _cmd_q[:] = [x for x in _cmd_q if x["tag"] != tag]
        if len(_cmd_q) < MAX_Q:
            _cmd_q.append({"pri": priority, "seq": _next(), "spec": spec, "tag": tag})


def enqueue_notification(note, priority=P_UI, tag=None):
    with _lock:
        if tag is not None:
            _notif_q[:] = [x for x in _notif_q if x["tag"] != tag]
        if len(_notif_q) < MAX_Q:
            _notif_q.append({"pri": priority, "seq": _next(), "note": note, "tag": tag})


def take_commands():
    with _lock:
        out = [x["spec"] for x in sorted(_cmd_q, key=lambda x: (x["pri"], x["seq"]))]
        _cmd_q.clear()
        return out


def take_notifications():
    with _lock:
        out = [x["note"] for x in sorted(_notif_q, key=lambda x: (x["pri"], x["seq"]))]
        _notif_q.clear()
        return out


def pending():
    with _lock:
        return {"cmd": len(_cmd_q), "notif": len(_notif_q)}
