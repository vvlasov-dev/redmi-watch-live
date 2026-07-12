"""Todos feature logic — validation + shaping over core.store.

PC is the source of truth; the watch gets a read-only mirror (pushed elsewhere,
via watch_io in a later step). No BT logic here. Every mutation returns the
fresh full list so the caller (route) can answer with one round-trip.
"""
from core import store

MAX_LEN = 200


def _clean(text):
    return (text or "").strip()[:MAX_LEN]


def all():
    return store.todos_all()


def add(text):
    t = _clean(text)
    if not t:
        return {"ok": False, "error": "empty", "todos": store.todos_all()}
    tid = store.todos_add(t)
    mark_dirty()
    return {"ok": True, "id": tid, "todos": store.todos_all()}


def toggle(tid):
    cur = {t["id"]: t for t in store.todos_all()}
    if int(tid) not in cur:
        return {"ok": False, "error": "not found", "todos": list(cur.values())}
    store.todos_set_done(tid, not cur[int(tid)]["done"])
    mark_dirty()
    return {"ok": True, "todos": store.todos_all()}


def edit(tid, text):
    t = _clean(text)
    if not t:
        return {"ok": False, "error": "empty", "todos": store.todos_all()}
    store.todos_edit(tid, t)
    mark_dirty()
    return {"ok": True, "todos": store.todos_all()}


def delete(tid):
    # drop this todo's watch reminder too (its row — and watch_rid — vanish next)
    t = next((x for x in store.todos_all() if x["id"] == int(tid)), None)
    if t and t.get("watch_rid"):
        _queue({"kind": "reminder_delete", "ids": [t["watch_rid"]]})
    store.todos_delete(tid)
    mark_dirty()
    return {"ok": True, "todos": store.todos_all()}


def reorder(ids):
    store.todos_reorder([int(i) for i in (ids or [])])
    return {"ok": True, "todos": store.todos_all()}


def open_top(n=5):
    """The top-N open todos — what a watch mirror would show."""
    return [t for t in store.todos_all() if not t["done"]][:n]


def watch_card(n=5):
    """(title, body) for the watch mirror. The watch's only proven persistent
    surface is a notification card, so we render the top-N open todos as a
    numbered list; the tag lets watch_io dedup rapid refreshes into one buzz."""
    open_ = [t for t in store.todos_all() if not t["done"]]
    if not open_:
        return ("Задачи", "Список пуст — всё сделано")
    lines = ["%d. %s" % (i + 1, t["text"]) for i, t in enumerate(open_[:n])]
    more = len(open_) - min(n, len(open_))
    if more > 0:
        lines.append("... ещё %d" % more)
    return ("Задачи (%d)" % len(open_), "\n".join(lines))


# ---------------------------------------------------------------------------
# Two-way watch sync (native Reminders app). PC is the source of truth; the
# watch is a live mirror. dashboard is imported lazily so unit tests that only
# exercise CRUD don't drag in the BT/HTTP stack.
#   PC -> watch : create a reminder per open todo, delete a done/removed todo's.
#   watch -> PC : if a reminder WE created vanished from the watch's list, the
#                 user completed/removed it on the wrist -> mark the todo done.
# The reminder id is the join key: stored per-todo (watch_rid). Creates and
# their acks are FIFO over the single BT channel, so a pending queue maps each
# ack back to the todo it was created for.
# ---------------------------------------------------------------------------
import collections
import time as _time

_pending = collections.deque()   # todo ids awaiting a create ack
_dirty = False                   # PC changed since last push
_last_get = 0                    # last watch->PC GET


def _queue(spec):
    import dashboard
    dashboard.queue_command(spec)


def mark_dirty():
    global _dirty
    _dirty = True


def _reminder_time(t):
    """(y,mo,d,h,mi) for a todo: its due_ts, else tomorrow 09:00."""
    if t.get("due_ts"):
        lt = _time.localtime(t["due_ts"])
        return lt.tm_year, lt.tm_mon, lt.tm_mday, lt.tm_hour, lt.tm_min
    lt = _time.localtime(_time.time() + 86400)
    return lt.tm_year, lt.tm_mon, lt.tm_mday, 9, 0


def sync_to_watch():
    """PC -> watch reconcile. Open todos with no reminder get one; done todos
    that still carry a reminder get it deleted."""
    for t in store.todos_all():
        if t["done"]:
            if t.get("watch_rid"):
                _queue({"kind": "reminder_delete", "ids": [t["watch_rid"]]})
                store.todos_set_watch_rid(t["id"], None)
        else:
            if not t.get("watch_rid") and t["id"] not in _pending:
                y, mo, d, h, mi = _reminder_time(t)
                _pending.append(t["id"])
                _queue({"kind": "reminder_create", "title": t["text"],
                        "y": y, "mo": mo, "d": d, "h": h, "mi": mi})


def on_reminder_ack(rid):
    """A createReminder ack came back (FIFO) -> bind it to the oldest pending todo."""
    if _pending:
        tid = _pending.popleft()
        store.todos_set_watch_rid(tid, rid)


_seen_rids = set()   # reminder ids ever observed present on the watch
_miss = {}           # rid -> consecutive GETs missing


def reconcile_from_watch(watch_list):
    """watch -> PC. A reminder we created, that WAS present and has now been
    absent for two consecutive polls, means the user completed/removed it on the
    wrist -> mark the todo done. Hardened against the watch's transient
    empty/partial GET replies (observed 2026-07-12: a spurious empty list would
    otherwise mark every task done):
      - ignore an empty reply while we still expect reminders,
      - require the id to have been SEEN before, and MISSING twice running."""
    watch_ids = {r.get("id") for r in (watch_list or [])}
    open_rids = {t.get("watch_rid") for t in store.todos_all()
                 if t.get("watch_rid") and not t["done"]}
    if open_rids and not watch_ids:
        return False   # empty reply while items are expected = glitch, ignore
    _seen_rids.update(watch_ids)
    changed = False
    for t in store.todos_all():
        rid = t.get("watch_rid")
        if not rid or t["done"]:
            continue
        if rid in watch_ids:
            _miss[rid] = 0
        elif rid in _seen_rids:
            _miss[rid] = _miss.get(rid, 0) + 1
            if _miss[rid] >= 2:
                store.todos_set_done(t["id"], True)
                store.todos_set_watch_rid(t["id"], None)
                _miss.pop(rid, None)
                changed = True
    return changed


def tick(connected, asleep, now=None):
    """Called ~every 20s by the service. Pushes pending PC changes and, less
    often, GETs the watch list to detect wrist-side completions. Silent during
    sleep (quiet night) and when disconnected."""
    global _dirty, _last_get
    if asleep or not connected:
        return
    if _dirty:
        sync_to_watch()
        _dirty = False
    now = now or _time.time()
    if now - _last_get >= 90:      # watch->PC poll, gentle on the BT channel
        _last_get = now
        _queue({"kind": "reminders_get"})
