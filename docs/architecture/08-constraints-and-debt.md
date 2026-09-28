# 08 – Constraints & Debt

This page lists facts a refactor has to work around, grouped by area. Nothing here is a plan. Each item says what is true today and where to find it. Severity: **H** = can produce wrong money or data, **M** = slows change or breaks easily, **L** = cleanup.

## Open issues

Problems and follow-ups found while writing these docs (2026-09-26). Check them off and link the fixing commit when they're resolved.

- [x] **Client-supplied odds.** `highest_scorer`, `lowest_scorer`, `first_seed`, and `ammad_playoff` bets stored the `odds` string sent by the browser without checking it against the odds tables, so a crafted request could set any payout. Fixed in `2ec2042`: every bet is priced from the published row its market key and selection find, and the request supplies only the key, the selection, the line, the run id and the amount. `app/markets.py`, `app/routes/betting.py`
- [x] **Duplicate odds rows after re-running notebook 07.** The five `betting_odds_*` tables appended by `run_id` and the app didn't filter by it. Duplicates appeared on the site, and since moneyline/O/U bets selected by row position (`matchup_idx`, `team_idx`), they could shift which matchup a bet landed on. Negative indexes and unknown `choice` values also weren't rejected. Fixed in `2ec2042`: a bet names its market by key (season, week, roster ids) instead of a row position, a malformed key or a selection outside its market is refused, and each bet records the `run_id` it was priced at and is refused when the page showed another run. Keeping a second run off the site is the pipeline's job: its `publish` step uploads only each week's latest run ([04](04-data-model.md#oddsdb--prices-and-curves)), while the notebooks' `scripts/publish.py` still copies every run (see Pipeline). `app/markets.py`, `app/routes/betting.py`
- [x] **Balance race.** Balance updates were read-modify-write on the ORM object with no row lock, so concurrent place/remove/settle requests could overwrite each other. Fixed in `13c1cba`: each event is one transaction that opens with a conditional guard and changes money by SQL arithmetic. `app/ledger.py`
- [ ] **Sleeper scraper uses the legacy host.** `scraper_sleeper.py` calls `api.sleeper.app/v1/projections/nfl/regular/{season}/{week}`. It still returns data (9,422 entries for 2026 week 3), but the Sleeper app itself now reads `api.sleeper.com/projections/nfl/{season}/{week}?season_type=regular&position[]=…`, which returns a list instead of a dict, nests stats under `stats`, and adds `pts_ppr`/`pts_half_ppr`/`pts_std` and a `company` field (`rotowire`). Migrate before the old host goes empty. `backend/scrapers/scraper_sleeper.py:93`
- [ ] **Add more projection sources.** Verified 2026-09-26 as free, no login, server-rendered HTML tables (plain `requests` + BeautifulSoup, same shape as the FantasyPros scraper). Sleeper is RotoWire data, so these would be the first independent additions since FirstDown.
  - CBS Sports: `https://www.cbssports.com/fantasy/football/stats/{POS}/{season}/{week}/projections/ppr/`, one page per position, fantasy points plus full stat line.
  - FFToday: `https://www.fftoday.com/rankings/playerwkproj.php?Season={season}&GameWeek={week}&PosID={id}&LeagueID=1`, one page per position (`PosID` 10=QB, 20=RB, 30=WR, 40=TE, 80=K, 99=DST), `FPts` column, `LeagueID` selects the scoring system.
  - FantasySharks: `https://www.fantasysharks.com/apps/bert/forecasts/projections.php?League=-1&Position={n}&scoring=1&Segment={n}`, most detailed stat breakdown, but the week is an opaque `Segment` number that has to be mapped each season.

## Cross-cutting

| | Item | Where |
|---|---|---|
| M | **Three team identifiers.** Tables key teams by `roster_id`/`team_id`, by `owner` (Sleeper display name), or by `team_name`. Joins translate between them in several places. | [04](04-data-model.md), `app/routes/odds.py`, notebooks 06/07/09 |
| M | **Two week formats.** `"Week N"` text in `projections*` tables, integer everywhere else. | `backend/scrapers/database.py`, notebooks 04/05 |
| M | **No shared "current week".** The site uses the highest unsettled `BettingPeriod`; each notebook has its own `CURRENT_WEEK` (currently 16 in 01/05/06/07 and 14 in 08/09). | `app/routes/helpers.py:57`, notebook config cells |
| M | **Single league and season baked in.** Owner-name map for 12 specific people, `LEAGUE_ID` default, 2025 bye weeks, 8-team playoff cutoff, fallback week 10. | `helpers.py:14`, `odds.py:22`, `scraper_sleeper_league.py:573`, `helpers.py:57` |
| M | **Implicit offline→online contract.** Column names the app reads are not declared anywhere; `publish.py` copies whatever the notebooks produced, and `tests/conftest.py` re-declares the schema by hand. | `scripts/publish.py`, `tests/conftest.py` |

## Pipeline

| | Item | Where |
|---|---|---|
| H | **Re-running notebook 07 duplicates odds rows** (append by `run_id`) unless `DELETE_WEEK` is set; the app doesn't filter by `run_id`, so it lists every run and quotes a bet from whichever run's row it reads first. | notebook 07, `odds.py:33`, `odds.py:78`, `markets.py:38` |
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
| M | CSRF is off by default; JSON `POST`/`DELETE` endpoints (including admin) are unprotected apart from SameSite=Lax. | `app/extensions.py`, `app/__init__.py` |
| M | Settlement is fully manual; `settle_week` doesn't settle bets. | `admin.py:178` |
| M | Analytics tables have no ORM models or schema checks; errors are caught and returned as `[]` with 200. | `odds.py` |
| M | Schema changes are ad-hoc `ALTER`s run at startup; failures are logged and ignored. | `app/migrations.py` |
| L | No ledger table: balances change in place, so money history can only be reconstructed from `bets`. | `app/ledger.py` |
| L | Logging is mostly `print()` + `traceback.print_exc()`; `betting.py`, `account.py` and the two futures endpoints use `logging`. | `admin.py`, `odds.py`, `helpers.py:65` |
| L | `/analytics` picks its week from PNG filenames even though the page no longer shows PNGs. | `pages.py:31` |

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

Real OAuth, `/account/update-profile` + CSRF, `pages.py`, `publish.py`, notebooks, live scrapers, JavaScript, and Postgres-specific behavior are untested. See [07](07-deployment-and-ops.md#tests).
