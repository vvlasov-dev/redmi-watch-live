"""Local SQLite persistence for watch history.

Survives restarts and enables day/week/month trends. Tables:
  samples(ts, hr, steps, cal)              -- realtime stream (while connected)
  daily(date_ts, ...aggregates..., json)   -- one row per day (daily summary)
  sleep(date_ts, ...durations..., json)    -- one row per night
  minutes(ts, hr, steps, cal, spo2, stress) -- per-minute detail (90d)
"""
import json as _json
import sqlite3
import threading
import time

_lock = threading.Lock()
_db = None


def init(path):
    global _db
    _db = sqlite3.connect(path, check_same_thread=False)
    _db.execute("PRAGMA journal_mode=WAL")
    _db.executescript(
        """
        CREATE TABLE IF NOT EXISTS samples(
            ts INTEGER PRIMARY KEY, hr INTEGER, steps INTEGER, cal INTEGER);
        CREATE TABLE IF NOT EXISTS daily(
            date_ts INTEGER PRIMARY KEY, steps INTEGER, calories INTEGER,
            hr_avg INTEGER, hr_resting INTEGER, hr_max INTEGER, hr_min INTEGER,
            spo2_avg INTEGER, spo2_min INTEGER, spo2_max INTEGER,
            stress_avg INTEGER, stress_min INTEGER, stress_max INTEGER,
            vitality INTEGER, standing_hours INTEGER, json TEXT);
        CREATE TABLE IF NOT EXISTS sleep(
            date_ts INTEGER PRIMARY KEY, asleep_min INTEGER,
            deep_min INTEGER, light_min INTEGER, rem_min INTEGER, awake_min INTEGER, json TEXT);
        CREATE TABLE IF NOT EXISTS minutes(
            ts INTEGER PRIMARY KEY, hr INTEGER, steps INTEGER, cal INTEGER,
            spo2 INTEGER, stress INTEGER);
        CREATE TABLE IF NOT EXISTS todos(
            id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
            done INTEGER DEFAULT 0, ord INTEGER DEFAULT 0,
            created_ts INTEGER, done_ts INTEGER);
        """
    )
    # migrations: add columns to a pre-existing todos table (IF NOT EXISTS won't)
    for col, decl in (("watch_rid", "INTEGER"), ("due_ts", "INTEGER")):
        try:
            _db.execute("ALTER TABLE todos ADD COLUMN %s %s" % (col, decl))
        except Exception:
            pass   # already there
    _db.commit()
    return _db


# ---------- todos (PC-edited task list, mirrored to the watch) ----------
def todos_all():
    if _db is None:
        return []
    with _lock:
        cur = _db.execute(
            "SELECT id,text,done,ord,created_ts,done_ts,watch_rid,due_ts "
            "FROM todos ORDER BY done, ord, id")
        rows = cur.fetchall()
    return [{"id": r[0], "text": r[1], "done": bool(r[2]), "ord": r[3],
             "created_ts": r[4], "done_ts": r[5], "watch_rid": r[6], "due_ts": r[7]}
            for r in rows]


def todos_set_watch_rid(tid, rid):
    if _db is None:
        return
    with _lock:
        _db.execute("UPDATE todos SET watch_rid=? WHERE id=?",
                    (int(rid) if rid is not None else None, int(tid)))
        _db.commit()


def todos_add(text, ord=None):
    """Insert a new open todo at the given order (default: end). Returns its id."""
    if _db is None:
        return None
    now = int(time.time())
    with _lock:
        if ord is None:
            row = _db.execute("SELECT COALESCE(MAX(ord),0)+1 FROM todos").fetchone()
            ord = row[0] if row else 0
        cur = _db.execute(
            "INSERT INTO todos(text,done,ord,created_ts) VALUES(?,0,?,?)",
            (text, int(ord), now))
        _db.commit()
        return cur.lastrowid


def todos_set_done(tid, done):
    if _db is None:
        return
    with _lock:
        _db.execute("UPDATE todos SET done=?, done_ts=? WHERE id=?",
                    (1 if done else 0, int(time.time()) if done else None, int(tid)))
        _db.commit()


def todos_edit(tid, text):
    if _db is None:
        return
    with _lock:
        _db.execute("UPDATE todos SET text=? WHERE id=?", (text, int(tid)))
        _db.commit()


def todos_delete(tid):
    if _db is None:
        return
    with _lock:
        _db.execute("DELETE FROM todos WHERE id=?", (int(tid),))
        _db.commit()


def todos_reorder(ids):
    """Persist a new open-list order: ids in the desired top-to-bottom order."""
    if _db is None or not ids:
        return
    with _lock:
        _db.executemany("UPDATE todos SET ord=? WHERE id=?",
                        [(i, int(tid)) for i, tid in enumerate(ids)])
        _db.commit()


def add_sample(ts, hr, steps, cal):
    if _db is None:
        return
    with _lock:
        _db.execute("INSERT OR REPLACE INTO samples(ts,hr,steps,cal) VALUES(?,?,?,?)",
                    (int(ts), int(hr or 0), int(steps or 0), int(cal or 0)))
        _db.commit()


