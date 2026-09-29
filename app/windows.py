"""Whether a week is open for betting.

Betting is open while the odds on the page come from a run made before the next kickoff. Each
published run carries the kickoff that closes its window; the window closing is not a lock,
because the next run reopens it. The admin's `lock_time` stays the hard close and the kill switch.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from .database import db
from .models import BettingPeriod, utc_now
from .routes.helpers import check_betting_period_lock, query_analytics

LATEST_RUN_SQL = """
    SELECT run_id, created_at, window_closes_at
    FROM simulation_runs
    WHERE week = :week
    ORDER BY season DESC, created_at DESC, run_id DESC
    LIMIT 1
"""


@dataclass(frozen=True)
class Window:
    week: int
    state: str  # "open" | "paused" | "closed"
    closes_at: datetime | None = None
    run_id: str | None = None
    run_created_at: datetime | None = None
    lock_time: datetime | None = None  # set only when the admin's lock closed the week


def betting_window(week, now=None):
    """The week's window: closed without a live period, under the admin's lock, or without a run's window."""
    period = db.session.query(BettingPeriod).filter_by(week=week).first()
    if period is None or period.is_settled:
        return Window(week, "closed")

    lock_time = check_betting_period_lock(period)
    if lock_time is not None:
        return Window(week, "closed", lock_time=lock_time)

    run = _latest_run(week)
    if run is None:
        return Window(week, "closed")
    created_at = _as_utc(run["created_at"])
    if run["window_closes_at"] is None:
        return Window(week, "closed", run_id=run["run_id"], run_created_at=created_at)

    closes_at = _as_utc(run["window_closes_at"])
    state = "open" if (now or utc_now()) < closes_at else "paused"
    return Window(week, state, closes_at, run["run_id"], created_at)


def latest_run_id(week):
    """The week's latest published run, read without the lock, or None before the first."""
    run = _latest_run(week)
    return run["run_id"] if run else None


def _latest_run(week):
    runs = query_analytics(LATEST_RUN_SQL, {"week": week})
    return runs[0] if runs else None


def _as_utc(timestamp):
    """A published ISO 8601 timestamp as an aware datetime; the pipeline writes UTC, so a naive one is UTC."""
    parsed = datetime.fromisoformat(timestamp)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed
