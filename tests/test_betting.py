import json
import re
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import text

from app import ledger
from app.database import db
from app.models import Bet, BettingPeriod, ParlayRefusal, WeeklyStats
from pipeline import markets as win_rules
from tests.conftest import RUN_ID, WINDOW_NOW

HIGHEST_SCORER_BET = {"market": "2026-w10-highest_scorer", "selection": "1", "run_id": RUN_ID, "amount": 100}
PLAYOFFS_BET = {"market": "2026-make_playoffs-1", "selection": "yes", "run_id": RUN_ID, "amount": 100}
TEAM_TOTAL_BET = {
    "market": "2026-w10-team_total-1",
    "selection": "over",
    "line": 110.5,
    "run_id": RUN_ID,
    "amount": 100,
}

# bet_type, key, selection, line, description, odds and the potential win on a 100 stake, from the seeded tables.
SINGLE_MARKETS = [
    ("moneyline", "2026-w10-moneyline-1v2", "1", None, "Alice A vs Bob B: Alice A -150", "-150", 66.67),
    ("team_total", "2026-w10-team_total-1", "over", 110.5, "Alice A O/U 110.50: Over", "-120", 83.33),
    ("highest_scorer", "2026-w10-highest_scorer", "1", None, "Alice A: Highest Scorer +185", "+185", 185.0),
    ("lowest_scorer", "2026-w10-lowest_scorer", "2", None, "Bob B: Lowest Scorer +230", "+230", 230.0),
    ("make_playoffs", "2026-make_playoffs-1", "yes", None, "Alice A: Make Playoffs Yes -400", "-400", 25.0),
    ("make_playoffs", "2026-make_playoffs-1", "no", None, "Alice A: Make Playoffs No +400", "+400", 400.0),
    ("last_place", "2026-last_place", "2", None, "Bob B: Last Place +230", "+230", 230.0),
    ("champion", "2026-champion", "1", None, "Alice A: Champion +233", "+233", 233.0),
]


