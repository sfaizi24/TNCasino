"""How a week's pending bets stand against the league's published scores.

Every bet whose market key names the scores it depends on gets an outcome, won, lost, push or
undecided, and a reason the admin reads beside it before confirming. A decided outcome comes from the
win rule in `pipeline/markets.py`, applied to the week's scores as a one-row score matrix, so a bet
settles by the rule its market was priced with. A parlay is judged leg by leg and settles all or
nothing: one lost leg loses it at once, even while other legs wait, and otherwise it waits for every
leg; its pushed legs drop out and the rest are re-priced on the matrix of the run it was placed at.
Make playoffs, yes or no, and last place wait for the regular season's last week, which judges every
pending bet holding one from the final standings, singles and futures parlays alike; the champion and
bets placed before market keys settle by hand. Nothing here moves money or commits: the admin routes
settle through the ledger.
"""

import json
from dataclasses import dataclass

import numpy as np
from sqlalchemy import or_

from pipeline import markets as win_rules

from . import parlays
from .database import db
from .markets import MarketError, parse_key, potential_win, price_from_odds
from .matrices import MissingMatrix, leg_outcome, score_matrix
from .models import Bet, BetLeg
from .routes.helpers import get_league_id_for_week, get_team_mapping, query_analytics

WON = "won"
LOST = "lost"
PUSH = "push"
UNDECIDED = "undecided"


class SettlementError(Exception):
    """Published data a week cannot be settled from, with a message the admin can act on."""


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


@dataclass(frozen=True)
class LeagueSettings:
    playoff_week_start: int
    playoff_teams: int
    num_teams: int


@dataclass
class Standing:
    roster_id: int
    owner: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0

    @property
    def record(self):
        ties = f"-{self.ties}" if self.ties else ""
        return f"{self.wins}-{self.losses}{ties}"


@dataclass(frozen=True)
class FinalStandings:
    """The regular season's standings, best first, and why they are not final yet while a week is unplayed."""

    settings: LeagueSettings
    table: list[Standing]
    incomplete: str | None

    def rank(self, roster_id):
        return next(rank for rank, standing in enumerate(self.table, start=1) if standing.roster_id == roster_id)


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


def league_settings(league_id):
    """The league's playoff format, from the settings Sleeper publishes with it."""
    rows = query_analytics(
        "SELECT settings FROM sleeper_leagues WHERE league_id = :league_id", {"league_id": league_id}
    )
    if not rows:
        raise SettlementError(f"League {league_id} has no published settings; publish the league step")

    settings = json.loads(rows[0]["settings"])
    return LeagueSettings(settings["playoff_week_start"], settings["playoff_teams"], settings["num_teams"])


def standings_before(week, league_id):
    """Every roster's record and points for from the league's games before `week`, best first."""
    rosters = query_analytics(
        """
        SELECT r.roster_id, u.username, u.display_name
        FROM sleeper_rosters r
        LEFT JOIN sleeper_users u ON r.owner_id = u.user_id
        WHERE r.league_id = :league_id
        """,
        {"league_id": league_id},
    )
    standings = {
        row["roster_id"]: Standing(
            row["roster_id"], row["username"] or row["display_name"] or f"Team {row['roster_id']}"
        )
        for row in rosters
    }

    games = query_analytics(
        """
        SELECT a.roster_id, a.points, b.points AS opponent_points
        FROM sleeper_matchups a
        JOIN sleeper_matchups b
          ON a.league_id = b.league_id
         AND a.week = b.week
         AND a.matchup_id_number = b.matchup_id_number
         AND a.roster_id <> b.roster_id
        WHERE a.league_id = :league_id AND a.week < :week
        """,
        {"league_id": league_id, "week": week},
    )
    for game in games:
        _add_game(standings[game["roster_id"]], game["points"] or 0, game["opponent_points"] or 0)

    return sorted(standings.values(), key=lambda standing: (-standing.wins, -standing.points_for, standing.roster_id))


def _add_game(standing, points, opponent_points):
    standing.points_for += points
    if points > opponent_points:
        standing.wins += 1
    elif points < opponent_points:
        standing.losses += 1
    else:
        standing.ties += 1


def final_standings(week):
    """The regular season's standings when `week` is its last week, else None."""
    league_id = get_league_id_for_week(week)
    if league_id is None:
        return None
    settings = league_settings(league_id)
    if week != settings.playoff_week_start - 1:
        return None
    incomplete = _incomplete_season(league_id, week, settings.num_teams)
    return FinalStandings(settings, standings_before(week + 1, league_id), incomplete)


def _incomplete_season(league_id, last_week, num_teams):
    """Why the standings are not final yet, naming the first week in which a roster has no score; None once final."""
    rows = query_analytics(
        """
        SELECT week, COUNT(DISTINCT roster_id) AS scored
        FROM sleeper_matchups
        WHERE league_id = :league_id AND week <= :last_week AND points IS NOT NULL AND points <> 0
        GROUP BY week
        """,
        {"league_id": league_id, "last_week": last_week},
    )
    scored = {row["week"]: row["scored"] for row in rows}
    for week in range(1, last_week + 1):
        if scored.get(week, 0) < num_teams:
            return f"regular season not complete: {scored.get(week, 0)} of {num_teams} rosters scored in week {week}"
    return None


