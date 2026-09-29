# TNCasino B6: betting windows

Written 2026-09-28 by the orchestrator session.

A self-contained brief for a Claude Code cloud session working from a fresh clone of `sfaizi24/TNCasino`. Everything a local engineer would read from the orchestrator's scratch briefs is in this document; everything else is in the repository. This file is `docs/briefs/b6-betting-windows.md` on `main`; read it from your clone, and treat the copy as the brief even if a Claude Doc of the same title exists.

## 1. Where you are

You are an engineer on TNCasino, a small Flask app for a 12-team fantasy football league: Google login, a betting page where owners stake fake money on the week's markets, a leaderboard, and an admin page. Odds come from tables published by an offline pipeline (`pipeline/`, not yours). Production runs gunicorn and nginx on a DigitalOcean droplet against PostgreSQL; the test suite runs on in-memory SQLite. The orchestrator scoped this package and merges your pull request.

- Start from `main` at commit `e8dddad` or later. It carries 801 green tests. Branch off it as `b6-betting-windows` and never commit to `main`.
- Install with `pip install -r requirements.txt` (Python 3.13). The file pins `ruff==0.15.7`, `pandas<3` and `sqlalchemy<2.1`; CI installs the same file, so do not change it in this branch.
- Run `python -m pytest -q -p no:cacheprovider` before every commit (about 40 s). Every test already on `main` stays green.
- Run `ruff format` and `ruff check` on every file you touch. Lint config is `ruff.toml` (line length 120, rules E F W I UP B). CI runs `ruff check .` and `ruff format --check .` with the same version.
- There is no `.env` and tests never need one. `tests/conftest.py` pins `DATABASE_URL` to `sqlite://` before the app is imported, because importing `app` runs `load_dotenv()`. Never set `DATABASE_URL` to anything but a scratch SQLite file. Never run `python -m scripts.publish` or `python -m pipeline`. Production is not reachable from your session and must not be.
- A clone has no databases under `backend/data/` (gitignored) and none are needed.
- Scratch scripts and rendered pages go outside the repository (for example `/tmp/b6/`), never in a commit.
- Commit in a few logical commits with imperative, why-first messages, each ending with a blank line and `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Do not push to `main`, merge, rebase or touch other branches. Push your branch and open a pull request against `main` (section 9). The orchestrator merges.

Rules that bind every engineer on this repository:

- Create or edit only the files in the ownership list (section 5). A change needed elsewhere goes in your report, precisely, not in the code. Never delete files.
- Keep every JSON response shape the browser relies on; add fields, never rename them.
- Money is a float in the schema and the API; keep it so.
- Quality bar (`CLAUDE.md`): this is a portfolio project, and the code must read as if a careful engineer wrote it by hand. Small functions, clear names, straightforward control flow, no clever one-liners, no over-commenting, no docstrings on obvious functions, no defensive handling of impossible cases, no single-use helper abstractions, no `utils` modules. A function you rewrite ends shorter and plainer than you found it.
- Update the architecture docs the package names in the same branch, in the voice of the existing pages (present tense, tables where the page already uses tables).

## 2. How the app is wired

Read these first, in this order, before writing anything:

1. `CLAUDE.md`, the quality bar and the map of `app/`.
2. `docs/architecture/05-web-app.md`, `06-betting-lifecycle.md`, `04-data-model.md` (the app tables and the published tables) and the "Open issues" and "Web app" parts of `08-constraints-and-debt.md`.
3. `docs/design/odds-models-2026.md` sections 3.6, 3.7 and 3.8 (the design this package implements), section 9 (the package list; you are B6) and the decisions list at the end of section 10 (the owner's decisions, taken 2026-09-28, which are final). Where the design and the code on `main` disagree, the code is the source of truth; say so in your report.
4. The app code, all of it: `app/__init__.py`, `app/database.py`, `app/models.py`, `app/migrations.py`, `app/auth.py`, `app/routes/helpers.py`, `app/routes/betting.py`, `app/routes/admin.py`, `app/routes/odds.py`, `app/routes/account.py`, `app/routes/pages.py`. Then `frontend/templates/betting.html`, `admin.html`, `frontend/static/js/betting.js`, `admin.js` and `frontend/static/css/betting.css`.
5. The tests: `tests/conftest.py` (fixtures `app`, `client`, `user`, `admin_user`, `logged_in_client`, `admin_client`, `betting_period`, `analytics_tables`, `seeded_analytics`, `pipeline_tables`), `tests/test_betting.py`, `tests/test_settlement.py`, `tests/test_admin.py`, `tests/test_helpers.py`, `tests/test_odds.py`. They show how a request is made as a logged-in user and how the published tables are faked.

Facts you would otherwise have to discover:

- `create_app(config)` reads `SECRET_KEY` and `DATABASE_URL` from the environment, applies the `config` dict on top, then calls `db.create_all()`. Outside `TESTING` it also runs `app/migrations.py`, a list of idempotent, dialect-aware `ALTER TABLE` statements. Tests skip migrations, so a new column must be in both `app/models.py` and `app/migrations.py`. This package adds no column and no table.
- The current week is `get_current_week()` in `app/routes/helpers.py`: the highest `BettingPeriod` with `is_settled` false, fallback 10 with a printed warning. `check_betting_period_lock(week)` returns the lock time when the admin's lock has closed the week (and lazily flips `is_locked`), else `None`.
- Published tables (`betting_odds_*`, `sleeper_*`, `team_lineups`, the curves, and now `simulation_runs`) are read with raw SQL through `query_analytics(sql, params)` with `:named` parameters. App tables (`users`, `bets`, `bet_legs`, `weekly_stats`, `betting_periods`) go through the SQLAlchemy ORM. The pipeline's publish step replaces the published tables whole; the app never writes to them.
- Every SQL statement must run on PostgreSQL and SQLite. `NULLS LAST` does; sorting ISO 8601 UTC text does.
- JSON endpoints return `{"success": true, ...}` or `{"success": false, "error": "..."}` with status 200, and the browser code branches on `success`. CSRF is off for JSON routes and disabled in tests.
- Owner names: the published tables carry Sleeper display names; `display_name_for` and `friendly_description` in `helpers.py` map them to first names. Team identity in the published tables is the Sleeper `roster_id`, never the owner name.
- Logging is `print()` in the existing routes. New code uses `logging`; do not convert code you are not otherwise rewriting.

## 3. What B1, B2, B4 and B7 left on main

Four packages merged on 2026-09-28 and set the ground this one builds on.

- **One notion of "betting is open".** `app/routes/betting.py` asks `check_betting_period_lock(week)` from `app/routes/helpers.py`. It reads the week's `BettingPeriod`, returns `None` when there is no period (betting open) and the `lock_time` when the period `is_locked` or its `lock_time` has passed, flipping `is_locked` on the way (the lazy lock). `place_bet` refuses with `_refuse_locked(lock_time)` ("Bets are locked as of 2026-10-02 12:15 AM UTC"), `remove_bet` the same, and `_bet_summary` says a bet is `removable` when the lock returns `None` and `_run_is_latest(bet)`, meaning its market still quotes the run that priced it. Nothing else in `app/` consults the lock, and nothing in `app/` reads `simulation_runs`.
- **The page.** `/betting` renders `betting.html` with `bets_open_until=_format_lock_time(period.lock_time)`, Eastern text under "Bets Open Until" in the prize strip (`betting.html` lines 30 to 45, `.tnc-prize-col-aside` and `.tnc-prize-when` in `betting.css`), or "Thursday Night Kickoff" when there is no period. `betting.js` never asks the server whether betting is open: `init` binds events, loads every tab's rows, loads the user's bets when signed in and renders; `handlePlace` toasts `result.error` and reloads the tab when the error is "Odds have changed"; `handleCancel` calls `DELETE /api/remove_bet/<id>`.
- **The admin card.** "Set Betting Period" (`admin.html` lines 36 to 50) takes a week and a "Lock Date & Time (UTC)" and notes that all times are UTC. `updateActiveWeekBanner` in `admin.js` shows the active period as "Open for Betting" with a countdown to its `lock_time`, or a locked badge. Until now the lock has been set to Thursday's kickoff. This package changes that: the lock becomes the hard close and the kill switch, and the window between pipeline runs decides day to day.
- **The period.** `BettingPeriod` in `app/models.py` has `week` (unique), `lock_time`, `is_locked`, `is_settled` and timestamps. The admin's settlement flow in `admin.py` sets `is_settled`.
- **The published run.** The pipeline publishes `simulation_runs`, the latest run per week, with the columns below (`SIMULATION_RUNS_DDL` in `pipeline/steps/simulate.py`; copy them, never import the step).

| Column | Meaning |
| --- | --- |
| `run_id` TEXT | `2026w04-20260930T230000`, the run every odds row of the week carries |
| `season`, `week` INTEGER | the week the run priced |
| `seed`, `n_sims` INTEGER, `model_version` TEXT, `n_teams` INTEGER, `draws_path` TEXT | not used by the app |
| `created_at` TEXT | ISO 8601 UTC with offset, `2026-09-30T23:00:00+00:00` |
| `n_locked` INTEGER | how many starters were pinned at real points; 0 until the pipeline's B5 package ships |
| `window_closes_at` TEXT | the first NFL kickoff of the week after `created_at`, same format; NULL when no game of the week follows the run, and NULL on every run made before B4 |
| `standings_through_week` INTEGER | not used by the app |

Wednesday's run closes at Thursday's kickoff (week 4: `2026-10-02T00:15:00+00:00`). The Friday rerun after Thursday's game closes at Sunday's first kickoff (`2026-10-04T13:30:00+00:00`), and a Saturday rerun the same. Production gets its first row on Wednesday 2026-09-30, and this package deploys only after that publish, so do not code around a missing table.

`tests/conftest.py` creates the published tables by hand (`ANALYTICS_TABLES`, `create_analytics_tables`) and seeds week 10 of 2026 under `RUN_ID`. The `betting_period` fixture makes week 10's period with a `lock_time` seven days ahead.

## 4. Goal

Betting is open when the odds on the page come from a run made before the next kickoff, and nothing else.

1. **Three states per week.** `open`: the week's latest published run has a window and the window has not closed. `paused`: the run's window has closed and no newer run has been published (Thursday night until the Friday rerun publishes; Sunday morning until the week is over). `closed`: no betting period for the week, or the period is settled, or the admin's lock (`is_locked`, or `lock_time` passed), or the run has no window.
2. **The app enforces it.** Placing a bet and removing a bet need `open`; `removable` in the bet list means `open` and the bet's run is still the latest. The window never flips `is_locked`: a window closing is not a lock, because the next run reopens it. `lock_time` stays the admin's backstop and kill switch, through `check_betting_period_lock` as it is.
3. **The page shows it.** One banner in place of "Bets Open Until", filled from a new endpoint, in the visitor's own time zone, and refreshed after every refused action.
4. **The admin knows the rule.** The lock is the hard close, set at or after the week's last window close, never Thursday's kickoff.

The driving risk is a bet placed on Thursday night against Wednesday's odds, after the game has started. The second is a week that never reopens on Friday because a Thursday `lock_time` locked it.

## 5. Ownership

| Files | You may |
| --- | --- |
| `app/windows.py`, `tests/test_windows.py` | create |
| `app/routes/betting.py` | edit |
| `frontend/templates/betting.html`, `frontend/static/js/betting.js`, `frontend/static/css/betting.css` | edit |
| `frontend/templates/admin.html`, `frontend/static/js/admin.js` | edit |
| `tests/conftest.py`, `tests/test_betting.py` | edit |
| `docs/architecture/05-web-app.md`, `docs/architecture/06-betting-lifecycle.md` | edit |

Not yours: `app/routes/helpers.py` (call `check_betting_period_lock` as it is), `app/ledger.py`, `app/markets.py`, `app/settlement.py`, `app/models.py`, `app/migrations.py`, `app/routes/admin.py`, `app/routes/odds.py`, `pipeline/`, `tests/pipeline/`, `scripts/`, `backend/`, `docs/design/`, `docs/architecture/08-constraints-and-debt.md`, `CLAUDE.md` and `requirements.txt`. List the doc 08 rows and `CLAUDE.md` lines that should change in your report. Another engineer is working in `pipeline/`, `tests/pipeline/` and docs 02, 03 and 04 at the same time on a local branch; you will not collide as long as you stay in the files above.

## 6. The work, in four parts

### Part 1: the window (`app/windows.py`)

```python
@dataclass(frozen=True)
class Window:
    week: int
    state: str                       # "open" | "paused" | "closed"
    closes_at: datetime | None       # the run's window_closes_at, UTC
    run_id: str | None
    run_created_at: datetime | None
    lock_time: datetime | None       # set only when the admin's lock closed the week

