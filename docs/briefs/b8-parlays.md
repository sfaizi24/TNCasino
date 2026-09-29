# TNCasino B8: parlays

Written 2026-09-29 by the orchestrator session, after B9 (merge `52aebf2`).

A self-contained brief for engineers working from a clone of `sfaizi24/TNCasino`. The package is four parts with fixed interfaces: parts 1 and 2 are built at the same time by different engineers, then parts 3a and 3b at the same time. Each part says which files it owns. This file is `docs/briefs/b8-parlays.md` on the branch `b8-parlays`; it is the brief even where a design page says otherwise.

## 1. Where you are

TNCasino is a small Flask app for a 12-team fantasy football league: Google login, a betting page where owners stake fake money on the week's markets, an account page, a leaderboard and an admin page that settles bets. Odds come from tables an offline pipeline publishes (`pipeline/`, not yours). Production runs gunicorn against PostgreSQL; the test suite runs on in-memory SQLite.

- Work on the branch `b8-parlays`, already cut from `main` at `d164e4c` and carrying two orchestrator commits: this brief and `app/matrices.py`. The orchestrator commits, pushes and opens the pull request; **do not commit, push, stash, checkout or touch git state**. Leave your changes in the working tree and report what you changed.
- The virtual environment `/home/user/venv-pip-pinned` has every requirement on Python 3.13. Tests: `/home/user/venv-pip-pinned/bin/python -m pytest -q -p no:cacheprovider` (about 30 s; 895 pass and 1 is skipped on the branch now). Lint: `/home/user/venv-pip-pinned/bin/ruff check .` and `/home/user/venv-pip-pinned/bin/ruff format <files you touched>`. Every test on the branch stays green.
- There is no `.env` and tests never need one. Never set `DATABASE_URL`. Never run `python -m scripts.publish` or `python -m pipeline`. Production is not reachable and must not be.
- Scratch scripts go under `/tmp/claude-0/…/scratchpad/`, never in the repository.
- Another engineer is editing other files of this branch at the same time. Stay inside your part's ownership list. A change you need elsewhere goes in your report, precisely, not in the code.

Rules that bind every engineer on this repository:

- Keep every JSON response shape the browser relies on; add fields, never rename or remove them.
- Money is a float in the schema and the API. Round money to cents with `round(x, 2)`.
- Quality bar (`CLAUDE.md`): this is a portfolio project, and the code must read as if a careful engineer wrote it by hand. Small functions, clear names, straightforward control flow, no over-commenting, no docstrings on obvious functions, no defensive handling of impossible cases, no single-use helper abstractions, no `utils` modules. A function you rewrite ends shorter and plainer than you found it.
- Logging is `logging`, as `betting.py` and `ledger.py` use.
- Every SQL statement runs on PostgreSQL and SQLite.

## 2. How the app is wired

Read these first, in this order:

1. `CLAUDE.md`: the quality bar and the map of `app/`.
2. `docs/architecture/06-betting-lifecycle.md` (all of it), `05-web-app.md` (Bet endpoints, Page to API map), `04-data-model.md` (the app tables, `simulation_runs`, `simulation_totals`).
3. `docs/design/odds-models-2026.md` section 1 (the parlay design), section 5 (vig and clamps) and the decisions at the end of section 10, especially decision 2 ("No cap. No house edge. Trust the model": every price is the raw share of sims, nothing is refused at ±5000, a combination that wins in no sim has no price; rules 1 and 2 of §1.3 stand) and decision 6 (scorer legs stay; B8 logs refusals).
4. The code your part touches (section 5), and always `app/matrices.py`, `app/markets.py`, `app/cashout.py`, `app/ledger.py`, `app/settlement.py`, `app/windows.py`, `app/routes/betting.py`, `pipeline/markets.py`, `tests/conftest.py`, `tests/test_cashout.py`, `tests/test_matrices.py`.

Facts you would otherwise have to discover:

