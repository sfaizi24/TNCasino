import json
import logging
from functools import lru_cache

import numpy as np
from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from .. import markets, settlement
from ..matrices import MissingMatrix, score_matrix
from ..windows import latest_run_id
from .helpers import (
    display_name_for,
    get_current_week,
    get_league_id_for_week,
    get_team_mapping,
    query_analytics,
    resolve_owner,
)

odds_bp = Blueprint("odds", __name__)

# League-wide views slice FLEX out of RB/WR/TE so it stacks/charts as its own group.

# Every spread line offered, as team1's line in half points, the same for every matchup.
SPREAD_LINES = [step / 2 for step in range(-2 * markets.SPREAD_LIMIT, 2 * markets.SPREAD_LIMIT + 1)]


@odds_bp.route("/api/matchups")
def get_matchups():
    try:
        week = get_current_week()
        team_mapping = get_team_mapping(week)

        matchups = []
        for row in _matchup_rows(week):
            team1_owner = team_mapping.get(row["team1_id"], f"Team {row['team1_id']}")
            team2_owner = team_mapping.get(row["team2_id"], f"Team {row['team2_id']}")

            matchups.append(
                {
                    "matchup": f"{team1_owner} vs {team2_owner}",
                    "original_matchup": row["matchup"],
                    "team1_id": row["team1_id"],
                    "team1_name": team1_owner,
                    "team1_win_prob": row["team1_win_prob"],
                    "team1_ml": row["team1_ml"],
                    "team2_id": row["team2_id"],
                    "team2_name": team2_owner,
                    "team2_win_prob": row["team2_win_prob"],
                    "team2_ml": row["team2_ml"],
                    "market": markets.key_for_row("moneyline", row),
                    "run_id": row["run_id"],
                }
            )

        return jsonify(matchups)
    except Exception as e:
        print(f"Error getting matchups: {e}")
        import traceback

        traceback.print_exc()
        return jsonify([])


@odds_bp.route("/api/spreads")
def get_spreads():
    try:
        week = get_current_week()
        run_id = latest_run_id(week)
        matrix = score_matrix(run_id)
        team_mapping = get_team_mapping(week)
        return jsonify([_spread_row(row, run_id, matrix, team_mapping) for row in _matchup_rows(week)])
    except MissingMatrix:
        logging.warning(f"No spreads: the matrix of run {run_id} is not stored")
        return jsonify([])
    except Exception:
        logging.exception("Could not list the spreads")
        return jsonify([])


def _matchup_rows(week):
    return query_analytics(
        """
        SELECT * FROM betting_odds_matchup_ml
        WHERE season = (SELECT MAX(season) FROM betting_odds_matchup_ml) AND week = :week
        ORDER BY matchup
        """,
        {"week": week},
    )


def _spread_row(row, run_id, matrix, team_mapping):
    """A matchup's spreads: team1's main line, the median margin to the half point, and every line offered."""
    market = markets.parse_key(markets.key_for_row("spread", row))
    team1, team2 = market.teams
    margins = matrix.scores[:, matrix.columns[team1]] - matrix.scores[:, matrix.columns[team2]]
    main_line = np.clip(round(-2 * np.median(margins)) / 2, -markets.SPREAD_LIMIT, markets.SPREAD_LIMIT)

    return {
        "market": market.key,
        "run_id": run_id,
        "team1_id": team1,
        "team1_name": team_mapping.get(team1, f"Team {team1}"),
        "team2_id": team2,
        "team2_name": team_mapping.get(team2, f"Team {team2}"),
        "line": float(main_line),
        "lines": list(_spread_lines(market.key, run_id)),
    }


# A run's prices never change, so a worker prices each matchup's lines once per run.
@lru_cache(maxsize=64)
def _spread_lines(market_key, run_id):
    market = markets.parse_key(market_key)
    matrix = score_matrix(run_id)
    return tuple(_spread_prices(market, line, run_id, matrix) for line in SPREAD_LINES)


