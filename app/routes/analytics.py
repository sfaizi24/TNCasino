from collections import defaultdict

from flask import Blueprint, jsonify

from .. import settlement
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
