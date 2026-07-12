# MIGRATION — layered → vertical feature slices

> **Status (2026-07-12): backend refactor DONE + first feature shipped.**
> ✅ 1 state · ✅ 2 store · ✅ 3 client+sleep-engine · ✅ 4 router split ·
> ✅ 5 watch_io arbiter · ✅ `features/todos/` backend (CRUD + watch mirror).
> **Remaining: the frontend split + the todos panel (browser-in-loop, daytime).**
> Everything below the frontend section is history/plan; the backend plan is
> complete and deployed. 5 test suites green.


**Goal:** restructure so each feature (sleep, todos, workouts, audio…) is a
self-contained folder, and the fragile shared plumbing lives in a thin `core/`
that features register into but never edit. Driver: **editing one feature must
touch a small folder, not the 52 KB `dashboard.py` or the 88 KB HTML** — this is
what keeps agent iteration cheap (few tokens) and safe.

Decisions locked (2026-07-10): **big-bang refactor first**, then todos on the
clean skeleton. Todos = **PC edits, watch views** (read-only mirror), stored
locally in `history.db`.

## Target structure

```
core/    client.py · store.py · service.py · state.py (S + _lock) · router.py · watch_io.py
features/
  sleep/    engine.py · parsers.py · routes.py · panel.html · panel.js · tests/
  todos/    engine.py · routes.py · panel.html · panel.js · tests/
app/     shell.html (frame + theme + render harness + shared chart builders)
FEATURES.md · docs/MIGRATION.md
```

## Hard constraint

**Never run this refactor in the hours before a sleep session.** The night
engine (`client.py` + `sleep_engine.py` + `service.py` reconnect) must be
verified green and left running before ~22:00. Do the big moves on a morning
with a full runway. Every step: `python run_tests.py` green → deploy →
`/state` healthy → only then next step.

## Backend plan (order matters)

1. **`core/state.py`** — extract `S` + `_lock` (+ `_save_sleep_session`). Widest
   blast radius: `client.py` and `sleep_engine.py` do `dashboard.S` /
   `dashboard._lock` everywhere. **Shim:** `dashboard.py` re-exports
   `from core.state import S, _lock` so existing refs keep working; migrate refs
   feature-by-feature afterward, not big-bang.
2. **`core/` moves** — `client.py`, `store.py`, `service.py` relocate (imports
   only; no logic change). Keep top-level shims (`client = core.client`) until
   every importer is updated.
3. **`features/sleep/`** — move `sleep_engine.py`→`engine.py`,
   sleep parsers out of `activity.py`→`parsers.py`.
4. **`core/router.py` + `features/*/routes.py`** — split the request handler:
   router owns `/state` + static + dispatch; each feature registers its routes
   (`/sleep/*`, `/lucid/*`, `/cue`, `/health/*` → sleep). `push_sleep` /
   `finalize_night` / `_merged_night` move into `features/sleep/`.
5. **`core/watch_io.py`** — the output arbiter. All features enqueue here instead
   of calling `queue_command` directly; it applies **priority + rate-limit** on
   the single BT channel (alarm-crit > lucid cue > todo push > reality-check
   buzz). This is what stops feature collisions like the 2026-07-10 lucid-vs-
   alarm-in-REM clash and the suspected BT-flood reboots.

## Frontend plan (the finicky part — browser-verify each step)

The HTML is a Claude Design Canvas file: one `text/x-dc` script with **shared**
chart builders and a **monolithic `renderVals()`**. It does NOT cut into
per-feature JS by copy-paste. Real steps:

1. **`app/shell.html`** = head + `<style>` + the `x-dc` harness + the shared
   builders (`mkArea…mkDayDots`). These are cross-feature infra, they live in the
   shell, not a feature.
2. **`renderVals()` → dispatcher**: split its body into `renderSleep(st)`,
   `renderActivity(st)`, `renderHr(st)`, each returning its own `*El` bindings;
   `renderVals` merges them. This is the actual refactor — do it incrementally,
   one panel at a time, screenshotting after each so the page never regresses.
3. **`features/<f>/panel.html` + `panel.js`** = that feature's markup card(s) +
   its `render<F>()`. Server composes `shell.html` + each registered
   `panel.html` into the served page (add a compose step to `core/router.py`).
4. **Invariant to check:** composed page === current page (visual + no console
   errors). Deploy is still HTML-only copy when no Python changed.

## First new slice — `features/todos/`

- **Store:** `todos(id, text, done, order, created_ts, done_ts)` table in
  `history.db` (aggregates-forever tier).
- **`engine.py`:** CRUD + ordering; no watch push logic here.
- **`routes.py`:** `/todos` (list), `/todos/add`, `/todos/toggle`, `/todos/reorder`,
  `/todos/delete` — all PC-driven.
- **`panel.html`/`panel.js`:** dashboard list with add/check/drag.
- **Watch view:** on change (debounced) enqueue a `watch_io` push of the top-N
  open todos. **Open protocol question first:** does the Xiaomi/Gadgetbridge
  path support a calendar/reminder surface (persistent list) or only
  notifications? If only notifications → the watch shows "current + next" as a
  low-priority notification refreshed on change. Research before building the
  watch side; the PC side is unblocked regardless.

## Later slices (not now)

- **`features/workouts/`** — parser (backlog #18, needs a recorded workout
  capture) + panel + a `/workouts/analyze` endpoint that runs an AI prompt over
  the session. Fits the sleep pattern.
- **`features/audio/`** — GATED on a feasibility spike: our SPP channel carries
  data/notifications, **not audio**; the watch mic is HFP-during-call only, not a
  continuous ambient stream to the PC. Prove capture is even possible before any
  architecture. Also weigh privacy / battery / single-BT-channel cost.