@pytest.mark.parametrize(
    ("bet_type", "market", "selection", "line", "description", "odds", "potential_win"), SINGLE_MARKETS
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
        "legs": [market],
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
        "/api/place_bet", json={"market": "2026-last_place", "selection": "2", "run_id": RUN_ID, "amount": 100}
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
        ({"market": "2026-w10-matchup_total-1v2"}, "Unknown market"),
        ({"market": "2026-w10-spread-1v2", "selection": "1"}, "Unknown line"),
        ({"market": "2026-w10-spread-1v2", "selection": "1", "line": 3.25}, "Unknown line"),
        ({"market": "2026-w10-spread-1v2", "selection": "3", "line": -4.0}, "Unknown selection"),
        ({"market": "2026-w10-team_total-7", "selection": "over", "line": 100.0}, "Unknown market"),
        ({"market": "2026-first_place", "selection": "2"}, "Unknown market"),
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


def close_the_window_at(closes_at):
    db.session.execute(text("UPDATE simulation_runs SET window_closes_at = :closes"), {"closes": closes_at.isoformat()})
    db.session.commit()


def pause_the_window():
    """The seeded run's kickoff has passed and no rerun has published."""
    close_the_window_at(WINDOW_NOW - timedelta(hours=1))


def publish_a_rerun(run_id, totals=None):
    """A rerun published before the clock, with an open window, that repriced every market; with its scores when given."""
    db.session.execute(
        text("""
        INSERT INTO simulation_runs (run_id, season, week, seed, n_sims, model_version, n_teams, draws_path,
                                     created_at, n_locked, window_closes_at, standings_through_week)
        VALUES (:run_id, 2026, 10, 2, 50000, 'v2', 12, 'draws.npy', :created_at, 3, :closes_at, 9)
    """),
        {
            "run_id": run_id,
            "created_at": (WINDOW_NOW - timedelta(minutes=30)).isoformat(),
            "closes_at": (WINDOW_NOW + timedelta(days=2)).isoformat(),
        },
    )
    for table in (
        "matchup_ml",
        "team_ou",
        "highest_scorer",
        "lowest_scorer",
        "make_playoffs",
        "last_place",
        "champion",
    ):
        db.session.execute(text(f"UPDATE betting_odds_{table} SET run_id = :run_id"), {"run_id": run_id})
    if totals is not None:
        store_the_scores(run_id, totals)
    db.session.commit()


def publish_a_run_without_odds(run_id, totals=None):
    """A newer run of the week whose odds never reached the odds tables, which still carry the seeded run."""
    db.session.execute(
        text("""
        INSERT INTO simulation_runs (run_id, season, week, seed, n_sims, model_version, n_teams, draws_path,
                                     created_at, n_locked, window_closes_at, standings_through_week)
        SELECT :rerun, season, week, 2, n_sims, model_version, n_teams, draws_path,
               :created_at, 1, window_closes_at, standings_through_week
        FROM simulation_runs WHERE run_id = :run_id
    """),
        {"rerun": run_id, "created_at": (WINDOW_NOW - timedelta(minutes=30)).isoformat(), "run_id": RUN_ID},
    )
    if totals is not None:
        store_the_scores(run_id, totals)
    db.session.commit()


def store_the_scores(run_id, totals):
    db.session.execute(
        text("""
        INSERT INTO simulation_totals (run_id, season, week, created_at, n_sims, roster_ids, totals)
        VALUES (:run_id, 2026, 10, :created_at, :n_sims, '1,2', :totals)
    """),
        {
            "run_id": run_id,
            "created_at": (WINDOW_NOW - timedelta(minutes=30)).isoformat(),
            "n_sims": len(totals),
            "totals": win_rules.encode_totals(totals),
        },
    )


def test_the_admin_lock_is_the_kill_switch(logged_in_client, user, betting_period, seeded_analytics):
    betting_period.lock_time = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    betting_period.is_locked = True
    db.session.commit()

    reply = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()

    assert reply == {"success": False, "error": "Bets are locked as of 2026-11-15 06:00 PM UTC"}
    assert db.session.query(Bet).count() == 0


def test_a_passed_lock_time_refuses_a_bet_and_locks_the_week(logged_in_client, user, betting_period, seeded_analytics):
    betting_period.lock_time = datetime.now(UTC) - timedelta(hours=1)
    db.session.commit()

    reply = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()

    assert reply["error"].startswith("Bets are locked as of ")
    assert betting_period.is_locked is True
    assert db.session.query(Bet).count() == 0


def test_a_bet_is_refused_while_the_window_is_paused(logged_in_client, user, betting_period, seeded_analytics):
    pause_the_window()

    reply = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()

    assert reply == {"success": False, "error": "Betting is paused until the odds update"}
    assert betting_period.is_locked is False
    assert db.session.query(Bet).count() == 0


def test_a_bet_is_refused_when_the_week_is_closed(logged_in_client, user, seeded_analytics):
    reply = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()

    assert reply == {"success": False, "error": "Betting is closed for week 10"}
    assert db.session.query(Bet).count() == 0


def test_a_bet_is_refused_when_the_run_has_no_window(logged_in_client, user, betting_period, seeded_analytics):
    db.session.execute(text("UPDATE simulation_runs SET window_closes_at = NULL"))
    db.session.commit()

    reply = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()

    assert reply == {"success": False, "error": "Betting is closed for week 10"}


def test_my_bets_lists_the_pick_and_whether_it_can_be_removed(logged_in_client, user, betting_period, seeded_analytics):
    logged_in_client.post("/api/place_bet", json=TEAM_TOTAL_BET)

    [listed] = logged_in_client.get("/api/my_bets").get_json()

    assert listed["description"] == "Alice A O/U 110.50: Over"
    assert listed["bet_type"] == "team_total"
    assert (listed["market"], listed["selection"], listed["line"]) == ("2026-w10-team_total-1", "over", 110.5)
    assert (listed["price"], listed["probability"], listed["run_id"]) == (-120, 0.55, RUN_ID)
    assert listed["legs"] == [
        {"market": "2026-w10-team_total-1", "selection": "over", "line": 110.5, "price": -120, "odds": "-120"}
    ]
    assert listed["removable"] is True


def test_a_bet_is_removable_only_while_its_week_is_open(logged_in_client, user, betting_period, seeded_analytics):
    logged_in_client.post("/api/place_bet", json=TEAM_TOTAL_BET)

    pause_the_window()
    [paused] = logged_in_client.get("/api/my_bets").get_json()
    close_the_window_at(WINDOW_NOW + timedelta(days=2))
    [reopened] = logged_in_client.get("/api/my_bets").get_json()
    publish_a_rerun("2026w10-20261110T143000")
    [repriced] = logged_in_client.get("/api/my_bets").get_json()

    assert paused["removable"] is False
    assert reopened["removable"] is True
    assert repriced["removable"] is False


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


def test_a_bet_stays_once_its_week_has_a_newer_run_even_while_its_odds_row_keeps_its_run(
    logged_in_client, user, betting_period, seeded_analytics
):
    bet_id = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()["bet_id"]
    publish_a_run_without_odds("2026w10-20261110T143000")

    [listed] = logged_in_client.get("/api/my_bets").get_json()
    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    # The newer run has no scores, so it offers no cash-out either.
    assert (listed["removable"], listed["cash_out_offer"]) == (False, None)
    assert reply == {"success": False, "error": "Odds have changed since this bet was placed"}
    assert db.session.get(Bet, bet_id).status == "pending"
    db.session.refresh(user)
    assert user.account_balance == 900.0


def test_a_futures_bet_is_removable_while_its_odds_row_keeps_its_run(
    logged_in_client, user, betting_period, seeded_analytics
):
    # A futures bet has no window run: a newer week run leaves its odds row, and so the bet, where they were.
    bet_id = logged_in_client.post("/api/place_bet", json=PLAYOFFS_BET).get_json()["bet_id"]
    publish_a_run_without_odds("2026w10-20261110T143000")

    [listed] = logged_in_client.get("/api/my_bets").get_json()
    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert (listed["removable"], listed["cash_out_offer"]) == (True, None)
    assert reply == {"success": True, "new_balance": 1000.0}


def test_a_futures_bet_stays_once_its_odds_row_has_a_newer_run(
    logged_in_client, user, betting_period, seeded_analytics
):
    bet_id = logged_in_client.post("/api/place_bet", json=PLAYOFFS_BET).get_json()["bet_id"]
    db.session.execute(text("UPDATE betting_odds_make_playoffs SET run_id = 'playoffs-only'"))
    db.session.commit()

    [listed] = logged_in_client.get("/api/my_bets").get_json()
    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert listed["removable"] is False
    assert reply == {"success": False, "error": "Odds have changed since this bet was placed"}
    assert db.session.get(Bet, bet_id).status == "pending"


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
    assert listed["legs"] == []
    assert listed["removable"] is True
    assert reply == {"success": True, "new_balance": 1000.0}
    assert db.session.get(Bet, legacy.id).status == "removed"


def test_the_admin_lock_keeps_a_bet_in_place(logged_in_client, user, betting_period, seeded_analytics):
    bet_id = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()["bet_id"]
    betting_period.lock_time = datetime(2026, 11, 15, 18, 0, tzinfo=UTC)
    betting_period.is_locked = True
    db.session.commit()

    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert reply == {"success": False, "error": "Bets are locked as of 2026-11-15 06:00 PM UTC"}
    assert db.session.get(Bet, bet_id).status == "pending"


def test_a_paused_window_keeps_a_bet_in_place(logged_in_client, user, betting_period, seeded_analytics):
    bet_id = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()["bet_id"]
    pause_the_window()

    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert reply == {"success": False, "error": "Betting is paused until the odds update"}
    assert db.session.get(Bet, bet_id).status == "pending"
    db.session.refresh(user)
    assert user.account_balance == 900.0


def test_a_settled_week_keeps_a_bet_in_place(logged_in_client, user, betting_period, seeded_analytics):
    bet_id = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()["bet_id"]
    betting_period.is_settled = True
    db.session.commit()

    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert reply == {"success": False, "error": "Betting is closed for week 10"}
    assert db.session.get(Bet, bet_id).status == "pending"


def test_the_account_page_lists_new_legacy_won_and_lost_bets_but_not_removed_ones(
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

    assert "Alice A: Highest Scorer" not in page
    assert "Alice A O/U 110.50: Over" in page
    assert "Ammad: #1 Seed" in page
    assert "+$100.00" in page
    assert "-$30.00" in page


def test_the_account_pages_week_pnl_counts_settled_bets_not_open_stakes(
    logged_in_client, user, betting_period, seeded_analytics
):
    won_id = logged_in_client.post("/api/place_bet", json=HIGHEST_SCORER_BET).get_json()["bet_id"]
    logged_in_client.post("/api/place_bet", json=TEAM_TOTAL_BET)
    ledger.settle(db.session.get(Bet, won_id), won=True)
    db.session.commit()

    page = logged_in_client.get("/account").get_data(as_text=True)

    # The balance is up $85 on the week, but only the won bet's $185 is settled; the other stake is still open.
    assert re.search(r"tnc-acct-bh-pnl[^>]*tnc-pos[^>]*>\+\$185\.00<", page)
    assert "+$85.00" not in page


# The worked example of design §2.1: $100 on roster 2 at +105 pays $205. After Thursday's game the
# rerun has roster 2 winning 3,819 of 10,000 sims, a fair value of $78.29 and an offer of $74.38.
DESIGN_EXAMPLE_BET = {"market": "2026-w10-moneyline-1v2", "selection": "2", "run_id": RUN_ID, "amount": 100}
RERUN_ID = "2026w10-20261110T143000"


def rerun_scores():
    roster_2_wins = np.arange(10_000) < 3_819
    return np.column_stack([np.full(10_000, 100.0), np.where(roster_2_wins, 110.0, 90.0)])


def place_the_design_example_bet(client):
    db.session.execute(text("UPDATE betting_odds_matchup_ml SET team2_ml = '+105'"))
    db.session.commit()
    return client.post("/api/place_bet", json=DESIGN_EXAMPLE_BET).get_json()["bet_id"]


def cash_out(client, bet_id, offer=74.38):
    return client.post(f"/api/cash_out/{bet_id}", json={"offer": offer}).get_json()


def weekly_money(user, week):
    stats = db.session.query(WeeklyStats).filter_by(user_id=user.id, week=week).one()
    return (
        stats.starting_balance,
        stats.ending_balance,
        stats.pnl,
        stats.active_bets_amount,
        stats.settled_pnl,
        stats.bets_placed,
        stats.bets_won,
    )


def test_cashing_out_pays_the_offer_and_posts_only_the_profit_to_the_current_week(
    logged_in_client, user, betting_period, seeded_analytics
):
    bet_id = place_the_design_example_bet(logged_in_client)
    publish_a_rerun(RERUN_ID, rerun_scores())
    db.session.add(BettingPeriod(week=11, lock_time=datetime.now(UTC) + timedelta(days=14)))
    db.session.commit()

    reply = cash_out(logged_in_client, bet_id)

    assert reply == {"success": True, "new_balance": pytest.approx(974.38), "cash_out_amount": 74.38}
    db.session.refresh(user)
    assert (user.account_balance, user.total_pnl) == pytest.approx((974.38, -25.62))
    bet = db.session.get(Bet, bet_id)
    assert (bet.status, bet.result, bet.cash_out_amount, bet.cash_out_run_id) == (
        "cashed_out",
        -25.62,
        74.38,
        RERUN_ID,
    )
    assert bet.cashed_out_at is not None
    assert bet.settled_at == bet.cashed_out_at
    assert [(leg.status, leg.settled_at) for leg in bet.legs] == [("cashed_out", bet.cashed_out_at)]
    # The bet's week closes the stake; the week it was taken in books the $25.62 loss.
    assert weekly_money(user, 10) == pytest.approx((1000.0, 974.38, -25.62, 0.0, 0.0, 1, 0))
    assert weekly_money(user, 11) == pytest.approx((900.0, 974.38, 74.38, 0.0, -25.62, 0, 0))
    assert logged_in_client.get("/api/my_bets").get_json() == []


def test_a_second_cash_out_of_the_same_bet_is_refused(logged_in_client, user, betting_period, seeded_analytics):
    bet_id = place_the_design_example_bet(logged_in_client)
    publish_a_rerun(RERUN_ID, rerun_scores())

    cash_out(logged_in_client, bet_id)
    second = cash_out(logged_in_client, bet_id)

    assert second == {"success": False, "error": "Bet not found"}
    db.session.refresh(user)
    assert user.account_balance == pytest.approx(974.38)


def test_a_cash_out_of_another_users_bet_a_settled_bet_or_no_bet_is_refused(
    logged_in_client, user, admin_user, betting_period, seeded_analytics
):
    theirs = place_the_design_example_bet(logged_in_client)
    settled = place_the_design_example_bet(logged_in_client)
    publish_a_rerun(RERUN_ID, rerun_scores())
    db.session.execute(text("UPDATE bets SET user_id = :owner WHERE id = :id"), {"owner": admin_user.id, "id": theirs})
    ledger.settle(db.session.get(Bet, settled), won=False)
    db.session.commit()

    replies = [cash_out(logged_in_client, bet_id) for bet_id in (theirs, settled, 999)]

    assert replies == [{"success": False, "error": "Bet not found"}] * 3
    assert db.session.get(Bet, theirs).status == "pending"
    assert db.session.get(Bet, settled).status == "lost"


def test_no_cash_out_while_the_bets_run_is_still_the_latest(logged_in_client, user, betting_period, seeded_analytics):
    bet_id = place_the_design_example_bet(logged_in_client)

    reply = cash_out(logged_in_client, bet_id)

    assert reply == {
        "success": False,
        "error": "Odds have not changed since this bet was placed; remove it instead",
    }
    assert db.session.get(Bet, bet_id).status == "pending"


def test_no_cash_out_while_the_window_is_paused(logged_in_client, user, betting_period, seeded_analytics):
    bet_id = place_the_design_example_bet(logged_in_client)
    publish_a_rerun(RERUN_ID, rerun_scores())
    pause_the_window()

    reply = cash_out(logged_in_client, bet_id)

    assert reply == {"success": False, "error": "Betting is paused until the odds update"}
    assert db.session.get(Bet, bet_id).status == "pending"


def test_a_cash_out_at_an_offer_the_latest_run_has_moved_is_refused_with_the_new_offer(
    logged_in_client, user, betting_period, seeded_analytics
):
    bet_id = place_the_design_example_bet(logged_in_client)
    publish_a_rerun(RERUN_ID, rerun_scores())

    reply = cash_out(logged_in_client, bet_id, offer=92.24)

    assert reply == {"success": False, "error": "Offer has changed", "offer": 74.38}
    assert db.session.get(Bet, bet_id).status == "pending"
    db.session.refresh(user)
    assert user.account_balance == 900.0


def test_my_bets_offers_a_cash_out_in_place_of_removal_once_a_newer_run_moves_the_odds(
    logged_in_client, user, betting_period, seeded_analytics
):
    place_the_design_example_bet(logged_in_client)

    [before] = logged_in_client.get("/api/my_bets").get_json()
    publish_a_rerun(RERUN_ID, rerun_scores())
    [after] = logged_in_client.get("/api/my_bets").get_json()

    assert (before["cash_out_offer"], before["removable"]) == (None, True)
    assert (after["cash_out_offer"], after["removable"]) == (74.38, False)


def test_a_bet_with_an_offer_is_not_removable_even_while_its_odds_row_keeps_its_run(
    logged_in_client, user, betting_period, seeded_analytics
):
    bet_id = place_the_design_example_bet(logged_in_client)
    publish_a_run_without_odds(RERUN_ID, rerun_scores())

    [listed] = logged_in_client.get("/api/my_bets").get_json()
    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert (listed["cash_out_offer"], listed["removable"]) == (74.38, False)
    assert reply == {"success": False, "error": "Odds have changed since this bet was placed"}


def test_a_last_place_bet_is_offered_and_cashed_out_at_the_newer_futures_quote(
    logged_in_client, user, betting_period, seeded_analytics
):
    last_place = {"market": "2026-last_place", "selection": "2", "run_id": RUN_ID, "amount": 100}
    bet_id = logged_in_client.post("/api/place_bet", json=last_place).get_json()["bet_id"]
    # A newer futures run puts Bob B's last place at 50%: $330 at 50% is worth $165, offered at $156.75.
    db.session.execute(text("UPDATE betting_odds_last_place SET run_id = 'newer-futures', probability = 0.5"))
    db.session.commit()

    [listed] = logged_in_client.get("/api/my_bets").get_json()
    reply = cash_out(logged_in_client, bet_id, offer=156.75)

    assert (listed["bet_type"], listed["market"], listed["selection"]) == ("last_place", "2026-last_place", "2")
    assert (listed["description"], listed["removable"], listed["cash_out_offer"]) == (
        "Bob B: Last Place +230",
        False,
        156.75,
    )
    assert reply == {"success": True, "new_balance": pytest.approx(1056.75), "cash_out_amount": 156.75}
    assert weekly_money(user, 10) == pytest.approx((1000.0, 1056.75, 56.75, 0.0, 56.75, 1, 0))


def test_the_account_page_shows_a_cash_out_with_its_signed_result(
    logged_in_client, user, betting_period, seeded_analytics
):
    bet_id = place_the_design_example_bet(logged_in_client)
    publish_a_rerun(RERUN_ID, rerun_scores())
    cash_out(logged_in_client, bet_id)
    db.session.add(
        Bet(
            user_id=user.id,
            bet_type="make_playoffs",
            description="Alice A: Make Playoffs +152",
            week=9,
            amount=100.0,
            odds="+152",
            potential_win=152.0,
            status="cashed_out",
            result=19.7,
            cash_out_amount=119.7,
        )
    )
    db.session.commit()

    page = logged_in_client.get("/account").get_data(as_text=True)

    assert re.search(r"tnc-neg\">-\$25\.62</div>\s*<div class=\"tnc-acct-bet-meta\">Cashed out", page)
    assert re.search(r"tnc-pos\">\+\$19\.70</div>\s*<div class=\"tnc-acct-bet-meta\">Cashed out", page)


# Parlays, on the seeded run: roster 1 wins 11 of the 20 sims, is over 110.5 in 9, and does both in 7.
ROSTER_1_WINS = {"market": "2026-w10-moneyline-1v2", "selection": "1"}
ROSTER_2_WINS = {"market": "2026-w10-moneyline-1v2", "selection": "2"}
ROSTER_1_OVER = {"market": "2026-w10-team_total-1", "selection": "over", "line": 110.5}
ROSTER_2_OVER = {"market": "2026-w10-team_total-2", "selection": "over", "line": 95.0}
ROSTER_1_HIGHEST = {"market": "2026-w10-highest_scorer", "selection": "1"}
ROSTER_2_HIGHEST = {"market": "2026-w10-highest_scorer", "selection": "2"}
ROSTER_2_LOWEST = {"market": "2026-w10-lowest_scorer", "selection": "2"}
MAKE_PLAYOFFS = {"market": "2026-make_playoffs-1", "selection": "yes"}
BOB_MAKES_PLAYOFFS = {"market": "2026-make_playoffs-2", "selection": "yes"}
BOB_MISSES_PLAYOFFS = {"market": "2026-make_playoffs-2", "selection": "no"}
ALICE_CHAMPION = {"market": "2026-champion", "selection": "1"}

CANNOT_PRICE = "Not offered: the simulations cannot price this parlay"

# The legs, the refusal text, its rule and the keys it names.
PARLAY_REFUSALS = [
    ([ROSTER_1_WINS] * 5, "A parlay has 2 to 4 legs", "size", []),
    (
        [ROSTER_1_OVER, MAKE_PLAYOFFS],
        "Weekly and futures picks cannot be parlayed together",
        "mixed",
        ["2026-make_playoffs-1"],
    ),
    ([ROSTER_1_WINS, ROSTER_2_WINS], "Two legs from one market", "same_market", ["2026-w10-moneyline-1v2"]),
    ([ROSTER_1_WINS, ROSTER_2_HIGHEST], CANNOT_PRICE, "impossible", []),
    (
        [ROSTER_1_HIGHEST, ROSTER_2_LOWEST],
        "A leg adds nothing to this parlay",
        "redundant",
        ["2026-w10-highest_scorer", "2026-w10-lowest_scorer"],
    ),
    (
        [MAKE_PLAYOFFS, {**MAKE_PLAYOFFS, "selection": "no"}],
        "Two legs from one market",
        "same_market",
        ["2026-make_playoffs-1"],
    ),
    ([{"market": "2026-last_place", "selection": "1"}, ALICE_CHAMPION], CANNOT_PRICE, "impossible", []),
    ([ALICE_CHAMPION, MAKE_PLAYOFFS], "A leg adds nothing to this parlay", "redundant", ["2026-make_playoffs-1"]),
]


def quote_parlay(client, legs, run_id=RUN_ID):
    return client.post("/api/parlay_quote", json={"legs": legs, "run_id": run_id}).get_json()


def place_parlay(client, legs, run_id=RUN_ID, amount=100):
    return client.post("/api/place_bet", json={"legs": legs, "run_id": run_id, "amount": amount}).get_json()


def move_a_quote(table, where):
    db.session.execute(text(f"UPDATE {table} SET run_id = 'new-run' WHERE {where}"))
    db.session.commit()


def test_a_parlay_is_quoted_at_the_sims_where_every_leg_wins(logged_in_client, user, betting_period, seeded_analytics):
    reply = quote_parlay(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER])

    assert reply == {
        "success": True,
        "run_id": RUN_ID,
        "probability": 0.35,
        "odds": "+186",
        "price": 186,
        "legs": [
            {
                "market": "2026-w10-moneyline-1v2",
                "selection": "1",
                "line": None,
                "odds": "-150",
                "price": -150,
                "probability": 0.6,
            },
            {
                "market": "2026-w10-team_total-1",
                "selection": "over",
                "line": 110.5,
                "odds": "-120",
                "price": -120,
                "probability": 0.55,
            },
        ],
    }
    assert db.session.query(ParlayRefusal).count() == 0


def test_a_placed_parlay_records_the_joint_price_and_each_legs_own(
    logged_in_client, user, betting_period, seeded_analytics
):
    reply = place_parlay(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER])

    assert reply == {
        "success": True,
        "new_balance": 900.0,
        "bet_id": 1,
        "market": None,
        "selection": None,
        "price": 186,
        "legs": ["2026-w10-moneyline-1v2", "2026-w10-team_total-1"],
    }
    bet = db.session.get(Bet, 1)
    assert (bet.bet_type, bet.week, bet.amount, bet.status) == ("parlay", 10, 100.0, "pending")
    assert bet.description == "Alice A vs Bob B: Alice A -150 + Alice A O/U 110.50: Over"
    assert (bet.odds, bet.price, bet.probability, bet.run_id, bet.potential_win) == ("+186", 186, 0.35, RUN_ID, 186.0)
    assert [(leg.market, leg.selection, leg.line, leg.price, leg.probability) for leg in bet.legs] == [
        ("2026-w10-moneyline-1v2", "1", None, -150, 0.6),
        ("2026-w10-team_total-1", "over", 110.5, -120, 0.55),
    ]
    assert {(leg.season, leg.week, leg.status) for leg in bet.legs} == {(2026, 10, "pending")}
    assert weekly_money(user, 10) == (1000.0, 900.0, -100.0, 100.0, 0.0, 1, 0)


