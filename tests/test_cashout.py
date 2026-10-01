from datetime import datetime

import numpy as np
import pytest
from sqlalchemy import text

from app import cashout
from app.cashout import NoOffer, Offer, offer_for, offers_for
from app.database import db
from app.markets import parse_key, potential_win
from app.models import Bet, BetLeg
from pipeline import markets as win_rules
from tests.conftest import RUN_ID, SEEDED_CHAMPIONS, SEEDED_POSITIONS, SEEDED_TOTALS, WINDOW_CLOSES_AT

# The run before the seeded one: a bet priced there is offered at the seeded run's matrix.
OLDER_RUN = "2026w10-20261108T140000"
NEWER_RUN = "2026w10-20261110T143000"
NEWER_RUN_CREATED_AT = "2026-11-10T14:30:00+00:00"

NO_OFFER = "No offer for this bet"
CANNOT_PRICE = "No offer: the latest run cannot price this bet"
UNCHANGED = "Odds have not changed since this bet was placed; remove it instead"


pytestmark = pytest.mark.usefixtures("betting_period", "seeded_analytics")


def _leg(market, selection, line=None, price=100):
    return BetLeg(
        season=2026,
        week=parse_key(market).week,
        market=market,
        selection=selection,
        line=line,
        price=price,
        probability=0.5,
    )


def _bet(user, market, selection, line=None, price=100, amount=100.0, run_id=OLDER_RUN, week=10, legs=None):
    bet = Bet(
        user_id=user.id,
        bet_type=parse_key(market).name,
        description=f"{market}: {selection}",
        week=week,
        amount=amount,
        odds=f"{price:+d}",
        price=price,
        probability=0.5,
        run_id=run_id,
        potential_win=potential_win(amount, price),
        legs=legs if legs is not None else [_leg(market, selection, line, price)],
    )
    db.session.add(bet)
    db.session.commit()
    return bet


def _parlay(user, legs, price, probability, run_id=RUN_ID, amount=100.0):
    bet = _bet(user, legs[0].market, legs[0].selection, price=price, amount=amount, run_id=run_id, legs=legs)
    bet.bet_type = "parlay"
    bet.probability = probability
    db.session.commit()
    return bet


def _add_newer_run(totals=None):
    """Publish a week 10 run after the seeded one, with its score matrix when one is given."""
    db.session.execute(
        text("""
        INSERT INTO simulation_runs
            (run_id, season, week, seed, n_sims, model_version, n_teams, draws_path, created_at,
             n_locked, window_closes_at, standings_through_week)
        VALUES (:run_id, 2026, 10, 2, 50000, 'v2', 12, 'draws.npy', :created_at, 1, :closes_at, 9)
    """),
        {"run_id": NEWER_RUN, "created_at": NEWER_RUN_CREATED_AT, "closes_at": WINDOW_CLOSES_AT},
    )
    if totals is not None:
        db.session.execute(
            text("""
            INSERT INTO simulation_totals (run_id, season, week, created_at, n_sims, roster_ids, totals)
            VALUES (:run_id, 2026, 10, :created_at, :n_sims, '1,2', :totals)
        """),
            {
                "run_id": NEWER_RUN,
                "created_at": NEWER_RUN_CREATED_AT,
                "n_sims": len(totals),
                "totals": win_rules.encode_totals(totals),
            },
        )
    db.session.commit()


def _refusal(bet, now=None):
    with pytest.raises(NoOffer) as refused:
        offer_for(bet, now)
    return str(refused.value)


def test_the_worked_example_of_the_design(user):
    # $100 on roster 2 at +105 pays $205; after Thursday's game the newer run has it winning 3,819 of 10,000 sims.
    bet = _bet(user, "2026-w10-moneyline-1v2", "2", price=105, run_id=RUN_ID)
    roster_2_wins = np.arange(10_000) < 3_819
    _add_newer_run(np.column_stack([np.full(10_000, 100.0), np.where(roster_2_wins, 110.0, 90.0)]))

    offer = offer_for(bet)

    assert offer == Offer(bet.id, 74.38, offer.fair_value, 0.3819, NEWER_RUN)
    assert round(offer.fair_value, 2) == 78.29