- `app/matrices.py` (new on this branch): `score_matrix(run_id)` returns a `Matrix(scores, columns)` (float64 `(n_sims, n_teams)`, roster id → column), cached per worker with `lru_cache`; it raises `MissingMatrix` when the run has no `simulation_totals` row. `leg_outcome(market, selection, line, matrix)` applies the market's win rule from `pipeline/markets.py` and returns an `Outcome(won, pushed)` of per-sim booleans; a roster the run did not simulate raises `KeyError`. `joint_probability(outcomes)` is the share of sims in which every outcome won. The tests clear the cache before each test (`fresh_matrix_cache` in `conftest.py`).
- `app/markets.py`: `parse_key`, `find_quote(market, selection) -> Quote(run_id, odds, probability, line)` (raises `MarketError`), `price_from_odds`, `potential_win(amount, price)`, and new on this branch `odds_from_probability(p) -> str` (fair American odds text, `"-150"`, `"+162"`, rounded as the pipeline rounds; valid for `0 < p < 1` only).
- `app/windows.py`: `betting_window(week)` gives `Window(week, state, closes_at, run_id, ...)`; `run_id` is the week's latest published run, the one every offered price must come from. Betting needs `state == "open"`.
- `app/ledger.py` is the only code that moves money. `place(bet)` takes the stake and adds the bet with its legs; `settle(bet, won)`, `push(bet)`, `void(bet)`, `remove(bet)`, `cash_out(...)` close it. Each opens with a guard `UPDATE … WHERE status = 'pending'`; nothing commits. `_close` sets every leg to the bet's status.
- `app/settlement.py`: `outcome_for(bet, scores) -> BetOutcome(bet, outcome, reason)` judges one leg against the week's published scores through the same win rules, on a one-row score matrix. `admin.py`'s preview shows the outcomes and `settle_outcomes` closes each bet whose outcome is still the one shown, through the ledger, one transaction per bet.
- `app/cashout.py` refuses a bet with more than one leg (`No offer for this bet`). That stays: parlays have no cash-out in this package.
- The betting page (`frontend/static/js/betting.js`) keeps per-card state: a pick (`state.picks[key]`), a stake, and renders a stake section with quick stakes and Place Bet under a picked side. `payloadForKey` builds `{market, selection, line, run_id, amount}` from the card's row. Active bets render as chips in groups by `bet_type` (`ACTIVE_GROUPS`) and mark the card side whose `market` and `selection` match (`sideForBet`).
- `tests/conftest.py` seeds week 10 of 2026 under `RUN_ID` with rosters 1 and 2: one moneyline (`2026-w10-moneyline-1v2`, 1 at -150 / 2 at +130), team totals for both (1 at 110.5, 2 at 95.0), highest and lowest scorer rows for both, futures rows. `SEEDED_TOTALS` is the run's 20-sim matrix: roster 1 beats roster 2 in 11 sims, ties once, loses 8; against 110.5 it is over 9 times, on the line once, under 10 times; roster 1 wins and is over 110.5 in 7 sims. `tests/test_cashout.py` shows how a test inserts a newer run with its own matrix (`_add_newer_run`).

## 3. Goal

A bettor can combine 2 to 4 same-week picks into one bet that wins only if every pick wins, priced at the joint chance: the share of the latest run's simulations in which every leg wins, at fair odds. Two combinations are refused: two legs from one market, and a leg that adds nothing. Every refusal is logged so the owner can see how often the rules bite. A parlay settles all or nothing; a pushed leg drops out and the rest are re-priced at the run the parlay was placed on.

The driving risks: the page (a slip that gathers legs across cards) and a wrong joint price paying wrong money. The second: settlement with a pushed leg, which needs the placement run's matrix.

## 4. Decisions, taken by the orchestrator 2026-09-29

These are settled; build to them and note in your report anything they get wrong.