def test_a_three_leg_parlay_is_placed_at_the_sims_where_all_three_win(
    logged_in_client, user, betting_period, seeded_analytics
):
    reply = place_parlay(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER, ROSTER_2_OVER], amount=50)

    assert (reply["success"], reply["new_balance"], reply["price"]) == (True, 950.0, 300)
    bet = db.session.get(Bet, reply["bet_id"])
    assert bet.description == ("Alice A vs Bob B: Alice A -150 + Alice A O/U 110.50: Over + Bob B O/U 95.00: Over")
    # Of the 7 sims where roster 1 wins and tops 110.5, roster 2 tops 95 in 5.
    assert (bet.odds, bet.probability, bet.potential_win) == ("+300", 0.25, 150.0)
    assert [leg.price for leg in bet.legs] == [-150, -120, 105]
    assert weekly_money(user, 10) == (1000.0, 950.0, -50.0, 50.0, 0.0, 1, 0)


@pytest.mark.parametrize("send", [quote_parlay, place_parlay])
@pytest.mark.parametrize(("legs", "error", "rule", "faulty"), PARLAY_REFUSALS)
def test_each_parlay_refusal_names_its_rule_and_legs(
    logged_in_client, user, betting_period, seeded_analytics, send, legs, error, rule, faulty
):
    reply = send(logged_in_client, legs)

    assert reply == {"success": False, "error": error, "rule": rule, "legs": faulty}
    assert db.session.query(Bet).count() == 0
    db.session.refresh(user)
    assert user.account_balance == 1000.0


