# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

TNCasino — fantasy football analytics & fake-money betting platform. Flask web app + Jupyter notebook data pipeline. Scrapes projections from multiple sources, runs Monte Carlo simulations, and generates betting odds.

**Auth**: Google OAuth via flask-dance (`app/auth.py`). Admin access controlled by `ADMIN_EMAILS` env var.

## Deployment

- **Production**: https://tncasino.win — DigitalOcean Droplet (143.198.183.213), gunicorn + nginx, Cloudflare DNS/SSL
- **Deploy code**: `git push origin main && ssh root@143.198.183.213 "cd /opt/tncasino && git pull && sudo systemctl restart tncasino"`
- **Publish data**: `python -m scripts.publish` (pushes local SQLite analytics to production PostgreSQL)
- **Publish analytics charts**: `scp backend/data/images/*.png root@143.198.183.213:/var/lib/tncasino/analytics/` after each pipeline run. The Flask app reads them from `$ANALYTICS_IMAGES_DIR` (set in prod `.env`), so they live outside the git working tree and survive `git pull`.
- **Server config**: systemd service at `/etc/systemd/system/tncasino.service`, nginx at `/etc/nginx/sites-available/tncasino`
- **Production env**: `/opt/tncasino/.env` (separate from local `.env`)

## Commands

- **Run locally**: `python -m app` (Flask on 0.0.0.0:5000)
- **Scrape projections**: `python -m scripts.scrape --week 17` (runs all scrapers, validates)
- **Validate scraping**: `python -m scripts.validate_scraping --week 17` (checks data quality)
- **Install browser drivers**: `playwright install chromium` (required for FanDuel scraper)
- **Format**: `ruff format <file>` | **Lint**: `ruff check <file>`

## Data Pipeline

Ten Jupyter notebooks in `backend/notebooks/`, run sequentially. See `docs/architecture/` for how the whole system works (pipeline, modeling, data model, web app, betting, deployment, known debt); update the relevant page when behavior changes.

1. `01_league_control` — fetch Sleeper league data
2. `02_projections_control` — scrape projections (Sleeper, FanDuel, FantasyPros, ESPN, FirstDown)
3. `03_post_scraping_processing` — clean & standardize
4. `04_match_projections_to_sleeper` — link players to Sleeper IDs
5. `05_compute_player_week_stats` — calculate mean/variance per player
6. `06_team_lineup_optimizer` — generate optimal lineups
7. `07_monte_carlo_simulations` — 50K iterations, generate odds (lognormal distribution)
8. `08_database_validation` — verify data integrity
9. `09_playoff_odds` — compute playoff probabilities
10. `10_prediction_accuracy` — optional; compare projections to actual scores (no writes)

## Database Architecture

**Production**: Self-hosted PostgreSQL on the DigitalOcean droplet. All data (user/betting + analytics) lives in one database, connected via `DATABASE_URL`.

**Local pipeline**: Notebooks write to local SQLite files (`league.db`, `projections.db`, `odds.db`, `montecarlo.db`). These are **gitignored** — never committed.

**Publishing data**: After running notebooks, `python -m scripts.publish` pushes analytics tables from local SQLite to production PostgreSQL using a staging+swap strategy. Tables are renamed to avoid collisions (e.g., `users` → `sleeper_users`, `rosters` → `sleeper_rosters`).

**Flask app reads**: All routes query PostgreSQL via `db.session` (SQLAlchemy). Analytics queries use `query_analytics()` helper in `app/routes/helpers.py`.

## Key Gotchas

- **Scrapers are fragile** — they break when source sites change layout. Expect failures and be ready to debug/adapt selectors.
- **Player name matching is brittle** — injury indicators get stripped from names; mismatches cause silent data loss.
- **Monte Carlo uses lognormal** (not normal) distribution. Position baseline variances: QB=7, RB=9, WR=10, TE=8, K=4, DST=7.
- **One module of win rules** — `pipeline/markets.py` says what wins and what pushes for every market; the odds step prices through it, the Flask app settles through it, and it will re-price through it. It imports only numpy and the standard library, and `pipeline/__init__.py` stays a bare docstring so the app can import it cheaply.
- **`simulation_totals` is append-only** — the pipeline's publish step stores each published run's score matrix there before the staging-and-swap and never replaces the table; every other published table is swapped whole.
- **The betting window comes from the runs, the lock is the kill switch** — `app/windows.py` opens betting while the latest published run's `window_closes_at` is in the future and pauses it after; the next publish reopens it. The admin's `lock_time` is the hard close: the lazy lock flips `is_locked` for good, so set it at the week's last kickoff (Sunday's first game), never Thursday's.
- **Every requirement is pinned exactly** — `requirements.txt` names the version each package resolved to on 2026-09-29. An upgrade is an edit to that file, tested locally before it reaches CI; `pip install -r requirements.txt` on a machine with older packages upgrades them.
- **Cash-out exists only after a reprice** — a pending bet, single or parlay, can be removed for a full refund while its own run is still the latest; once a newer run has repriced it, `app/cashout.py` offers 95% of fair value from that run instead, and the ledger posts only the profit or loss to the week the cash-out is taken.
- **Parlays are priced and settled on the score matrix** — `app/parlays.py` prices a slip at the share of the latest run's sims in which every leg wins, with no cap and no house edge, and refuses two legs from one market, a leg that adds nothing, and a combination the sims never produce; a pushed leg drops out at settlement and the rest re-price on the placement run's matrix, which is why `simulation_totals` keeps every run. `parlay_refusals` is the app's table; both publishers leave it alone.
- **Tests**: `python -m pytest` — 1027 tests (app tests on in-memory SQLite, pipeline tests on scratch SQLite files), about 50 s. CI runs lint + tests on every push/PR.
- **`.env` required** — needs `SECRET_KEY`, `DATABASE_URL`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `ADMIN_EMAILS`. Local dev also needs `OAUTHLIB_INSECURE_TRANSPORT=1` and `OAUTHLIB_RELAX_TOKEN_SCOPE=1`. Prod sets `ANALYTICS_IMAGES_DIR=/var/lib/tncasino/analytics` so the analytics charts live outside the git working tree; local dev falls back to `backend/data/images/`.

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
  ledger.py           — The only code that moves money: open_week, place, remove, settle, push, void, cash_out (settle and push take a parlay's adjusted payout and per-leg statuses)
  markets.py          — Market keys and their quotes: parse_key, key_for_row, find_quote, price_from_odds, odds_from_probability, potential_win
  matrices.py         — A run's score matrix decoded once per worker, and the win rules on it: score_matrix, leg_outcome, joint_probability
  migrations.py       — Schema migrations (run on startup)
  models.py           — SQLAlchemy models (User, Bet, BetLeg, ParlayRefusal, WeeklyStats, BettingPeriod)
  parlays.py          — A slip of 2 to 4 picks priced at the joint chance of the latest run: quote, joint_price, ParlayRefusal
  settlement.py       — Outcomes of a week's keyed bets from the published scores: team_scores, outcomes_for_week, outcome_for
  windows.py          — Whether a week is open for betting: Window, betting_window
  routes/
    helpers.py        — Shared helpers: query_analytics(), get_current_week(), check_betting_period_lock(period), admin_required()
    pages.py          — Public pages: /, /about, /analytics, static files
    account.py        — User account: /account, /account/update-profile
    odds.py           — Odds API: /api/matchups, /api/team_performance, etc. (12 routes)
    betting.py        — Betting: /betting, /leaderboard, /api/place_bet, /api/betting_window, /api/cash_out, /api/parlay_quote, etc. (9 routes)
    admin.py          — Admin: /admin, /admin/pipeline, /api/admin/* (12 routes)

scripts/              — Standalone CLI tools (invoked as `python -m scripts.<name>`)
  publish.py          — Push local SQLite analytics data to production PostgreSQL
  scrape.py           — Orchestrate scrapers: per-source isolation, validation, structured output
  validate_scraping.py — Check projection data quality (sources, positions, duplicates)
```
