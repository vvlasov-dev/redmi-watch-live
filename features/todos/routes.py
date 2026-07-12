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
    """Manual full sync now (the service also syncs automatically). Reconciles
    open todos into the watch's native Reminders app (they show under
    'Не завершено'); done/removed todos get their reminder deleted."""
    engine.sync_to_watch()
    open_n = len([t for t in engine.all() if not t["done"]])
    h._send(json.dumps({"ok": True, "open": open_n}))


# specific prefixes before the bare /todos list route
router.register("POST", "/todos/add", _add)
router.register("POST", "/todos/toggle", _toggle)
router.register("POST", "/todos/edit", _edit)
router.register("POST", "/todos/delete", _delete)
router.register("POST", "/todos/reorder", _reorder)
router.register("POST", "/todos/push", _push)
router.register("GET", "/todos", _list)
