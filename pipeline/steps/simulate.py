"""Simulate every starting lineup's weekly total n_sims times and save the draws to Parquet for the odds step."""

import time
from datetime import datetime

import numpy as np
import pandas as pd

from pipeline.db import ensure_columns
from pipeline.model.params import load_params
from pipeline.model.sampling import simulate_teams
from pipeline.runner import StepContext, StepResult, timestamp, utc_now
from pipeline.settings import Settings
from pipeline.steps.league import load_league_settings

NAME = "simulate"

SLOT_RANK = {slot: rank for rank, slot in enumerate(["QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX", "K", "DEF"])}

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
  created_at TEXT NOT NULL,
  n_locked INTEGER NOT NULL DEFAULT 0,
  window_closes_at TEXT,
  standings_through_week INTEGER NOT NULL DEFAULT 0
)
"""
# Columns added after the table first shipped; an older odds.db gains them before its next run is recorded.
ADDED_COLUMNS = {
    "n_locked": "INTEGER NOT NULL DEFAULT 0",
    "window_closes_at": "TEXT",
    "standings_through_week": "INTEGER NOT NULL DEFAULT 0",
}


def run(ctx: StepContext) -> StepResult:
    settings = ctx.settings
    started = time.perf_counter()

    starters = load_starters(ctx)
    params = load_params(settings.model_version)
    # Nothing is locked yet; a rerun after the week's first games will fix their players at their real points.
    locked_points: dict[str, float] = {}
    ctx.log(
        f"{starters['roster_id'].nunique()} teams, {len(starters)} starters, {settings.n_sims} sims, "
        f"seed {settings.seed}, params {settings.model_version}"
    )
    draws, roster_ids = simulate_teams(starters, params, settings.n_sims, settings.seed, locked_points)
    draws_path = save_draws(settings, ctx.run_id, draws, roster_ids)
    created_at = utc_now()
    window_closes_at = next_kickoff(ctx, created_at)
    standings_through_week = fewest_games_played(ctx)
    record_run(
        ctx,
        len(roster_ids),
        draws_path,
        created_at=created_at,
        n_locked=len(locked_points),
        window_closes_at=window_closes_at,
        standings_through_week=standings_through_week,
    )

    elapsed = time.perf_counter() - started
    ctx.log(f"draws saved to {draws_path} in {elapsed:.1f}s")
    ctx.log(f"betting window closes at {window_closes_at}; standings through week {standings_through_week}")
    warnings = []
    if window_closes_at is None:
        warnings.append(f"no week {settings.week} kickoff after this run in nfl_schedules; it has no betting window")

    owners = dict(zip(starters["roster_id"], starters["owner"], strict=True))
    summary = {
        "run_id": ctx.run_id,
        "n_sims": settings.n_sims,
        "seed": settings.seed,
        "model_version": settings.model_version,
        "n_locked": len(locked_points),
        "window_closes_at": window_closes_at,
        "standings_through_week": standings_through_week,
        "teams": team_summaries(draws, [owners[roster_id] for roster_id in roster_ids]),
        "elapsed_s": round(elapsed, 2),
    }
    return StepResult(summary=summary, warnings=warnings)


def load_starters(ctx: StepContext) -> pd.DataFrame:
    """The week's starters ordered by roster then slot, which fixes each player's column in the random draws."""
    settings = ctx.settings
    starters = pd.read_sql_query(
        "SELECT roster_id, owner, slot, sleeper_player_id, position, nfl_team, mu, sigma FROM team_lineups "
        "WHERE season = ? AND week = ? AND sleeper_player_id IS NOT NULL",
        ctx.db("projections"),
        params=(settings.season, settings.week),
    )
    league = load_league_settings(ctx.db("league"), settings.league_id)
    if settings.week >= league.playoff_week_start:
        starters = starters[starters["roster_id"].isin(playoff_roster_ids(ctx))]
    if starters.empty:
        raise LookupError(
            f"no starters to simulate for season {settings.season} week {settings.week}: "
            "team_lineups has none, or no roster has a playoff matchup"
        )

    starters = starters.assign(slot_rank=starters["slot"].map(SLOT_RANK))
    return starters.sort_values(["roster_id", "slot_rank"], ignore_index=True)


def playoff_roster_ids(ctx: StepContext) -> list[int]:
    """Rosters with a game this playoff week; Sleeper leaves matchup_id empty for teams without one."""
    rows = ctx.db("league").execute(
        "SELECT roster_id FROM matchups WHERE league_id = ? AND week = ? AND matchup_id_number IS NOT NULL",
        (ctx.settings.league_id, ctx.settings.week),
    )
    return [row["roster_id"] for row in rows]


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


def next_kickoff(ctx: StepContext, after: datetime) -> str | None:
    """The week's first kickoff after `after`, when betting on a run made then closes; None if no game is left."""
    rows = ctx.db("league").execute(
        "SELECT game_date FROM nfl_schedules WHERE season = ? AND week = ? AND is_bye = 0 AND game_date IS NOT NULL",
        (ctx.settings.season, ctx.settings.week),
    )
    kickoffs = [datetime.fromisoformat(row["game_date"]) for row in rows]
    upcoming = [kickoff for kickoff in kickoffs if kickoff > after]
    return timestamp(min(upcoming)) if upcoming else None


def fewest_games_played(ctx: StepContext) -> int:
    """The week the standings are complete through: Sleeper adds a week's results only once the week is over."""
    query = "SELECT MIN(wins + losses + ties) FROM rosters WHERE league_id = ?"
    (played,) = ctx.db("league").execute(query, (ctx.settings.league_id,)).fetchone()
    return played


def record_run(
    ctx: StepContext,
    n_teams: int,
    draws_path: str,
    *,
    created_at: datetime | None = None,
    n_locked: int = 0,
    window_closes_at: str | None = None,
    standings_through_week: int = 0,
) -> None:
    settings = ctx.settings
    conn = ctx.db("odds")
    conn.execute(SIMULATION_RUNS_DDL)
    ensure_columns(conn, "simulation_runs", ADDED_COLUMNS)
    conn.execute(
        "INSERT OR REPLACE INTO simulation_runs "
        "(run_id, season, week, seed, n_sims, model_version, n_teams, draws_path, created_at, "
        "n_locked, window_closes_at, standings_through_week) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            ctx.run_id,
            settings.season,
            settings.week,
            settings.seed,
            settings.n_sims,
            settings.model_version,
            n_teams,
            draws_path,
            timestamp(created_at or utc_now()),
            n_locked,
            window_closes_at,
            standings_through_week,
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
