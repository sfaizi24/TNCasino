# Odds models for the next bet types

Status: research proposal, 2026-09-28, with the owner's decisions of the same day recorded in §10.
Nothing here is built.
Owner: orchestrator session (staff engineer). Research: R2. Implementation: engineer agents, one work package each.

This document proposes how to price and settle the bets the owner asked for next: parlays (one
bet made of several single bets, its legs, that wins only if every leg wins), cash-out (closing a
bet early for an amount offered now), and a second betting window after Thursday night's game. It
also ranks other bets the pipeline's outputs already support, and cuts the work into packages.

A *run* is one execution of the `simulate` step: it plays the week 50,000 times and keeps every
team's score in each simulated week, called a *sim*, under a `run_id`. A *market* is one thing to
bet on, such as team 4 against team 1, and a *selection* is one side of it, such as team 4. Prices
are American odds, as on the site: +150 means a $100 stake wins $150, and -150 means a $150 stake
wins $100. A *fair* price has no house margin: at the model's chance, the bettor expects to get
back exactly the stake. Teams are Sleeper roster ids 1 to 12.

The numbers come from two places. Week-4 numbers come from run `2026w04-20260928T030248`, made on
Sunday night during week 3; its standings count 2 played weeks instead of 3, so its futures
(season-long bets such as make playoffs) only illustrate. Backtest numbers come from 2025 weeks 10
to 16, with v2.1 refitted each time without the week it prices: 42 games and 84 team scores, a
small sample. Tables name the script behind them, §10 lists the scripts, and the glossary at the
end repeats the betting and statistics terms.

---

## Recommendation

Build in this order; each item names its work package (§9). The owner's decisions of 2026-09-28
(§10) change item 3: no refusal at ±5000.

1. **Structured bets (B1):** each bet stores its market and selection; Flask sets the price (doc 08 issues 1–2).
2. **Atomic balances (B2):** every balance change is one conditional SQL update (doc 08 issue 3).
3. **Fair prices (B1):** no house margin; refuse any price beyond ±5000 instead of clamping chances at 0.1%.
4. **Score matrix (B3):** publish each run's 50,000 × 12 team scores; `pipeline/markets.py` holds the win rules.
5. **Friday rerun (B4, B5):** after Thursday's game, rerun with finished players fixed at their real points.
6. **Windows (B6):** a run's odds are open from its publish to the next NFL kickoff; bets keep their line and price.
7. **Settlement (B7):** from Sleeper's league points through an admin preview, with pushes and voids.
8. **Parlays (B8):** 2 to 4 same-week legs, priced from the sims where every leg wins, under three refusal rules.
9. **Cash-out (B9):** 95% of fair value from the latest run, only while a window is open.
10. **New singles (B10):** matchup totals, head to head, spreads, alternate lines, last place; props (B11) last.

---

## 1. Parlays

### 1.1 The product price and the joint price

A *parlay* is one bet made of several *legs*, each an ordinary single bet, and it wins only if
every leg wins. The usual shortcut multiplies the legs' chances as if the legs had nothing to do
with each other; this document calls that the *product*. The pipeline can do better, because every
run keeps its 50,000 sims: the share of sims in which every leg wins is the *joint chance*. The
*ratio* of joint to product says how wrong the shortcut is. It is also what a bettor expects back
per $1 at a product price: at 1.74 the bettor expects $1.74 per $1 staked, at 0.26 only 26 cents.

A week offers 72 legs: 12 moneyline picks (a *moneyline* bet picks the winner of a matchup), 24
team totals (over or under a line on one team's score), 12 matchup totals (over or under a line on
both teams' combined score), and 12 highest-scorer and 12 lowest-scorer picks across the league.
The week-4 inputs:

| Team | Opponent | Wins | Moneyline | Team line |
|---|---|---|---|---|
| 1 | 4 | 51.1% | -105 | 108.58 |
| 2 | 5 | 36.6% | +173 | 110.36 |
| 3 | 6 | 42.7% | +134 | 103.93 |
| 4 | 1 | 48.9% | +105 | 107.70 |
| 5 | 2 | 63.4% | -173 | 121.43 |
| 6 | 3 | 57.3% | -134 | 109.44 |
| 7 | 8 | 64.1% | -179 | 123.34 |
| 8 | 7 | 35.9% | +179 | 111.36 |
| 9 | 12 | 27.7% | +261 | 91.57 |
| 10 | 11 | 37.5% | +167 | 99.77 |
| 11 | 10 | 62.5% | -167 | 109.71 |
| 12 | 9 | 72.3% | -261 | 109.63 |

Each team line sits at the team's simulated *median*, the score half the sims fall below, so over
and under are 50% each and the app offers both at even money.

### 1.2 Moneylines and team totals in week 4

The brief asks for every 2- and 3-leg combination of moneylines and team totals: 36 legs, 612
pairs and 6,528 triples once combinations with two legs from one market are left out
(`parlays_week4.py`).

| Two legs | Pairs | Smallest ratio | Median | Largest | Off by more than 10% |
|---|---|---|---|---|---|
| From different matchups | 540 | 0.96 | 1.00 | 1.04 | 0 |
| Both team totals of one matchup | 24 | 0.99 | 1.00 | 1.01 | 0 |
| A team wins and goes over | 12 | 1.27 | 1.50 | 1.74 | 12 |
| A team wins and its opponent goes under | 12 | 1.28 | 1.50 | 1.71 | 12 |
| A team wins and goes under | 12 | 0.26 | 0.50 | 0.73 | 12 |
| A team wins and its opponent goes over | 12 | 0.29 | 0.50 | 0.72 | 12 |

Legs inside one matchup are tied together. A team that wins has usually scored a lot, so "wins and
goes over" happens about 1.5 times as often as the product says, and "wins and goes under" about
half as often. Legs from different matchups are nearly unrelated, but not exactly: 10.2% of the
540 cross-matchup pairs sit more than two standard errors from 1, where chance alone would put
about 4.6% (a *standard error* is the typical wobble from using 50,000 sims instead of infinitely
many). The cause is one NFL team's players starting on different fantasy rosters (§7.4). Those
gaps stay small: at most 4.1% for two legs and 9.4% for three.

The worst pairs (`worst_cases.py`):

| Legs | Product | Joint | Sims where both win | Ratio | Product price | Joint price |
|---|---|---|---|---|---|---|
| 9 wins, 9 under 91.57 | 13.9% | 3.6% | 1,805 | 0.26 | +622 | +2670 |
| 9 wins, 12 over 109.63 | 13.9% | 4.0% | 2,016 | 0.29 | +622 | +2380 |
| 8 wins, 8 under 111.36 | 17.9% | 6.3% | 3,167 | 0.35 | +458 | +1479 |
| 8 wins, 7 over 123.34 | 17.9% | 6.4% | 3,217 | 0.36 | +458 | +1454 |
| 10 wins, 10 under 99.77 | 18.7% | 6.8% | 3,408 | 0.36 | +434 | +1367 |
| 2 wins, 2 under 110.36 | 18.3% | 6.7% | 3,363 | 0.37 | +447 | +1387 |
| 9 wins, 9 over 91.57 | 13.8% | 24.1% | 12,043 | 1.74 | +622 | +315 |
| 9 wins, 12 under 109.63 | 13.9% | 23.7% | 11,832 | 1.71 | +622 | +323 |
| 8 wins, 8 over 111.36 | 17.9% | 29.5% | 14,763 | 1.65 | +458 | +239 |
| 8 wins, 7 under 123.34 | 17.9% | 29.4% | 14,713 | 1.64 | +458 | +240 |
| 10 wins, 10 over 99.77 | 18.7% | 30.6% | 15,324 | 1.64 | +434 | +226 |
| 2 wins, 2 over 110.36 | 18.3% | 29.9% | 14,929 | 1.63 | +447 | +235 |

Reading the first row: team 9 wins only 27.7% of sims and needs a big score to do it, so it wins
while staying under its line in 3.6% of sims, not 13.9%. At the product price of +622, a $100 bet would
expect $26 back. At the joint price of +2670 it expects $100 back.

| Three legs | Triples | Smallest ratio | Median | Largest | Off by more than 10% |
|---|---|---|---|---|---|
| From three different matchups | 4,320 | 0.91 | 1.00 | 1.08 | 0 |
| Two or three from one matchup | 2,208 | 0 | 1.00 | 2.90 | 67.2% |

Six triples can never win. Each asks a team to win and stay under its line while its opponent goes
over a higher line, which arithmetic rules out. Their product prices would pay a bet that cannot
win, and the current clamp would price their joint chance of 0 at +99900.

| Legs | Product | Product price |
|---|---|---|
| 9 wins, 9 under 91.57, 12 over 109.63 | 6.9% | +1344 |
| 3 wins, 3 under 103.93, 6 over 109.44 | 10.7% | +837 |
| 10 wins, 10 under 99.77, 11 over 109.71 | 9.4% | +968 |
| 4 wins, 4 under 107.70, 1 over 108.58 | 12.2% | +719 |
| 8 wins, 8 under 111.36, 7 over 123.34 | 9.0% | +1015 |
| 2 wins, 2 under 110.36, 5 over 121.43 | 9.1% | +994 |

When the winner's line is the higher one, the triple is possible but rare: both teams must land in
the gap between the two lines.

| Legs | Gap between the lines | Product | Joint | Sims | Product price | Joint price |
|---|---|---|---|---|---|---|
| 1 wins, 1 under 108.58, 4 over 107.70 | 0.88 | 12.8% | 0.01% | 7 | +682 | +99900 |
| 6 wins, 6 under 109.44, 3 over 103.93 | 5.51 | 14.3% | 0.55% | 275 | +598 | +18082 |
| 11 wins, 11 under 109.71, 10 over 99.77 | 9.94 | 15.6% | 1.57% | 783 | +540 | +6286 |
| 5 wins, 5 under 121.43, 2 over 110.36 | 11.07 | 15.9% | 1.75% | 877 | +531 | +5601 |
| 7 wins, 7 under 123.34, 8 over 111.36 | 11.98 | 16.0% | 1.95% | 977 | +524 | +5018 |

The other end, where the product pays far too much:

| Legs | Product | Joint | Sims | Ratio | Product price | Joint price |
|---|---|---|---|---|---|---|
| 9 wins, 9 over 91.57, 12 under 109.63 | 6.9% | 20.1% | 10,027 | 2.90 | +1344 | +399 |
| 8 wins, 8 over 111.36, 7 under 123.34 | 9.0% | 23.1% | 11,546 | 2.58 | +1015 | +333 |
| 2 wins, 2 over 110.36, 5 under 121.43 | 9.2% | 22.9% | 11,471 | 2.51 | +993 | +336 |
| 10 wins, 10 over 99.77, 11 under 109.71 | 9.4% | 23.4% | 11,703 | 2.50 | +968 | +327 |
| 3 wins, 3 over 103.93, 6 under 109.44 | 10.7% | 24.5% | 12,242 | 2.29 | +837 | +308 |
| 4 wins, 4 over 107.70, 1 under 108.58 | 12.2% | 25.2% | 12,575 | 2.06 | +719 | +298 |

Chart `docs/design/images/r2_parlay_ratio_distribution.png` shows the same thing as bars: how many combinations fall
in each 0.1-wide band of the ratio, two legs in the top panel and three legs below, on a log scale.
Combinations whose legs all come from different matchups (blue) sit only in the 0.9 and 1.0 bands;
combinations that share a matchup (orange) spread from 0 to beyond 2.5.

### 1.3 Which combinations to refuse

Three rules, applied in order:

1. **Two legs from one market:** both teams of a matchup, a team's over and its under, two
   highest-scorer picks. Such legs can only win together on an exact tie.
2. **A leg that adds nothing:** some legs guarantee another, such as "team 1 is the highest scorer"
   and "team 1 beats team 4". Priced from the joint chance, the parlay pays exactly what "team 1
   is the highest scorer" pays alone, so it looks like a parlay without being one. Refuse it and
   point to the single. In the sims the test is exact: removing the leg does not change the number
   of winning sims.
3. **A price beyond ±5000:** a joint chance under 1/51 (about 2%) or over 50/51 rests on too few
   sims and swings the leaderboard (§5). This covers the triples that can never win.

Over all 72 legs (`refusal_counts.py`):

| Rule, in order | 2 legs | With a scorer leg | 3 legs | With a scorer leg |
|---|---|---|---|---|
| Combinations | 2,556 | | 59,640 | |
| Two legs from one market | 156 (6.1%) | 84.6% | 10,040 (16.8%) | 89.0% |
| A leg adds nothing | 36 (1.4%) | 100% | 5,116 (8.6%) | 98.6% |
| Beyond ±5000 | 378 (14.8%) | 100% | 19,270 (32.3%) | 99.5% |
| Priced | 1,986 (77.7%) | 44.4% | 25,214 (42.3%) | 36.4% |
| Priced, product off by more than 10% | 21.2% | | 37.9% | |

Without scorer legs the picture is calm. Of 1,128 pairs, 24 are refused (2.1%, all two legs from
one market) and 8.7% of the rest have a product off by more than 10%. Of 17,296 triples, 1,270 are
refused (7.3%) and 24.0% of the rest are off by more than 10%. Among moneyline and team-total
legs alone, no pair and 0.8% of triples price beyond +5000.

Ratio ranges by the markets a pair draws from, over the 2,300 pairs in which neither leg is
redundant and both legs win together in at least 50 sims:

| Pair drawn from | Pairs | Ratio range |
|---|---|---|
| Two moneylines | 60 | 0.96–1.03 |
| Two team totals | 264 | 0.96–1.04 |
| A matchup total and a moneyline | 144 | 0.97–1.03 |
| Two matchup totals | 60 | 0.97–1.03 |
| A matchup total and a team total | 288 | 0.47–1.53 |
| A moneyline and a team total | 288 | 0.26–1.74 |
| Highest scorer and a team total | 273 | 0.02–2.00 |
| Lowest scorer and a team total | 270 | 0.06–2.00 |
| Highest scorer and a matchup total | 143 | 0.07–1.98 |
| Lowest scorer and a matchup total | 142 | 0.22–1.91 |
| Highest and lowest scorer | 128 | 0.90–1.83 |
| Highest scorer and a moneyline | 120 | 0.90–1.18 |
| Lowest scorer and a moneyline | 120 | 0.92–1.20 |

A scorer leg and a total on the same team nearly decide each other. The team that scores the most
in the league is nearly always over its own line, so "8 is the highest scorer and 8 goes over" wins
about as often as the scorer leg alone (ratio near 2). It is nearly never under: "5 is the highest
scorer and 5 goes under" has a ratio of 0.02. A matchup total and that matchup's moneyline, on the
other hand, are nearly unrelated: a high combined score says little about who won.

Too tied together for a product price: a moneyline with a team total from the same matchup (0.26
to 1.74), a matchup total with a team total (0.47 to 1.53), a scorer leg with any total (0.02 to
2.00) or with the other scorer market (up to 1.83), and any triple with two or three legs from one
matchup (0 to 2.90). Pricing every parlay from the joint chance makes that list irrelevant.

### 1.4 Which legs belong

Same-week moneylines, team totals, matchup totals, highest and lowest scorer, and later spreads and
head to head (§4). All are priced from the joint chance on one run's sims.

No futures legs. First place and make playoffs come from the playoffs step, a separate simulation
of the rest of the season (20,000 sims per run), and settle in December; a parlay holding one would
sit open for months. Joint pricing would need each sim's final standings next to its week scores.
The pieces exist: the playoffs step plays the current week with the first 20,000 sims of the run
(`simulated_current_week`, playoffs.py line 174), so storing each run's finishing positions (20,000
× 12 one-byte numbers, 240 KB) would line the two up sim for sim. What would change my mind: owners
asking for season-long parlays once the weekly ones run.