def _spread_prices(market, line, run_id, matrix):
    """Both sides of a matchup at team1's line; team2 takes the other side of it."""
    team1, team2 = market.teams
    team1_side = markets.spread_quote(market, str(team1), line, run_id, matrix)
    team2_side = markets.spread_quote(market, str(team2), -line, run_id, matrix)
    return {
        "line": line,
        "team1_odds": team1_side.odds,
        "team1_prob": team1_side.probability,
        "team2_odds": team2_side.odds,
        "team2_prob": team2_side.probability,
    }


@odds_bp.route("/api/team_performance")
def get_team_performance():
    try:
        week = get_current_week()

        rows = query_analytics(
            """
            SELECT * FROM betting_odds_team_ou
            WHERE season = (SELECT MAX(season) FROM betting_odds_team_ou) AND week = :week
            ORDER BY owner
            """,
            {"week": week},
        )

        teams = []
        for row in rows:
            teams.append(
                {
                    "team_id": row["team_id"],
                    "owner": display_name_for(row["owner"]),
                    "line": row["line"],
                    "over_prob": row["over_prob"],
                    "under_prob": row["under_prob"],
                    "market": markets.key_for_row("team_total", row),
                    "run_id": row["run_id"],
                    "over_odds": row["over_odds"],
                    "under_odds": row["under_odds"],
                }
            )

        return jsonify(teams)
    except Exception as e:
        print(f"Error getting team performance: {e}")
        import traceback

        traceback.print_exc()
        return jsonify([])


def _scorer_query(market_name):
    week = get_current_week()
    rows = query_analytics(
        f"""
        SELECT s.season, s.week, s.team_id, s.run_id, s.owner, s.probability, s.odds, ou.line AS proj_pts
        FROM betting_odds_{market_name} s
        LEFT JOIN betting_odds_team_ou ou
            ON ou.team_id = s.team_id AND ou.season = s.season AND ou.week = s.week
        WHERE s.season = (SELECT MAX(season) FROM betting_odds_{market_name}) AND s.week = :week
        ORDER BY s.probability DESC
        """,
        {"week": week},
    )
    return [
        {
            "owner": display_name_for(row["owner"]),
            "win_prob": round(row["probability"] * 100, 1),
            "odds": row["odds"],
            "proj_pts": round(row["proj_pts"], 1) if row["proj_pts"] is not None else None,
            "market": markets.key_for_row(market_name, row),
            "run_id": row["run_id"],
            "team_id": row["team_id"],
        }
        for row in rows
    ]


@odds_bp.route("/api/highest_scorer")
def get_highest_scorer():
    try:
        return jsonify(_scorer_query("highest_scorer"))
    except Exception as e:
        print(f"Error getting highest scorer: {e}")
        import traceback

        traceback.print_exc()
        return jsonify([])


@odds_bp.route("/api/lowest_scorer")
def get_lowest_scorer():
    try:
        return jsonify(_scorer_query("lowest_scorer"))
    except Exception as e:
        print(f"Error getting lowest scorer: {e}")
        import traceback

        traceback.print_exc()
        return jsonify([])


def _futures_rows(market_name):
    """The latest futures run, which is the highest week published for the latest season."""
    table = f"betting_odds_{market_name}"
    # Make playoffs is the one futures market with a second side, NO.
    no_side = ", no_probability, no_american_odds" if market_name == "make_playoffs" else ""
    rows = query_analytics(
        f"""
        SELECT season, week, team_id, run_id, owner, probability, american_odds{no_side}
        FROM {table}
        WHERE season = (SELECT MAX(season) FROM {table})
          AND week = (SELECT MAX(week) FROM {table} WHERE season = (SELECT MAX(season) FROM {table}))
        ORDER BY probability DESC
        """
    )
    listed = []
    for row in rows:
        entry = {
            "owner": display_name_for(row["owner"]),
            "win_prob": round(row["probability"] * 100, 1),
            "odds": row["american_odds"],
            "market": markets.key_for_row(market_name, row),
            "run_id": row["run_id"],
            "team_id": row["team_id"],
            "week": row["week"],
        }
        if no_side:
            entry.update(no_odds=row["no_american_odds"], no_win_prob=round(row["no_probability"] * 100, 1))
        listed.append(entry)
    return listed


