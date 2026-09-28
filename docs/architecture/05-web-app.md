# 05 – Web App

A server-rendered Flask app with vanilla-JS pages that call JSON endpoints. It only reads analytics tables and only writes the five ORM tables.

## Structure

```
app/__init__.py      create_app(): config from env, extensions, blueprints, create_all + migrations
app/auth.py          Google OAuth (Flask-Dance), Flask-Login user loader, /auth/logout
app/ledger.py        The only code that moves money: open_week, place, remove, settle, push, void
app/markets.py       Market keys and their quotes: key_for_row(), parse_key(), find_quote(),
                     price_from_odds(), potential_win()
app/models.py        User, Bet, BetLeg, WeeklyStats, BettingPeriod
app/migrations.py    Idempotent ALTERs run on every startup
app/settlement.py    Each pending bet's outcome from the published scores: team_scores(),
                     outcomes_for_week(), outcome_for()
app/routes/
  helpers.py         query_analytics(), get_current_week(), check_betting_period_lock(),
                     admin_required, owner → display-name mapping
  pages.py           /, /login, /about, /analytics, static + chart images
  account.py         /account, profile update
  odds.py            Odds + analytics JSON API
  betting.py         /betting, /leaderboard, bet placement/removal
  admin.py           /admin + admin JSON API
frontend/templates/  Jinja2 (base.html + one per page)
frontend/static/js/  betting.js, analytics.js, admin.js, base.js (mobile menu only)
```

**Startup** (`create_app`): read `SECRET_KEY` (required unless testing), `DATABASE_URL`, Google OAuth credentials; SQLAlchemy with `pool_pre_ping` and `pool_recycle=300`; `ProxyFix` for nginx; secure cookies when `FLASK_ENV=production`; register blueprints; `db.create_all()` then `run_schema_migrations()`. Module-level `app = create_app()` is what gunicorn imports (skipped under pytest).

**Reading analytics.** Routes call `query_analytics(sql, params)`, which runs raw SQL through `db.session` and returns a list of dicts. There are no ORM models for analytics tables, so a renamed column fails at request time.

## Authentication & authorization

```mermaid
sequenceDiagram
    actor U as Browser
    participant A as Flask
    participant G as Google
    U->>A: GET protected page
    A-->>U: 302 → /auth/google (next URL saved in session)
    U->>G: consent (openid, email, profile)
    G-->>A: /auth/google/authorized
    A->>G: fetch userinfo
    A->>A: find or create User (id = Google id, balance 1000)<br/>is_admin = email in ADMIN_EMAILS
    A-->>U: login_user (permanent session) → 302 next URL
```

- **Anonymous** users can see `/betting` (odds are public), `/leaderboard`, `/about`, and most odds APIs.
- **Logged in** is required for placing/viewing/removing bets, `/account`, `/analytics` data endpoints (`/api/teams`, `/api/team_distribution`, `/api/team_players`).
- **Admin** (`@admin_required`) is re-derived from `ADMIN_EMAILS` on every login, so removing an email revokes admin at next sign-in.
- Unauthenticated `/api/*` calls get `401` JSON; pages redirect to Google.
- **CSRF**: `CSRFProtect` is installed with default checking **off**. Only `POST /account/update-profile` calls `csrf.protect()`. The JSON `POST`/`DELETE` endpoints rely on the `SameSite=Lax` session cookie and JSON content type.

## Routes

