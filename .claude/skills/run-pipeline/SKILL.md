---
name: run-pipeline
description: Run the data pipeline step by step — execute each notebook, validate output, and fix issues before proceeding. Use for weekly data refreshes.
disable-model-invocation: true
---

Run the TNCasino data pipeline interactively. Execute each notebook in order, validate the output after each step, and investigate/fix any failures before moving on.

**Important**: Scrapers are fragile and break often. When a scraper fails, inspect the error, check if the source site changed, and adapt selectors or logic as needed. Do not skip a failed step — fix it first.

Use `$ARGUMENTS` to control which steps to run (e.g., `/run-pipeline 01-04` or `/run-pipeline 07` for a single step). Default: run steps 1–9. Step 10 (prediction accuracy) is optional and only runs when requested.

Prefer `python -m scripts.scrape --week N --validate` over notebook 02 for step 2: it deletes stale rows per source, isolates failures, and validates the result. Every notebook has its own hardcoded `CURRENT_WEEK`; confirm each one matches before running. See `docs/architecture/02-data-pipeline.md` for what each step reads and writes.

## Pipeline Steps

For each notebook, run it with `jupyter nbconvert --to notebook --execute backend/notebooks/<notebook>.ipynb --output <notebook>.ipynb` and then validate.

### Step 1: `01_league_control`
- **Does**: Fetches league data from Sleeper API
- **Validate**: Check `league.db` has current week's matchups and all roster data. Confirm row counts look reasonable.

### Step 2: `02_projections_control`
- **Does**: Scrapes projections from FanDuel, FantasyPros, ESPN, FirstDown
- **Validate**: Check `projections.db` for new projection rows. Verify each source contributed data (some sources may fail independently). Report which sources succeeded/failed.
- **Common failures**: Site layout changes break selectors. Playwright timeouts on FanDuel. Rate limiting on ESPN.

### Step 3: `03_post_scraping_processing`
- **Does**: Cleans and standardizes projection data
- **Validate**: Check that player names are normalized, duplicates removed, and injury indicators stripped.

### Step 4: `04_match_projections_to_sleeper`
- **Does**: Links projections to Sleeper player IDs
- **Validate**: Check match rate — flag if >10% of players couldn't be matched (name matching is brittle).

### Step 5: `05_compute_player_week_stats`
- **Does**: Calculates mean projections and variance per player
- **Validate**: Spot-check a few players' stats. Verify source disagreement weights look reasonable.

### Step 6: `06_team_lineup_optimizer`
- **Does**: Generates optimal lineups per team
- **Validate**: Each team should have a valid lineup (QB, 2 RB, 2 WR, TE, FLEX, K, DST). Flag any team with missing positions.

### Step 7: `07_monte_carlo_simulations`
- **Does**: Runs 50K Monte Carlo simulations per matchup using lognormal distribution
- **Validate**: Check `montecarlo.db` was created/updated. Verify simulation counts and that odds look reasonable (no 100-0 blowouts unless warranted).

### Step 8: `08_database_validation`
- **Does**: Cross-checks data integrity across all databases
- **Validate**: Report any issues found by the notebook itself.

### Step 9: `09_playoff_odds`
- **Does**: Computes playoff probabilities from simulation results
- **Validate**: Probabilities should sum to reasonable values. Check that all teams have odds calculated.

### Step 10 (optional): `10_prediction_accuracy`
- **Does**: Compares past projections to actual scores (MAE/RMSE/bias by source and position)
- **Validate**: Nothing is written; just report the headline accuracy numbers.

## After Pipeline

Report a summary: which steps succeeded, which needed fixes, and any data quality concerns.
