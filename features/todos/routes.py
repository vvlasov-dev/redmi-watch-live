"""Todos HTTP routes — all PC-driven. Registered into core.router at import.

GET  /todos          -> {"todos": [...]}
POST /todos/add      {text}         -> {"ok", "id", "todos"}
POST /todos/toggle   {id}           -> {"ok", "todos"}
POST /todos/edit     {id, text}     -> {"ok", "todos"}
POST /todos/delete   {id}           -> {"ok", "todos"}
POST /todos/reorder  {ids:[...]}    -> {"ok", "todos"}
"""
import json

import dashboard
from core import router
from features.todos import engine


def _list(h):
    h._send(json.dumps({"todos": engine.all()}))


def _add(h):
    p = h._read_json()
    h._send(json.dumps(engine.add(p.get("text"))))


def _toggle(h):
    p = h._read_json()
    h._send(json.dumps(engine.toggle(p.get("id"))))


def _edit(h):
    p = h._read_json()
    h._send(json.dumps(engine.edit(p.get("id"), p.get("text"))))


def _delete(h):
    p = h._read_json()
    h._send(json.dumps(engine.delete(p.get("id"))))


def _reorder(h):
    p = h._read_json()
    h._send(json.dumps(engine.reorder(p.get("ids"))))


def _push(h):
    """Mirror open todos into the watch's native Reminders app (Напоминания),
    where they appear under 'Не завершено' — a real on-wrist checklist.

    Reconcile: delete the reminders WE created last time (tracked by id, so the
    user's own reminders are never touched), then create one per open todo.
    Default time = tomorrow 09:00 (a task with an explicit due keeps its own)."""
    import time as _t
    old = dashboard.clear_watch_reminders()
    if old:
        dashboard.queue_command({"kind": "reminder_delete", "ids": old})
    lt = _t.localtime(_t.time() + 86400)   # tomorrow, 09:00 default
    open_ = [t for t in engine.all() if not t["done"]][:20]   # watch caps ~50; be gentle
    for t in open_:
        due = t.get("due_ts")
        d = _t.localtime(due) if due else lt
        dashboard.queue_command({"kind": "reminder_create", "title": t["text"],
                                 "y": d.tm_year, "mo": d.tm_mon, "d": d.tm_mday,
                                 "h": (d.tm_hour if due else 9), "mi": (d.tm_min if due else 0)})
    h._send(json.dumps({"ok": True, "count": len(open_)}))


# specific prefixes before the bare /todos list route
router.register("POST", "/todos/add", _add)
router.register("POST", "/todos/toggle", _toggle)
router.register("POST", "/todos/edit", _edit)
router.register("POST", "/todos/delete", _delete)
router.register("POST", "/todos/reorder", _reorder)
router.register("POST", "/todos/push", _push)
router.register("GET", "/todos", _list)
