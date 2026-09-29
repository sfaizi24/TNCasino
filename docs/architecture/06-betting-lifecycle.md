# 06 – Betting Lifecycle

All money is fake. Every user starts with **1,000**. Code: `app/routes/betting.py`, `app/routes/admin.py`, `app/routes/helpers.py`, `app/windows.py`, `app/ledger.py`, `app/markets.py`, `app/settlement.py`, `app/cashout.py`.

## Betting period (one per week)

Betting is open when the odds on the page come from a run made before the next kickoff, and nothing else. `betting_window(week)` in `app/windows.py` reads the week's `BettingPeriod`, the admin's lock and the week's latest `simulation_runs` row ([04](04-data-model.md)) into one of three states:

| State | When | Place, remove, cash out |
|---|---|---|
| `open` | the period exists and is neither settled nor locked, and now is before the latest run's `window_closes_at` | allowed |
| `paused` | the same, but the run's window has closed and no newer run has published: Thursday night until the Friday rerun, Sunday morning until the week is settled | refused, `"Betting is paused until the odds update"` |
| `closed` | no period, or the period is settled, or the admin's lock (`is_locked`, or `lock_time` passed), or the latest run has no window | refused, `"Bets are locked as of …"` under the lock, else `"Betting is closed for week 4"` |

```mermaid
stateDiagram-v2
    [*] --> Closed: no period
    Closed --> Open: admin set_betting_period(week, lock_time)<br/>and a published run with a window
    Open --> Paused: the run's window_closes_at passes<br/>(the next kickoff)
    Paused --> Open: the pipeline publishes a newer run<br/>with an open window
    Open --> Locked: lock_time passes (lazy)<br/>or the admin locks
    Paused --> Locked: lock_time passes (lazy)<br/>or the admin locks
    Locked --> Open: admin unlock_period<br/>(lock_time = now + 7 days)
    Open --> Settled: admin settle_week
    Paused --> Settled: admin settle_week
    Locked --> Settled: admin settle_week
    Settled --> [*]
```

The window comes from the runs: Wednesday's run closes at Thursday's kickoff, the Friday rerun after Thursday's game closes at Sunday's first kickoff, and a Saturday rerun the same. With no rerun published, the latest run's window has closed and betting stays paused, which is the safe default. A window closing never flips `is_locked`; the lock is the admin's backstop and kill switch.

