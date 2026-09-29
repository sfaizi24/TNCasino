import numpy as np
import pytest
from sqlalchemy import text

from app.database import db
from app.markets import Quote, parse_key
from app.parlays import Leg, ParlayRefusal, joint_price, quote
from pipeline import markets as win_rules
from tests.conftest import RUN_ID

NEWER_RUN = "2026w10-20261110T143000"

ROSTER_1_WINS = {"market": "2026-w10-moneyline-1v2", "selection": "1"}
ROSTER_2_WINS = {"market": "2026-w10-moneyline-1v2", "selection": "2"}
ROSTER_1_OVER = {"market": "2026-w10-team_total-1", "selection": "over", "line": 110.5}
ROSTER_1_UNDER = {"market": "2026-w10-team_total-1", "selection": "under", "line": 110.5}
ROSTER_2_OVER = {"market": "2026-w10-team_total-2", "selection": "over", "line": 95.0}
ROSTER_1_HIGHEST = {"market": "2026-w10-highest_scorer", "selection": "1"}
ROSTER_2_HIGHEST = {"market": "2026-w10-highest_scorer", "selection": "2"}
ROSTER_2_LOWEST = {"market": "2026-w10-lowest_scorer", "selection": "2"}
ROSTER_1_MINUS_4 = {"market": "2026-w10-spread-1v2", "selection": "1", "line": -4.0}
ROSTER_2_PLUS_4 = {"market": "2026-w10-spread-1v2", "selection": "2", "line": 4.0}
FUTURES = {"market": "2026-make_playoffs-1", "selection": "yes"}
LAST_WEEK = {"market": "2026-w09-moneyline-1v2", "selection": "1"}

CANNOT_PRICE = "Not offered: the simulations cannot price this parlay"

pytestmark = pytest.mark.usefixtures("seeded_analytics")


def _refusal(requests, run_id=RUN_ID):
    with pytest.raises(ParlayRefusal) as refused:
        quote(requests, 10, run_id)
    return str(refused.value), refused.value.rule, refused.value.legs


def _publish_matrix(run_id, roster_ids, totals):
    db.session.execute(
        text("""
        INSERT INTO simulation_totals (run_id, season, week, created_at, n_sims, roster_ids, totals)
        VALUES (:run_id, 2026, 10, '2026-11-10T14:30:00+00:00', :n_sims, :roster_ids, :totals)
    """),
        {"run_id": run_id, "n_sims": len(totals), "roster_ids": roster_ids, "totals": win_rules.encode_totals(totals)},
    )
    db.session.commit()


def _move_quotes(table, run_id, where="1 = 1"):
    db.session.execute(text(f"UPDATE {table} SET run_id = :run_id WHERE {where}"), {"run_id": run_id})
    db.session.commit()


def _judged(*wins):
    won = np.array(wins, dtype=bool)
    return win_rules.Outcome(won=won, pushed=np.zeros_like(won))


def test_a_win_and_an_over_are_priced_at_the_sims_where_both_happen():
    parlay = quote([ROSTER_1_WINS, ROSTER_1_OVER], 10, RUN_ID)

    # Roster 1 wins in 11 of the 20 sims and is over 110.5 in 9; it does both in 7.
    assert parlay.run_id == RUN_ID
    assert parlay.probability == 7 / 20
    assert parlay.odds == "+186"
    assert parlay.price == 186
    assert parlay.legs == (
        Leg(parse_key("2026-w10-moneyline-1v2"), "1", None, Quote(RUN_ID, "-150", 0.6, None)),
        Leg(parse_key("2026-w10-team_total-1"), "over", 110.5, Quote(RUN_ID, "-120", 0.55, 110.5)),
    )


def test_three_legs_are_priced_at_the_sims_where_all_three_happen():
    parlay = quote([ROSTER_1_WINS, ROSTER_1_OVER, ROSTER_2_OVER], 10, RUN_ID)

    # Of the 7 sims where roster 1 wins and tops 110.5, roster 2 tops 95 in 5; any two legs alone win in 7 or 8.
    assert parlay.probability == 5 / 20
    assert parlay.odds == "+300"
    assert parlay.price == 300
    assert [leg.quote.odds for leg in parlay.legs] == ["-150", "-120", "+105"]


def test_a_spread_leg_is_priced_at_its_own_line():
    parlay = quote([ROSTER_1_MINUS_4, ROSTER_1_OVER], 10, RUN_ID)

    # Roster 1 covers -4.0 in 9 sims and tops 110.5 in 9; it does both in the 6 where it scores 118 to 130.
    assert (parlay.probability, parlay.odds) == (6 / 20, "+233")
    assert parlay.legs[0] == Leg(parse_key("2026-w10-spread-1v2"), "1", -4.0, Quote(RUN_ID, "+122", 0.45, -4.0))


