# Pipeline v2 — design and requirements

Status: approved for build on 2026-09-26. Must be live for week 4 by Wednesday 2026-09-30.
Owner: orchestrator session (staff engineer). Implementation: engineer agents, one work package each.

This document is the contract. Engineers implement their work package from it plus the files it
points at. Anything not specified here follows the notebook behaviour it replaces
(`backend/notebooks/*.ipynb`) and the quality bar in `CLAUDE.md`.

---

## 1. Goals and non-goals

Goals

- Replace the ten notebooks with an importable, testable `pipeline/` package and a CLI that a
  Claude agent runs weekly with no hand edits.
- Keep observability: every step records a machine-readable summary, and the Flask admin area
  shows the pipeline state for a week (`/admin/pipeline`).
- Fix the scrapers for 2026 and add checks that catch silent failures (wrong positions, stale
  pages, truncated tables), plus an explicit agent review verdict per source.
- Refit the projection model (v2): fitted sigma(mu, position), dud mixture, source weights,
  small correlation table, versioned parameters, calibration reporting.
- Model lineups from the real waiver wire for genuine holes only.
- Multi-week rest-of-season simulation for playoff odds using Sleeper, ESPN and FantasySharks
  future-week projections.
- Keep every table the Flask app reads shape-compatible so the website never breaks mid-week.
- Lay the groundwork for later betting features (parlays, cash-out, Thursday reruns): raw
  simulation draws are kept per run, odds are keyed by run, futures markets are produced every run.

Non-goals (after Wednesday)

- Flask betting-engine changes (parlay legs, structured markets, cash-out, multiple windows).
- Making Flask season-aware. Production tables hold the current season only, as in 2025.
- Modelling owner behaviour (lapse rates, lineup snapshots). Owners are trusted to set lineups.

---

## 2. Runtime model

Unchanged from today: the pipeline runs on the developer machine against local SQLite files and
publishes analytics tables into the production PostgreSQL that the Flask app reads. Flask is
always on; the pipeline is a program that runs once or twice a week.

```
python -m pipeline run --week 4              # every step except publish
python -m pipeline run --week 4 --steps scrape,clean,match
python -m pipeline run --week 4 --from stats  # stats and everything after it
python -m pipeline run --week 4 --steps publish
python -m pipeline status --week 4           # latest status of each step for the week
python -m pipeline review --week 4 --source espn.com --verdict ok --note "top 15 look right"
python -m pipeline fit-model --season 2025 --weeks 10-16 --out v2   # writes model params
```

Exit code 0 when every requested step finished with status `ok` or `warn`; 1 otherwise. The
runner stops at the first failed step.

---

## 3. Package layout and file ownership

```
pipeline/
  __init__.py
  __main__.py          CLI (argparse): run, status, review, fit-model, migrate-legacy
  settings.py          Settings dataclass + discovery from Sleeper + env overrides
  runner.py            step registry lookup, StepContext, StepResult, run records
  db.py                SQLite paths and connection helpers
  sources/
    __init__.py        SOURCE_NAMES and load_source(name) (lazy import by module name)
    base.py            Projection dataclass, ProjectionSource base class
    sleeper.py  espn.py  fantasysharks.py  fantasypros.py  firstdown.py  fanduel.py
    verify.py          per-source silent-failure checks -> SourceReport
    teams.py           NFL team code normalisation (all sources -> Sleeper codes)
  steps/
    __init__.py        STEP_ORDER, DEFAULT_STEPS, load_step(name) (lazy import)
    league.py scrape.py clean.py match.py stats.py accuracy.py calibrate.py lineups.py
    simulate.py odds.py playoffs.py validate.py publish.py
  model/
    __init__.py
    params.py          load_params(version) -> dict; path helpers
    params/v1.json     today's constants (alpha, beta, pos sigma, equal weights)
    params/v2.json     fitted parameters (WP6)
    sigma.py           sigma(mu, position, spread, params)
    sampling.py        joint player sampling: lognormal, dud mixture, copula
    fit.py             fitting from history (WP6)
  charts.py            matplotlib bar charts saved under backend/data/images/
tests/pipeline/        pytest, tmp_path SQLite, fixture payloads in tests/pipeline/fixtures/
```

Ownership is disjoint per work package (section 12). Shared files (`requirements.txt`,
`pipeline/steps/__init__.py`, `pipeline/sources/__init__.py`) are written by WP1 once and not
edited by other packages. Steps and sources are loaded lazily by module name so adding one never
touches the registry file.

Legacy code (`backend/notebooks/`, `backend/scrapers/`, `scripts/scrape.py`,
`scripts/validate_scraping.py`, `scripts/publish.py`, `tests/test_scrape.py`,
`tests/test_scrapers.py`) is deleted in WP9 after the new pipeline has produced a full week-4 run.
Until then it must keep passing CI; do not edit it.

---

## 4. Settings

`pipeline/settings.py` exposes `Settings` (frozen dataclass) and `load_settings(week=None, season=None)`.

Fields: `season: int`, `week: int`, `league_id: str`, `seed: int` (default 1738),
`n_sims: int` (default 50_000), `model_version: str` (default `"v1"` until WP6 flips it),
`data_dir: Path` (`backend/data`), `db_paths: dict[str, Path]` (league, projections, odds, pipeline),
`sims_dir: Path` (`backend/data/sims`), `images_dir: Path` (`backend/data/images`),
`sleeper_username: str`.