@odds_bp.route("/api/make_playoffs")
def get_make_playoffs():
    try:
        return jsonify(_futures_rows("make_playoffs"))
    except Exception:
        logging.exception("Could not list the make-playoffs odds")
        return jsonify([])


@odds_bp.route("/api/last_place")
def get_last_place():
    try:
        return jsonify(_futures_rows("last_place"))
    except Exception:
        logging.exception("Could not list the last-place odds")
        return jsonify([])


@odds_bp.route("/api/champion")
def get_champion():
    try:
        return jsonify(_futures_rows("champion"))
    except Exception:
        logging.exception("Could not list the champion odds")
        return jsonify([])


@odds_bp.route("/api/lineup/<owner>")
def get_lineup(owner):
    try:
        week = get_current_week()

        rows = query_analytics(
            """
            SELECT slot, player_name, position, mu, is_locked, locked_points
            FROM team_lineups
            WHERE owner = :owner AND week = :week
                AND slot IN ('QB', 'RB1', 'RB2', 'WR1', 'WR2', 'TE', 'FLEX', 'K', 'DEF')
            ORDER BY
                CASE slot
                    WHEN 'QB' THEN 1
                    WHEN 'RB1' THEN 2
                    WHEN 'RB2' THEN 3
                    WHEN 'WR1' THEN 4
                    WHEN 'WR2' THEN 5
                    WHEN 'TE' THEN 6
                    WHEN 'FLEX' THEN 7
                    WHEN 'K' THEN 8
                    WHEN 'DEF' THEN 9
                    ELSE 10
                END
            """,
            {"owner": resolve_owner(owner), "week": week},
        )

        lineup = []
        for row in rows:
            lineup.append(
                {
                    "slot": row["slot"],
                    "player_name": row["player_name"],
                    "position": row["position"],
                    "projected_points": round(row["mu"], 1),
                    **_lock_fields(row),
                }
            )

        return jsonify(lineup)
    except Exception as e:
        print(f"Error getting lineup: {e}")
        import traceback

        traceback.print_exc()
        return jsonify([])


def _lock_fields(row):
    """Whether a lineup row's game is final, and the real points it is fixed at if so."""
    if not row["is_locked"]:
        return {"is_locked": False, "locked_points": None}
    return {"is_locked": True, "locked_points": float(row["locked_points"])}


@odds_bp.route("/api/teams")
@login_required
def get_teams():
    try:
        week = get_current_week()
        league_id = get_league_id_for_week(week)
        if not league_id:
            return jsonify({"teams": []})

        rows = query_analytics(
            """
            SELECT r.roster_id, u.username, u.display_name
            FROM sleeper_rosters r
            LEFT JOIN sleeper_users u ON r.owner_id = u.user_id
            WHERE r.league_id = :league_id
            ORDER BY r.roster_id
            """,
            {"league_id": league_id},
        )

        teams = []
        for row in rows:
            username = row["username"]
            display_name = row["display_name"]
            roster_id = row["roster_id"]

            raw_label = display_name or username or f"Team {roster_id}"
            label = display_name_for(raw_label)
            slug = username or display_name

            if slug:
                teams.append({"label": label, "slug": slug, "roster_id": roster_id})

        return jsonify({"teams": teams})
    except Exception as e:
        print(f"[TEAMS ERROR] User {current_user.id} - Exception: {type(e).__name__}: {str(e)}")
        import traceback

        traceback.print_exc()
        return jsonify({"teams": []})


