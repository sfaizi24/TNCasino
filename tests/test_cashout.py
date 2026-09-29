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
from tests.conftest import RUN_ID, SEEDED_TOTALS, WINDOW_CLOSES_AT

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


def test_a_futures_bet_is_offered_at_the_latest_futures_quote(user):
    # Placed in week 9 at +152; the seeded week 10 run puts roster 1 at 50%. Week 9 has no period, so
    # only the current week's window can open the offer.
    db.session.execute(text("UPDATE betting_odds_make_playoffs SET probability = 0.5 WHERE team_id = 1"))
    db.session.commit()
    bet = _bet(user, "2026-make_playoffs-1", "yes", price=152, run_id="2026w09-20261103T140000", week=9)

    assert offer_for(bet) == Offer(bet.id, 119.7, 126.0, 0.5, RUN_ID)


def test_no_futures_offer_while_the_standings_are_behind(user):
    db.session.execute(
        text("UPDATE simulation_runs SET standings_through_week = 8 WHERE run_id = :run_id"), {"run_id": RUN_ID}
    )
    db.session.commit()
    bet = _bet(user, "2026-make_playoffs-1", "yes", run_id="2026w09-20261103T140000", week=9)

    assert _refusal(bet) == "No offer until the next run: the standings are behind"


def test_no_futures_offer_once_the_latest_run_drops_the_selection(user):
    bet = _bet(user, "2026-first_place", "3", run_id="2026w09-20261103T140000", week=9)

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


def test_no_offer_for_a_parlay(user):
    legs = [_leg("2026-w10-moneyline-1v2", "1"), _leg("2026-w10-highest_scorer", "1")]
    bet = _bet(user, "2026-w10-moneyline-1v2", "1", legs=legs)

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
    first = _bet(user, "2026-w10-moneyline-1v2", "1")
    second = _bet(user, "2026-w10-highest_scorer", "2")

    offers = offers_for([first, second])

    assert offers.keys() == {first.id, second.id}
    assert calls == ["window", "decode"]


def test_offers_for_leaves_out_the_bets_without_an_offer(user):
    priced = _bet(user, "2026-w10-moneyline-1v2", "1")
    legacy = _bet(user, "2026-w10-highest_scorer", "1", run_id=None, legs=[])
    parlay = _bet(
        user,
        "2026-w10-moneyline-1v2",
        "2",
        legs=[_leg("2026-w10-moneyline-1v2", "2"), _leg("2026-w10-lowest_scorer", "2")],
    )
    unchanged = _bet(user, "2026-w10-lowest_scorer", "2", run_id=RUN_ID)

    offers = offers_for([priced, legacy, parlay, unchanged])

    assert offers == {priced.id: offer_for(priced)}