def test_a_spread_leg_at_an_alternate_line_carries_that_line():
    parlay = quote([{**ROSTER_2_PLUS_4, "line": 9.5}, ROSTER_1_OVER], 10, RUN_ID)

    # Roster 2 +9.5 covers in 14 sims; roster 1 still tops 110.5 in 4 of them, at margins 5, 4, -26 and -23.
    assert parlay.legs[0].line == 9.5
    assert parlay.legs[0].quote == Quote(RUN_ID, "-233", 0.70, 9.5)
    assert (parlay.probability, parlay.odds) == (4 / 20, "+400")


def test_a_spread_leg_without_a_line_is_refused_as_a_leg():
    faulty = {"market": "2026-w10-spread-1v2", "selection": "1"}

    assert _refusal([faulty, ROSTER_1_OVER]) == ("Unknown line", "leg", ("2026-w10-spread-1v2",))


def test_a_moneyline_adds_nothing_to_its_favourite_covering_the_spread():
    assert _refusal([ROSTER_1_MINUS_4, ROSTER_1_WINS]) == (
        "A leg adds nothing to this parlay",
        "redundant",
        ("2026-w10-moneyline-1v2",),
    )


def test_a_team_totals_line_matches_at_two_decimals():
    shown = {**ROSTER_1_OVER, "line": "110.504"}

    assert quote([ROSTER_1_WINS, shown], 10, RUN_ID).probability == 7 / 20


@pytest.mark.parametrize("size", [0, 1, 5])
def test_a_parlay_has_two_to_four_legs(size):
    # Five faulty legs: the size is checked before any leg.
    assert _refusal([FUTURES] * size) == ("A parlay has 2 to 4 legs", "size", ())


@pytest.mark.parametrize(
    ("faulty", "message"),
    [
        ({"market": "2026-w10-moneyline-2v1", "selection": "2"}, "Unknown market"),
        ({"market": "2026-w10-moneyline-1v2", "selection": "7"}, "Unknown selection"),
        (FUTURES, "Futures cannot be parlayed"),
        ({"market": "2026-first_place", "selection": "1"}, "Futures cannot be parlayed"),
        (LAST_WEEK, "Not this week's market"),
    ],
)
def test_a_faulty_leg_is_refused_by_its_key(faulty, message):
    assert _refusal([ROSTER_1_OVER, faulty]) == (message, "leg", (faulty["market"],))


def test_every_faulty_leg_is_named_under_the_first_ones_message():
    refusal = _refusal([FUTURES, ROSTER_1_OVER, LAST_WEEK])

    assert refusal == ("Futures cannot be parlayed", "leg", ("2026-make_playoffs-1", "2026-w09-moneyline-1v2"))


def test_a_faulty_leg_is_refused_before_a_shared_market():
    assert _refusal([ROSTER_1_WINS, ROSTER_2_WINS, FUTURES]) == (
        "Futures cannot be parlayed",
        "leg",
        ("2026-make_playoffs-1",),
    )


@pytest.mark.parametrize(
    ("requests", "named"),
    [
        (["x", "y"], (None, None)),
        ([{}, ROSTER_1_OVER], (None,)),
        ([ROSTER_1_WINS, None, {"selection": "over"}, FUTURES], (None, None, "2026-make_playoffs-1")),
    ],
)
def test_an_entry_that_names_no_market_is_refused_as_an_unknown_one(requests, named):
    assert _refusal(requests) == ("Unknown market", "leg", named)


@pytest.mark.parametrize(
    ("requests", "shared"),
    [
        ([ROSTER_1_WINS, ROSTER_2_WINS], ("2026-w10-moneyline-1v2",)),
        ([ROSTER_1_OVER, ROSTER_1_UNDER], ("2026-w10-team_total-1",)),
        ([ROSTER_1_HIGHEST, ROSTER_2_HIGHEST], ("2026-w10-highest_scorer",)),
        ([ROSTER_1_MINUS_4, ROSTER_2_PLUS_4], ("2026-w10-spread-1v2",)),
        ([ROSTER_1_MINUS_4, {**ROSTER_1_MINUS_4, "line": -9.5}], ("2026-w10-spread-1v2",)),
        (
            [ROSTER_1_WINS, ROSTER_1_OVER, ROSTER_2_WINS, ROSTER_1_UNDER],
            ("2026-w10-moneyline-1v2", "2026-w10-team_total-1"),
        ),
    ],
)
def test_two_legs_from_one_market_are_refused(requests, shared):
    assert _refusal(requests) == ("Two legs from one market", "same_market", shared)


def test_a_shared_market_is_refused_before_moved_odds():
    assert _refusal([ROSTER_1_WINS, ROSTER_2_WINS], run_id=NEWER_RUN)[1] == "same_market"


def test_every_leg_quoted_at_another_run_than_the_windows_has_changed():
    assert _refusal([ROSTER_1_WINS, ROSTER_1_OVER], run_id=NEWER_RUN) == (
        "Odds have changed",
        "odds_changed",
        ("2026-w10-moneyline-1v2", "2026-w10-team_total-1"),
    )


def test_legs_quoted_at_different_runs_name_the_one_that_moved():
    _move_quotes("betting_odds_team_ou", NEWER_RUN, "team_id = 1")

    assert _refusal([ROSTER_1_WINS, ROSTER_1_OVER]) == ("Odds have changed", "odds_changed", ("2026-w10-team_total-1",))
    assert _refusal([ROSTER_1_WINS, ROSTER_1_OVER], run_id=NEWER_RUN)[2] == ("2026-w10-moneyline-1v2",)


@pytest.mark.parametrize("line", [111.0, 110.49, None, "not a line"])
def test_a_team_total_at_another_line_has_changed(line):
    shown = {**ROSTER_1_OVER, "line": line}

    assert _refusal([ROSTER_1_WINS, shown]) == ("Odds have changed", "odds_changed", ("2026-w10-team_total-1",))


def test_a_leg_without_a_price_is_not_offered():
    db.session.execute(text("UPDATE betting_odds_highest_scorer SET odds = NULL WHERE team_id = 1"))
    db.session.commit()

    assert _refusal([ROSTER_1_OVER, ROSTER_1_HIGHEST]) == ("Not offered", "no_price", ("2026-w10-highest_scorer",))


def test_a_leg_without_a_price_comes_after_moved_odds_and_before_the_matrix():
    db.session.execute(text("UPDATE betting_odds_highest_scorer SET odds = NULL WHERE team_id = 1"))
    db.session.execute(text("DELETE FROM simulation_totals"))
    db.session.commit()

    assert _refusal([ROSTER_1_OVER, ROSTER_1_HIGHEST], run_id=NEWER_RUN)[1] == "odds_changed"
    assert _refusal([ROSTER_1_OVER, ROSTER_1_HIGHEST])[2] == ("2026-w10-highest_scorer",)


def test_a_run_without_a_stored_matrix_is_not_offered():
    db.session.execute(text("DELETE FROM simulation_totals"))
    db.session.commit()

    assert _refusal([ROSTER_1_WINS, ROSTER_1_OVER]) == ("Not offered", "no_price", ())


def test_legs_that_never_win_together_are_not_offered():
    # Roster 1 wins in 11 sims and roster 2 has the top score in the other 9, the tie included.
    assert _refusal([ROSTER_1_WINS, ROSTER_2_HIGHEST]) == (CANNOT_PRICE, "impossible", ())


def test_a_joint_chance_of_zero_is_refused_before_an_idle_leg():
    # Without the over the other two legs still win in no sim, so the over adds nothing, but there is no price.
    assert _refusal([ROSTER_1_WINS, ROSTER_2_HIGHEST, ROSTER_1_OVER]) == (CANNOT_PRICE, "impossible", ())


def test_a_moneyline_adds_nothing_to_the_same_teams_highest_score():
    # Rosters 1v2 and 3v4 over six sims. Roster 1 has the top score in the first and fifth and beats roster 2
    # in both; it also beats roster 2 in the third and fourth, where roster 3 or 4 has the top score.
    _publish_matrix(
        NEWER_RUN,
        "1,2,3,4",
        np.array(
            [
                [130.0, 100.0, 110.0, 105.0],
                [100.0, 120.0, 110.0, 90.0],
                [115.0, 105.0, 125.0, 100.0],
                [95.0, 90.0, 100.0, 120.0],
                [120.0, 118.0, 90.0, 100.0],
                [105.0, 110.0, 95.0, 99.0],
            ]
        ),
    )
    _move_quotes("betting_odds_matchup_ml", NEWER_RUN)
    _move_quotes("betting_odds_highest_scorer", NEWER_RUN)

    refusal = _refusal([ROSTER_1_HIGHEST, ROSTER_1_WINS], run_id=NEWER_RUN)

    assert refusal == ("A leg adds nothing to this parlay", "redundant", ("2026-w10-moneyline-1v2",))


def test_every_idle_leg_is_named():
    # With two teams, roster 1 has the top score exactly when roster 2 has the bottom one: 12 sims, the tie included.
    assert _refusal([ROSTER_1_HIGHEST, ROSTER_2_LOWEST]) == (
        "A leg adds nothing to this parlay",
        "redundant",
        ("2026-w10-highest_scorer", "2026-w10-lowest_scorer"),
    )


def test_joint_price_counts_the_sims_every_judged_leg_wins():
    judged = [_judged(True, True, False, True), _judged(True, False, True, True)]

    assert joint_price(judged) == (0.5, "-100")
    assert joint_price(judged[:1]) == (0.75, "-300")


@pytest.mark.parametrize(
    "judged",
    [
        [_judged(True, False, True, False), _judged(False, True, False, True)],
        [_judged(True, True, True, True), _judged(True, True, True, True)],
    ],
)
def test_joint_price_refuses_a_chance_of_zero_or_one(judged):
    with pytest.raises(ParlayRefusal) as refused:
        joint_price(judged)

    assert refused.value.rule == "impossible"
    assert str(refused.value) == CANNOT_PRICE
