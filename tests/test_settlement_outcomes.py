from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy import text

from app.models import Bet, BetLeg
from app.settlement import LOST, PUSH, UNDECIDED, WON, SettlementError, TeamScore, outcome_for, team_scores
from pipeline.markets import encode_totals
from tests.conftest import set_points

MONEYLINE = "2026-w10-moneyline-1v2"
TEAM_TOTAL = "2026-w10-team_total-1"
HIGHEST = "2026-w10-highest_scorer"
LOWEST = "2026-w10-lowest_scorer"

NAMES = {1: "Alice", 2: "Bob", 3: "Carol"}

# The run a parlay was placed at, and its matrix for rosters 1, 2 and 3: roster 1 beats roster 2 in 6
# of 10 sims, roster 3 is under 115 in 5, and both happen in 3.
PLACEMENT_RUN = "2026w10-20261111T140000"
PLACEMENT_TOTALS = np.array(
    [
        [110.0, 100.0, 100.0],
        [120.0, 90.0, 110.0],
        [105.0, 95.0, 114.0],
        [110.0, 100.0, 120.0],
        [115.0, 105.0, 130.0],
        [125.0, 115.0, 116.0],
        [90.0, 100.0, 100.0],
        [95.0, 100.0, 105.0],
        [100.0, 110.0, 120.0],
        [80.0, 120.0, 125.0],
    ]
)


def _scores(*points):
    """Scores for rosters 1, 2 and 3 in that order, where None is a roster that has not played."""
    return {roster_id: TeamScore(roster_id, NAMES[roster_id], score) for roster_id, score in enumerate(points, 1)}


def _judge(market, selection, scores, line=None):
    return outcome_for(Bet(legs=[BetLeg(market=market, selection=selection, line=line)]), scores)


def _parlay(*legs):
    """A $100 parlay placed at the placement run, from (market, selection, line, price) legs with ids 1, 2, 3..."""
    return Bet(
        amount=100.0,
        run_id=PLACEMENT_RUN,
        legs=[
            BetLeg(id=leg_id, market=market, selection=selection, line=line, price=price)
            for leg_id, (market, selection, line, price) in enumerate(legs, 1)
        ],
    )


def _store_placement_matrix(session):
    session.execute(
        text("""
        INSERT INTO simulation_totals (run_id, season, week, created_at, n_sims, roster_ids, totals)
        VALUES (:run_id, 2026, 10, '2026-11-11T14:00:00+00:00', 10, '1,2,3', :totals)
    """),
        {"run_id": PLACEMENT_RUN, "totals": encode_totals(PLACEMENT_TOTALS)},
    )


@pytest.mark.parametrize(
    ("selection", "scores", "outcome"),
    [
        ("1", _scores(120.5, 98.25), WON),
        ("2", _scores(120.5, 98.25), LOST),
        ("2", _scores(101.0, 101.0), PUSH),
        ("1", _scores(120.5, None), UNDECIDED),
        ("1", _scores(120.5), UNDECIDED),
    ],
    ids=["above", "below", "level", "opponent unplayed", "opponent missing"],
)
def test_moneyline_compares_the_pick_with_its_opponent(selection, scores, outcome):
    assert _judge(MONEYLINE, selection, scores).outcome == outcome


def test_moneyline_reason_puts_the_pick_first():
    assert _judge(MONEYLINE, "2", _scores(120.5, 98.25)).reason == "Bob 98.25 vs Alice 120.50"


def test_moneyline_waits_for_both_teams():
    assert _judge(MONEYLINE, "1", _scores(None, None)).reason == "no score for Alice and Bob"


@pytest.mark.parametrize(
    ("selection", "points", "outcome"),
    [
        ("over", 111.0, WON),
        ("over", 110.0, LOST),
        ("under", 110.0, WON),
        ("under", 111.0, LOST),
        ("over", 110.5, PUSH),
        ("under", 110.5, PUSH),
        ("over", None, UNDECIDED),
    ],
)
def test_team_total_compares_the_score_with_the_line_of_the_bet(selection, points, outcome):
    assert _judge(TEAM_TOTAL, selection, _scores(points), line=110.5).outcome == outcome


