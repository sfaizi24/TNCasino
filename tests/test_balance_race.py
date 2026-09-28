import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

from app.database import db
from app.models import Bet, BettingPeriod, User, WeeklyStats
from tests.conftest import RUN_ID, create_analytics_tables, seed_analytics

STAKE = {"market": "2026-w10-team_total-1", "selection": "under", "line": 110.5, "run_id": RUN_ID, "amount": 100}


def _seed(app):
    with app.app_context():
        create_analytics_tables(db.session)
        seed_analytics(db.session)
        db.session.add_all(
            [
                User(id="racer", username="racer", account_balance=1000.0, total_pnl=0.0),
                User(id="admin", username="admin", account_balance=1000.0, total_pnl=0.0, is_admin=True),
                BettingPeriod(week=10, lock_time=datetime.now(UTC) + timedelta(days=7)),
            ]
        )
        db.session.commit()


def _signed_in(app, user_id):
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = user_id
    return client


def _post_all_at_once(clients, url, payload):
    """Release one request per client at the same moment and count the outcomes."""
    start = threading.Barrier(len(clients), timeout=30)

    def post(client):
        start.wait()
        reply = client.post(url, json=payload).get_json()
        return "accepted" if reply["success"] else reply["error"]

    with ThreadPoolExecutor(max_workers=len(clients)) as pool:
        return Counter(pool.map(post, clients))


def _money(app):
    with app.app_context():
        racer = db.session.get(User, "racer")
        week = db.session.query(WeeklyStats).filter_by(user_id="racer", week=10).one()
        return {
            "account_balance": racer.account_balance,
            "total_pnl": racer.total_pnl,
            "bets": db.session.query(Bet).count(),
            "active_bets_amount": week.active_bets_amount,
            "bets_placed": week.bets_placed,
            "bets_won": week.bets_won,
        }


def test_twenty_stakes_of_100_spend_at_most_the_1000_balance(file_backed_app):
    _seed(file_backed_app)
    clients = [_signed_in(file_backed_app, "racer") for _ in range(20)]

    outcomes = _post_all_at_once(clients, "/api/place_bet", STAKE)

    assert outcomes == {"accepted": 10, "Insufficient balance": 10}
    assert _money(file_backed_app) == {
        "account_balance": 0.0,
        "total_pnl": 0.0,
        "bets": 10,
        "active_bets_amount": 1000.0,
        "bets_placed": 10,
        "bets_won": 0,
    }


def test_twenty_settlements_of_one_bet_pay_it_once(file_backed_app):
    _seed(file_backed_app)
    _signed_in(file_backed_app, "racer").post("/api/place_bet", json=STAKE)
    with file_backed_app.app_context():
        bet_id = db.session.query(Bet.id).scalar()
    clients = [_signed_in(file_backed_app, "admin") for _ in range(20)]

    outcomes = _post_all_at_once(clients, "/api/admin/settle_bet", {"bet_id": bet_id, "won": True})

    assert outcomes == {"accepted": 1, "Bet already settled": 19}
    assert _money(file_backed_app) == {
        "account_balance": 1100.0,
        "total_pnl": 100.0,
        "bets": 1,
        "active_bets_amount": 0.0,
        "bets_placed": 1,
        "bets_won": 1,
    }