@pytest.mark.parametrize("send", [quote_parlay, place_parlay])
def test_a_parlay_leg_without_a_price_is_not_offered(logged_in_client, user, betting_period, seeded_analytics, send):
    db.session.execute(text("UPDATE betting_odds_highest_scorer SET odds = NULL WHERE team_id = 1"))
    db.session.commit()

    reply = send(logged_in_client, [ROSTER_1_OVER, ROSTER_1_HIGHEST])

    assert reply == {"success": False, "error": "Not offered", "rule": "no_price", "legs": ["2026-w10-highest_scorer"]}


@pytest.mark.parametrize("send", [quote_parlay, place_parlay])
def test_a_parlay_on_a_run_without_its_scores_is_not_offered(
    logged_in_client, user, betting_period, seeded_analytics, send
):
    db.session.execute(text("DELETE FROM simulation_totals"))
    db.session.commit()

    reply = send(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER])

    assert reply == {"success": False, "error": "Not offered", "rule": "no_price", "legs": []}


@pytest.mark.parametrize("send", [quote_parlay, place_parlay])
def test_a_leg_quoted_at_another_run_has_changed_and_the_reply_carries_the_windows_run(
    logged_in_client, user, betting_period, seeded_analytics, send
):
    move_a_quote("betting_odds_team_ou", "team_id = 1")

    reply = send(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER])

    assert reply == {
        "success": False,
        "error": "Odds have changed",
        "rule": "odds_changed",
        "legs": ["2026-w10-team_total-1"],
        "run_id": RUN_ID,
    }
    assert db.session.query(Bet).count() == 0


@pytest.mark.parametrize("send", [quote_parlay, place_parlay])
def test_a_page_showing_another_runs_prices_has_seen_every_leg_change(
    logged_in_client, user, betting_period, seeded_analytics, send
):
    reply = send(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER], run_id="2026w10-20261109T140000")

    assert reply == {
        "success": False,
        "error": "Odds have changed",
        "rule": "odds_changed",
        "legs": ["2026-w10-moneyline-1v2", "2026-w10-team_total-1"],
        "run_id": RUN_ID,
    }
    assert db.session.query(Bet).count() == 0


@pytest.mark.parametrize(("legs", "error", "rule", "faulty"), PARLAY_REFUSALS)
def test_the_quote_logs_only_the_refusals_the_owner_reviews(
    logged_in_client, user, betting_period, seeded_analytics, legs, error, rule, faulty
):
    quote_parlay(logged_in_client, legs)
    place_parlay(logged_in_client, legs)

    logged = [
        (row.user_id, row.week, row.run_id, json.loads(row.legs), row.rule) for row in db.session.query(ParlayRefusal)
    ]
    if rule in ("same_market", "impossible", "redundant"):
        assert logged == [(user.id, 10, RUN_ID, legs, rule)]
        assert db.session.query(ParlayRefusal).one().created_at is not None
    else:
        assert logged == []


def test_moved_odds_and_missing_prices_are_not_logged(logged_in_client, user, betting_period, seeded_analytics):
    quote_parlay(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER], run_id="2026w10-20261109T140000")
    db.session.execute(text("UPDATE betting_odds_highest_scorer SET odds = NULL WHERE team_id = 1"))
    db.session.commit()
    quote_parlay(logged_in_client, [ROSTER_1_OVER, ROSTER_1_HIGHEST])

    assert db.session.query(ParlayRefusal).count() == 0


@pytest.mark.parametrize(
    ("legs", "named"),
    [
        (["x", "y"], [None, None]),
        ([{}, ROSTER_1_OVER], [None]),
        ([ROSTER_1_WINS, "x", {"market": "2026-first_place", "selection": "1"}], [None, "2026-first_place"]),
    ],
)
@pytest.mark.parametrize("send", [quote_parlay, place_parlay])
def test_a_leg_that_names_no_market_is_refused_as_an_unknown_one(
    logged_in_client, user, betting_period, seeded_analytics, send, legs, named
):
    reply = send(logged_in_client, legs)

    assert reply == {"success": False, "error": "Unknown market", "rule": "leg", "legs": named}
    assert db.session.query(Bet).count() == 0


@pytest.mark.parametrize("send", [quote_parlay, place_parlay])
def test_a_page_showing_another_runs_prices_names_a_leg_that_is_not_an_object_as_none(
    logged_in_client, user, betting_period, seeded_analytics, send
):
    reply = send(logged_in_client, ["x", ROSTER_1_WINS], run_id="2026w10-20261109T140000")

    assert reply == {
        "success": False,
        "error": "Odds have changed",
        "rule": "odds_changed",
        "legs": [None, "2026-w10-moneyline-1v2"],
        "run_id": RUN_ID,
    }


def test_a_quote_without_a_list_of_legs_is_refused(logged_in_client, user, betting_period, seeded_analytics):
    reply = logged_in_client.post("/api/parlay_quote", json={"legs": ROSTER_1_WINS, "run_id": RUN_ID}).get_json()

    assert reply == {"success": False, "error": "A parlay has 2 to 4 legs", "rule": "size", "legs": []}


def test_a_parlay_is_not_quoted_while_the_window_is_paused(logged_in_client, user, betting_period, seeded_analytics):
    pause_the_window()

    reply = quote_parlay(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER])

    assert reply == {"success": False, "error": "Betting is paused until the odds update"}


def test_an_anonymous_quote_needs_a_login(client, betting_period, seeded_analytics):
    response = client.post("/api/parlay_quote", json={"legs": [ROSTER_1_WINS, ROSTER_1_OVER], "run_id": RUN_ID})

    assert response.status_code == 401


def test_my_bets_lists_a_parlays_legs_and_marks_no_card(logged_in_client, user, betting_period, seeded_analytics):
    place_parlay(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER])

    [listed] = logged_in_client.get("/api/my_bets").get_json()

    assert (listed["bet_type"], listed["description"]) == (
        "parlay",
        "Alice A vs Bob B: Alice A -150 + Alice A O/U 110.50: Over",
    )
    assert (listed["market"], listed["selection"], listed["line"]) == (None, None, None)
    assert (listed["odds"], listed["price"], listed["probability"], listed["potential_win"]) == (
        "+186",
        186,
        0.35,
        186.0,
    )
    assert listed["legs"] == [
        {"market": "2026-w10-moneyline-1v2", "selection": "1", "line": None, "price": -150, "odds": "-150"},
        {"market": "2026-w10-team_total-1", "selection": "over", "line": 110.5, "price": -120, "odds": "-120"},
    ]
    assert (listed["removable"], listed["cash_out_offer"]) == (True, None)


def test_a_parlay_is_removed_while_its_run_is_the_latest(logged_in_client, user, betting_period, seeded_analytics):
    bet_id = place_parlay(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER])["bet_id"]

    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert reply == {"success": True, "new_balance": 1000.0}
    bet = db.session.get(Bet, bet_id)
    assert bet.status == "removed"
    assert [leg.status for leg in bet.legs] == ["void", "void"]


def test_a_parlay_stays_once_its_week_has_a_newer_run(logged_in_client, user, betting_period, seeded_analytics):
    bet_id = place_parlay(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER])["bet_id"]
    publish_a_run_without_odds(RERUN_ID)

    [listed] = logged_in_client.get("/api/my_bets").get_json()
    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert listed["removable"] is False
    assert reply == {"success": False, "error": "Odds have changed since this bet was placed"}
    assert db.session.get(Bet, bet_id).status == "pending"


# The rerun's 10 sims: roster 1 wins and tops 110.5 in the first four, so the parlay placed at 7 of 20
# sims (+186, paying $286 on $100) is worth $114.40 there and offered $108.68.
PARLAY_RERUN_SCORES = np.array([[120.0, 100.0]] * 4 + [[105.0, 100.0]] * 3 + [[115.0, 125.0]] * 2 + [[100.0, 110.0]])


def test_a_parlay_is_cashed_out_at_the_joint_chance_of_the_rerun(
    logged_in_client, user, betting_period, seeded_analytics
):
    bet_id = place_parlay(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER])["bet_id"]
    publish_a_rerun(RERUN_ID, PARLAY_RERUN_SCORES)

    reply = cash_out(logged_in_client, bet_id, offer=108.68)

    assert reply == {"success": True, "new_balance": pytest.approx(1008.68), "cash_out_amount": 108.68}
    db.session.refresh(user)
    assert (user.account_balance, user.total_pnl) == pytest.approx((1008.68, 8.68))
    bet = db.session.get(Bet, bet_id)
    assert (bet.status, bet.result, bet.cash_out_amount, bet.cash_out_run_id) == (
        "cashed_out",
        8.68,
        108.68,
        RERUN_ID,
    )
    assert bet.settled_at == bet.cashed_out_at
    assert [(leg.status, leg.settled_at) for leg in bet.legs] == [("cashed_out", bet.cashed_out_at)] * 2
    assert weekly_money(user, 10) == pytest.approx((1000.0, 1008.68, 8.68, 0.0, 8.68, 1, 0))


def test_my_bets_offers_a_parlay_a_cash_out_once_a_rerun_moves_its_odds(
    logged_in_client, user, betting_period, seeded_analytics
):
    place_parlay(logged_in_client, [ROSTER_1_WINS, ROSTER_1_OVER])
    publish_a_rerun(RERUN_ID, PARLAY_RERUN_SCORES)

    [listed] = logged_in_client.get("/api/my_bets").get_json()

    assert (listed["bet_type"], listed["cash_out_offer"], listed["removable"]) == ("parlay", 108.68, False)


