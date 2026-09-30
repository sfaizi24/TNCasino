# TNCasino WP9: the cutover from notebooks to the pipeline

Written 2026-09-30 by the orchestrator session, after WP8f (PR 19, merge 3c23465) and model v2.3
(c1bb3c9). For the cloud session, which designs the change, writes its own engineer briefs, runs its
engineers, pushes the branch `wp9-cutover` cut from `main` and opens the pull request. The
orchestrator merges after the first full week-4 run on the owner's machine has been judged good.

## 1. Goal

The pipeline package is the only data pipeline. An agent runs the week from a runbook, from the
first scrape on Wednesday to the reruns and the publish, and reads the result on the admin
dashboard. The notebooks, the legacy scrapers and the legacy scripts are gone, and every doc, the
README and CLAUDE.md describe the system as it now is.

## 2. What is known

- `docs/design/pipeline-v2.md` §12 names the package: runbook skill rewrite, docs update, deletion
  of `backend/notebooks/`, `backend/scrapers/`, `scripts/scrape.py`, `scripts/validate_scraping.py`,
  `scripts/publish.py`, `tests/test_scrape.py` and `tests/test_scrapers.py`, CLAUDE.md commands,
  CI, requirements cleanup. Its line about deleting legacy code "after the new pipeline has produced
  a full week-4 run" is satisfied on the orchestrator's side: the first full 2026 week-4 run went
  through on 2026-09-30 under v2.3, without publish, with five sources (FirstDown, FFToday and
  RotoBaller had not posted the week yet) and status `warn` from the accuracy and calibrate steps,
  which had no week-3 projections to grade. The playoffs step took 66 of the run's 80 minutes:
  FantasySharks spaces its requests 60 seconds apart and the step re-projects ten future weeks.
- `docs/design/odds-models-2026.md` §3.3 corrects the weekly schedule the first runbook plan had:
  Friday's rerun is `--steps league,lineups,simulate,odds,playoffs,validate`, Saturday's is the
  full default run, and the playoffs step may be left out of a rerun because publish keeps the
  latest run per table. B5, which pins kicked-off starters for the Friday rerun, is not merged and
  has no start date; the runbook describes the code on `main`.
- The three skills in `.claude/skills/` (`run-pipeline`, `scrape`, `verify`) are written for the
  notebooks and `scripts/scrape.py`. `review` (`python -m pipeline review`) is how an agent records
  a verdict on a source's week.
- The admin dashboard (`/admin/pipeline`, `docs/design/pipeline-v2.md` §10) was built in WP8b
  before most steps existed. The owner reports it does not render properly on production. The
  steps' summaries have grown since (lists of dicts, nested dicts, chart names, warnings); which
  shapes break the page is for you to find out.
- Production's virtualenv is a hand-installed subset without numpy
  (`docs/architecture/08-constraints-and-debt.md`). The deploy that follows this package installs
  `requirements.txt` there; `jupyter` is in it only for the notebooks.
- `python -m pipeline run ... --steps publish` uploads the charts itself, so CLAUDE.md's `scp` line
  for the analytics images describes the legacy path.

## 3. Your decisions

- What the weekly runbook says, step by step, for Wednesday, Friday and Saturday with the code on
  `main`: the commands, what the agent checks after each step, what a verdict is and when to give
  one, when to publish, what to do when a source fails, and what it reads on the dashboard. Whether
  `scrape` and `verify` stay as skills, merge into the runbook or go.
- What replaces the dashboard's rendering where it breaks, and how the page shows a run's
  summaries so an agent or the owner can judge a week from it.
- What each doc under `docs/architecture/` and the README say once the notebooks are gone; what
  CLAUDE.md's Commands, Data Pipeline and Database sections become; which requirements go with
  the notebooks.

## 4. What you can and cannot do

- No `.env`, no production, no publish, no `migrate-legacy`. `DATABASE_URL`, if you set it to run
  the app for the dashboard work, points at a scratch SQLite file and nothing else. Tests never
  need it (`tests/conftest.py` pins it).
- The 2025 and 2026 databases are not in your sandbox. Manual pipeline runs use a scratch data
  directory through `PIPELINE_DATA_DIR`, with `PIPELINE_SEASON=2026` and
  `PIPELINE_LEAGUE_ID=1387602586542018560`, and `--steps league` fills it from Sleeper. Never
  commit a `.db` file.
- Deleting the legacy code is the package. Nothing that survives may import from
  `backend/scrapers/` or `scripts/`; find out what does before deleting. `backend/data/` stays,
  and its `databases/`, `sims/` and `images/` are gitignored.
- Docs stay truthful to the code on `main`, including B5's absence. Aggregates only from
  RotoBaller and Fleaflicker, as before. The repository is public.
- `python -m pytest -q`, `python -m ruff check .` and `python -m ruff format --check .` clean.
  1259 tests pass and one is skipped on `main` at c1bb3c9. The tests that go with the legacy code
  go with it; say the count after.

## 5. Acceptance

- `backend/notebooks/`, `backend/scrapers/`, `scripts/scrape.py`, `scripts/validate_scraping.py`,
  `scripts/publish.py`, `tests/test_scrape.py` and `tests/test_scrapers.py` are gone, nothing
  imports them, and `requirements.txt` carries nothing that only they used.
- An agent with `.claude/skills/run-pipeline/SKILL.md` and the docs can run 2026 week N from the
  first command to the publish without reading this brief or the design docs.
- `/admin/pipeline` renders every summary shape the steps write today, with a test per shape.
- CLAUDE.md, README and `docs/architecture/*` describe no notebook, no legacy scraper and no
  `scripts/` command; the deploy and publish lines in CLAUDE.md are the pipeline's.
- CI green on the branch. Pull request body: what was deleted, what the runbook is, what broke on
  the dashboard and how it renders now.