def outcomes_for_week(week, scores):
    """The week's pending bets and, in the regular season's last week, every pending bet with a futures leg too."""
    standings = final_standings(week)
    listed = Bet.week == week
    if standings is not None:
        listed = or_(listed, Bet.legs.any(BetLeg.week.is_(None)))

    bets = db.session.query(Bet).filter(Bet.status == "pending", listed).order_by(Bet.id).all()
    return [outcome_for(bet, scores, standings) for bet in bets]


def outcome_for(bet, scores, standings=None):
    if not bet.legs:
        return BetOutcome(bet, UNDECIDED, "placed before market keys: settle by hand")
    if len(bet.legs) > 1:
        return _parlay(bet, scores, standings)

    [leg] = bet.legs
    return BetOutcome(bet, *_judge_leg(leg, scores, standings))


def _judge_leg(leg, scores, standings=None):
    try:
        market = parse_key(leg.market)
    except MarketError as error:
        return UNDECIDED, str(error)

    if market.name == "moneyline":
        return _moneyline(market, leg, scores)
    if market.name == "spread":
        return _spread(market, leg, scores)
    if market.name == "team_total":
        return _team_total(market, leg, scores)
    if market.name in ("highest_scorer", "lowest_scorer"):
        return _scorer(market, leg, scores)
    if market.name == "champion":
        return UNDECIDED, "champion: settle by hand after the final"
    if standings is None:
        return UNDECIDED, "futures: judged from the final standings"
    return _standings_future(market, leg, standings)


def _parlay(bet, scores, standings):
    judged = [(leg, *_judge_leg(leg, scores, standings)) for leg in bet.legs]
    lost = [reason for _, outcome, reason in judged if outcome == LOST]
    if lost:
        # The bet is over, so a leg still waiting is lost with it.
        statuses = {leg.id: LOST if outcome == UNDECIDED else outcome for leg, outcome, _ in judged}
        return BetOutcome(bet, LOST, "lost: " + "; ".join(lost), leg_statuses=statuses)

    decided = sum(1 for _, outcome, _ in judged if outcome != UNDECIDED)
    if decided < len(judged):
        return BetOutcome(bet, UNDECIDED, f"{decided} of {len(judged)} legs decided")

    statuses = {leg.id: outcome for leg, outcome, _ in judged}
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
    picked, other = _matchup_sides(market, leg, scores)
    unscored = [team for team in (picked, other) if team.points is None]
    if unscored:
        return UNDECIDED, _no_score(unscored)

    reason = f"{picked.team} {picked.points:.2f} vs {other.team} {other.points:.2f}"
    matrix, columns = _score_matrix(scores)
    return _judge(win_rules.moneyline(matrix, columns[picked.roster_id], columns[other.roster_id])), reason


def _spread(market, leg, scores):
    picked, other = _matchup_sides(market, leg, scores)
    unscored = [team for team in (picked, other) if team.points is None]
    if unscored:
        return UNDECIDED, _no_score(unscored)

    reason = f"{picked.team} {picked.points:.2f} {leg.line:+.1f} vs {other.team} {other.points:.2f}"
    matrix, columns = _score_matrix(scores)
    return _judge(win_rules.spread(matrix, columns[picked.roster_id], columns[other.roster_id], leg.line)), reason


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


def _standings_future(market, leg, standings):
    if standings.incomplete:
        return UNDECIDED, standings.incomplete

    roster_id = market.teams[0] if market.teams else int(leg.selection)
    rank = standings.rank(roster_id)
    settings = standings.settings
    if market.name == "make_playoffs":
        made_it = rank <= settings.playoff_teams
        won = made_it if leg.selection == "yes" else not made_it
    else:
        won = rank == settings.num_teams

    standing = standings.table[rank - 1]
    reason = f"{_ordinal(rank)} of {settings.num_teams}: {standing.record}, {standing.points_for:,.2f} pts"
    return (WON if won else LOST), reason


def _ordinal(number):
    if number % 100 in (11, 12, 13):
        return f"{number}th"
    return f"{number}" + {1: "st", 2: "nd", 3: "rd"}.get(number % 10, "th")


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


def _matchup_sides(market, leg, scores):
    """The picked roster and the other roster of a matchup's key, each with its score."""
    first, second = market.teams
    picked_id = int(leg.selection)
    other_id = second if picked_id == first else first
    return _team(scores, picked_id), _team(scores, other_id)


def _team(scores, roster_id):
    return scores.get(roster_id, TeamScore(roster_id, f"Team {roster_id}", None))


def _no_score(teams):
    if len(teams) > 2:
        return f"no score for {len(teams)} teams"
    return "no score for " + " and ".join(team.team for team in teams)
