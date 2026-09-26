# 08 – Constraints & Debt

This page lists facts a refactor has to work around, grouped by area. Nothing here is a plan. Each item says what is true today and where to find it. Severity: **H** = can produce wrong money or data, **M** = slows change or breaks easily, **L** = cleanup.

## Open issues

High-severity problems found while writing these docs (2026-09-26). Check them off and link the fixing commit when they're resolved.

- [ ] **Client-supplied odds.** `highest_scorer`, `lowest_scorer`, `first_seed`, and `ammad_playoff` bets store the `odds` string sent by the browser without checking it against the odds tables, so a crafted request can set any payout. `app/routes/betting.py:255`
- [ ] **Duplicate odds rows after re-running notebook 07.** The five `betting_odds_*` tables append by `run_id` and the app doesn't filter by it. Duplicates appear on the site, and since moneyline/O/U bets select by row position (`matchup_idx`, `team_idx`), they can shift which matchup a bet lands on. Negative indexes and unknown `choice` values also aren't rejected. `app/routes/odds.py:30`, `app/routes/betting.py:420–482`, notebook 07
- [ ] **Balance race.** Balance updates are read-modify-write on the ORM object with no row lock, so concurrent place/remove/settle requests can overwrite each other. `app/routes/betting.py`, `app/routes/admin.py:118`

## Cross-cutting

| | Item | Where |
|---|---|---|
| M | **Three team identifiers.** Tables key teams by `roster_id`/`team_id`, by `owner` (Sleeper display name), or by `team_name`. Joins translate between them in several places. | [04](04-data-model.md), `app/routes/odds.py`, notebooks 06/07/09 |
| M | **Two week formats.** `"Week N"` text in `projections*` tables, integer everywhere else. | `backend/scrapers/database.py`, notebooks 04/05 |
| M | **No shared "current week".** The site uses the highest unsettled `BettingPeriod`; each notebook has its own `CURRENT_WEEK` (currently 16 in 01/05/06/07 and 14 in 08/09). | `app/routes/helpers.py:57`, notebook config cells |
| M | **Single league and season baked in.** Owner-name map for 12 specific people, `LEAGUE_ID` default, 2025 bye weeks, "ammad_playoff" bet type, 8-team playoff cutoff, fallback week 10. | `helpers.py:14`, `odds.py:20`, `scraper_sleeper_league.py:573`, `betting.py:380` |
| M | **Implicit offline→online contract.** Column names the app reads are not declared anywhere; `publish.py` copies whatever the notebooks produced, and `tests/conftest.py` re-declares the schema by hand. | `scripts/publish.py`, `tests/conftest.py` |

## Pipeline

| | Item | Where |
|---|---|---|
| H | **Re-running notebook 07 duplicates odds rows** (append by `run_id`) unless `DELETE_WEEK` is set; the app doesn't filter by `run_id`. | notebook 07, `odds.py:30`, `odds.py:69` |
| M | Notebooks are the orchestration layer: absolute Windows paths, per-notebook config, run by hand in order. | `backend/notebooks/01–09` |
| M | Notebook 07 reads lineups from a **CSV** written by 06, not the database. | notebooks 06/07 |
| M | Notebook 07 calls the live Sleeper bracket API in playoff mode, so results depend on when it runs. | notebook 07 |
| M | Notebook 02 duplicates `scripts/scrape.py` without the stale-row delete or validation. | notebook 02 |
| M | Notebook 03 rewrites **all weeks** in place with one-off fixes (e.g. specific players). | notebook 03 |
| M | Name matching is heuristic with one hardcoded override; unmatched projections are silently dropped from μ/σ. | notebook 04 |
| M | Scrapers have no common interface; `scrape_and_save` signatures differ, default DB paths are relative to the working directory, and default seasons disagree (`"2024"` vs `"2025"`). | `backend/scrapers/scraper_*.py` |
| M | ESPN, FantasyPros, FirstDown depend on page structure; FantasyPros and FirstDown can't request a specific week. | [02](02-data-pipeline.md#projection-sources) |
| M | Odds conversion is copy-pasted between notebooks 07 and 09, with different edge-case outputs. | notebooks 07/09 |
| M | Playoff odds (09) simulate only the current week, not the remaining schedule. | notebook 09 |
| L | Notebook 08 hardcodes 12 teams/6 matchups and fails in playoff weeks. | notebook 08 |
| L | `simulation_runs.n_matchups` is recorded as 0. | notebook 07 |
| L | Dead code/data: `database_users.py`, empty `projections.player_stats` and its methods, stale `betting_odds_*` copies in `projections.db`, `.ipynb_checkpoints/`, empty root `odds.db`, `instance/betting_app.db`. | as listed |
| L | `backend/` is excluded from ruff, so none of the pipeline code is linted. | `ruff.toml` |

