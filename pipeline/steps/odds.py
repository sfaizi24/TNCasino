"""Price the week's betting markets and chart curves from the latest simulation draws, as notebook 07 did."""

import json
import sqlite3
from itertools import permutations

import numpy as np
import pandas as pd
from scipy.stats import gaussian_kde

from pipeline import charts
from pipeline.runner import StepContext, StepResult, timestamp, utc_now
from pipeline.settings import Settings

NAME = "odds"

CURVE_X = np.linspace(0, 300, 300)
MARGIN_X = np.linspace(-40, 40, 161)

# The legacy odds.db tables plus season; the two curve tables also gain run_id so every run is kept.
ODDS_DDL = """
CREATE TABLE IF NOT EXISTS betting_odds_team_ou (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  season INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT,
  owner TEXT,
  line REAL,
  over_prob REAL,
  over_odds TEXT,
  under_prob REAL,
  under_odds TEXT,
  push_count INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, team_id)
);
CREATE TABLE IF NOT EXISTS betting_odds_matchup_ou (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  season INTEGER NOT NULL,
  matchup TEXT,
  team1_id INTEGER,
  team1_name TEXT,
  team2_id INTEGER,
  team2_name TEXT,
  line REAL,
  over_prob REAL,
  over_odds TEXT,
  under_prob REAL,
  under_odds TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, team1_id, team2_id)
);
CREATE TABLE IF NOT EXISTS betting_odds_matchup_ml (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  season INTEGER NOT NULL,
  matchup TEXT,
  team1_id INTEGER,
  team1_name TEXT,
  team1_win_prob REAL,
  team1_ml TEXT,
  team2_id INTEGER,
  team2_name TEXT,
  team2_win_prob REAL,
  team2_ml TEXT,
  ties INTEGER,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, team1_id, team2_id)
);
CREATE TABLE IF NOT EXISTS betting_odds_highest_scorer (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  season INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT,
  owner TEXT,
  count INTEGER,
  probability REAL,
  odds TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, team_id)
);
CREATE TABLE IF NOT EXISTS betting_odds_lowest_scorer (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  season INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT,
  owner TEXT,
  count INTEGER,
  probability REAL,
  odds TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, team_id)
);
CREATE TABLE IF NOT EXISTS team_distribution_curves (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  season INTEGER NOT NULL,
  owner TEXT NOT NULL,
  x_values TEXT NOT NULL,
  density_values TEXT NOT NULL,
  cdf_values TEXT NOT NULL,
  mean REAL NOT NULL,
  p10 REAL NOT NULL,
  p50 REAL NOT NULL,
  p90 REAL NOT NULL,
  n_sims INTEGER NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, owner)
);
CREATE TABLE IF NOT EXISTS team_matchup_margin_curves (
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  season INTEGER NOT NULL,
  team_owner TEXT NOT NULL,
  opponent_owner TEXT NOT NULL,
  team_win_prob REAL NOT NULL,
  opponent_win_prob REAL NOT NULL,
  tie_prob REAL NOT NULL,
  left_x_values TEXT NOT NULL,
  left_y_values TEXT NOT NULL,
  right_x_values TEXT NOT NULL,
  right_y_values TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (run_id, week, team_owner, opponent_owner)
);
"""


def run(ctx: StepContext) -> StepResult:
    settings = ctx.settings
    simulation = latest_simulation(ctx)
    draws = load_draws(settings, simulation["draws_path"])
    teams = load_teams(ctx)
    matchups = load_matchups(ctx)
    ctx.log(f"simulation {simulation['run_id']}: {len(draws)} sims, {draws.shape[1]} teams, {len(matchups)} matchups")

    densities = score_densities(draws)
    moneylines = matchup_moneylines(draws, teams, matchups)
    tables = {
        "betting_odds_team_ou": team_over_unders(draws, teams),
        "betting_odds_matchup_ou": matchup_over_unders(draws, teams, matchups),
        "betting_odds_matchup_ml": moneylines,
        "betting_odds_highest_scorer": scorer_odds(draws, teams, draws.max(axis=1)),
        "betting_odds_lowest_scorer": scorer_odds(draws, teams, draws.min(axis=1)),
        "team_distribution_curves": distribution_curves(draws, teams, densities),
        "team_matchup_margin_curves": margin_curves(draws, teams),
    }
    save_tables(ctx, simulation["run_id"], tables)
    for row in moneylines:
        ctx.log(f"{row['matchup']}: {row['team1_win_prob']:.1%} / {row['team2_win_prob']:.1%}")

    written = []
    if not ctx.options.get("no_charts"):
        owner_draws = draws.rename(columns=teams["owner"])
        owner_densities = densities.rename(columns=teams["owner"])
        written = [
            charts.score_distributions_overlay(settings.images_dir, settings.week, owner_draws, owner_densities),
            charts.score_boxplot(settings.images_dir, settings.week, owner_draws),
        ]

    warnings = []
    if not matchups:
        warnings.append(f"league.db has no matchups for week {settings.week}; only team markets were priced")
    return StepResult(summary=summarise(simulation["run_id"], teams, tables), warnings=warnings, charts=written)