Discovery order

1. `season`: `--season` flag, else env `PIPELINE_SEASON`, else `GET https://api.sleeper.app/v1/state/nfl` → `season`.
2. `week`: `--week` flag (required for `run`), else the same state call's `week`.
3. `league_id`: env `PIPELINE_LEAGUE_ID`, else discover: resolve `SLEEPER_USERNAME` to a user id
   (`/v1/user/{username}`), list `/v1/user/{uid}/leagues/nfl/{season}`, and pick the league whose
   `previous_league_id` chain reaches env `LEAGUE_ID` (the 2025 league). Raise with the candidate
   list if none or several match. The 2026 league is `1387602586542018560`.
4. `model_version`: env `PIPELINE_MODEL_VERSION`, else the default.

Discovery results are cached per process. Tests inject settings directly; no network in tests.

---

## 5. Step contract and runner

```python
# pipeline/runner.py (shape; WP1 writes the real thing)
@dataclass
class StepContext:
    settings: Settings
    run_id: str
    options: dict          # CLI passthrough: sources, no_charts, etc.
    def db(self, name: str) -> sqlite3.Connection   # league | projections | odds | pipeline
    def log(self, message: str) -> None            # prints with a step prefix

@dataclass
class StepResult:
    summary: dict          # JSON-serialisable; shown on the dashboard
    warnings: list[str] = field(default_factory=list)
    charts: list[str] = field(default_factory=list)   # file names under images_dir
```

A step is a module in `pipeline/steps/` with `NAME: str` and `run(ctx: StepContext) -> StepResult`.
It raises (any exception) to fail. Status is `ok` when it returns with no warnings, `warn` when
warnings are non-empty, `failed` when it raises.

`STEP_ORDER = ["league", "scrape", "clean", "match", "stats", "accuracy", "calibrate", "lineups",
"simulate", "odds", "playoffs", "validate", "publish"]`. `DEFAULT_STEPS` is everything except
`publish`. Steps are idempotent for a (season, week): rerunning replaces that week's rows.

Summaries are small. Keys are snake_case; values are numbers, strings, short lists, or lists of
small dicts (rendered as tables on the dashboard). Never put raw DataFrames or thousands of rows
in a summary.

Run records live in `backend/data/databases/pipeline.db`:

```sql
CREATE TABLE pipeline_runs (
  run_id TEXT PRIMARY KEY,          -- "{season}w{week:02d}-{YYYYmmddTHHMMSS}"
  season INTEGER NOT NULL, week INTEGER NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,   -- running|ok|warn|failed
  steps TEXT NOT NULL,              -- JSON list of requested step names
  git_sha TEXT, error TEXT
);
CREATE TABLE pipeline_steps (
  run_id TEXT NOT NULL, step TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, duration_s REAL,
  status TEXT NOT NULL,             -- running|ok|warn|failed
  summary TEXT, warnings TEXT, charts TEXT, error TEXT,   -- JSON
  PRIMARY KEY (run_id, step)
);
CREATE TABLE source_reviews (
  season INTEGER NOT NULL, week INTEGER NOT NULL, source TEXT NOT NULL,
  verdict TEXT NOT NULL,            -- ok|reject
  note TEXT, reviewed_at TEXT NOT NULL,
  PRIMARY KEY (season, week, source)
);
```

Every CLI `run` creates one `pipeline_runs` row. The odds/simulation `run_id` equals the pipeline
`run_id` of the invocation that ran `simulate`. `status --week N` prints, per step in order, the
most recent `pipeline_steps` row for that season/week across runs. Timestamps are ISO 8601 UTC.

---

## 6. Data stores

Local SQLite under `backend/data/databases/` (gitignored):

| file | holds |
|---|---|
| `league.db` | leagues, users, rosters, matchups, nfl_players, nfl_schedules, player_stats, transactions, projections_rosters |
| `projections.db` | projections, projections_with_sleeper, player_week_stats, team_lineups, team_projections_summary, prediction_accuracy, team_accuracy |
| `odds.db` | simulation_runs, betting_odds_*, standings_probability_matrix, team_distribution_curves, team_matchup_margin_curves, calibration_metrics |
| `pipeline.db` | pipeline_runs, pipeline_steps, source_reviews |

Raw simulation draws go to Parquet, not SQLite:
`backend/data/sims/{season}/wk{week:02d}/{run_id}.parquet` with columns `sim_id int32`,
`roster_id int16`, `total_points float32` (50k × 12 rows). `montecarlo.db` is retired.

Typing rules for every table the pipeline creates: `season INTEGER` and `week INTEGER` columns,
never `"Week 4"` strings. The Sleeper mirror tables in `league.db` (`leagues`, `users`, `rosters`,
`matchups`, `nfl_players`, `transactions`, `player_stats`) keep their legacy DDL so the 2025 rows
already in the file stay readable; SQLite's TEXT affinity makes `season = 2026` match either way. WP3 ships a one-off `python -m pipeline migrate-legacy` that backs up the
three legacy files to `backend/data/databases/backup-2025/`, converts `"Week N"` to `N`, and adds
`season = 2025` where missing, so history is uniform for calibration.