1. **Legs.** The four weekly markets the app offers: `moneyline`, `team_total`, `highest_scorer`, `lowest_scorer`, all for the current week. Futures legs are refused (`Futures cannot be parlayed`). Matchup totals, spreads and head to head are package B10 and not offered yet. 2 to 4 legs.
2. **Pricing.** The run is the current week's `betting_window(week).run_id`. Every leg's `find_quote` must carry that run id and, for a team total, the line the page showed at two decimals; otherwise `Odds have changed` (the route adds the window's `run_id`). The joint chance is `matrices.joint_probability` over `leg_outcome` of every leg on `score_matrix(run_id)`. The price is `odds_from_probability(joint)`; `price_from_odds` of that text is the integer price; `potential_win(amount, price)` the payout. No clamp, no cap, no house edge (decision 2). A joint chance of exactly 0 or 1 is not offered.
3. **Refusals, in order.** (a) not 2 to 4 legs; (b) a leg whose key fails to parse, names another week, or is a futures market; (c) two legs with the same market key (`Two legs from one market`: both sides of a matchup, a team's over and under, two highest-scorer picks); (d) a leg whose quote is at another run or line (`Odds have changed`), or whose price is null (`Not offered`); (e) the run's matrix is not stored (`Not offered`, rule `no_price`); (f) the joint chance is 0 or 1 (`Not offered: the simulations cannot price this parlay`, rule `impossible`); (g) a leg that adds nothing: removing it leaves the count of winning sims unchanged, tested exactly (`A leg adds nothing to this parlay`). A refusal names the offending legs by market key so the slip can mark them.
4. **The refusal log.** A new app table `parlay_refusals` (`ParlayRefusal` in `models.py`: `id`, `user_id`, `week`, `run_id`, `legs` (Text, the JSON list of `{market, selection, line}` as requested), `rule` (Text: `same_market`, `redundant`, `impossible`), `created_at`). The quote endpoint writes a row for refusals whose `rule` is one of those three, which are (c), (f) and (g), not for malformed input or moved odds. `db.create_all()` creates a new table, so no migration is needed. Nothing reads it yet; it is for the owner's SQL after weeks 5 and 6 (design §1.4).
5. **The bet record.** `bet_type = "parlay"`; `description` is each leg's single description (as `_describe` in `betting.py` writes it, odds included) joined with ` + `; `odds` is the joint odds text; `price` the integer; `probability` the joint chance; `run_id` the run; `potential_win` from the joint price; `week` the current week. Each `bet_legs` row carries its own single `price` and `probability` from its quote, and its `line`.
6. **Settlement (design §1.6).** A parlay is undecided until every leg is decided. Any lost leg: lost. All legs won: won. Legs that pushed drop out: with two or more legs left, the parlay is re-priced at the joint chance of the remaining legs on the placement run's matrix (`score_matrix(bet.run_id)`), so it pays `potential_win(amount, price_from_odds(odds_from_probability(joint)))` if the rest won; with one leg left it pays that leg's stored `price`; with none left it is a push. If the placement run's matrix is missing, the parlay is undecided with the reason `placement run's matrix not stored: settle by hand`. The ledger writes the adjusted `potential_win` onto the bet, and each leg takes its own outcome (`won`, `lost`, `push`), not the bet's.
7. **Removal.** A parlay is removable under the same rule as a single, with every leg's market still quoting the placement run. `_run_is_latest` checks every leg.
8. **Cash-out.** None for parlays in this package; `cashout.py` already refuses them.
9. **The page.** A slip. A picked side offers "Add to parlay" beside Place Bet; the slip lists the legs, quotes them live, and places the parlay. Details in part 3b.
10. **Leaderboard and account.** A parlay is a bet like any other: it counts in `total_pnl` and `settled_pnl`; the popular-bet groups are by the four single `bet_type`s and leave parlays out; the leaderboard's best-odds queries cast `odds` to an integer, which the joint odds text satisfies. The account page lists it by its description.

## 5. Ownership and interfaces

### Part 1: pricing (`app/parlays.py`)

| Files | You may |
|---|---|
| `app/parlays.py`, `tests/test_parlays.py` | create |

Not yours: everything else, including `tests/conftest.py` (insert what you need inside your tests, as `test_cashout.py` does).

```python
@dataclass(frozen=True)
class Leg:
    market: Market
    selection: str
    line: float | None
    quote: Quote                  # the leg's own single quote at the run

@dataclass(frozen=True)
class Parlay:
    run_id: str
    legs: tuple[Leg, ...]
    probability: float            # the joint chance
    odds: str                     # odds_from_probability(probability)

    @property
    def price(self) -> int        # price_from_odds(self.odds)

class ParlayRefusal(Exception):
    """Why these legs are not offered together; the message can be shown to the bettor."""
    rule: str                     # "size" | "leg" | "same_market" | "odds_changed" | "no_price" | "impossible" | "redundant"
    legs: tuple[str, ...]         # the market keys at fault, possibly empty

def quote(requests, week, run_id) -> Parlay      # raises ParlayRefusal
def joint_price(outcomes) -> tuple[float, str]   # (probability, odds) of legs already judged; raises ParlayRefusal("no_price") at 0 or 1
```

`requests` is the list of `{"market", "selection", "line"}` dicts the browser sent (`line` may be missing or null; `selection` is a string). `week` is the current week and `run_id` the window's run. The checks run in decision 3's order with these texts: `A parlay has 2 to 4 legs` (size); the `MarketError` text, `Not this week's market`, or `Futures cannot be parlayed` (leg); `Two legs from one market` (same_market); `Odds have changed` (odds_changed; a team total's line compares at two decimals as `_quote_moved` in `betting.py` does) or `Not offered` (no_price, a null price); `Not offered` (no_price, `MissingMatrix`); `Not offered: the simulations cannot price this parlay` (impossible, joint 0 or 1); `A leg adds nothing to this parlay` (redundant). The redundancy test: for each leg, the winning-sim count of the other legs equals the count of all legs; every such leg is named. `joint_price` is shared with part 2, which re-prices the remaining legs of a parlay with a pushed leg.

Tests (`tests/test_parlays.py`) on the seeded matrix and runs you insert: a 2-leg parlay of roster 1 to win and over 110.5 priced at 7/20 (odds `+186`), with each leg's own quote; a 3-leg parlay; each refusal in order with its rule and legs; the redundancy case (a highest-scorer pick and the same team's moneyline on a matrix where every top score also wins its game: build one); the joint of 0 (roster 1 wins and roster 2 is the highest scorer on the seeded matrix, or a matrix you build) refused before redundancy; two legs whose quotes are at different runs; a team total at a moved line; futures and other-week legs; `joint_price` on judged outcomes.