- `lock_time` is entered in the admin form as a naive datetime and **stored as UTC**. Set it at the week's last window close, Sunday's first kickoff (week 4: `2026-10-04T13:30` UTC), never Thursday's: a Thursday lock keeps the week shut after the Friday rerun.
- The lock is lazy: `check_betting_period_lock(week)` flips `is_locked` when it sees `now ≥ lock_time`. `betting_window` is its one caller, so the lock still flips on the first `place_bet`, `remove_bet`, `cash_out`, `my_bets` or `betting_window` request after `lock_time`; the `/betting` route itself doesn't check it.
- **Current week** = highest-numbered unsettled period. Settling week *N* moves the site to the next unsettled period; if there is none, it falls back to week 10. Creating the week *N+1* period before settling *N* moves the site forward immediately.
- `settle_week` **only sets `is_settled`**. It doesn't touch bets; pending bets stay pending. The admin page shows only the current week's bets, so settle them ([Settlement](#settlement)) before creating the next week's period or marking this one settled.

## Bet

```mermaid
stateDiagram-v2
    [*] --> pending: place_bet<br/>(balance −= amount)
    pending --> removed: remove_bet while the window is open<br/>and its run is the latest<br/>(balance += amount)
    pending --> won: admin settle_outcomes<br/>or settle_bet(won=true)<br/>(balance += amount + potential_win)
    pending --> lost: admin settle_outcomes<br/>or settle_bet(won=false)
    pending --> push: admin settle_outcomes,<br/>tie or score on the line<br/>(balance += amount)
    pending --> void: admin void_bet<br/>(balance += amount)
    pending --> cashed_out: cash_out while the window is open<br/>and a newer run has moved the odds<br/>(balance += offer)
```

The app judges each weekly bet placed by market key against the league's published scores, and the admin confirms the outcomes it shows ([Settlement](#settlement)); futures and legacy bets are settled by hand as won or lost. A bet's legs settle with it, taking its status and `settled_at` in the same transaction. A push and a void both return the stake. A push is a result and stays on the record: the account page lists it as Push, and the leaderboard's popular-bet counts include it and show Push when nothing in the group won or lost. A void means the bet should never have stood, so it leaves `bets_placed` as a remove does, the account page lists it as Void, and the popular-bet counts leave it out with the removed bets.

A pending bet can be removed while its week's window is open and its market still shows the run it was priced at. Once a new run is published for the market, `remove_bet` refuses with `"Odds have changed since this bet was placed"`, and removal gives way to cash-out. A legacy bet has no market to check and is removable while the window is open. `my_bets` reports the rule as each bet's `removable`, and the betting page shows the cancel button only when it is true. A removed bet keeps its row with status `removed` and its legs `void`; the account page lists it as Removed, and the leaderboard's popular-bet counts leave it out.

### Cash-out

Once a newer run has moved a bet's odds, the bettor can close it for 95% of its fair value in that run, while the window is open; otherwise it rides to settlement. `app/cashout.py` prices the offer and never moves money:

```
fair value = (amount + potential_win) × p_win      p_win from the latest run, at the bet's own line
offer      = round(0.95 × fair value, 2)
```

| | Weekly bet | Futures bet |
|---|---|---|
| Latest run | `betting_window(bet.week).run_id` | the `run_id` of the market's latest futures quote |
| Window that must be open | the bet's week | the current week |
| `p_win` | the share of that run's `simulation_totals` sims the bet wins, through the market's win rule, at the leg's own line and selection | the quote's `probability` |

The 5% margin covers news the latest run has not seen and keeps holding a bet the better choice when the odds have barely moved. The latest run must differ from the bet's `run_id`, so a bet is either removable or offered, never both. `my_bets` carries each bet's offer as `cash_out_offer`, and `POST /api/cash_out/<id>` sends back the offer the page showed. The route recomputes it and refuses without moving money when:

| Refusal | When |
|---|---|
| `"Bet not found"` | the bet is not the user's or not pending, or another request closed it first |
| `"No offer for this bet"` | the bet has no legs (a legacy bet) or more than one, or its market key fails to parse |
| `"Betting is paused until the odds update"`, `"Betting is closed for week 4"` | the window is not open; the admin's lock reads as closed |
| `"Odds have not changed since this bet was placed; remove it instead"` | the latest run is the bet's own |
| `"No offer until the next run: the standings are behind"` | futures: the run's `standings_through_week` is below the current week − 1 |
| `"No offer: the latest run cannot price this bet"` | the bet wins in every sim or in none, the latest run has no stored matrix or no column for a roster in the key, the futures market or selection is gone from the latest run, or the offer rounds below one cent |
| `"Offer has changed"` | the recomputed offer differs from the one sent at two decimals; the reply adds the new `offer` |

Only the profit or loss of a cash-out, `offer − amount`, reaches `total_pnl` and a leaderboard, and it posts to the week the cash-out is taken, not the bet's week, so cashing out a week-4 futures bet in week 9 leaves week 4's leaderboard as it was. The bet keeps its row with status `cashed_out`, the result, and the offer, run and time it was taken at, which is the log the margin is reviewed against. The account page lists it as Cashed out with its signed result. The leaderboard counts it as placed: the popular-bet counts include it with neither a win nor a loss and show Cashed out when every bet in the group was, and the best- and worst-bet lists, which read only won and lost bets, leave it out.

### Markets

A bet names a market by key and picks one selection in it. `app/markets.py` builds the key from an odds row, parses it back, and finds the quote: the row's price, chance, line and `run_id` for that selection. From the request, `place_bet` reads only the key, the selection, the `run_id`, the amount and, for team totals, the line; the price always comes from the table.

| Market (`bet_type`) | Key | Selection | Line | Priced from | Price, chance |
|---|---|---|---|---|---|
| `moneyline` | `2026-w04-moneyline-1v4`, lower roster id first | a roster id from the key | | `betting_odds_matchup_ml` by season, week, `team1_id`, `team2_id` | `team1_ml`/`team2_ml`, `team1_win_prob`/`team2_win_prob` |
| `team_total` | `2026-w04-team_total-4` | `over` or `under` | the row's `line`, two decimals | `betting_odds_team_ou` by season, week, `team_id` | `over_odds`/`under_odds`, `over_prob`/`under_prob` |
| `highest_scorer` | `2026-w04-highest_scorer` | a roster id with a row that week | | `betting_odds_highest_scorer` by season, week, `team_id` | `odds`, `probability` |
| `lowest_scorer` | `2026-w04-lowest_scorer` | a roster id with a row that week | | `betting_odds_lowest_scorer` by season, week, `team_id` | `odds`, `probability` |
| `first_place` | `2026-first_place` | a roster id in the latest futures run | | `betting_odds_first_place` by season, `team_id`, at the season's highest `week` | `american_odds`, `probability` |
| `make_playoffs` | `2026-make_playoffs-4` | `yes` | | `betting_odds_make_playoffs` by season, `team_id`, at the season's highest `week` | `american_odds`, `probability` |

Only the latest published season is quoted. Weekly markets must be for the current week. Futures keys have no week; a futures bet's `week` is the current week, where its weekly stats post. After the window, amount and balance checks, `place_bet` refuses without moving money when:

| Refusal | When |
|---|---|
| `"Unknown market"` | the key is not exactly one of the shapes above, or no row has it |
| `"Not this week's market"` | a weekly key names another week |
| `"Unknown selection"` | the market has no such selection: a roster id not in the key, `yes` on a moneyline, `over` on a scorer market, a roster with no row |
| `"Odds have changed"` | the row's `run_id` differs from the request's, or a team total's line is missing or differs at two decimals; the reply adds the row's `run_id`, `price`, `odds` and `line` |
| `"Not offered"` | the side's price is null because its simulated chance is 0 or 1; the page shows "No price" there |

The bet stores the table's `odds` text, `price` (the odds as an integer: `+150` is 150, `-150` is −150, even money is 100), and the row's `probability` and `run_id`. Its one `bet_legs` row records the pick as data: the key, the selection, the line, the price and the chance. The `description` keeps the old wording for the weekly markets (`Samer vs Ammad: Samer -143`, `Samer: Highest Scorer +474`), with team-total lines now at two decimals (`Samer O/U 110.63: Over`); futures read `Samer: First Place +139` and `Ammad: Make Playoffs -114`. The admin page shows it beside each bet's outcome; only futures and legacy bets are still settled by reading it.

**Legacy bets.** Bets placed before market keys existed have no legs and null `price`, `probability` and `run_id`. They keep their `description` and `odds` as the only record of the pick, show on the account page, the leaderboard and the admin page like any other bet, and settle by hand as before. Their `bet_type` was renamed once to the market names (`team_ou` → `team_total`, `first_seed` → `first_place`, `ammad_playoff` → `make_playoffs`); their descriptions keep the old wording ("#1 Seed", "Ammad Playoff").

### Payout math

| Odds | `potential_win` (profit) |
|---|---|
| `+X` | amount × X / 100 |
| `−X` | amount × 100 / X |
| `EVEN` | amount |

`place_bet` works it out from the bet's `price`, where even money is 100, so an even-money win pays the stake. On a win the user receives `amount + potential_win` (stake back plus profit). On a loss nothing is returned; the stake was already deducted when the bet was placed. A push or a void returns the stake alone.

## Settlement

`app/settlement.py` judges each pending bet of a week against the league's team scores, published as `sleeper_matchups`. It never moves money or commits; the admin routes settle through the ledger. Every comparison goes through the win rules in `pipeline/markets.py` ([03](03-modeling-and-odds.md#4-markets)), applied to a one-row score matrix of the week's played scores, so the pipeline prices a market and the app settles it by the same rule. The rules compare floats exactly, and nothing is rounded: scores and lines both carry two decimals, and the same two-decimal value always reads back as the same float, so a score on its line is a push.

| Market | Won | Lost | Push | Undecided |
|---|---|---|---|---|
| `moneyline` | the picked roster outscores the other roster in the key | the other way | equal points | either roster has no score |
| `team_total` | `over`: points above the leg's `line`; `under`: below | the other way | points on the line | the roster has no score |
| `highest_scorer`, `lowest_scorer` | the picked roster's points equal the week's highest (lowest) | otherwise | never | any roster of the week's league has no score |
| `first_place`, `make_playoffs` | | | | always: settled by hand after the season |
| a bet without legs | | | | always: settled by hand |
| a key that fails to parse | | | | always, with the parse error as the reason |

- A roster has no score when its row is missing or its `points` are exactly 0. Sleeper lists every roster of a week at 0.0 until the week is played, and no team with a lineup scores exactly zero.
- The moneyline's other roster comes from the key, not from `matchup_id_number`.
- A team total settles at the `line` stored on its leg, never the odds table's current line, which a later run may have moved.
- Every roster tied on the top (or bottom) score wins in full, because the pricing counted each of them the winner.
- A week whose `sleeper_matchups` rows come from more than one league is refused whole: `"Week 4 has scores from 2 leagues; publish only this season's league"`.

Each outcome carries a reason the admin reads beside it: `Bob B 131.20 vs Alice A 110.50`, `Alice A 110.50, line 110.50`, `highest 131.20: Bob B, Carol C`, `no score for Bob B`, `futures: settle by hand`, `placed before market keys: settle by hand`.

**Preview, then confirm.** The Settle Week card on `/admin` shows the week's scores as a strip, then every pending bet with its bettor, description, reason and outcome (`GET /api/admin/settlement_preview`). One button, "Settle N decided bets", sends each decided bet with the outcome the page showed (`POST /api/admin/settle_outcomes`). The route recomputes every outcome from the scores published now and settles a bet, through `ledger.settle` or `ledger.push`, only when the two agree. Each bet is its own transaction (guard, commit, next), so a failure part-way leaves the bets before it settled. The others are skipped with a reason, the page lists what settled and what was skipped, and both cards reload:

| Skip reason | When |
|---|---|
| `"scores changed: now push"` | a publish since the preview changed the outcome (`now won`, `now lost` the same way) |
| `"undecided"` | the bet has no outcome from the scores published now |
| `"already settled"` | the bet is no longer pending: settled, pushed, voided or removed |
| `"not found"` | no bet of that week has the id |

**Runbook.** When the admin sets the week's period, the lock goes at the week's last window close, Sunday's first kickoff in UTC; the runs open and close betting between Wednesday and then on their own. Locking by hand before that is the kill switch for the week.

**Which scores.** `sleeper_matchups` has no `run_id` and no fetch time: publish replaces it whole from the pipeline's latest league fetch, so settlement uses whatever the last publish carried, and the app cannot tell when that fetch ran. The design's open hypothesis ([odds models §6](../design/odds-models-2026.md#6-settlement-edge-cases)) is that Sleeper's stat corrections move a few starters by a point or two after Monday night. The test is to fetch week 4's matchups on Tuesday morning and again on Friday and compare `players_points`; until the answer is known, settle on Tuesday's fetch. The admin runs the league fetch and publish on Tuesday, checks the card's scores strip, which shows exactly the points the outcomes use, and then presses the button. A bet settled from Tuesday's scores is not revisited when a correction lands later.

**By hand.** The Pending Bets card keeps a Win and a Loss button on every pending bet (`POST /api/admin/settle_bet`). They are how futures (after the season) and legacy bets settle, and they settle any other pending bet as won or lost too. The Void button beside them (`POST /api/admin/void_bet`, after a confirm naming the bet and its stake) refunds a bet that should never have stood: a game not played, an admin mistake.

Both cards show the current week, and the page has no week switch; `settlement_preview` and `pending_bets` take `?week=N` for any other week, and `settle_outcomes` takes the week in its body.

## Accounting

Three places hold money state. `app/ledger.py` is the only code that changes them, with one function per event (`open_week`, `place`, `remove`, `settle`, `push`, `void`, `cash_out`):

```mermaid
flowchart LR
    subgraph users
        BAL[account_balance]
        TP[total_pnl]
    end
    subgraph weekly_stats["weekly_stats (user, week)"]
        SB[starting_balance]
        EB[ending_balance]
        PNL[pnl = ending − starting]
        ACT[active_bets_amount]
        SP[settled_pnl]
        CNT[bets_placed / bets_won]
    end
    subgraph bets
        ST[status / result]
    end
```

| Event | `users` | `weekly_stats` | `bets`, `bet_legs` |
|---|---|---|---|
| First bet of the week | | row created, `starting_balance` = current balance | |
| **Place** | balance −= amount | placed +1, active += amount, ending = balance | insert `pending`, with a `pending` leg |
| **Remove** | balance += amount | placed −1, active −= amount, ending = balance | `removed`; legs `void` |
| **Settle won** | balance += amount + win; total_pnl += win | active −= amount, settled_pnl += win, won +1, ending = balance | `won`, result = +win; legs `won` |
| **Settle lost** | total_pnl −= amount | active −= amount, settled_pnl −= amount, ending = balance | `lost`, result = −amount; legs `lost` |
| **Push** | balance += amount | active −= amount, ending = balance | `push`, result = 0; legs `push` |
| **Void** | balance += amount | placed −1, active −= amount, ending = balance | `void`, result = 0; legs `void` |
| **Cash out** | balance += offer; total_pnl += offer − amount | the bet's week: active −= amount, ending = balance; the cash-out week: settled_pnl += offer − amount, ending = balance | `cashed_out`, result = offer − amount, `cash_out_amount` = offer, `cash_out_run_id`, `cashed_out_at`; legs `cashed_out` |

A push leaves `total_pnl`, `settled_pnl`, `bets_placed` and `bets_won` as they were: the bet was placed and settled for nothing. A void takes the bet out of `bets_placed`, as a remove does, because it should never have counted. A cash-out leaves `bets_placed` and `bets_won` as they were, and when the bet's week is the cash-out week one row takes both changes. Settle, push, void and cash out set `settled_at` on the bet and its legs.

`weekly_stats.pnl` includes the cost of still-open bets (balance-based), while `settled_pnl` only counts resolved bets. The leaderboard uses `users.total_pnl` for all-time and `weekly_stats.settled_pnl` for weekly rankings, and the account page's Weekly P&L shows `settled_pnl` too, so a cash-out's returned stake never reads as profit anywhere.

There is no ledger table: balances change in place, so history can only be reconstructed from `bets`.

### Concurrent requests

Place, remove, settle, push, void and cash out each run as one transaction that opens with a conditional guard: a statement that changes a row only while the event is still allowed. The route commits once when the guard changed a row and rolls back when it changed none; `settle_outcomes` does this once per bet:

| Event | Guard | When it changes no row |
|---|---|---|
| **Place** | `UPDATE users SET account_balance = account_balance - :stake WHERE id = :user AND account_balance >= :stake` | `"Insufficient balance"` |
| **Remove** | `UPDATE bets SET status = 'removed' WHERE id = :bet AND status = 'pending'` | `"Bet not found"` |
| **Settle** | `UPDATE bets SET status = :status, result = :result, settled_at = :now WHERE id = :bet AND status = 'pending'` | `"Bet already settled"` from `settle_bet`; `settle_outcomes` skips the bet as `"already settled"` |
| **Push** | `UPDATE bets SET status = 'push', result = 0, settled_at = :now WHERE id = :bet AND status = 'pending'` | `settle_outcomes` skips the bet as `"already settled"` |
| **Void** | `UPDATE bets SET status = 'void', result = 0, settled_at = :now WHERE id = :bet AND status = 'pending'` | `"Bet already settled"` from `void_bet` |
| **Cash out** | `UPDATE bets SET status = 'cashed_out', result = :result, cash_out_amount = :offer, cash_out_run_id = :run_id, cashed_out_at = :now, settled_at = :now WHERE id = :bet AND status = 'pending'` | `"Bet not found"` |

The statements after the guard change the stored values by SQL arithmetic (`bets_placed = bets_placed + 1`) and read `ending_balance` and `pnl` from the user's balance by subquery, so no request writes back a number it read earlier. The effects table above therefore holds when requests arrive together: a stake never takes a balance below zero, and a bet is paid or refunded at most once. On PostgreSQL a guard that meets a row another request is changing waits for that request to finish, then re-checks its condition against the new value; SQLite runs one writer at a time.

The week's `weekly_stats` row is created in its own short transaction before any money moves; a cash-out opens the current week's row this way before its guard. When two requests create it at once, the loser fails on `uq_user_week`, rolls back and uses the winner's row. `place_bet`'s balance check before the guard is only an early answer, and `new_balance` in the reply is re-read from the database after the commit.