@pytest.mark.parametrize(
    ("market", "selection", "line", "probability"),
    [
        ("2026-w10-moneyline-1v2", "1", None, 0.55),
        ("2026-w10-moneyline-1v2", "2", None, 0.40),
        ("2026-w10-team_total-1", "over", 110.5, 0.45),
        ("2026-w10-team_total-1", "under", 110.5, 0.50),
        ("2026-w10-highest_scorer", "1", None, 0.60),
        ("2026-w10-lowest_scorer", "1", None, 0.45),
        ("2026-w10-spread-1v2", "1", -4.0, 0.45),
        ("2026-w10-spread-1v2", "2", 9.5, 0.70),
    ],
)
def test_each_weekly_market_is_priced_by_its_win_rule(user, market, selection, line, probability):
    bet = _bet(user, market, selection, line)

    offer = offer_for(bet)

    assert offer.run_id == RUN_ID
    assert offer.probability == pytest.approx(probability)
    assert offer.fair_value == pytest.approx(200.0 * probability)
    assert offer.amount == round(0.95 * 200.0 * probability, 2)


def test_a_team_total_is_priced_at_the_legs_line_not_the_quoted_one(user):
    bet = _bet(user, "2026-w10-team_total-1", "over", line=100.0)

    offer = offer_for(bet)

    assert offer.probability == pytest.approx(np.mean(SEEDED_TOTALS[:, 0] > 100.0))
    assert offer.probability == pytest.approx(0.70)
    assert offer.amount == 133.0


def test_a_spread_is_offered_at_its_own_line_in_the_newer_run(user):
    # Placed at roster 1 -4.0 on the seeded run; the newer run has roster 1 winning by 6 in 7 of 10 sims and
    # losing by 6 in the rest, so -4.0 covers in 7.
    bet = _bet(user, "2026-w10-spread-1v2", "1", line=-4.0, price=122, run_id=RUN_ID)
    _add_newer_run(np.array([[106.0, 100.0]] * 7 + [[100.0, 106.0]] * 3))

    offer = offer_for(bet)

    assert (offer.run_id, offer.probability) == (NEWER_RUN, 0.7)
    assert offer.fair_value == pytest.approx(222.0 * 0.7)
    assert offer.amount == 147.63


def test_a_futures_bet_is_offered_at_the_latest_futures_quote(user):
    # Placed in week 9 at +152; the seeded week 10 run puts roster 1 at 50%. Week 9 has no period, so
    # only the current week's window can open the offer.
    db.session.execute(text("UPDATE betting_odds_make_playoffs SET probability = 0.5 WHERE team_id = 1"))
    db.session.commit()
    bet = _bet(user, "2026-make_playoffs-1", "yes", price=152, run_id="2026w09-20261103T140000", week=9)

    assert offer_for(bet) == Offer(bet.id, 119.7, 126.0, 0.5, RUN_ID)


@pytest.mark.parametrize(("name", "selection"), [("last_place", "2"), ("champion", "1")])
def test_last_place_and_the_champion_are_offered_at_their_latest_quote(user, name, selection):
    # Placed in week 9 at +300; the seeded week 10 run puts the pick at 40%: $400 at 40% is worth $160.
    db.session.execute(text(f"UPDATE betting_odds_{name} SET probability = 0.4"))
    db.session.commit()
    bet = _bet(user, f"2026-{name}", selection, price=300, run_id="2026w09-20261103T140000", week=9)

    assert offer_for(bet) == Offer(bet.id, 152.0, 160.0, 0.4, RUN_ID)


