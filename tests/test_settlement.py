import pytest

from app.models import Bet, WeeklyStats
from tests.conftest import RUN_ID

HIGHEST_SCORER_BET = {"market": "2026-w10-highest_scorer", "selection": "1", "run_id": RUN_ID, "amount": 100}


def _place_legacy_bet(db_session, user, betting_period):
    """Store a bet as they were before markets, with no run, price or legs, and its weekly stats."""
    user.account_balance -= 100.0
    bet = Bet(
        user_id=user.id,
        bet_type="highest_scorer",
        description="Player A: Highest Scorer +200",
        amount=100.0,
        odds="+200",
        potential_win=200.0,
        status="pending",
        week=betting_period.week,
    )
    stat = WeeklyStats(
        user_id=user.id,
        week=betting_period.week,
        starting_balance=1000.0,
        ending_balance=900.0,
        pnl=-100.0,
        active_bets_amount=100.0,
        settled_pnl=0.0,
        bets_placed=1,
        bets_won=0,
    )
    db_session.session.add_all([bet, stat])
    db_session.session.commit()
    return bet, stat


def test_settle_bet_won(admin_client, user, admin_user, betting_period, db_session):
    bet, stat = _place_legacy_bet(db_session, user, betting_period)

    resp = admin_client.post(
        "/api/admin/settle_bet",
        json={
            "bet_id": bet.id,
            "won": True,
        },
    )
    assert resp.get_json()["success"] is True

    db_session.session.refresh(bet)
    db_session.session.refresh(user)

    assert bet.status == "won"
    assert bet.legs == []
    assert bet.result == 200.0
    assert user.account_balance == 900.0 + 100.0 + 200.0  # refund + winnings
    assert user.total_pnl == 200.0


def test_settle_bet_lost(admin_client, user, admin_user, betting_period, db_session):
    bet, stat = _place_legacy_bet(db_session, user, betting_period)

    resp = admin_client.post(
        "/api/admin/settle_bet",
        json={
            "bet_id": bet.id,
            "won": False,
        },
    )
    assert resp.get_json()["success"] is True

    db_session.session.refresh(bet)
    db_session.session.refresh(user)

    assert bet.status == "lost"
    assert bet.result == -100.0
    assert user.account_balance == 900.0  # no refund
    assert user.total_pnl == -100.0


def test_settle_won_updates_weekly_stats(admin_client, user, admin_user, betting_period, db_session):
    bet, stat = _place_legacy_bet(db_session, user, betting_period)

    admin_client.post(
        "/api/admin/settle_bet",
        json={
            "bet_id": bet.id,
            "won": True,
        },
    )

    db_session.session.refresh(stat)
    db_session.session.refresh(user)

    assert stat.active_bets_amount == 0.0
    assert stat.settled_pnl == 200.0
    assert stat.bets_won == 1
    assert stat.ending_balance == user.account_balance


def test_settle_lost_updates_weekly_stats(admin_client, user, admin_user, betting_period, db_session):
    bet, stat = _place_legacy_bet(db_session, user, betting_period)

    admin_client.post(
        "/api/admin/settle_bet",
        json={
            "bet_id": bet.id,
            "won": False,
        },
    )

    db_session.session.refresh(stat)

    assert stat.active_bets_amount == 0.0
    assert stat.settled_pnl == -100.0
    assert stat.bets_won == 0


def test_settle_already_settled_bet(admin_client, user, admin_user, betting_period, db_session):
    bet, _ = _place_legacy_bet(db_session, user, betting_period)
    bet.status = "won"
    db_session.session.commit()

    resp = admin_client.post(
        "/api/admin/settle_bet",
        json={
            "bet_id": bet.id,
            "won": True,
        },
    )
    data = resp.get_json()

    assert data["success"] is False
    assert "already settled" in data["error"].lower()


def test_settle_nonexistent_bet(admin_client, admin_user):
    resp = admin_client.post(
        "/api/admin/settle_bet",
        json={
            "bet_id": 9999,
            "won": True,
        },
    )
    data = resp.get_json()

    assert data["success"] is False
    assert "not found" in data["error"].lower()


def test_settle_week_marks_period_settled(admin_client, admin_user, betting_period, db_session):
    resp = admin_client.post(
        "/api/admin/settle_week",
        json={
            "week": betting_period.week,
        },
    )
    assert resp.get_json()["success"] is True

    db_session.session.refresh(betting_period)
    assert betting_period.is_settled is True


# The admin places these bets too, so one signed-in client books and settles them.


@pytest.mark.parametrize(("won", "status"), [(True, "won"), (False, "lost")])
def test_settling_a_bet_settles_its_leg_with_it(
    admin_client, betting_period, seeded_analytics, db_session, won, status
):
    bet_id = admin_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()["bet_id"]

    reply = admin_client.post("/api/admin/settle_bet", json={"bet_id": bet_id, "won": won}).get_json()

    bet = db_session.session.get(Bet, bet_id)
    [leg] = bet.legs
    assert reply == {"success": True}
    assert (bet.status, leg.status) == (status, status)
    assert leg.settled_at is not None
    assert leg.settled_at == bet.settled_at


def test_a_second_settlement_leaves_the_leg_as_first_settled(
    admin_client, betting_period, seeded_analytics, db_session
):
    bet_id = admin_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()["bet_id"]
    admin_client.post("/api/admin/settle_bet", json={"bet_id": bet_id, "won": True})

    reply = admin_client.post("/api/admin/settle_bet", json={"bet_id": bet_id, "won": False}).get_json()

    assert reply == {"success": False, "error": "Bet already settled"}
    assert [leg.status for leg in db_session.session.get(Bet, bet_id).legs] == ["won"]
