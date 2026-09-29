# TNCasino B9: cash-out

Written 2026-09-29 by the orchestrator session, after B6 (merge `31db13e`) and the exact pins (merge `2163a8c`).

A self-contained brief for engineers working from a clone of `sfaizi24/TNCasino`. The package is split into three parts with fixed interfaces, so parts 1 and 2 can be built at the same time by different engineers and part 3 joins them. Each part says which files it owns. This file is `docs/briefs/b9-cash-out.md` on the branch `b9-cash-out`; it is the brief even where a design page says otherwise.

## 1. Where you are

TNCasino is a small Flask app for a 12-team fantasy football league: Google login, a betting page where owners stake fake money on the week's markets, a leaderboard, an account page and an admin page. Odds come from tables an offline pipeline publishes (`pipeline/`, not yours). Production runs gunicorn against PostgreSQL; the test suite runs on in-memory SQLite.

- Work on the branch `b9-cash-out`, already cut from `main` at `891ad7f`. The orchestrator commits, pushes and opens the pull request; **do not commit, push, or touch other branches**. Leave your changes in the working tree and report what you changed.
- The virtual environment `/home/user/venv-pip-pinned` has every requirement installed on Python 3.13. Run tests as `/home/user/venv-pip-pinned/bin/python -m pytest -q -p no:cacheprovider` (about 30 s, 826 tests on `main`) and lint as `/home/user/venv-pip-pinned/bin/ruff check .` and `/home/user/venv-pip-pinned/bin/ruff format --check .`. Every test on `main` stays green; format every file you touch.
- There is no `.env` and tests never need one. `tests/conftest.py` pins `DATABASE_URL` to `sqlite://` before the app is imported. Never set `DATABASE_URL` to anything else. Never run `python -m scripts.publish` or `python -m pipeline`. Production is not reachable and must not be.
- Scratch scripts go under `/tmp/claude-0/…/scratchpad/`, never in the repository.
- Another engineer may be editing other files of this branch at the same time. Stay inside your part's ownership list. A change you need elsewhere goes in your report, precisely, not in the code.

Rules that bind every engineer on this repository:

- Keep every JSON response shape the browser relies on; add fields, never rename or remove them.
- Money is a float in the schema and the API; keep it so. Round an offer to cents with `round(x, 2)`.
- Quality bar (`CLAUDE.md`): this is a portfolio project, and the code must read as if a careful engineer wrote it by hand. Small functions, clear names, straightforward control flow, no clever one-liners, no over-commenting, no docstrings on obvious functions, no defensive handling of impossible cases, no single-use helper abstractions, no `utils` modules. A function you rewrite ends shorter and plainer than you found it.
- Logging is `logging`, as `betting.py` and `ledger.py` already use.
- Every SQL statement runs on PostgreSQL and SQLite.

## 2. How the app is wired

Read these first, in this order:

1. `CLAUDE.md`: the quality bar and the map of `app/`.
2. `docs/architecture/06-betting-lifecycle.md` (the betting period, the Bet state diagram, Markets, Settlement, Accounting and Concurrent requests), `05-web-app.md` (Bet endpoints, Page to API map) and `04-data-model.md` (the app tables; the published `simulation_runs` and `simulation_totals`).
3. `docs/design/odds-models-2026.md` section 2 (the cash-out design this package implements) and the decisions at the end of section 10, especially decision 3: a bet can be removed for a full refund while the run that priced it is still the latest; once a later run has moved its odds, removal gives way to cash-out at 95% of fair value from the latest run, kept below full value so holding a bet stays attractive.
4. The code your part touches (section 5), and always `app/ledger.py`, `app/markets.py`, `app/windows.py`, `app/settlement.py`, `pipeline/markets.py`, `app/routes/betting.py` and `tests/conftest.py`.

Facts you would otherwise have to discover:

