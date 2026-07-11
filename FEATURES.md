# FEATURES — code map (read this first to orient)

One-screen map of the codebase so a change can be located without grepping the
whole tree. Structure today is **layered** (one big `dashboard.py` + one
`index.dc.html`); `docs/MIGRATION.md` describes the move to **vertical feature
slices** (`core/` + `features/`) that makes each edit touch a small folder.

## Backend files

| File | Role | Key entry points |
|---|---|---|
| `client.py` | BT SPP protocol state machine, frame build/parse, run loop | `build_gentle_cue`, `build_notification`, `build_hr_config_*`, `_send_device_command`, realtime re-arm |
| `activity.py` | Byte-exact activity-file parsers | `parse_sleep`, `parse_sleep_stages`, `is_sleep_stages`, daily/details parsers |
| `dashboard.py` | In-memory state `S` + HTTP server + `/state` model + all routes | `push_sleep`, `push_details`, `finalize_night`, `_merged_night`, `start/stop_sleep_session`, `queue_command`, request handler |
| `sleep_engine.py` | Sleep/lucid engine: pure `decide()` + 30s `_tick` | `decide`, `_wake_ok_phase`, `_wake_tick`, `_auto_tick`, `_daycue_tick`, `night_estimate`, `restore`, `arm` |
| `store.py` | SQLite persistence (days/sleep forever, minutes 90d, samples 14d) | `upsert_sleep`, `upsert_daily`, `upsert_minutes`, `load_minutes` |
| `service.py` | Supervisor: hardened startup, reconnect loop, wiring | `_health_monitor`, keep-awake, `on_hr_config` |
| `miniproto.py` `spp.py` `xcrypto.py` | Protobuf / V2 framing / AES-CTR + CRC crypto | protocol primitives |
| `tray.py` `notify.py` `watch_*` `morning_report.py` `demo_state.py` | Tray app, notify helpers, Stop-hook, morning report, `/demo` fixture | peripheral |

## HTTP endpoints (all in `dashboard.py` today)

| Path | Does | Feature |
|---|---|---|
| `/state` | full UI model (live) | core |
| `/state_demo` `/data` `/export` | demo fixture / raw / backup | core |
| `/sync` | force a data sync | core |
| `/sleep/start` `/sleep/stop` | session control (+ `finalize_night` on stop) | sleep |
| `/lucid/on` `/lucid/off` | arm/disarm lucid master | sleep |
| `/cue` | queue one gentle buzz | sleep |
| `/notify` | push a notification to the watch | watch-io |
| `/vibrate` `/vibrate/stop` | find-device siren on/off | watch-io |
| `/alarm` `/alarm/delete` | create/clear a hardware alarm | watch-io |
| `/health/hrcfg_get` `/health/advanced_on` | HR/REM config | sleep |
| `/support.js` `/vendor/*` | static assets | frontend |

## Frontend (`index.dc.html`, 1146 lines — Claude Design Canvas)

- `<style>` 14–46 — theme/CSS.
- markup 47–355 — cards, each fenced by an HTML comment: HERO HR, activity
  rings, stats band, insights, HR-by-hour, HR zones, steps, stress, SpO2,
  alarm+vibrate, sleep helper, trends (7д/30д/12м), sleep section.
- `text/x-dc` script 356–1144 — **one render object**: shared chart builders
  (`mkArea mkBars mkRings mkStack mkHypno mkZigPack mkSleepWave mkHrRanges
  mkDayDots`) + the monolithic `renderVals()` that computes every panel and sets
  the `*El` bindings. NOTE: builders are shared and `renderVals` is one function,
  so per-feature splitting is a refactor, not a cut — see `docs/MIGRATION.md`.

## Feature → where its code lives today

- **sleep/lucid**: `sleep_engine.py` (all) · `activity.py` sleep parsers ·
  `dashboard.py` `push_sleep`/`finalize_night`/`_merged_night`/session +
  `/sleep/*` `/lucid/*` `/cue` `/health/*` · sleep + trends cards in the HTML.
- **activity/HR/steps/stress/spo2**: `activity.py` daily/details ·
  `dashboard.py` `push_details` + `/state` · HERO/rings/stats/HR/steps cards.
- **watch-io (shared output)**: `client.py` command builders +
  `_send_device_command` · `dashboard.queue_command` · `/notify` `/vibrate*`
  `/alarm*`. Candidate to become `core/watch_io.py` (priority arbiter).
- **todos** (planned): none yet — first new vertical slice.
