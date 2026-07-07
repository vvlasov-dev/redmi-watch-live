# evals/ — quality gates for the non-deterministic parts

The "smart" surface of this project (the sleep/lucid engine's `decide()`) is a
heuristic over noisy sensor data, not an LLM — so its eval harness is a **golden
dataset of real nights → expected decisions**, run on every change. Same
discipline as LLM regression evals, honest to what the system actually is.

## What's already the eval gate

`../test_sleep_engine.py` **is** the golden-dataset regression suite:

- Cases are distilled from **real failed nights** (2026-07-03/04/06/07), not
  invented — split-night counter resets, frozen counters, stages-without-REM,
  window boundaries, quiet-night HR estimation, daytime-test guard.
- Each is a frozen input → asserted `decide()` output (action + reason class).
- `run_tests.py` runs it with the protocol + parser suites; `deploy.ps1` aborts
  on failure. Touch the engine → a case must cover the new branch.

**Adding a golden night:** capture the night's `.sleep_session.json` probes (or
`captures/*.bin`), reduce to the minimal probe sequence that reproduces the
behaviour, add a `decide()` assertion. Real capture > synthetic when available.

## LLM-as-judge — the template for the next LLM feature

When an actual LLM feature lands (e.g. the morning-report narrative, or watch
summaries generated in-repo), score it here instead of eyeballing:

```
evals/
  cases/              # inputs: {night_json, expected_points[]}
  judge_prompt.md     # rubric: is the summary accurate, concise, no fabrication?
  run_evals.py        # feed each case → model → judge → score; fail if < threshold
```

Rules that make it a real gate, not theatre:

- **Golden set, versioned.** Inputs + a rubric or reference, committed.
- **Judge scores against the rubric**, returns a number; CI fails if the mean
  regresses vs the last committed score.
- **Any prompt edit must run `evals/`** — same status as changing a unit test.
- Score data separately from marketing: "accurate" and "reads well" are two axes.

Until that feature exists, this folder documents the intent; the live gate is
`test_sleep_engine.py`.
