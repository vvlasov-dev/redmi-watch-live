# FEATURES — code map (read this first to orient)

One-screen map so a change can be located without grepping the tree. The
structure is now **vertical slices**: fragile shared plumbing in `core/`,
each feature a self-contained folder in `features/`. Editing one feature touches
a small folder, not the old 50 KB `dashboard.py`. Top-level `client.py`,
`store.py`, `sleep_engine.py` are thin **shims** re-exporting the moved code, so
old imports still work. Migration status + what's left: `docs/MIGRATION.md`.

## core/ — shared plumbing (features register into it, never edit it)

| File | Role | Key entry points |
|---|---|---|
| `core/state.py` | in-memory state `S` + `_lock` + session persistence | `S`, `_lock`, `_save_sleep_session` |
| `core/store.py` | SQLite (days/sleep forever, minutes 90d, samples 14d, todos) | `upsert_sleep/daily/minutes`, `load_*`, `todos_*` |
| `core/client.py` | BT SPP protocol state machine, frame build/parse, run loop | `build_*` builders, `_send_device_command`, realtime re-arm |
| `core/router.py` | HTTP route registry + Handler dispatch (first-prefix-wins) | `register`, `set_default_get`, `serve` |
| `core/watch_io.py` | single BT-output arbiter: priority + tag-dedup + cap | `enqueue_command/notification`, `take_*`, `_infer_pri` |

## features/ — vertical slices

| Slice | Files | What |
|---|---|---|
| `features/sleep/` | `engine.py` · `routes.py` | lucid cues, smart wake, auto-night, daycue; `/sleep`,`/lucid`,`/cue`,`/health` |
| `features/todos/` | `engine.py` · `routes.py` | PC task list + watch mirror; `/todos/*` |

## app assembly (still in dashboard.py for now)

| File | Role |
|---|---|
| `dashboard.py` | `/state` model (`snapshot`), push_* ingest, sleep session control, core+watch-io routes, serve() → router |
| `activity.py` | byte-exact activity-file parsers (`parse_sleep`, `parse_sleep_stages`, daily/details) |
| `service.py` | supervisor: startup, reconnect loop, wiring; **composition root** (imports feature route modules to register them) |
| `miniproto.py` `spp.py` `xcrypto.py` | protobuf / V2 framing / AES-CTR+CRC primitives |
| `tray.py` `notify.py` `watch_*` `morning_report.py` `demo_state.py` | tray, notify helpers, Stop-hook, morning report, `/demo` fixture |

## HTTP endpoints

| Path | Does | Feature |
|---|---|---|
| `GET /state` `/state_demo` `/data` `/export` `/sync` | UI model / demo / raw / CSV / force-sync | core (dashboard) |
| `POST /notify` `/vibrate*` `/alarm*` | notification / find-device / hardware alarm | watch-io (dashboard) |
| `POST /sleep/start` `/sleep/stop` | session control (+ finalize on stop) | sleep |
| `POST /lucid/on` `/lucid/off` `/cue` | lucid master / manual cue | sleep |
| `POST /health/hrcfg_get` `/health/advanced_on` | HR/REM config | sleep |
| `GET /todos` · `POST /todos/{add,toggle,edit,delete,reorder,push}` | task CRUD + watch mirror | todos |

Route order matters (specific before parent); registration order = dispatch
order. See `core/router.py` header.

## Frontend (`index.dc.html` — Claude Design Canvas, monolithic)

Still one `text/x-dc` script: shared chart builders + monolithic `renderVals()`.
The per-feature split (`app/shell.html` + `renderVals` → `render<Feature>()`)
and the **todos panel** are the remaining frontend work — needs a browser in the
loop (see `docs/MIGRATION.md`). No todos UI yet; the backend is done and
reachable at `/todos`.

## Not built / blocked

- **audio capture** — infeasible on this watch (SPP has no audio surface, mic is
  call-only HFP). Full reasoning + source-agnostic alternative:
  `docs/AUDIO_SPIKE.md`.
- **workouts** — needs a recorded workout `.bin` capture first (backlog #18),
  then `features/workouts/` parser + panel + AI-analysis endpoint.
