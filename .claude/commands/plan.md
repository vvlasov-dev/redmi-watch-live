---
description: Produce a change plan before writing code (decomposition discipline)
---

Before writing any code for the task below, produce a plan and wait for
confirmation. Do not edit files yet.

Output exactly:
1. **Goal** — one sentence.
2. **Affected files** — each with its layer (`docs/CONVENTIONS.md` §2).
3. **Interface changes** — signatures, `/state` shape, protocol frames touched.
4. **Blast radius** — does this touch the BT protocol, the night engine, or the
   live service? Name what could break.
5. **Test plan** — the exact regression/unit tests that will prove it, and where
   they go (`test_activity.py` / `test_sleep_engine.py` / `selftest.py`).
6. **Verification** — which real artifact you'll check (capture / DB / `/state`
   / screenshot).

Then stop and ask to proceed.

$ARGUMENTS
