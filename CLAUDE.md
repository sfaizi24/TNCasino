# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

TNCasino — fantasy football analytics & fake-money betting platform. Flask web app + a Python data pipeline (`pipeline/`). Scrapes projections from multiple sources, runs Monte Carlo simulations, and generates betting odds.

**Auth**: Google OAuth via flask-dance (`app/auth.py`). Admin access controlled by `ADMIN_EMAILS` env var.

## Deployment

- **Production**: https://tncasino.win — DigitalOcean Droplet (143.198.183.213), gunicorn + nginx, Cloudflare DNS/SSL
- **Deploy code**: `git push origin main && ssh root@143.198.183.213 "cd /opt/tncasino && git pull && sudo systemctl restart tncasino"`
- **Publish data**: `python -m pipeline run --week N --steps publish --dry-run` to see what would be uploaded, then the same without `--dry-run`. It stages and swaps this season's analytics tables and run records into production PostgreSQL at `DATABASE_URL`.
- **Publish analytics charts**: the publish step uploads `backend/data/images/*.png` to the droplet's `/var/lib/tncasino/analytics/` itself, by `scp`, before any table (`--no-charts` skips it). The Flask app reads them from `$ANALYTICS_IMAGES_DIR` (set in prod `.env`), so they live outside the git working tree and survive `git pull`.
- **Server config**: systemd service at `/etc/systemd/system/tncasino.service`, nginx at `/etc/nginx/sites-available/tncasino`
- **Production env**: `/opt/tncasino/.env` (separate from local `.env`)

## Commands

