# TNCasino B10b: last place, the champion, and futures that settle from the standings

Written 2026-09-29 by the orchestrator session, after B10a (spreads, PR 12).

A self-contained brief for engineers working from a clone of `sfaizi24/TNCasino`. Three parts with fixed interfaces, built at the same time by different engineers: P (the pipeline), A (the app's backend) and B (the page). Each part says which files it owns. This file is `docs/briefs/b10-futures.md` on the branch `b10-futures`; it is the brief even where a design page says otherwise.

## 1. Where you are

TNCasino is a small Flask app for a 12-team fantasy football league (Google login, a betting page, an account page, a leaderboard, an admin page that settles bets) fed by an offline pipeline (`pipeline/`) that scrapes projections, simulates the week 50,000 times, prices the markets and publishes tables to production PostgreSQL. The app prices singles, parlays and cash-outs from those tables and from each run's stored score matrix. The test suite runs the app on in-memory SQLite and the pipeline on scratch SQLite files.

- Work on the branch `b10-futures`, cut from `main` after the B10a merge. The orchestrator commits, pushes and opens the pull request; **do not commit, push, stash, checkout or touch git state**. Leave your changes in the working tree and report what you changed.
- The virtual environment `/home/user/venv-pip-pinned` has every requirement on Python 3.13. Tests: `/home/user/venv-pip-pinned/bin/python -m pytest -q -p no:cacheprovider` (about 40 s; 1102 pass and 1 is skipped on `main`). Lint: `/home/user/venv-pip-pinned/bin/ruff check .` and `/home/user/venv-pip-pinned/bin/ruff format <files you touched>`. Every test on `main` stays green.
- There is no `.env` and tests never need one. Never set `DATABASE_URL`. Never run `python -m scripts.publish` or `python -m pipeline`. Production is not reachable and must not be. The Sleeper API is reachable but tests never call it; the pipeline tests fake it, as `tests/pipeline/test_playoffs.py` shows.
- Scratch scripts go under `/tmp/claude-0/…/scratchpad/`, never in the repository.
- Two other engineers are editing other files of this branch at the same time. Stay inside your part's ownership list. A change you need elsewhere goes in your report, precisely, not in the code.

Rules that bind every engineer on this repository:

- Keep every JSON response shape the browser relies on; add fields, never rename or remove them. Keep every published table's existing columns.
- Money is a float in the schema and the API.
- Quality bar (`CLAUDE.md`): this is a portfolio project, and the code must read as if a careful engineer wrote it by hand. Small functions, clear names, straightforward control flow, no over-commenting, no docstrings on obvious functions, no defensive handling of impossible cases, no single-use helper abstractions, no `utils` modules. A function you rewrite ends shorter and plainer than you found it.
- Every SQL statement runs on PostgreSQL and SQLite. Logging is `logging` in the app; the pipeline logs through `ctx.log`.

## 2. How the system is wired

Read these first, in this order:

1. `CLAUDE.md`.
2. `docs/architecture/02-data-pipeline.md` and `03-modeling-and-odds.md` (the playoffs step), `04-data-model.md` (the futures tables, `standings_probability_matrix`, the publishing map), `06-betting-lifecycle.md` (Markets, Settlement, Accounting), `05-web-app.md` (odds endpoints, Bet endpoints, admin endpoints).
3. `docs/design/odds-models-2026.md` §1.4 (why futures are not parlay legs), §4 (ranks 5 to 7), §5, and decisions 2, 7, 8 and 11 to 14 at the end of §10 (below).
4. The code your part touches (section 5).

Facts you would otherwise have to discover:

- **The playoffs step** (`pipeline/steps/playoffs.py`) runs after `simulate` and `odds`. It takes the first 20,000 sims of the week's simulation, projects, picks and simulates each later regular-season week (`simulate_future_weeks`, weeks `week+1 .. playoff_week_start-1`), ranks every simulated season on top of the record to date (`standings.finishing_positions`: most wins, then ties, then points), and writes `betting_odds_first_place` (finishing first), `betting_odds_make_playoffs` (finishing within `playoff_teams`) and `standings_probability_matrix` (every team at every position). `market_rows` drops chances under 1% or over 99%. `save_tables` stamps rows with `ctx.run_id`, the pipeline run's id, and `simulated_current_week` logs the simulation's own `run_id`. The step returns early once `settings.week >= playoff_week_start`.
- **League settings** come from Sleeper through `load_league_settings` (`pipeline/steps/league.py`): `playoff_teams`, `playoff_week_start`, `num_teams`, and the raw `settings` JSON in `league.db`'s `leagues` table, which is not published today. The owner's league: 12 teams, 8 playoff teams, playoffs from week 15 (the regular season is weeks 1 to 14), `playoff_seed_type` 0 (a fixed bracket, no reseeding), `playoff_round_type` 0 (one week per round), no byes.
- **Publishing** (`pipeline/steps/publish.py`): `TABLES` maps local tables to production names; `keep_latest_run` keeps each week's rows from its most recently created run; `validate.py` lists the futures tables it checks. `scripts/publish.py` is the notebook-era publisher with its own map.
- **The app's futures** (`app/markets.py` `SHAPES`, `QUOTE_SQL`; `app/routes/odds.py` `_futures_rows`; `betting.js` kinds `fp` and `mp` under the Futures tab): quoted at the season's highest published week; a futures bet's `week` is the week it was placed in; `find_quote` reads `american_odds` and `probability`. Cash-out prices a futures bet from its latest quote (`app/cashout.py` `_futures_probability`). Parlays refuse futures legs.
- **Settlement** (`app/settlement.py`): `outcomes_for_week(week, scores)` judges the pending bets whose `bet.week == week`; futures are `undecided, "futures: settle by hand"`. The admin previews and confirms (`settlement_preview`, `settle_outcomes`), one transaction per bet; the by-hand Win and Loss buttons (`settle_bet`) close singles. `ledger.settle` and `ledger.push` post the bet's result to `bet.week`'s `weekly_stats`; `ledger.cash_out` posts to the week it is taken (the owner's rule: a returned stake never counts as profit, and a bet cannot buy another week's leaderboard).
- **Standings in the app**: `_standings_through(week, league_id)` in `app/routes/odds.py` builds records from `sleeper_matchups` pairs (wins, losses, ties, points for) and sorts by wins then points; `PLAYOFF_CUTOFF = 8` is hard-coded there.
- **Tests**: `tests/conftest.py` fakes the published tables for week 10 of 2026 (`ANALYTICS_TABLES`, `create_analytics_tables`, `seed_analytics`); the seeded league is `league1` with rosters 1 and 2. `tests/pipeline/test_playoffs.py` runs the playoffs step on a four-team fake league with fake sources and 400 sims; read it before touching the step.

## 3. Goal

Two more futures for the owners: **last place** (finishing last in the regular-season standings) and **champion** (winning the playoff bracket), priced by the pipeline from the same simulated seasons the other futures use, offered as singles with cash-out, and settled the way each deserves: the standings futures (first place, make playoffs, last place) automatically from the published standings once the regular season ends, through the admin's preview; the champion by hand after the final. Alongside, the pipeline stops re-stamping futures rows on a playoffs-only rerun, and a futures bet's profit posts to the week it settles, not the week it was placed.

The driving risk: the bracket simulation paying the wrong team (seeding, pairing or tie rule wrong). The second: standings settlement ranking a tie differently from Sleeper.

## 4. Decisions, taken by the orchestrator 2026-09-29 with the owner

1. **Markets.** `last_place` (key `2026-last_place`, selection a roster id; table `betting_odds_last_place`) and `champion` (key `2026-champion`, selection a roster id; table `betting_odds_champion`). Both tables have exactly the columns of `betting_odds_first_place`. First Place stays the #1 seed. No top-N market (the owner: no third place, and the playoff line is `make_playoffs`).
2. **Last place** is the chance of finishing position `num_teams` in the standings simulation: `counts[:, num_teams - 1] / n_sims`, the same ranking as the other positions.
3. **Champion** mirrors Sleeper's bracket exactly, read from the league settings: `playoff_teams` seeds from each simulated season's final standings; rounds `log2(playoff_teams)` (8 teams: three rounds, weeks `playoff_week_start` to `playoff_week_start + 2`); round one pairs seed *i* with seed `playoff_teams + 1 − i` (1v8, 2v7, 3v6, 4v5); later rounds pair winners in fixed bracket order (the winner of 1v8 meets the winner of 4v5, the winner of 2v7 meets the winner of 3v6, then the final), no reseeding; no byes; a tie in a playoff game advances the higher seed. A league whose `playoff_seed_type` or `playoff_round_type` is not 0, or whose `playoff_teams` is not a power of two, makes the step raise `RuntimeError` naming the setting, as divisions do today. The playoff weeks are projected, picked and simulated by the same path as the future regular-season weeks (they need no pairings). `champion = wins of the final / n_sims`.
4. **Chances under 1% or over 99% are not offered**, as for the other futures (the pipeline's existing rule, kept for consistency; the standings matrix keeps every number).
5. **Futures rows carry the simulation's run id.** `save_tables` stamps `run_id` with the simulation run the step read (`odds.latest_simulation(ctx)["run_id"]`), not the pipeline run's id, for the futures tables and the standings matrix alike. A playoffs-only rerun then replaces the week's rows under the same run id, so the app sees the odds as unchanged, and `keep_latest_run` finds the run in `simulation_runs`.
6. **The league's settings are published.** `league.db`'s `leagues` table publishes as `sleeper_leagues` with its columns as they are; the app reads `settings` (JSON) for `playoff_week_start`, `playoff_teams` and `num_teams` of the league its matchups name. `validate` and both publishers learn the two new tables and `sleeper_leagues`.
7. **Standings settlement.** When the admin previews the final regular-season week (`week == playoff_week_start − 1`), the preview also lists every pending `first_place`, `make_playoffs` and `last_place` bet, whatever week it was placed in, judged from the standings through that week: undecided until every roster of the league has a score in every regular-season week (reason `regular season not complete: 10 of 12 rosters scored in week 14`); otherwise ranked by wins, then points for, then roster id (Sleeper's tie-break is points for; the pipeline's ranking puts ties between wins and points, which cannot differ here because a tie counts for neither side), and `first_place` wins at rank 1, `make_playoffs` at rank ≤ `playoff_teams`, `last_place` at rank `num_teams`; reason `3rd of 12: 9-5, 1,612.30 pts`. `champion` bets stay `undecided, "champion: settle by hand after the final"`.
8. **Profit posts to the week a futures bet settles.** `ledger.settle(bet, won, potential_win=None, leg_statuses=None, week=None)` and `ledger.push(bet, leg_statuses=None, week=None)`: `week` is the week `settled_pnl` and `bets_won` post to, defaulting to `bet.week`; `active_bets_amount` always leaves `bet.week`'s row, as `cash_out` already does. The admin routes pass the settlement week for a futures bet (one whose legs have no week): `settle_outcomes` passes the week being settled, `settle_bet` (by hand) the current week, after `open_week` for that week. Weekly bets keep posting to their own week.
9. **The by-hand path** keeps working for every futures market (Win and Loss on the Pending Bets card), including the champion after the final; it is the only path for the champion.
10. **Cash-out** on the two new markets works through the existing futures path with no change. Parlays keep refusing futures legs.
11. **No champion odds during the playoffs.** The step's early return stays; a champion bet placed in week 14 is the last. Recorded as a follow-up, not built.

## 5. Ownership and interfaces

### Part P: the pipeline

| Files | You may |
|---|---|
| `pipeline/steps/playoffs.py`, `pipeline/standings.py`, `pipeline/steps/validate.py`, `pipeline/steps/publish.py`, `scripts/publish.py` | edit |
| `tests/pipeline/test_playoffs.py`, `test_standings.py`, `test_validate.py`, `test_publish.py` | edit |
| `docs/architecture/02-data-pipeline.md`, `03-modeling-and-odds.md`, `04-data-model.md` | edit |

Not yours: anything under `app/`, `frontend/`, `tests/*.py` at the top level, `tests/conftest.py`, docs 05 to 08, `CLAUDE.md`.

- `playoffs.py`: `last_place` and `champion` per decisions 2 and 3; the bracket in its own short functions (seeds per sim from `finishing_positions`, one function that plays a round given seeds and a week's scores, one that plays the bracket); `FUTURES_DDL` gains the two tables; `save_tables` per decision 5; `print_futures` gains the two columns; the unsupported-settings errors. Playoff weeks join `future_weeks` for projection, picking and simulation (and for `refuse_weeks_already_run` / `clear_future_weeks`), but not for the standings, which end at `playoff_week_start − 1`. `check_totals` also checks the champion chances sum to 1 and last place to 1.
- `standings.py` only if the seed or bracket helpers belong beside `finishing_positions`.
- `validate.py`: the two tables in `FUTURES_TABLES` and the probability and owner checks; `publish.py` and `scripts/publish.py`: the two tables and `("league", "leagues", "sleeper_leagues")`.
- Tests: the bracket on hand-built seeds and scores (a 4-team and an 8-team case with known winners, a tie advancing the higher seed, the pairing order in round two); the step on the fake league producing all five tables with chances that sum as decision 3 says and rows stamped with the simulation's run id; an unsupported setting raising; validate and publish knowing the new tables.
- Docs: 02's step table and the playoffs step's paragraph; 03's futures section (the bracket rule, the tie rule, what is not offered); 04's tables and the publishing map (`sleeper_leagues`, the two tables, the run id change).

### Part A: the app's backend

| Files | You may |
|---|---|
| `app/markets.py`, `app/settlement.py`, `app/ledger.py`, `app/routes/admin.py`, `app/routes/odds.py`, `app/routes/betting.py` | edit |
| `tests/conftest.py`, `tests/test_markets.py`, `tests/test_settlement_outcomes.py`, `tests/test_settlement.py`, `tests/test_ledger.py`, `tests/test_admin.py`, `tests/test_odds.py`, `tests/test_betting.py`, `tests/test_cashout.py` | edit |
| `docs/architecture/05-web-app.md`, `06-betting-lifecycle.md` | edit |

Not yours: `pipeline/`, `scripts/`, the frontend (part B), `app/cashout.py`, `app/parlays.py`, `app/matrices.py`, `app/windows.py`, `app/models.py`, docs 02 to 04 and 08, `CLAUDE.md`.

- `markets.py`: `SHAPES["last_place"] = (False, ())`, `SHAPES["champion"] = (False, ())`; `QUOTE_SQL` entries shaped like `first_place`'s on the two tables. `betting.py`: `MARKET_LABELS` gains `Last Place` and `Champion`, so descriptions read `Bob B: Last Place +400` and `Alice A: Champion +250`. `odds.py`: `GET /api/last_place` and `GET /api/champion` through `_futures_rows`, no login, `[]` on error; replace the hard-coded `PLAYOFF_CUTOFF` with the published `playoff_teams` where `league_overview` uses it, reading it through the same settings function settlement uses (put that function in `settlement.py` or `routes/helpers.py`, whichever reads better; it takes a league id and returns `playoff_week_start`, `playoff_teams`, `num_teams` from `sleeper_leagues.settings`).
- `settlement.py`: decision 7. Keep `outcome_for` for the week's own bets; add the standings pass (`standings_outcomes(week, league_id)` or similar) that `outcomes_for_week` runs when the week is the final regular-season one; the standings from `sleeper_matchups` pairs as `_standings_through` builds them (move or share that code; `odds.py` may import from `settlement.py`, not the reverse); the reasons as decided. The champion's undecided reason.
- `ledger.py`: decision 8; `_close` posts `settled_pnl` and the counters to `week` and `active_bets_amount` to `bet.week`, as `cash_out` does; share what the two have.
- `admin.py`: `_settle_as_shown` passes `week` for a futures bet (legs with no week); `settle_bet` passes the current week for one after `ledger.open_week(bet.user_id, week)`; `_preview_row` unchanged in shape.
- `conftest.py`: `betting_odds_last_place` and `betting_odds_champion` (columns as `first_place`), seeded for week 10 under `RUN_ID` (say Alice A last place 0.20 `+400`, Bob B 0.30 `+230`; champion Alice A 0.30 `+233`, Bob B 0.15 `+567`); `sleeper_leagues` with one row for `league1` whose `settings` JSON fits the seeded two-roster league: `num_teams` 2, `playoff_teams` 1, `playoff_week_start` 11, so week 10 is the final regular-season week and the standings tests run on the seeded matchups; `seed_analytics` also needs `sleeper_matchups` rows for weeks 1 to 9 for both rosters, or the standings tests insert them (`set_points` sets week 10; add a helper that scores a whole regular season). Keep every existing constant and fixture.
- Tests: quotes and placement of both markets (description, columns); `my_bets` and cash-out on a `last_place` bet after a newer futures run; the standings pass: undecided while a roster lacks a week, then first place, make playoffs and last place each won and lost from a fully scored season, the tie-break by points, the reasons; the champion undecided reason; `settle_outcomes` on the final week closing a futures bet placed in an earlier week with the profit in the final week's `weekly_stats` and the placement week's `active_bets_amount` back at 0; `settle_bet` by hand on a futures bet posting to the current week; ledger `week` defaulting to `bet.week` for every existing case (the existing tests must pass unchanged); `league_overview` reading `playoff_teams`; the two endpoints.
- Docs: 06's Markets table rows, the futures settlement paragraph (decision 7's rule and reasons), the Accounting rows and the sentence on where a futures bet's profit posts, the by-hand paragraph; 05's routes and Bet endpoints tables, the API map, the admin endpoints paragraph (the preview's final-week rows).

### Part B: the page

| Files | You may |
|---|---|
| `frontend/static/js/betting.js`, `frontend/static/css/betting.css`, `frontend/templates/betting.html` | edit |

Not yours: anything under `app/`, `tests/`, `docs/`. Build to the contract: `GET /api/last_place` and `GET /api/champion` return rows shaped exactly like `/api/first_place` (`owner`, `probability`, `odds`, `win_prob`, `market`, `run_id`, `team_id`, and whatever else `_futures_rows` returns today; read `app/routes/odds.py`); `my_bets` summaries carry `bet_type` `last_place` or `champion` with `market` and `selection` as for `first_place`.

- `SOURCES.lp = '/api/last_place'`, `SOURCES.ch = '/api/champion'`; `state.rows.lp`, `state.rows.ch`; the Futures tab lists four groups in this order: First place, Make playoffs, Last place, Champion. `isFuture` covers the two kinds; `sidesOf` for them is the single-side futures shape (selection the roster id); `renderFuturePicks` as for `fp`. `chipLabel`: `Bob B to finish last`, `Alice A to win the championship`. `ACTIVE_GROUPS`'s FUTURES group gains the two `bet_type`s.
- Nothing else changes; check signed out and at 390 px with a mocked reply, `node --check`, and report.

## 6. Reports

Each part ends with a report to the orchestrator: what you built per file; what you verified by hand (part P: the step on the fake league with the champion chances printed; part A: a real placement and a final-week preview through the test client; part B: the mocked tab); tests added and the pytest total; the `ruff check` and `ruff format --check` results; deviations and why; changes needed in files you do not own (CLAUDE.md lines, doc 08 rows, the design doc); open questions. Do not commit.

The orchestrator reviews the parts together, runs the suite and the browser check, commits in logical steps, pushes `b10-futures` and opens the pull request against `main`. The owner merges.
