from datetime import UTC, datetime, timedelta

import pytest

from app import ledger
from app.database import db
from app.models import Bet, BetLeg, BettingPeriod, WeeklyStats


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


def _parlay_on(user, week, markets):
    """A two-leg parlay at +300 on the markets given as (key, leg week)."""
    return Bet(
        user_id=user.id,
        bet_type="parlay",
        description=" + ".join(market for market, _ in markets),
        amount=10.0,
        odds="+300",
        potential_win=30.0,
        week=week,
        legs=[
            BetLeg(season=2026, week=leg_week, market=market, selection="yes", price=-150, probability=0.6)
            for market, leg_week in markets
        ],
    )


WEEKLY_PARLAY = [("2026-w10-moneyline-1v2", 10), ("2026-w10-team_total-1", 10)]
FUTURES_PARLAY = [("2026-make_playoffs-1", None), ("2026-make_playoffs-2", None)]


def test_pending_bets_say_which_bets_settle_by_hand(admin_client, admin_user, betting_period, db_session):
    legacy = Bet(
        user_id=admin_user.id,
        bet_type="moneyline",
        description="Legacy",
        amount=10.0,
        odds="+100",
        potential_win=10.0,
        week=10,
    )
    db_session.session.add_all(
        [
            _bet_on(admin_user, "moneyline", "2026-w10-moneyline-1v2", 10, 10),
            _bet_on(admin_user, "champion", "2026-champion", 10, None),
            _parlay_on(admin_user, 10, WEEKLY_PARLAY),
            _parlay_on(admin_user, 10, FUTURES_PARLAY),
            legacy,
        ]
    )
    db_session.session.commit()

    bets = admin_client.get("/api/admin/pending_bets").get_json()

    assert [(bet["bet_type"], bet["by_hand"]) for bet in bets] == [
        ("moneyline", True),
        ("champion", True),
        ("parlay", False),
        ("parlay", True),
        ("moneyline", True),
    ]


def _place_in_week_8(bet):
    ledger.open_week(bet.user_id, 8)
    ledger.place(bet)
    db.session.commit()
    return bet


def _weekly(user_id, week):
    stats = db.session.query(WeeklyStats).filter_by(user_id=user_id, week=week).one()
    return stats.bets_placed, stats.bets_won, stats.active_bets_amount, stats.settled_pnl


def test_a_futures_parlay_settled_by_hand_posts_its_result_to_the_settling_week(
    admin_client, admin_user, betting_period, db_session
):
    parlay = _place_in_week_8(_parlay_on(admin_user, 8, FUTURES_PARLAY))

    reply = admin_client.post("/api/admin/settle_bet", json={"bet_id": parlay.id, "won": True}).get_json()

    assert reply == {"success": True}
    db.session.refresh(parlay)
    assert [parlay.status] + [leg.status for leg in parlay.legs] == ["won", "won", "won"]
    assert _weekly(admin_user.id, 10) == (0, 1, 0.0, 30.0)
    assert _weekly(admin_user.id, 8) == (1, 0, 0.0, 0.0)


def test_a_lost_futures_parlay_takes_its_legs_with_it(admin_client, admin_user, betting_period, db_session):
    parlay = _place_in_week_8(_parlay_on(admin_user, 8, FUTURES_PARLAY))

    admin_client.post("/api/admin/settle_bet", json={"bet_id": parlay.id, "won": False})

    db.session.refresh(parlay)
    assert [parlay.status] + [leg.status for leg in parlay.legs] == ["lost", "lost", "lost"]
    assert _weekly(admin_user.id, 10) == (0, 0, 0.0, -10.0)


def test_a_weekly_parlay_is_still_not_settled_by_hand(admin_client, admin_user, betting_period, db_session):
    parlay = _place_in_week_8(_parlay_on(admin_user, 8, WEEKLY_PARLAY))

    reply = admin_client.post("/api/admin/settle_bet", json={"bet_id": parlay.id, "won": True}).get_json()

    assert reply == {"success": False, "error": "Parlays settle from the Settle Week card"}
    db.session.refresh(parlay)
    assert [parlay.status] + [leg.status for leg in parlay.legs] == ["pending", "pending", "pending"]


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