**Reversed 2026-10-01 (B12).** The owners asked for season-long parlays, the condition above: "can
we do futures parlays? like only parlay futures with futures or current week w current week." The
standings matrix this section sketched is now stored. The playoffs step writes each run's simulated
seasons to `simulation_standings`, one row per simulation run: every season's finishing place for
each roster, the 20,000 × 12 one-byte numbers above, with the season's champion beside them as a
thirteenth column, 260 KB before compression. Publish appends it as it appends
`simulation_totals`. A slip now holds two to four of the week's picks or two to four futures picks
(make playoffs yes or no, last place, champion), priced at the share of the latest futures run's
seasons in which every leg wins. The two kinds never share a slip: a mixed slip is refused
(`mixed`), as the owners asked, so the week's sims are not lined up with the seasons after all, and
a weekly parlay still settles within its week. The three refusal rules carry over to futures: one
roster's YES and NO, or two teams for last place, are two legs from one market; one roster last and
champion, or NO and champion, never win together; a roster's champion leg makes its YES add nothing.
First place left the markets in the same package.

Scorer legs are allowed, under the three rules. Runner-up: leave scorer legs out of parlays, which
cuts refusals from 22.3% of pairs and 57.7% of triples to 2.1% and 7.3%. What would change my mind:
if more than a fifth of the parlays owners try are refused (B8 logs refusals), drop scorer legs.

### 1.5 Pricing at bet time

| Option | How it works | Stored per run | Verdict |
|---|---|---|---|
| Score matrix and shared rules | Publish the 50,000 × 12 scores; Flask caches them per worker and prices any legs with `markets.py` | 2.1 MB compressed | Build this |
| Bit matrix | Publish one win-or-lose bit per leg per sim, 50,000 × 72 | 0.37 MB compressed | Runner-up |
| Draws in Postgres, priced in SQL | 600,000 rows per run, one self-join per leg | about 30 million rows a season | Rejected |
| Precomputed prices | Every 2- and 3-leg price, written at publish | 62,196 rows; 4 legs add 1,028,790 | Rejected |
| Endpoint on the pipeline machine | Flask asks the laptop | nothing | Rejected |

- The bit matrix fixes the legs and lines at publish. It cannot price a bet's old line after a
  rerun (cash-out and parlay settlement need that), alternate lines, or any two teams head to head.
- SQL writes the win rules a second time in another language, and joins 600,000 rows per leg.
- Precomputed prices cover no alternate or old lines, and a 4-leg cap multiplies the rows by 17.5.
- The laptop is off most of the week, and pipeline-v2 §2 keeps the site independent of it.

Sizes and speed (`parlays_week4.py`, `float32_check.py`, timed on the laptop):

| Item | Size or time |
|---|---|
| Parquet draws on the laptop, one run | 3,385,309 bytes |
| Score matrix, float32 | 2,400,000 bytes |
| Score matrix, zlib-compressed | 2,097,668 bytes |
| Bit matrix for 72 legs, zlib-compressed | 373,472 bytes |
| Runs per season (17 weeks × Wednesday, Friday, Saturday) | about 50 |
| Score matrices per season | about 105 MB |
| One run cached in a Flask worker, float64 | 4.8 MB |
| Decompress one run | 9.2 ms |
| Convert to float64 | 1.1 ms |
| Price a 3-leg parlay | 0.21 ms |
| Price a 4-leg parlay | 0.33 ms |

The odds step compares in float64 (`load_draws`, `pipeline/steps/odds.py` line 185), and
`markets.py` should do the same. Comparing in float32 changed none of 3.6 million leg outcomes, so
a worker could cache 2.4 MB per run instead if memory is tight.

**`simulation_totals`**, a new Postgres table:

| Column | Type | Meaning |
|---|---|---|
| `run_id` | text, primary key | the run |
| `season`, `week` | integer | |
| `created_at` | text | the run's creation time |
| `n_sims` | integer | 50,000 |
| `roster_ids` | text | column order, `1,2,...,12` |
| `totals` | bytea | zlib-compressed float32, little-endian, 12 scores per sim, sim after sim |

Publish inserts each week's latest run with `INSERT ... ON CONFLICT (run_id) DO NOTHING` before it
swaps in the other tables, so a published run always has its matrix. The table must never be
swapped, because a swap would throw away the season's earlier matrices. Since B3 (2026-09-28)
publish refuses to stage any table in `APPEND_ONLY_TABLES`, a second guard beside
`PROTECTED_TABLES`, and `simulation_totals` is its one member. Every published run of the season
is kept, because settlement and cash-out re-price at the run a bet was placed at.

**`pipeline/markets.py`**, the win rules in one place:

| Market | Wins | Pushes |
|---|---|---|
| Moneyline, head to head | score above the opponent's | equal scores |
| Over | score above the line | score on the line |
| Under | score below the line | score on the line |
| Spread | score plus the line above the opponent's | exactly equal |
| Highest or lowest scorer | every team sharing the top (bottom) score | – |

