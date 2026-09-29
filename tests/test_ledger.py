from functools import partial

import pytest
from sqlalchemy import text

from app import ledger
from app.database import db
from app.models import Bet, WeeklyStats
from tests.conftest import RUN_ID

STAKE = {"market": "2026-w10-team_total-1", "selection": "under", "line": 110.5, "run_id": RUN_ID, "amount": 100}

EVENTS = {
    "settle": partial(ledger.settle, won=True),
    "push": ledger.push,
    "void": ledger.void,
    "remove": ledger.remove,
}


def _bet(user, amount):
    return Bet(
        user_id=user.id,
        bet_type="highest_scorer",
        description="Player A: Highest Scorer +100",
        week=10,
        amount=amount,
        odds="+100",
        potential_win=amount,
        status="pending",
    )


def _placed_bet(user, amount=100.0):
    """Place a bet and hand it back detached, the way a request that read it while pending holds it."""
    bet = _bet(user, amount)
    ledger.open_week(user.id, bet.week)
    assert ledger.place(bet)
    db.session.commit()
    db.session.refresh(bet)
    db.session.expunge(bet)
    return bet


def _set_stored_balance(user, balance):
    """Change the balance in the database only, so the user loaded in this session goes stale."""
    db.session.execute(
        text("UPDATE users SET account_balance = :balance WHERE id = :id"), {"balance": balance, "id": user.id}
    )


def _money(user):
    week = db.session.query(WeeklyStats).filter_by(user_id=user.id, week=10).one()
    return {
        "account_balance": user.account_balance,
        "total_pnl": user.total_pnl,
        "bets": db.session.query(Bet).count(),
        "starting_balance": week.starting_balance,
        "ending_balance": week.ending_balance,
        "pnl": week.pnl,
        "active_bets_amount": week.active_bets_amount,
        "settled_pnl": week.settled_pnl,
        "bets_placed": week.bets_placed,
        "bets_won": week.bets_won,
    }


def test_stake_one_cent_over_the_balance_changes_nothing(user):
    _placed_bet(user, 400.0)
    before = _money(user)

    assert ledger.place(_bet(user, 600.01)) is False
    db.session.rollback()

    assert _money(user) == before


def test_stake_of_the_whole_balance_is_accepted(user):
    _placed_bet(user, 1000.0)

    assert _money(user)["account_balance"] == 0.0


def test_stake_comes_off_the_stored_balance_not_the_loaded_one(
    logged_in_client, user, betting_period, seeded_analytics
):
    _set_stored_balance(user, 300.0)
    assert user.account_balance == 1000.0

    reply = logged_in_client.post("/api/place_bet", json=STAKE).get_json()

    assert reply == {
        "success": True,
        "new_balance": 200.0,
        "bet_id": 1,
        "market": "2026-w10-team_total-1",
        "selection": "under",
        "price": 100,
    }
    assert _money(user)["starting_balance"] == 300.0


def test_stake_the_stored_balance_cannot_cover_is_refused(logged_in_client, user, betting_period, seeded_analytics):
    _set_stored_balance(user, 50.0)
    assert user.account_balance == 1000.0

    reply = logged_in_client.post("/api/place_bet", json=STAKE).get_json()

    assert reply == {"success": False, "error": "Insufficient balance"}
    assert _money(user)["account_balance"] == 50.0
    assert db.session.query(Bet).count() == 0


def test_settling_twice_pays_once(user):
    bet = _placed_bet(user)
    assert ledger.settle(bet, won=True) is True
    db.session.commit()
    paid = _money(user)

    assert ledger.settle(bet, won=True) is False
    db.session.rollback()

    assert paid["account_balance"] == 1100.0
    assert _money(user) == paid


def test_removing_twice_refunds_once(user):
    bet = _placed_bet(user)
    assert ledger.remove(bet) is True
    db.session.commit()
    refunded = _money(user)

    assert ledger.remove(bet) is False
    db.session.rollback()

    assert refunded["account_balance"] == 1000.0
    assert _money(user) == refunded


def test_settling_a_removed_bet_pays_nothing(user):
    bet = _placed_bet(user)
    ledger.remove(bet)
    db.session.commit()
    removed = _money(user)

    assert ledger.settle(bet, won=True) is False
    db.session.rollback()

    assert _money(user) == removed


def test_removing_a_settled_bet_refunds_nothing(user):
    bet = _placed_bet(user)
    ledger.settle(bet, won=False)
    db.session.commit()
    settled = _money(user)

    assert ledger.remove(bet) is False
    db.session.rollback()

    assert _money(user) == settled


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("push", "push"),
        ("settle", "push"),
        ("push", "settle"),
        ("void", "void"),
        ("settle", "void"),
        ("void", "settle"),
        ("push", "void"),
        ("remove", "void"),
        ("void", "remove"),
    ],
)
def test_an_event_on_a_bet_that_is_no_longer_pending_changes_nothing(user, first, second):
    bet = _placed_bet(user)
    assert EVENTS[first](bet) is True
    db.session.commit()
    closed = _money(user)

    assert EVENTS[second](bet) is False
    db.session.rollback()

    assert _money(user) == closed


@pytest.mark.parametrize(("event", "bets_placed"), [("push", 2), ("void", 1)])
def test_push_and_void_refund_the_stake_and_only_a_push_still_counts_as_placed(user, event, bets_placed):
    earlier = _placed_bet(user)
    ledger.settle(earlier, won=True)
    db.session.commit()
    bet = _placed_bet(user, 50.0)

    assert EVENTS[event](bet) is True
    db.session.commit()

    assert _money(user) == {
        "account_balance": 1100.0,
        "total_pnl": 100.0,
        "bets": 2,
        "starting_balance": 1000.0,
        "ending_balance": 1100.0,
        "pnl": 100.0,
        "active_bets_amount": 0.0,
        "settled_pnl": 100.0,
        "bets_placed": bets_placed,
        "bets_won": 1,
    }
    closed = db.session.get(Bet, bet.id)
    assert (closed.status, closed.result) == (event, 0.0)
    assert closed.settled_at is not None


def test_second_bet_of_the_week_adds_to_the_counters(user):
    _placed_bet(user, 100.0)
    _placed_bet(user, 50.0)

    assert _money(user) == {
        "account_balance": 850.0,
        "total_pnl": 0.0,
        "bets": 2,
        "starting_balance": 1000.0,
        "ending_balance": 850.0,
        "pnl": -150.0,
        "active_bets_amount": 150.0,
        "settled_pnl": 0.0,
        "bets_placed": 2,
        "bets_won": 0,
    }
