from decimal import Decimal

import numpy as np
import pytest
from sqlalchemy import text

from app import settlement
from app.models import Bet, BetLeg
from app.settlement import (
    LOST,
    PUSH,
    UNDECIDED,
    WON,
    SettlementError,
    TeamScore,
    final_standings,
    outcome_for,
    team_scores,
)
from pipeline.markets import encode_totals
from tests.conftest import play_weeks, set_points

MONEYLINE = "2026-w10-moneyline-1v2"
SPREAD = "2026-w10-spread-1v2"
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


# Alice beats Bob by 5.5 in every case but the unplayed ones.
@pytest.mark.parametrize(
    ("selection", "line", "scores", "outcome"),
    [
        ("1", -5.0, _scores(110.5, 105.0), WON),
        ("1", -6.0, _scores(110.5, 105.0), LOST),
        ("2", 6.0, _scores(110.5, 105.0), WON),
        ("2", 5.0, _scores(110.5, 105.0), LOST),
        ("1", -5.5, _scores(110.5, 105.0), PUSH),
        ("2", 5.5, _scores(110.5, 105.0), PUSH),
        ("1", -5.5, _scores(110.5, None), UNDECIDED),
        ("2", 5.5, _scores(110.5), UNDECIDED),
    ],
    ids=[
        "favourite covers",
        "favourite short",
        "underdog covers",
        "underdog short",
        "on",
        "other side on",
        "opponent unplayed",
        "opponent missing",
    ],
)
def test_a_spread_adds_the_picks_line_to_its_score(selection, line, scores, outcome):
    assert _judge(SPREAD, selection, scores, line=line).outcome == outcome


def test_a_spread_reason_puts_the_pick_and_its_line_first():
    assert _judge(SPREAD, "1", _scores(110.5, 105.0), line=-5.5).reason == "Alice 110.50 -5.5 vs Bob 105.00"
    assert _judge(SPREAD, "2", _scores(110.5, 105.0), line=5.5).reason == "Bob 105.00 +5.5 vs Alice 110.50"


def test_a_spread_waits_for_both_teams():
    assert _judge(SPREAD, "2", _scores(None, None), line=5.5).reason == "no score for Bob and Alice"


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


@pytest.mark.parametrize(
    ("market", "selection"), [("2026-make_playoffs-1", "yes"), ("2026-make_playoffs-1", "no"), ("2026-last_place", "2")]
)
def test_standings_futures_wait_for_the_final_standings(market, selection):
    result = _judge(market, selection, _scores(120.5, 98.25, 110.5))

    assert (result.outcome, result.reason) == (UNDECIDED, "futures: judged from the final standings")


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


def test_one_lost_leg_loses_the_parlay_at_once_and_the_legs_still_waiting_with_it():
    result = outcome_for(_parlay(BOB_WINS, ALICE_OVER, ALICE_HIGHEST), _scores(120.5, 98.25, None))

    assert (result.outcome, result.reason) == (LOST, "lost: Bob 98.25 vs Alice 120.50")
    assert result.leg_statuses == {1: LOST, 2: WON, 3: LOST}


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


# Ten weeks for rosters 1 and 2 of the seeded league, whose regular season ends in week 10. Alice A wins
# weeks 1 to 6 and 10 for 7-3 and 1,140.25 points; Bob B goes 3-7 with 1,100.00.
ALICE_ON_TOP = ([120.0] * 6 + [100.0] * 3 + [120.25], [110.0] * 10)
# Both 5-5, Bob B on more points: 1,150.00 to Alice A's 905.00.
BOB_ON_POINTS = ([101.0] * 5 + [80.0] * 5, [100.0] * 5 + [130.0] * 5)
# Every week a tie, so both are 0-0-10 on 1,000.00 and the lower roster id ranks first.
LEVEL = ([100.0] * 10, [100.0] * 10)


def _play_season(session, season):
    alice, bob = season
    play_weeks(session, {1: alice[:9], 2: bob[:9]})
    set_points(session, {1: alice[9], 2: bob[9]})


def _judge_on_the_standings(market, selection):
    return outcome_for(Bet(legs=[BetLeg(market=market, selection=selection)]), {}, final_standings(10))


@pytest.mark.parametrize(
    ("market", "selection", "outcome", "reason"),
    [
        ("2026-make_playoffs-1", "yes", WON, "1st of 2: 7-3, 1,140.25 pts"),
        ("2026-make_playoffs-2", "yes", LOST, "2nd of 2: 3-7, 1,100.00 pts"),
        ("2026-make_playoffs-2", "no", WON, "2nd of 2: 3-7, 1,100.00 pts"),
        ("2026-make_playoffs-1", "no", LOST, "1st of 2: 7-3, 1,140.25 pts"),
        ("2026-last_place", "2", WON, "2nd of 2: 3-7, 1,100.00 pts"),
        ("2026-last_place", "1", LOST, "1st of 2: 7-3, 1,140.25 pts"),
    ],
)
def test_the_final_standings_decide_each_standings_future(
    seeded_analytics, db_session, market, selection, outcome, reason
):
    _play_season(db_session.session, ALICE_ON_TOP)

    result = _judge_on_the_standings(market, selection)

    assert (result.outcome, result.reason) == (outcome, reason)


