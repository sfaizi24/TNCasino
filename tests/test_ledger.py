from functools import partial

import pytest
from sqlalchemy import select, text

from app import ledger
from app.database import db
from app.models import Bet, BetLeg, WeeklyStats
from tests.conftest import RUN_ID

STAKE = {"market": "2026-w10-team_total-1", "selection": "under", "line": 110.5, "run_id": RUN_ID, "amount": 100}
# The run a cash-out is priced from: one published after the bet's.
LATER_RUN_ID = "2026w10-20261113T140000"

EVENTS = {
    "settle": partial(ledger.settle, won=True),
    "push": ledger.push,
    "void": ledger.void,
    "remove": ledger.remove,
    "cash_out": partial(ledger.cash_out, offer=137.5, run_id=LATER_RUN_ID, week=10),
}


def _leg(market="2026-w10-highest_scorer", selection="1"):
    return BetLeg(season=2026, week=10, market=market, selection=selection, price=100, probability=0.5)


def _bet(user, amount, legs=None):
    return Bet(
        user_id=user.id,
        bet_type="highest_scorer",
        description="Player A: Highest Scorer +100",
        week=10,
        amount=amount,
        odds="+100",
        potential_win=amount,
        status="pending",
        legs=legs or [_leg()],
    )


def _placed_bet(user, amount=100.0, legs=None):
    """Place a bet and hand it back detached, the way a request that read it while pending holds it."""
    bet = _bet(user, amount, legs)
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


