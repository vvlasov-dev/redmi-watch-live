# Conventions — Redmi Watch 5 Live

Single source of truth for how we build in this repo. `CLAUDE.md` and
`.cursor/rules/` are thin adapters that point here. Edit this file; all tools
pick it up.

Working style is **agent-oriented**, not autocomplete: you plan, you get
reviewed by a second optic, tests are the guardrail. Instructions set intent;
`run_tests.py` + `deploy.ps1` enforce it.

---

## 1. Decomposition — plan before code

Before writing non-trivial code, output a short plan and wait for confirmation:

- **Affected files** and which layer each belongs to (see §2).
- **Interface changes** (function signatures, `/state` shape, protocol frames).
- **Test list** — what will prove it works, and what could break.
- **Blast radius** — could this touch the BT protocol, the night engine, or the
  live service? Those are the dangerous zones.

Skip the plan only for one-liners and pure text/UI copy.

## 2. Architecture & layers

Keep changes inside the right layer; don't smear protocol logic into the UI.

| File | Responsibility | Rule |
|---|---|---|
| `client.py` | BT SPP protocol state machine, frame build/parse, run loop | Minimise chatter to the watch (see §5). One thing per command builder. |
| `activity.py` | Activity-file parsers (daily/details/sleep/stages) | **Byte-exact ports** of Gadgetbridge — cite the source, never guess offsets. |
| `dashboard.py` | In-memory state, HTTP server, the `/state` model | The single place that shapes what the UI sees. Merge, don't overwrite. |
| `sleep_engine.py` | Pure `decide()` core + 30s tick loop (lucid cues, smart wake, auto-night) | `decide()` stays PURE and unit-tested. Side effects live in `_tick`. |
| `store.py` | SQLite persistence (days/sleep forever, minutes 90d, samples 14d) | Aggregates are permanent; only raw high-freq data is pruned. |
| `index.dc.html` | Claude Design Canvas frontend (`{{ }}` bindings, `renderVals()`) | No logic in markup. See §6 for canvas traps. |
| `service.py` | Supervisor: hardened startup, reconnect loop, wiring | Never let the process hard-die; catch all in the reconnect loop. |

## 3. Data honesty (the core principle of this project)

The watch lies and drops data; our job is to not repeat its lies.

- **Absence is not a fact.** Missing REM ≠ `rem = 0`. Use `None` + "нет данных"
  when a flag (`has_rem`) says the watch didn't measure it.
- **The minute-HR stream is the arbiter.** Watch files are *hints*; cross-check
  totals against per-minute pulse before trusting them.
- **Show both numbers when they disagree**, with a note — don't pick one silently.
- Never fabricate demo-like values into the real `/state` path (`/demo` is separate).

## 4. Verification — prove it against data, not eyes

Every "recent bug caught" in this repo was found by checking an **invariant**,
not by reading code. Do the same:

- Parser change → verify against a **real capture** (`captures/*.bin`) and assert
  an invariant (e.g. sum of stage minutes == duration).
- Engine change → **replay a real night** through `decide()` before shipping.
- DB/state change → query `history.db` / hit `/state` and check the numbers.
- UI change → screenshot in the browser **and** read console for errors.
- Decisions that fire at night (cues/alarm) must **log every branch** so the
  morning review can explain "why it did/didn't fire".

## 5. The watch is fragile — respect the hardware

- **One BT channel.** While the PC holds the watch, the phone gets nothing.
- **Minimise command chatter** — the firmware is suspected of weekly reboots
  under a 24/7 flood. Re-arm the realtime stream only when it actually went
  quiet; don't poll things that return empty acks.
- **Quiet night:** while the user is asleep, do **not** poll activity files —
  every fetch is logged by the watch as a 1-minute awakening and breaks its
  stage detection. Run on the persisted HR stream; sync once after wake.
- **Never restart the service during a live sleep session.** Deploy HTML-only,
  or wait. A restart mid-night loses the running estimate window.

## 6. Frontend (Claude Design Canvas) rules

- Bindings are `{{ }}` resolved in `renderVals()`. Compute in JS, bind values.
- **No controlled-input trap:** don't set `value=` on `<input>` without an
  `onChange`; set defaults via `id` + a deferred setter, read on click.
- Deploy is **HTML-only copy** when no Python changed (no service restart).
- Server sends `Cache-Control: no-store` so a plain reload shows the deploy.

## 7. Definition of Done (run this checklist before saying "готово")

- [ ] Verified against **real data** (capture / DB / `/state` / screenshot), not "looks right".
- [ ] `python run_tests.py` passes (or the change is docs/text only).
- [ ] Engine/parser change has a **regression test** capturing the case.
- [ ] Deployed via `deploy.ps1` (gates on tests) or HTML-only copy — stated which.
- [ ] Report is **honest**: what's verified vs assumed; if tests were skipped, say so.
- [ ] No new dependency without a one-line justification.

## 8. Tests are the guardrail, not an afterthought

- A behaviour change starts from a **failing/regression test**, not from code.
- `run_tests.py` = `selftest.py` (crypto/framing/protobuf) +
  `test_activity.py` (parsers vs real captures) + `test_sleep_engine.py`
  (`decide()` replays of real nights).
- `deploy.ps1` **aborts** if tests fail (`-SkipTests` only for emergencies).
- `captures/*.bin` are real-hardware fixtures — add one when you meet a new
  file shape, and pin an assertion to it.

## 9. Review by a second optic

Don't let the author review the author. Use `/review` (adversarial reviewer
subagent) before merging anything non-trivial — its job is to break the change
against this project's known failure modes (data-honesty, byte-exact parsing,
engine edge cases, night-safety), not to praise it.