| Route | Method | Auth | Reads | Writes |
|---|---|---|---|---|
| `/` | GET | — | | redirect → `/betting` |
| `/login`, `/about` | GET | — | | |
| `/analytics` | GET | — | PNG filenames in `ANALYTICS_IMAGES_DIR` | |
| `/analytics-images/<file>` | GET | — | PNG files (1-day cache) | |
| `/betting` | GET | — | `betting_periods` | |
| `/leaderboard` | GET | — | `users`, `bets`, `weekly_stats` | |
| `/account` | GET | login | `bets`, `weekly_stats` | |
| `/account/update-profile` | POST | login + CSRF | | `users` |
| `/api/session-check` | GET | — | | |
| `/api/matchups` | GET | — | `betting_odds_matchup_ml`, `sleeper_*` | |
| `/api/team_performance` | GET | — | `betting_odds_team_ou` | |
| `/api/highest_scorer`, `/api/lowest_scorer` | GET | — | `betting_odds_*_scorer` + `team_ou` | |
| `/api/first_place`, `/api/make_playoffs` | GET | — | `betting_odds_first_place`, `_make_playoffs` | |
| `/api/lineup/<owner>` | GET | — | `team_lineups` | |
| `/api/league_overview` | GET | — | `sleeper_matchups`, `sleeper_rosters`, `sleeper_users`, `team_distribution_curves`, `betting_odds_matchup_ml` | |
| `/api/position_strength` | GET | — | `team_lineups` | |
| `/api/teams` | GET | login | `sleeper_rosters`, `sleeper_users` | |
| `/api/team_distribution` | GET | login | `team_distribution_curves`, `team_matchup_margin_curves`, `betting_odds_matchup_ml` | |
| `/api/team_players` | GET | login | `projections_rosters` (falls back to `team_lineups`) | |
| `/api/place_bet` | POST | login | odds tables, `betting_periods` | `bets`, `bet_legs`, `users`, `weekly_stats` |
| `/api/my_bets` | GET | login | `bets`, `bet_legs`, `betting_periods`, odds tables | |
| `/api/remove_bet/<id>` | DELETE | login | `betting_periods`, odds tables | `bets`, `bet_legs`, `users`, `weekly_stats` |
| `/admin` | GET | admin | | |
| `/api/admin/betting_periods` | GET | admin | `betting_periods` | |
| `/api/admin/set_betting_period` | POST | admin | | `betting_periods` |
| `/api/admin/pending_bets` | GET | admin | `bets` | |
| `/api/admin/settle_bet` | POST | admin | | `bets`, `bet_legs`, `users`, `weekly_stats` |
| `/api/admin/settlement_preview` | GET | admin | `betting_periods` (default week), `bets`, `bet_legs`, `users`, `sleeper_matchups`, `sleeper_rosters`, `sleeper_users` | |
| `/api/admin/settle_outcomes` | POST | admin | `bets`, `bet_legs`, `sleeper_matchups`, `sleeper_rosters`, `sleeper_users` | `bets`, `bet_legs`, `users`, `weekly_stats` |
| `/api/admin/void_bet` | POST | admin | `bets` | `bets`, `bet_legs`, `users`, `weekly_stats` |
| `/api/admin/settle_week` | POST | admin | | `betting_periods` |
| `/api/admin/unlock_period` | POST | admin | | `betting_periods` |
| `/auth/google`, `/auth/google/authorized` | GET | — | | `users` |
| `/auth/logout` | GET | — | | |

Most odds endpoints catch every exception, print or log it, and return `[]` with status 200, so a missing table looks like "no data" to the frontend.

Every odds/analytics endpoint except the two futures endpoints uses `get_current_week()` (the highest unsettled betting period), so creating a period in `/admin` is what moves the site to a new week. `/api/first_place` and `/api/make_playoffs` list the latest futures run instead, the highest published `week` of the season, because that run may not have reached the current week. The odds listings read only the latest published season. Each row carries its `market` key and `run_id`, which a bet sends back, and the roster ids behind it (`team1_id`/`team2_id` or `team_id`); prices pass through as the table has them, null included.

### Bet endpoints

| Endpoint | Request | Response |
|---|---|---|
| `POST /api/place_bet` | `market`, `selection`, `run_id`, `amount`, and `line` for team totals; nothing else is read | `success`, `new_balance`, `bet_id`, `market`, `selection`, `price` |
| `GET /api/my_bets` | | The user's pending bets, each with `id`, `description`, `amount`, `odds`, `potential_win`, `status`, `week`, `bet_type`, `market`, `selection`, `line`, `price`, `probability`, `run_id`, `removable` |
| `DELETE /api/remove_bet/<id>` | | `success`, `new_balance` |

