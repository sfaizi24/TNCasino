"""Turn the week's matched projections into one scoring distribution per player.

mu is the weighted mean of the sources' points, each corrected by its source's bias at the player's
position, spread is the sample standard deviation across sources, and sigma comes from the model's
sigma formula; weights, biases and the formula come from the model parameters for
settings.model_version. Name, position and team come from Sleeper's nfl_players.
"""

import itertools
import sqlite3

import numpy as np

from pipeline.model.params import load_params
from pipeline.model.sigma import sigma
from pipeline.runner import StepContext, StepResult, timestamp, utc_now

NAME = "stats"

DEFAULT_SOURCE = {"weight": 1.0, "bias": {}}
POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]

PLAYER_WEEK_STATS_DDL = """
CREATE TABLE IF NOT EXISTS player_week_stats (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  sleeper_player_id TEXT NOT NULL,
  player_name TEXT NOT NULL,
  position TEXT NOT NULL,
  team TEXT,
  mu REAL NOT NULL,
  sigma REAL NOT NULL,
  var REAL NOT NULL,
  n_sources INTEGER NOT NULL,
  spread REAL,
  model_version TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (season, week, sleeper_player_id)
);
"""

INSERT_STATS = """
INSERT INTO player_week_stats (season, week, sleeper_player_id, player_name, position, team, mu, sigma, var,
                               n_sources, spread, model_version, computed_at)
VALUES (:season, :week, :sleeper_player_id, :player_name, :position, :team, :mu, :sigma, :var,
        :n_sources, :spread, :model_version, :computed_at)
"""

# Highest first is the order notebook 05 summed in, so the 2025 means reproduce to the last bit.
MATCHED_PROJECTIONS = """
SELECT sleeper_player_id, source_website, position, projected_points
FROM projections_with_sleeper
WHERE season = ? AND week = ? AND sleeper_player_id IS NOT NULL
ORDER BY sleeper_player_id, projected_points DESC, source_website
"""


def run(ctx: StepContext) -> StepResult:
    season, week = ctx.settings.season, ctx.settings.week
    params = load_params(ctx.settings.model_version)
    league = ctx.db("league")
    players = {
        row["player_id"]: row
        for row in league.execute("SELECT player_id, first_name, last_name, position, team FROM nfl_players")
    }

    conn = ctx.db("projections")
    conn.executescript(PLAYER_WEEK_STATS_DDL)
    rows = conn.execute(MATCHED_PROJECTIONS, (season, week)).fetchall()
    if not rows:
        raise RuntimeError(f"no matched projections for season {season} week {week}; run the match step first")

    computed_at = timestamp(utc_now())
    stats = []
    for player_id, group in itertools.groupby(rows, key=lambda row: row["sleeper_player_id"]):
        player_stats = compute_player_stats(list(group), players[player_id], params)
        player_stats.update(season=season, week=week, model_version=params["version"], computed_at=computed_at)
        stats.append(player_stats)
    with conn:
        conn.execute("DELETE FROM player_week_stats WHERE season = ? AND week = ?", (season, week))
        conn.executemany(INSERT_STATS, stats)

    ctx.log(f"{len(stats)} players from {len(rows)} matched projections, model {params['version']}")
    return StepResult(summarize(stats))


def compute_player_stats(rows: list[sqlite3.Row], player: sqlite3.Row, params: dict) -> dict:
    points = []
    weights = []
    for row in rows:
        source = params["sources"].get(row["source_website"], DEFAULT_SOURCE)
        points.append(row["projected_points"] - source["bias"].get(player["position"], 0.0))
        weights.append(source["weight"])
    mu = float(np.average(points, weights=weights))
    spread = float(np.std(points, ddof=1)) if len(points) >= 2 else 0.0
    player_sigma = sigma(mu, player["position"], spread, params)
    return {
        "sleeper_player_id": player["player_id"],
        "player_name": f"{player['first_name'] or ''} {player['last_name'] or ''}".strip(),
        "position": player["position"],
        "team": player["team"],
        "mu": mu,
        "sigma": player_sigma,
        "var": player_sigma**2,
        "n_sources": len(points),
        "spread": spread,
    }


def summarize(stats: list[dict]) -> dict:
    by_position = []
    for position in POSITION_ORDER:
        group = [player for player in stats if player["position"] == position]
        if group:
            by_position.append(
                {
                    "position": position,
                    "n": len(group),
                    "mean_mu": round(float(np.mean([player["mu"] for player in group])), 2),
                    "mean_sigma": round(float(np.mean([player["sigma"] for player in group])), 2),
                }
            )
    leaders = sorted(stats, key=lambda player: player["mu"], reverse=True)[:10]
    top = [
        {
            "name": player["player_name"],
            "position": player["position"],
            "mu": round(player["mu"], 2),
            "sigma": round(player["sigma"], 2),
        }
        for player in leaders
    ]
    return {"n_players": len(stats), "by_position": by_position, "top": top}