def test_a_no_on_the_playoffs_is_offered_at_its_latest_quote(user):
    # Placed in week 9 at +300; the seeded week 10 run quotes Alice A missing the playoffs at 20%.
    bet = _bet(user, "2026-make_playoffs-1", "no", price=300, run_id="2026w09-20261103T140000", week=9)

    assert offer_for(bet) == Offer(bet.id, 76.0, 80.0, 0.2, RUN_ID)


def test_no_futures_offer_while_the_standings_are_behind(user):
    db.session.execute(
        text("UPDATE simulation_runs SET standings_through_week = 8 WHERE run_id = :run_id"), {"run_id": RUN_ID}
    )
    db.session.commit()
    bet = _bet(user, "2026-make_playoffs-1", "yes", run_id="2026w09-20261103T140000", week=9)

    assert _refusal(bet) == "No offer until the next run: the standings are behind"


def test_a_playoffs_only_rerun_keeps_the_futures_offer(user):
    # The playoffs step reran alone: its futures rows carry a run id with no simulation_runs row, and the
    # standings it used are the week's simulation run's.
    db.session.execute(text("UPDATE betting_odds_make_playoffs SET probability = 0.5, run_id = 'playoffs-only'"))
    db.session.commit()
    bet = _bet(user, "2026-make_playoffs-1", "yes", price=152, run_id="2026w09-20261103T140000", week=9)

    assert offer_for(bet) == Offer(bet.id, 119.7, 126.0, 0.5, "playoffs-only")


def test_no_futures_offer_once_the_latest_run_drops_the_selection(user):
    bet = _bet(user, "2026-champion", "3", run_id="2026w09-20261103T140000", week=9)

    assert _refusal(bet) == CANNOT_PRICE


def test_no_futures_offer_while_its_run_is_the_latest(user):
    bet = _bet(user, "2026-make_playoffs-1", "yes", run_id=RUN_ID)

    assert _refusal(bet) == UNCHANGED


def test_no_offer_while_the_bets_run_is_the_latest(user):
    bet = _bet(user, "2026-w10-moneyline-1v2", "1", run_id=RUN_ID)

    assert _refusal(bet) == UNCHANGED


def test_no_offer_for_a_bet_no_longer_pending(user):
    bet = _bet(user, "2026-w10-moneyline-1v2", "1")
    bet.status = "won"

    assert _refusal(bet) == NO_OFFER


def test_no_offer_for_a_legacy_bet(user):
    bet = _bet(user, "2026-w10-highest_scorer", "1", run_id=None, legs=[])

    assert _refusal(bet) == NO_OFFER


def test_no_offer_when_the_market_key_does_not_parse(user):
    bet = _bet(user, "2026-w10-moneyline-1v2", "1")
    bet.legs[0].market = "2026-w10-parlay"

    assert _refusal(bet) == NO_OFFER


def test_no_offer_while_betting_is_paused(user):
    bet = _bet(user, "2026-w10-moneyline-1v2", "1")

    assert _refusal(bet, now=datetime.fromisoformat(WINDOW_CLOSES_AT)) == "Betting is paused until the odds update"


@pytest.mark.parametrize("change", ["is_locked", "is_settled"])
def test_no_offer_once_the_week_is_closed(user, betting_period, change):
    setattr(betting_period, change, True)
    db.session.commit()
    bet = _bet(user, "2026-w10-moneyline-1v2", "1")

    assert _refusal(bet) == "Betting is closed for week 10"


@pytest.mark.parametrize("selection", ["over", "under"])
def test_no_offer_when_every_sim_or_none_wins(user, selection):
    # Roster 1 scores at least 90 in every seeded sim.
    bet = _bet(user, "2026-w10-team_total-1", selection, line=80.0)

    assert _refusal(bet) == CANNOT_PRICE


def test_no_offer_for_a_roster_missing_from_the_matrix(user):
    bet = _bet(user, "2026-w10-moneyline-1v3", "1")

    assert _refusal(bet) == CANNOT_PRICE


def test_no_offer_when_the_latest_run_has_no_stored_matrix(user):
    _add_newer_run()
    bet = _bet(user, "2026-w10-moneyline-1v2", "1")

    assert _refusal(bet) == CANNOT_PRICE


