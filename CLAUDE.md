# Redmi Watch 5 Live — project instructions

Local Windows service that reads a Redmi Watch 5 Active over Bluetooth SPP
(ported Gadgetbridge Xiaomi protocol), serves a single-page dashboard at
http://127.0.0.1:8765, persists to SQLite, and runs a sleep/lucid engine.

**Read `docs/CONVENTIONS.md` first** — it is the source of truth for how we work
here (plan-before-code, layers, data honesty, verification, tests-as-guardrail).
@docs/CONVENTIONS.md

## Orientation (read before touching code)

- Layers and rules: `docs/CONVENTIONS.md` §2.
- Deep protocol/architecture notes live in the auto-memory
  (`redmi-watch-dashboard`) and this file's git history — check there before
  re-deriving a fix.

## Commands

- Deploy: `powershell -ExecutionPolicy Bypass -File deploy.ps1` (syncs src →
  `%LOCALAPPDATA%\RedmiWatchLive`, restarts service, gates on tests). Never
  clobbers the runtime `config.json`.
- HTML-only change: copy `index.dc.html` to the runtime dir — **no restart**.
- Tests: `python run_tests.py` (protocol + parsers + engine replays).
- Status/health: `status.ps1`.

## Hard rules (non-negotiable)

- **Never restart the service during a live sleep session** — deploy HTML-only or wait.
- **Byte-exact parsers** — cite the Gadgetbridge source, don't guess offsets.
- **Absence ≠ zero** — honour `has_rem`/flags; show "нет данных", not fake facts.
- **Quiet night** — no activity polling while the user sleeps (each fetch = a
  false 1-min awakening on the watch).
- **Minimise BT chatter** — suspected cause of the watch's weekly reboots.
- Verify against real data (capture/DB/`/state`/screenshot), not "looks right".

## Definition of Done

Run the checklist in `docs/CONVENTIONS.md` §7 before reporting "готово".
