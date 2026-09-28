from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from app import ledger
from app.database import db
from app.models import Bet, BettingPeriod, WeeklyStats
from tests.conftest import RUN_ID

HIGHEST_SCORER_BET = {"market": "2026-w10-highest_scorer", "selection": "1", "run_id": RUN_ID, "amount": 100}
TEAM_TOTAL_BET = {
    "market": "2026-w10-team_total-1",
    "selection": "over",
    "line": 110.5,
    "run_id": RUN_ID,
    "amount": 100,
}

# bet_type, key, selection, line, description, odds and the potential win on a 100 stake, from the seeded tables.
SIX_MARKETS = [
    ("moneyline", "2026-w10-moneyline-1v2", "1", None, "Alice A vs Bob B: Alice A -150", "-150", 66.67),
    ("team_total", "2026-w10-team_total-1", "over", 110.5, "Alice A O/U 110.50: Over", "-120", 83.33),
    ("highest_scorer", "2026-w10-highest_scorer", "1", None, "Alice A: Highest Scorer +185", "+185", 185.0),
    ("lowest_scorer", "2026-w10-lowest_scorer", "2", None, "Bob B: Lowest Scorer +230", "+230", 230.0),
    ("first_place", "2026-first_place", "2", None, "Bob B: First Place +150", "+150", 150.0),
    ("make_playoffs", "2026-make_playoffs-1", "yes", None, "Alice A: Make Playoffs -400", "-400", 25.0),
]


@pytest.mark.parametrize(
    ("bet_type", "market", "selection", "line", "description", "odds", "potential_win"), SIX_MARKETS
)
def test_each_market_is_priced_from_its_table_not_the_request(
    logged_in_client,
    user,
    betting_period,
    seeded_analytics,
    bet_type,
    market,
    selection,
    line,
    description,
    odds,
    potential_win,
):
    stake = {"market": market, "selection": selection, "line": line, "run_id": RUN_ID, "amount": 100}

    reply = logged_in_client.post("/api/place_bet", json={**stake, "odds": "+900", "price": 900}).get_json()

    assert reply == {
        "success": True,
        "new_balance": 900.0,
        "bet_id": 1,
        "market": market,
        "selection": selection,
        "price": int(odds),
    }
    bet = db.session.get(Bet, 1)
    assert bet.bet_type == bet_type
    assert bet.description == description
    assert bet.odds == odds
    assert bet.potential_win == pytest.approx(potential_win, abs=0.01)


def test_a_placed_bet_records_its_quote_and_one_leg(logged_in_client, user, betting_period, seeded_analytics):
    logged_in_client.post("/api/place_bet", json=TEAM_TOTAL_BET)

    bet = db.session.query(Bet).one()
    assert (bet.run_id, bet.price, bet.probability, bet.week, bet.status) == (RUN_ID, -120, 0.55, 10, "pending")
    [leg] = bet.legs
    assert (leg.season, leg.week, leg.market, leg.selection) == (2026, 10, "2026-w10-team_total-1", "over")
    assert (leg.line, leg.price, leg.probability) == (110.5, -120, 0.55)
    assert (leg.status, leg.settled_at) == ("pending", None)


def test_a_futures_bet_posts_to_the_current_week(logged_in_client, user, betting_period, seeded_analytics):
    logged_in_client.post(
        "/api/place_bet", json={"market": "2026-first_place", "selection": "2", "run_id": RUN_ID, "amount": 100}
    )

    bet = db.session.query(Bet).one()
    assert bet.week == 10
    assert (bet.legs[0].season, bet.legs[0].week) == (2026, None)


def test_a_stale_run_is_refused_with_the_new_quote(logged_in_client, user, betting_period, seeded_analytics):
    db.session.execute(text("UPDATE betting_odds_highest_scorer SET run_id = 'new-run'"))
    db.session.execute(text("UPDATE betting_odds_highest_scorer SET odds = '+210' WHERE team_id = 1"))
    db.session.commit()

    reply = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()

    assert reply == {
        "success": False,
        "error": "Odds have changed",
        "run_id": "new-run",
        "price": 210,
        "odds": "+210",
        "line": None,
    }
    db.session.refresh(user)
    assert user.account_balance == 1000.0
    assert db.session.query(Bet).count() == 0
    assert db.session.query(WeeklyStats).count() == 0


