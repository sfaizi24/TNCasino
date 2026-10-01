import json
from collections import defaultdict

from flask import Blueprint, jsonify
from sqlalchemy import inspect

from .. import settlement
from ..database import db
from .helpers import display_name_for, get_current_week, get_league_id_for_week, query_analytics

analytics_bp = Blueprint("analytics", __name__)


def _playoff_teams():
    league_id = get_league_id_for_week(get_current_week())
    return settlement.league_settings(league_id).playoff_teams if league_id else None


@analytics_bp.route("/api/playoff_picture")
def playoff_picture():
    """Each team's chance of every final regular-season place, from the latest futures run."""
    rows = query_analytics(
        """
        SELECT week, team_id, owner, position, probability
        FROM standings_probability_matrix
        WHERE season = (SELECT MAX(season) FROM standings_probability_matrix)
          AND week = (SELECT MAX(week) FROM standings_probability_matrix
                      WHERE season = (SELECT MAX(season) FROM standings_probability_matrix))
        ORDER BY team_id, position
        """
    )
    if not rows:
        return jsonify({"week": None, "playoff_cutoff": None, "teams": []})

    places_by_team = defaultdict(list)
    owners = {}
    for row in rows:
        places_by_team[row["team_id"]].append(row["probability"])
        owners[row["team_id"]] = row["owner"]

    cutoff = _playoff_teams()
    teams = []
    for team_id, places in places_by_team.items():
        teams.append(
            {
                "label": display_name_for(owners[team_id]),
                "places": [round(p * 100, 1) for p in places],
                "playoffs": round(sum(places[:cutoff]) * 100, 1) if cutoff else None,
                "expected_place": sum(place * p for place, p in enumerate(places, start=1)),
            }
        )
    teams.sort(key=lambda team: team["expected_place"])
    for team in teams:
        team["expected_place"] = round(team["expected_place"], 1)

    return jsonify({"week": rows[0]["week"], "playoff_cutoff": cutoff, "teams": teams})


def _playoff_chances_by_week(cutoff):
    return query_analytics(
        """
        SELECT week, team_id, MAX(owner) AS owner, SUM(probability) AS probability
        FROM standings_probability_matrix
        WHERE season = (SELECT MAX(season) FROM standings_probability_matrix)
          AND position <= :cutoff
        GROUP BY week, team_id
        """,
        {"cutoff": cutoff},
    )


def _title_chances_by_week():
    # The champion market lists every team the sims give a chance, so a team missing from a week had none.
    return query_analytics(
        """
        SELECT week, team_id, probability
        FROM betting_odds_champion
        WHERE season = (SELECT MAX(season) FROM betting_odds_champion)
        """
    )


@analytics_bp.route("/api/season_race")
def season_race():
    """Each team's playoff and title chances at every week the futures were run this season."""
    cutoff = _playoff_teams()
    if not cutoff:
        return jsonify({"weeks": [], "teams": []})

    playoffs = defaultdict(dict)
    labels = {}
    for row in _playoff_chances_by_week(cutoff):
        playoffs[row["team_id"]][row["week"]] = round(row["probability"] * 100, 1)
        labels[row["team_id"]] = display_name_for(row["owner"])

    titles = defaultdict(dict)
    for row in _title_chances_by_week():
        titles[row["team_id"]][row["week"]] = round(row["probability"] * 100, 1)

    weeks = sorted({week for chances in playoffs.values() for week in chances})
    teams = [
        {
            "label": labels[team_id],
            "playoffs": [playoffs[team_id].get(week) for week in weeks],
            "title": [titles[team_id].get(week, 0.0) for week in weeks],
        }
        for team_id in playoffs
    ]
    teams.sort(key=lambda team: team["title"][-1], reverse=True)
    return jsonify({"weeks": weeks, "teams": teams})


def _graded_teams():
    """Every team week the accuracy step has graded this season; none before the first graded week."""
    if not inspect(db.engine).has_table("team_accuracy"):
        return []
    return query_analytics(
        """
        SELECT season, week, roster_id, owner, covered, win_prob, won
        FROM team_accuracy
        WHERE season = (SELECT MAX(season) FROM team_accuracy)
        """
    )


def _moneyline_record(rows):
    """How the model's favorite fared in each matchup, counted once from the favorite's side."""
    favorites = [
        row for row in rows if row["win_prob"] is not None and row["win_prob"] > 0.5 and row["won"] is not None
    ]
    won = sum(1 for row in favorites if row["won"])
    return {"won": won, "lost": len(favorites) - won}


def _coverage(rows):
    graded = [row for row in rows if row["covered"] is not None]
    return {"inside": sum(1 for row in graded if row["covered"]), "teams": len(graded)}


def _player_misses(season, week, owners):
    """Each started player's actual points against the model's projection, from the owners' Sleeper lineups."""
    projected = {
        row["sleeper_player_id"]: row
        for row in query_analytics(
            """
            SELECT sleeper_player_id, first_name, last_name, position, mu
            FROM projections_rosters
            WHERE CAST(season AS INTEGER) = :season AND week = :week AND mu > 0
            """,
            {"season": season, "week": week},
        )
    }
    matchups = query_analytics(
        "SELECT roster_id, starters, players_points FROM sleeper_matchups WHERE league_id = :league_id AND week = :week",
        {"league_id": get_league_id_for_week(week), "week": week},
    )

    misses = []
    for matchup in matchups:
        points = json.loads(matchup["players_points"] or "{}")
        for player_id in json.loads(matchup["starters"] or "[]"):
            player = projected.get(player_id)
            if player is None or player_id not in points:
                continue
            misses.append(
                {
                    "player": f"{player['first_name']} {player['last_name']}",
                    "position": player["position"],
                    "owner": display_name_for(owners.get(matchup["roster_id"])),
                    "projected": round(player["mu"], 1),
                    "actual": round(points[player_id], 1),
                }
            )
    misses.sort(key=lambda miss: miss["actual"] - miss["projected"])
    return misses


@analytics_bp.route("/api/model_report")
def model_report():
    """How last week's projections held up: the moneyline favorites, the 80% ranges, and the biggest misses."""
    rows = _graded_teams()
    if not rows:
        return jsonify({"week": None})

    season = rows[0]["season"]
    week = max(row["week"] for row in rows)
    last_week = [row for row in rows if row["week"] == week]
    owners = {row["roster_id"]: row["owner"] for row in last_week}
    misses = _player_misses(season, week, owners)

    return jsonify(
        {
            "week": week,
            "moneyline": {"week": _moneyline_record(last_week), "season": _moneyline_record(rows)},
            "coverage": {"week": _coverage(last_week), "season": _coverage(rows)},
            "booms": [miss for miss in reversed(misses[-5:]) if miss["actual"] > miss["projected"]],
            "busts": [miss for miss in misses[:5] if miss["actual"] < miss["projected"]],
        }
    )