def latest_simulation(ctx: StepContext) -> sqlite3.Row:
    settings = ctx.settings
    rows = ctx.db("odds").execute(
        "SELECT run_id, draws_path FROM simulation_runs WHERE season = ? AND week = ? "
        "ORDER BY created_at DESC, run_id DESC LIMIT 1",
        (settings.season, settings.week),
    )
    simulation = rows.fetchone()
    if simulation is None:
        raise LookupError(f"no simulation run for season {settings.season} week {settings.week}; run simulate first")
    return simulation


def load_draws(settings: Settings, draws_path: str) -> pd.DataFrame:
    """The simulated totals as one row per sim and one column per roster_id."""
    long_draws = pd.read_parquet(settings.data_dir / draws_path)
    draws = long_draws.pivot(index="sim_id", columns="roster_id", values="total_points")
    draws.columns = draws.columns.astype(int)
    return draws.astype(np.float64)


def load_teams(ctx: StepContext) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT DISTINCT roster_id, team_name, owner FROM team_lineups WHERE season = ? AND week = ?",
        ctx.db("projections"),
        params=(ctx.settings.season, ctx.settings.week),
        index_col="roster_id",
    )


def load_matchups(ctx: StepContext) -> list[tuple[int, int]]:
    """The week's head-to-head pairs, lower roster_id first as notebook 07 ordered them."""
    rows = ctx.db("league").execute(
        "SELECT matchup_id_number, roster_id FROM matchups "
        "WHERE league_id = ? AND week = ? AND matchup_id_number IS NOT NULL "
        "ORDER BY matchup_id_number, roster_id",
        (ctx.settings.league_id, ctx.settings.week),
    )
    rosters_by_matchup = {}
    for row in rows:
        rosters_by_matchup.setdefault(row["matchup_id_number"], []).append(row["roster_id"])
    return [tuple(rosters) for rosters in rosters_by_matchup.values() if len(rosters) == 2]


def probability_to_american_odds(prob: float) -> str | None:
    """Fair (no-vig) American odds as a numeric string; None for a chance of 0 or 1, which is not offered."""
    if prob <= 0 or prob >= 1:
        return None
    if prob >= 0.5:
        odds = -(prob / (1 - prob)) * 100
        return f"{round(odds)}"
    odds = ((1 - prob) / prob) * 100
    return f"+{round(odds)}"


def team_columns(teams: pd.DataFrame, roster_id: int) -> dict:
    return {"team_id": roster_id, "team_name": teams.at[roster_id, "team_name"], "owner": teams.at[roster_id, "owner"]}


def matchup_columns(teams: pd.DataFrame, team1_id: int, team2_id: int) -> dict:
    return {
        "matchup": f"Team {team1_id} vs Team {team2_id}",
        "team1_id": team1_id,
        "team1_name": teams.at[team1_id, "team_name"],
        "team2_id": team2_id,
        "team2_name": teams.at[team2_id, "team_name"],
    }


def over_under(points: pd.Series, line: float) -> dict:
    """Over and under priced on their own share of sims; a push (points == line) pays neither side."""
    over_prob = (points > line).mean()
    under_prob = (points < line).mean()
    return {
        "line": line,
        "over_prob": over_prob,
        "over_odds": probability_to_american_odds(over_prob),
        "under_prob": under_prob,
        "under_odds": probability_to_american_odds(under_prob),
    }


def team_over_unders(draws: pd.DataFrame, teams: pd.DataFrame) -> list[dict]:
    rows = []
    for roster_id, points in draws.items():
        line = np.round(points.median(), 2)
        push_count = int((points == line).sum())
        rows.append({**team_columns(teams, roster_id), **over_under(points, line), "push_count": push_count})
    return rows


def matchup_over_unders(draws: pd.DataFrame, teams: pd.DataFrame, matchups: list[tuple[int, int]]) -> list[dict]:
    rows = []
    for team1_id, team2_id in matchups:
        combined = draws[team1_id] + draws[team2_id]
        rows.append({**matchup_columns(teams, team1_id, team2_id), **over_under(combined, combined.median())})
    return rows


def matchup_moneylines(draws: pd.DataFrame, teams: pd.DataFrame, matchups: list[tuple[int, int]]) -> list[dict]:
    rows = []
    for team1_id, team2_id in matchups:
        team1 = draws[team1_id]
        team2 = draws[team2_id]
        team1_win_prob = (team1 > team2).mean()
        team2_win_prob = (team2 > team1).mean()
        rows.append(
            {
                **matchup_columns(teams, team1_id, team2_id),
                "team1_win_prob": team1_win_prob,
                "team1_ml": probability_to_american_odds(team1_win_prob),
                "team2_win_prob": team2_win_prob,
                "team2_ml": probability_to_american_odds(team2_win_prob),
                "ties": int((team1 == team2).sum()),
            }
        )
    return rows


