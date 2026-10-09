"""The admin's actions on betting periods and bets, shared by the admin page and the `flask admin` commands.

Each action commits its own work, so a bet settled before a later failure stands.
"""

from datetime import UTC, datetime

from . import ledger, settlement
from .database import db
from .models import Bet, BettingPeriod
from .routes.helpers import get_current_season, query_analytics


class NoPublishedLeague(Exception):
    pass


def parse_lock_time(text):
    """A lock time as the admin page and the commands give it, YYYY-MM-DDTHH:MM in UTC."""
    return datetime.strptime(text, "%Y-%m-%dT%H:%M").replace(tzinfo=UTC)


def set_period(week, lock_time):
    """Create the week's betting period in the published league's season, or move its lock and reopen it."""
    season = _published_season()
    period = db.session.query(BettingPeriod).filter_by(season=season, week=week).first()
    if period is None:
        period = BettingPeriod(season=season, week=week, lock_time=lock_time, is_locked=False, is_settled=False)
        db.session.add(period)
    else:
        period.lock_time = lock_time
        period.is_locked = False
    db.session.commit()
    return period


def _published_season():
    """A season begins at its league's first publish, so a new season's week 1 cannot land in the last one."""
    rows = query_analytics("SELECT MAX(CAST(season AS INTEGER)) AS season FROM sleeper_leagues")
    if rows[0]["season"] is None:
        raise NoPublishedLeague("No league is published; publish the league step first")
    return rows[0]["season"]


def close_week(week):
    """Mark the current season's week settled, which closes its window; False when the week has no period."""
    period = db.session.query(BettingPeriod).filter_by(season=get_current_season(), week=week).first()
    if period is None:
        return False
    period.is_settled = True
    db.session.commit()
    return True


def settles_by_hand(bet):
    """A single, or a futures parlay, which may hold a champion leg decided only after the final. A weekly parlay
    settles from the preview, which judges it leg by leg, dropping pushed legs and re-pricing the rest."""
    return len(bet.legs) < 2 or all(leg.week is None for leg in bet.legs)


def settle_by_hand(bet, won, settling_week):
    """Win or lose a bet the preview cannot decide; False when it was already settled."""
    if not ledger.settle(bet, won, week=result_week(bet, settling_week)):
        db.session.rollback()
        return False
    db.session.commit()
    return True


def settle_as_shown(result, shown_outcome, week):
    """Settle one bet in its own transaction if its outcome is still the one shown; return why not, or None."""
    if result.outcome == settlement.UNDECIDED:
        return "undecided"
    if result.outcome != shown_outcome:
        return f"scores changed: now {result.outcome}"

    posts_to = result_week(result.bet, week)
    if result.outcome == settlement.PUSH:
        closed = ledger.push(result.bet, leg_statuses=result.leg_statuses, week=posts_to)
    else:
        closed = ledger.settle(
            result.bet,
            won=result.outcome == settlement.WON,
            potential_win=result.potential_win,
            leg_statuses=result.leg_statuses,
            week=posts_to,
        )
    if not closed:
        db.session.rollback()
        return "already settled"
    db.session.commit()
    return None


def missing_bet_reason(bet_id):
    """Why a bet the preview showed is no longer among the week's pending bets."""
    bet = db.session.get(Bet, bet_id)
    return "already settled" if bet is not None and bet.status != "pending" else "not found"


def result_week(bet, settling_week):
    """The week a bet's result posts to: a futures bet's is the week it settles in, opened first; any other its own.
    Both weeks are in the bet's season."""
    is_futures = bool(bet.legs) and all(leg.week is None for leg in bet.legs)
    if not is_futures:
        return bet.week
    ledger.open_week(bet.user_id, bet.season, settling_week)
    return settling_week
