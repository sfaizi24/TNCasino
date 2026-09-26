# Analytics Page Review

> **Historical.** This review is resolved: analytics curves are now precomputed in notebook 07, published to Postgres, and served by the Flask routes (commits `6e4023c` onward). For current behavior see [architecture/05-web-app.md](architecture/05-web-app.md).

Date: 2026-05-18

Scope: review of the interactive analytics page changes on branch `interactive-team-charts`, with emphasis on bugs, production readiness, and performance.

## Summary

The new analytics page moves from static PNG-only charts toward interactive Chart.js views backed by `/api/team_distribution`. The product direction is good, but the current implementation is not production-ready because it reads raw local Monte Carlo simulation rows from `backend/data/databases/montecarlo.db`.

That raw Monte Carlo database is intentionally not published to production. The right fix is to precompute smaller chart-ready tables during the notebook/publish flow, push those tables to Postgres, and have the Flask route serve those precomputed rows instead of doing live SQLite reads and KDE math per request.

Shipping priority:

1. Fix the production data gap.
2. Fix the missing dependency or remove the request-time SciPy dependency.
3. Add visible frontend errors for failed chart loads.
4. Add the safety/UX fixes while touching the page.

## Main Findings

### 1. Production data gap

Current code:

- `app/montecarlo.py` reads `backend/data/databases/montecarlo.db` directly.
- `app/routes/odds.py` calls `montecarlo.get_samples()` inside `/api/team_distribution`.
- `scripts/publish.py` does not publish `monte_carlo_simulations`.
- Repo docs explicitly say not to push the Monte Carlo DB because it is too big.

Impact:

In production, the endpoint will likely fail to find raw simulation data. The frontend currently handles this poorly: non-OK responses are mostly ignored, so the chart can stay blank without a useful user-visible error.

Fix:

Do not publish the raw simulation table. Publish derived analytics tables instead.

### 2. Request-time work is too expensive

Current endpoint does this on every chart request:

- Reads 50,000 simulation rows per team.
- For matchup charts, reads two teams, so about 100,000 rows.
- Computes KDE density curves with SciPy.
- Sorts samples for CDF and margin curves.

Local timing observed during review:

- Reading one team's samples: about 366-388 ms.
- Computing one KDE curve: about 270 ms.
- A matchup request can therefore spend about 1s or more before browser rendering.

Fix:

Move KDE/CDF/margin computation into the data pipeline. The web app should only read precomputed chart points.

### 3. Missing dependency

`app/montecarlo.py` imports `scipy.stats.gaussian_kde`, but `requirements.txt` does not include `scipy`.

Impact:

A clean deploy based on `requirements.txt` can fail at import/startup if SciPy is unavailable.

Fix:

Short-term: add `scipy` to `requirements.txt`.

Better long-term: remove SciPy from request handling entirely by serving precomputed chart data.

### 4. Frontend assumes opponent payload exists

`/api/team_distribution` can return a team-only payload if opponent lookup fails. `frontend/static/js/analytics.js` assumes `payload.opponent` exists in `renderDistributionChart()`.

Impact:

If scheduled opponent lookup or opponent sample lookup fails, the chart render can throw a JavaScript error.

Fix:

Either make the backend return a clear error when an opponent is required, or update the frontend to render a team-only state safely.

### 5. Unescaped HTML injection risk

Several frontend render functions insert labels/player names using template strings and `innerHTML`.

Impact:

Sleeper usernames, display names, or player names can become HTML if they contain special characters. This is avoidable even if the current data looks safe.

Fix:

Use DOM creation plus `textContent`, or add a small `escapeHtml()` helper and use it for all interpolated user/team/player text.

### 6. Conditional moneyline display

The backend sends stored `moneyline`, but the frontend ignores it and recomputes a fair moneyline from win probability.

Impact:

Scheduled matchup odds may not match `betting_odds_matchup_ml`.

For arbitrary user-selected pairs, recomputing a fair moneyline from the empirical win probability is correct because no published scheduled-matchup moneyline exists.

Fix:

Display the stored `moneyline` when present for scheduled matchups. Compute fair moneyline only when backend `moneyline` is null, which should be the arbitrary-pair path.

## Recommended Production Design

### Keep raw simulations local

Continue generating full Monte Carlo simulations locally:

```text
12 teams * 50,000 simulations = 600,000 raw rows per full week
```

Do not push that raw table unless there is a separate product need for simulation-level drilldown.

### Publish chart-ready tables

Add derived tables generated from `montecarlo.db` after the simulation notebook runs.

Option A: compact row-per-team arrays

```sql
CREATE TABLE team_distribution_curves (
    week INTEGER NOT NULL,
    owner TEXT NOT NULL,
    label TEXT,
    x_values JSONB NOT NULL,
    density_values JSONB NOT NULL,
    cdf_values JSONB NOT NULL,
    mean REAL NOT NULL,
    p10 REAL NOT NULL,
    p50 REAL NOT NULL,
    p90 REAL NOT NULL,
    n_sims INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (week, owner)
);
```

Option B: normalized row-per-chart-point