@odds_bp.route("/api/team_distribution")
@login_required
def get_team_distribution():
    """Return precomputed density/CDF curves for the team and (optionally) an opponent.

    When `opponent` is supplied, the matched precomputed margin curve drives
    win probability so users can compare any two teams, not just the
    scheduled pair. Moneylines come from the published `betting_odds_matchup_ml`
    only when this exact pair is the scheduled matchup.
    """
    team_slug = request.args.get("team")
    if not team_slug:
        return jsonify({"error": "Team parameter required"}), 400

    week = request.args.get("week", type=int) or get_current_week()
    team_owner = resolve_owner(team_slug)

    team_dist = _fetch_distribution(week, team_owner)
    if team_dist is None:
        return jsonify({"error": f"No distribution data for {team_owner} in week {week}"}), 404

    opponent_slug = request.args.get("opponent")
    if opponent_slug and opponent_slug != team_slug:
        opp_owner = resolve_owner(opponent_slug)
    else:
        opp_owner = _scheduled_opponent_owner(week, team_owner)

    response = {
        "week": week,
        "x": team_dist["x_values"],
        "team": _team_payload(team_owner, team_dist, None, None),
    }

    if not opp_owner:
        return jsonify(response)

    opp_dist = _fetch_distribution(week, opp_owner)
    margin = _fetch_margin(week, team_owner, opp_owner)
    if opp_dist is None or margin is None:
        return jsonify(response)

    team_ml, opp_ml = _scheduled_moneylines(week, team_owner, opp_owner)

    response["team"] = _team_payload(team_owner, team_dist, margin["team_win_prob"], team_ml)
    response["opponent"] = _team_payload(opp_owner, opp_dist, margin["opponent_win_prob"], opp_ml)
    response["margin"] = {
        "left_x": margin["left_x_values"],
        "left_y": margin["left_y_values"],
        "right_x": margin["right_x_values"],
        "right_y": margin["right_y_values"],
    }
    return jsonify(response)


def _team_payload(owner, dist, win_prob, moneyline):
    return {
        "owner": owner,
        "label": display_name_for(owner),
        "y": dist["density_values"],
        "cdf": dist["cdf_values"],
        "mean": dist["mean"],
        "p10": dist["p10"],
        "p50": dist["p50"],
        "p90": dist["p90"],
        "win_prob": win_prob,
        "moneyline": moneyline,
    }


def _fetch_distribution(week, owner):
    rows = query_analytics(
        "SELECT x_values, density_values, cdf_values, mean, p10, p50, p90 "
        "FROM team_distribution_curves WHERE week = :week AND owner = :owner",
        {"week": week, "owner": owner},
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "x_values": json.loads(row["x_values"]),
        "density_values": json.loads(row["density_values"]),
        "cdf_values": json.loads(row["cdf_values"]),
        "mean": row["mean"],
        "p10": row["p10"],
        "p50": row["p50"],
        "p90": row["p90"],
    }


def _fetch_margin(week, team_owner, opp_owner):
    rows = query_analytics(
        "SELECT team_win_prob, opponent_win_prob, left_x_values, left_y_values, right_x_values, right_y_values "
        "FROM team_matchup_margin_curves "
        "WHERE week = :week AND team_owner = :team AND opponent_owner = :opp",
        {"week": week, "team": team_owner, "opp": opp_owner},
    )
    if not rows:
        return None
    row = rows[0]
    return {
        "team_win_prob": row["team_win_prob"],
        "opponent_win_prob": row["opponent_win_prob"],
        "left_x_values": json.loads(row["left_x_values"]),
        "left_y_values": json.loads(row["left_y_values"]),
        "right_x_values": json.loads(row["right_x_values"]),
        "right_y_values": json.loads(row["right_y_values"]),
    }


def _scheduled_opponent_owner(week, team_owner):
    """Return the scheduled opponent's owner handle, or None if not scheduled."""
    league_id = get_league_id_for_week(week)
    if not league_id:
        return None
    roster_rows = query_analytics(
        """
        SELECT r.roster_id
        FROM sleeper_rosters r
        LEFT JOIN sleeper_users u ON r.owner_id = u.user_id
        WHERE r.league_id = :league_id
          AND (u.username = :owner OR u.display_name = :owner)
        """,
        {"league_id": league_id, "owner": team_owner},
    )
    if not roster_rows:
        return None
    team_rid = roster_rows[0]["roster_id"]

    matchup_rows = query_analytics(
        "SELECT team1_id, team2_id FROM betting_odds_matchup_ml "
        "WHERE week = :week AND (team1_id = :rid OR team2_id = :rid)",
        {"week": week, "rid": team_rid},
    )
    if not matchup_rows:
        return None
    matchup = matchup_rows[0]
    opp_rid = matchup["team2_id"] if matchup["team1_id"] == team_rid else matchup["team1_id"]

    opp_rows = query_analytics(
        """
        SELECT u.username, u.display_name
        FROM sleeper_rosters r
        LEFT JOIN sleeper_users u ON r.owner_id = u.user_id
        WHERE r.roster_id = :rid AND r.league_id = :league_id
        """,
        {"rid": opp_rid, "league_id": league_id},
    )
    if not opp_rows:
        return None
    return opp_rows[0]["username"] or opp_rows[0]["display_name"]


def _scheduled_moneylines(week, team_owner, opp_owner):
    """Return stored (team_ml, opp_ml) when this pair is the scheduled matchup, else (None, None)."""
    league_id = get_league_id_for_week(week)
    if not league_id:
        return None, None
    rows = query_analytics(
        """
        SELECT u.username, u.display_name, r.roster_id
        FROM sleeper_rosters r
        LEFT JOIN sleeper_users u ON r.owner_id = u.user_id
        WHERE r.league_id = :league_id
          AND (u.username IN (:a, :b) OR u.display_name IN (:a, :b))
        """,
        {"league_id": league_id, "a": team_owner, "b": opp_owner},
    )
    by_owner = {(r["username"] or r["display_name"]): r["roster_id"] for r in rows}
    team_rid = by_owner.get(team_owner)
    opp_rid = by_owner.get(opp_owner)
    if team_rid is None or opp_rid is None:
        return None, None

    matchup_rows = query_analytics(
        """
        SELECT team1_id, team1_ml, team2_id, team2_ml
        FROM betting_odds_matchup_ml
        WHERE week = :week
          AND ((team1_id = :a AND team2_id = :b) OR (team1_id = :b AND team2_id = :a))
        """,
        {"week": week, "a": team_rid, "b": opp_rid},
    )
    if not matchup_rows:
        return None, None
    matchup = matchup_rows[0]
    if matchup["team1_id"] == team_rid:
        return matchup["team1_ml"], matchup["team2_ml"]
    return matchup["team2_ml"], matchup["team1_ml"]