def betting_window(week: int, now: datetime | None = None) -> Window
```

In order:

1. No `BettingPeriod` for the week, or `is_settled`: closed, no lock time.
2. `check_betting_period_lock(week)` returns a lock time: closed with that lock time. This is the one place the lazy lock still runs, and it is meant to.
3. The week's latest published run: `SELECT run_id, created_at, window_closes_at FROM simulation_runs WHERE week = :week ORDER BY season DESC, created_at DESC, run_id DESC LIMIT 1`. No row, or a NULL `window_closes_at`: closed.
4. `now < closes_at`: open; otherwise paused.

Parse the two timestamps with `datetime.fromisoformat`, and treat a naive value as UTC. `now` defaults to `datetime.now(UTC)`; tests pass it. The function reads and never writes, apart from what `check_betting_period_lock` does in step 2.

### Part 2: the routes enforce it (`app/routes/betting.py`)

- `place_bet` refuses before anything else when the window is not open. Closed with a lock time keeps today's text (`_refuse_locked`). Closed otherwise: `Betting is closed for week {week}`. Paused: `Betting is paused until the odds update`. The rest of the refusal order stays (amount, balance, market, "Not this week's market", quote, "Odds have changed", "Not offered"); rule 3 of design section 3.6 stays enforced by `_quote_moved` and `find_quote`.
- `remove_bet` refuses with the same three texts. When open it works as today, including the "Odds have changed since this bet was placed" refusal and the full refund. Cash-out is package B9, not yours.
- `_bet_summary`'s `removable` becomes `window.state == "open" and _run_is_latest(bet)`. A bet list spans weeks, so look each distinct week up once in `get_my_bets` rather than once per bet.
- New endpoint `GET /api/betting_window`, no login, `?week=` optional, default `get_current_week()`:

```json
{"success": true, "week": 4, "state": "open",
 "closes_at": "2026-10-02T00:15:00+00:00", "run_created_at": "2026-09-30T23:00:00+00:00"}
