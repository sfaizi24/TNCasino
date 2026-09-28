"""How a week's pending bets stand against the league's published scores.

Every bet whose market key names the scores it depends on gets an outcome, won, lost, push or
undecided, and a reason the admin reads beside it before confirming. Futures and bets placed before
market keys are always undecided and settle by hand. Nothing here moves money or commits: the admin
routes settle through the ledger.
"""

from dataclasses import dataclass

from .database import db
from .markets import MarketError, parse_key
from .models import Bet
from .routes.helpers import get_team_mapping, query_analytics

WON = "won"
LOST = "lost"
PUSH = "push"
UNDECIDED = "undecided"


class SettlementError(Exception):
    """Published scores a week cannot be settled from, with a message the admin can act on."""


@dataclass(frozen=True)
class TeamScore:
    roster_id: int
    team: str
    points: float | None


@dataclass(frozen=True)
class BetOutcome:
    bet: Bet
    outcome: str
    reason: str


def team_scores(week):
    """Every roster of the week's league by roster id; points are None until the roster's week is played."""
    rows = query_analytics(
        "SELECT league_id, roster_id, points FROM sleeper_matchups WHERE week = :week",
        {"week": week},
    )
    leagues = {row["league_id"] for row in rows}
    if len(leagues) > 1:
        raise SettlementError(f"Week {week} has scores from {len(leagues)} leagues; publish only this season's league")

    points = {row["roster_id"]: row["points"] for row in rows}
    names = get_team_mapping(week)
    scores = {}
    for roster_id in sorted(points.keys() | names.keys()):
        score = points.get(roster_id)
        # Sleeper lists every roster of a week at exactly 0.0 until that week is played.
        if score == 0:
            score = None
        scores[roster_id] = TeamScore(roster_id, names.get(roster_id, f"Team {roster_id}"), score)
    return scores


def outcomes_for_week(week, scores):
    bets = db.session.query(Bet).filter_by(week=week, status="pending").order_by(Bet.id).all()
    return [outcome_for(bet, scores) for bet in bets]


def outcome_for(bet, scores):
    if not bet.legs:
        return BetOutcome(bet, UNDECIDED, "placed before market keys: settle by hand")

    [leg] = bet.legs
    try:
        market = parse_key(leg.market)
    except MarketError as error:
        return BetOutcome(bet, UNDECIDED, str(error))

    if market.name == "moneyline":
        outcome, reason = _moneyline(market, leg, scores)
    elif market.name == "team_total":
        outcome, reason = _team_total(market, leg, scores)
    elif market.name in ("highest_scorer", "lowest_scorer"):
        outcome, reason = _scorer(market, leg, scores)
    else:
        outcome, reason = UNDECIDED, "futures: settle by hand"
    return BetOutcome(bet, outcome, reason)


def _moneyline(market, leg, scores):
    first, second = market.teams
    picked_id = int(leg.selection)
    other_id = second if picked_id == first else first
    picked = _team(scores, picked_id)
    other = _team(scores, other_id)

    unscored = [team for team in (picked, other) if team.points is None]
    if unscored:
        return UNDECIDED, _no_score(unscored)

    reason = f"{picked.team} {picked.points:.2f} vs {other.team} {other.points:.2f}"
    return _compare(_cents(picked.points), _cents(other.points)), reason


def _team_total(market, leg, scores):
    [roster_id] = market.teams
    team = _team(scores, roster_id)
    if team.points is None:
        return UNDECIDED, _no_score([team])

    # The line the bet was placed at, never the odds table's current one: a later run may have moved it.
    reason = f"{team.team} {team.points:.2f}, line {leg.line:.2f}"
    points = _cents(team.points)
    line = _cents(leg.line)
    if leg.selection == "over":
        return _compare(points, line), reason
    return _compare(line, points), reason


def _scorer(market, leg, scores):
    picked_id = int(leg.selection)
    unscored = [team for team in scores.values() if team.points is None]
    if picked_id not in scores:
        unscored.append(_team(scores, picked_id))
    if unscored:
        return UNDECIDED, _no_score(unscored)

    cents = {roster_id: _cents(team.points) for roster_id, team in scores.items()}
    if market.name == "highest_scorer":
        label, target = "highest", max(cents.values())
    else:
        label, target = "lowest", min(cents.values())

    # Every team on the target score wins in full: the pricing counted each of them the winner.
    leaders = [scores[roster_id].team for roster_id, value in cents.items() if value == target]
    reason = f"{label} {target / 100:.2f}: {', '.join(leaders)}"
    if cents[picked_id] == target:
        return WON, reason
    return LOST, reason


def _team(scores, roster_id):
    return scores.get(roster_id, TeamScore(roster_id, f"Team {roster_id}", None))


def _no_score(teams):
    if len(teams) > 2:
        return f"no score for {len(teams)} teams"
    return "no score for " + " and ".join(team.team for team in teams)


def _compare(ours, theirs):
    if ours > theirs:
        return WON
    if ours < theirs:
        return LOST
    return PUSH


def _cents(points):
    """Scores and lines both carry two decimals, so whole cents compare exactly where floats would not."""
    return round(points * 100)