@odds_bp.route("/api/team_players")
@login_required
def get_team_players():
    team_owner = request.args.get("team")

    if not team_owner:
        return jsonify({"error": "Team parameter required"}), 400

    try:
        week = get_current_week()
        league_id = get_league_id_for_week(week)
        if not league_id:
            return jsonify({"error": "Team not found"}), 404

        raw_owner = resolve_owner(team_owner)
        roster_rows = query_analytics(
            """
            SELECT r.roster_id, r.starters, r.players
            FROM sleeper_rosters r
            LEFT JOIN sleeper_users u ON r.owner_id = u.user_id
            WHERE r.league_id = :league_id
              AND (u.username = :team_owner OR u.display_name = :team_owner)
            """,
            {"league_id": league_id, "team_owner": raw_owner},
        )

        if not roster_rows:
            return jsonify({"error": "Team not found"}), 404

        roster_id = roster_rows[0]["roster_id"]

        player_rows = query_analytics(
            """
            SELECT p.first_name, p.last_name, p.position, p.mu, p.var, p.starting_status,
                   l.is_locked, l.locked_points
            FROM projections_rosters p
            LEFT JOIN team_lineups l
                ON l.season = p.season AND l.week = p.week AND l.roster_id = p.roster_id
                    AND l.sleeper_player_id = p.sleeper_player_id
            WHERE p.roster_id = :roster_id AND p.week = :week
            ORDER BY
                CASE p.position
                    WHEN 'QB' THEN 1
                    WHEN 'RB' THEN 2
                    WHEN 'WR' THEN 3
                    WHEN 'TE' THEN 4
                    WHEN 'K' THEN 5
                    WHEN 'DEF' THEN 6
                    ELSE 7
                END,
                p.mu DESC
            """,
            {"roster_id": roster_id, "week": week},
        )

        if not player_rows:
            lineup_rows = query_analytics(
                """
                SELECT player_name, position, mu, var, is_locked, locked_points
                FROM team_lineups
                WHERE roster_id = :roster_id AND week = :week
                    AND slot IN ('QB', 'RB1', 'RB2', 'WR1', 'WR2', 'TE', 'FLEX', 'K', 'DEF')
                ORDER BY
                    CASE slot
                        WHEN 'QB' THEN 1
                        WHEN 'RB1' THEN 2
                        WHEN 'RB2' THEN 3
                        WHEN 'WR1' THEN 4
                        WHEN 'WR2' THEN 5
                        WHEN 'TE' THEN 6
                        WHEN 'FLEX' THEN 7
                        WHEN 'K' THEN 8
                        WHEN 'DEF' THEN 9
                        ELSE 10
                    END
                """,
                {"roster_id": roster_id, "week": week},
            )

            starters = []
            for row in lineup_rows:
                player_name = row["player_name"] or ""
                first_name, _, last_name = player_name.partition(" ")
                starters.append(
                    {
                        "player_first_name": first_name,
                        "player_last_name": last_name,
                        "position": row["position"],
                        "mu": float(row["mu"]) if row["mu"] is not None else None,
                        "var": float(row["var"]) if row["var"] is not None else None,
                        **_lock_fields(row),
                    }
                )

            return jsonify({"starters": starters, "bench": []})

        starters = []
        bench = []

        for row in player_rows:
            player_data = {
                "player_first_name": row["first_name"] or "",
                "player_last_name": row["last_name"] or "",
                "position": row["position"],
                "mu": float(row["mu"]) if row["mu"] is not None else None,
                "var": float(row["var"]) if row["var"] is not None else None,
                **_lock_fields(row),
            }

            if row["starting_status"] and str(row["starting_status"]).strip():
                starters.append(player_data)
            else:
                bench.append(player_data)

        bench.sort(
            key=lambda x: (
                {"QB": 1, "RB": 2, "WR": 3, "TE": 4, "K": 5, "DEF": 6}.get(x["position"], 7),
                -(x["mu"] if x["mu"] is not None else 0),
            )
        )

        return jsonify({"starters": starters, "bench": bench})

    except Exception as e:
        print(f"[TEAM_PLAYERS ERROR] {type(e).__name__}: {str(e)}")
        import traceback

        traceback.print_exc()
        return jsonify({"starters": [], "bench": []})


@odds_bp.route("/api/league_overview")
def league_overview():
    """Power-ranking payload: standings, projected mean/p10/p90, and matchup win prob."""
    week = get_current_week()
    league_id = get_league_id_for_week(week)
    if not league_id:
        return jsonify({"week": week, "teams": []})

    standings = settlement.standings_before(week, league_id)

    dist_rows = query_analytics(
        "SELECT owner, mean, p10, p50, p90 FROM team_distribution_curves WHERE week = :week",
        {"week": week},
    )
    dist_by_owner = {r["owner"]: r for r in dist_rows}

    ml_rows = query_analytics(
        "SELECT team1_id, team1_win_prob, team2_id, team2_win_prob FROM betting_odds_matchup_ml WHERE week = :week",
        {"week": week},
    )
    win_prob_by_rid = {}
    for r in ml_rows:
        win_prob_by_rid[r["team1_id"]] = r["team1_win_prob"]
        win_prob_by_rid[r["team2_id"]] = r["team2_win_prob"]

    teams = []
    for rank, standing in enumerate(standings, start=1):
        dist = dist_by_owner.get(standing.owner)
        teams.append(
            {
                "rank": rank,
                "label": display_name_for(standing.owner),
                "record": standing.record,
                "proj_mean": round(dist["mean"], 1) if dist else None,
                "p10": round(dist["p10"], 1) if dist else None,
                "p90": round(dist["p90"], 1) if dist else None,
                "win_prob": round(win_prob_by_rid.get(standing.roster_id, 0) * 100, 1)
                if standing.roster_id in win_prob_by_rid
                else None,
            }
        )

    playoff_teams = settlement.league_settings(league_id).playoff_teams
    return jsonify({"week": week, "playoff_cutoff": playoff_teams, "teams": teams})