### Part 2: settlement and the ledger

| Files | You may |
|---|---|
| `app/settlement.py`, `app/ledger.py`, `app/routes/admin.py` | edit |
| `tests/test_settlement_outcomes.py`, `tests/test_settlement.py`, `tests/test_ledger.py` | edit |

Not yours: `app/parlays.py` (call `parlays.joint_price` for the re-price; until part 1 lands, code against the signature above and stub it in your tests with `monkeypatch` if you must), `app/models.py`, `app/routes/betting.py`, the frontend, the docs, `tests/conftest.py`.

```python
@dataclass(frozen=True)
class BetOutcome:
    bet: Bet
    outcome: str
    reason: str
    potential_win: float | None = None       # set when pushed legs changed what the parlay pays
    leg_statuses: dict | None = None         # leg id → won | lost | push, set for parlays

def ledger.settle(bet, won, potential_win=None, leg_statuses=None) -> bool
def ledger.push(bet, leg_statuses=None) -> bool
```

`outcome_for` on a bet with two or more legs: judge each leg with the existing per-market functions (refactor so a leg is judged on its own and a single bet is the one-leg case; the single-bet reasons and outcomes stay exactly as the current tests assert). Then decision 6. The reason for a parlay reads like `2 of 3 legs decided`, `lost: Bob B 98.25 vs Alice A 120.50`, `won, 3 legs`, `won, 1 leg pushed: pays 66.67 on the rest`, `push: every leg on its line`. Re-pricing uses `matrices.score_matrix(bet.run_id)`, `matrices.leg_outcome` at each remaining leg's stored line and selection, and `parlays.joint_price`; catch `MissingMatrix` for the undecided reason in decision 6.

`ledger.settle` writes `potential_win` onto the bet in the guard `UPDATE` when given, so `result` and `payout` use it. Leg statuses: when `leg_statuses` is given, each leg takes its own; otherwise every leg takes the bet's status as today. `_close` stays short; share what you can.

`admin.py`: `_settle_as_shown` passes `result.potential_win` and `result.leg_statuses` through; `_preview_row` adds `legs` (a list of `{market, selection, line}` for every bet, one entry for a single) and leaves `market`, `selection`, `line` as they are for singles and null for a parlay; `potential_win` in the row is the bet's stored value.