def test_a_team_total_at_a_moved_line_is_refused(logged_in_client, user, betting_period, seeded_analytics):
    reply = logged_in_client.post("/api/place_bet", json={**TEAM_TOTAL_BET, "line": 111.25}).get_json()

    assert (reply["error"], reply["line"], reply["run_id"]) == ("Odds have changed", 110.5, RUN_ID)
    assert db.session.query(Bet).count() == 0


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"market": "2026-w10-moneyline-1v2", "selection": "3"}, "Unknown selection"),
        ({"market": "2026-w09-highest_scorer"}, "Not this week's market"),
        ({"market": "2026-w10-spread-1v2"}, "Unknown market"),
        ({"market": "2026-w10-team_total-7", "selection": "over", "line": 100.0}, "Unknown market"),
    ],
)
def test_a_pick_the_week_does_not_offer_is_refused(
    logged_in_client, user, betting_period, seeded_analytics, change, error
):
    reply = logged_in_client.post("/api/place_bet", json={**HIGHEST_SCORER_BET, **change}).get_json()

    assert reply == {"success": False, "error": error}
    assert db.session.query(Bet).count() == 0


def test_a_side_without_a_price_is_not_offered(logged_in_client, user, betting_period, seeded_analytics):
    db.session.execute(text("UPDATE betting_odds_highest_scorer SET odds = NULL WHERE team_id = 1"))
    db.session.commit()

    reply = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()

    assert reply == {"success": False, "error": "Not offered"}
    assert db.session.query(Bet).count() == 0


def test_place_bet_insufficient_balance(logged_in_client, user, betting_period, seeded_analytics):
    reply = logged_in_client.post("/api/place_bet", json={**HIGHEST_SCORER_BET, "amount": 1500}).get_json()

    assert reply == {"success": False, "error": "Insufficient balance"}
    db.session.refresh(user)
    assert user.account_balance == 1000.0


def test_place_bet_invalid_amount(logged_in_client, user, betting_period, seeded_analytics):
    reply = logged_in_client.post("/api/place_bet", json={**HIGHEST_SCORER_BET, "amount": 0}).get_json()

    assert reply == {"success": False, "error": "Invalid bet amount"}


def test_place_bet_creates_weekly_stats(logged_in_client, user, betting_period, seeded_analytics):
    logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET)

    stat = db.session.query(WeeklyStats).filter_by(user_id=user.id).first()
    assert stat is not None
    assert stat.starting_balance == 1000.0
    assert stat.bets_placed == 1
    assert stat.active_bets_amount == 100.0


def test_place_bet_updates_existing_weekly_stats(logged_in_client, user, betting_period, seeded_analytics):
    logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET)
    logged_in_client.post(
        "/api/place_bet",
        json={"market": "2026-w10-lowest_scorer", "selection": "2", "run_id": RUN_ID, "amount": 50},
    )

    stat = db.session.query(WeeklyStats).filter_by(user_id=user.id).first()
    assert stat.bets_placed == 2
    assert stat.active_bets_amount == 150.0


def test_place_bet_locked_period(logged_in_client, user, db_session, seeded_analytics):
    period = BettingPeriod(
        week=10,
        lock_time=datetime.now(UTC) - timedelta(hours=1),
        is_locked=True,
        is_settled=False,
    )
    db_session.session.add(period)
    db_session.session.commit()

    reply = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()

    assert reply["success"] is False
    assert "locked" in reply["error"].lower()
    assert db.session.query(Bet).count() == 0


def test_my_bets_lists_the_pick_and_whether_it_can_be_removed(logged_in_client, user, betting_period, seeded_analytics):
    logged_in_client.post("/api/place_bet", json=TEAM_TOTAL_BET)

    [listed] = logged_in_client.get("/api/my_bets").get_json()

    assert listed["description"] == "Alice A O/U 110.50: Over"
    assert listed["bet_type"] == "team_total"
    assert (listed["market"], listed["selection"], listed["line"]) == ("2026-w10-team_total-1", "over", 110.5)
    assert (listed["price"], listed["probability"], listed["run_id"]) == (-120, 0.55, RUN_ID)
    assert listed["removable"] is True