def test_no_offer_under_a_cent(user):
    bet = _bet(user, "2026-w10-lowest_scorer", "1", price=-1000, amount=0.01)

    assert _refusal(bet) == CANNOT_PRICE


# Parlays placed on the seeded run, where roster 1 wins and tops 110.5 in 7 of 20 sims (+186), and
# roster 2 also tops 95 in 5 of those (+300).
ROSTER_1_WINS = ("2026-w10-moneyline-1v2", "1", None, -150)
ROSTER_1_OVER = ("2026-w10-team_total-1", "over", 110.5, -120)
ROSTER_2_OVER = ("2026-w10-team_total-2", "over", 95.0, 105)

# The newer run's 10 sims. Roster 1 wins and tops 110.5 in the first four, with roster 2 over 95 in
# three of them; it wins under its line in three, and loses in the last three.
NEWER_TOTALS = np.array(
    [
        [120.0, 100.0],
        [120.0, 100.0],
        [120.0, 100.0],
        [125.0, 90.0],
        [105.0, 100.0],
        [105.0, 100.0],
        [105.0, 100.0],
        [115.0, 125.0],
        [115.0, 125.0],
        [100.0, 110.0],
    ]
)


def _legs(*picks):
    return [_leg(market, selection, line, price) for market, selection, line, price in picks]


def test_a_parlay_is_offered_at_the_joint_chance_of_its_legs_in_the_newer_run(user):
    # $100 at +186 pays $286; in the newer run roster 1 wins and tops 110.5 in 4 of 10 sims.
    bet = _parlay(user, _legs(ROSTER_1_WINS, ROSTER_1_OVER), price=186, probability=7 / 20)
    _add_newer_run(NEWER_TOTALS)

    offer = offer_for(bet)

    assert offer == Offer(bet.id, 108.68, offer.fair_value, 0.4, NEWER_RUN)
    assert round(offer.fair_value, 2) == 114.4


def test_a_three_leg_parlay_is_offered_at_the_sims_where_all_three_win(user):
    # $50 at +300 pays $200; in the newer run all three legs win in 3 of 10 sims.
    legs = _legs(ROSTER_1_WINS, ROSTER_1_OVER, ROSTER_2_OVER)
    bet = _parlay(user, legs, price=300, probability=5 / 20, amount=50.0)
    _add_newer_run(NEWER_TOTALS)

    offer = offer_for(bet)

    assert offer == Offer(bet.id, 57.0, offer.fair_value, 0.3, NEWER_RUN)
    assert round(offer.fair_value, 2) == 60.0


def test_no_offer_for_a_parlay_whose_legs_cannot_all_win_in_the_newer_run(user):
    # Roster 1 wins only under its line, and tops it only in a loss.
    bet = _parlay(user, _legs(ROSTER_1_WINS, ROSTER_1_OVER), price=186, probability=7 / 20)
    _add_newer_run(np.array([[105.0, 100.0]] * 5 + [[115.0, 120.0]] * 5))

    assert _refusal(bet) == CANNOT_PRICE


def test_no_offer_for_a_parlay_while_its_run_is_the_latest(user):
    bet = _parlay(user, _legs(ROSTER_1_WINS, ROSTER_1_OVER), price=186, probability=7 / 20)

    assert _refusal(bet) == UNCHANGED


def test_no_offer_for_a_parlay_while_betting_is_paused(user):
    bet = _parlay(user, _legs(ROSTER_1_WINS, ROSTER_1_OVER), price=186, probability=7 / 20, run_id=OLDER_RUN)

    assert _refusal(bet, now=datetime.fromisoformat(WINDOW_CLOSES_AT)) == "Betting is paused until the odds update"