def scorer_odds(draws: pd.DataFrame, teams: pd.DataFrame, extremes: pd.Series) -> list[dict]:
    """How often each team posts the week's extreme score; every team sharing it in a sim gets that sim."""
    counts = draws.eq(extremes, axis=0).sum()
    rows = []
    for roster_id, count in counts.items():
        probability = count / len(draws)
        rows.append(
            {
                **team_columns(teams, roster_id),
                "count": int(count),
                "probability": probability,
                "odds": probability_to_american_odds(probability),
            }
        )
    return sorted(rows, key=lambda row: row["probability"], reverse=True)


def score_densities(draws: pd.DataFrame) -> pd.DataFrame:
    """Each team's kernel density estimate on CURVE_X, shared by the curve table and the overlay chart."""
    densities = {roster_id: gaussian_kde(points)(CURVE_X) for roster_id, points in draws.items()}
    return pd.DataFrame(densities, index=CURVE_X)


def distribution_curves(draws: pd.DataFrame, teams: pd.DataFrame, densities: pd.DataFrame) -> list[dict]:
    rows = []
    for roster_id, points in draws.items():
        cdf = np.searchsorted(np.sort(points), CURVE_X, side="right") / len(points)
        p10, p50, p90 = np.percentile(points, [10, 50, 90])
        rows.append(
            {
                "owner": teams.at[roster_id, "owner"],
                "x_values": json.dumps(CURVE_X.tolist()),
                "density_values": json.dumps(densities[roster_id].tolist()),
                "cdf_values": json.dumps(cdf.tolist()),
                "mean": float(points.mean()),
                "p10": float(p10),
                "p50": float(p50),
                "p90": float(p90),
                "n_sims": len(points),
            }
        )
    return rows


def margin_curves(draws: pd.DataFrame, teams: pd.DataFrame) -> list[dict]:
    """For every ordered pair, the chance of losing by at least x (x <= 0) and of winning by more than x (x >= 0)."""
    left = MARGIN_X <= 0
    right = MARGIN_X >= 0
    rows = []
    for team_id, opponent_id in permutations(draws.columns, 2):
        team = draws[team_id].to_numpy()
        opponent = draws[opponent_id].to_numpy()
        cdf = np.searchsorted(np.sort(team - opponent), MARGIN_X, side="right") / len(team)
        team_win_prob = float((team > opponent).mean())
        tie_prob = float((team == opponent).mean())
        rows.append(
            {
                "team_owner": teams.at[team_id, "owner"],
                "opponent_owner": teams.at[opponent_id, "owner"],
                "team_win_prob": team_win_prob,
                "opponent_win_prob": 1.0 - team_win_prob - tie_prob,
                "tie_prob": tie_prob,
                "left_x_values": json.dumps(MARGIN_X[left].tolist()),
                "left_y_values": json.dumps(cdf[left].tolist()),
                "right_x_values": json.dumps(MARGIN_X[right].tolist()),
                "right_y_values": json.dumps((1.0 - cdf[right]).tolist()),
            }
        )
    return rows


def save_tables(ctx: StepContext, run_id: str, tables: dict[str, list[dict]]) -> None:
    """Replace the simulation run's rows in each table, so rerunning odds on the same draws is idempotent."""
    settings = ctx.settings
    conn = ctx.db("odds")
    conn.executescript(ODDS_DDL)
    created_at = timestamp(utc_now())
    for table, rows in tables.items():
        conn.execute(f"DELETE FROM {table} WHERE run_id = ?", (run_id,))
        frame = pd.DataFrame(rows).assign(
            run_id=run_id, week=settings.week, season=settings.season, created_at=created_at
        )
        frame.to_sql(table, conn, if_exists="append", index=False)
    conn.commit()


def summarise(run_id: str, teams: pd.DataFrame, tables: dict[str, list[dict]]) -> dict:
    favourites = []
    for row in tables["betting_odds_matchup_ml"]:
        if row["team1_win_prob"] >= row["team2_win_prob"]:
            favourite, prob = row["team1_id"], row["team1_win_prob"]
        else:
            favourite, prob = row["team2_id"], row["team2_win_prob"]
        favourites.append(
            {"matchup": row["matchup"], "favourite": teams.at[favourite, "owner"], "prob": round(prob, 3)}
        )

    team_lines = sorted(tables["betting_odds_team_ou"], key=lambda row: row["line"], reverse=True)
    highest = tables["betting_odds_highest_scorer"][:3]
    return {
        "run_id": run_id,
        "n_matchups": len(favourites),
        "favourites": favourites,
        "ou_lines": [{"owner": row["owner"], "line": float(row["line"])} for row in team_lines],
        "highest": [{"owner": row["owner"], "prob": round(row["probability"], 3)} for row in highest],
    }