def move_every_quote():
    for table in ("matchup_ml", "team_ou", "highest_scorer", "make_playoffs"):
        move_a_quote(f"betting_odds_{table}", "1 = 1")


PARLAY_BET = {"legs": [ROSTER_1_WINS, ROSTER_1_OVER], "run_id": RUN_ID, "amount": 100}
FUTURES_PARLAY_BET = {
    "legs": [MAKE_PLAYOFFS, BOB_MAKES_PLAYOFFS],
    "run_id": RUN_ID,
    "amount": 100,
}

# What can happen to the runs after a bet is placed.
RUN_CHANGES = {
    "nothing": lambda: None,
    "a rerun with its scores": lambda: publish_a_rerun(RERUN_ID, PARLAY_RERUN_SCORES),
    "a rerun without its scores": lambda: publish_a_rerun(RERUN_ID),
    "a newer run whose odds never published": lambda: publish_a_run_without_odds(RERUN_ID),
    "a newer run with scores whose odds never published": lambda: publish_a_run_without_odds(
        RERUN_ID, PARLAY_RERUN_SCORES
    ),
    "odds rows at a run never published": move_every_quote,
    "a paused window": pause_the_window,
}


@pytest.mark.parametrize("change", RUN_CHANGES.values(), ids=RUN_CHANGES.keys())
@pytest.mark.parametrize(
    "payload",
    [HIGHEST_SCORER_BET, PLAYOFFS_BET, PARLAY_BET, FUTURES_PARLAY_BET],
    ids=["single", "futures", "parlay", "futures parlay"],
)
def test_a_bet_is_removable_or_offered_a_cash_out_never_both(
    logged_in_client, user, betting_period, seeded_analytics, payload, change
):
    bet_id = logged_in_client.post("/api/place_bet", json=payload).get_json()["bet_id"]
    change()

    [listed] = logged_in_client.get("/api/my_bets").get_json()
    removed = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert not (listed["removable"] and listed["cash_out_offer"])
    assert removed["success"] is listed["removable"]


# Futures parlays, on the seeded run's standings: Alice A and Bob B both make the playoffs in 8 of the 20 seasons,
# and Bob B misses them in one of the six seasons Alice A is champion.
@pytest.mark.parametrize(
    ("legs", "probability", "odds", "leg_quotes"),
    [
        ([MAKE_PLAYOFFS, BOB_MAKES_PLAYOFFS], 0.4, "+150", [("yes", "-400", 0.8), ("yes", "-150", 0.6)]),
        ([BOB_MISSES_PLAYOFFS, ALICE_CHAMPION], 0.05, "+1900", [("no", "+150", 0.4), ("1", "+233", 0.3)]),
    ],
)
def test_a_futures_parlay_is_quoted_at_the_seasons_where_every_leg_wins(
    logged_in_client, user, betting_period, seeded_analytics, legs, probability, odds, leg_quotes
):
    reply = quote_parlay(logged_in_client, legs)

    assert (reply["success"], reply["run_id"], reply["probability"], reply["odds"]) == (True, RUN_ID, probability, odds)
    assert [(leg["selection"], leg["odds"], leg["probability"], leg["line"]) for leg in reply["legs"]] == [
        (*quote, None) for quote in leg_quotes
    ]


def test_a_placed_futures_parlay_posts_to_the_current_week_with_season_long_legs(
    logged_in_client, user, betting_period, seeded_analytics
):
    reply = place_parlay(logged_in_client, [MAKE_PLAYOFFS, BOB_MAKES_PLAYOFFS])

    assert (reply["success"], reply["new_balance"], reply["price"], reply["legs"]) == (
        True,
        900.0,
        150,
        ["2026-make_playoffs-1", "2026-make_playoffs-2"],
    )
    bet = db.session.get(Bet, reply["bet_id"])
    assert (bet.bet_type, bet.week, bet.odds, bet.probability, bet.run_id, bet.potential_win) == (
        "parlay",
        10,
        "+150",
        0.4,
        RUN_ID,
        150.0,
    )
    assert bet.description == "Alice A: Make Playoffs Yes -400 + Bob B: Make Playoffs Yes -150"
    assert [(leg.season, leg.week, leg.market, leg.selection) for leg in bet.legs] == [
        (2026, None, "2026-make_playoffs-1", "yes"),
        (2026, None, "2026-make_playoffs-2", "yes"),
    ]
    assert weekly_money(user, 10) == (1000.0, 900.0, -100.0, 100.0, 0.0, 1, 0)


@pytest.mark.parametrize("send", [quote_parlay, place_parlay])
def test_a_futures_parlay_is_priced_at_its_own_run_whatever_the_weeks(
    logged_in_client, user, betting_period, seeded_analytics, send
):
    # The week moves on to a newer run while the futures rows, and so the slip, stay at the seeded one.
    publish_a_run_without_odds(RERUN_ID)

    reply = send(logged_in_client, [MAKE_PLAYOFFS, BOB_MAKES_PLAYOFFS])

    assert reply["success"] is True
    if send is quote_parlay:
        assert (reply["run_id"], reply["probability"]) == (RUN_ID, 0.4)
    else:
        assert db.session.get(Bet, reply["bet_id"]).run_id == RUN_ID


@pytest.mark.parametrize("send", [quote_parlay, place_parlay])
def test_a_futures_leg_quoted_at_another_run_has_changed(
    logged_in_client, user, betting_period, seeded_analytics, send
):
    move_a_quote("betting_odds_make_playoffs", "team_id = 2")

    reply = send(logged_in_client, [MAKE_PLAYOFFS, BOB_MAKES_PLAYOFFS])

    assert reply == {
        "success": False,
        "error": "Odds have changed",
        "rule": "odds_changed",
        "legs": ["2026-make_playoffs-2"],
        "run_id": RUN_ID,
    }
    assert db.session.query(Bet).count() == 0


def test_a_futures_parlay_is_removable_while_its_legs_quotes_keep_its_run(
    logged_in_client, user, betting_period, seeded_analytics
):
    bet_id = place_parlay(logged_in_client, [MAKE_PLAYOFFS, BOB_MAKES_PLAYOFFS])["bet_id"]
    publish_a_run_without_odds(RERUN_ID)

    [listed] = logged_in_client.get("/api/my_bets").get_json()
    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert listed["removable"] is True
    assert reply == {"success": True, "new_balance": 1000.0}
    assert [leg.status for leg in db.session.get(Bet, bet_id).legs] == ["void", "void"]