## Modeling

| | Item | Where |
|---|---|---|
| M | Players are simulated independently (no QB–WR, same-game, or opponent correlation). | notebook 07 |
| M | Model parameters (α, β, σ_pos, replacement ranks, seed, N) are notebook constants, not versioned alongside the output. | notebooks 05/06/07 |
| L | Simulation results depend on the order teams/players are drawn from one seeded RNG. | notebook 07 |

## Web app

| | Item | Where |
|---|---|---|
| H | **Client-supplied odds.** `highest_scorer`, `lowest_scorer`, `first_seed`, `ammad_playoff` bets store the `odds` string sent by the browser without checking it against the odds tables. | `app/routes/betting.py:255` and the three branches after it |
| H | **Index-based selections.** Moneyline and team O/U bets identify the pick by row position (`matchup_idx`, `team_idx`) in a query result; negative indexes aren't rejected, and `choice` isn't validated. | `betting.py:420–482` |
| H | Balance updates are read-modify-write without row locks; concurrent requests can race. No ledger table. | `betting.py`, `admin.py:118` |
| M | CSRF is off by default; JSON `POST`/`DELETE` endpoints (including admin) are unprotected apart from SameSite=Lax. | `app/extensions.py`, `app/__init__.py` |
| M | `place_bet` has six near-identical branches (~330 lines). | `betting.py:206` |
| M | Bet selections are stored only as display strings (`description`); settling a bet requires a human to read them. | `app/models.py` |
| M | Settlement is fully manual; `settle_week` doesn't settle bets. | `admin.py:178` |
| M | Analytics tables have no ORM models or schema checks; errors are caught and returned as `[]` with 200. | `odds.py` |
| M | Schema changes are ad-hoc `ALTER`s run at startup; failures are logged and ignored. | `app/migrations.py` |
| L | Logging is `print()` + `traceback.print_exc()`. | all routes |
| L | `/api/first_place` and `/api/ammad_playoff` have no frontend caller; `/analytics` picks its week from PNG filenames even though the page no longer shows PNGs. | `odds.py:142`, `pages.py:31` |
| L | Admin `pending_bets` defaults to week 10. | `admin.py:89` |

## Data & publishing

| | Item | Where |
|---|---|---|
| M | Publishing replaces whole tables (all weeks) every time; the analytics tables get pandas-inferred types and no keys or indexes. | `scripts/publish.py` |
| M | `montecarlo.db` (400+ MB) grows by ~600k rows per run and is never pruned. | notebook 07 |
| L | `--dry-run` still creates (then drops) staging tables in production. | `publish.py` |

## Ops

| | Item | Where |
|---|---|---|
| M | Single droplet for app and database; backup strategy is not documented. | [07](07-deployment-and-ops.md) |
| M | Deploys are a manual `ssh … git pull` with no CI gate or rollback step; code, data, and charts are released independently. | `CLAUDE.md` |
| M | No pinned dependency versions; `gunicorn` isn't in `requirements.txt`. | `requirements.txt` |
| L | No health check, error tracking, or alerting. | — |
| L | No `.env.example`. | — |

## Test gaps

Real OAuth, `/account` + CSRF, `pages.py`, `publish.py`, notebooks, live scrapers, JavaScript, and Postgres-specific behavior are untested. See [07](07-deployment-and-ops.md#tests).