It uses numpy only. `pipeline/__init__.py` is a docstring, so Flask can import `pipeline.markets`
without loading the rest of the pipeline. The odds step is refactored onto it, and the test is that
it reproduces the frozen week-4 tables exactly.

**At bet time** Flask:

1. receives the legs (market key, selection, line), the stake and the run_id the page showed;
2. refuses if the window is closed or the run is no longer the latest, and shows the new price;
3. loads the run's matrix from a per-worker cache keyed by run_id (one Postgres read per worker per run);
4. computes each leg's win column, applies the three rules and prices the joint chance fair;
5. stores the bet and its legs with the run_id, price and chance, in the transaction that takes
   the stake (B2).

### 1.6 Settling a parlay

All or nothing: one lost leg loses the parlay. A pushed leg (a score exactly on its line) or a void
leg drops out, and the remaining legs are re-priced on the placement run's matrix at their stored
lines. With one leg left, the bet becomes that single at its stored price; with none left, the
stake is refunded. A parlay settles only when every leg is decided. Pushes are rare: a team scores
exactly its line in 0.018% of sims when scores are rounded to cents (§6).

**Recommendation.** Parlays of 2 to 4 same-week legs, priced from the joint chance on the published
score matrix with `pipeline/markets.py`, under the three refusal rules, with no futures legs.

**Runner-up.** The bit matrix (0.37 MB a run), which gives up old lines after a rerun, alternate
lines and head to head. What would change my mind: a worker's memory not fitting two cached runs
of 4.8 MB. For settlement, the runner-up is to store at placement the price of every subset of
legs (14 prices for a 4-leg parlay), which settles without the old matrix.

**Implementation notes (B8, 2026-09-29).** `app/parlays.py` quotes a slip of 2 to 4 same-week
legs at the joint chance on the latest run's matrix, refusing in a fixed order: size, a leg that
does not parse or is futures or another week's, two legs from one market, odds that moved (another
run, or a team total at another line), no price, a joint chance of 0 or 1 (`impossible`), and a
leg whose removal leaves the winning-sim count unchanged (`redundant`). The refusals the owner
reviews (`same_market`, `impossible`, `redundant`) are logged to `parlay_refusals` by
`POST /api/parlay_quote`; `POST /api/place_bet` with two or more `legs` records the parlay through
the same ledger path as a single. `app/matrices.py` decodes a run's matrix once per worker
(`lru_cache(maxsize=4)`) for parlays, cash-out and settlement. Settlement judges a parlay leg by
leg, undecided until every leg is; pushed legs drop out and the rest re-price on the placement
run's matrix, a lone leg at its own price, none left is a push, and a missing matrix is undecided
for the admin to settle by hand. Cash-out prices a parlay at the joint chance of its legs
(PR 10, merge 022e9af). Built by a Claude Code
cloud session from `docs/briefs/b8-parlays.md`; merged 2026-09-29 (9e43632), 980 tests.

**Implementation notes (B12, 2026-10-01).** Every team gets a make-playoffs card: the 1% to 99%
band of §2.4 no longer applies to make playoffs, only to last place and the champion, and a side
whose chance is exactly 0 or 1 is stored with NULL odds, shown as "No price" and refused at
placement. Futures legs are allowed now (§1.4). A futures slip is
quoted and placed at the futures run its page listed, on that run's `simulation_standings` row
(`standings_matrix`, cached four runs per worker like the score matrix), through the futures win
rules in `pipeline/markets.py`; its refusals run in the same order with `mixed` after a leg's own,
and `mixed` is not logged to `parlay_refusals`. A futures parlay is removable while its legs' quotes
keep its run, and offered a cash-out at the joint chance on the latest futures run's seasons once a
newer run quotes its legs and the standings are not behind. Settlement now loses a parlay as soon as
one leg loses, undecided legs taking `lost`; a futures parlay whose standings legs won and whose
champion leg is left stays open until the admin settles it by hand after the final. The app reads
`no_probability`, `no_american_odds` and `simulation_standings`, so after the merge the pipeline
runs (the playoffs step at least) and publishes from this code before the app restarts; until then
`/api/make_playoffs` returns an empty list, and make-playoffs bets and futures parlays are refused.
The production table `betting_odds_first_place` is dropped by hand after the merge, since the publish
step swaps only the tables it lists.

---

## 2. Cash-out

### 2.1 Fair value

*Cash-out* lets a bettor close a pending bet before it is decided, for an amount offered now. The
fair value of a bet is its payout times its chance of winning in the latest run, at the bet's own
line. The offer is 95% of that:

```
offer = 0.95 × payout × p_win     (payout = stake + potential win; p_win from the latest run at the bet's line)
```

For a parlay, `p_win` is the joint chance of its legs in the latest run. A push refunds the stake;
its chance, about 0.02%, is left out.

Worked example on real numbers (`thursday_lock.py`). $100 on team 4 to beat team 1 at +105 pays
$205 if it wins. At placement its chance was 48.9%, so its fair value was $205 × 0.48862 = $100.17;
the 17 cents come from rounding the fair +104.66 to +105. Thursday's game, PIT at CLE, has one
league starter: team 4's FLEX, Quinshon Judkins. After it, the Friday run changes team 4 only:

| Judkins scores | Team 4 wins | Fair value | Offer at 95% |
|---|---|---|---|
| Before the game | 48.9% | $100.17 | none: no newer run |
| 2.51 (1 game in 10 is worse) | 38.2% | $78.29 | $74.38 |
| 9.72 (his median) | 47.4% | $97.09 | $92.24 |
| 19.61 (1 game in 10 is better) | 60.4% | $123.84 | $117.64 |

Futures work the same way. $100 on team 4 to make the playoffs at +152 (39.6%) pays $252, a fair
value of $99.83 at placement. If a later run puts team 4 at 50%, the fair value is $126.00 and the
offer $119.70.

### 2.2 The house margin

The offer keeps 5% of fair value because:

- only bettors who choose certainty pay it; everyone else still bets at fair prices;
- it covers news the latest run has not seen, such as an injury after the scrape;
- it makes cashing out on a stale run a poor trade for small edges;
- it is small next to what a rerun moves: the mean Thursday-game move of 8.8 percentage points
  (§3.4) is about $18 on a $205 payout, against a margin of about $5.

Revisit the 5% after weeks 5 and 6 with the cash-out log.

### 2.3 What a bet must store

Today a bet stores a readable `description` and an `odds` string; nothing records which team or
side was picked (doc 06). Add to `Bet`:

| Column | Type | Why |
|---|---|---|
| `run_id` | text | the run the bet was priced at |
| `price` | integer | American odds as a number: +105 is 105, -105 is -105, even is 100 |
| `probability` | float | the chance at placement |
| `cashed_out_at` | timestamp | |
| `cash_out_amount` | float | |

New statuses: `push`, `void`, `cashed_out`. The `odds` string stays for display and for the
leaderboard queries that cast it to a number (betting.py lines 107 and 141).

A new table `bet_legs` holds what was picked. A single bet has one leg, a parlay two to four:

| Column | Type | Meaning |
|---|---|---|
| `id` | integer | |
| `bet_id` | integer | the bet |
| `season`, `week` | integer | `week` is null for futures |
| `market` | text | the market key |
| `selection` | text | a roster id, `over`, `under`, `yes` or `no` (`no` from B12) |
| `line` | numeric(7,2) | null when the market has no line |
| `price`, `probability` | integer, float | the leg's own single price and chance at placement |
| `status` | text | pending, won, lost, push or void |
| `settled_at` | timestamp | |

Market keys, lower roster id first as the odds tables order matchups:

| Market | Key | Selection | Line |
|---|---|---|---|
| Moneyline | `2026-w04-moneyline-1v4` | roster | – |
| Team total | `2026-w04-team_total-4` | over or under | 107.70 |
| Matchup total | `2026-w04-matchup_total-1v4` | over or under | 218.07 |
| Spread | `2026-w04-spread-1v4` | roster | signed, +0.96 for team 4 |
| Highest scorer | `2026-w04-highest_scorer` | roster | – |
| Lowest scorer | `2026-w04-lowest_scorer` | roster | – |
| Head to head | `2026-w04-head_to_head-3v10` | roster | – |
| First place (today's `first_seed`; removed in B12) | `2026-first_place` | roster | – |
| Make playoffs (today's `ammad_playoff`) | `2026-make_playoffs-4` | yes, or no from B12 | – |
| Last place | `2026-last_place` | roster | – |

Bets placed before the change keep their description and settle by hand as today.

### 2.4 When cash-out is refused

- **No newer run** than the bet's: the price has not moved. While the window is open the bettor can
  remove the bet instead.
- **The window is closed:** a game has kicked off and no locked rerun has been published, so the
  latest run does not know what happened.
- **The chance is at the edge:** the market is gone from the latest run (the playoffs step drops
  futures rows under 1% or over 99%, playoffs.py lines 30–31), or the bet wins in none of the sims
  or in all of them, so the sims cannot say what it is worth. Today's clamp would show such a
  chance as 0.1% or 99.9%; cash-out must never use a clamped number.
- **Futures from a run whose standings miss a week:** the week-4 run counted 2 played weeks, not 3.
  The run records `standings_through_week` (§3.3), and Flask refuses when it is behind.
- **The bet is settled or already cashed out.**

### 2.5 Removing a bet

`remove_bet` (betting.py line 582) refunds any pending bet in full until the week locks, and
deletes the row. With a Thursday rerun that is a free option: after a 2.51-point Judkins game the
refund pays $100 for a bet worth $78.29, a $21.71 gift. Allow removal only while the bet's run is
still the latest and its window is open; after that, cash-out is the way out. Keep the row with a
`removed` status instead of deleting it. Today removal is safe only if the admin locks at or before
Thursday's kickoff, which README line 75 describes as the 2025 habit.

Cash-out profit or loss posts to the week it is taken, not to `bet.week` as `settle_bet` does;
otherwise cashing out a week-4 futures bet in week 9 would rewrite week 4's leaderboard.

**Recommendation.** Cash-out on every bet type at 95% of fair value from the latest run, only while
a window is open, with the bet storing its run, price, chance and legs.

**Runner-up.** Futures-only cash-out first, at the same 95%: weekly Wednesday runs already move
futures, so it needs no rerun. What would change my mind: the Thursday rerun slipping past week 6.

**Implementation notes (B9, 2026-09-29).** `app/cashout.py` prices a pending bet, single or parlay, at the bet's
own line: a weekly bet from the week's latest run's score matrix in `simulation_totals` through
the win rule in `pipeline/markets.py`, a futures bet from the latest futures quote once the week's
simulation run has standings through at least the previous week. There is no offer while the
bet's own run is still the latest (removal is the way out), outside an open window, or when the
chance is 0 or 1, the selection is gone from the market or the roster is missing from the matrix.
`ledger.cash_out` closes the bet with status `cashed_out` (legs the same), pays the offer and
posts only offer minus stake to the week the cash-out is taken, so the returned stake never
reaches a leaderboard. The account page's weekly P&L now shows `settled_pnl`, the figure the
leaderboard ranks by. The bet keeps `cash_out_amount`, `cash_out_run_id` and `cashed_out_at` for
the week-6 margin review. §3.7's own-line-beside-today's on each chip is not built. Built by a
Claude Code cloud session from `docs/briefs/b9-cash-out.md`; merged 2026-09-29 (52aebf2), 877 tests.

---

## 3. Thursday rerun and windows

### 3.1 Which players are locked

The brief suggests `player_stats`. Use `matchups.players_points` instead:

- `player_stats` is fetched for weeks 1 to N−1 only, so Thursday's game is not in it mid-week.
- It holds PPR points (points per reception, a standard scoring system), and the league scores QBs
  and defenses differently. Bets settle on the league's points.
- `matchups.players_points` is Sleeper's league scoring for every rostered player, `matchups.points`
  is its sum over the starters, and it fills in live: at the week-4 run, 99 of 108 week-3 starters
  already had points.

League points against PPR points for 2025 weeks 10–16 starters (`explore_midweek.py`):

| Position | Starters | Same points | Mean gap | Largest gap |
|---|---|---|---|---|
| QB | 84 | 44.0% | 0.76 | 4 |
| DEF | 84 | 20.2% | 1.81 | 7 |
| K | 84 | 100% | 0 | 0 |
| RB | 197 | 100% | 0 | 0 |
| TE | 89 | 100% | 0 | 0 |
| WR | 217 | 100% | 0 | 0 |

A player is *locked* when his NFL game is final: `nfl_schedules.status = 'STATUS_FINAL'` for his
team (`nfl_players.team`) in week N. Decide by status, never by nonzero points: a player can finish
on 0, and live points are nonzero mid-game. B5 makes `simulate` refuse to run while any week-N
game is in progress.

Lock the owner's actual starters, not the modelled ones. For games that have kicked off, pin the
owner's starters from `matchups.starters` at their real points in the owner's slots; bench players
in those games can no longer enter a lineup, so the lineups step must not pick them; fill the other
slots as today. The modelled lineups are not the owners': in 2025 weeks 10–16 they shared on
average 7.15 of 9 starters (`validate_2025.py`). Sleeper locks a slot at its player's kickoff, so
pinning reads a fact rather than modelling owners, but it touches pipeline-v2's non-goal on lineup
snapshots, and the owner should confirm it.

Keep seed 1738. On week 4, regenerating the run reproduced the stored draws bit for bit, and
locking Judkins changed team 4 only: teams 1–3 and 5–12 stayed bit-identical (`thursday_lock.py`).
The model links only players of the same NFL team, and every player of a finished game is fixed at
once, so fixing them needs no change to anyone else's draws. A changed starter is different: it can
shift the draws of other rosters' players from the same NFL team, because teammates are drawn
together.

### 3.2 What the league step must provide

Nothing new to fetch. It already reads week N's matchups (starters and live `players_points`) and
refreshes `nfl_schedules` (game status) and `nfl_players` (team and injury status). It must simply
run at rerun time. `simulate_teams` already accepts `locked_points` (player id to points); B5 builds
that from these tables.

The WP9 runbook plan (the run-pipeline skill rewrite) needs two corrections:

| Plan | What happens | Fix |
|---|---|---|
| Friday: `run --week N --from simulate` (WP9 brief lines 54–55) | Same starters, seed 1738 and no locks: it reproduces Wednesday's run bit for bit under a new run_id | `--steps league,lineups,simulate,odds,playoffs,validate`, with locks |
| Saturday: "re-run scrape..odds" (WP9 brief addendum) | `--from scrape` skips league, so injury statuses keep Wednesday's values and late inactives stay in the lineups (the lineups step drops Out, IR, PUP, Sus and Doubtful, lineups.py line 18) | The full default run, league included |

### 3.3 Which steps rerun

- **Friday**, once Thursday's game is final: `python -m pipeline run --week N --steps
  league,lineups,simulate,odds,playoffs,validate`, then publish. No scrape: Wednesday's projections
  still hold for players who have not played, the league step brings injury news, and scrapers are
  the fragile part.
- The playoffs step re-projects every future week (scrape to stats, over the network), and its
  runtime on a rerun is unmeasured. If it is too slow or fragile on Friday, leave it out: publish
  keeps the latest run per table (`keep_latest_run`), so the futures tables keep Wednesday's rows
  while the weekly tables move to Friday's run.
- **Saturday**, the late-inactives run: the full default run, then publish. It locks Thursday's
  players the same way, because locks come from game status.

New columns (pipeline-v2 §6.1 allows added columns):

| Table | Column | Meaning |
|---|---|---|
| `simulation_runs` | `n_locked` | players fixed at real points; 0 on Wednesday |
| `simulation_runs` | `window_closes_at` | the next NFL kickoff after the run, UTC |
| `simulation_runs` | `standings_through_week` | the last week the standings count as played |
| `team_lineups` | `locked_points`, `is_locked` | a slot's real points once its game is final |

`team_lineups` has no run_id, so each run overwrites the week's lineups; the draws, which price
everything, are kept per run. The accuracy and calibrate steps grade the latest run by creation time
(accuracy.py lines 109–124). They must grade the latest run with `n_locked = 0`, or the Thursday
players' known points flatter the model.

### 3.4 How much a Thursday game moves prices

The real Thursday game, with Judkins's score at five points of his range:

| Judkins scores | Team 4 beats team 1 | Team 4 over 107.70 | Team 4's new line | 1 + 4 over 218.07 |
|---|---|---|---|---|
| Before the game | 48.9% | 50.0% | 107.70 | 50.0% |
| 2.51 (1 game in 10 is worse) | 38.2% | 34.7% | 99.33 | 39.3% |
| 6.29 (1 in 4 is worse) | 43.0% | 41.4% | 103.12 | 44.0% |
| 9.72 (his median) | 47.4% | 47.8% | 106.54 | 48.5% |
| 14.19 (1 in 4 is better) | 53.1% | 56.4% | 111.02 | 54.4% |
| 19.61 (1 in 10 is better) | 60.4% | 66.8% | 116.43 | 61.3% |

The real Thursday game has one league starter; every other week-4 game has five to nine. Treating
each week-4 game in turn as the Thursday game, with 200 possible outcomes each, the moneylines of
matchups with a starter in that game moved by (in percentage points: a move from 48.9% to 38.2% is
10.7 points):

| Game | League starters | Matchups affected | Mean move | 1 in 10 moves exceed | Largest |
|---|---|---|---|---|---|
| PIT at CLE (the real one) | 1 | 1 | 6.5 | 12.9 | 38.3 |
| IND at WAS | 6 | 4 | 8.1 | 17.4 | 53.6 |
| TEN at BAL | 5 | 3 | 9.4 | 19.4 | 55.4 |
| NE at BUF | 8 | 4 | 10.3 | 23.5 | 55.9 |
| NYJ at CHI | 7 | 5 | 7.3 | 15.4 | 58.8 |
| JAX at CIN | 9 | 5 | 9.1 | 20.5 | 44.1 |
| DAL at HOU | 9 | 5 | 8.5 | 18.5 | 50.8 |
| ARI at NYG | 6 | 4 | 9.2 | 19.7 | 45.8 |
| LAR at PHI | 8 | 4 | 8.8 | 17.8 | 49.2 |
| GB at TB | 7 | 5 | 7.6 | 15.1 | 52.8 |
| MIA at MIN | 5 | 3 | 8.4 | 17.5 | 45.7 |
| KC at LV | 6 | 2 | 13.0 | 25.6 | 63.6 |
| LAC at SEA | 9 | 4 | 9.7 | 19.1 | 66.1 |
| DEN at SF | 7 | 4 | 8.1 | 18.3 | 34.0 |
| DET at CAR | 7 | 5 | 8.6 | 17.7 | 41.5 |
| ATL at NO | 8 | 4 | 9.9 | 21.1 | 49.1 |

Over all 12,400 moves the mean is 8.8 points, the median 6.8, one in ten exceeds 18.9, and 33.9%
exceed 10 points. Team lines of teams with a starter in the game move 6.4 points on average (median
4.8; one in ten beyond 13.2). An NFL team has 3.7 league starters on average this week, 6 at most.
A typical week-4 game touches four of the six matchups, and a third of those moves exceed 10
points. If Thursday games look like that, Wednesday's prices are stale by Friday for most bets,
which is why cash-out and removal need the rerun and the windows.

### 3.5 Team lines that move

The Friday run re-centres each team line at its new median, at even money. Bets placed before keep
their own line and price: a Wednesday over at 107.70 on team 4 stands at 34.7% after a 2.51-point
Judkins game and settles at 107.70. Runner-up: keep Wednesday's lines and re-price both sides. What
would change my mind: owners finding the moving lines confusing.

### 3.6 Windows

A *window* is a stretch of time in which one run's odds are open for betting. Week 4:

| Window | Opens | Closes | Odds from |
|---|---|---|---|
| 1 | Wednesday's publish | Thu Oct 1, 8:15pm ET (PIT at CLE) | Wednesday's run |
| Paused | Thursday's kickoff | Friday's publish | – |
| 2 | Friday's publish | Sun Oct 4, 9:30am ET (IND at WAS, 6 league starters) | Friday's run, then Saturday's |
| Closed | Sunday 9:30am ET | settlement | – |

The brief's example closes at Sunday 1pm, which would leave the 9:30am game and its 6 starters
open. A window closes at the next kickoff of any game: owners can change starters until kickoff,
and a live game leaks its result.

A new bet is accepted only if:

1. the week's `BettingPeriod` exists and is neither locked nor settled;
2. now is before the latest published run's `window_closes_at`;
3. the run the page showed is still the latest; otherwise Flask refuses and shows the new price.

`lock_time` stays as the admin's backstop and kill switch. Windows must not use the lazy lock:
`check_betting_period_lock` sets `is_locked` for good once `lock_time` passes (helpers.py line 87),
which would keep window 2 shut. With the laptop off, no Friday run is published, the latest run's
window has closed, and betting stays closed, which is the safe default. A Sunday-night run for
Monday's game would use the same machinery; it is not proposed now.

### 3.7 What the user sees

- A banner: "Odds updated Fri 1:10am ET after Thursday's game. Betting closes Sun 9:30am ET."
- Between Thursday's kickoff and Friday's publish: "Betting paused until the odds update after
  Thursday's game."
- Locked players with their final points.
- Each open bet with its own line and price next to today's, and its cash-out offer.

### 3.8 The Saturday inactives run

It is the full default run inside window 2. It replaces Friday's odds; bets placed on Friday keep
Friday's prices. Hypothesis: on Saturday, sources may drop players who already played on Thursday,
or list their actual points as projections. B5 must pin locked starters even when no source
projects them, and ignore projections for locked players. Test: scrape on the Saturday of week 5
and count Thursday players with no projection, or with a projection equal to their actual points.

**Recommendation.** Friday and Saturday reruns with finished players locked at their league points
and the owners' kicked-off starters pinned; two windows a week set by the runs themselves, with
`lock_time` as the kill switch.

**Runner-up.** Windows the admin sets by hand, two lock times a week: simpler, but a forgotten
change leaves betting open during a game. What would change my mind: if the rerun proves
unreliable in weeks 5 and 6, fall back to one window closing at Thursday's kickoff.

**Implementation notes (B6, 2026-09-28).** `app/windows.py` computes a week's window in the order
of the acceptance rules above: no period or a settled one is closed; the admin's lock (checked
through the lazy `check_betting_period_lock`, its one remaining caller) is closed with the lock
time; otherwise the latest `simulation_runs` row for the week decides, open before its
`window_closes_at` and paused after, and closed when the run has no window. Only the lock
flips `is_locked`; a paused window never does, so the Friday publish reopens betting on its
own. `place_bet` and `remove_bet` refuse outside an open window, `removable` needs the window
open as well as the bet's run still the latest, and `GET /api/betting_window` reports the
state for the page and the admin banner. The banner shows the times in the visitor's own zone
rather than ET, and the app reads whatever `window_closes_at` the pipeline set rather than
computing kickoffs. The locked players with their final points (§3.7) wait for B5's `n_locked`
and the lineup endpoint. Built by a Claude Code cloud session from
`docs/briefs/b6-betting-windows.md`; merged 2026-09-28 (31db13e), 825 tests.

**Implementation notes (B5, 2026-09-30).** A player is locked when `nfl_schedules.status` is
`STATUS_FINAL` for his team in the week, as §3.1 says. For a final game the lineups step pins the
owner's actual starters from `matchups.starters`, in slot order, at their league points
(`matchups.players_points`, 0 when missing), even when no source projects them any more, which
answers §3.8; his other rostered players from that game cannot enter the lineup and are labelled
`played` in `projections_rosters.roster_status`, and the model fills every other slot as before.
`team_lineups` gains `is_locked` and `locked_points` (NULL unless locked); a locked row has μ at
its points and σ and variance 0, and the step's summary counts `n_locked`. The simulate step reads
the locked rows and fixes those starters at their points in every simulation, recording the count
in `simulation_runs.n_locked`; it still draws the normals for every column and overwrites only the
locked ones, so every other starter's draws are bit-identical to an unlocked run. The owner
changed two things. A game in progress at run time does not stop the run, as §3.1 had it: its
players are simulated as unplayed and the step warns, naming the game. And the accuracy step,
which grades the week's latest run with `n_locked = 0` (Wednesday's) and reads that run's curves
and moneylines, takes each team's projected total from that run's `team_distribution_curves.mean`,
because a rerun that pins real points overwrites `team_projections_summary`; a week with only
locked runs has its players graded and its teams not, with a warning. Calibrate follows. The
lineup APIs (`/api/lineup/<owner>`, `/api/team_players`) return `is_locked` and `locked_points`
per starter, and the lineup columns on the betting and analytics pages mark a locked player with
his final points (§3.7). Those endpoints select the two new columns, so the lineups step runs and
publishes from this code before the app restarts; until then they return empty lists, as the
`sleeper_leagues` note in CLAUDE.md already requires of a publish. The runbook corrections of §3.2 were made in WP9: Friday runs
`--steps league,lineups,simulate,odds,playoffs,validate` and Saturday the full default run; B5
removes the runbook's caveat that played games are not pinned.

---

## 4. Other bet types

A *spread* bet picks a team to win by more than a set margin, or lose by less. Week-4 spreads at
the simulated median margin (`spreads_week4.py`):

| Matchup | Favourite by | Favourite wins | Margin under 10 points |
|---|---|---|---|
| 1 v 4 | team 1 by 0.96 | 51.1% | 24.8% |
| 2 v 5 | team 5 by 11.09 | 63.4% | 23.0% |
| 3 v 6 | team 6 by 5.68 | 57.3% | 24.8% |
| 7 v 8 | team 7 by 12.03 | 64.1% | 22.5% |
| 9 v 12 | team 12 by 18.02 | 72.3% | 21.6% |
| 10 v 11 | team 11 by 9.88 | 62.5% | 24.4% |

At the median each side covers exactly 50%, and a margin lands exactly on the line, to the cent,
in 0.006% to 0.020% of sims.

Ranked by value for effort:

| Rank | Bet | Needs | Effort | Evidence |
|---|---|---|---|---|
| 1 | Matchup totals | publish `betting_odds_matchup_ou` (computed, not published today); round lines to cents | small | overs hit 50.0% in 2025 |
| 2 | Head to head between any two teams | nothing new: `team_matchup_margin_curves`, published and used by Flask's team comparison, already holds every ordered pair's win chance | small | the moneyline's rule |
| 3 | Spreads | lines in half-point steps inside ±40 from the same published margin curves; any other line from the score matrix (B3) | small | covered 50.0% in 2025 |
| 4 | Alternate totals and spreads (other lines at other prices) | the score matrix | small after B3 | – |
| 5 | Last place | publish `standings_probability_matrix` | small | 5 of 12 teams priceable; team 9 at -361 |
| 6 | Top 3 or top 4 | the same table | small | 11 of 12 priceable |
| 7 | Exact seed | the same table | small | 114 of 144 priceable |
| 8 | Margin buckets (wins by 0–10, 10–20 and so on) | the score matrix | small | overlaps spreads; skip unless asked |
| 9 | Season points | a new playoffs output: each team's season total per sim | medium | – |
| 10 | Player props | player draws or precomputed prices | large | see below |

Ranks 1 to 4 add bets every week; the first three need no score matrix. The margin curves
(`margin_curves`, pipeline/steps/odds.py line 334) are counted straight from the sims: for every
ordered pair of teams, the chance of winning by more than each margin from 0 to 40, and of losing by
at least each margin from 0 to 40, in half-point steps. They are keyed by owner name, so B10 maps
names to roster ids. Ranks 5 to 7 come from a table the playoffs step already writes
(`seed_prices.py`); "priceable" means within ±5000 (§5).

Last place in week 4:

| Team | 9 | 10 | 4 | 12 | 2 | 3 | 1 | 11 | 6 | 5 | 7 | 8 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Last place | 78.3% | 7.8% | 6.0% | 3.2% | 2.1% | 1.5% | 0.3% | 0.3% | 0.3% | 0.1% | 0.1% | 0.02% |

**Player props** are overs and unders on one player's points. Storing player draws costs 21.6 MB
per run (19.4 MB compressed, 11.5 MB if rounded to cents first), about 1 GB a season: too big to
cache per worker. Regenerating is cheap: the simulate code reproduces every starter's draws bit for
bit from the seed in 0.65 s. So the odds step should price props itself and publish about 108 rows
a run, one line per starter at even money. Props stay singles: no parlays and no cash-out.

Whether props can be trusted, for 2025 weeks 10–16 held out (`validate_2025.py`; doc 03 for the
80% range; `explore_scoring.py` for the scoring gap):

| Position | Actual inside the 80% range (doc 03, starters) | Started players under their median, league points (starts) | League minus PPR per start, mean |
|---|---|---|---|
| QB | 77.9% | 54.8% (84) | -0.76 |
| RB | 79.6% | 48.7% (197) | 0 |
| WR | 76.4% | 50.9% (216) | 0 |
| TE | 80.5% | 49.4% (87) | 0 |
| K | 83.7% | 36.6% (82) | 0 |
| DEF | 81.3% | 44.0% (84) | +0.29 |

A prop at the median should lose half the time. RB, WR and TE do, within noise. QBs land under
54.8% of the time, within noise, in the direction of the league scoring them 0.76 points below the
PPR points the model is fitted on (an interception costs one point more). Defenses land under
44.0%, also within noise, in the direction of the league scoring them 0.29 points above PPR.
Started kickers land under only 36.6%, about 2.4 standard errors from half, with identical scoring.

**Recommendation.** Matchup totals, head to head, spreads with alternate lines, then last place.
Props last: RB, WR and TE first, then QB and DEF after a league-scoring fit, and K after a kicker
check (§8).

**Runner-up.** Futures first (last place, top 3, exact seed): one table the playoffs step already
writes serves all three. What would change my mind: owners asking for season-long bets more than
weekly ones.

---

## 5. Vig and clamps

*Vig* is the house's cut. It is usually set as an *overround*: the chances implied by a market's
prices add up to more than 100%. At a 5% overround an even bet is priced as if each side had a
52.5% chance, about -110. A *clamp* caps a chance before it becomes a price; today every chance is
clamped to between 0.1% and 99.9%, so prices run up to +99900.

| Policy | What it does | Effect on the leaderboard |
|---|---|---|
| Fair, clamped at ±99900 (today) | Any chance down to 0.1% is priced | $500 at +99900 wins $499,500; one long shot can decide the season |
| 5% overround | An even bet becomes -110.5 and wins $90.48 per $100 | Every bet loses $4.76 per $100 on average, a 3-leg parlay $13.62; $1,000 a week for 14 weeks costs $667; the leaderboard rewards whoever bets least |
| Fair, prices clipped at ±5000 | A long shot shows +5000 whatever its chance | Hidden mispricing: team 2 for first place (1.2%; a market until B12) returns $62.73 per $100; a 99% favourite at -5000 returns $100.98 |
| Fair, refused beyond ±5000 (recommended) | Chances under 1/51 (about 2%) or over 50/51 are not offered | The biggest win on $500 is $25,000; every offered price rests on at least about 980 sims |

Why ±5000. A chance of 1/51 rests on about 980 of 50,000 sims, so it is uncertain by about 3% of
itself; at today's 0.1% clamp it rests on 50 sims and is uncertain by about 14%. Futures use 20,000
sims, where 1/51 is 392 sims (about 5%). The cap also fits parlays: four coin-flip legs price at
+1500 and five at +3100, while six (+6300) would be refused.

Week-4 markets it removes: team 9 to be highest scorer (1.6%, +6237), team 7 to be lowest scorer
(1.7%, +5621), team 2 for first place (1.2%, +8030; a market until B12), team 9 to make the playoffs (1.5%, +6589), 7
of 12 last-place picks and 29 of 144 exact seeds.

**Recommendation.** Fair prices everywhere, and refuse any price beyond ±5000 instead of clamping
or clipping. A margin makes the house the only sure winner and turns the leaderboard into a
contest of who bets least; the clamp at +99900 lets one lucky long shot decide the season.

**Runner-up.** Refuse beyond ±9900 instead (a 1% chance, about 500 sims). What would change my
mind: owners wanting long shots enough to accept noisier prices. If the owner wants a house edge,
put 5% on parlays only and keep singles fair.

---

## 6. Settlement edge cases

Today the admin settles each bet by hand as won or lost (`settle_bet`, admin.py line 137). There
is no push, void or refund, and the "already settled" check reads the status before writing it, so
two requests at once can both pay. A *push* returns the stake because the result landed exactly
on the line; a *void* returns it because the bet should not stand.

| Case | How often (week 4, scores in cents) | Today | Proposed |
|---|---|---|---|
| Moneyline tie | 0.006% to 0.022% per matchup; about 1% for any game in a season | The admin must pick won or lost | Push |
| Team total on the line | 0.018% per team on average | The admin must pick | Push |
| Matchup total on the line | practically never, because lines are stored unrounded (218.07276916503906) | – | Round lines to cents; push |
| Highest or lowest scorer shared | 0.02% and 0.028% | The admin must pick | Pay every tied team in full, as the pricing assumes |
| Parlay with a pushed or void leg | rare | no parlays | The leg drops out and the rest are re-priced (§1.6) |
| Cash-out, then the event | every cash-out | – | The bet closed at cash-out; the result changes nothing |
| A rerun removed the market | e.g. a futures row falls under 1% | – | The bet stands and settles on what happened; only cash-out is unavailable |
| A later run moved the line | every rerun | – | Settle at the bet's stored line and price |
| Admin void (a mistake, a game not played) | rare | no path | Void |

The team-total description prints the line with one decimal (betting.py line 440), so a line of
91.57 shows as 91.6: a score of 91.58 is over the real line but looks under the ticket's. Store the
line to cents in `bet_legs` and display two decimals.

For shared scorer titles, the runner-up is a dead heat (split the payout between tied teams, the
sportsbook rule). The pricing counts a shared top score as a win for every tied team, so paying in
full is what the price promised.

Settle from the league's team scores, published as `sleeper_matchups`. Publish replaces that table
whole and it has no run_id, so settlement needs a league fetch and publish after Monday night's
game. Hypothesis: stat corrections move a few starters by a point or two after Monday. Test: fetch
week 4's matchups on Tuesday morning and again on Friday, and compare `players_points`. Until the
answer is known, settle on Tuesday's fetch.

Proposed flow: an admin page lists each pending bet with the outcome `markets.py` computes from the
published scores (won, lost, push or void); the admin confirms in one click. Every balance change
is one statement:

```sql
UPDATE users SET account_balance = account_balance - :stake
WHERE id = :user_id AND account_balance >= :stake;         -- placing: exactly one row, or refuse

UPDATE bets SET status = :new_status, settled_at = now()
WHERE id = :bet_id AND status = 'pending';                  -- settling: pay only if one row changed
```

Payouts, refunds and cash-outs add with `account_balance + :amount` the same way, and `total_pnl`
and `weekly_stats` follow the same pattern. The second statement also stops a double settlement.

**Recommendation.** Settlement by market key from the published league scores, with push and void,
through an admin preview, on atomic updates.

**Runner-up.** Manual settlement as today, with push and void buttons added. What would change my
mind: B7 slipping past the first parlay release, since parlay settlement by hand is error-prone.

**Implementation notes (B7, 2026-09-28).** `app/settlement.py` judges every keyed bet through the
win rules in `pipeline/markets.py` on a one-row score matrix, the week's played scores in sorted
roster order, so a market settles by the rule that priced it. Nothing is rounded: Sleeper's scores
and the app's lines both carry two decimals, and the same two-decimal value always reads back as
the same float, so a score on its line pushes. A roster with 0 points counts as not yet played and
its bets stay undecided, as do futures and legless legacy bets, which the admin settles by hand.
The admin page previews the week's scores and each bet's outcome, then settles the week in one
confirmation; a bet settles only when its recomputed outcome matches the one previewed. Void
closes a bet that should not stand and refunds the stake. The stat-correction hypothesis above is
still untested; week 4 is the first chance to compare Tuesday's and Friday's fetches.

---

## 7. Validation on 2025 weeks 10–16

### 7.1 Method

The evaluate tooling (`pipeline/model/evaluate.py`) refitted v2.1 seven times, each time without
the week being tested, with FantasyPros left out as v2.1's parameters record. Each week's modelled
lineups were simulated 50,000 times with seed 1738, and every bet was settled on the league's real
team scores (`matchups.points`). That gives 42 games and 84 team scores (`validate_2025.py`). A
*95% range* below comes from resampling the seven weeks 2,000 times: the true value is very likely
inside it.

### 7.2 Singles

| Check | Result | Fair would be |
|---|---|---|
| Team overs that hit | 48.8% (95% range 39.3%–59.5%) | 50% |
| Actual minus simulated mean | +2.45 points (-2.82 to +7.07) | 0 |
| Matchup overs that hit | 50.0% | 50% |
| Spreads at the median covered, lower roster id side | 50.0% | 50% |
| Spreads at the median covered, favourite side | 50.0% | 50% |

Share of actual scores below each simulated *quantile* (the q25 is the score 25% of sims fall
below):

| Actual below the simulated | q10 | q25 | q50 | q75 | q90 |
|---|---|---|---|---|---|
| Team scores (84) | 6.0% | 25.0% | 51.2% | 69.0% | 85.7% |
| Matchup totals (42) | 7.1% | 33.3% | 50.0% | 66.7% | 81.0% |
| Margins (42) | 9.5% | 23.8% | 50.0% | 71.4% | 85.7% |

With 84 scores, one standard error is 3.3 percentage points at q10 and q90 and 5.5 at the median;
with 42 games, 4.6 and 7.7. Every cell is within about two standard errors of its target.

Moneyline favourites:

| Stated chance | Games | Stated on average | Won |
|---|---|---|---|
| 50–60% | 26 | 54.9% | 57.7% |
| 60–70% | 11 | 62.0% | 63.6% |
| 70–80% | 5 | 74.0% | 80.0% |
| All | 42 | 59.1% | 61.9% |

### 7.3 Two-leg parlays

Every 2-leg parlay the week offered from moneylines and team totals: 4,284 in all, 3,780 joining
different games and 504 from one game (4 pairs of totals and 8 moneyline-and-total pairs per game).
*Calibration* asks whether things stated at 25% happen 25% of the time.

Grouped by the joint price:

| Stated chance | Parlays | Stated on average | Happened |
|---|---|---|---|
| up to 10% | 60 | 7.4% | 8.3% |
| 10–20% | 513 | 16.2% | 15.6% |
| 20–30% | 3,146 | 25.0% | 25.0% |
| 30–40% | 493 | 33.7% | 34.3% |
| 40–60% | 72 | 43.0% | 43.1% |

Grouped by the product price:

| Stated chance | Parlays | Stated on average | Happened |
|---|---|---|---|
| up to 10% | 6 | 9.2% | 0.0% |
| 10–20% | 493 | 16.8% | 15.2% |
| 20–30% | 3,293 | 25.0% | 25.1% |
| 30–40% | 475 | 33.2% | 34.1% |
| 40–60% | 17 | 43.8% | 52.9% |

Both look fine overall, because 88% of these parlays join different games, where the two prices
agree. The difference is in same-game pairs:

| Kind | Parlays | Joint says | Product says | Happened |
|---|---|---|---|---|
| Both totals of one game | 168 | 25.0% | 25.0% | 25.0% |
| Different games | 3,780 | 25.0% | 25.0% | 25.0% |
| A team wins, its opponent goes over | 84 | 13.2% | 25.0% | 14.3% |
| A team wins, its opponent goes under | 84 | 36.8% | 25.0% | 35.7% |
| A team wins and goes over | 84 | 36.8% | 25.0% | 34.5% |
| A team wins and goes under | 84 | 13.2% | 25.0% | 15.5% |

Winners went over their own line in 29 of 42 games (69%); the product assumes 50%, and the model
said 73.6%. Chart `docs/design/images/r2_parlay_calibration_2025.png` shows the four moneyline-and-total kinds as
grouped bars: the product's chance (grey), the joint chance (blue) and what happened (orange). The
orange bars sit beside the blue ones.

The *Brier score* is the average squared gap between a stated chance and what happened (1 or 0);
lower is better. The product's score minus the joint's:

| Parlays | Product minus joint | 95% range |
|---|---|---|
| All 4,284 | +0.0007 | about 0 to +0.0016 |
| Same game, 504 | +0.0066 | -0.0002 to +0.0141 |
| Moneyline and total, same game, 336 | +0.0100 | -0.0002 to +0.0213 |

All three favour the joint price, and all three ranges touch zero.

### 7.4 How team scores move together, week 4

*Correlation* runs from -1 to 1 and says how much two scores rise and fall together; 0 means
unrelated. With 50,000 sims, noise alone gives correlations of about ±0.0045. The model links
players of one NFL team (QB with WR 0.22, QB with TE 0.22, QB with RB 0.07, RB with WR -0.05), so
two fantasy rosters that split an NFL team's QB and receivers move together a little
(`correlation_links.py`):

| Teams | Correlation | Linked players |
|---|---|---|
| 3 and 10 | 0.059 | 3 has BUF's QB, 10 a BUF WR; 3 has a NO WR, 10 NO's QB |
| 7 and 9 | 0.047 | 7 has DAL's QB, 9 a DAL WR; 7 has a BAL WR, 9 BAL's QB; 7 has a NO RB, 9 a NO WR (a small negative link) |
| 8 and 10 | 0.036 | 8 has CIN's QB, 10 a CIN WR |
| 3 and 8 | 0.035 | 3 has BUF's QB, 8 a BUF TE |
| 5 and 11 | 0.031 | 5 has SF's QB, 11 an SF TE |
| 2 and 9 | 0.026 | 2 has a BAL TE, 9 BAL's QB |
| 6 and 8 | 0.025 | 6 has a CIN WR, 8 CIN's QB |
| 11 and 12 | 0.024 | 11 has CAR's QB, 12 a CAR WR |

| Sum of links between the two rosters | Pairs | Mean correlation | Range |
|---|---|---|---|
| Negative (an RB and a WR of one NFL team) | 13 | -0.007 | -0.015 to -0.003 |
| None | 28 | 0.000 | -0.007 to 0.008 |
| 0.07 to 0.20 | 14 | 0.009 | -0.007 to 0.024 |
| 0.22 to 0.29 | 9 | 0.027 | 0.018 to 0.036 |
| 0.30 or more | 2 | 0.053 | 0.047 to 0.059 |

The links explain nearly everything: across the 66 pairs, the sum of links and the correlation
agree at 0.95 on a scale where 1 is perfect. Fantasy opponents sit near 0 (-0.006 to +0.008). CHI,
CIN, DAL, NO and MIN each have starters on five rosters this week. This is where the product's
cross-matchup gaps of up to 4.1% come from; the joint price includes them for free.

### 7.5 How much this proves

Seven weeks of six games is small. The 4,284 parlays reuse the same 84 scores, so they are not
4,284 independent tests. What 2025 shows is the direction, and it matches the mechanics of week 4:
a team that wins has usually scored a lot. The size of the joint price's advantage is not pinned
down.

**Recommendation.** Price parlays from the joint chance; add the same-game parlay table to the
calibrate step and read it again after 2026 week 10.

**Runner-up.** Offer only legs from different matchups until 2026 data confirms the same-game
prices. It ranks second because the direction is certain and the product is off by up to 74% on
exactly these parlays. What would change my mind: 2026 same-game parlays settling closer to the
product than to the joint price by week 10.

---

## 8. Model ideas

Each is a hypothesis with a test; none is needed for the bets above.

1. **QB and the defense he faces.** In 2026 weeks 1–3 a QB's points and the opposing defense's move
   strongly against each other: correlation -0.45 after removing each player's own average (95%
   range -0.60 to -0.26, 87 pairs), -0.56 raw (`qb_def_2026.py`). The model has no such link. It
   barely matters for these bets: in week 4 no QB faces a defense on his fantasy opponent's roster.
   A link of -0.4 would move no moneyline by more than 0.15 percentage points, narrow team 3's range
   of scores (Josh Allen and the NE defense) by about 3%, and lower the chance that two linked
   rosters both go over by up to 2.8% of itself. Test: refit with the link on 2025 plus 2026 weeks
   1–6 and compare the gate's scores. Low priority.
2. **Opposing QBs.** Correlation +0.23 after own averages (95% range -0.11 to +0.47, 42 pairs):
   shootouts may lift both. Not established. In week 4 only teams 1 and 8 hold opposing QBs
   (Lawrence and Burrow); a link of +0.2 would raise their chance of both going over by 2% of
   itself. Test: the same refit.
3. **Vegas totals.** A game's betting total says how many points the NFL game should produce and
   could scale its players' projections. We would not scrape it from sportsbooks. ESPN's scoreboard,
   which the league step already reads, may carry it (not checked: this study had no network), and
   whether reading it there is acceptable is the owner's call. Test: attach 2025 game totals to the
   backtest and check whether the model's misses by NFL game follow them.
4. **A thin upper tail.** 14.3% of 2025 team scores beat the simulated q90, where 10% is expected
   (about 1.3 standard errors), and actual scores ran 2.45 points above the simulated mean (95% range
   -2.82 to +7.07). Test: repeat on 2026 weeks 4–10; if more than 13% again beat q90, widen the
   upper tail.
5. **League scoring.** The model is fitted on PPR points but bets settle on league points: in 2025
   weeks 10–16, started QBs scored 0.76 less on average and started defenses 0.29 more, about -0.48
   per team-week (`explore_scoring.py`). The model floors a defense at -5 (doc 03), yet the
   league's rules allow as low as -11 (-4 for allowing 35 or more points, -7 for 550 or more yards),
   and 2025 had a -6 (DET, week 15). Test: refit on `matchups.players_points` and compare the gate.
6. **Kickers.** Started kickers beat their median 63.4% of the time in 2025 (82 starts, about 2.4
   standard errors from half). Hypothesis: sources undersell good kickers. Test: 2026 weeks 1–8; if
   still above 58%, add a kicker bias for high projections.

**Recommendation.** None of these before the new bets ship. First the league-scoring fit (idea 5):
props depend on it, and it touches every QB and defense in every bet. Then run the tests of ideas
4 and 6 as their 2026 weeks come in.

**Runner-up.** The QB and opposing-defense link first (idea 1), the strongest link measured, though
it moves week-4 moneylines by at most 0.15 percentage points. What would change my mind: a week
in which a QB faces a defense on his fantasy opponent's roster and the link moves that moneyline
by more than a point.

---

## 9. Work packages

Sizes: small is a day, medium two to three days, large a week. The structured market key comes
first, because every later package stores or reads it.

| # | Package | Side | Size | Needs | Doc-08 open issues |
|---|---|---|---|---|---|
| B1 | Structured bets and server pricing | Flask | large | – | closes 1 and 2 |
| B2 | Atomic balance changes | Flask | small | – | closes 3 |
| B3 | Score matrix and `markets.py` | pipeline | medium | – | – |
| B4 | Publish additions and run columns | pipeline | small | – | – |
| B5 | Locked rerun | pipeline | medium | B4 | – |
| B6 | Betting windows | Flask | medium | B1, B4; ships with B5 | – |
| B7 | Settlement from league scores | Flask | medium | B1, B2 | uses B2's pattern |
| B8 | Parlays | Flask | large | B1, B3, B7 | uses B2's pattern |
| B9 | Cash-out | Flask | medium | B1, B3, B4, B6 | uses B2's pattern |
| B10 | New singles | Flask | small each | B1; B4 for some, B3 for alternate lines | – |
| B11 | Player props | both | large, later | a league-scoring fit | – |

- **B1.** Every bet gets legs with a market key and selection; Flask prices every single from the
  published tables by key and never reads a price from the browser; unknown keys, selections and
  negative indexes are rejected (today only `idx >= len` is checked, betting.py lines 430 and 476);
  first place and make playoffs get a page. Driving risk: six bet types and their page code change
  at once, and bets placed under the old scheme must still settle. Shipped 2026-09-28 (merge
  4234525).
- **B2.** Conditional updates for placing, removing and settling, with concurrency tests. Driving
  risk: it touches every money path at once. Shipped 2026-09-28 (13c1cba, merge 9554aaa).
- **B3.** `pipeline/markets.py`, the odds step moved onto it, and `simulation_totals` written at
  publish. Driving risk: the refactored odds step must reproduce the frozen week-4 tables exactly.
  Shipped 2026-09-28 (merge 669368d); the rebuilt step reproduced the week-4 tables cell for cell.
- **B4.** Publish `betting_odds_matchup_ou` and `standings_probability_matrix`, round matchup lines
  to cents, and add `n_locked`, `window_closes_at` and `standings_through_week` to
  `simulation_runs`. Driving risk: changing the staging-and-swap set. Shipped 2026-09-28 (merge
  669368d).
- **B5.** Locks from final games at league points, owners' kicked-off starters pinned, refusal
  while a game is in progress, accuracy and calibrate on `n_locked = 0`, and the runbook
  corrections of §3.2. Driving risk: pinning edge cases (a pinned starter no source projects, an
  empty slot) and sources dropping players who have played. Shipped 2026-09-30 (merge 93da40d).
- **B6.** Acceptance by window, the paused banner, `lock_time` as a kill switch, `remove_bet`
  limited to the latest run. Driving risk: time zones, the lazy lock, and behaviour when the laptop
  is off. Shipped 2026-09-28 (merge 31db13e), ahead of B5: until the Friday rerun exists, the
  window pauses at Thursday's kickoff and stays paused for the week.
- **B7.** The admin preview, push and void, settlement by key. Driving risk: a wrongly computed
  outcome pays wrong money; stat corrections. Shipped 2026-09-28 (merge 68da2e4).
- **B8.** The parlay slip, the refusal rules with a refusal log, the per-worker matrix cache,
  parlay settlement. Driving risk: the page and the cache. Shipped 2026-09-29 (merge 9e43632);
  parlay cash-out followed 2026-09-29 (merge 022e9af); the by-hand admin buttons on parlays are
  open (doc 08). B12 gave them to all-futures parlays; weekly parlays still settle only from the
  preview.
- **B9.** Offers from the latest run for futures and weekly bets, and the week the profit posts to.
  Driving risk: futures and weekly bets reach their chances by different paths. Shipped
  2026-09-29 (merge 52aebf2); a playoffs-only rerun ends removal on that week's futures bets
  (doc 08), so rerun from `simulate` until the playoffs step stamps the simulation run it read.
- **B10.** Matchup totals, head to head, spreads, alternate lines, last place, top 3 or 4, exact
  seed. Head to head and half-point spreads need only B1; matchup totals and the futures need B4;
  alternate lines need B3. Scoped to spreads alone by decision 7. B10a shipped 2026-09-29 (merge
  b8965fe): half-point lines priced in the app from the score matrix, main line at the median
  margin, alternates within ten points, `GET /api/spreads`, a Spreads tab with a line picker;
  built by the cloud session from `docs/briefs/b10-spreads.md`. B10b shipped 2026-09-29 (merge
  09bcb71): last place and the champion, the champion priced on Sleeper's fixed bracket played on
  simulated playoff weeks, futures rows stamped with the simulation run they read, `sleeper_leagues`
  published, the standings futures settled from the final standings in the regular season's last
  week, the champion by hand, and a futures result posted to the week it settles in; built by the
  cloud session from `docs/briefs/b10-futures.md`. Top 4 is the existing make-playoffs market.
- **B11.** Props priced in the odds step (§4).
- **B12.** Futures as YES or NO, no first place, and futures parlays (§1.4). First place is gone
  from the pipeline's tables, the publish map, validation, the charts, the API and the page; make
  playoffs is priced on both sides for every team, the NO side at 1 − YES stored beside it in
  `betting_odds_make_playoffs`, a side at a chance of 0 or 1 left without odds; the playoffs step prices its three futures through
  `pipeline/markets.py` and stores each run's simulated seasons in `simulation_standings`, which
  publish appends like `simulation_totals` and validate checks; a slip holds weekly picks or futures
  picks, never both, and a futures slip is quoted, cashed out and settled on those seasons, a lost
  leg settling it at once and a champion leg leaving it to the admin after the final. Driving risk:
  the futures rows and the seasons a parlay is priced on drifting apart when the playoffs step
  reruns after a publish. Built by the cloud session from `docs/briefs/b12-futures.md`. Shipped
  2026-10-01 (merge pending).

Doc 08's line references have moved: the browser's odds are read at betting.py lines 257, 300, 341
and 382, and the settlement balance update is at admin.py line 170.

| Release | Packages | What the owner gets |
|---|---|---|
| 1 | B1 and B2 (Flask); B3 and B4 (pipeline) | Server-priced bets, a futures page, matrices accumulating |
| 2 | B5, B6, B7 | The Friday window, settlement with pushes |
| 3 | B8, B9 | Parlays and cash-out |
| 4 | B10 | New singles |
| Later | B11 | Props |

**Recommendation.** The order above: structured bets and safe balances first, then the rerun with
windows and settlement, then parlays and cash-out.

**Runner-up.** Parlays before the rerun: release 2 becomes B7 and B8, release 3 B5, B6 and B9.
Parlays need no change to the weekly timing. What would change my mind: the owner valuing parlays
over the Friday window.

---

## 10. Evidence

The scripts live in the research scratch directory
(`C:/Users/SAMERF~1/AppData/Local/Temp/claude/C--Users-Samer-Faizi-Documents-Claude-Model/27e44c82-5c89-4120-ad21-e08d7614a476/scratchpad/research/r2/`),
each analysis script beside its printed output (`.out`). They read copies of the databases and of
the week-4 draws, never the originals.

| Script | Computes | Used in |
|---|---|---|
| `copy_data.py` | copies the databases and the week-4 draws into scratch | – |
| `explore.py` | a first look at the run, its odds tables and the futures tables | – |
| `explore_scoring.py` | the league's scoring rules, and league against PPR points per started player | §4, §8 |
| `explore_midweek.py` | mid-week table contents, game statuses, league against PPR points | §3.1 |
| `parlays_week4.py` | the 72 legs, joint against product for every combination, correlations, market pairs, sizes and timings, the ratio chart | §1, §7.4 |
| `worst_cases.py` | the most mispriced combinations, with sim counts and prices | §1.2 |
| `refusal_counts.py` | the three refusal rules over all 72 legs | §1.3 |
| `correlation_links.py` | correlations against shared NFL-team links | §7.4 |
| `push_rate.py` | pushes and ties with scores rounded to cents | §1.6, §6 |
| `float32_check.py` | float32 against float64 outcomes, timings | §1.5 |
| `thursday_lock.py` | bit-exact reproduction, the Judkins lock, each game as the Thursday game, prop sizes | §2, §3, §4 |
| `validate_2025.py` | the 2025 held-out checks and the calibration chart | §4, §7 |
| `qb_def_2026.py` | QB links in 2026 weeks 1–3 and their effect on week 4 | §8 |
| `seed_prices.py` | exact seed, top 3, top 4 and last place | §4, §5 |
| `spreads_week4.py` | week-4 spreads | §4 |

Charts, bar charts only, committed under `docs/design/images/`: `r2_parlay_ratio_distribution.png`
(§1.2) and `r2_parlay_calibration_2025.png` (§7.3).

Not verified:

| Claim | Why not | Test |
|---|---|---|
| The QB and opposing-defense link | 3 weeks of 2026 | Refit with 2026 weeks 1–6 (§8) |
| Real Thursday moves in 2025 | the 576 migrated 2025 schedule rows have no game status and no kickoff time | Compare week 4's Wednesday and Friday runs |
| Vegas totals on ESPN's scoreboard | no network | Read one scoreboard response |
| The gain from pinning owners' starters | needs lineups at kickoff | Pinned against modelled on week 4's Friday run |
| A real futures cash-out | one week-4 run exists | Week 5's run against week 4's |
| Props on league points | small samples (82–216 players) | Repeat on 2026 weeks 1–8 |
| Price drift between runs without news | one run per week so far | Wednesday against Saturday runs, excluding locked players |
| When stat corrections land | not observable in stored data | Tuesday against Friday fetches (§6) |
| How owners use cash-out | not built | The cash-out log after weeks 5 and 6 |
| The playoffs step's runtime on a rerun | network step, not run | Time it on the first Friday run |
| Postgres read time for a 2.1 MB row | no production access | Time it on the droplet |
| Disk room for 105 MB a season | no production access | `df -h` on the droplet |
| Gunicorn worker count and memory | not in the repository | Read the systemd unit |
| The admin's 2026 lock time | README line 75 describes 2025 | Ask the owner |

Decisions for the owner, taken 2026-09-28:

1. Pin owners' kicked-off starters (§3.1), which touches pipeline-v2's lineup-snapshot non-goal.
   Yes.
2. Fair prices refused beyond ±5000, or a house edge (§5). Neither: "No cap. No house edge. Trust
   the model." Read as: every price is the raw share of sims, with no clamp at 0.1% and no refusal
   at ±5000, so one sim in 50,000 prices at +4999900; a selection or combination that wins in no
   sim has no price and is not offered. Rules 1 and 2 of §1.3 stand.
3. `remove_bet` limited to the latest run's open window (§2.5). Yes: a bet can be removed for a
   full refund while the run that priced it is still the latest; once a later run has moved its
   odds, removal gives way to cash-out at 95% of fair value from the latest run (§2.1), kept
   below the full value so that holding a bet stays attractive. Removed rows are kept.
4. Reading game totals from ESPN, which are sportsbook numbers (§8). Open; no model idea is built
   before the new bets.
5. Whether 2026 still locks at Thursday's kickoff. Yes, and betting reopens once the Friday rerun
   has published (§3.6).
6. Scorer legs in parlays (§1.4). They stay; B8 logs refusals.

Decisions for the owner, taken 2026-09-29:

7. B10's scope. Spreads first, alone (§4 rank 3, with alternate lines from the score matrix).
   Matchup totals and head to head are not wanted now.
8. Last place (§4 rank 5) is decided by the regular-season standings, not by the playoffs, so the
   market settles when the regular season ends. There is no third-place finish in this league, so
   no top-3 market; top 4 (the playoff line) is the only "top N".
9. Parlays get cash-out at the joint chance of their legs (§2.1), shipped as a B9 follow-up
   (PR 10). Futures legs in parlays wait on per-sim standings from the playoffs step (§1.4).
10. The simulation count may come down from 50,000 if prices hold; the analysis to run first is
    recorded in doc 08's open issues.

---

## Glossary

- **95% range:** from resampling the data 2,000 times (whole weeks for the 2025 checks, whole games
  for §8); the true value is very likely inside it.
- **Alternate line:** an over, under or spread at a line other than the main one, at its own price.
- **American odds:** +150 means $100 wins $150; -150 means $150 wins $100; even (+100) means $100 wins $100.
- **Backtest:** pricing past weeks with a model that did not see them, then comparing with what happened.
- **Brier score:** the average squared gap between a stated chance and the result (1 or 0); lower is better.
- **Calibration:** whether things stated at a chance happen at that chance.
- **Cash-out:** closing a pending bet early for an amount offered now.
- **Clamp:** a cap on a chance before it becomes a price. The pipeline clamped to 0.1% to 99.9% until 2026-09-28, when the owner removed it (section 10, decision 2): a chance of exactly 0 or 1 now has no price and is not offered.
- **Correlation:** from -1 to 1, how much two numbers rise and fall together; 0 is unrelated.
- **Fair price:** a price with no house margin; at the model's chance the bettor expects the stake back.
- **Futures:** bets settled at the end of the season: make playoffs (yes or no), last place and champion.
- **Gate:** the held-out checks a refitted model is scored on before it becomes the default
  (`pipeline/model/evaluate.py`, doc 03).
- **Head to head:** a moneyline between any two teams, whether or not they play each other that week.
- **Highest (lowest) scorer:** a bet that a team posts the league's highest (lowest) score of the week.
- **Joint chance:** the share of sims in which every leg of a parlay wins.
- **Leg:** one single bet inside a parlay.
- **Line:** the number an over, under or spread is measured against.
- **Lock:** (1) a betting period's close (`lock_time`); (2) in a rerun, a player whose game is final,
  fixed at his real points.
- **Margin curve:** for two teams, the chance of winning by more than each margin, or losing by at
  least it, in half-point steps up to 40 (`team_matchup_margin_curves`).
- **Market, selection, market key:** one thing to bet on, the side picked, and the text naming the
  market (`2026-w04-moneyline-1v4`).
- **Matchup total:** over or under on both teams' combined score.
- **Median, quantile:** the q50 (median) is the score half the sims fall below; the q10, the score 10% fall below.
- **Moneyline:** a bet on who wins a matchup.
- **Overround (vig):** how far a market's implied chances add up past 100%; the house's cut.
- **Parlay:** one bet of several legs that wins only if every leg wins.
- **PPR:** points per reception, the standard scoring the model is fitted on.
- **Product price:** a parlay priced by multiplying its legs' chances.
- **Prop:** an over or under on one player's points.
- **Push:** a result exactly on the line; the stake is returned.
- **Ratio:** joint chance over product; what a product-priced parlay returns per $1 on average.
- **Run, sim:** one execution of the simulate step, and one of its 50,000 simulated weeks.
- **Same-game parlay:** a parlay with two or more legs from one matchup.
- **Score matrix:** a run's 50,000 × 12 table of team scores, one row per sim, published as
  `simulation_totals` since B3.
- **Seed:** (1) the random seed, 1738, that makes a run repeatable; (2) a team's final
  regular-season place, 1 to 12, which an *exact seed* bet picks.
- **Spread:** a bet that a team wins by more than a margin, or loses by less.
- **Standard error:** the typical wobble of a number measured from a limited sample.
- **Team total:** over or under on one team's score.
- **Void:** a bet cancelled and refunded.
- **Window:** a stretch of time in which one run's odds are open for betting.