Canonical position codes: `QB, RB, WR, TE, K, DEF` (sources say DST; convert on ingest).
Notebook 03 rewrote `DEF` to `DST` in every legacy table, so `migrate-legacy` converts `DST` back
to `DEF` wherever it finds it (`nfl_players`, `projections`, `projections_with_sleeper`,
`player_week_stats`, `team_lineups`, `projections_rosters`).
Canonical team codes: Sleeper's (`pipeline/sources/teams.py` maps JAC→JAX, WSH→WAS, LA→LAR,
GBP→GB, SFO→SF, KCC→KC, NEP→NE, NOS→NO, TBB→TB, LVR→LV, etc.).
Team identity is `roster_id` within a `league_id`; never key history by owner name (one owner
changed between 2025 and 2026).

### 6.1 Frozen tables (Flask contract)

The Flask routes query these by `week` only and read the columns below by name. Columns may be
added (`season`, `run_id`), never removed, renamed or retyped. Row semantics per week must match
today's notebooks exactly (one row set per week in production).

| production table | local source | columns Flask reads |
|---|---|---|
| betting_odds_matchup_ml | odds.db | week, matchup, team1_id, team1_name, team1_win_prob, team1_ml, team2_id, team2_name, team2_win_prob, team2_ml, ties (`SELECT *`) |
| betting_odds_team_ou | odds.db | week, team_id, team_name, owner, line, over_prob, over_odds, under_prob, under_odds, push_count (`SELECT *`) |
| betting_odds_highest_scorer, betting_odds_lowest_scorer | odds.db | week, owner, probability, odds |
| betting_odds_first_place, betting_odds_make_playoffs | odds.db | week, owner, probability, american_odds |
| team_distribution_curves | odds.db | week, owner, x_values, density_values, cdf_values, mean, p10, p50, p90 |
| team_matchup_margin_curves | odds.db | week, team_owner, opponent_owner, team_win_prob, opponent_win_prob, left_x_values, left_y_values, right_x_values, right_y_values |
| team_lineups | projections.db | roster_id, owner, week, slot (QB,RB1,RB2,WR1,WR2,TE,FLEX,K,DEF), player_name, position, mu, var |
| projections_rosters | league.db | roster_id, week, sleeper_player_id, first_name, last_name, position, mu, var, starting_status |
| sleeper_rosters (from rosters) | league.db | roster_id, league_id, owner_id, starters, players |
| sleeper_users (from users) | league.db | user_id, username, display_name |
| sleeper_matchups (from matchups) | league.db | league_id, week, roster_id, matchup_id_number, points |

`owner` in the odds/lineup tables must be populated the way notebooks 06/07 populate it (Flask
resolves owners by `username OR display_name`, see `app/routes/helpers.py::get_team_mapping`).
`betting.py` indexes `betting_odds_team_ou ORDER BY owner` and `betting_odds_matchup_ml ORDER BY
matchup` positionally, so those orderings must stay unique and stable within a week.

Odds tables with `run_id` in their primary key keep every run locally. The publish step selects,
per week, only the rows of the latest `run_id` (by `created_at`) so production still has one row
set per week. Publish also filters every table to the current season (`season = ?` where the
column exists, else `league_id = ?` where it exists, else the whole table), matching the
single-season behaviour Flask expects.

---

## 7. Sources

### 7.1 Base

```python
# pipeline/sources/base.py (WP1 writes this verbatim)
@dataclass(frozen=True)
class Projection:
    source: str            # website key, e.g. "espn.com"
    season: int
    week: int
    first_name: str
    last_name: str
    position: str          # canonical QB/RB/WR/TE/K/DEF
    team: str | None       # canonical Sleeper code or None
    points: float          # PPR projected points
    external_id: str | None = None   # source's own player id when available

class ProjectionSource:
    name: str              # short key: sleeper, espn, fantasysharks, fantasypros, firstdown, fanduel
    website: str           # stored in projections.source_website
    supports_future_weeks: bool
    def fetch(self, season: int, week: int) -> list[Projection]: ...
```

`fetch` does network I/O and returns parsed rows; every source also exposes a pure
`parse(payload, season, week) -> list[Projection]` that the tests exercise on saved fixtures
under `tests/pipeline/fixtures/{name}/`. Fixtures are real responses captured in week 3 of 2026,
trimmed to a few hundred rows.

### 7.2 Sources and their 2026 state