@pytest.mark.parametrize(
    ("season", "first", "reason"),
    [(BOB_ON_POINTS, "2", "1st of 2: 5-5, 1,150.00 pts"), (LEVEL, "1", "1st of 2: 0-0-10, 1,000.00 pts")],
    ids=["points for", "roster id"],
)
def test_level_wins_rank_by_points_for_then_roster_id(seeded_analytics, db_session, season, first, reason):
    _play_season(db_session.session, season)

    result = _judge_on_the_standings(f"2026-make_playoffs-{first}", "yes")

    assert (result.outcome, result.reason) == (WON, reason)


def test_the_standings_wait_for_every_roster_to_score_in_the_last_week(seeded_analytics, db_session):
    play_weeks(db_session.session, {1: [100.0] * 9, 2: [110.0] * 9})
    set_points(db_session.session, {1: 120.0})

    result = _judge_on_the_standings("2026-last_place", "2")

    assert (result.outcome, result.reason) == (
        UNDECIDED,
        "regular season not complete: 1 of 2 rosters scored in week 10",
    )


def test_a_score_of_zero_in_an_earlier_week_leaves_the_season_incomplete(seeded_analytics, db_session):
    play_weeks(db_session.session, {1: [100.0] * 9, 2: [110.0] * 3 + [0.0] + [110.0] * 5})
    set_points(db_session.session, {1: 120.0, 2: 90.0})

    assert final_standings(10).incomplete == "regular season not complete: 1 of 2 rosters scored in week 4"


def test_only_the_regular_seasons_last_week_has_final_standings(seeded_analytics, db_session):
    _play_season(db_session.session, ALICE_ON_TOP)

    assert final_standings(9) is None
    assert final_standings(10).incomplete is None


def test_a_league_without_published_settings_cannot_be_settled(seeded_analytics, db_session):
    db_session.session.execute(text("DELETE FROM sleeper_leagues"))

    with pytest.raises(SettlementError, match="League league1 has no published settings"):
        final_standings(10)


def test_the_champion_settles_by_hand_whatever_the_standings(seeded_analytics, db_session):
    _play_season(db_session.session, ALICE_ON_TOP)
    champion = Bet(legs=[BetLeg(market="2026-champion", selection="1")])

    results = [outcome_for(champion, {}, standings) for standings in (None, final_standings(10))]

    assert {(result.outcome, result.reason) for result in results} == {
        (UNDECIDED, "champion: settle by hand after the final")
    }


ALICE_IN = ("2026-make_playoffs-1", "yes", None, -400)
ALICE_LAST = ("2026-last_place", "1", None, 400)
BOB_LAST = ("2026-last_place", "2", None, 230)
ALICE_CHAMPION = ("2026-champion", "1", None, 233)


def test_a_futures_parlay_waits_for_the_final_standings(seeded_analytics):
    result = outcome_for(_parlay(ALICE_IN, BOB_LAST), {})

    assert (result.outcome, result.reason) == (UNDECIDED, "0 of 2 legs decided")


def test_a_futures_parlay_settles_from_the_final_standings(seeded_analytics, db_session):
    _play_season(db_session.session, ALICE_ON_TOP)

    result = outcome_for(_parlay(ALICE_IN, BOB_LAST), {}, final_standings(10))

    assert (result.outcome, result.reason) == (WON, "won, 2 legs")
    assert (result.potential_win, result.leg_statuses) == (None, {1: WON, 2: WON})


def test_a_futures_parlay_with_a_champion_leg_waits_for_the_final_once_the_rest_have_won(seeded_analytics, db_session):
    _play_season(db_session.session, ALICE_ON_TOP)

    result = outcome_for(_parlay(BOB_LAST, ALICE_CHAMPION), {}, final_standings(10))

    assert (result.outcome, result.reason) == (UNDECIDED, "1 of 2 legs decided")
    assert result.leg_statuses is None


def test_a_futures_parlay_is_lost_with_its_champion_leg_once_a_standings_leg_loses(seeded_analytics, db_session):
    _play_season(db_session.session, ALICE_ON_TOP)

    result = outcome_for(_parlay(ALICE_LAST, ALICE_CHAMPION), {}, final_standings(10))

    assert (result.outcome, result.reason) == (LOST, "lost: 1st of 2: 7-3, 1,140.25 pts")
    assert result.leg_statuses == {1: LOST, 2: LOST}


@pytest.mark.parametrize(
    ("number", "ordinal"),
    [(1, "1st"), (2, "2nd"), (3, "3rd"), (4, "4th"), (11, "11th"), (12, "12th"), (13, "13th"), (21, "21st")],
)
def test_a_rank_reads_as_an_ordinal(number, ordinal):
    assert settlement._ordinal(number) == ordinal
