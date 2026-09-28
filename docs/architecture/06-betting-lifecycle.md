# 06 – Betting Lifecycle

All money is fake. Every user starts with **1,000**. Code: `app/routes/betting.py`, `app/routes/admin.py`, `app/routes/helpers.py`, `app/ledger.py`.

## Betting period (one per week)

```mermaid
stateDiagram-v2
    [*] --> Open: admin set_betting_period(week, lock_time)
    Open --> Locked: first place/remove request<br/>after lock_time (lazy)
    Locked --> Open: admin unlock_period<br/>(lock_time = now + 7 days)
    Open --> Open: admin set_betting_period again<br/>(new lock_time, is_locked=false)
    Locked --> Settled: admin settle_week
    Open --> Settled: admin settle_week
    Settled --> [*]
```

- `lock_time` is entered in the admin form as a naive datetime and **stored as UTC**.
- The lock is lazy: `check_betting_period_lock(week)` flips `is_locked` when it sees `now ≥ lock_time`. It is only called from `place_bet` and `remove_bet`; the betting page itself doesn't check it.
- **Current week** = highest-numbered unsettled period. Settling week *N* moves the site to the next unsettled period; if there is none, it falls back to week 10. Creating the week *N+1* period before settling *N* moves the site forward immediately.
- `settle_week` **only sets `is_settled`**. It doesn't touch bets; pending bets stay pending.

## Bet

```mermaid
stateDiagram-v2
    [*] --> pending: place_bet<br/>(balance −= amount)
    pending --> [*]: remove_bet before lock<br/>(balance += amount, row deleted)
    pending --> won: admin settle_bet(won=true)<br/>(balance += amount + potential_win)
    pending --> lost: admin settle_bet(won=false)
```

The app has no knowledge of real results. The admin looks at each pending bet (`/admin`, filtered by week) and clicks won or lost.

### Bet types

| `bet_type` | Offered on site | Selection sent by client | Odds taken from |
|---|---|---|---|
| `moneyline` | yes | `matchup_idx` + `team1`/`team2` | Server: row *idx* of `betting_odds_matchup_ml` for the week, ordered by `matchup` |
| `team_ou` | yes | `team_idx` + `over`/`under` | Server: row *idx* of `betting_odds_team_ou` ordered by `owner`; always `EVEN` |
| `highest_scorer` | yes | `owner`, `odds` | **Client** |
| `lowest_scorer` | yes | `owner`, `odds` | **Client** |
| `first_seed` | no UI | `owner`, `odds` | **Client** |
| `ammad_playoff` | no UI | `owner`, `odds` | **Client** |

Moneyline and O/U selections are **positional**: the client and server must agree on row order, and a re-publish that adds rows (e.g. a duplicate run) shifts the indexes. The four owner/odds bet types store whatever odds string the browser sends without checking it against the odds tables.

`description` is a human-readable string (e.g. `"Samer vs Ammad: Samer -150"`); nothing structured records which team or side was picked.

### Payout math

| Odds | `potential_win` (profit) |
|---|---|
| `+X` | amount × X / 100 |
| `−X` | amount × 100 / X |
| `EVEN` | amount |

On a win the user receives `amount + potential_win` (stake back plus profit). On a loss nothing is returned; the stake was already deducted when the bet was placed.

## Accounting

Three places hold money state. `app/ledger.py` is the only code that changes them, with one function per event (`open_week`, `place`, `remove`, `settle`):

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

| Event | `users` | `weekly_stats` | `bets` |
|---|---|---|---|
| First bet of the week | | row created, `starting_balance` = current balance | |
| **Place** | balance −= amount | placed +1, active += amount, ending = balance | insert `pending` |
| **Remove** | balance += amount | placed −1, active −= amount, ending = balance | delete row |
| **Settle won** | balance += amount + win; total_pnl += win | active −= amount, settled_pnl += win, won +1, ending = balance | `won`, result = +win |
| **Settle lost** | total_pnl −= amount | active −= amount, settled_pnl −= amount, ending = balance | `lost`, result = −amount |

`weekly_stats.pnl` includes the cost of still-open bets (balance-based), while `settled_pnl` only counts resolved bets. The leaderboard uses `users.total_pnl` for all-time and `weekly_stats.settled_pnl` for weekly rankings.

There is no ledger table: balances change in place, so history can only be reconstructed from `bets`.

### Concurrent requests

Place, remove and settle each run as one transaction that opens with a conditional guard: a statement that changes a row only while the event is still allowed. The route commits once when the guard changed a row and rolls back when it changed none:

| Event | Guard | When it changes no row |
|---|---|---|
| **Place** | `UPDATE users SET account_balance = account_balance - :stake WHERE id = :user AND account_balance >= :stake` | `"Insufficient balance"` |
| **Remove** | `DELETE FROM bets WHERE id = :bet AND status = 'pending'` | `"Bet not found"` |
| **Settle** | `UPDATE bets SET status = :status, result = :result, settled_at = :now WHERE id = :bet AND status = 'pending'` | `"Bet already settled"` |

The statements after the guard change the stored values by SQL arithmetic (`bets_placed = bets_placed + 1`) and read `ending_balance` and `pnl` from the user's balance by subquery, so no request writes back a number it read earlier. The effects table above therefore holds when requests arrive together: a stake never takes a balance below zero, and a bet is paid or refunded at most once. On PostgreSQL a guard that meets a row another request is changing waits for that request to finish, then re-checks its condition against the new value; SQLite runs one writer at a time.

The week's `weekly_stats` row is created in its own short transaction before any money moves. When two requests create it at once, the loser fails on `uq_user_week`, rolls back and uses the winner's row. `place_bet`'s balance check before the guard is only an early answer, and `new_balance` in the reply is re-read from the database after the commit.
