"""watch_io arbiter: priority ordering, tag dedup, kind-inferred priority."""
from core import watch_io as w

FAILED = 0


def check(name, cond):
    global FAILED
    print(("  OK   " if cond else "  FAIL ") + name)
    if not cond:
        FAILED += 1


def reset():
    w._cmd_q.clear()
    w._notif_q.clear()


# --- priority: enqueue low-then-high, drain returns urgent first ---
reset()
w.enqueue_command({"kind": "cue", "title": "RC"})       # daycue (3)
w.enqueue_command({"kind": "cue"})                       # lucid (1)
w.enqueue_command({"kind": "alarm", "hour": 7})          # alarm (0)
kinds = [c.get("kind") + ("/day" if c.get("title") else "") for c in w.take_commands()]
check("drains alarm > lucid > daycue", kinds == ["alarm", "cue", "cue/day"])
check("drain clears the queue", w.pending()["cmd"] == 0)

# --- FIFO within the same priority (seq tiebreak) ---
reset()
w.enqueue_command({"kind": "vibrate", "n": 1})
w.enqueue_command({"kind": "vibrate", "n": 2})
ns = [c["n"] for c in w.take_commands()]
check("same-priority keeps FIFO order", ns == [1, 2])

# --- tag dedup: latest same-tag note wins, others collapse ---
reset()
w.enqueue_notification({"title": "Задачи (1)"}, tag="todos")
w.enqueue_notification({"title": "Задачи (2)"}, tag="todos")
w.enqueue_notification({"title": "other"}, tag=None)
notes = w.take_notifications()
check("tag dedup collapses to latest", any(n["title"] == "Задачи (2)" for n in notes)
      and not any(n["title"] == "Задачи (1)" for n in notes))
check("untagged note survives dedup", any(n["title"] == "other" for n in notes))

# --- inferred priority mapping ---
check("alarm => P_ALARM", w._infer_pri({"kind": "alarm"}) == w.P_ALARM)
check("plain cue => P_LUCID", w._infer_pri({"kind": "cue"}) == w.P_LUCID)
check("cue+text => P_DAYCUE", w._infer_pri({"kind": "cue", "body": "x"}) == w.P_DAYCUE)
check("unknown => P_UI", w._infer_pri({"kind": "whatever"}) == w.P_UI)

# --- size cap holds ---
reset()
for i in range(40):
    w.enqueue_command({"kind": "vibrate", "i": i})
check("queue capped at MAX_Q", w.pending()["cmd"] == w.MAX_Q)

print("\n%s" % ("ALL WATCH_IO TESTS PASSED" if not FAILED else "%d WATCH_IO TEST(S) FAILED" % FAILED))
import sys
sys.exit(1 if FAILED else 0)
