# TNCasino B12: futures as yes or no, no first place, and futures parlays

Written 2026-10-01 by the orchestrator session, after B5 (PR 21, merge 93da40d). For the cloud
session, which designs the change, writes its own engineer briefs, runs its engineers, pushes the
branch `b12-futures` cut from `main` and opens the pull request. The orchestrator merges.

## 1. Goal

The owner's three asks, in their words: "Remove first place bets entirely, make the 'make playoffs'
future be yes or no (should look like the Over/Under bet type and just say YES or NO). Also can we
do futures parlays? like only parlay futures with futures or current week w current week."

So after this package the Futures tab offers make playoffs (yes or no), last place and champion; a
make-playoffs card looks like a team-total card with YES and NO where Over and Under are; and a
parlay slip holds either two to four of the week's picks or two to four futures picks, never both.

## 2. What is known

- First place lives in `app/markets.py` (`SHAPES`, `QUOTE_SQL`), `app/settlement.py`
  (`STANDINGS_MARKETS`, `_standings_future`), `app/routes/betting.py` (the bet-type labels),
  `app/routes/odds.py` (`/api/first_place`), `betting.js` (kind `fp`), the playoffs step
  (`betting_odds_first_place`, `check_totals`, `print_futures`, the sort in `leaders`, the chart
  `first_place_race`), `publish.py` `TABLES`, `validate.py`, `legacy.py`, the docs and the tests.
  No page reads the `first_place_race` chart today. `standings_probability_matrix` already holds
  every team's chance at every rank, so nothing about the standings is lost when the market goes.
- Production holds two first-place bets, both from 2025 and both settled, and no pending futures
  bet of any kind. `app/migrations.py` renamed the 2025 `first_seed` type to `first_place`; those
  two rows keep that type and the account page must still label them. Market keys, parlays and
  cash-out are on `main` but not yet deployed to production, so no keyed first-place bet exists
  anywhere.
- Make playoffs is quoted with the single selection `yes` at the pipeline's `american_odds` and
  `probability` (`QUOTE_SQL`, `find_quote`); the page writes the key `2026-make_playoffs-<roster>`.
  `app/markets.py` has `odds_from_probability`, the fair American odds the app prices parlays and
  spreads with. The pipeline prices its futures rows in `pipeline/steps/playoffs.py` `market_rows`,
  which drops chances under 1% or over 99%.
- The team-total card is `renderTotalPicks` in `betting.js` with `tnc-ou-*` classes in
  `betting.css`; the futures card is `renderFuturePicks`, which already prints a "Yes" label on a
  make-playoffs pick. The site is mobile first; check a card at 375 px.
- Parlays (`app/parlays.py`, `app/matrices.py`) price a slip at the share of the run's sims in
  which every leg wins, on the score matrix the publish step appends to `simulation_totals` for
  each published run. `_leg` refuses any key without a week ("Futures cannot be parlayed").
  `docs/design/odds-models-2026.md` §1.4 explains why and says what would change the decision:
  owners asking for season-long parlays. They have. §1.4 also names the pieces: the playoffs step
  has, in memory, every simulated season's finishing positions (`final_standings`, 20,000 sims by
  12 teams) and its champion (`standings.play_bracket`), and `simulation_totals` is the precedent
  for keeping a run's matrix.
- Standings futures settle in the regular season's last week from the final standings
  (`outcomes_for_week`, the admin's settlement preview); the champion settles by hand after the
  final (`settle_bet`). A futures bet's cash-out comes from its latest quote
  (`app/cashout.py` `_futures_probability`) and is refused while the run's
  `standings_through_week` is behind. Both were written for singles because parlays refused
  futures. A futures result posts to the week it settles in.
- `main` at 28b2ad0: 1281 tests pass and one is skipped; ruff clean.

## 3. Your decisions

- How the NO side is priced and stored: in the pipeline's table, in the app from the YES
  probability, or both, and how the two sides stay consistent with the matrix a futures parlay is
  priced on. Whether `market_rows`' 1% and 99% cut-offs still make sense when every card has two
  sides.
- What the pipeline stores per published playoffs run so the app can price a futures parlay sim by
  sim (make playoffs yes or no, last place, champion), where it lives, how large it is, and how it
  reaches production. `simulation_totals` is the model: append-only, one row per run, decoded once
  per worker.
- The parlay rules for futures legs: which combinations are refused (two legs on one roster's
  make-playoffs market, a leg that adds nothing, a combination the sims never produce, and whatever
  else the standings imply, such as last place with champion for one roster), which run a futures
  parlay is priced at and tied to, and how the slip tells a bettor that weekly and futures picks do
  not mix.
- How a futures parlay settles when its legs decide at different times (standings legs in the
  regular season's last week, a champion leg after the final), what the admin sees, and what its
  cash-out offer is while the season runs.
- Which chart, doc and dashboard mentions of first place go, and what the design doc's §1.4 and
  §9 say afterwards.

## 4. What you can and cannot do

- No `.env`, no production, no publish. `DATABASE_URL`, if set to run the app, points at a scratch
  SQLite file. Tests never need it. Manual pipeline runs use a scratch data directory through
  `PIPELINE_DATA_DIR`; never commit a `.db` file. Aggregates only from RotoBaller and Fleaflicker.
  The repository is public.
- Keep every JSON field the page relies on and every published table's existing columns, except
  that `betting_odds_first_place` may go. A removed market's settled bets stay readable on the
  account page and the leaderboard.
- No cap and no house edge, on futures parlays as on weekly ones; cash-out stays at 95% of fair
  value. Money is a float in the schema and the API. Every SQL statement runs on PostgreSQL and
  SQLite.
- Quality bar as in `CLAUDE.md`: a function you rewrite ends shorter and plainer than you found
  it; no single-use helpers, no over-commenting, no defensive handling of impossible cases.
- `python -m pytest -q`, `python -m ruff check .` and `python -m ruff format --check .` clean.
  Say the test count after.

## 5. Acceptance

- No first-place market anywhere: not on the betting page, not in the API, not in the pipeline's
  tables, publish map, validation, charts or docs. The two settled 2025 first-place bets still show
  with a label on the account page.
- A make-playoffs card shows the owner's name with a YES and a NO button, each with its odds and
  chance, laid out like a team-total card at 375 px and at desktop width. Either side can be
  placed, cashed out and settled: YES wins inside the playoff line, NO wins outside it, from the
  final standings.
- A parlay of two to four futures legs quotes, places, shows on the slip, cashes out and settles,
  priced from the published playoffs run's own simulations. A slip that mixes a weekly pick with a
  futures pick is refused with a message the bettor understands, and weekly parlays behave as
  before.
- `simulation_totals` semantics unchanged; whatever the futures parlays are priced on is kept for
  every published run the same way.
- `docs/architecture/03`, `04`, `05`, `06` and the design doc describe the markets as they now are;
  §1.4 records the reversal and why.
- CI green on the branch. Pull request body: what the NO side is priced from, what is stored per
  run and how large it is, the futures parlay rules, how a futures parlay settles, and the deploy
  order (whether the app needs a publish from this code before it restarts).
