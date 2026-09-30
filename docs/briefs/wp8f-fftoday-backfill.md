# TNCasino WP8f: FFToday's 2025 weeks in the fit

Written 2026-09-29 by the orchestrator session, after WP8c (PR 16, merge f0af278). For the cloud
session, which designs the change, writes its own engineer briefs, runs its engineers, pushes the
branch `wp8f-fftoday-backfill` cut from `main` and opens the pull request. The orchestrator merges,
then runs the backfill and the refit on the owner's machine, where the 2025 databases live.

## 1. Goal

The model's parameters are fitted with FFToday among the sources, so that in a 2026 run FFToday
enters the blend with its own weight and bias instead of a weight of 1 and no bias. Research
`docs/design/sources-2026-research.md` §4.8 chose this option, (b), over adding FFToday now and
refitting after four 2026 weeks.

## 2. What is known

FFToday still serves its 2025 pages. Fetching 2025 weeks 10 to 16 from the owner's machine on
2026-09-29 (six requests a week, five seconds apart) parsed every week cleanly, 209 to 243 rows,
week stamps and top players all ok. Six of the seven weeks were then refused by the scrape step's
`value_agreement` check, at QB against Sleeper's rows for the same week:

| week | FFToday QB r | median difference | other positions |
|---|---|---|---|
| 10 | 0.81 | 2.98 | ok |
| 11 | 0.77 | 3.88 | ok |
| 12 | 0.88 | 3.68 | ok, the week stored |
| 13 | 0.84 | 4.20 | ok |
| 14 | 0.79 | 4.26 | TE r 0.84, also refused |
| 15 | 0.72 | 3.96 | ok |
| 16 | 0.84 | 3.86 | ok |

The check is right about 2026 and wrong about 2025: the same measure puts ESPN, FanDuel and
FirstDown's stored 2025 QB rows below 0.85 against Sleeper in most of those weeks too (ESPN 0.54
to 0.86, FanDuel 0.47 to 0.85, FirstDown 0.81 to 0.83). Sleeper's 2025 QB projections ran about
3.3 points above everyone else, which is why v2.2 carries a hand-set Sleeper QB bias
(`pipeline/model/params/v2.2.json`, `amended`, and research §9). The 2025 rows of the other
sources were stored by the notebooks, before the check existed.

The scrape step does not store a refused source's rows and `review` only records verdicts or
deletes, so today there is no way to keep those six weeks.

## 3. Your decisions

- How a past week's FFToday rows get stored when the same-week Sleeper check refuses them, without
  weakening the check for live weeks. Keep the 2026 path exactly as it is.
- What the refit fits on and how it is versioned: whether `fantasypros.com` stays excluded as in
  v2.2, and how the refit treats Sleeper's QB bias given that a plain refit on 2025 rows would
  put it back near 4.4 and the v2.2 amendment set it to 1.45 for 2026. Say what the orchestrator
  should run and what to expect from the gate.

## 4. What you can and cannot do

- No `.env`, no `DATABASE_URL`, no publish, no `migrate-legacy`. Production is not reachable.
- The 2025 databases are not in the repository and not in your sandbox. You build from the
  committed fixtures and the tests' scratch databases; the orchestrator runs the real backfill and
  `fit-model` afterwards. Say in the pull request the exact commands to run, in order.
- Live requests to `www.fftoday.com` are fine for investigation, through `get` in
  `pipeline/sources/base.py` with its User-Agent and FFToday's five-second spacing, and nothing
  else on the site. FFToday has no letter, so its numbers may appear in the pull request.
- Tests run on fixtures only. `python -m pytest -q`, `python -m ruff check .` and
  `python -m ruff format --check .` clean. 1227 tests pass and one is skipped on `main`.
- Update the docs that describe what you change: `docs/design/pipeline-v2.md` §7 for the scrape
  step, `docs/architecture/02-data-pipeline.md` or `03-modeling-and-odds.md` as applies, `CLAUDE.md` if a command
  changes. No new parameter version is committed by you: the orchestrator commits it after the fit.

## 5. Acceptance

- On the owner's machine, the commands in your pull request store FFToday's rows for 2025 weeks
  10 to 16 in the local `projections.db`, matched to Sleeper ids, and `fit-model` for 2025 weeks
  10 to 16 writes a parameter version with `fftoday.com` among its sources and reports its gate.
- A 2026 live week behaves as before: a source that fails `value_agreement` is still dropped.
- CI green on the branch. Pull request body: what changed, why, the commands, and the numbers
  from any live fetch.
