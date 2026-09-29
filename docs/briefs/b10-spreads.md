# TNCasino B10a: spreads

Written 2026-09-29 by the orchestrator session, after the debt clean-up (`debt-after-b8`).

A self-contained brief for engineers working from a clone of `sfaizi24/TNCasino`. Two parts with a fixed interface between them, built at the same time by different engineers. Each part says which files it owns. This file is `docs/briefs/b10-spreads.md` on the branch `b10-spreads`; it is the brief even where a design page says otherwise.

## 1. Where you are

TNCasino is a small Flask app for a 12-team fantasy football league: Google login, a betting page where owners stake fake money on the week's markets, singles and parlays, cash-out, an account page, a leaderboard and an admin page that settles bets. Odds come from tables an offline pipeline publishes (`pipeline/`, not yours), and since B8 the app also holds each published run's full score matrix, 50,000 simulated scores per team, and prices parlays and cash-outs from it. Production runs gunicorn against PostgreSQL; the test suite runs on in-memory SQLite.

- Work on the branch `b10-spreads`, cut from `main` after the debt merge. The orchestrator commits, pushes and opens the pull request; **do not commit, push, stash, checkout or touch git state**. Leave your changes in the working tree and report what you changed.
- The virtual environment `/home/user/venv-pip-pinned` has every requirement on Python 3.13. Tests: `/home/user/venv-pip-pinned/bin/python -m pytest -q -p no:cacheprovider` (about 40 s; 1027 pass and 1 is skipped on `main`). Lint: `/home/user/venv-pip-pinned/bin/ruff check .` and `/home/user/venv-pip-pinned/bin/ruff format <files you touched>`. Every test on `main` stays green.
- There is no `.env` and tests never need one. Never set `DATABASE_URL`. Never run `python -m scripts.publish` or `python -m pipeline`. Production is not reachable and must not be.
- Scratch scripts go under `/tmp/claude-0/…/scratchpad/`, never in the repository.
- Another engineer is editing other files of this branch at the same time. Stay inside your part's ownership list. A change you need elsewhere goes in your report, precisely, not in the code.

Rules that bind every engineer on this repository:

- Keep every JSON response shape the browser relies on; add fields, never rename or remove them.
- Money is a float in the schema and the API. Lines are floats too; a spread line is a multiple of 0.5.
- Quality bar (`CLAUDE.md`): this is a portfolio project, and the code must read as if a careful engineer wrote it by hand. Small functions, clear names, straightforward control flow, no over-commenting, no docstrings on obvious functions, no defensive handling of impossible cases, no single-use helper abstractions, no `utils` modules. A function you rewrite ends shorter and plainer than you found it.
- Every SQL statement runs on PostgreSQL and SQLite. Logging is `logging`.

## 2. How the app is wired

Read these first, in this order:

1. `CLAUDE.md`: the quality bar and the map of `app/`.
2. `docs/architecture/06-betting-lifecycle.md` (Markets, Parlays, Cash-out, Settlement), `05-web-app.md` (the odds endpoints, Bet endpoints, Page to API map), `04-data-model.md` (`betting_odds_matchup_ml`, `simulation_totals`).
3. `docs/design/odds-models-2026.md` §4 (the spread rows of the ranking table and the week-4 spread table), §5, and decisions 2 and 7 at the end of §10 (no cap, no house edge; spreads alone for B10).
4. The code your part touches (section 5), and always `app/markets.py`, `app/matrices.py`, `app/windows.py`, `app/parlays.py`, `app/cashout.py`, `app/settlement.py`, `app/routes/betting.py`, `app/routes/odds.py` (`get_matchups`), `pipeline/markets.py`, `tests/conftest.py`.

Facts you would otherwise have to discover:

- A market is named by a key (`app/markets.py`): `SHAPES` says whether it is weekly and which roster-id columns its key carries; `parse_key` and `key_for_row` build and read keys; `find_quote(market, selection)` returns `Quote(run_id, odds, probability, line)` from the published odds tables, raising `MarketError("Unknown market")` or `("Unknown selection")`. Every price a bet stores comes from a `Quote`.
- `app/matrices.py`: `score_matrix(run_id)` (cached per worker; `MissingMatrix` when the run's matrix is not stored), `leg_outcome(market, selection, line, matrix) -> Outcome(won, pushed)` per sim, `joint_probability(outcomes)`. `pipeline/markets.py` has the win rules, including `spread(scores, team, opponent, line)`: the picked team's score plus the line beats the opponent's; equal pushes. `probability(outcome)` is the share of sims won.
- `app/windows.py`: `betting_window(week)` names the week's latest published run (`run_id`) and whether betting is open. It runs the lazy lock (a write), so read-only code that only needs the run must not call it.
- `app/parlays.py`: `quote(requests, week, run_id)` prices 2 to 4 legs; each leg's quote must be at the run and, for a team total, at the line the page showed (`_moved`). `app/cashout.py`: the weekly path prices every leg through `leg_outcome`. `app/settlement.py`: `_judge_leg` dispatches on the market name to a per-market function returning `(outcome, reason)`.
- `app/routes/odds.py` `get_matchups` lists the week's matchups from `betting_odds_matchup_ml` (latest season, current week) with team names from `get_team_mapping`, each row carrying its `market` key and `run_id`. The page (`betting.js`) renders one card per row per tab (`SOURCES`, `TABS`, `sidesOf`, `renderMatchupPicks`, `payloadForKey`).
- The test fixtures (`tests/conftest.py`) seed week 10 of 2026 with one matchup, rosters 1 and 2, under `RUN_ID`, and the run's 20-sim matrix `SEEDED_TOTALS` (roster 1 column 0, roster 2 column 1). Compute the margins you assert from it. Tests that need another run insert it with its matrix as `tests/test_cashout.py` and `tests/test_parlays.py` do.

## 3. Goal

A bettor can bet a matchup at a spread: a team to win by more than a line, or lose by less, at fair odds from the latest run's score matrix, at the main line or any alternate half-point line. Spreads are weekly singles, parlay legs and cash-out candidates like every other weekly market, and they settle from the published scores by the shared rule.

The driving risk: a wrong sign convention paying the wrong side. The second: the page, which needs a line picker the other cards do not have.

## 4. Decisions, taken by the orchestrator 2026-09-29

1. **Key and selection.** `spread` joins `SHAPES` as weekly with `("team1_id", "team2_id")`, so its key is `2026-w04-spread-1v4`, lower roster id first, the pairing `betting_odds_matchup_ml` holds. The selection is a roster id from the key.
2. **The line belongs to the selected team.** A leg `(selection 4, line +5.5)` wins when roster 4's score plus 5.5 beats roster 1's, and pushes when they are equal; `(selection 1, line −5.5)` is the other side of the same line. Stored on the leg as the selected team's line, signed. `win_rules.spread(scores, picked, other, line)` is the rule everywhere.
3. **Half-point lines.** Every line is a multiple of 0.5 within ±40 (the range the pipeline's margin curves cover). The main line of a matchup is the median of `team1 − team2` over the latest run's sims, rounded to the nearest 0.5 and negated for team1 (a favourite by 5.7 shows −5.5); team2's line is its negative. Alternate lines run from the main line −10 to +10 in 0.5 steps. A push is therefore rare but stays handled. (The design prices at the median to the cent; half points are the sportsbook convention and keep the picker simple.)
4. **Priced in the app, not the pipeline.** No new published table. A spread quote is computed from `score_matrix` of the week's latest run at the requested line: `probability = win_rules.probability(spread outcome)`, `odds = odds_from_probability(probability)`, or no price when the probability is 0 or 1 (owner's decision 2: no cap, no house edge). The run is the week's latest `simulation_runs` row, read without the lazy lock.
5. **`find_quote` grows a `line`.** `find_quote(market, selection, line=None)`: ignored for the markets quoted from tables; required for a spread, where it is validated (a number, a multiple of 0.5, within ±40, else `MarketError("Unknown line")`), the matchup must exist in `betting_odds_matchup_ml` for the latest season and the key's week (else `Unknown market`), the selection must be one of its two rosters (else `Unknown selection`), and the returned `Quote.line` is the requested line. A missing matrix is `MarketError("Not offered")`.
6. **Moved odds.** For a spread the page's line is the line, so "Odds have changed" for a single or a parlay leg means only that the run moved; there is no line comparison beyond the run.
7. **The record.** `bet_type = "spread"`; description `Alice A vs Bob B: Alice A -5.5 -110` (matchup names, then the picked team, its signed line at one decimal, the odds); leg `line` = the selected team's line. `potential_win` from the quote's price as for every single.
8. **Settlement.** `_judge_leg` gains `spread`: undecided while either roster has no score; reason `Alice A 110.50 -5.5 vs Bob B 105.00`; won, lost or push by the shared rule on the one-row score matrix, as the moneyline does.
9. **Parlays and cash-out.** `matrices.leg_outcome` gains a `spread` branch; parlay legs carry `line` and quote through `find_quote` with it; the same-market rule (one key per matchup) already refuses two spread legs of one matchup and a spread with its own other side. Cash-out follows through `leg_outcome`.
10. **The listing.** `GET /api/spreads`, no login, current week: one row per matchup with `market`, `run_id`, `team1_id`, `team1_name`, `team2_id`, `team2_name`, `line` (team1's main line) and `lines`: the 41 alternate lines from main −10 to +10, each `{"line", "team1_odds", "team1_prob", "team2_odds", "team2_prob"}` with `null` odds where a side has no price. Like the other odds endpoints it returns `[]` on an error, including a missing matrix. Names through `display_name_for` as `get_matchups` does.
11. **Leaderboard.** A spread is a bet like any other for money; the popular-bet groups stay the four they are (note it in the docs).

## 5. Ownership and interfaces

### Part A: pricing, settlement and the API

| Files | You may |
|---|---|
| `app/markets.py`, `app/matrices.py`, `app/windows.py`, `app/settlement.py`, `app/parlays.py`, `app/routes/betting.py`, `app/routes/odds.py` | edit |
| `tests/test_markets.py`, `tests/test_matrices.py`, `tests/test_settlement_outcomes.py`, `tests/test_parlays.py`, `tests/test_betting.py`, `tests/test_odds.py`, `tests/test_cashout.py` | edit |
| `docs/architecture/05-web-app.md`, `06-betting-lifecycle.md` | edit |

Not yours: the frontend (part B), `app/cashout.py` (it should need no change; report if it does), `app/ledger.py`, `app/routes/admin.py`, `tests/conftest.py`, `docs/design/`, `08-constraints-and-debt.md`, `CLAUDE.md`.

- `app/windows.py`: extract the latest-run read into `latest_run_id(week) -> str | None` (the `LATEST_RUN_SQL` query, no lock, no period) and have `betting_window` use it. `markets.find_quote` imports it for spreads.
- `app/markets.py`: `SHAPES["spread"]`; `find_quote(market, selection, line=None)` per decisions 4 and 5, with the spread branch in its own short function; the table markets ignore `line`. `key_for_row("spread", row)` works on a matchup row unchanged.
- `app/matrices.py`: `leg_outcome` gains `spread`.
- `app/settlement.py`: `_spread(market, leg, scores)`, dispatched from `_judge_leg`.
- `app/parlays.py`: `_leg` passes the request's `line` to `find_quote`; `_moved` compares lines only for `team_total`.
- `app/routes/betting.py`: `_place_single` passes the pick's `line` to `find_quote`; `_quote_moved` compares lines only for `team_total`; `_describe` gains the spread description; `MARKET_LABELS` needs nothing for a spread.
- `app/routes/odds.py`: `GET /api/spreads` per decision 10, next to `get_matchups`, sharing its matchup query and naming.
- Tests: `find_quote` for a spread at the main line and an alternate (state the sims from `SEEDED_TOTALS`: roster 1's margins over roster 2 are the 20 differences, so the median, the rounded main line and each side's chance are exact), the `Unknown line` cases (`None`, `3.25`, `41`, `"x"`), unknown matchup and selection, missing matrix; `leg_outcome` for both sides at a line and a push at an exact margin; settlement won, lost, push, undecided and the reason; a spread parlay leg quoted and priced, and two spread legs of one matchup refused as `same_market`; a spread single placed with its description, columns and `potential_win`, at the main and an alternate line, and `Odds have changed` once the run moves; `/api/spreads` rows and `lines`, and `[]` without a matrix; a spread cash-out offer on a newer run.
- Docs: 06's Markets table gains the `spread` row (key, selection, line, priced from the score matrix at the requested line, price and chance from the sims) and a paragraph on lines (decision 3), the refusal `Unknown line`, and the Settlement table's spread row with its reason; 05's routes table, the odds-endpoints paragraph and the API map gain `/api/spreads`; the Bet endpoints table says a spread sends `line`.

### Part B: the page

| Files | You may |
|---|---|
| `frontend/static/js/betting.js`, `frontend/static/css/betting.css`, `frontend/templates/betting.html` | edit |

Not yours: anything under `app/`, `tests/` or `docs/`. Build to the API contract in decision 10 and the payloads below; part A implements them at the same time.

- A `Spreads` tab (`sp`) after Moneyline, listing `/api/spreads`. Each card is a matchup like the moneyline card: two sides, each pick button showing the side's signed line at one decimal (`-5.5` / `+5.5`) and its odds, both from the card's current line entry. Between the names and the buttons a line picker: `−½`, the current line, `+½` (`data-action="spread-line"`, `data-delta="-0.5"` / `"0.5"`), stepping through `row.lines`; a `Main` reset (`data-action="spread-main"`) when off the main line. Stepping clears the card's pick. A side with `null` odds at a line shows `No price` as other cards do.
- State: `state.spreadLines[key]` holds the index into `row.lines` (default the main line's index). `sidesOf('sp', row)` reads the current entry: selections are the roster ids, labels the names, odds and chances from the entry, and each side's `line` (team1's line for team1, its negative for team2).
- Payloads: a single sends `{market, selection, line: <the selected side's line>, run_id, amount}`; a parlay leg carries the same `line`. `legLabel` for a spread reads `Alice A -5.5`; the chip label for a placed spread bet the same, from `bet.legs[0].line` and the side's name (via `sideForBet`, which matches on `market` and `selection`; the card's current line may differ from the bet's, and that is fine).
- Everything else as the moneyline card: lineups toggle, placed strip, stake section, Add to parlay.
- Keep the page working signed out and at 390 px (the picker must fit between the two names). Stable `data-action` values for the orchestrator's browser check: `spread-line`, `spread-main`, and the existing `pick`, `place`, `parlay`.

## 6. Reports

Each part ends with a report to the orchestrator: what you built per file; what you verified by hand (part A: a real request to `/api/spreads` and a placement through the test client, with the numbers; part B: `node --check` and how the picker behaves against a mocked reply); tests added and the pytest total; the `ruff check` and `ruff format --check` results; deviations and why; changes needed in files you do not own (CLAUDE.md lines, doc 08 rows); open questions. Do not commit.

The orchestrator reviews both parts, runs the suite and the browser check, commits in logical steps, pushes `b10-spreads` and opens the pull request against `main`. The owner merges.