| key | website | transport | future weeks | notes |
|---|---|---|---|---|
| sleeper | sleeper.com | HTTP JSON `https://api.sleeper.com/projections/nfl/{season}/{week}?season_type=regular&position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=K&position[]=DEF&order_by=pts_ppr` | yes | rows carry `player_id` (use as external_id), `player.{first_name,last_name,team,position}`, `stats.pts_ppr`. Rows without `pts_ppr` are skipped. |
| espn | espn.com | HTTP JSON `https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/3?scoringPeriodId={week}&view=kona_player_info` with header `x-fantasy-filter: {"players":{"filterSlotIds":{"value":[0,2,4,6,17,16]},"filterStatsForCurrentSeasonScoringPeriodId":{"value":[week]},"limit":600,"sortPercOwned":{"sortPriority":1,"sortAsc":false}}}` | yes | projection = entry in `player.stats[]` with `statSourceId==1`, `statSplitTypeId==1`, `scoringPeriodId==week`, value `appliedTotal`; position from `defaultPositionId` {1 QB, 2 RB, 3 WR, 4 TE, 5 K, 16 DEF}; `proTeamId` numeric → code table; `fullName` split on first space with suffix handling. Replaces the Selenium scraper, which mislabels positions. |
| fantasysharks | fantasysharks.com | HTTP HTML `https://www.fantasysharks.com/apps/bert/forecasts/projections.php?League=-1&Position={pos}&scoring=2&Segment={segment}&uid=4` | yes | Position 1 QB, 2 RB, 4 WR, 5 TE, 7 K, 6 DEF (verify in a live probe); 2026 segments: week N = 882 + N (week 1 = 883 … week 18 = 900); table `id="toolData"`, skip "Tier N" divider rows; names are "Last, First"; last column "Pts". |
| fantasypros | fantasypros.com | HTTP HTML `https://www.fantasypros.com/nfl/projections/{qb|rb|wr|te|k|dst}.php?week={week}&scoring=PPR` | no | server-rendered table `id="data"`; do not use Selenium. The page's selected-week option must equal the requested week or the source fails the week-stamp check. |
| firstdown | firstdown.studio | HTTP HTML `https://www.firstdown.studio/rankings/{qb|rb|wr|te|k}` | no | current headers are `Player`, `Pts` (skill) and `Kick Pts` (K); pick the column whose header is exactly `Pts` or `Kick Pts`, never `Proj. Team Pts`. No DEF. |
| fanduel | fanduel.com | Playwright, intercept GraphQL | no | page `https://www.fanduel.com/research/nfl/fantasy/ppr`; intercept POST responses whose URL contains `/research/api/graphql`; rows in `data.getProjections`. Runs in-process with the Playwright sync API. |

Team names for DEF rows: normalise "Bills D/ST", "Buffalo Bills", "BUF" to the form Sleeper stores
(first_name = city, last_name = nickname, position `DEF`, team code set). Follow how notebooks
03/04 matched DEF rows.

### 7.3 Verification (silent-failure checks)

`pipeline/sources/verify.py::verify_source(rows, week, sleeper_players, sleeper_rows, previous_rows) -> SourceReport`
where `SourceReport(source, status, checks: list[Check(name, status, detail)], n_rows)` and status
is the worst check status (`ok` < `warn` < `fail`).

| check | rule | on breach |
|---|---|---|
| position_agreement | match rows to Sleeper `nfl_players` by normalised (first, last) [+ team]; share of matched rows whose position differs from Sleeper's `position` | > 2% fail; > 0.5% warn |
| duplicate_positions | same (first, last) under two positions within the source | > 2 players fail; > 0 warn |
| position_counts | rows per position inside ranges QB 20–50, RB 40–130, WR 50–170, TE 20–90, K 15–40, DEF 20–36 (K/DEF absent is allowed only for sources that never provide them) | outside fail |
| value_agreement | per position, Pearson r and median absolute difference vs the Sleeper projection on matched players | QB/RB/WR/TE: r < 0.85 or MAD > 4.0 fail; K/DEF: warn only |
| team_codes | share of rows whose team is unrecognised after normalisation | > 5% fail; > 0 warn |
| week_stamp | the payload's own week (query echo, `scoringPeriodId`, selected option, segment) equals the requested week | mismatch fail; `n/a` for sources without one |
| freshness | share of matched players with identical points to the same source's previous week | > 90% fail |
| top_players | the top 3 by points per position all exist in Sleeper's player DB under that position | any miss fail |

A source with status `fail` has its rows deleted from `projections` for that week and is listed in
the step summary; the step itself is `warn`. The scrape step fails only when fewer than 3 sources
are `ok`/`warn`, or Sleeper is not among them (Sleeper supplies ids and the DEF baseline).

Agent review: the scrape step prints, per source, the top 15 by points per position as a compact
table (name, team, points). The runbook tells the agent to eyeball them and record
`python -m pipeline review --week N --source <website> --verdict ok|reject --note "..."`.
A `reject` verdict deletes that source's rows for the week; the agent then reruns from `clean`.
Verdicts show on the dashboard next to the checks.

---

## 8. Steps

Each step lists inputs → outputs → summary keys → acceptance. Weeks/seasons come from `ctx.settings`.

### league (WP4)
Port of notebook 01 plus real data for the parts it faked.
- Fetch league, users, rosters, matchups (weeks 1..week), transactions, `nfl_players` dump
  (weekly; store `fantasy_positions`, `status`, `injury_status`, `team`, `active`).
- `nfl_schedules(season, week, team, opponent, is_home, is_bye)` for all 18 weeks from the ESPN
  scoreboard API `https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?seasontype=2&week={n}&dates={season}` (bye = team absent that week). Replaces the hard-coded 2025 bye table.
- `player_stats` actuals for weeks 1..week-1 from `https://api.sleeper.app/v1/stats/nfl/regular/{season}/{week}` (keep `pts_ppr`).
- `league_settings` helper for later steps: roster_positions, playoff_teams, playoff_week_start,
  waiver type/budget from `leagues.settings` JSON.