@pytest.mark.parametrize("selection", ["over", "under"])
def test_a_score_on_a_line_with_no_exact_float_pushes(selection):
    assert Decimal(110.63) != Decimal("110.63")

    result = _judge(TEAM_TOTAL, selection, _scores(110.63), line=110.63)

    assert (result.outcome, result.reason) == (PUSH, "Alice 110.63, line 110.63")


def test_team_total_of_a_team_missing_from_the_scores_is_undecided():
    result = _judge("2026-w10-team_total-3", "over", _scores(120.5, 98.25), line=110.5)

    assert (result.outcome, result.reason) == (UNDECIDED, "no score for Team 3")


@pytest.mark.parametrize(
    ("market", "selection", "outcome"),
    [(HIGHEST, "1", WON), (HIGHEST, "3", LOST), (LOWEST, "2", WON), (LOWEST, "1", LOST)],
)
def test_scorer_markets_compare_the_pick_with_every_team(market, selection, outcome):
    assert _judge(market, selection, _scores(120.5, 98.25, 110.5)).outcome == outcome


@pytest.mark.parametrize("selection", ["1", "2", "3"])
def test_every_team_in_a_three_way_tie_wins_in_full(selection):
    result = _judge(HIGHEST, selection, _scores(120.5, 120.5, 120.5))

    assert (result.outcome, result.reason) == (WON, "highest 120.50: Alice, Bob, Carol")


def test_a_team_outside_a_tie_for_lowest_loses():
    result = _judge(LOWEST, "3", _scores(98.25, 98.25, 110.5))

    assert (result.outcome, result.reason) == (LOST, "lowest 98.25: Alice, Bob")


def test_scorer_markets_wait_for_every_team_in_the_league():
    result = _judge(HIGHEST, "1", _scores(120.5, 98.25, None))

    assert (result.outcome, result.reason) == (UNDECIDED, "no score for Carol")


def test_a_scorer_pick_missing_from_the_scores_is_undecided():
    result = _judge(HIGHEST, "4", _scores(120.5, 98.25, 110.5))

    assert (result.outcome, result.reason) == (UNDECIDED, "no score for Team 4")


def test_a_week_nobody_has_played_counts_the_teams_without_a_score():
    assert _judge(LOWEST, "1", _scores(None, None, None)).reason == "no score for 3 teams"


@pytest.mark.parametrize(("market", "selection"), [("2026-first_place", "1"), ("2026-make_playoffs-1", "yes")])
def test_futures_settle_by_hand(market, selection):
    result = _judge(market, selection, _scores(120.5, 98.25, 110.5))

    assert (result.outcome, result.reason) == (UNDECIDED, "futures: settle by hand")


def test_a_bet_placed_before_market_keys_settles_by_hand():
    result = outcome_for(Bet(), _scores(120.5, 98.25, 110.5))

    assert (result.outcome, result.reason) == (UNDECIDED, "placed before market keys: settle by hand")


@pytest.mark.parametrize("market", ["2026-w10-moneyline-2v1", "not a key"])
def test_a_key_that_does_not_parse_is_undecided_with_the_parse_error(market):
    result = _judge(market, "1", _scores(120.5, 98.25))

    assert (result.outcome, result.reason) == (UNDECIDED, "Unknown market")


ALICE_WINS = (MONEYLINE, "1", None, -150)
BOB_WINS = (MONEYLINE, "2", None, 130)
ALICE_OVER = (TEAM_TOTAL, "over", 110.5, -120)
ALICE_HIGHEST = (HIGHEST, "1", None, 185)
ALICE_OVER_HER_SCORE = (TEAM_TOTAL, "over", 120.5, -120)
CAROL_UNDER = ("2026-w10-team_total-3", "under", 115.0, 110)


def test_a_parlay_waits_for_every_leg():
    result = outcome_for(_parlay(ALICE_WINS, ALICE_OVER, ALICE_HIGHEST), _scores(120.5, None))

    assert (result.outcome, result.reason) == (UNDECIDED, "1 of 3 legs decided")
    assert (result.potential_win, result.leg_statuses) == (None, None)


