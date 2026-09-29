from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app.database import db
from app.models import BettingPeriod
from app.windows import Window, betting_window
from tests.conftest import RUN_CREATED_AT, RUN_ID, WINDOW_CLOSES_AT, WINDOW_NOW

RUN_CREATED = datetime(2026, 11, 10, 14, tzinfo=UTC)
CLOSES = datetime(2026, 11, 13, 0, 15, tzinfo=UTC)


def add_run(run_id, created_at, window_closes_at, week=10, season=2026):
    db.session.execute(
        text("""
        INSERT INTO simulation_runs
            (run_id, season, week, seed, n_sims, model_version, n_teams, draws_path, created_at,
             n_locked, window_closes_at, standings_through_week)
        VALUES (:run_id, :season, :week, 1, 50000, 'v2', 12, 'draws.npy', :created_at, 0, :closes_at, 9)
    """),
        {"run_id": run_id, "season": season, "week": week, "created_at": created_at, "closes_at": window_closes_at},
    )
    db.session.commit()


def test_open_before_the_window_closes(betting_period, seeded_analytics):
    window = betting_window(10, now=CLOSES - timedelta(minutes=1))

    assert window == Window(10, "open", CLOSES, RUN_ID, RUN_CREATED)


def test_paused_once_the_window_has_closed(betting_period, seeded_analytics):
    assert betting_window(10, now=CLOSES).state == "paused"
    assert betting_window(10, now=CLOSES + timedelta(days=1)) == Window(10, "paused", CLOSES, RUN_ID, RUN_CREATED)


def test_now_defaults_to_the_clock(betting_period, seeded_analytics):
    assert betting_window(10).state == "open"


def test_closed_without_a_betting_period(seeded_analytics):
    assert betting_window(10) == Window(10, "closed")


def test_closed_once_the_period_is_settled(betting_period, seeded_analytics):
    betting_period.is_settled = True
    db.session.commit()

    assert betting_window(10) == Window(10, "closed")


def test_closed_by_the_admin_lock_with_its_time(betting_period, seeded_analytics):
    betting_period.is_locked = True
    db.session.commit()

    window = betting_window(10)

    assert (window.state, window.run_id, window.closes_at) == ("closed", None, None)
    assert window.lock_time.replace(tzinfo=UTC) == betting_period.lock_time.replace(tzinfo=UTC)


def test_a_passed_lock_time_closes_the_week_and_locks_it(betting_period, seeded_analytics):
    betting_period.lock_time = datetime.now(UTC) - timedelta(hours=1)
    db.session.commit()

    window = betting_window(10)

    assert window.state == "closed"
    assert window.lock_time is not None
    assert db.session.get(BettingPeriod, betting_period.id).is_locked is True


@pytest.mark.parametrize("now", [CLOSES - timedelta(hours=1), CLOSES + timedelta(hours=1)])
def test_the_window_never_flips_the_lock(betting_period, seeded_analytics, now):
    betting_window(10, now=now)

    assert db.session.get(BettingPeriod, betting_period.id).is_locked is False


def test_closed_without_a_run(betting_period, analytics_tables):
    assert betting_window(10) == Window(10, "closed")


def test_closed_when_the_run_has_no_window(betting_period, seeded_analytics):
    db.session.execute(text("UPDATE simulation_runs SET window_closes_at = NULL"))
    db.session.commit()

    assert betting_window(10) == Window(10, "closed", None, RUN_ID, RUN_CREATED)


def test_a_naive_timestamp_is_read_as_utc(betting_period, seeded_analytics):
    db.session.execute(
        text("UPDATE simulation_runs SET created_at = '2026-11-10T14:00:00', window_closes_at = '2026-11-13T00:15:00'")
    )
    db.session.commit()

    window = betting_window(10, now=CLOSES - timedelta(minutes=1))

    assert (window.closes_at, window.run_created_at) == (CLOSES, RUN_CREATED)


def test_the_latest_run_of_the_week_wins(betting_period, seeded_analytics):
    rerun_closes = "2026-11-15T18:00:00+00:00"
    add_run("2026w10-20261113T060000", "2026-11-13T06:00:00+00:00", rerun_closes)
    add_run("2026w10-20261101T060000", "2026-11-01T06:00:00+00:00", None)

    window = betting_window(10, now=CLOSES + timedelta(hours=6))

    assert (window.state, window.run_id) == ("open", "2026w10-20261113T060000")
    assert window.closes_at == datetime.fromisoformat(rerun_closes)


def test_another_season_of_the_same_week_is_ignored(betting_period, seeded_analytics):
    add_run("2025w10-20251112T140000", "2025-11-12T14:00:00+00:00", "2025-11-15T00:15:00+00:00", season=2025)

    assert betting_window(10).run_id == RUN_ID


def test_the_endpoint_reports_the_current_week_signed_out(client, betting_period, seeded_analytics):
    reply = client.get("/api/betting_window").get_json()

    assert reply == {
        "success": True,
        "week": 10,
        "state": "open",
        "closes_at": WINDOW_CLOSES_AT,
        "run_created_at": RUN_CREATED_AT,
    }


def test_the_endpoint_reports_a_paused_week(client, betting_period, seeded_analytics):
    db.session.execute(
        text("UPDATE simulation_runs SET window_closes_at = :closes"), {"closes": WINDOW_NOW.isoformat()}
    )
    db.session.commit()

    reply = client.get("/api/betting_window?week=10").get_json()

    assert (reply["state"], reply["closes_at"], reply["run_created_at"]) == (
        "paused",
        WINDOW_NOW.isoformat(),
        RUN_CREATED_AT,
    )


def test_the_endpoint_reports_a_closed_week_without_the_lock_time(client, betting_period, seeded_analytics):
    betting_period.is_locked = True
    db.session.commit()

    reply = client.get("/api/betting_window?week=10").get_json()

    assert reply == {"success": True, "week": 10, "state": "closed", "closes_at": None, "run_created_at": None}


def test_the_endpoint_takes_any_week(client, betting_period, seeded_analytics):
    reply = client.get("/api/betting_window?week=11").get_json()

    assert reply == {"success": True, "week": 11, "state": "closed", "closes_at": None, "run_created_at": None}
