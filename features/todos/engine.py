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
    return {"ok": True, "id": tid, "todos": store.todos_all()}


def toggle(tid):
    cur = {t["id"]: t for t in store.todos_all()}
    if int(tid) not in cur:
        return {"ok": False, "error": "not found", "todos": list(cur.values())}
    store.todos_set_done(tid, not cur[int(tid)]["done"])
    return {"ok": True, "todos": store.todos_all()}


def edit(tid, text):
    t = _clean(text)
    if not t:
        return {"ok": False, "error": "empty", "todos": store.todos_all()}
    store.todos_edit(tid, t)
    return {"ok": True, "todos": store.todos_all()}


def delete(tid):
    store.todos_delete(tid)
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