def test_a_futures_parlay_stays_once_a_legs_quote_has_a_newer_run(
    logged_in_client, user, betting_period, seeded_analytics
):
    bet_id = place_parlay(logged_in_client, [MAKE_PLAYOFFS, BOB_MAKES_PLAYOFFS])["bet_id"]
    move_a_quote("betting_odds_make_playoffs", "team_id = 2")

    [listed] = logged_in_client.get("/api/my_bets").get_json()
    reply = logged_in_client.delete(f"/api/remove_bet/{bet_id}").get_json()

    assert listed["removable"] is False
    assert reply == {"success": False, "error": "Odds have changed since this bet was placed"}
    assert db.session.get(Bet, bet_id).status == "pending"


def test_a_one_leg_list_is_a_single(logged_in_client, user, betting_period, seeded_analytics):
    reply = place_parlay(logged_in_client, [ROSTER_1_OVER])

    assert reply == {
        "success": True,
        "new_balance": 900.0,
        "bet_id": 1,
        "market": "2026-w10-team_total-1",
        "selection": "over",
        "price": -120,
        "legs": ["2026-w10-team_total-1"],
    }
    bet = db.session.get(Bet, 1)
    assert (bet.bet_type, bet.description, bet.odds, bet.run_id) == (
        "team_total",
        "Alice A O/U 110.50: Over",
        "-120",
        RUN_ID,
    )


def test_a_one_leg_list_at_a_moved_line_is_refused_as_a_single(
    logged_in_client, user, betting_period, seeded_analytics
):
    reply = place_parlay(logged_in_client, [{**ROSTER_1_OVER, "line": 111.25}])

    assert reply == {
        "success": False,
        "error": "Odds have changed",
        "run_id": RUN_ID,
        "price": -120,
        "odds": "-120",
        "line": 110.5,
    }
    assert db.session.query(Bet).count() == 0


def test_a_one_entry_legs_list_that_is_not_an_object_is_an_unknown_market(
    logged_in_client, user, betting_period, seeded_analytics
):
    reply = logged_in_client.post("/api/place_bet", json={"legs": ["x"], "run_id": RUN_ID, "amount": 10}).get_json()

    assert reply == {"success": False, "error": "Unknown market"}


# Spreads on the seeded run: roster 1's median margin over roster 2 is 4, so its main line is -4.0 at +122 (9 of
# 20 sims cover, 2 land on the line); roster 2 at +9.5 covers in 14 sims, at -233.
SPREAD_BET = {"market": "2026-w10-spread-1v2", "selection": "1", "line": -4.0, "run_id": RUN_ID, "amount": 100}


@pytest.mark.parametrize(
    ("selection", "line", "description", "odds", "probability", "potential_win"),
    [
        ("1", -4.0, "Alice A vs Bob B: Alice A -4.0 +122", "+122", 0.45, 122.0),
        ("2", 9.5, "Alice A vs Bob B: Bob B +9.5 -233", "-233", 0.70, 42.92),
    ],
    ids=["main line", "alternate line"],
)
def test_a_spread_is_placed_at_the_line_the_bettor_picked(
    logged_in_client,
    user,
    betting_period,
    seeded_analytics,
    selection,
    line,
    description,
    odds,
    probability,
    potential_win,
):
    reply = logged_in_client.post(
        "/api/place_bet", json={**SPREAD_BET, "selection": selection, "line": line}
    ).get_json()

    assert reply == {
        "success": True,
        "new_balance": 900.0,
        "bet_id": 1,
        "market": "2026-w10-spread-1v2",
        "selection": selection,
        "price": int(odds),
        "legs": ["2026-w10-spread-1v2"],
    }
    bet = db.session.get(Bet, 1)
    assert (bet.bet_type, bet.description, bet.odds, bet.price) == ("spread", description, odds, int(odds))
    assert (bet.probability, bet.run_id, round(bet.potential_win, 2)) == (probability, RUN_ID, potential_win)
    [leg] = bet.legs
    assert (leg.market, leg.selection, leg.line, leg.price, leg.probability) == (
        "2026-w10-spread-1v2",
        selection,
        line,
        int(odds),
        probability,
    )
    [listed] = logged_in_client.get("/api/my_bets").get_json()
    assert (listed["market"], listed["selection"], listed["line"], listed["removable"]) == (
        "2026-w10-spread-1v2",
        selection,
        line,
        True,
    )


def test_a_spread_at_a_newer_run_has_changed(logged_in_client, user, betting_period, seeded_analytics):
    # The rerun has roster 1 beating roster 2 by 10 in 6,181 of its 10,000 sims and losing by 10 in the rest.
    publish_a_rerun(RERUN_ID, rerun_scores())

    reply = logged_in_client.post("/api/place_bet", json=SPREAD_BET).get_json()

    assert reply == {
        "success": False,
        "error": "Odds have changed",
        "run_id": RERUN_ID,
        "price": -162,
        "odds": "-162",
        "line": -4.0,
    }
    assert db.session.query(Bet).count() == 0


def test_a_spread_side_that_always_covers_is_not_offered(logged_in_client, user, betting_period, seeded_analytics):
    reply = logged_in_client.post("/api/place_bet", json={**SPREAD_BET, "line": 30.0}).get_json()

    assert reply == {"success": False, "error": "Not offered"}
    assert db.session.query(Bet).count() == 0


def test_a_spread_parlay_leg_keeps_its_line(logged_in_client, user, betting_period, seeded_analytics):
    spread_leg = {"market": "2026-w10-spread-1v2", "selection": "1", "line": -4.0}

    quoted = quote_parlay(logged_in_client, [spread_leg, ROSTER_1_OVER])
    placed = place_parlay(logged_in_client, [spread_leg, ROSTER_1_OVER])

    # Roster 1 covers -4.0 and tops 110.5 in 6 of the 20 sims.
    assert (quoted["probability"], quoted["odds"], quoted["legs"][0]["line"]) == (0.3, "+233", -4.0)
    bet = db.session.get(Bet, placed["bet_id"])
    assert bet.description == "Alice A vs Bob B: Alice A -4.0 +122 + Alice A O/U 110.50: Over"
    assert [(leg.market, leg.line, leg.price) for leg in bet.legs] == [
        ("2026-w10-spread-1v2", -4.0, 122),
        ("2026-w10-team_total-1", 110.5, -120),
    ]
