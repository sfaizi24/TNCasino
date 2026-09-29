from datetime import UTC, datetime, timedelta

import pytest

from app.database import db
from app.models import Bet, BetLeg, BettingPeriod


def test_admin_page_requires_admin(logged_in_client, user):
    resp = logged_in_client.get("/admin", follow_redirects=False)
    assert resp.status_code == 302


def test_admin_page_accessible_to_admin(admin_client, admin_user):
    resp = admin_client.get("/admin")
    assert resp.status_code == 200


def test_admin_api_rejects_anonymous(client):
    resp = client.get("/api/admin/pending_bets")
    # Should redirect to login or return 401
    assert resp.status_code in (302, 401)


def test_pending_bets_default_to_the_current_week(admin_client, admin_user, db_session):
    db_session.session.add(BettingPeriod(week=12, lock_time=datetime.now(UTC)))
    for week in (10, 12):
        db_session.session.add(
            Bet(
                user_id=admin_user.id,
                bet_type="moneyline",
                description=f"Week {week} bet",
                amount=10.0,
                odds="+100",
                potential_win=10.0,
                week=week,
            )
        )
    db_session.session.commit()

    bets = admin_client.get("/api/admin/pending_bets").get_json()

    assert [bet["description"] for bet in bets] == ["Week 12 bet"]


def _bet_on(user, bet_type, market, week, leg_week):
    return Bet(
        user_id=user.id,
        bet_type=bet_type,
        description=f"Week {week}: {market}",
        amount=10.0,
        odds="+100",
        potential_win=10.0,
        week=week,
        legs=[BetLeg(season=2026, week=leg_week, market=market, selection="1", price=100, probability=0.5)],
    )


def test_pending_bets_keep_every_pending_futures_bet_whatever_its_week(admin_client, admin_user, db_session):
    db_session.session.add(BettingPeriod(week=12, lock_time=datetime.now(UTC)))
    db_session.session.add_all(
        [
            _bet_on(admin_user, "moneyline", "2026-w10-moneyline-1v2", 10, 10),
            _bet_on(admin_user, "champion", "2026-champion", 10, None),
            _bet_on(admin_user, "moneyline", "2026-w12-moneyline-1v2", 12, 12),
        ]
    )
    db_session.session.commit()

    bets = admin_client.get("/api/admin/pending_bets").get_json()

    assert [bet["description"] for bet in bets] == ["Week 10: 2026-champion", "Week 12: 2026-w12-moneyline-1v2"]


def test_set_betting_period(admin_client, admin_user, db_session):
    resp = admin_client.post(
        "/api/admin/set_betting_period",
        json={
            "week": 15,
            "lock_time": "2026-12-25T18:00",
        },
    )
    assert resp.get_json()["success"] is True

    period = db.session.query(BettingPeriod).filter_by(week=15).first()
    assert period is not None
    assert period.is_locked is False
    assert period.lock_time.year == 2026
    assert period.lock_time.month == 12


def _unlock(client, period, **body):
    period.is_locked = True
    db.session.commit()
    reply = client.post("/api/admin/unlock_period", json={"week": period.week, **body}).get_json()
    db.session.refresh(period)
    return reply


def _as_utc(moment):
    # SQLite hands back naive datetimes; PostgreSQL keeps the zone.
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def test_unlocking_with_a_lock_time_locks_at_it_in_utc(admin_client, betting_period):
    reply = _unlock(admin_client, betting_period, lock_time="2026-10-04T13:30")

    assert reply == {"success": True}
    assert betting_period.is_locked is False
    assert _as_utc(betting_period.lock_time) == datetime(2026, 10, 4, 13, 30, tzinfo=UTC)


def test_unlocking_without_a_lock_time_locks_a_week_ahead(admin_client, betting_period):
    before = datetime.now(UTC)

    reply = _unlock(admin_client, betting_period)

    assert reply == {"success": True}
    assert betting_period.is_locked is False
    week_ahead = _as_utc(betting_period.lock_time) - before
    assert timedelta(days=7) <= week_ahead < timedelta(days=7, minutes=1)


@pytest.mark.parametrize("lock_time", ["2026-10-04 13:30", "Sunday", ""])
def test_unlocking_with_a_malformed_lock_time_is_refused(admin_client, betting_period, lock_time):
    reply = _unlock(admin_client, betting_period, lock_time=lock_time)

    assert reply == {"success": False, "error": "Invalid lock time"}
    assert betting_period.is_locked is True