```

Both timestamps are ISO 8601 UTC, `null` when there is no run. Do not send the lock time.

- `/betting` stops passing `bets_open_until`. Delete `_format_lock_time` and whatever it alone imported. The template still gets `current_week`.

### Part 3: the page shows it (`betting.html`, `betting.js`, `betting.css`)

Replace the "Bets Open Until" column with a banner element the script fills; keep the prize strip layout, with the aside column holding the banner. In `betting.js`:

- `loadWindow()` fetches `/api/betting_window` into `state.window`; `init` loads it alongside the tab rows (`Promise.all`).
- `renderWindow()` writes one line, times in the visitor's zone (`toLocaleString` with weekday, hour and minute). Open: `Odds updated Wed 7:00 PM. Betting closes Thu 8:15 PM.` Paused: `Betting paused since Thu 8:15 PM, until the odds update.` Closed: `Betting is closed for week 4.` Colour the three states differently and keep the text readable at phone width.
- When the state is not open, the place buttons and "Cancel bet" are disabled with the banner's text as their tooltip. The server refuses regardless; this is courtesy, not enforcement.
- After a refused place or cancel, reload the window and the bets, then re-render, so a visitor who kept the page open across a kickoff sees why.

### Part 4: the admin knows the rule (`admin.html`, `admin.js`)

- Under "Lock Date & Time (UTC)", replace the UTC note with the rule: the lock is the hard close and the kill switch; set it to the week's last window close, Sunday's first kickoff (for week 4, `2026-10-04T13:30` UTC), never Thursday's; between runs the betting window closes and reopens on its own. Keep the datetime input as it is.
- `updateActiveWeekBanner` adds one line from `/api/betting_window` for the active week, in the admin's zone, next to the existing countdown to the lock: `Window: open until Thu 8:15 PM`, `Window: paused since Thu 8:15 PM` or `Window: closed`.

## 7. Tests and the browser check

- `tests/conftest.py`: `simulation_runs` joins `ANALYTICS_TABLES` with the twelve columns from section 3, and `seed_analytics` inserts one row for `RUN_ID` (week 10, season 2026, `created_at` `2026-11-10T14:00:00+00:00`, `window_closes_at` `2026-11-13T00:15:00+00:00`, `n_locked` 0). Existing tests that place or remove bets run with the `betting_period` fixture and a `now` before the close. Give `betting_window` its `now` from a module-level clock the tests can monkeypatch, or pass a fixed `now` from the tests, whichever keeps the routes plain.
- `tests/test_windows.py`: open, paused, and closed for each of no period, settled period, `is_locked`, `lock_time` passed (with the lock time reported and `is_locked` flipped, as today), no run, NULL window; a naive timestamp read as UTC; the latest of two runs wins; a paused or open window never flips `is_locked`; the endpoint's JSON for the three states, with and without `?week=`, signed out.
- `tests/test_betting.py`: rewrite `test_place_bet_locked_period` and `test_remove_bet_locked_period` around the kill switch (the texts stay); add the paused and closed refusals for place and remove; extend `test_my_bets_lists_the_pick_and_whether_it_can_be_removed` so `removable` is false while paused and true again once a newer run's window is open (update the seeded run's row).
- `tests/test_helpers.py`'s lock tests stay as they are.
- Any test that placed a bet without a betting period or a run needs the fixture now, since a week without a period is closed. Say in the report which ones.

The browser check. Google login cannot be completed from your session, so log a browser in by setting the session cookie yourself: run the app on a scratch SQLite file with the published tables faked (reuse the DDL and seed rows from `tests/conftest.py` in a scratch script), and set the `session` cookie to `SecureCookieSessionInterface().get_signing_serializer(app).dumps({"_user_id": "<user id>"})`. Try `playwright install chromium`; the package is in `requirements.txt`. Take three screenshots of the betting page (open, paused, closed; edit the seeded run's `window_closes_at` and the period between shots) and one of the admin card with the new rule, and check the banner once at phone width. Attach them to the pull request. If Chromium cannot be installed in your session, render the templates through the Flask test client as a logged-in user instead, say so in the report, and the orchestrator will check the JavaScript locally after the merge.

## 8. Docs to update

- `docs/architecture/05-web-app.md`: the Bet endpoints table (the new endpoint; place and remove need an open window), the refusal paragraph, the `removable` paragraph and the Page to API map.
- `docs/architecture/06-betting-lifecycle.md`: the betting period section and its state diagram (open, paused, closed, and the lock as the hard close), the lazy-lock bullets, the Bet diagram's "removed" transition ("while the window is open and its run is the latest"), and a runbook line for the admin: set the lock at the week's last window close.
- Not `docs/design/odds-models-2026.md`, `docs/architecture/08-constraints-and-debt.md` or `CLAUDE.md`; the orchestrator records the package there from your report.

## 9. How to finish: branch, commits, pull request, report

- Work on `b6-betting-windows`. Make a few logical commits (the window module and its tests; the routes and their tests; the page; the admin card; the docs), each ending with `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`. Before each commit run `python -m pytest -q -p no:cacheprovider`, `ruff check .` and `ruff format --check .`.
- Push the branch and open a pull request against `main`. CI runs ruff and pytest on Python 3.13 and must be green. Do not merge, and do not enable auto-merge; the orchestrator merges and deploys after the week-4 publish.
- The pull request description is your report, in this order:
  1. The branch and `git log --oneline main..HEAD`.
  2. What you built, per file.
  3. What you verified by hand: the three refusal texts and the endpoint's JSON for the three states from a real request, and the screenshot paths or the reason there are none.
  4. Tests added and the new pytest total.
  5. The `ruff check` and `ruff format --check` results.
  6. Deviations from this brief, and any place the design and the code disagreed and which side you followed.
  7. Changes needed from someone else: the rows for `docs/architecture/08-constraints-and-debt.md` and the `CLAUDE.md` lines (the routes count in `betting.py`, the new module), the tests that needed the fixture, and anything `helpers.py` or `admin.py` should change.
  8. Open questions and anything you could not verify.
- End the description with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.

## 10. A second, separate task if time allows: pin every requirement exactly

Start this only after the B6 branch is pushed, on a new branch `pin-requirements` cut from `main`, not from the B6 branch.

Why: CI was red from 2026-09-26 to 2026-09-28 without anyone noticing, because `requirements.txt` was unpinned and a fresh install resolved ruff 0.16.9, pandas 3 and SQLAlchemy 2.1, which behave differently from the local versions. Commit a4dcea2 pinned `ruff==0.15.7`, `pandas<3` and `sqlalchemy<2.1`; every other line is still loose.

What to do:

- Pin each direct requirement listed in `requirements.txt` to the exact version a fresh `pip install -r requirements.txt` resolves on Python 3.13 (`pip freeze` after the install, filtered to the listed names). Keep the file's order, its comments, the `ruff==0.15.7` line with its comment ("CI must format exactly like the local hook"), and its CRLF line endings. Do not add transitive packages and do not add a lockfile.
- Verify with a fresh virtual environment: install from the pinned file, then run the full test suite, `ruff check .` and `ruff format --check .`.
- Open a pull request against `main` whose description lists each package and its pinned version in a table, then the same report items as section 9. Same rules: CI green, no merge, no auto-merge. The orchestrator confirms the same versions install on Windows before merging.
