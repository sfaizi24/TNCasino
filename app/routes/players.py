from flask import Blueprint, jsonify
from sqlalchemy import inspect

from ..database import db
from .helpers import get_current_week, get_team_mapping, query_analytics

players_bp = Blueprint("players", __name__)

POSITIONS = ("QB", "RB", "WR", "TE")
LIST_LENGTH = 5
# A spread needs a few sources behind it; two that differ say little about which one is off.
MIN_SOURCES = 3
# Below this a point or two of disagreement looks huge next to the projection and says nothing.
MIN_PROJECTION = 8

# The playoffs step stores future weeks in the same table, so the week is the betting week, never the latest one.
WEEK_PLAYERS = """
    SELECT s.player_name, s.position, s.team, s.mu, s.p10, s.p90, s.n_sources, s.spread,
           s.source_low, s.source_high, r.roster_id, r.starting_status
    FROM player_week_stats s
    LEFT JOIN projections_rosters r
      ON r.sleeper_player_id = s.sleeper_player_id AND r.week = s.week AND CAST(r.season AS INTEGER) = s.season
    WHERE s.season = (SELECT MAX(season) FROM player_week_stats) AND s.week = :week
      AND s.position IN ('QB', 'RB', 'WR', 'TE')
"""


def _week_players(week):
    """Every projected skill player of the week, with his roster; none before the first publish."""
    if not inspect(db.engine).has_table("player_week_stats"):
        return []
    return query_analytics(WEEK_PLAYERS, {"week": week})


def _top_projections(players, owners):
    """The best projections at each position, each with the range the simulations draw him from."""
    top = {}
    for position in POSITIONS:
        group = sorted(
            (player for player in players if player["position"] == position),
            key=lambda player: player["mu"],
            reverse=True,
        )
        top[position] = [
            {
                "player": player["player_name"],
                "team": player["team"],
                "owner": owners.get(player["roster_id"]),
                "projected": round(player["mu"], 1),
                "low": round(player["p10"], 1),
                "high": round(player["p90"], 1),
            }
            for player in group[:LIST_LENGTH]
        ]
    return top


def _source_spread(player, owners):
    return {
        "player": player["player_name"],
        "position": player["position"],
        "owner": owners.get(player["roster_id"]),
        "projected": round(player["mu"], 1),
        "low": round(player["source_low"], 1),
        "high": round(player["source_high"], 1),
        "sources": player["n_sources"],
    }


@players_bp.route("/api/player_report")
def player_report():
    """The week's top projections by position, and the starters the sources disagree on most and least."""
    week = get_current_week()
    players = _week_players(week)
    if not players:
        return jsonify({"week": None})

    owners = get_team_mapping(week)
    starters = [
        player
        for player in players
        if player["starting_status"] and player["n_sources"] >= MIN_SOURCES and player["mu"] >= MIN_PROJECTION
    ]
    # Relative to the projection: 4 points of disagreement mean less on a 25-point star than on an 8-point flex.
    starters.sort(key=lambda player: player["spread"] / player["mu"])

    return jsonify(
        {
            "week": week,
            "top": _top_projections(players, owners),
            "split": [_source_spread(player, owners) for player in reversed(starters[-LIST_LENGTH:])],
            "agree": [_source_spread(player, owners) for player in starters[:LIST_LENGTH]],
        }
    )
