"""Simulate every starting lineup's weekly total n_sims times and save the draws to Parquet for the odds step."""

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline.model.sampling import simulate_teams
from pipeline.runner import StepContext, StepResult, timestamp, utc_now
from pipeline.settings import Settings

NAME = "simulate"

SLOT_RANK = {slot: rank for rank, slot in enumerate(["QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX", "K", "DEF"])}
PARAMS_DIR = Path(__file__).resolve().parent.parent / "model" / "params"

SIMULATION_RUNS_DDL = """
CREATE TABLE IF NOT EXISTS simulation_runs (
  run_id TEXT PRIMARY KEY,
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  seed INTEGER NOT NULL,
  n_sims INTEGER NOT NULL,
  model_version TEXT NOT NULL,
  n_teams INTEGER NOT NULL,
  draws_path TEXT NOT NULL,
  created_at TEXT NOT NULL
)
"""


def run(ctx: StepContext) -> StepResult:
    settings = ctx.settings
    started = time.perf_counter()

    starters = load_starters(ctx)
    params = load_params(settings.model_version)
    ctx.log(
        f"{starters['roster_id'].nunique()} teams, {len(starters)} starters, {settings.n_sims} sims, "
        f"seed {settings.seed}, params {settings.model_version}"
    )
    draws, roster_ids = simulate_teams(starters, params, settings.n_sims, settings.seed)
    draws_path = save_draws(settings, ctx.run_id, draws, roster_ids)
    record_run(ctx, len(roster_ids), draws_path)

    elapsed = time.perf_counter() - started
    ctx.log(f"draws saved to {draws_path} in {elapsed:.1f}s")
    owners = dict(zip(starters["roster_id"], starters["owner"], strict=True))
    summary = {
        "run_id": ctx.run_id,
        "n_sims": settings.n_sims,
        "seed": settings.seed,
        "model_version": settings.model_version,
        "teams": team_summaries(draws, [owners[roster_id] for roster_id in roster_ids]),
        "elapsed_s": round(elapsed, 2),
    }
    return StepResult(summary=summary)


def load_starters(ctx: StepContext) -> pd.DataFrame:
    """The week's starters ordered by roster then slot, which fixes each player's column in the random draws."""
    settings = ctx.settings
    starters = pd.read_sql_query(
        "SELECT roster_id, owner, slot, sleeper_player_id, position, nfl_team, mu, sigma FROM team_lineups "
        "WHERE season = ? AND week = ? AND sleeper_player_id IS NOT NULL",
        ctx.db("projections"),
        params=(settings.season, settings.week),
    )
    if settings.week >= playoff_week_start(ctx):
        starters = starters[starters["roster_id"].isin(playoff_roster_ids(ctx))]
    if starters.empty:
        raise LookupError(
            f"no starters to simulate for season {settings.season} week {settings.week}: "
            "team_lineups has none, or no roster has a playoff matchup"
        )

    starters = starters.assign(slot_rank=starters["slot"].map(SLOT_RANK))
    return starters.sort_values(["roster_id", "slot_rank"], ignore_index=True)


def playoff_week_start(ctx: StepContext) -> int:
    row = ctx.db("league").execute("SELECT settings FROM leagues WHERE league_id = ?", (ctx.settings.league_id,))
    return json.loads(row.fetchone()["settings"])["playoff_week_start"]


def playoff_roster_ids(ctx: StepContext) -> list[int]:
    """Rosters with a game this playoff week; Sleeper leaves matchup_id empty for teams without one."""
    rows = ctx.db("league").execute(
        "SELECT roster_id FROM matchups WHERE league_id = ? AND week = ? AND matchup_id_number IS NOT NULL",
        (ctx.settings.league_id, ctx.settings.week),
    )
    return [row["roster_id"] for row in rows]


def load_params(version: str) -> dict:
    with open(PARAMS_DIR / f"{version}.json") as file:
        return json.load(file)


def save_draws(settings: Settings, run_id: str, draws: np.ndarray, roster_ids: list[int]) -> str:
    """Write the draws in long format and return their path relative to the data directory."""
    path = settings.sims_dir / str(settings.season) / f"wk{settings.week:02d}" / f"{run_id}.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    n_sims, n_teams = draws.shape
    long_draws = pd.DataFrame(
        {
            "sim_id": np.repeat(np.arange(n_sims, dtype=np.int32), n_teams),
            "roster_id": np.tile(np.array(roster_ids, dtype=np.int16), n_sims),
            "total_points": draws.ravel(),
        }
    )
    long_draws.to_parquet(path, index=False)
    return path.relative_to(settings.data_dir).as_posix()


def record_run(ctx: StepContext, n_teams: int, draws_path: str) -> None:
    settings = ctx.settings
    conn = ctx.db("odds")
    conn.execute(SIMULATION_RUNS_DDL)
    conn.execute(
        "INSERT OR REPLACE INTO simulation_runs "
        "(run_id, season, week, seed, n_sims, model_version, n_teams, draws_path, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ctx.run_id,
            settings.season,
            settings.week,
            settings.seed,
            settings.n_sims,
            settings.model_version,
            n_teams,
            draws_path,
            timestamp(utc_now()),
        ),
    )
    conn.commit()


def team_summaries(draws: np.ndarray, owners: list[str]) -> list[dict]:
    teams = []
    for column, owner in enumerate(owners):
        totals = draws[:, column].astype(np.float64)
        p10, p50, p90 = np.percentile(totals, [10, 50, 90])
        teams.append(
            {
                "owner": owner,
                "mean": round(float(totals.mean()), 2),
                "p10": round(float(p10), 2),
                "p50": round(float(p50), 2),
                "p90": round(float(p90), 2),
            }
        )
    return sorted(teams, key=lambda team: team["mean"], reverse=True)
