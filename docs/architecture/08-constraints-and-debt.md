# 08 – Constraints & Debt

This page lists facts a refactor has to work around, grouped by area. Nothing here is a plan. Each item says what is true today and where to find it. Severity: **H** = can produce wrong money or data, **M** = slows change or breaks easily, **L** = cleanup.

## Open issues

Problems and follow-ups found while writing these docs (2026-09-26). Check them off and link the fixing commit when they're resolved.

- [x] **Client-supplied odds.** `highest_scorer`, `lowest_scorer`, `first_seed`, and `ammad_playoff` bets stored the `odds` string sent by the browser without checking it against the odds tables, so a crafted request could set any payout. Fixed in `2ec2042`: every bet is priced from the published row its market key and selection find, and the request supplies only the key, the selection, the line, the run id and the amount. `app/markets.py`, `app/routes/betting.py`
- [x] **Duplicate odds rows after re-running notebook 07.** The five `betting_odds_*` tables appended by `run_id` and the app didn't filter by it. Duplicates appeared on the site, and since moneyline/O/U bets selected by row position (`matchup_idx`, `team_idx`), they could shift which matchup a bet landed on. Negative indexes and unknown `choice` values also weren't rejected. Fixed in `2ec2042`: a bet names its market by key (season, week, roster ids) instead of a row position, a malformed key or a selection outside its market is refused, and each bet records the `run_id` it was priced at and is refused when the page showed another run. Keeping a second run off the site is the pipeline's job: its `publish` step uploads only each week's latest run ([04](04-data-model.md#oddsdb--prices-and-curves)), while the notebooks' `scripts/publish.py` still copies every run (see Pipeline). `app/markets.py`, `app/routes/betting.py`
- [x] **Balance race.** Balance updates were read-modify-write on the ORM object with no row lock, so concurrent place/remove/settle requests could overwrite each other. Fixed in `13c1cba`: each event is one transaction that opens with a conditional guard and changes money by SQL arithmetic. `app/ledger.py`
- [ ] **Decide the simulation count on evidence.** Every run draws 50,000 sims (`n_sims` in `pipeline/settings.py`); the playoffs step uses its first 20,000. The owner is open to fewer (2026-09-29) if prices hold. What the count buys: a chance p from n sims wobbles by about sqrt(p(1−p)/n), so a 50% moneyline is known to ±0.2 points at 50,000 and ±0.5 at 10,000, and a 3% parlay joint (about 1,500 winning sims) to ±0.08 points; with no cap (decision 2) the rarest priced combinations rest on the fewest sims, so they lose precision first. What it costs: `simulate` runtime, a 2.1 MB compressed matrix per run in `simulation_totals` (about 105 MB a season), and 4.8 MB per decoded run in each worker's cache. The analysis to run before changing it: reprice one week's markets and a sample of 2- and 3-leg parlays at 50,000, 20,000 and 10,000 sims across several seeds, and compare the spread of prices between seeds to the smallest move the page shows (one American-odds point); pick the smallest count whose seed-to-seed spread stays under that for every offered single and for parlays down to the joint chance the owner still wants priced. Cash-out offers inherit the same precision, so the 95% margin (about $5 on a $100 stake) should stay several times the price noise. | `pipeline/settings.py`, `pipeline/steps/simulate.py`
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
| M | **Implicit offline→online contract.** Column names the app reads are not declared anywhere; `publish.py` copies whatever the notebooks produced, and `tests/conftest.py` re-declares the schema by hand. `simulation_totals` is the first published table with a declared schema; its encoding lives in `pipeline/markets.py`, which the app already imports for the win rules; the encoding side waits for parlays and cash-out. | `scripts/publish.py`, `tests/conftest.py` |

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
| L | `simulation_runs` rows recorded before `n_locked`, `window_closes_at` and `standings_through_week` existed keep the column defaults (0, NULL, 0), so a standings week of 0 can mean an old run as well as a genuine week-1 run. | `pipeline/steps/simulate.py` |
| L | Dead code/data: `database_users.py`, empty `projections.player_stats` and its methods, stale `betting_odds_*` copies in `projections.db`, `.ipynb_checkpoints/`, empty root `odds.db`, `instance/betting_app.db`. | as listed |
| L | `backend/` is excluded from ruff, so none of the pipeline code is linted. | `ruff.toml` |
| M | **A playoffs-only rerun changes what bettors may do.** The playoffs step stamps the futures rows with the pipeline run that ran it, not the simulation run it read. Rerunning the step alone (the retry after a late failure) gives the week's futures a new `run_id` with no `simulation_runs` row and no new information behind it. The app then treats every futures bet placed on that week's full run as repriced: removal ends and cash-out at 95% of an unchanged fair value is the only way out. Offers themselves survive, because the standings check reads the week's simulation run (`app/cashout.py`, B9). Until the step stamps its rows with the simulation run it used, rerun from `simulate`, never `playoffs` alone. | `pipeline/steps/playoffs.py`, `app/routes/betting.py` |

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
| M | Weekly bets placed by market key settle from the published scores since `c735899` (admin page `cf1db58`), after the admin previews and confirms them. `sleeper_matchups` has no fetch time, so nothing stops a settle on a mid-week publish's partial scores, and whether Sleeper's stat corrections move scores after Tuesday's fetch is an untested hypothesis; a settled bet is not revisited when a correction lands ([06](06-betting-lifecycle.md#settlement)). Futures and bets placed before market keys still settle by hand, and `settle_week` still settles no bets. | `app/settlement.py`, `app/routes/admin.py` |
| M | Analytics tables have no ORM models or schema checks; errors are caught and returned as `[]` with 200. | `odds.py` |
| M | Schema changes are ad-hoc `ALTER`s run at startup; failures are logged and ignored. | `app/migrations.py` |
| L | No ledger table: balances change in place, so money history can only be reconstructed from `bets`. | `app/ledger.py` |
| L | Logging is mostly `print()` + `traceback.print_exc()`; `betting.py`, `account.py`, the two futures endpoints and the three settlement endpoints use `logging`. | `admin.py`, `odds.py`, `helpers.py:65` |
| L | `/analytics` picks its week from PNG filenames even though the page no longer shows PNGs. | `pages.py:31` |
| L | The admin page has no week switch. Its settlement and pending-bets cards follow the current week, which moves on once the next week's period exists or this one is marked settled; bets still pending from an earlier week can then be settled only by calling the admin API with that week. | `frontend/static/js/admin.js` |
| L | The admin page scrolls sideways at phone width: at a 375 px viewport the Current Betting Periods table stretches `.admin-grid`'s `1fr` column to 390 px and the page to 404 px. The settlement and pending-bets cards sit outside the grid and fit. The B7 browser check found this and left it. | `frontend/static/css/admin.css` |
| L | Removal and cash-out decide "the latest run" from different tables: `remove_bet` and `removable` from the odds rows (`_run_is_latest`), an offer from `simulation_runs` (the window). `my_bets` shows the two as exclusive, but when `simulation_runs` is newer than the odds tables (a `simulate` that published without its `odds`) the remove endpoint would still refund a bet the page offers a cash-out on. | `app/routes/betting.py`, `app/cashout.py` |
| L | Nothing reads `parlay_refusals` yet. The owner counts its rows by `rule` after weeks 5 and 6 to decide whether scorer legs stay in parlays ([design §1.4](../design/odds-models-2026.md#14-which-legs-belong)); the page shows no history of refusals. | `app/routes/betting.py` |
| L | The admin's by-hand Win button settles a parlay at its stored joint price and marks every leg won; it does not drop a pushed leg or re-price the rest. Only the preview-and-confirm path judges a parlay leg by leg, so parlays settle through the Settle Week card. | `app/routes/admin.py` |
| L | A parlay request whose `legs` entries are not objects reaches the routes' catch-all and is refused with the raw Python error text rather than a rule. | `app/parlays.py` |
| L | Every worker keeps up to four decoded score matrices (`lru_cache(maxsize=4)` in `app/matrices.py`, about 4.8 MB each in float64) for cash-out, parlay quotes and settlement re-pricing. A week with more runs than that, or a worker asked about several weeks at once, re-reads and decodes on each miss; nothing evicts by age, and a matrix stored after a worker saw the run missing is found on the next request because a miss is not cached. | `app/matrices.py` |
| L | The 5% cash-out margin is to be revisited after weeks 5 and 6 ([design §2.2](../design/odds-models-2026.md#22-the-house-margin)). There is no log table: the record is `bets.cash_out_amount`, `cash_out_run_id` and `cashed_out_at`, from which the chance at cash-out can be recomputed from `simulation_totals` and the outcome the bet would have had settled from the scores. | `app/cashout.py`, `app/ledger.py` |
| L | `GET /api/betting_window` is a read that writes: it runs the lazy lock, so an anonymous page load after `lock_time` flips `is_locked`. Harmless today, since the next place or remove would have flipped it, but a GET with a side effect. | `app/windows.py`, `app/routes/betting.py` |
| L | `betting_window` loads the week's `BettingPeriod` and then `check_betting_period_lock` loads it again, because the helper takes a week rather than a period. | `app/windows.py`, `app/routes/helpers.py` |
| L | The betting page cannot tell a locked week from a settled one or from a run with no window: the endpoint reports `closed` without the lock time, so the banner reads `Betting is closed for week N` for all three. | `frontend/static/js/betting.js` |
| L | `simulation_runs.created_at` and `window_closes_at` are read as ISO 8601 text. If a publish ever gives PostgreSQL a timestamp column instead of TEXT, `datetime.fromisoformat` receives a `datetime` and raises; check the column types after the first week-4 publish. | `app/windows.py`, `pipeline/steps/publish.py` |
| L | `unlock_period` sets `lock_time` a week ahead. Now that the lock is the hard close, an admin who unlocks on a Sunday has to set it back to the week's last kickoff by hand. | `app/routes/admin.py` |

## Data & publishing

| | Item | Where |
|---|---|---|
| M | Publishing replaces whole tables (all weeks) every time; the analytics tables get pandas-inferred types and no keys or indexes. The pipeline's `publish` step keeps that shape for every table but `simulation_totals`, which it appends to and never swaps ([04](04-data-model.md#publishing-map)). | `scripts/publish.py`, `pipeline/steps/publish.py` |
| L | `simulation_totals` grows by about 2.1 MB per published run (about 105 MB a season) and is never pruned, by design: settlement and cash-out re-price a bet at the run it was placed at. | `pipeline/steps/publish.py` |
| M | `montecarlo.db` (400+ MB) grows by ~600k rows per run and is never pruned. | notebook 07 |
| L | `--dry-run` still creates (then drops) staging tables in production. | `publish.py` |

## Ops

| | Item | Where |
|---|---|---|
| M | Single droplet for app and database; backup strategy is not documented. | [07](07-deployment-and-ops.md) |
| M | Deploys are a manual `ssh … git pull` with no CI gate or rollback step; code, data, and charts are released independently. | `CLAUDE.md` |
| M | The production venv is a hand-installed subset on Python 3.12 (31 packages, `gunicorn` among them, none of it from `requirements.txt`), and it has no `numpy`. Since B7, `app/settlement.py` (and since B9, `app/cashout.py`) imports `pipeline/markets.py`, which imports numpy, so the next deploy must install numpy (or the requirements file) before the restart or gunicorn fails at import. The requirements file also carries the scraping and notebook stack the droplet does not need; splitting it is WP9's. | `requirements.txt`, `/opt/tncasino/venv` |
| L | Every requirement is pinned exactly (PR 7, 2026-09-29), so security and bug-fix releases arrive only through a deliberate edit to `requirements.txt`. | `requirements.txt` |
| L | No health check, error tracking, or alerting. | — |
| L | No `.env.example`. | — |

## Test gaps

Real OAuth, `/account/update-profile` + CSRF, `pages.py`, `publish.py`, notebooks, live scrapers, JavaScript, and Postgres-specific behavior are untested: the app's market queries and the publish step's `simulation_totals` statements have only ever run on SQLite. See [07](07-deployment-and-ops.md#tests).