def test_offers_for_reads_a_weeks_window_and_matrix_once(user, monkeypatch):
    decode_totals = win_rules.decode_totals
    betting_window = cashout.betting_window
    calls = []

    def counted(function, name):
        def wrapper(*args, **kwargs):
            calls.append(name)
            return function(*args, **kwargs)

        return wrapper

    monkeypatch.setattr(win_rules, "decode_totals", counted(decode_totals, "decode"))
    monkeypatch.setattr(cashout, "betting_window", counted(betting_window, "window"))
    single = _bet(user, "2026-w10-highest_scorer", "2")
    parlay = _parlay(user, _legs(ROSTER_1_WINS, ROSTER_1_OVER), price=186, probability=0.5, run_id=OLDER_RUN)

    offers = offers_for([single, parlay])

    assert offers.keys() == {single.id, parlay.id}
    assert offers[parlay.id].probability == pytest.approx(7 / 20)
    assert calls == ["window", "decode"]


def test_offers_for_leaves_out_the_bets_without_an_offer(user):
    priced = _bet(user, "2026-w10-moneyline-1v2", "1")
    legacy = _bet(user, "2026-w10-highest_scorer", "1", run_id=None, legs=[])
    unchanged = _bet(user, "2026-w10-lowest_scorer", "2", run_id=RUN_ID)

    offers = offers_for([priced, legacy, unchanged])

    assert offers == {priced.id: offer_for(priced)}


# A futures parlay placed at the seeded run, Alice A and Bob B both to make the playoffs, 8 of its 20 seasons.
NEWER_FUTURES_RUN = "2026w10-20261110T150000"
BOTH_IN = (("2026-make_playoffs-1", "yes", None, -400), ("2026-make_playoffs-2", "yes", None, -150))


def _add_newer_futures_run(positions, champions):
    """Publish a newer playoffs run: its standings matrix, and every make-playoffs row re-quoted at it."""
    db.session.execute(
        text("""
        INSERT INTO simulation_standings (run_id, season, week, created_at, n_sims, playoff_teams, roster_ids, standings)
        VALUES (:run_id, 2026, 10, '2026-11-10T15:00:00+00:00', :n_sims, 2, '1,2,3,4', :standings)
    """),
        {
            "run_id": NEWER_FUTURES_RUN,
            "n_sims": len(positions),
            "standings": win_rules.encode_standings(positions, champions),
        },
    )
    db.session.execute(text("UPDATE betting_odds_make_playoffs SET run_id = :run_id"), {"run_id": NEWER_FUTURES_RUN})
    db.session.commit()


def test_a_futures_parlay_is_offered_at_the_joint_chance_of_its_legs_in_the_newer_futures_run(user):
    # $100 at +150 pays $250. The newer run lifts Bob B into the playoffs, and champion, in four of the seeded
    # seasons where only Alice A made them, so both make them in 12 of 20.
    bet = _parlay(user, _legs(*BOTH_IN), price=150, probability=0.4)
    lifted = [8, 10, 12, 13]
    positions, champions = SEEDED_POSITIONS.copy(), SEEDED_CHAMPIONS.copy()
    positions[lifted] = [[1, 2, 4, 3], [2, 1, 4, 3], [1, 2, 4, 3], [2, 1, 4, 3]]
    champions[lifted] = 1
    _add_newer_futures_run(positions, champions)

    offer = offer_for(bet)

    assert offer == Offer(bet.id, 142.5, 150.0, 0.6, NEWER_FUTURES_RUN)


def test_no_offer_for_a_futures_parlay_while_its_run_is_the_latest(user):
    bet = _parlay(user, _legs(*BOTH_IN), price=150, probability=0.4)

    assert _refusal(bet) == UNCHANGED


def test_no_offer_for_a_futures_parlay_whose_newer_run_has_no_stored_standings(user):
    bet = _parlay(user, _legs(*BOTH_IN), price=150, probability=0.4)
    db.session.execute(text("UPDATE betting_odds_make_playoffs SET run_id = :run_id"), {"run_id": NEWER_FUTURES_RUN})
    db.session.commit()

    assert _refusal(bet) == CANNOT_PRICE