```sql
CREATE TABLE team_distribution_curve_points (
    week INTEGER NOT NULL,
    owner TEXT NOT NULL,
    point_index INTEGER NOT NULL,
    x REAL NOT NULL,
    density REAL NOT NULL,
    cdf REAL NOT NULL,
    PRIMARY KEY (week, owner, point_index)
);

CREATE TABLE team_distribution_summary (
    week INTEGER NOT NULL,
    owner TEXT NOT NULL,
    label TEXT,
    mean REAL NOT NULL,
    p10 REAL NOT NULL,
    p50 REAL NOT NULL,
    p90 REAL NOT NULL,
    n_sims INTEGER NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (week, owner)
);
```

Recommendation: use Option A unless you need SQL-level filtering across individual chart points. It is simpler and much smaller.

### Publish margin curves

For all arbitrary team pairs, add a second derived table:

```sql
CREATE TABLE team_matchup_margin_curves (
    week INTEGER NOT NULL,
    team_owner TEXT NOT NULL,
    opponent_owner TEXT NOT NULL,
    team_win_prob REAL NOT NULL,
    opponent_win_prob REAL NOT NULL,
    tie_prob REAL NOT NULL,
    left_x_values JSONB NOT NULL,
    left_y_values JSONB NOT NULL,
    right_x_values JSONB NOT NULL,
    right_y_values JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (week, team_owner, opponent_owner)
);
```

Because the current UI allows selecting any team against any other team, all directed pairs are required. For a 12-team league, that is about 132 rows per week. If the product chooses to support only scheduled matchups, the opponent dropdown should be removed or limited and this table can be reduced to about 6 rows per week.

## Estimated Table Sizes

Raw Monte Carlo:

- Full week: about 600,000 rows for 12 teams.
- Local database across several weeks is currently about 431 MB.

Team distribution curves:

- 12 rows per week if stored as JSON arrays.
- Each row has about 160 x values, 160 density values, and 160 CDF values.
- Rough size: likely tens to low hundreds of KB per week.

Scheduled matchup-only margin curves:

- About 6 rows per week.
- Each row has about 162 total x/y margin values.
- Rough size: likely under 50 KB per week.

All pair margin curves:

- About 132 directed rows per 12-team week.
- Rough size: likely under 1-2 MB per week, depending on JSON formatting.

Bottom line: precomputed chart tables should be tiny compared with raw simulations and much faster to serve.

## Implementation Plan

1. Apply quick unblockers for testing the branch:

- Add `scipy` to `requirements.txt` if the raw-sample fallback remains in the app during the transition.
- Add visible frontend errors when `/api/team_distribution` returns 404 or 500.
- Avoid marking a tab as rendered until its first data load succeeds.

2. Decide and lock the UX scope:

- If the Matchups tab keeps arbitrary team-vs-team selection, precompute all directed pair margin curves.
- If only scheduled matchups are intended, remove or constrain the arbitrary opponent dropdown.

3. Add the precompute step to the data pipeline.

The owner should be either:

- `backend/notebooks/07_monte_carlo_simulations.ipynb`, if these tables are considered part of the simulation output, or
- a new `07b`/post-simulation notebook cell if keeping chart-serving tables separate makes the pipeline easier to reason about.

This step should read `backend/data/databases/montecarlo.db` and write derived tables to local SQLite, probably `odds.db`.

4. Add the derived tables to `scripts.publish.TABLE_MAP` so they are staged and swapped into production Postgres with the other analytics tables.

5. Update `/api/team_distribution` to query Postgres through `query_analytics()` instead of reading `montecarlo.db`.

6. Keep the current raw-sample path only as a local fallback if desired, guarded behind a clear development-only branch.

7. Handle missing precomputed data explicitly:

- If a requested team distribution is missing, return a clear 404 JSON error and show it in the chart area.
- If an arbitrary pair is missing, show a clear unavailable state instead of leaving stale or blank charts.
- If only scheduled pairs are precomputed, prevent users from selecting unsupported pairs.

8. Fix frontend error handling:

- Show a visible chart error when `/api/team_distribution` returns 404 or 500.
- Avoid marking a tab as rendered until its first load succeeds.
- Handle team-only payloads safely.

9. Fix frontend rendering safety:

- Escape all team/player labels inserted into `innerHTML`, or switch to DOM nodes and `textContent`.

10. Fix moneyline display:

- Use backend `moneyline` when present for scheduled matchups.
- Compute fair moneyline only when backend `moneyline` is null for arbitrary matchups.

11. Add tests:

- API test for `/api/team_distribution` using precomputed chart tables.
- Test for missing team parameter.
- Test for missing distribution data.
- Test for scheduled matchup odds using stored moneylines.

## Quick Interim Fixes

If the full derived-table work needs to wait, the minimum safer patch is:

1. Add `scipy` to `requirements.txt`.
2. Add an index to local `monte_carlo_simulations`:

```sql
CREATE INDEX IF NOT EXISTS idx_monte_carlo_owner_week_sim
ON monte_carlo_simulations(owner, week, sim_id);
```

3. Cache sample arrays and density payloads by `(week, owner)` inside the Flask process.
4. Add frontend visible error handling for failed distribution requests.

This will help local performance, but it does not solve the production data gap because production still does not have `montecarlo.db`.

## Verification From Review

Commands run:

```text
python -m pytest -q
python -m ruff check app/routes/odds.py app/montecarlo.py
python -m py_compile app/routes/odds.py app/montecarlo.py
git diff --check
```

Results:

- Test suite passed: 79 passed.
- Ruff passed for changed Python files.
- Python compilation passed.
- `git diff --check` only reported LF/CRLF warnings.
