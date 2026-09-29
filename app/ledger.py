"""The only code in the app that moves money.

Each event runs its guard statement first and returns whether the guard changed a row. Nothing
here commits: the route commits or rolls back once. Balances and counters change by SQL arithmetic
on the stored values, so requests that arrive together cannot overwrite each other.
"""

from datetime import UTC, datetime

from sqlalchemy import insert, select, update
from sqlalchemy.exc import IntegrityError

from .database import db
from .models import Bet, BetLeg, User, WeeklyStats


def open_week(user_id, week):
    """Create the user's stats row for the week in its own transaction, before any money moves."""
    existing = select(WeeklyStats.id).where(WeeklyStats.user_id == user_id, WeeklyStats.week == week)
    if db.session.scalar(existing) is not None:
        return

    balance = _balance(user_id)
    try:
        db.session.execute(
            insert(WeeklyStats).values(
                user_id=user_id, week=week, starting_balance=balance, ending_balance=balance, pnl=0.0
            )
        )
        db.session.commit()
    except IntegrityError:
        # Another request opened the week first; its row is the one to use.
        db.session.rollback()


def place(bet):
    stake = (
        update(User)
        .where(User.id == bet.user_id, User.account_balance >= bet.amount)
        .values(account_balance=User.account_balance - bet.amount)
    )
    if _execute(stake).rowcount == 0:
        return False

    db.session.add(bet)
    _update_weekly_stats(
        bet.user_id,
        bet.week,
        bets_placed=WeeklyStats.bets_placed + 1,
        active_bets_amount=WeeklyStats.active_bets_amount + bet.amount,
    )
    return True


def remove(bet):
    withdraw = update(Bet).where(Bet.id == bet.id, Bet.status == "pending").values(status="removed")
    if _execute(withdraw).rowcount == 0:
        return False

    _execute(update(BetLeg).where(BetLeg.bet_id == bet.id).values(status="void"))
    _execute(update(User).where(User.id == bet.user_id).values(account_balance=User.account_balance + bet.amount))
    _update_weekly_stats(
        bet.user_id,
        bet.week,
        bets_placed=WeeklyStats.bets_placed - 1,
        active_bets_amount=WeeklyStats.active_bets_amount - bet.amount,
    )
    return True


def settle(bet, won, potential_win=None, leg_statuses=None, week=None):
    """A win pays `potential_win` when given, as a parlay whose pushed legs dropped out does, and records it.

    The result posts to `week`, by default the bet's own; the stake always leaves the bet's own week.
    """
    if not won:
        return _close(bet, "lost", result=-bet.amount, payout=0.0, leg_statuses=leg_statuses, week=week)

    win = bet.potential_win if potential_win is None else potential_win
    return _close(
        bet,
        "won",
        result=win,
        payout=bet.amount + win,
        leg_statuses=leg_statuses,
        potential_win=potential_win,
        week=week,
        bets_won=WeeklyStats.bets_won + 1,
    )


def push(bet, leg_statuses=None, week=None):
    """A result exactly on the line: the stake comes back and the bet still counts as placed."""
    return _close(bet, "push", result=0.0, payout=bet.amount, leg_statuses=leg_statuses, week=week)


def void(bet):
    """A bet that should never have stood: the stake comes back and it no longer counts as placed."""
    return _close(bet, "void", result=0.0, payout=bet.amount, bets_placed=WeeklyStats.bets_placed - 1)


def cash_out(bet, offer, run_id, week):
    """Close a pending bet for the offer; only the profit or loss, never the stake, posts to `week`."""
    cashed_out_at = datetime.now(UTC)
    result = round(offer - bet.amount, 2)
    close = (
        update(Bet)
        .where(Bet.id == bet.id, Bet.status == "pending")
        .values(
            status="cashed_out",
            result=result,
            cash_out_amount=offer,
            cash_out_run_id=run_id,
            cashed_out_at=cashed_out_at,
            settled_at=cashed_out_at,
        )
    )
    if _execute(close).rowcount == 0:
        return False

    _close_legs(bet, "cashed_out", cashed_out_at)
    _pay(bet.user_id, offer, result)
    _post_result(bet, week, result)
    return True


def _close(bet, status, result, payout, leg_statuses=None, potential_win=None, week=None, **counters):
    settled_at = datetime.now(UTC)
    close = (
        update(Bet)
        .where(Bet.id == bet.id, Bet.status == "pending")
        .values(status=status, result=result, settled_at=settled_at)
    )
    if potential_win is not None:
        close = close.values(potential_win=potential_win)
    if _execute(close).rowcount == 0:
        return False

    _close_legs(bet, status, settled_at, leg_statuses)
    _pay(bet.user_id, payout, result)
    _post_result(bet, bet.week if week is None else week, result, **counters)
    return True


def _post_result(bet, week, result, **counters):
    """The stake leaves the bet's own week; the result and the counters post to `week`."""
    _update_weekly_stats(bet.user_id, bet.week, active_bets_amount=WeeklyStats.active_bets_amount - bet.amount)
    _update_weekly_stats(bet.user_id, week, settled_pnl=WeeklyStats.settled_pnl + result, **counters)


def _close_legs(bet, status, settled_at, leg_statuses=None):
    """Every leg takes the bet's status, unless `leg_statuses` gives each leg of a parlay its own."""
    if leg_statuses is None:
        _execute(update(BetLeg).where(BetLeg.bet_id == bet.id).values(status=status, settled_at=settled_at))
        return
    for leg_id, leg_status in leg_statuses.items():
        _execute(
            update(BetLeg)
            .where(BetLeg.id == leg_id, BetLeg.bet_id == bet.id)
            .values(status=leg_status, settled_at=settled_at)
        )


def _pay(user_id, payout, result):
    _execute(
        update(User)
        .where(User.id == user_id)
        .values(account_balance=User.account_balance + payout, total_pnl=User.total_pnl + result)
    )


def _update_weekly_stats(user_id, week, **counters):
    balance = _balance(user_id)
    _execute(
        update(WeeklyStats)
        .where(WeeklyStats.user_id == user_id, WeeklyStats.week == week)
        .values(**counters, ending_balance=balance, pnl=balance - WeeklyStats.starting_balance)
    )


def _balance(user_id):
    return select(User.account_balance).where(User.id == user_id).scalar_subquery()


def _execute(statement):
    return db.session.execute(statement.execution_options(synchronize_session=False))
