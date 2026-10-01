from collections import defaultdict

from flask import Blueprint, jsonify

from ..markets import parse_key
from .helpers import get_current_week, get_team_mapping, query_analytics

money_bp = Blueprint("money", __name__)

# Removed bets were refunded and void bets returned their stake, so neither is money in play.
SINGLE_LEGS = """
    SELECT b.amount, l.season, l.market, l.selection, l.probability
    FROM bets b
    JOIN bet_legs l ON l.bet_id = b.id
    WHERE b.bet_type != 'parlay' AND b.status NOT IN ('removed', 'void')
      AND l.season = (SELECT MAX(season) FROM bet_legs)
"""

WEEK_TITLES = {"highest_scorer": "Highest scorer", "lowest_scorer": "Lowest scorer"}
FUTURES_TITLES = {"last_place": "Last place", "champion": "Champion"}
SIDE_LABELS = {"yes": "Yes", "no": "No", "over": "Over", "under": "Under"}


def _title(market, teams):
    if market.name in WEEK_TITLES:
        return WEEK_TITLES[market.name]
    if market.name in FUTURES_TITLES:
        return FUTURES_TITLES[market.name]
    names = [teams.get(team, f"Team {team}") for team in market.teams]
    if market.name == "team_total":
        return f"{names[0]} total"
    if market.name == "make_playoffs":
        return f"{names[0]} makes playoffs"
    matchup = " vs ".join(names)
    return f"{matchup} spread" if market.name == "spread" else matchup


def _side_label(selection, teams):
    if selection in SIDE_LABELS:
        return SIDE_LABELS[selection]
    return teams.get(int(selection), f"Team {selection}")


def _both_sides(market):
    """The selections of a two-sided market, so a side nobody backed still shows at zero."""
    if market.name in ("moneyline", "spread"):
        return [str(team) for team in market.teams]
    if market.name == "make_playoffs":
        return ["yes", "no"]
    if market.name == "team_total":
        return ["over", "under"]
    return []


def _markets(legs, teams):
    """Stakes on each side of each market, the biggest market first and its biggest side first."""
    stakes = defaultdict(lambda: defaultdict(lambda: {"stake": 0.0, "bets": 0}))
    for leg in legs:
        side = stakes[leg["market"]][leg["selection"]]
        side["stake"] += leg["amount"]
        side["bets"] += 1

    markets = []
    for key, sides in stakes.items():
        market = parse_key(key)
        for selection in _both_sides(market):
            sides.setdefault(selection, {"stake": 0.0, "bets": 0})
        rows = [
            {"label": _side_label(selection, teams), "stake": round(side["stake"], 2), "bets": side["bets"]}
            for selection, side in sides.items()
        ]
        rows.sort(key=lambda row: row["stake"], reverse=True)
        markets.append({"title": _title(market, teams), "stake": sum(row["stake"] for row in rows), "sides": rows})
    markets.sort(key=lambda market: market["stake"], reverse=True)
    return markets


def _favorite_share(legs):
    """The share of moneyline money on the side the model priced to win."""
    moneyline = [leg for leg in legs if parse_key(leg["market"]).name == "moneyline"]
    total = sum(leg["amount"] for leg in moneyline)
    if not total:
        return None
    on_favorite = sum(leg["amount"] for leg in moneyline if leg["probability"] > 0.5)
    return round(on_favorite / total * 100, 1)


def _parlays(week):
    row = query_analytics(
        """
        SELECT COUNT(*) AS slips, COALESCE(SUM(amount), 0) AS stake
        FROM bets
        WHERE bet_type = 'parlay' AND week = :week AND status NOT IN ('removed', 'void')
        """,
        {"week": week},
    )[0]
    return {"slips": row["slips"], "stake": round(row["stake"], 2)}


def _house_net(week):
    """The house takes the other side of every bet, so its result is the bettors' settled results reversed."""
    row = query_analytics(
        "SELECT COALESCE(SUM(settled_pnl), 0) AS bettors FROM weekly_stats WHERE week = :week", {"week": week}
    )[0]
    return round(-row["bettors"], 2)


@money_bp.route("/api/money")
def money():
    """Where the money is this week and on the futures, in totals only: no bettor is named."""
    week = get_current_week()
    teams = get_team_mapping(week)
    legs = query_analytics(SINGLE_LEGS)
    this_week = [leg for leg in legs if parse_key(leg["market"]).week == week]
    futures = [leg for leg in legs if parse_key(leg["market"]).week is None]

    return jsonify(
        {
            "week": week,
            "markets": _markets(this_week, teams),
            "futures": _markets(futures, teams),
            "parlays": _parlays(week),
            "favorite_share": _favorite_share(this_week),
            "house_net": _house_net(week),
        }
    )
