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
    """Mirror the current open todos to the watch as a notification card.
    Manual (a 'показать на часах' action) — never auto-fired on every edit, so
    it can't spam the wrist; the 'todos' tag dedups repeats into one buzz."""
    title, body = engine.watch_card()
    dashboard.queue_notification(title, body, app="Задачи", tag="todos")
    h._send(json.dumps({"ok": True, "sent": {"title": title, "body": body}}))


# specific prefixes before the bare /todos list route
router.register("POST", "/todos/add", _add)
router.register("POST", "/todos/toggle", _toggle)
router.register("POST", "/todos/edit", _edit)
router.register("POST", "/todos/delete", _delete)
router.register("POST", "/todos/reorder", _reorder)
router.register("POST", "/todos/push", _push)
router.register("GET", "/todos", _list)
