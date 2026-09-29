"""How a week's pending bets stand against the league's published scores.

Every bet whose market key names the scores it depends on gets an outcome, won, lost, push or
undecided, and a reason the admin reads beside it before confirming. A decided outcome comes from the
win rule in `pipeline/markets.py`, applied to the week's scores as a one-row score matrix, so a bet
settles by the rule its market was priced with. Futures and bets placed before market keys are always
undecided and settle by hand. A parlay is judged leg by leg and settles all or nothing; its pushed legs
drop out and the rest are re-priced on the matrix of the run it was placed at. Nothing here moves money
or commits: the admin routes settle through the ledger.
"""

from dataclasses import dataclass

import numpy as np

from pipeline import markets as win_rules

from . import parlays
from .database import db
from .markets import MarketError, parse_key, potential_win, price_from_odds
from .matrices import MissingMatrix, leg_outcome, score_matrix
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
    potential_win: float | None = None  # set when pushed legs changed what the parlay pays
    leg_statuses: dict | None = None  # leg id → won | lost | push, set for parlays


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
    if len(bet.legs) > 1:
        return _parlay(bet, scores)

    [leg] = bet.legs
    return BetOutcome(bet, *_judge_leg(leg, scores))


def _judge_leg(leg, scores):
    try:
        market = parse_key(leg.market)
    except MarketError as error:
        return UNDECIDED, str(error)

    if market.name == "moneyline":
        return _moneyline(market, leg, scores)
    if market.name == "team_total":
        return _team_total(market, leg, scores)
    if market.name in ("highest_scorer", "lowest_scorer"):
        return _scorer(market, leg, scores)
    return UNDECIDED, "futures: settle by hand"


def _parlay(bet, scores):
    judged = [(leg, *_judge_leg(leg, scores)) for leg in bet.legs]
    decided = sum(1 for _, outcome, _ in judged if outcome != UNDECIDED)
    if decided < len(judged):
        return BetOutcome(bet, UNDECIDED, f"{decided} of {len(judged)} legs decided")

    statuses = {leg.id: outcome for leg, outcome, _ in judged}
    lost = [reason for _, outcome, reason in judged if outcome == LOST]
    if lost:
        return BetOutcome(bet, LOST, "lost: " + "; ".join(lost), leg_statuses=statuses)

    standing = [leg for leg, outcome, _ in judged if outcome == WON]
    return _parlay_without_losses(bet, standing, statuses)


def _parlay_without_losses(bet, standing, statuses):
    pushed = len(bet.legs) - len(standing)
    if not pushed:
        return BetOutcome(bet, WON, f"won, {len(standing)} legs", leg_statuses=statuses)
    if not standing:
        return BetOutcome(bet, PUSH, "push: every leg on its line", leg_statuses=statuses)

    try:
        win = _win_on(bet, standing)
    except MissingMatrix:
        return BetOutcome(bet, UNDECIDED, "placement run's matrix not stored: settle by hand")
    legs_pushed = "1 leg" if pushed == 1 else f"{pushed} legs"
    reason = f"won, {legs_pushed} pushed: pays {win:.2f} on the rest"
    return BetOutcome(bet, WON, reason, potential_win=win, leg_statuses=statuses)


def _win_on(bet, legs):
    """What the parlay pays on the legs left standing: a lone leg's own price, or the rest re-priced at placement."""
    if len(legs) == 1:
        price = legs[0].price
    else:
        matrix = score_matrix(bet.run_id)
        outcomes = [leg_outcome(parse_key(leg.market), leg.selection, leg.line, matrix) for leg in legs]
        _, odds = parlays.joint_price(outcomes)
        price = price_from_odds(odds)
    return round(potential_win(bet.amount, price), 2)


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
    matrix, columns = _score_matrix(scores)
    return _judge(win_rules.moneyline(matrix, columns[picked_id], columns[other_id])), reason


def _team_total(market, leg, scores):
    [roster_id] = market.teams
    team = _team(scores, roster_id)
    if team.points is None:
        return UNDECIDED, _no_score([team])

    # The line the bet was placed at, never the odds table's current one: a later run may have moved it.
    reason = f"{team.team} {team.points:.2f}, line {leg.line:.2f}"
    matrix, columns = _score_matrix(scores)
    return _judge(win_rules.team_total(matrix, columns[roster_id], leg.line, leg.selection)), reason


def _scorer(market, leg, scores):
    picked_id = int(leg.selection)
    unscored = [team for team in scores.values() if team.points is None]
    if picked_id not in scores:
        unscored.append(_team(scores, picked_id))
    if unscored:
        return UNDECIDED, _no_score(unscored)

    if market.name == "highest_scorer":
        label, rule = "highest", win_rules.highest_scorer
    else:
        label, rule = "lowest", win_rules.lowest_scorer

    matrix, columns = _score_matrix(scores)
    winners = [scores[roster_id] for roster_id, column in columns.items() if rule(matrix, column).won[0]]
    names = ", ".join(team.team for team in winners)
    reason = f"{label} {winners[0].points:.2f}: {names}"
    return _judge(rule(matrix, columns[picked_id])), reason


def _score_matrix(scores):
    played = sorted(roster_id for roster_id, team in scores.items() if team.points is not None)
    # No rounding: a score and a line with the same two decimals always read back as the same float.
    matrix = np.array([[scores[roster_id].points for roster_id in played]], dtype=np.float64)
    columns = {roster_id: column for column, roster_id in enumerate(played)}
    return matrix, columns


def _judge(outcome):
    if outcome.pushed[0]:
        return PUSH
    if outcome.won[0]:
        return WON
    return LOST


def _team(scores, roster_id):
    return scores.get(roster_id, TeamScore(roster_id, f"Team {roster_id}", None))


def _no_score(teams):
    if len(teams) > 2:
        return f"no score for {len(teams)} teams"
    return "no score for " + " and ".join(team.team for team in teams)
