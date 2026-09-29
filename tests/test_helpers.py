from datetime import UTC, datetime, timedelta

from app.models import BettingPeriod
from app.routes.helpers import check_betting_period_lock, get_current_week


def test_get_current_week_returns_active_period(db_session):
    period = BettingPeriod(
        week=12,
        lock_time=datetime.now(UTC) + timedelta(days=7),
        is_locked=False,
        is_settled=False,
    )
    db_session.session.add(period)
    db_session.session.commit()

    assert get_current_week() == 12


def test_get_current_week_defaults_when_no_periods(db_session):
    assert get_current_week() == 10


def test_check_lock_returns_none_for_unlocked_future_period(db_session):
    period = BettingPeriod(
        week=10,
        lock_time=datetime.now(UTC) + timedelta(days=7),
        is_locked=False,
        is_settled=False,
    )
    db_session.session.add(period)
    db_session.session.commit()

    assert check_betting_period_lock(period) is None


def test_check_lock_auto_locks_past_due_period(db_session):
    period = BettingPeriod(
        week=10,
        lock_time=datetime.now(UTC) - timedelta(hours=1),
        is_locked=False,
        is_settled=False,
    )
    db_session.session.add(period)
    db_session.session.commit()

    result = check_betting_period_lock(period)
    assert result is not None
    assert result == period.lock_time

    db_session.session.refresh(period)
    assert period.is_locked is True


def test_check_lock_returns_lock_time_for_already_locked(db_session):
    lock_time = datetime.now(UTC) + timedelta(days=1)
    period = BettingPeriod(
        week=10,
        lock_time=lock_time,
        is_locked=True,
        is_settled=False,
    )
    db_session.session.add(period)
    db_session.session.commit()

    result = check_betting_period_lock(period)
    # SQLite strips timezone info, so compare without tzinfo
    assert result.replace(tzinfo=None) == lock_time.replace(tzinfo=None)