def test_one_lost_leg_loses_the_parlay_and_every_leg_keeps_its_own_outcome():
    result = outcome_for(_parlay(BOB_WINS, ALICE_OVER_HER_SCORE, ALICE_HIGHEST), _scores(120.5, 98.25))

    assert (result.outcome, result.reason) == (LOST, "lost: Bob 98.25 vs Alice 120.50")
    assert (result.potential_win, result.leg_statuses) == (None, {1: LOST, 2: PUSH, 3: WON})


def test_a_parlay_wins_when_every_leg_wins():
    result = outcome_for(_parlay(ALICE_WINS, ALICE_OVER, ALICE_HIGHEST), _scores(120.5, 98.25))

    assert (result.outcome, result.reason) == (WON, "won, 3 legs")
    assert (result.potential_win, result.leg_statuses) == (None, {1: WON, 2: WON, 3: WON})


def test_a_pushed_leg_drops_out_and_the_rest_are_repriced_on_the_placement_matrix(analytics_tables, db_session):
    _store_placement_matrix(db_session.session)

    result = outcome_for(_parlay(ALICE_WINS, ALICE_OVER_HER_SCORE, CAROL_UNDER), _scores(120.5, 98.25, 110.5))

    # Alice beats Bob and Carol stays under 115 in 3 of the placement run's 10 sims: a joint 0.3 at +233.
    assert (result.outcome, result.reason) == (WON, "won, 1 leg pushed: pays 233.00 on the rest")
    assert (result.potential_win, result.leg_statuses) == (233.0, {1: WON, 2: PUSH, 3: WON})


def test_a_parlay_left_with_one_leg_pays_that_legs_own_price():
    result = outcome_for(_parlay(ALICE_WINS, ALICE_OVER_HER_SCORE), _scores(120.5, 98.25))

    assert (result.outcome, result.reason) == (WON, "won, 1 leg pushed: pays 66.67 on the rest")
    assert (result.potential_win, result.leg_statuses) == (66.67, {1: WON, 2: PUSH})


def test_a_parlay_whose_every_leg_pushed_is_a_push():
    alice_on_the_line = (TEAM_TOTAL, "under", 101.0, 100)

    result = outcome_for(_parlay(ALICE_WINS, alice_on_the_line), _scores(101.0, 101.0))

    assert (result.outcome, result.reason) == (PUSH, "push: every leg on its line")
    assert (result.potential_win, result.leg_statuses) == (None, {1: PUSH, 2: PUSH})


def test_a_parlay_to_reprice_without_its_placement_matrix_settles_by_hand(analytics_tables):
    result = outcome_for(_parlay(ALICE_WINS, ALICE_OVER_HER_SCORE, CAROL_UNDER), _scores(120.5, 98.25, 110.5))

    assert (result.outcome, result.reason) == (UNDECIDED, "placement run's matrix not stored: settle by hand")
    assert (result.potential_win, result.leg_statuses) == (None, None)


def test_scores_name_every_roster_of_the_weeks_league(seeded_analytics, db_session):
    set_points(db_session.session, {1: 120.5, 2: 98.25})

    assert team_scores(10) == {1: TeamScore(1, "Alice A", 120.5), 2: TeamScore(2, "Bob B", 98.25)}


def test_a_roster_at_zero_or_without_points_has_not_played(seeded_analytics, db_session):
    set_points(db_session.session, {1: 0.0})

    assert team_scores(10) == {1: TeamScore(1, "Alice A", None), 2: TeamScore(2, "Bob B", None)}


def test_a_league_roster_without_a_matchup_row_has_no_score(seeded_analytics, db_session):
    db_session.session.execute(
        text("INSERT INTO sleeper_rosters (roster_id, league_id, owner_id) VALUES (3, 'league1', 'nobody')")
    )

    assert team_scores(10)[3] == TeamScore(3, "Team 3", None)


def test_a_week_with_scores_from_two_leagues_cannot_be_settled(seeded_analytics, db_session):
    db_session.session.execute(
        text("INSERT INTO sleeper_matchups (league_id, week, roster_id, points) VALUES ('old-league', 10, 99, 101.0)")
    )

    with pytest.raises(SettlementError, match="Week 10 has scores from 2 leagues"):
        team_scores(10)


def test_a_week_without_published_scores_has_no_teams(seeded_analytics):
    assert team_scores(11) == {}