- `create_app(config)` calls `db.create_all()`; outside `TESTING` it also runs `app/migrations.py`, a list of idempotent, dialect-aware `ALTER TABLE` statements. Tests skip migrations, so a new column must be in both `app/models.py` and `app/migrations.py`, following the `bets.run_id`, `price`, `probability` loop already there.
- `app/ledger.py` is the only code that moves money. Each event runs a guard `UPDATE … WHERE status = 'pending'` first and returns whether it changed a row; nothing in the ledger commits. Balances and counters change by SQL arithmetic. `_close` is how `settle`, `push` and `void` finish a bet; read it before writing anything.
- `app/windows.py`: `betting_window(week, now=None)` returns a `Window(week, state, closes_at, run_id, run_created_at, lock_time)` with `state` in `open`, `paused`, `closed`. `run_id` is the week's latest published run. Betting, and removing a bet, need `open`.
- `app/markets.py`: `parse_key(key)` gives a `Market(name, season, week, teams)`; `find_quote(market, selection)` gives the latest published `Quote(run_id, odds, probability, line)` for that selection, raising `MarketError` when the market or selection is not in the tables. Futures (`first_place`, `make_playoffs`) are quoted at the season's highest published week, so a later week's run replaces an earlier one's rows.
- `pipeline/markets.py` (imported as `win_rules` in `app/settlement.py`): the win rule of every market over a score matrix of shape `(n_sims, n_teams)`, `probability(outcome)` as the share of sims won, and `decode_totals(blob, n_sims, n_teams)` for the stored matrix. It imports only numpy.
- `simulation_totals` (published, append-only): one row per run with `run_id`, `season`, `week`, `created_at`, `n_sims`, `roster_ids` (`"1,2,3,…"`, the matrix's column order) and `totals` (the encoded matrix, BLOB in SQLite, bytea in PostgreSQL). About 2.1 MB a row. A run the site shows always has its row. Every bet stores the `run_id` it was priced at.
- Futures probabilities live only in `betting_odds_first_place.probability` and `betting_odds_make_playoffs.probability`, written by the pipeline's playoffs step from its standings simulation, not from the score matrix. Rows under 1% or over 99% are not written at all, so a quoted futures probability is never clamped; a missing row means the market is gone from that run.
- `simulation_runs.standings_through_week` is the week the run's standings were complete through (the fewest games any roster had played). At current week *N* a run whose standings are complete should read *N − 1*.
- The current week is `get_current_week()` in `app/routes/helpers.py`. A futures bet's `week` is the week it was placed in.
- JSON endpoints return `{"success": true, …}` or `{"success": false, "error": "…"}` with status 200; the browser branches on `success`. CSRF is off for JSON routes and disabled in tests.
- `tests/conftest.py` fakes the published tables (`ANALYTICS_TABLES`, `create_analytics_tables`, `seed_analytics`) for week 10 of 2026 under `RUN_ID`, with `simulation_runs` seeded and an autouse clock pinning the betting window open. `betting_period` makes week 10's period. `test_ledger.py` shows how a ledger event is tested; `test_betting.py` how a signed-in request is made.

## 3. Goal

A bettor can close a pending bet before it is decided, for 95% of its fair value from the latest run, while the week's betting window is open. Removal stays the way out while the bet's run is still the latest; cash-out is the way out once a newer run has moved the odds.

```
fair value = (stake + potential_win) × p_win      p_win from the latest run, at the bet's own line
offer      = round(0.95 × fair value, 2)
```

Profit or loss from a cash-out (`offer − stake`) posts to the week it is taken, not the bet's week, so cashing out a week-4 futures bet in week 9 does not rewrite week 4's leaderboard.

The driving risk: futures and weekly bets reach their chances by different paths, and a wrong chance pays wrong money. The second: a clamped or edge chance (0 or 1, or a market the latest run dropped) must never be priced.

## 4. Decisions, taken by the orchestrator 2026-09-29

These are settled; build to them and note in your report anything they get wrong.

1. **Two pricing paths, one rule each.** A weekly bet's `p_win` is computed from the score matrix of the week's latest run in `simulation_totals`, through the win rule its market was priced with, at the leg's own line and selection. A futures bet's `p_win` is the latest futures `Quote.probability` for the leg's market and selection. Weekly markets never read `Quote.probability`, because a team total's quoted line has moved; futures never read the matrix, because their chance is not a function of one week's scores.
2. **Which run is "the latest".** Weekly: `betting_window(bet.week).run_id`. Futures: `find_quote(...).run_id`. A cash-out needs that run to differ from `bet.run_id`; otherwise the price has not moved and the bet is removable instead.
3. **Which window.** Weekly: the bet's week. Futures: the current week (`get_current_week()`), because the latest run belongs to it. In both cases the window must be `open`.
4. **Futures standings check.** The latest futures run's `simulation_runs.standings_through_week` must be at least `current_week − 1`; otherwise the run's standings miss a week and there is no offer.
5. **Edges.** No offer when `p_win` is exactly 0 or 1 from the matrix, when a futures market or selection is missing from the latest run (`MarketError`), when the leg's market fails to parse, when the bet has no legs (a legacy bet), or when the offer rounds below one cent.
6. **Single bets only.** A bet with more than one leg (a parlay, package B8) gets no offer. Do not write joint pricing.
7. **The record.** `bets` gains `cashed_out_at` (timestamp with time zone), `cash_out_amount` (float) and `cash_out_run_id` (text). Status `cashed_out`; `result = offer − amount`; `settled_at` is set too, as every closed bet has it. Legs take status `cashed_out` and the same `settled_at`. The log the design asks for is these columns: the run, the amount and the time let the margin be reviewed against what the bet would have paid.
8. **Accounting.** `users.account_balance += offer`; `users.total_pnl += result`. On the bet's week's `weekly_stats`: `active_bets_amount −= amount`. On the cash-out week's `weekly_stats` (opened with `ledger.open_week` first): `settled_pnl += result`. When the two weeks are the same, one row takes both. `bets_placed` and `bets_won` do not change. `ending_balance` and `pnl` are recomputed on every touched row, as `_update_weekly_stats` does. The owner's rule (2026-09-29): only the profit or loss of a cash-out ever reaches a leaderboard. The returned stake never enters `settled_pnl` or `total_pnl`, so a large bet placed in week 1 and cashed out in week 2 books `offer − stake` in week 2 and nothing else; with the odds unmoved there is no offer at all (decision 2), and with the odds barely moved the 5% margin makes it a small loss. Part 2's tests assert the stake never appears in either figure.
9. **Offer shown, offer taken.** `my_bets` carries each bet's offer. The cash-out request sends the offer the page showed; the route recomputes it and refuses with `"Offer has changed"` (adding the new `offer`) when the two differ at two decimals, the way `place_bet` refuses `"Odds have changed"`.
10. **Confirm before money moves.** The page asks `confirm()` with the amount before sending, as the admin's Void does. Cancel keeps asking nothing.
11. **Leaderboard and account.** A cashed-out bet counts as placed (not removed, not void). The popular-bet counts include it with neither a win nor a loss, like a push; the best/worst-odds and biggest-win/loss queries filter on `won` and `lost` and so leave it out. The account page lists it as `Cashed out` with its signed result. The leaderboard template shows a `Cashed out` outcome where it now shows `Pending` for unknown statuses.

## 5. Ownership and interfaces

### Part 1: pricing (`app/cashout.py`)

| Files | You may |
|---|---|
| `app/cashout.py`, `tests/test_cashout.py` | create |
| `tests/conftest.py` | edit: add `simulation_totals` to `ANALYTICS_TABLES` and `create_analytics_tables`, and seed a matrix in `seed_analytics` (below) |

Not yours: everything else. In particular `app/ledger.py`, `app/models.py`, `app/routes/`, the frontend and the docs.

```python
@dataclass(frozen=True)
class Offer:
    bet_id: int
    amount: float           # the offer, rounded to cents
    fair_value: float
    probability: float      # p_win from the latest run
    run_id: str             # the run priced from


class NoOffer(Exception):
    """Why this bet has no cash-out offer now; the message can be shown to the bettor."""


def offer_for(bet, now=None) -> Offer                    # raises NoOffer
def offers_for(bets, now=None) -> dict[int, Offer]       # bet id → Offer, bets with no offer left out
```

`offers_for` prices a list of pending bets for one page load. It decodes each weekly run's matrix once, not once per bet, and looks each week's window up once. It never raises `NoOffer`; it leaves those bets out.

`offer_for` checks, in order, and each failure raises `NoOffer` with the text given:

| Check | Text |
|---|---|
| the bet is not pending, has no legs, or has more than one leg | `No offer for this bet` |
| the leg's market fails to parse | `No offer for this bet` |
| the window (decision 3) is not `open` | `Betting is paused until the odds update` when paused; `Betting is closed for week N` when closed, including under the admin's lock (do not repeat the lock text; the route for placing bets owns it) |
| the latest run is the bet's run (decision 2) | `Odds have not changed since this bet was placed; remove it instead` |
| futures: standings behind (decision 4) | `No offer until the next run: the standings are behind` |
| the chance is an edge (decision 5), the market is gone, or the offer is under a cent | `No offer: the latest run cannot price this bet` |

The matrix: `SELECT n_sims, roster_ids, totals FROM simulation_totals WHERE run_id = :run_id`; decode with `win_rules.decode_totals`; the column of a roster is its index in `roster_ids.split(",")`. A roster in the key that is not in `roster_ids`: `No offer: the latest run cannot price this bet`. The win rule per market is what `app/settlement.py` applies (`moneyline`, `team_total` with `leg.line` and `leg.selection`, `highest_scorer`, `lowest_scorer`); `p_win = win_rules.probability(outcome)`. Read `settlement.py` and share its way of naming things, but do not import from it or change it.

Tests (`tests/test_cashout.py`): the worked example of design §2.1 reproduced on a seeded matrix (a $100 moneyline at +105 whose team wins 38.2% of a newer run's sims is offered $74.38, fair value $78.29); a team total priced at the leg's line, not the quoted one; each refusal above; a futures offer from a newer futures run and its standings refusal; `offers_for` decoding one matrix for two bets of the same week (assert the query count or the decode count); a legacy bet and a two-leg bet left out.

The conftest matrix: keep it small and exact. Seed one `simulation_totals` row for `RUN_ID` with, say, 20 sims and the two seeded rosters `1,2`, encoded with `pipeline.markets.encode_totals`, and expose the plain array as a module constant so tests can state the chances they expect. Tests that need a newer run insert their own rows.

### Part 2: the money (`app/ledger.py`, the schema)

| Files | You may |
|---|---|
| `app/models.py`, `app/migrations.py`, `app/ledger.py` | edit |
| `tests/test_ledger.py`, `tests/test_models.py` | edit |

Not yours: `app/cashout.py`, `app/routes/`, the frontend, the docs, `tests/conftest.py`.

```python
def cash_out(bet, offer: float, week: int) -> bool
```

`week` is the week the profit posts to (the current week; the route passes it and has called `open_week(bet.user_id, week)` first). The guard is the bet's status: `UPDATE bets SET status = 'cashed_out', result = :result, cash_out_amount = :offer, cash_out_run_id = :run_id, cashed_out_at = :now, settled_at = :now WHERE id = :bet AND status = 'pending'`; when it changes no row, return `False` and do nothing else. Then the legs, the user's balance and `total_pnl`, and the two weekly rows, per decision 8. The signature takes `offer`; take `run_id` too if that reads better (`cash_out(bet, offer, run_id, week)`), and say so in the report so part 3 matches.

`_close` is close to what you need but posts everything to `bet.week`; extend the ledger so `cash_out` and `_close` share what they can without making `_close` harder to read. Every function stays short.

Schema: the three columns of decision 7 in `Bet`, with the `nullable=True` the other optional columns use; the same three in `migrations.py` next to the `run_id`, `price`, `probability` loop (`TIMESTAMP WITH TIME ZONE` on PostgreSQL, `TIMESTAMP` on SQLite; `DOUBLE PRECISION`/`REAL`; `VARCHAR`). Update the status comment on `BetLeg.status`.

Tests (`tests/test_ledger.py`): a cash-out at a profit and at a loss, checking every number in `_money` for both the bet's week and the cash-out week when they differ and when they are the same; a second cash-out of the same bet changes nothing; a cash-out after a settle changes nothing; the legs' status and `settled_at`; the race test style of `test_balance_race.py` is not required here.

### Part 3: the routes, the page, the docs (after parts 1 and 2 are in the tree)

| Files | You may |
|---|---|
| `app/routes/betting.py` | edit |
| `frontend/static/js/betting.js`, `frontend/static/css/betting.css` | edit |
| `frontend/templates/account.html`, `frontend/templates/leaderboard.html` | edit |
| `tests/test_betting.py`, `tests/test_leaderboard.py` | edit |
| `docs/architecture/04-data-model.md` (the `bets` and `bet_legs` rows only), `05-web-app.md`, `06-betting-lifecycle.md` | edit |

Not yours: `app/cashout.py`, `app/ledger.py`, `app/models.py`, `app/migrations.py`, `app/routes/admin.py`, `app/routes/helpers.py`, `tests/conftest.py`, `docs/design/`, `08-constraints-and-debt.md`, `CLAUDE.md`.

- `GET /api/my_bets`: each summary gains `cash_out_offer` (float or null) from `cashout.offers_for(bets)`. Nothing else in the summary changes. A bet is never both `removable` and offered: `removable` needs the run unchanged, an offer needs it changed.
- `POST /api/cash_out/<int:bet_id>`, login required, body `{"offer": 74.38}`. Order: bet not found or not the user's or not pending → `Bet not found`; `offer_for(bet)` → its `NoOffer` text; the offer differs from the body's at two decimals → `Offer has changed` with `offer` (the new one) added to the reply; `ledger.open_week(user, current_week)`; `ledger.cash_out(...)` false → `Bet not found` after rollback; commit; reply `{"success": true, "new_balance": …, "cash_out_amount": …}`. Log the cash-out the way `_record_bet` logs a placement.
- `betting.js`: the placed strip and the active chip show a `Cash out $74.38` button when `cash_out_offer` is not null (and no cancel, since the two exclude each other). Clicking asks `confirm()` naming the bet's description and the amount, posts, and on success sets the balance, reloads bets and renders, with a toast `Cashed out for $74.38`. A refusal toasts the error and, like a refused cancel, reloads the window and the bets so the offers on the page are current. Style the button apart from Cancel; keep the chip readable at phone width.
- `account.html`: status `cashed_out` shows `Cashed out` and the signed result, coloured like a win or a loss by its sign. `leaderboard.html`: a `Cashed out` outcome where the template now falls back to `Pending`.
- Tests (`tests/test_betting.py`): the endpoint's happy path with the numbers of design §2.1 (balance, `total_pnl`, both weeks' stats, the bet's columns); each refusal text; `Offer has changed`; `my_bets` carrying `cash_out_offer` and `removable` never both true; the account page listing a cashed-out bet. `tests/test_leaderboard.py`: a cashed-out bet counts as placed in the popular-bet count and appears in neither wins nor losses.
- Browser check: run the app on a scratch SQLite file with the published tables faked from `tests/conftest.py` (the B6 scripts in this session's scratchpad show how, including the signed session cookie and launching Chromium by `executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome"`). Seed a newer run so a bet has an offer; screenshot the chip with the offer, the confirm, and the account page row; one shot at 390 px. Report the paths.
- Docs, in the voice of the existing pages: 06's Bet state diagram gains `pending → cashed_out` (while the window is open and a newer run has moved the odds; balance += offer), the paragraph after it says when removal ends and cash-out begins, Accounting gets a `Cash out` row in the events table and the guard in the Concurrent requests table; 05's Bet endpoints table gets the new endpoint and `cash_out_offer`, the Page to API map the new edge; 04's `bets` row the three columns and the `cashed_out` status.

## 6. Reports

Each part ends with a report to the orchestrator, in this order: what you built per file; what you verified by hand; tests added and the pytest total; the `ruff check` and `ruff format --check` results; deviations from this brief and why; changes needed in files you do not own, precisely; open questions. Do not commit.

The orchestrator reviews the three parts together, runs the suite, commits in logical steps, pushes `b9-cash-out` and opens the pull request against `main`. The owner merges.