- summary: `n_users, n_rosters, n_matchup_rows, n_players, n_schedule_rows, byes_this_week: [codes], owner_changes: [...]` (rosters whose owner_id differs from the previous season's league, by roster_id).

### scrape (WP2a)
- For each requested source (default all six; `--sources` limits): delete the source's rows for
  (season, week), `fetch`, insert into `projections`, run `verify_source`.
- `projections` schema: `id PK, source_website, season INT, week INT, player_first_name,
  player_last_name, position, team, projected_points, external_id, created_at,
  UNIQUE(source_website, season, week, player_first_name, player_last_name, position)`.
- summary: `sources: [{source, status, n_rows, elapsed_s, checks: [{name, status, detail}]}], n_ok_sources, dropped: [...]`; prints the review tables.
- Acceptance: fixture parser tests per source; verify tests with synthetic mislabels (Gibbs as QB
  must trip position_agreement), stale copy, truncated WR list.

### clean (WP3)
Port of notebook 03: name normalisation (suffixes Jr./Sr./II/III, punctuation, injury tags like
"Q", "O", "IR" stripped from names), DEF naming, team normalisation, dedupe within source,
position canonicalisation. Writes back into `projections` (or a `projections_clean` table if the
port keeps raw rows; engineer's call, documented in the module docstring).
- summary: `n_in, n_out, n_renamed, n_dropped_duplicates, unknown_teams: [...]`.

### match (WP3)
Port of notebook 04: attach `sleeper_player_id` with `match_method` in order: external_id
(Sleeper rows), exact (first, last, position, team), exact (first, last, position), normalised
last name + first initial + position, DEF by team. Unmatched rows are kept with NULL id.
Writes `projections_with_sleeper(season, week, ...)`.
- summary: `n_rows, n_matched, match_rate, by_method: {...}, unmatched_top: [{name, position, source, points}]` (top 10 by points).
- Acceptance: on 2025 week 16 legacy data the match rate is ≥ the notebook's (compare counts).

### stats (WP3)
Port of notebook 05 with model params.
- mu = weighted mean of bias-corrected source points (`params["sources"][website]`, default weight 1, bias 0).
- spread = sample std across sources (0 when n_sources = 1).
- sigma = `pipeline.model.sigma.sigma(mu, position, spread, params)`; v1 formula
  `sqrt((alpha*spread)^2 + (beta*pos_sigma)^2)`.
- Writes `player_week_stats(season, week, sleeper_player_id, player_name, position, team, mu, sigma, var, n_sources, spread, model_version, computed_at, PK(season, week, sleeper_player_id))`. Players with a Sleeper id only.
- summary: `n_players, by_position: [{position, n, mean_mu, mean_sigma}], top: [{name, position, mu, sigma}]` (top 10).
- Acceptance: regression on 2025 week 16 legacy data with `v1` params reproduces the notebook's
  mu exactly and sigma within 1e-6 for every player.

### accuracy (WP7)
Replaces notebook 10 and feeds calibration. Computes for `week - 1` (skips with a warning when
actuals are missing). Runs right after `stats` and before `calibrate`, so it needs only last week's
tables plus this run's `league` step, and the same run's calibration sees last week's rows.
- Player level: per source and consensus, by position: n, MAE, bias, Pearson r vs `player_stats.pts_ppr`.
- Team level: projected total (lineup mu) vs actual `matchups.points`, plus p10/p90 coverage from
  `team_distribution_curves`, moneyline outcome vs `betting_odds_matchup_ml` probabilities.
- Writes `prediction_accuracy(season, week, source, position, n, mae, bias, corr)` and
  `team_accuracy(season, week, roster_id, projected, actual, p10, p90, covered, win_prob, won)`.
- Bar charts only (the user finds scatter plots unreadable): MAE by source per position; team
  projected vs actual grouped bars.
- summary: `week_evaluated, consensus_mae_by_position, best_source_by_position, team_mae, coverage_80, brier_ml, charts`.

### calibrate (WP6)
Reads `prediction_accuracy`/`team_accuracy` history and the current params; reports calibration
for the dashboard: per-position PIT coverage at 50/80/95%, team-level coverage, moneyline Brier,
per-source MAE. Writes `calibration_metrics(season, week, model_version, metric, position, value)`
and a bar chart PNG. Never refits inside `run`; refitting is `fit-model`.
- summary: `model_version, weeks_used, team_coverage_80, player_coverage_80_by_position: {...}, brier_ml, charts`.

### lineups (WP4)
Replaces notebook 06. Trust owners; fill only real holes from the waiver wire.
- Slots from league settings (today `QB, RB, RB, WR, WR, TE, FLEX, K, DEF`; slot names as the frozen `team_lineups.slot`).
- Eligible roster players: on `rosters.players`, not in `reserve`/`taxi`, has a `player_week_stats` row for the week, `injury_status` not in {Out, IR, PUP, Suspended, Doubtful}, not on bye.
- Optimal lineup by mu: fill fixed slots greedily by position, FLEX from remaining RB/WR/TE.
- Holes: slots still empty. Free-agent pool: `nfl_players` not on any roster in the league, with a
  `player_week_stats` row from ≥ 2 sources, eligible as above.
- Allocation per position (FLEX last, from the leftover RB/WR/TE pool): order the teams with a
  hole by remaining FAAB (`waiver_budget - waiver_budget_used`) descending, then
  `waiver_position` ascending; the i-th team gets the i-th best remaining free agent; each free
  agent is used once. Replacement mu = `min(fa.mu, cap)` where cap = median mu of the filled
  starters at that position across the league; sigma/var from the free agent's own row.
- Writes `team_lineups` (frozen columns + `season`, `sleeper_player_id`, `is_replacement`) and
  `team_projections_summary` (total_mu, combined_sigma, total_var, waiver_pickups) and
  `projections_rosters` (every rostered player with mu/var) exactly as Flask reads them.
  Flask treats any truthy `starting_status` as a starter (`odds.py::get_team_players`), so it stays
  an integer flag: 1 for players in the optimal lineup, 0 otherwise. The richer classification
  goes in a new `roster_status` column: `starter`, `bench`, `out`, `bye`, `unprojected`.
- summary: `teams: [{owner, total_mu, holes: [{slot, replacement, mu}]}], n_replacements, pool_sizes: {position: n}, cap_by_position: {...}, unresolved: [...]`.
- Acceptance: unit tests with a synthetic 3-team league: healthy players never replaced; Out
  player creates a hole; two teams needing WR get 1st and 2nd best FA in FAAB order; cap binds on
  an outlier FA; FLEX filled last; bye players excluded.

### simulate (WP5)
Replaces the sampling half of notebook 07.
- Inputs: `team_lineups` starters for the week (mu, sigma, position, team, sleeper_player_id),
  params (`dud`, `correlation` may be null), `n_sims`, `seed`, optional `locked_points:
  dict[sleeper_player_id, float]` (players whose games are final; point mass). The CLI does not
  expose locking yet; the function must support it.
- Sampling (`pipeline/model/sampling.py`): draw one standard normal per starter league-wide with
  the copula correlation from params (same NFL team pairs only; identity when null); `u = Φ(z)`;
  if `u < p_dud(mu, position)` the player scores a uniform draw on `[0, threshold_ratio * mu]`,
  else the remaining mass maps through the lognormal quantile with the non-dud mean adjusted so the
  overall mean stays mu. `p_dud = 0` when `dud` is null (pure lognormal, today's behaviour).
  Lognormal params as notebook 07 `lognormal_params`.
- Writes Parquet draws and `simulation_runs(run_id PK, season, week, seed, n_sims, model_version, n_teams, draws_path, created_at)`.
- summary: `run_id, n_sims, seed, model_version, teams: [{owner, mean, p10, p50, p90}], elapsed_s`.
- Acceptance: with `v1` params and seed 1738 the per-team means match notebook 07 within 0.5%;
  runtime < 30 s; correlation test: two same-team QB/WR draws have sample correlation within 0.03 of the table.

### odds (WP5)
Replaces the odds half of notebook 07, reading the latest draws for the week.
- Team O/U (line = median, push handling as today), matchup moneylines (ties as today), highest/lowest
  scorer (vectorised argmax/argmin over the sims × teams matrix), distribution curves
  (300-point grid, pdf/cdf JSON as today), matchup margin curves (`MARGIN_X = linspace(-40, 40, 161)`
  split at 0 as today). American odds conversion as in notebook 07 (`probability_to_american_odds`, no vig), except that p is clamped to [0.001, 0.999] so the string stays numeric for Flask's `int()`.
- All rows carry `run_id` = the simulate run and `season`.
- summary: `run_id, n_matchups, favourites: [{matchup, favourite, prob}], ou_lines: [{owner, line}], highest: [{owner, prob}] (top 3)`.
- Acceptance: on the same draws the outputs equal the notebook's within float tolerance; the
  frozen-table contract test (section 6.1 columns) passes.

### playoffs (WP7)
Replaces notebook 09 with a rest-of-season simulation.
- Future weeks `week+1 .. playoff_week_start-1`: projections from the future-capable sources
  (sleeper, espn, fantasysharks) fetched into `projections` with their own week, matched and
  turned into `player_week_stats` rows for those weeks (reuse the clean/match/stats functions).
- For each future week, each roster's optimal lineup from its current roster (no waiver fill;
  bye/unprojected players score 0); per-week draws use the same sampler; the current week uses
  the draws already produced by `simulate`.
- Standings from actual records to date + simulated results, Sleeper tiebreak (points for).
- Writes `betting_odds_first_place`, `betting_odds_make_playoffs` (frozen), `standings_probability_matrix`.
  Keep the 1–99% filtering the notebook applied when inserting.
- summary: `weeks_simulated, sources_used, first_place: [{owner, prob}], make_playoffs: [{owner, prob}], elapsed_s`.
- Acceptance: first-place probabilities sum to 1 ± 0.01; make-playoff probabilities sum to
  `playoff_teams` ± 0.05; runtime < 3 min at 20k sims per future week (n_sims for futures is a param, default 20_000).

### validate (WP3)
Port of notebook 08 as assertions over the week's tables: every roster has a lineup with all
slots; no NULL mu in lineups; odds probabilities in [0, 1] and matchup pairs sum to 1 ± ties; every
frozen table has rows for the week; owners in odds tables resolve to `users`.
- summary: `checks: [{name, status, detail}]`. Any failure raises.

### publish (WP8a)
Port of `scripts/publish.py` (staging + swap) into a step, with the filters of section 6.1
(latest run per week, current season), plus `pipeline_runs`, `pipeline_steps`, `source_reviews`,
`calibration_metrics`, `prediction_accuracy`, `team_accuracy`, `simulation_runs`.
`PROTECTED_TABLES` stays. Chart upload: `scp backend/data/images/*.png` to
`PUBLISH_CHARTS_TARGET` (default `root@143.198.183.213:/var/lib/tncasino/analytics/`) unless
`--no-charts`.
- summary: `tables: [{name, rows}], charts_uploaded, elapsed_s, target_host`.

---

## 9. Model

Parameters are JSON files under `pipeline/model/params/`, committed to git, loaded by version.

```json
{
  "version": "v1",
  "fitted_on": null,
  "sources": {"sleeper.com": {"weight": 1.0, "bias": 0.0}},
  "sigma": {"formula": "v1", "alpha": 2.0, "beta": 1.0,
            "pos_sigma": {"QB": 7, "RB": 9, "WR": 10, "TE": 8, "K": 4, "DEF": 7}, "default_pos_sigma": 8.0},
  "dud": null,
  "correlation": null
}
```

v2 (WP6) uses `"sigma": {"formula": "linear", "by_position": {"QB": {"a": .., "b": ..}, ...}}`
(sigma = a + b·mu, floored at 1.0), `"dud": {"threshold_ratio": 0.25, "by_position": {"QB": {"c": .., "d": ..}}}`
(p_dud = logistic(c + d·mu)), `"correlation": {"same_nfl_team": {"QB-WR": 0.25, "QB-TE": 0.20, "QB-RB": 0.05, "RB-WR": -0.05}}`,
and fitted per-source weight (∝ 1/MSE, normalised) and bias (mean signed error) for sources with
≥ 3 weeks of history; others get weight 1, bias 0.

`fit-model` fits on 2025 weeks 10–16 plus any 2026 weeks with actuals (from
`projections_with_sleeper` + `player_stats`). Acceptance gate before `model_version` defaults to
v2, as amended 2026-09-27: leave-one-week-out on 2025 weeks 10–16, team-level 80% interval
coverage in [0.72, 0.88], player-level 80% coverage by position in [0.70, 0.90] for QB/RB/WR/TE,
and a moneyline not significantly worse than v1's. A player-week with an actual of 0 or less has
the PIT interval [0, p_dud] and covers a band by the share of that interval inside it. For the
moneyline, d = (p_fit − y)² − (p_v1 − y)² per held-out game, ties left out, and the fit fails only
when mean(d) > 2·sd(d)/√n (with fewer than 2 games, when mean(d) > 0). The zero rule changed
because 8–24% of eligible player-weeks score 0 or less, mostly players who did not play, and
scoring each as the point 0 capped any model's coverage at one minus that share (0.76 at WR), so
a calibrated model could not pass. The Brier rule changed because a strict ≤ on 42 games was a
coin flip: v2's score was 0.0004 above v1's with a standard error of 0.0017. The gate result goes
in the WP6 and WP6b reports and in `docs/architecture/03`.

Known v1 findings to fix: player sigma too wide at low mu and too narrow at high mu; source
disagreement not predictive (so `alpha` carries little information); left tail too thin.

---

## 10. Dashboard (WP8b)

Flask, admin only, reads production tables via `query_analytics`.

- `GET /admin/pipeline?week=N` renders `admin_pipeline.html`: week selector (weeks present in
  `pipeline_steps` joined to `pipeline_runs`), then for the week a table of steps in `STEP_ORDER`
  with latest status (colour-coded), duration, finished time, and the rendered summary: scalar
  keys as a definition list, lists of dicts as small tables, warnings and error text verbatim.
  A "Sources" panel shows each source's checks and the agent verdict from `source_reviews`. A
  "Runs" panel lists runs for the week (run_id, status, steps, started/finished). Chart names in
  a summary link to the existing analytics image route.
- `GET /api/admin/pipeline?week=N` returns the same data as JSON: `{"week", "steps": [...],
  "sources": [...], "runs": [...]}` with the latest `pipeline_steps` row per step for that week.
- Link from `/admin`. Tests use a raw-SQL fixture creating the three run tables (pattern in
  `tests/conftest.py::analytics_tables`) and cover: latest-per-step selection, empty week, admin
  gate, JSON shape.

---

## 11. Engineering rules for every work package

- Follow `CLAUDE.md`: small focused modules, clear names, no clever one-liners, no over-commenting,
  no defensive handling of impossible cases, no single-use helper abstractions. A module docstring
  of one to three lines saying what the module does is enough; no docstrings on obvious functions.
- Python 3.13, `ruff format` and `ruff check` clean (`pyproject.toml` config; `pipeline/` is linted).
- Tests under `tests/pipeline/`, fast, no network, no dependence on the developer's local
  databases. Use `tmp_path` SQLite or in-memory. Fixture payloads are trimmed real captures.
- `python -m pytest` must stay green, including the existing 88 tests.
- Read the notebook you replace before porting (`backend/notebooks/NN_*.ipynb`; extract with
  `jupyter nbconvert --to script --output-dir <scratch> <notebook>`). Preserve behaviour unless
  this document changes it.
- Do not edit files outside your ownership list. If you need a change elsewhere, say so in your
  report instead of making it.
- Commit on your branch with clear messages (imperative, why-first) and report the branch name.
- Use pandas where it is clearer than loops, numpy for the simulation, `requests` for HTTP,
  `beautifulsoup4`+`lxml` for HTML, `pyarrow` for Parquet, `playwright` for FanDuel only.

Local data for regression work is read-only at
`C:/Users/Samer Faizi/Documents/Claude Model/backend/data/databases/` (2025 season, weeks 10–16
for odds; `projections.db` has `"Week N"` strings until `migrate-legacy` runs). Copy files to a
scratch directory before writing to them.

---

## 12. Work packages

| WP | scope | owns | depends on |
|---|---|---|---|
| WP1 core | settings, runner, run records, CLI (`run`, `status`, `review`), db helpers, registries, `sources/base.py`, `steps/__init__.py`, requirements | `pipeline/__init__.py`, `__main__.py`, `settings.py`, `runner.py`, `db.py`, `sources/__init__.py`, `sources/base.py`, `steps/__init__.py`, `requirements.txt` (add `beautifulsoup4`, `lxml`, `pyarrow`, `numpy`), `tests/pipeline/__init__.py`, `tests/pipeline/test_runner.py`, `tests/pipeline/test_settings.py`, `tests/pipeline/test_cli.py` | — |
| WP2a sources (API) | sleeper, espn, fantasysharks, `teams.py`, `verify.py`, scrape step | `pipeline/sources/{sleeper,espn,fantasysharks,teams,verify}.py`, `pipeline/steps/scrape.py`, `tests/pipeline/test_sources_api.py`, `tests/pipeline/test_verify.py`, `tests/pipeline/test_scrape_step.py`, `tests/pipeline/fixtures/{sleeper,espn,fantasysharks}/` | WP1 |
| WP2b sources (HTML) | fantasypros, firstdown, fanduel | `pipeline/sources/{fantasypros,firstdown,fanduel}.py`, `tests/pipeline/test_sources_html.py`, `tests/pipeline/fixtures/{fantasypros,firstdown,fanduel}/` | WP1 (imports `teams.py` from WP2a; if it is not merged yet, define `_TEAM_CODES` locally and the orchestrator reconciles) |
| WP3 clean/match/stats | clean, match, stats, validate steps; model params v1 + sigma; legacy migration | `pipeline/steps/{clean,match,stats,validate}.py`, `pipeline/model/__init__.py`, `pipeline/model/params.py`, `pipeline/model/params/v1.json`, `pipeline/model/sigma.py`, `pipeline/legacy.py` (migrate-legacy; CLI wiring reported to orchestrator), `tests/pipeline/test_{clean,match,stats,validate,params}.py` | WP1 |
| WP4 league + lineups | league step, lineups step, waiver allocation | `pipeline/steps/{league,lineups}.py`, `pipeline/waivers.py`, `tests/pipeline/test_{league,lineups,waivers}.py`, `tests/pipeline/fixtures/sleeper_league/` | WP1 |
| WP5 simulate + odds | sampler, simulate and odds steps, Parquet draws | `pipeline/model/sampling.py`, `pipeline/steps/{simulate,odds}.py`, `tests/pipeline/test_{sampling,simulate,odds}.py` | WP1 |
| WP6 model v2 | fit-model, calibrate step, v2 params, gate report, charts | `pipeline/model/fit.py`, `pipeline/model/params/v2.json`, `pipeline/steps/calibrate.py`, `pipeline/charts.py`, `tests/pipeline/test_{fit,calibrate}.py`; may extend `sigma.py` and `sampling.py` for the v2 formulas | WP3, WP5 |
| WP7 playoffs + accuracy | multi-week playoffs, accuracy | `pipeline/steps/{playoffs,accuracy}.py`, `pipeline/standings.py`, `tests/pipeline/test_{playoffs,accuracy,standings}.py` | WP2a, WP3, WP4, WP5 |
| WP8a publish | publish step | `pipeline/steps/publish.py`, `tests/pipeline/test_publish.py` | WP1 |
| WP8b dashboard | Flask admin pipeline page + API + tests | `app/routes/admin.py` (add routes only), `frontend/templates/admin_pipeline.html`, `frontend/templates/admin.html` (link only), `tests/test_admin_pipeline.py`, `tests/conftest.py` (add one fixture only) | — |
| WP9 cutover | runbook skill rewrite, docs update, delete notebooks/legacy scrapers/scripts, CLAUDE.md commands, CI, requirements cleanup | `.claude/skills/run-pipeline/`, `.claude/skills/scrape/`, `docs/architecture/*`, `CLAUDE.md`, `requirements.txt`, deletions | all |

Batches: 1 = WP1 + WP8b. 2 = WP2a, WP2b, WP3, WP4, WP5, WP8a. 3 = WP6, WP7. 4 = WP9 plus a
full week-4 run and publish.

---

## 13. Schedule

- Sat 26: this document; batch 1; batch 2 started.
- Sun 27: batch 2 merged; end-to-end run on 2025 week 16 legacy data with v1 params reproduces
  today's odds; batch 3 started.
- Mon 28: batch 3 merged; model v2 gate decided; first full 2026 week-4 dry run.
- Tue 29: WP9 cutover; agent runs week 4 for real with the runbook; publish to production.
- Wed 30: rerun with fresh projections, publish, open betting.