def upsert_daily(d):
    if _db is None:
        return
    with _lock:
        _db.execute(
            """INSERT OR REPLACE INTO daily(date_ts,steps,calories,hr_avg,hr_resting,hr_max,hr_min,
               spo2_avg,spo2_min,spo2_max,stress_avg,stress_min,stress_max,vitality,standing_hours,json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (d.get("date_ts"), d.get("steps"), d.get("calories"), d.get("hr_avg"), d.get("hr_resting"),
             d.get("hr_max"), d.get("hr_min"), d.get("spo2_avg"), d.get("spo2_min"), d.get("spo2_max"),
             d.get("stress_avg"), d.get("stress_min"), d.get("stress_max"), d.get("vitality"),
             d.get("standing_hours"), _json.dumps(d)))
        _db.commit()


def upsert_minutes(rows):
    if _db is None or not rows:
        return
    with _lock:
        _db.executemany(
            "INSERT OR REPLACE INTO minutes(ts,hr,steps,cal,spo2,stress) VALUES(?,?,?,?,?,?)",
            [(int(r["ts"]), r.get("hr"), r.get("steps"), r.get("cal"), r.get("spo2"), r.get("stress"))
             for r in rows if r.get("ts")])
        _db.commit()


def load_minutes(since_ts, limit=2000):
    if _db is None:
        return []
    with _lock:
        cur = _db.execute(
            "SELECT ts,hr,steps,cal,spo2,stress FROM minutes WHERE ts>=? ORDER BY ts LIMIT ?",
            (int(since_ts), limit))
        rows = cur.fetchall()
    return [{"ts": r[0], "hr": r[1], "steps": r[2], "cal": r[3], "spo2": r[4], "stress": r[5]}
            for r in rows]


def upsert_sleep(s):
    if _db is None:
        return
    with _lock:
        _db.execute(
            """INSERT OR REPLACE INTO sleep(date_ts,asleep_min,deep_min,light_min,rem_min,awake_min,json)
               VALUES(?,?,?,?,?,?,?)""",
            (s.get("bed_ts") or s.get("date_ts"), s.get("asleep_min"), s.get("deep_min"),
             s.get("light_min"), s.get("rem_min"), s.get("awake_min"), _json.dumps(s)))
        _db.commit()


def hr_minutes_from_samples(since_ts):
    """Per-minute (ts, avg hr, max steps) from the realtime stream — lets the
    sleep engine see the whole night WITHOUT syncing the watch (quiet night)."""
    if _db is None:
        return []
    with _lock:
        cur = _db.execute(
            """SELECT (ts/60)*60 AS m, CAST(AVG(hr) AS INT), MAX(steps)
               FROM samples WHERE ts>=? AND hr>0 GROUP BY m ORDER BY m""",
            (int(since_ts),))
        rows = cur.fetchall()
    return [{"ts": r[0], "hr": r[1], "steps": r[2]} for r in rows]


def load_recent_samples(since_ts, limit=5000):
    if _db is None:
        return []
    with _lock:
        cur = _db.execute("SELECT ts,hr,steps,cal FROM samples WHERE ts>=? ORDER BY ts DESC LIMIT ?",
                          (int(since_ts), limit))
        rows = cur.fetchall()
    rows.reverse()
    return [{"t": r[0], "hr": r[1], "steps": r[2], "cal": r[3]} for r in rows]


def load_days(n=30):
    if _db is None:
        return []
    with _lock:
        cur = _db.execute("SELECT json FROM daily ORDER BY date_ts DESC LIMIT ?", (n,))
        rows = cur.fetchall()
    days = [_json.loads(r[0]) for r in rows if r[0]]
    days.reverse()
    return days


def load_sleep_latest():
    if _db is None:
        return None
    with _lock:
        cur = _db.execute("SELECT json FROM sleep ORDER BY date_ts DESC LIMIT 1")
        r = cur.fetchone()
    return _json.loads(r[0]) if r and r[0] else None


def load_sleeps(n=30):
    if _db is None:
        return []
    with _lock:
        cur = _db.execute("SELECT json FROM sleep ORDER BY date_ts DESC LIMIT ?", (n,))
        rows = cur.fetchall()
    out = [_json.loads(r[0]) for r in rows if r[0]]
    out.reverse()
    return out


def counts():
    if _db is None:
        return {"daily": 0, "sleep": 0}
    with _lock:
        d = _db.execute("SELECT COUNT(*) FROM daily").fetchone()[0]
        s = _db.execute("SELECT COUNT(*) FROM sleep").fetchone()[0]
    return {"daily": d, "sleep": s}


def prune(samples_keep=14, minutes_keep=90):
    """Prune only RAW high-frequency data. Daily summaries and sleep records
    are aggregates and kept FOREVER (month/year trends need them); per-minute
    rows now live 90 days (~5 MB) so nights stay reviewable."""
    if _db is None:
        return
    now = int(time.time())
    with _lock:
        _db.execute("DELETE FROM samples WHERE ts<?", (now - samples_keep * 86400,))
        _db.execute("DELETE FROM minutes WHERE ts<?", (now - minutes_keep * 86400,))
        _db.commit()