Tests: `test_settlement_outcomes.py`: a parlay undecided while a leg is; lost on one lost leg even with others won; won with all legs; one pushed leg of three re-priced on the placement matrix (insert a `simulation_totals` row for the bet's run and state the expected joint); one pushed of two paying the remaining leg's stored price; all pushed is a push; missing placement matrix undecided with its reason; every single-bet test unchanged. `test_ledger.py`: `settle` with an adjusted `potential_win` pays and records it; leg statuses written per leg; the defaults unchanged. `test_settlement.py`: the preview row's `legs`, and `settle_outcomes` closing a parlay with a pushed leg at the adjusted payout.

### Part 3a: the routes, the model, the docs (after parts 1 and 2)

| Files | You may |
|---|---|
| `app/routes/betting.py`, `app/models.py` | edit |
| `tests/test_betting.py`, `tests/test_models.py` | edit |
| `docs/architecture/04-data-model.md`, `05-web-app.md`, `06-betting-lifecycle.md` | edit |

Not yours: `app/parlays.py`, `app/settlement.py`, `app/ledger.py`, `app/routes/admin.py`, the frontend (part 3b), `tests/conftest.py`, `docs/design/`, `08-constraints-and-debt.md`, `CLAUDE.md`.

- `ParlayRefusal` model per decision 4.
- `POST /api/parlay_quote`, login required, body `{"legs": [{market, selection, line}], "run_id"}`. Order: the window must be open (reuse `_refuse_window`); `parlays.quote(legs, week, window.run_id)`; on `ParlayRefusal` reply `{"success": false, "error": text, "rule": rule, "legs": [keys]}` and, for `odds_changed`, add `"run_id": window.run_id`; log rules `same_market`, `no_price` and `redundant` to `parlay_refusals` (commit). Success: `{"success": true, "run_id", "probability", "odds", "price", "legs": [{market, selection, line, odds, price, probability}]}`.
- `POST /api/place_bet` with `legs` of two or more entries takes the parlay path after the window, amount and balance checks: `parlays.quote`, then a `Bet` per decision 5 with one `BetLeg` per leg, recorded through the existing `_record_bet`. A `ParlayRefusal` refuses with its text (and `rule`, `legs`, and `run_id` for `odds_changed`). A request without `legs`, or with one entry, is a single exactly as today. The reply keeps its fields and, for a parlay, has `market` and `selection` null and `price` the joint price; add `legs` (the keys) to every reply.
- `my_bets`: every summary gains `legs` (`[{market, selection, line, price, odds}]`, one entry for a single); a parlay's `market`, `selection`, `line` are null so no card side is marked. `_run_is_latest` checks every leg (decision 7).
- Tests: a 2-leg and a 3-leg parlay placed and recorded (columns, legs, balance, stats); each refusal text from the quote and from placement, with `rule` and `legs`; `Odds have changed` carrying `run_id`; the refusal log rows written for the three logged rules and not for the others; `my_bets` for a parlay; removal of a parlay while its run is the latest and refusal once one leg's market has a newer run; the single path unchanged (`legs` with one entry behaves as a single).
- Docs: 06 gains a Parlays subsection under Markets (what a parlay is, the joint price, the refusal rules and their texts, decision 2's no-cap, the record, settlement with pushed legs and the re-price, no cash-out) and the Bet diagram's `pending → won` note that a parlay pays its adjusted `potential_win`; 05 gains the endpoint in the routes and Bet endpoints tables, the `legs` fields, the API map edge, and `parlays.py` and `matrices.py` in the structure block; 04 gains the `parlay_refusals` table and `bet_type = parlay`.

### Part 3b: the page (after parts 1 and 2; alongside 3a, against the API contract above)

| Files | You may |
|---|---|
| `frontend/static/js/betting.js`, `frontend/static/css/betting.css`, `frontend/templates/betting.html` | edit |

Not yours: anything in `app/`, `tests/`, `docs/`.

- Under a picked side, beside Place Bet, a secondary button `Add to parlay` (`data-action="parlay"`). Clicking adds `{market, selection, line, run_id, label}` to `state.slip.legs` (label from the card: the team or owner and the side, e.g. `Bob B +105`, `Alice A Over 110.50`, `Bob B highest scorer`), clears the card's pick and renders. A leg already in the slip cannot be added twice; the button reads `In parlay` and is disabled.
- The slip: a panel (`#slip`, `.tnc-slip`) fixed bottom-right on desktop, a full-width bottom sheet at phone width, hidden when empty. It lists the legs with a remove button each, a Clear button, and: with one leg, `Add another leg`; with two to four, the quote from `POST /api/parlay_quote` (debounced 250 ms after any change): `+186 · 35% chance` and a stake row (quick stakes, input, `Pays out $X` from the price, `Place parlay`), or the refusal text with the offending legs marked (`.is-flagged`) and the place button disabled. `Odds have changed` from the quote calls `reloadTab()` and re-quotes with the new `run_id`.
- Place parlay posts to `/api/place_bet` with `legs`, `run_id`, `amount`; on success set the balance, clear the slip, reload bets, render, toast `Parlay placed!`; a refusal toasts the error and re-quotes; `Odds have changed` reloads the tab first.
- Active bets: `ACTIVE_GROUPS` gains `{ label: 'PARLAY', types: ['parlay'] }`; a parlay chip's label is `3-leg parlay` and its odds the joint odds; `sideForBet` finds no card for a parlay (its `market` is null), so no card is marked or disabled by it. The placed strip on cards is for singles only.
- Keep the page working signed out (no slip, no Add to parlay: the card shows `Login to bet` as today) and at 390 px. The orchestrator runs the browser check after both 3a and 3b land; make the slip easy to drive: stable `data-action` values (`parlay`, `slip-remove`, `slip-clear`, `slip-quick`, `slip-stake`, `slip-place`) and ids.

## 6. Reports

Each part ends with a report to the orchestrator: what you built per file; what you verified by hand; tests added and the pytest total; the `ruff check` and `ruff format --check` results; deviations from this brief and why; changes needed in files you do not own, precisely; open questions. Do not commit.

The orchestrator reviews the parts together, runs the suite and the browser check, commits in logical steps, pushes `b8-parlays` and opens the pull request against `main`. The owner merges.