def test_removing_a_bet_keeps_it_as_removed_and_refunds_once(logged_in_client, user, betting_period, seeded_analytics):
    bet_id = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()["bet_id"]

    first = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()
    second = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert first == {"success": True, "new_balance": 1000.0}
    assert second == {"success": False, "error": "Bet not found"}
    bet = db.session.get(Bet, bet_id)
    assert bet.status == "removed"
    assert [leg.status for leg in bet.legs] == ["void"]
    stat = db.session.query(WeeklyStats).filter_by(user_id=user.id).first()
    assert stat.bets_placed == 0
    assert stat.active_bets_amount == 0.0
    assert logged_in_client.get("/api/my_bets").get_json() == []


def test_a_bet_whose_run_was_replaced_cannot_be_removed(logged_in_client, user, betting_period, seeded_analytics):
    bet_id = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()["bet_id"]
    db.session.execute(text("UPDATE betting_odds_highest_scorer SET run_id = 'new-run'"))
    db.session.commit()

    [listed] = logged_in_client.get("/api/my_bets").get_json()
    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert listed["removable"] is False
    assert reply == {"success": False, "error": "Odds have changed since this bet was placed"}
    assert db.session.get(Bet, bet_id).status == "pending"
    db.session.refresh(user)
    assert user.account_balance == 900.0


def test_a_legacy_bet_without_legs_is_still_removable(logged_in_client, user, betting_period):
    legacy = Bet(
        user_id=user.id,
        bet_type="highest_scorer",
        description="Alice A: Highest Scorer +100",
        week=10,
        amount=100.0,
        odds="+100",
        potential_win=100.0,
    )
    ledger.open_week(user.id, 10)
    ledger.place(legacy)
    db.session.commit()

    [listed] = logged_in_client.get("/api/my_bets").get_json()
    reply = logged_in_client.delete(f"/api/remove_bet/{legacy.id}").get_json()

    assert (listed["market"], listed["selection"], listed["line"], listed["run_id"]) == (None, None, None, None)
    assert listed["removable"] is True
    assert reply == {"success": True, "new_balance": 1000.0}
    assert db.session.get(Bet, legacy.id).status == "removed"


def test_remove_bet_locked_period(logged_in_client, user, db_session, seeded_analytics):
    period = BettingPeriod(
        week=10,
        lock_time=datetime.now(UTC) + timedelta(days=7),
        is_locked=False,
        is_settled=False,
    )
    db_session.session.add(period)
    db_session.session.commit()
    bet_id = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()["bet_id"]

    period.is_locked = True
    period.lock_time = datetime.now(UTC) - timedelta(hours=1)
    db_session.session.commit()

    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert reply["success"] is False
    assert db.session.get(Bet, bet_id).status == "pending"


def test_the_account_page_lists_new_legacy_removed_won_and_lost_bets(
    logged_in_client, user, betting_period, seeded_analytics
):
    removed_id = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()["bet_id"]
    logged_in_client.delete(f"/api/remove_bet/{removed_id}")
    logged_in_client.post("/api/place_bet", json=TEAM_TOTAL_BET)
    db.session.add_all(
        [
            Bet(
                user_id=user.id,
                bet_type="first_place",
                description="Ammady: #1 Seed +250",
                week=9,
                amount=40.0,
                odds="+250",
                potential_win=100.0,
                status="won",
                result=100.0,
            ),
            Bet(
                user_id=user.id,
                bet_type="team_total",
                description="Ammady O/U 101.5: Under",
                week=9,
                amount=30.0,
                odds="EVEN",
                potential_win=30.0,
                status="lost",
                result=-30.0,
            ),
        ]
    )
    db.session.commit()

    page = logged_in_client.get("/account").get_data(as_text=True)

    assert "Alice A: Highest Scorer" in page
    assert "Removed" in page
    assert "Alice A O/U 110.50: Over" in page
    assert "Ammad: #1 Seed" in page
    assert "+$100.00" in page
    assert "-$30.00" in page