def _money(user, week=10):
    stats = db.session.query(WeeklyStats).filter_by(user_id=user.id, week=week).one()
    return {
        "account_balance": user.account_balance,
        "total_pnl": user.total_pnl,
        "bets": db.session.query(Bet).count(),
        "starting_balance": stats.starting_balance,
        "ending_balance": stats.ending_balance,
        "pnl": stats.pnl,
        "active_bets_amount": stats.active_bets_amount,
        "settled_pnl": stats.settled_pnl,
        "bets_placed": stats.bets_placed,
        "bets_won": stats.bets_won,
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
        "legs": ["2026-w10-team_total-1"],
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
        ("cash_out", "cash_out"),
        ("settle", "cash_out"),
        ("cash_out", "settle"),
        ("void", "cash_out"),
        ("remove", "cash_out"),
        ("cash_out", "remove"),
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


@pytest.mark.parametrize(("offer", "profit"), [(137.5, 37.5), (62.5, -37.5)])
def test_cash_out_in_the_bets_week_books_the_profit_there(user, offer, profit):
    bet = _placed_bet(user)

    assert ledger.cash_out(bet, offer, LATER_RUN_ID, week=10) is True
    db.session.commit()

    assert _money(user) == {
        "account_balance": 1000.0 + profit,
        "total_pnl": profit,
        "bets": 1,
        "starting_balance": 1000.0,
        "ending_balance": 1000.0 + profit,
        "pnl": profit,
        "active_bets_amount": 0.0,
        "settled_pnl": profit,
        "bets_placed": 1,
        "bets_won": 0,
    }


@pytest.mark.parametrize(("offer", "profit"), [(137.5, 37.5), (62.5, -37.5)])
def test_cash_out_in_a_later_week_books_the_profit_in_that_week(user, offer, profit):
    bet = _placed_bet(user)
    ledger.open_week(user.id, 11)

    assert ledger.cash_out(bet, offer, LATER_RUN_ID, week=11) is True
    db.session.commit()

    assert _money(user, week=10) == {
        "account_balance": 1000.0 + profit,
        "total_pnl": profit,
        "bets": 1,
        "starting_balance": 1000.0,
        "ending_balance": 1000.0 + profit,
        "pnl": profit,
        "active_bets_amount": 0.0,
        "settled_pnl": 0.0,
        "bets_placed": 1,
        "bets_won": 0,
    }
    assert _money(user, week=11) == {
        "account_balance": 1000.0 + profit,
        "total_pnl": profit,
        "bets": 1,
        "starting_balance": 900.0,
        "ending_balance": 1000.0 + profit,
        "pnl": offer,
        "active_bets_amount": 0.0,
        "settled_pnl": profit,
        "bets_placed": 0,
        "bets_won": 0,
    }


def test_a_cash_out_for_the_stake_books_no_profit_in_either_week(user):
    bet = _placed_bet(user, 100.0)
    ledger.open_week(user.id, 11)

    assert ledger.cash_out(bet, 100.0, LATER_RUN_ID, week=11) is True
    db.session.commit()

    assert user.account_balance == 1000.0
    assert user.total_pnl == 0.0
    assert _money(user, week=10)["settled_pnl"] == 0.0
    assert _money(user, week=11)["settled_pnl"] == 0.0


def test_a_second_cash_out_in_a_later_week_changes_nothing(user):
    bet = _placed_bet(user)
    ledger.open_week(user.id, 11)
    assert ledger.cash_out(bet, 137.5, LATER_RUN_ID, week=11) is True
    db.session.commit()
    cashed_out = (_money(user, week=10), _money(user, week=11))

    assert ledger.cash_out(bet, 150.0, LATER_RUN_ID, week=11) is False
    db.session.rollback()

    assert (_money(user, week=10), _money(user, week=11)) == cashed_out


def test_cash_out_records_the_offer_on_the_bet_and_closes_its_legs(user):
    bet = _placed_bet(user)

    ledger.cash_out(bet, 62.5, LATER_RUN_ID, week=10)
    db.session.commit()

    cashed_out = db.session.get(Bet, bet.id)
    assert (cashed_out.status, cashed_out.result) == ("cashed_out", -37.5)
    assert (cashed_out.cash_out_amount, cashed_out.cash_out_run_id) == (62.5, LATER_RUN_ID)
    assert cashed_out.cashed_out_at is not None
    assert cashed_out.settled_at == cashed_out.cashed_out_at
    assert [(leg.status, leg.settled_at) for leg in cashed_out.legs] == [("cashed_out", cashed_out.cashed_out_at)]


def _placed_parlay(user):
    """A $100 three-leg parlay that pays $100, placed and detached, with its leg ids in placement order."""
    legs = [_leg(), _leg("2026-w10-moneyline-1v2", "1"), _leg("2026-w10-team_total-1", "over")]
    bet = _placed_bet(user, legs=legs)
    leg_ids = db.session.scalars(select(BetLeg.id).where(BetLeg.bet_id == bet.id).order_by(BetLeg.id)).all()
    return bet, leg_ids


def _leg_statuses(bet):
    legs = db.session.query(BetLeg).filter_by(bet_id=bet.id).order_by(BetLeg.id)
    return [(leg.status, leg.settled_at) for leg in legs]


def test_a_win_at_an_adjusted_potential_win_pays_and_records_it(user):
    bet, _ = _placed_parlay(user)

    assert ledger.settle(bet, won=True, potential_win=66.67) is True
    db.session.commit()

    settled = db.session.get(Bet, bet.id)
    assert (settled.status, settled.potential_win, settled.result) == ("won", 66.67, 66.67)
    assert _money(user) == {
        "account_balance": 1066.67,
        "total_pnl": 66.67,
        "bets": 1,
        "starting_balance": 1000.0,
        "ending_balance": 1066.67,
        "pnl": pytest.approx(66.67),
        "active_bets_amount": 0.0,
        "settled_pnl": 66.67,
        "bets_placed": 1,
        "bets_won": 1,
    }


@pytest.mark.parametrize(
    ("won", "statuses"),
    [(True, ["won", "push", "won"]), (False, ["won", "push", "lost"])],
)
def test_each_leg_takes_its_own_status_when_given(user, won, statuses):
    bet, leg_ids = _placed_parlay(user)

    assert ledger.settle(bet, won=won, leg_statuses=dict(zip(leg_ids, statuses, strict=True))) is True
    db.session.commit()

    settled_at = db.session.get(Bet, bet.id).settled_at
    assert _leg_statuses(bet) == [(status, settled_at) for status in statuses]


@pytest.mark.parametrize(("won", "status", "balance"), [(True, "won", 1100.0), (False, "lost", 900.0)])
def test_by_default_every_leg_takes_the_bets_status_and_the_bet_pays_its_potential_win(user, won, status, balance):
    bet, _ = _placed_parlay(user)

    assert ledger.settle(bet, won=won) is True
    db.session.commit()

    settled = db.session.get(Bet, bet.id)
    assert (settled.status, settled.potential_win) == (status, 100.0)
    assert [leg_status for leg_status, _ in _leg_statuses(bet)] == [status, status, status]
    assert _money(user)["account_balance"] == balance