- **Run locally**: `python -m app` (Flask on 0.0.0.0:5000)
- **Run the week**: `python -m pipeline run --week N` runs every step but publish, in order. `--steps league,lineups` runs only the named steps, `--from clean` resumes at a step, `--sources sleeper,espn` narrows the scrape and playoffs steps, `--no-charts` skips the PNGs.
- **Check a week**: `python -m pipeline status --week N` (each step's latest status, duration, run and notes)
- **Judge a source**: `python -m pipeline review --week N --source espn.com --verdict ok|reject --note "..."` (a reject deletes that source's rows for the week; then rerun `--from clean`)
- **Backfill a source's past weeks**: `python -m pipeline backfill --season 2025 --weeks 10-16 --source fftoday` (weeks already played only; `value_agreement` is advisory there, every other check still refuses)
- **Fit the model**: `python -m pipeline fit-model --season 2025 --weeks 10-16 --out v2.3 --exclude-sources fantasypros.com` (writes `pipeline/model/params/v2.3.json`, scored week by week beside v1)
- **Publish**: `python -m pipeline run --week N --steps publish --dry-run`, then without `--dry-run`
- **Install browser drivers**: `playwright install chromium` (required for the FanDuel source)
- **Format**: `ruff format <file>` | **Lint**: `ruff check <file>`

## Data Pipeline

The `pipeline/` package runs a week as thirteen steps in a fixed order (`STEP_ORDER` in `pipeline/steps/__init__.py`) and records every run and step in `pipeline.db`. See `docs/architecture/` for how the whole system works (pipeline, modeling, data model, web app, betting, deployment, known debt); update the relevant page when behavior changes. The weekly routine, command by command, is the runbook skill `.claude/skills/run-pipeline/SKILL.md`.

1. `league` — Sleeper league, users, rosters, matchups, players and actual points, plus the NFL schedule from ESPN, into `league.db`
2. `scrape` — fetch each source, verify it, store the rows that pass in `projections`
3. `clean` — strip injury tags and suffixes, make positions, team codes and defense names canonical, in place
4. `match` — link each projection to a Sleeper player id → `projections_with_sleeper`
5. `stats` — one distribution per player (μ, σ) from the model parameters → `player_week_stats`
6. `accuracy` — grade last week's projections, team totals and moneylines → `prediction_accuracy`, `team_accuracy`
7. `calibrate` — season-to-date calibration of the model as it ran, no refit → `calibration_metrics`
8. `lineups` — each roster's best lineup, starters whose game is final pinned at their points, holes filled from the waiver wire → `team_lineups`, `team_projections_summary`, `projections_rosters`
9. `simulate` — 50,000 correlated lognormal draws of every starter, locked starters fixed → Parquet in `backend/data/sims/`, a `simulation_runs` row with `window_closes_at`
10. `odds` — price the week's markets and chart curves from the latest draws → `betting_odds_*`, curve tables
11. `playoffs` — project and simulate the rest of the season and the bracket, priced through `pipeline/markets.py` → make playoffs (yes or no), last place, champion, `standings_probability_matrix`, and the run's simulated seasons in `simulation_standings`
12. `validate` — cross-table checks over lineups, simulation and odds; writes nothing, stops a broken week before publish
13. `publish` — runs only when named: charts, new score matrices into `simulation_totals` and new standings into `simulation_standings`, then staging and swap into PostgreSQL

Eight sources live in `pipeline/sources/`: Sleeper (the reference the others are checked against), ESPN, FantasySharks, FirstDown, FanDuel (headless Chromium through Playwright), FFToday, RotoBaller and Fleaflicker (both read under a permission letter; Fleaflicker reads the owner's own league, `FLEAFLICKER_LEAGUE_ID`). Every HTTP request identifies itself with `USER_AGENT` and goes through `base.get`, spaced per host. `pipeline/sources/verify.py` checks each source's week against Sleeper's players and projections and its own previous week (position_agreement, duplicate_positions, position_counts, value_agreement, team_codes, week_stamp, freshness, top_players); a failing source is dropped for the week, and a full run needs three usable sources, Sleeper among them.

## Database Architecture

**Production**: Self-hosted PostgreSQL on the DigitalOcean droplet. All data (user/betting + analytics) lives in one database, connected via `DATABASE_URL`.

**Local pipeline**: The steps write to local SQLite files in `backend/data/databases/` (`league.db`, `projections.db`, `odds.db`, and `pipeline.db` for the run records), the simulation draws to Parquet in `backend/data/sims/`, and the charts to `backend/data/images/`. All of it is **gitignored** — never committed.

**Publishing data**: The publish step (`python -m pipeline run --week N --steps publish`) appends each new run's score matrix to `simulation_totals` and its simulated seasons to `simulation_standings`, then pushes this season's analytics tables and run records (`TABLES` in `pipeline/steps/publish.py`, 24 of them) from local SQLite to production PostgreSQL using a staging+swap strategy. Tables are renamed to avoid collisions (e.g., `users` → `sleeper_users`, `rosters` → `sleeper_rosters`).

**Flask app reads**: All routes query PostgreSQL via `db.session` (SQLAlchemy). Analytics queries use `query_analytics()` helper in `app/routes/helpers.py`.

## Key Gotchas

- **Sources are fragile** — they break when a site changes its layout or API. The verification checks catch most breaks and drop the source for the week; the fix is a parser change in `pipeline/sources/` plus a refreshed fixture under `tests/pipeline/fixtures/`.
- **Player name matching is brittle** — injury indicators get stripped from names; mismatches cause silent data loss.
- **Monte Carlo draws a lognormal mixture, not a normal** — each starter's score is a dud with a fitted probability, else a lognormal around μ; σ comes from the model version's fitted line in μ (`pipeline/model/params/v2.3.json`, the default), with teammates correlated by position pair. `v1.json` keeps the 2025 formulas, including the position baseline σ of QB 7, RB 9, WR 10, TE 8, K 4, DEF 7.
- **One module of win rules** — `pipeline/markets.py` says what wins and what pushes for every market; the odds and playoffs steps price through it, the Flask app settles through it, and it will re-price through it. It imports only numpy and the standard library, and `pipeline/__init__.py` stays a bare docstring so the app can import it cheaply.
- **`simulation_totals` is append-only** — the pipeline's publish step stores each published run's score matrix there before the staging-and-swap and never replaces the table; `simulation_standings`, each futures run's simulated seasons, is appended the same way, and every other published table is swapped whole.
- **The betting window comes from the runs, the lock is the kill switch** — `app/windows.py` opens betting while the latest published run's `window_closes_at` is in the future and pauses it after; the next publish reopens it. The admin's `lock_time` is the hard close: the lazy lock flips `is_locked` for good, so set it at the week's last kickoff (Sunday's first game), never Thursday's.
- **Locks come from final games** — the lineups step pins the owners' actual starters whose NFL game is final at their real league points (`team_lineups.is_locked`, `locked_points`), the simulate step fixes them in every draw and records `n_locked`, a game in progress only warns, and the accuracy step grades the latest run with `n_locked = 0` so real points never flatter the model. The lineup endpoints select the two columns, so run the lineups step and publish from this code before restarting the app; until then `/api/lineup` and `/api/team_players` return empty lists.
- **Every requirement is pinned exactly** — `requirements.txt` names the version each package resolved to on 2026-09-29. An upgrade is an edit to that file, tested locally before it reaches CI; `pip install -r requirements.txt` on a machine with older packages upgrades them.
- **Cash-out exists only after a reprice** — a pending bet, single or parlay, can be removed for a full refund while its own run is still the latest; once a newer run has repriced it, `app/cashout.py` offers 95% of fair value from that run instead, and the ledger posts only the profit or loss to the week the cash-out is taken.
- **Parlays are priced and settled on the score matrix** — `app/parlays.py` prices a slip at the share of the latest run's sims in which every leg wins, with no cap and no house edge, and refuses two legs from one market, a leg that adds nothing, and a combination the sims never produce; a pushed leg drops out at settlement and the rest re-price on the placement run's matrix, which is why `simulation_totals` keeps every run. A slip is weekly picks or futures picks, never both (`mixed`); a futures slip is priced the same way on the latest futures run's simulated seasons, which the playoffs step stores and the publish step appends to `simulation_standings`, never swapped. `parlay_refusals` is the app's table; the publish step leaves it alone.
- **Futures settle from the standings and post to the week they settle in** — the futures are make playoffs (YES or NO, a card for every team, a side at a chance of exactly 0 or 1 at NULL odds and not offered), last place and champion (every team at its fair odds however long the shot; only a chance of exactly 0 or 1 is left out); first place is gone, and the two settled 2025 first-place bets keep `bet_type = 'first_place'` and their description. The regular season's last week's settlement preview judges every pending bet with a futures leg, single or parlay, from the final standings: YES wins within `playoff_teams`, NO outside them, and a lost leg settles a parlay at once. The champion, and a futures parlay whose standings legs won and whose champion leg is left, are settled by hand after the final (Win and Loss on the Pending Bets card). A futures result posts to the week it settles in, opened for the user first. The app reads the playoff format from `sleeper_leagues`, so after this lands publish before restarting the app: until the first publish creates the table, `/api/league_overview` fails and the settlement preview refuses. The app also reads `no_probability` and `no_american_odds` in `betting_odds_make_playoffs` and the `simulation_standings` table, so after B12 merges, run the pipeline (the playoffs step at least) and publish from this code before restarting the app: until then `/api/make_playoffs` returns an empty list, and make-playoffs bets and futures parlays are refused. The orphaned production table `betting_odds_first_place` is dropped by hand after the merge, since publish swaps only the tables it lists.
- **Tests**: `python -m pytest` — 1394 tests (app tests on in-memory SQLite, pipeline tests on scratch SQLite files and saved fixtures), about 50 s. CI runs lint + tests on every push/PR.
- **`.env` required** — needs `SECRET_KEY`, `DATABASE_URL`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `ADMIN_EMAILS`. Local dev also needs `OAUTHLIB_INSECURE_TRANSPORT=1` and `OAUTHLIB_RELAX_TOKEN_SCOPE=1`. The Fleaflicker source reads `FLEAFLICKER_LEAGUE_ID` (the owner's own league, never committed); unset, that source fails and the run goes on without it. Prod sets `ANALYTICS_IMAGES_DIR=/var/lib/tncasino/analytics` so the analytics charts live outside the git working tree; local dev falls back to `backend/data/images/`.

## Code Quality Philosophy

This is a portfolio project. Every line of code should look like it was carefully written by a thoughtful engineer, not generated by AI. Prioritize:

- **Human-readable** — clear variable names, logical flow, no clever one-liners. A reviewer should understand intent at a glance.
- **Small and focused** — keep files, functions, and classes short. Extract when something does two things. Avoid bloat.
- **Easily debuggable** — straightforward control flow, no deep nesting, no magic. When something breaks, the cause should be obvious.
- **Impressive to reviewers** — clean architecture, consistent patterns, well-structured modules. The kind of code that makes someone think "this person knows what they're doing."

Do not leave behind AI artifacts: no over-commented code, no unnecessary docstrings on obvious methods, no defensive error handling for impossible cases, no "helper" abstractions that only get used once.

## Code Style

- Python 3.13+, formatted with `ruff format`
- Flask with Jinja2 templates in `frontend/templates/`
- SQLAlchemy ORM (models in `app/models.py`, init in `app/database.py`)

## App Structure

```
app/                  — Flask application package
  __init__.py         — App factory, config, extensions, blueprint registration. Also exposes `app` for gunicorn.
  __main__.py         — `python -m app` entry point for local dev
  auth.py             — Google OAuth, login_manager, admin email allowlist
  cashout.py          — What a pending bet is worth now: Offer, NoOffer, offer_for, offers_for
  database.py         — SQLAlchemy instance
  extensions.py       — Shared Flask extensions (CSRFProtect)
  ledger.py           — The only code that moves money: open_week, place, remove, settle, push, void, cash_out (settle and push take a parlay's adjusted payout, per-leg statuses and the week the result posts to)
  markets.py          — Market keys and their quotes: parse_key, key_for_row, find_quote (a spread at its line from the score matrix), spread_quote, price_from_odds, odds_from_probability, potential_win
  matrices.py         — A run's score matrix and its simulated seasons, each decoded once per worker, and the win rules on them: score_matrix, standings_matrix, leg_outcome, futures_outcome, joint_probability
  migrations.py       — Schema migrations (run on startup)
  models.py           — SQLAlchemy models (User, Bet, BetLeg, ParlayRefusal, WeeklyStats, BettingPeriod)
  parlays.py          — A slip of 2 to 4 weekly picks or 2 to 4 futures picks, never both, priced at the joint chance of its run: quote, joint_price, is_futures, ParlayRefusal
  settlement.py       — Outcomes of a week's keyed bets from the published scores and, in the regular season's last week, of every bet with a futures leg from the final standings: team_scores, outcomes_for_week, outcome_for, final_standings, standings_before, league_settings
  windows.py          — Whether a week is open for betting: Window, betting_window, latest_run_id
  routes/
    helpers.py        — Shared helpers: query_analytics(), get_current_week(), check_betting_period_lock(period), admin_required()
    pipeline_summary.py — A pipeline step's summary as typed sections for the dashboard: summary_sections()
    pages.py          — Public pages: /, /about, /analytics, static files
    account.py        — User account: /account, /account/update-profile
    odds.py           — Odds API: /api/matchups, /api/spreads, /api/last_place, /api/champion, /api/team_performance, etc. (14 routes)
    analytics.py      — The analytics page's season outlook and model report: /api/playoff_picture, /api/season_race, /api/model_report
    money.py          — Where the money sits, in totals only: /api/money
    betting.py        — Betting: /betting, /leaderboard, /api/place_bet, /api/betting_window, /api/cash_out, /api/parlay_quote, etc. (9 routes)
    admin.py          — Admin: /admin, /admin/pipeline, /api/admin/* (12 routes)

pipeline/             — The weekly pipeline (invoked as `python -m pipeline <command>`)
  __main__.py         — CLI: run, status, review, backfill, fit-model, migrate-legacy
  runner.py           — Runs steps in canonical order and records every run and step in pipeline.db: run_steps, make_run_id, record_review
  settings.py         — Season, week, league and paths from flags, the environment and Sleeper: Settings, load_settings, load_local_settings
  db.py               — SQLite paths and connections, and the run-record tables pipeline_runs, pipeline_steps, source_reviews
  backfill.py         — Stores one source's projections for weeks already played, for the model fit
  legacy.py           — One-off migration that converted the 2025 databases to integer seasons and weeks (migrate-legacy)
  markets.py          — The win rule of every market on a run's score matrix or its simulated seasons, and both encodings (encode_totals, encode_standings); imports numpy and stdlib only
  names.py            — Player-name normalisation shared by the sources, clean and match
  standings.py        — Final standings for every simulation and the playoff bracket they seed
  waivers.py          — Fills lineup holes from the waiver wire in FAAB order, capped at the league's median starter
  charts.py           — PNG charts for the analytics page (with accuracy_charts.py and playoff_charts.py)
  sources/
    base.py           — Projection, ProjectionSource, and get(): every HTTP request, with USER_AGENT and per-host spacing
    verify.py         — The silent-failure checks run on a source's week before its rows are stored
    teams.py          — NFL team codes as Sleeper spells them, and the aliases other sources use
    sleeper.py        — Sleeper's public projections API; the reference every other source is checked against
    espn.py           — ESPN's fantasy API
    fantasysharks.py  — FantasySharks' projections table (no QB)
    firstdown.py      — FirstDown Studio's rankings snapshot (no DEF)
    fanduel.py        — FanDuel Research's GraphQL responses, captured in headless Chromium by Playwright
    fftoday.py        — FFToday's projections table, rescored with league scoring (QB, RB, WR, TE)
    rotoballer.py     — RotoBaller's weekly article, under a permission letter (QB, RB, WR, TE)
    fleaflicker.py    — The owner's own Fleaflicker league through its documented API, under a permission letter
  steps/
    __init__.py       — STEP_ORDER, DEFAULT_STEPS (every step but publish), resolve_steps
    league.py         — Sleeper league data and the NFL schedule → league.db
    scrape.py         — Fetch, verify and store each source's projections
    clean.py          — Canonical names, positions, teams and defenses, in place
    match.py          — Sleeper player ids → projections_with_sleeper
    stats.py          — μ and σ per player → player_week_stats
    accuracy.py       — Last week's projections, team totals and moneylines graded → prediction_accuracy, team_accuracy
    calibrate.py      — Season-to-date calibration → calibration_metrics
    lineups.py        — Best lineups with waiver fill → team_lineups, team_projections_summary, projections_rosters
    simulate.py       — Draws to Parquet → simulation_runs
    odds.py           — Weekly markets and curves → betting_odds_*, team_distribution_curves, team_matchup_margin_curves
    playoffs.py       — Rest-of-season futures priced through pipeline/markets.py → betting_odds_make_playoffs (yes and no), _last_place, _champion, standings_probability_matrix, simulation_standings
    validate.py       — Cross-table checks; writes nothing
    publish.py        — Charts, simulation_totals and simulation_standings, then staging and swap of TABLES into PostgreSQL
  model/
    fit.py            — Fits a parameter version from a past season's projections and actual points: fit_and_write
    evaluate.py       — Scores parameters on held-out weeks (PIT coverage, team [p10, p90], moneyline Brier) beside v1
    sampling.py       — Joint sampling of starters: lognormal above a floor, a dud mixture, a same-team Gaussian copula
    sigma.py          — A player's standard deviation under the parameters' sigma formula
    params.py         — load_params by version
    params/           — v1.json (frozen baseline) and v2 to v2.3 (fitted on 2025 weeks 10-16; v2.3 is the default)
```