A refusal is `success: false` with an `error` message. `place_bet` prices the bet from the row its `market` and `selection` find ([06](06-betting-lifecycle.md#markets)); when that row's `run_id` differs from the request's, or a team total's `line` has moved, it refuses with `"Odds have changed"` and adds the row's `run_id`, `price`, `odds` and `line`. In `my_bets`, the six fields from `market` to `run_id` are null on bets placed before market keys existed.

`removable` is true while the bet's week is open and its market's row still carries the bet's `run_id`; a legacy bet has no market to check and is removable while its week is open. `remove_bet` applies the same rule, refusing a bet whose run has been replaced with `"Odds have changed since this bet was placed"`. A removed bet keeps its row, with status `removed`.

### Admin endpoints

| Endpoint | Request | Response |
|---|---|---|
| `GET /api/admin/settlement_preview` | `week` in the query, the current week by default | `success`, `week`, `scores` (each `roster_id`, `team`, `points`), `bets` (each `id`, `user`, `description`, `amount`, `odds`, `potential_win`, `market`, `selection`, `line`, `outcome`, `reason`), `decided`, `undecided` |
| `POST /api/admin/settle_outcomes` | `week`, and `bets`: each `id` with the `outcome` the page showed | `success`, `settled` (bet ids), `skipped` (each `id`, `reason`) |
| `POST /api/admin/void_bet` | `bet_id` | `success` |

The preview lists the week's pending bets in id order. `points` is null for a roster that has not played. `user` is the bettor's first and last name, or `User #` and the first eight characters of the id when both are empty. `market`, `selection` and `line` are null on legacy bets. `outcome` is `won`, `lost`, `push` or `undecided`; `decided` counts the bets with one of the first three and `undecided` the rest. `settle_outcomes` settles a bet only when its recomputed outcome matches the one sent, and gives every other bet a skip reason ([06](06-betting-lifecycle.md#settlement)). A refusal is `success: false` with an `error`: `"Week required"` from `settle_outcomes`; `"Bet ID required"`, `"Bet not found"` and `"Bet already settled"` from `void_bet`. `settlement_preview` and `settle_outcomes` refuse a week whose scores come from more than one league.

## Page → API map

```mermaid
flowchart LR
    subgraph Pages
        B[/betting<br/>betting.js/]
        AN[/analytics<br/>analytics.js/]
        AD[/admin<br/>admin.js/]
        LB[/leaderboard/]
        AC[/account/]
    end

    B --> M[/api/matchups]
    B --> TP[/api/team_performance]
    B --> HS[/api/highest_scorer]
    B --> LS[/api/lowest_scorer]
    B --> FP[/api/first_place]
    B --> MPO[/api/make_playoffs]
    B --> MB[/api/my_bets]
    B --> LU[/api/lineup/owner]
    B --> PB[/api/place_bet]
    B --> RB[/api/remove_bet]

    AN --> SC[/api/session-check]
    AN --> T[/api/teams]
    AN --> TD[/api/team_distribution]
    AN --> LU
    AN --> TPL[/api/team_players]
    AN --> LO[/api/league_overview]
    AN --> PS[/api/position_strength]

    AD --> BPS[/api/admin/betting_periods]
    AD --> SBP[/api/admin/set_betting_period]
    AD --> ULP[/api/admin/unlock_period]
    AD --> SPV[/api/admin/settlement_preview]
    AD --> SOC[/api/admin/settle_outcomes]
    AD --> PEN[/api/admin/pending_bets]
    AD --> STB[/api/admin/settle_bet]
    AD --> VDB[/api/admin/void_bet]
    AD --> STW[/api/admin/settle_week]
```

`/leaderboard` and `/account` are fully server-rendered with no API calls. The betting page's Futures tab lists `/api/first_place` and `/api/make_playoffs` in two groups.

- **betting.js** loads the six odds endpoints in parallel, lazy-loads lineups when a card expands, and updates the balance optimistically on place/remove before reloading `my_bets`. Each card carries its row's `market` key and each pick button its `selection`; a bet marks the card side whose market and selection match its own, so a legacy bet is listed as active but marks no card. A side with a null price shows "No price" instead of a pick button, the cancel button shows only on `removable` bets, and an `"Odds have changed"` refusal reloads the tab at the new quote.
- **analytics.js** renders five Chart.js charts from the precomputed curves (matchup distributions, margin, lineup comparison, standings, position strength). The PNG charts from the pipeline are not used on this page any more; `/analytics` only uses the PNG directory to decide which week to show.
- **admin.js** loads the betting periods, then the Settle Week preview and the Pending Bets list for the current week. Settling the decided bets, settling one by hand and voiding one each reload both cards; after settling the decided bets they reload even when the reply is an error, because the bets settled before a failure stand. Bettors set their own names on the account page and team names come from Sleeper, so the two cards escape every name, description and reason they render.
