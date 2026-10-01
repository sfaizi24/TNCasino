# 08 – Constraints & Debt

This page lists facts a refactor has to work around, grouped by area. Nothing here is a plan. Each item says what is true today and where to find it. Severity: **H** = can produce wrong money or data, **M** = slows change or breaks easily, **L** = cleanup.

## Open issues

Problems and follow-ups found while writing these docs (2026-09-26). Check them off and link the fixing commit when they're resolved.

- [x] **Client-supplied odds.** `highest_scorer`, `lowest_scorer`, `first_seed`, and `ammad_playoff` bets stored the `odds` string sent by the browser without checking it against the odds tables, so a crafted request could set any payout. Fixed in `2ec2042`: every bet is priced from the published row its market key and selection find, and the request supplies only the key, the selection, the line, the run id and the amount. `app/markets.py`, `app/routes/betting.py`
- [x] **Duplicate odds rows after a rerun of the retired notebooks' simulation.** The five `betting_odds_*` tables appended by `run_id` and the app didn't filter by it. Duplicates appeared on the site, and since moneyline/O/U bets selected by row position (`matchup_idx`, `team_idx`), they could shift which matchup a bet landed on. Negative indexes and unknown `choice` values also weren't rejected. Fixed in `2ec2042`: a bet names its market by key (season, week, roster ids) instead of a row position, a malformed key or a selection outside its market is refused, and each bet records the `run_id` it was priced at and is refused when the page showed another run. Keeping a second run off the site is the pipeline's job: its `publish` step uploads only each week's latest run ([04](04-data-model.md#oddsdb--prices-and-curves)). `app/markets.py`, `app/routes/betting.py`
- [x] **Balance race.** Balance updates were read-modify-write on the ORM object with no row lock, so concurrent place/remove/settle requests could overwrite each other. Fixed in `13c1cba`: each event is one transaction that opens with a conditional guard and changes money by SQL arithmetic. `app/ledger.py`
- [ ] **Decide the simulation count on evidence.** Every run draws 50,000 sims (`n_sims` in `pipeline/settings.py`); the playoffs step uses its first 20,000. The owner is open to fewer (2026-09-29) if prices hold. What the count buys: a chance p from n sims wobbles by about sqrt(p(1−p)/n), so a 50% moneyline is known to ±0.2 points at 50,000 and ±0.5 at 10,000, and a 3% parlay joint (about 1,500 winning sims) to ±0.08 points; with no cap (decision 2) the rarest priced combinations rest on the fewest sims, so they lose precision first. What it costs: `simulate` runtime, a 2.1 MB compressed matrix per run in `simulation_totals` (about 105 MB a season), and 4.8 MB per decoded run in each worker's cache. The analysis to run before changing it: reprice one week's markets and a sample of 2- and 3-leg parlays at 50,000, 20,000 and 10,000 sims across several seeds, and compare the spread of prices between seeds to the smallest move the page shows (one American-odds point); pick the smallest count whose seed-to-seed spread stays under that for every offered single and for parlays down to the joint chance the owner still wants priced. Cash-out offers inherit the same precision, so the 95% margin (about $5 on a $100 stake) should stay several times the price noise. | `pipeline/settings.py`, `pipeline/steps/simulate.py`
- [ ] **No loading state between pressing a place button and the reply.** `betting.js` updates the balance optimistically and shows a toast when the reply lands, but the Place Bet, Place parlay and Cash out buttons stay enabled and unchanged while the request is out (a second click sends a second request, which the ledger's guards make harmless but the bettor cannot tell). The owner asked (2026-09-29) for a loading UX: disable the pressed button and show progress until `success` or `error` arrives, on the card's stake row, the slip and the chip. | `frontend/static/js/betting.js`, `betting.css`
- [x] **The old Sleeper scraper used the legacy host.** `backend/scrapers/scraper_sleeper.py:93` called `api.sleeper.app/v1/projections/nfl/regular/{season}/{week}`, while the pipeline's source, `pipeline/sources/sleeper.py`, reads `api.sleeper.com/projections/nfl/{season}/{week}?season_type=regular&position[]=…` (checked 2026-09-29). Resolved in the WP9 cutover (6b7bd89), which deleted the scraper with the rest of `backend/`.
- [ ] **Add more projection sources.** FFToday was added in 2026-09 (WP8c) as the sixth source, RotoBaller (WP8d) as the seventh and Fleaflicker (WP8e) as the eighth; the other two candidates from 2026-09-26 are settled, and the next HTML source copies `pipeline/sources/firstdown.py` or `fftoday.py` (a `ProjectionSource` whose `fetch` calls a pure `parse` and requests through `base.get`; FantasyPros is gone), and a JSON source copies `espn.py` or `fleaflicker.py`.
  - FFToday: `LeagueID=107644`, FFToday's PPR preset (the `LeagueID=1` listed here before is Standard scoring); QB, RB, WR and TE only, since it publishes no field-goal distances for K and no DEF.
  - RotoBaller: added in 2026-09 (WP8d). It is read under the owner's letter of 2026-09-28 (non-commercial, no raw redistribution, attribution on the about page, revocable). The week's article is found through the news sitemap, which covers about 48 hours, so a week posted after the Wednesday run is dropped for that week. Its fixtures are synthetic (the site's markup with made-up numbers) because the repository is public and the letter forbids publishing the data; the same rule applies to every source read under a letter.
  - Fleaflicker: added in 2026-09 (WP8e) as the eighth source. It reads the owner's own league through the documented API under Fleaflicker's letter of 2026-09-28 (non-commercial, no raw or per-player exposure, documented endpoints and rate limits, revocable). Points arrive in the league's scoring, so the league's rules are checked against the pinned set on every run. It serves no future weeks. Its fixtures are synthetic because the repository is public.
  - CBS Sports: out. Its terms of use (section 10, Acceptable Use) bar spidering, scraping and data mining.
  - FantasySharks: already a source. Its scraper sends `scoring=2`, and the week is a `Segment` id offset per season (2026: week N = 882 + N).

## Cross-cutting

| | Item | Where |
|---|---|---|
| M | **Three team identifiers.** Tables key teams by `roster_id`/`team_id`, by `owner` (Sleeper display name), or by `team_name`. Joins translate between them in several places. | [04](04-data-model.md), `app/routes/odds.py`, `pipeline/steps/` |
| M | **No shared "current week".** The site uses the highest unsettled `BettingPeriod`; the pipeline takes `--week` from the operator, and nothing checks that the two agree. | `app/routes/helpers.py:57`, `pipeline/__main__.py` |
| M | **Single league baked in.** Owner-name map for 12 specific people, fallback week 10. | `helpers.py:14`, `helpers.py:57` |
| M | **Implicit offline→online contract.** Column names the app reads are declared only in the pipeline steps' DDL; the `publish` step copies whatever those steps produced, and `tests/conftest.py` re-declares the schema by hand. `simulation_totals` is the first published table with a declared schema; its encoding lives in `pipeline/markets.py`, which the app already imports for the win rules; the encoding side waits for parlays and cash-out. | `pipeline/steps/publish.py`, `tests/conftest.py` |

## Pipeline

| | Item | Where |
|---|---|---|
| M | Name matching is heuristic with one hardcoded override; an unmatched projection is left out of μ/σ, and only the step's summary (match rate, the ten highest unmatched) shows it. | `pipeline/steps/match.py` |
| M | FirstDown depends on the snapshot embedded in its rankings page and can't request a specific week. | `pipeline/sources/firstdown.py` |
| L | `simulation_runs` rows recorded before `n_locked`, `window_closes_at` and `standings_through_week` existed keep the column defaults (0, NULL, 0), so a standings week of 0 can mean an old run as well as a genuine week-1 run. | `pipeline/steps/simulate.py` |

## Modeling

| | Item | Where |
|---|---|---|
| L | Simulation results depend on the order starters are drawn from one seeded RNG: the same lineups and seed reproduce the same totals, and reordering the starters changes them. | `pipeline/model/sampling.py` |

## Web app

| | Item | Where |
|---|---|---|
| M | CSRF is off by default; JSON `POST`/`DELETE` endpoints (including admin) are unprotected apart from SameSite=Lax. | `app/extensions.py`, `app/__init__.py` |
| M | Weekly bets placed by market key settle from the published scores since `c735899` (admin page `cf1db58`), after the admin previews and confirms them. `sleeper_matchups` has no fetch time, so nothing stops a settle on a mid-week publish's partial scores, and whether Sleeper's stat corrections move scores after Tuesday's fetch is an untested hypothesis; a settled bet is not revisited when a correction lands ([06](06-betting-lifecycle.md#settlement)). The champion, futures parlays waiting on a champion leg, and bets placed before market keys still settle by hand; the standings futures, singles and parlays, settle from the last regular-season week's preview. All-futures parlays have Win and Loss buttons on the Pending Bets card; weekly parlays still settle only from the preview. `settle_week` still settles no bets. | `app/settlement.py`, `app/routes/admin.py` |
| M | Analytics tables have no ORM models or schema checks; errors are caught and returned as `[]` with 200. | `odds.py` |
| M | Schema changes are ad-hoc `ALTER`s run at startup; failures are logged and ignored. | `app/migrations.py` |
| L | No ledger table: balances change in place, so money history can only be reconstructed from `bets`. | `app/ledger.py` |
| L | Logging is mostly `print()` + `traceback.print_exc()`; `betting.py`, `account.py`, the three futures endpoints and the three settlement endpoints use `logging`. | `admin.py`, `odds.py`, `helpers.py:65` |
| L | `/analytics` picks its week from PNG filenames even though the page no longer shows PNGs. | `pages.py:31` |
| L | The admin page has no week switch. Its settlement and pending-bets cards follow the current week, which moves on once the next week's period exists or this one is marked settled; bets still pending from an earlier week can then be settled only by calling the admin API with that week. | `frontend/static/js/admin.js` |
| L | Nothing reads `parlay_refusals` yet. The owner counts its rows by `rule` after weeks 5 and 6 to decide whether scorer legs stay in parlays ([design §1.4](../design/odds-models-2026.md#14-which-legs-belong)); the page shows no history of refusals. | `app/routes/betting.py` |
| L | The first publish after B10b restamps the current week's futures rows with the simulation's run id instead of the pipeline run's. Futures bets placed before it will look repriced once: removal ends for them and cash-out is the way out. Later weeks are unaffected. | `pipeline/steps/playoffs.py` |
| L | `frontend/static/js/analytics.js` keeps a hard-coded `PLAYOFF_CUTOFF = 8` fallback for the standings chart, while `/api/league_overview` now reads the playoff line from `sleeper_leagues`; the fallback only shows before that table is published. | `frontend/static/js/analytics.js` |
| M | The app reads `sleeper_leagues` for the playoff line and the regular season's length: `/api/league_overview` fails and the settlement preview refuses (`League … has no published settings`) until the first publish after B10b creates the table. Deploy B10b after that publish, or publish before restarting. | `app/settlement.py`, `app/routes/odds.py` |
| L | Spreads are priced per request from the score matrix, not from a published table: `/api/spreads` runs the win rule 82 times per matchup (41 lines, two sides) over the run's sims on every page load, and each spread quote at placement runs it once more. About 25 million comparisons a page for six matchups at 50,000 sims, a few tens of milliseconds; nothing is cached but the decoded matrix. | `app/routes/odds.py`, `app/markets.py` |
| L | Every worker keeps up to four decoded score matrices (`lru_cache(maxsize=4)` in `app/matrices.py`, about 4.8 MB each in float64) for cash-out, parlay quotes and settlement re-pricing. A week with more runs than that, or a worker asked about several weeks at once, re-reads and decodes on each miss; nothing evicts by age, and a matrix stored after a worker saw the run missing is found on the next request because a miss is not cached. | `app/matrices.py` |
| L | The 5% cash-out margin is to be revisited after weeks 5 and 6 ([design §2.2](../design/odds-models-2026.md#22-the-house-margin)). There is no log table: the record is `bets.cash_out_amount`, `cash_out_run_id` and `cashed_out_at`, from which the chance at cash-out can be recomputed from `simulation_totals` and the outcome the bet would have had settled from the scores. | `app/cashout.py`, `app/ledger.py` |
| L | `GET /api/betting_window` is a read that writes: it runs the lazy lock, so an anonymous page load after `lock_time` flips `is_locked`. Harmless today, since the next place or remove would have flipped it, but a GET with a side effect. | `app/windows.py`, `app/routes/betting.py` |
| L | The betting page cannot tell a locked week from a settled one or from a run with no window: the endpoint reports `closed` without the lock time, so the banner reads `Betting is closed for week N` for all three. | `frontend/static/js/betting.js` |
| L | `simulation_runs.created_at` and `window_closes_at` are read as ISO 8601 text. If a publish ever gives PostgreSQL a timestamp column instead of TEXT, `datetime.fromisoformat` receives a `datetime` and raises; check the column types after the first week-4 publish. | `app/windows.py`, `pipeline/steps/publish.py` |

## Data & publishing

| | Item | Where |
|---|---|---|
| M | The `publish` step replaces whole tables (the season's rows, all weeks) every time; the analytics tables get pandas-inferred types and no keys or indexes. `simulation_totals` is the exception: it has a declared schema and is appended to, never swapped ([04](04-data-model.md#publishing-map)). | `pipeline/steps/publish.py` |
| L | `simulation_totals` grows by about 2.1 MB per published run (about 105 MB a season) and is never pruned, by design: settlement and cash-out re-price a bet at the run it was placed at. | `pipeline/steps/publish.py` |
| M | The Parquet draws under `backend/data/sims/` grow by one file per run (600k rows) and are never pruned. | `pipeline/steps/simulate.py` |

## Ops

| | Item | Where |
|---|---|---|
| M | Single droplet for app and database; backup strategy is not documented. | [07](07-deployment-and-ops.md) |
| M | Deploys are a manual `ssh … git pull` with no CI gate or rollback step; code and data (with its charts) are released independently. | `CLAUDE.md` |
| M | The production venv is a hand-installed subset on Python 3.12 (31 packages, `gunicorn` among them, none of it from `requirements.txt`), and it has no `numpy`. Since B7, `app/settlement.py` (and since B9, `app/cashout.py`) imports `pipeline/markets.py`, which imports numpy, so the next deploy must install numpy (or the requirements file) before the restart or gunicorn fails at import. The requirements file also carries the pipeline's scraping and charting stack, which the droplet does not need. | `requirements.txt`, `/opt/tncasino/venv` |
| L | Every requirement is pinned exactly (PR 7, 2026-09-29), so security and bug-fix releases arrive only through a deliberate edit to `requirements.txt`. | `requirements.txt` |
| L | No health check, error tracking, or alerting. | — |
| L | FantasySharks is read a page a minute (its robots.txt asks for a 60 s crawl delay), four minutes a week for its five position pages, so the playoffs step's rest-of-season projection spends about 40 minutes waiting on it. Its future weeks are real (bye teams drop out and RB, WR and TE agree with Sleeper at r 0.93 to 0.96), so it stays; `--sources` narrows the step when time matters. | `pipeline/sources/fantasysharks.py`, `pipeline/steps/playoffs.py` |
| L | No `.env.example`. | — |

## Test gaps

Real OAuth, `/account/update-profile` + CSRF, `pages.py`, JavaScript, and Postgres-specific behavior are untested: the app's market queries and the publish step's `simulation_totals` statements have only ever run on SQLite. See [07](07-deployment-and-ops.md#tests).
