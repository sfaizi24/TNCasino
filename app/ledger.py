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
        bet,
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
        bet,
        bets_placed=WeeklyStats.bets_placed - 1,
        active_bets_amount=WeeklyStats.active_bets_amount - bet.amount,
    )
    return True


def settle(bet, won):
    if won:
        return _close(
            bet,
            "won",
            result=bet.potential_win,
            payout=bet.amount + bet.potential_win,
            bets_won=WeeklyStats.bets_won + 1,
        )
    return _close(bet, "lost", result=-bet.amount, payout=0.0)


def push(bet):
    """A result exactly on the line: the stake comes back and the bet still counts as placed."""
    return _close(bet, "push", result=0.0, payout=bet.amount)


def void(bet):
    """A bet that should never have stood: the stake comes back and it no longer counts as placed."""
    return _close(bet, "void", result=0.0, payout=bet.amount, bets_placed=WeeklyStats.bets_placed - 1)


def _close(bet, status, result, payout, **counters):
    settled_at = datetime.now(UTC)
    close = (
        update(Bet)
        .where(Bet.id == bet.id, Bet.status == "pending")
        .values(status=status, result=result, settled_at=settled_at)
    )
    if _execute(close).rowcount == 0:
        return False

    _execute(update(BetLeg).where(BetLeg.bet_id == bet.id).values(status=status, settled_at=settled_at))
    _execute(
        update(User)
        .where(User.id == bet.user_id)
        .values(account_balance=User.account_balance + payout, total_pnl=User.total_pnl + result)
    )
    _update_weekly_stats(
        bet,
        active_bets_amount=WeeklyStats.active_bets_amount - bet.amount,
        settled_pnl=WeeklyStats.settled_pnl + result,
        **counters,
    )
    return True


def _update_weekly_stats(bet, **counters):
    balance = _balance(bet.user_id)
    _execute(
        update(WeeklyStats)
        .where(WeeklyStats.user_id == bet.user_id, WeeklyStats.week == bet.week)
        .values(**counters, ending_balance=balance, pnl=balance - WeeklyStats.starting_balance)
    )


def _balance(user_id):
    return select(User.account_balance).where(User.id == user_id).scalar_subquery()


def _execute(statement):
    return db.session.execute(statement.execution_options(synchronize_session=False))
