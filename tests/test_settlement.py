import json

import pytest
from sqlalchemy import text

from app import ledger
from app.database import db
from app.models import Bet, BetLeg, User, WeeklyStats
from tests.conftest import RUN_ID, play_weeks, set_points

HIGHEST_SCORER_BET = {"market": "2026-w10-highest_scorer", "selection": "1", "run_id": RUN_ID, "amount": 100}
MONEYLINE_BET = {"market": "2026-w10-moneyline-1v2", "selection": "2", "run_id": RUN_ID, "amount": 100}
TEAM_TOTAL_BET = {
    "market": "2026-w10-team_total-1",
    "selection": "under",
    "line": 110.5,
    "run_id": RUN_ID,
    "amount": 100,
}
LOWEST_SCORER_BET = {"market": "2026-w10-lowest_scorer", "selection": "1", "run_id": RUN_ID, "amount": 100}
FIRST_PLACE_BET = {"market": "2026-first_place", "selection": "2", "run_id": RUN_ID, "amount": 100}
PLAYOFFS_BET = {"market": "2026-make_playoffs-1", "selection": "yes", "run_id": RUN_ID, "amount": 100}
LAST_PLACE_BET = {"market": "2026-last_place", "selection": "2", "run_id": RUN_ID, "amount": 100}
CHAMPION_BET = {"market": "2026-champion", "selection": "1", "run_id": RUN_ID, "amount": 100}

# With Alice A on 110.50 and Bob B on 120.25, the moneyline on Bob wins at +130, the under on
# Alice's 110.50 line pushes, the highest scorer on Alice loses, the lowest scorer on Alice wins at
# +400 and the first place bet waits for weeks 1 to 9, the rest of the seeded league's regular season.
WEEK_OF_BETS = [MONEYLINE_BET, TEAM_TOTAL_BET, HIGHEST_SCORER_BET, LOWEST_SCORER_BET, FIRST_PLACE_BET]
SCORES = {1: 110.5, 2: 120.25}


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


def _place(client, bets):
    ids = []
    for bet in bets:
        ids.append(client.post("/api/place_bet", json=bet).get_json()["bet_id"])
    return ids


def _preview(client, week=10):
    return client.get(f"/api/admin/settlement_preview?week={week}").get_json()


def _settle(client, shown):
    return client.post("/api/admin/settle_outcomes", json={"week": 10, "bets": shown}).get_json()


def _decided(preview):
    """What the admin page sends when the admin settles the preview's decided bets."""
    return [{"id": row["id"], "outcome": row["outcome"]} for row in preview["bets"] if row["outcome"] != "undecided"]


def test_the_preview_judges_every_pending_bet_of_the_week_beside_the_scores(
    admin_client, user, betting_period, seeded_analytics, db_session
):
    moneyline, team_total, highest, lowest, first_place = _place(admin_client, WEEK_OF_BETS)
    legacy, _ = _place_legacy_bet(db_session, user, betting_period)
    set_points(db_session.session, SCORES)

    preview = _preview(admin_client)

    assert preview["scores"] == [
        {"roster_id": 1, "team": "Alice A", "points": 110.5},
        {"roster_id": 2, "team": "Bob B", "points": 120.25},
    ]
    assert [(row["id"], row["user"], row["outcome"], row["reason"]) for row in preview["bets"]] == [
        (moneyline, "Admin User", "won", "Bob B 120.25 vs Alice A 110.50"),
        (team_total, "Admin User", "push", "Alice A 110.50, line 110.50"),
        (highest, "Admin User", "lost", "highest 120.25: Bob B"),
        (lowest, "Admin User", "won", "lowest 110.50: Alice A"),
        (first_place, "Admin User", "undecided", "regular season not complete: 0 of 2 rosters scored in week 1"),
        (legacy.id, "Test User", "undecided", "placed before market keys: settle by hand"),
    ]
    assert (preview["success"], preview["week"], preview["decided"], preview["undecided"]) == (True, 10, 4, 2)


def test_a_preview_row_shows_the_bet_as_it_was_placed(admin_client, user, betting_period, seeded_analytics, db_session):
    [team_total] = _place(admin_client, [TEAM_TOTAL_BET])
    legacy, _ = _place_legacy_bet(db_session, user, betting_period)
    set_points(db_session.session, SCORES)

    assert _preview(admin_client)["bets"] == [
        {
            "id": team_total,
            "user": "Admin User",
            "description": "Alice A O/U 110.50: Under",
            "amount": 100.0,
            "odds": "+100",
            "potential_win": 100.0,
            "market": "2026-w10-team_total-1",
            "selection": "under",
            "line": 110.5,
            "legs": [{"market": "2026-w10-team_total-1", "selection": "under", "line": 110.5}],
            "outcome": "push",
            "reason": "Alice A 110.50, line 110.50",
        },
        {
            "id": legacy.id,
            "user": "Test User",
            "description": "Player A: Highest Scorer +200",
            "amount": 100.0,
            "odds": "+200",
            "potential_win": 200.0,
            "market": None,
            "selection": None,
            "line": None,
            "legs": [],
            "outcome": "undecided",
            "reason": "placed before market keys: settle by hand",
        },
    ]


def _place_parlay(user):
    """Bob B to win at +130 and Alice A under 110.50 at +100, as one $100 parlay at +300."""
    legs = [
        BetLeg(season=2026, week=10, market=MONEYLINE_BET["market"], selection="2", price=130, probability=0.4),
        BetLeg(
            season=2026,
            week=10,
            market=TEAM_TOTAL_BET["market"],
            selection="under",
            line=110.5,
            price=100,
            probability=0.45,
        ),
    ]
    parlay = Bet(
        user_id=user.id,
        bet_type="parlay",
        description="Alice A vs Bob B: Bob B +130 + Alice A O/U 110.50: Under",
        week=10,
        amount=100.0,
        odds="+300",
        price=300,
        probability=0.25,
        run_id=RUN_ID,
        potential_win=300.0,
        legs=legs,
    )
    ledger.open_week(user.id, 10)
    assert ledger.place(parlay)
    db.session.commit()
    return parlay.id


def test_a_parlays_preview_row_lists_its_legs_in_place_of_one_market(
    admin_client, admin_user, betting_period, seeded_analytics, db_session
):
    parlay = _place_parlay(admin_user)
    set_points(db_session.session, SCORES)

    [row] = _preview(admin_client)["bets"]

    assert row == {
        "id": parlay,
        "user": "Admin User",
        "description": "Alice A vs Bob B: Bob B +130 + Alice A O/U 110.50: Under",
        "amount": 100.0,
        "odds": "+300",
        "potential_win": 300.0,
        "market": None,
        "selection": None,
        "line": None,
        "legs": [
            {"market": "2026-w10-moneyline-1v2", "selection": "2", "line": None},
            {"market": "2026-w10-team_total-1", "selection": "under", "line": 110.5},
        ],
        "outcome": "won",
        "reason": "won, 1 leg pushed: pays 130.00 on the rest",
    }


def test_settling_a_parlay_with_a_pushed_leg_pays_the_rest_and_settles_each_leg_as_it_stood(
    admin_client, admin_user, betting_period, seeded_analytics, db_session
):
    parlay = _place_parlay(admin_user)
    set_points(db_session.session, SCORES)

    reply = _settle(admin_client, _decided(_preview(admin_client)))

    bet = db_session.session.get(Bet, parlay)
    week = db_session.session.query(WeeklyStats).filter_by(user_id=admin_user.id, week=10).one()
    db_session.session.refresh(admin_user)
    assert reply == {"success": True, "settled": [parlay], "skipped": []}
    assert (bet.status, bet.potential_win, bet.result) == ("won", 130.0, 130.0)
    assert sorted((leg.market, leg.status) for leg in bet.legs) == [
        ("2026-w10-moneyline-1v2", "won"),
        ("2026-w10-team_total-1", "push"),
    ]
    assert (admin_user.account_balance, admin_user.total_pnl) == (1130.0, 130.0)
    assert (week.bets_won, week.settled_pnl, week.active_bets_amount) == (1, 130.0, 0.0)


@pytest.mark.parametrize("won", [True, False])
def test_a_parlay_is_not_settled_by_hand(admin_client, admin_user, betting_period, seeded_analytics, db_session, won):
    parlay = _place_parlay(admin_user)

    reply = admin_client.post("/api/admin/settle_bet", json={"bet_id": parlay, "won": won}).get_json()

    bet = db_session.session.get(Bet, parlay)
    db_session.session.refresh(admin_user)
    assert reply == {"success": False, "error": "Parlays settle from the Settle Week card"}
    assert [bet.status] + [leg.status for leg in bet.legs] == ["pending", "pending", "pending"]
    assert admin_user.account_balance == 900.0


def test_a_parlay_can_still_be_voided(admin_client, admin_user, betting_period, seeded_analytics, db_session):
    parlay = _place_parlay(admin_user)

    reply = admin_client.post("/api/admin/void_bet", json={"bet_id": parlay}).get_json()

    bet = db_session.session.get(Bet, parlay)
    db_session.session.refresh(admin_user)
    assert reply == {"success": True}
    assert [bet.status] + [leg.status for leg in bet.legs] == ["void", "void", "void"]
    assert admin_user.account_balance == 1000.0


def test_the_preview_leaves_out_settled_bets_and_other_weeks(admin_client, betting_period, seeded_analytics):
    moneyline, team_total = _place(admin_client, [MONEYLINE_BET, TEAM_TOTAL_BET])
    admin_client.post("/api/admin/settle_bet", json={"bet_id": moneyline, "won": True})

    assert [row["id"] for row in _preview(admin_client)["bets"]] == [team_total]
    assert _preview(admin_client, week=11)["bets"] == []


def test_a_bettor_without_a_name_shows_as_the_start_of_their_id(
    admin_client, betting_period, seeded_analytics, db_session
):
    db_session.session.add(User(id="a1b2c3d4-e5f6", account_balance=900.0))
    db_session.session.add(
        Bet(
            user_id="a1b2c3d4-e5f6",
            bet_type="highest_scorer",
            description="Player A: Highest Scorer +200",
            week=10,
            amount=100.0,
            odds="+200",
            potential_win=200.0,
        )
    )
    db_session.session.commit()

    preview = admin_client.get("/api/admin/settlement_preview").get_json()

    assert (preview["week"], [row["user"] for row in preview["bets"]]) == (10, ["User #a1b2c3d4"])


def test_a_week_without_published_scores_decides_nothing(admin_client, betting_period, seeded_analytics, db_session):
    _place(admin_client, [MONEYLINE_BET])
    db_session.session.execute(text("DELETE FROM sleeper_matchups"))
    db_session.session.commit()

    preview = _preview(admin_client)

    assert (preview["scores"], preview["decided"], preview["undecided"]) == ([], 0, 1)
    assert preview["bets"][0]["reason"] == "no score for Team 2 and Team 1"


def test_a_week_with_scores_from_two_leagues_is_not_previewed(
    admin_client, betting_period, seeded_analytics, db_session
):
    db_session.session.execute(
        text("INSERT INTO sleeper_matchups (league_id, week, roster_id, points) VALUES ('old-league', 10, 99, 101.0)")
    )
    db_session.session.commit()

    assert _preview(admin_client) == {
        "success": False,
        "error": "Week 10 has scores from 2 leagues; publish only this season's league",
    }


def test_settling_the_preview_closes_each_decided_bet_and_leaves_the_rest(
    admin_client, admin_user, betting_period, seeded_analytics, db_session
):
    moneyline, team_total, highest, lowest, first_place = _place(admin_client, WEEK_OF_BETS)
    set_points(db_session.session, SCORES)

    reply = _settle(admin_client, _decided(_preview(admin_client)))

    assert reply == {"success": True, "settled": [moneyline, team_total, highest, lowest], "skipped": []}
    statuses = {bet.id: (bet.status, bet.legs[0].status) for bet in db_session.session.query(Bet)}
    assert statuses == {
        moneyline: ("won", "won"),
        team_total: ("push", "push"),
        highest: ("lost", "lost"),
        lowest: ("won", "won"),
        first_place: ("pending", "pending"),
    }
    week = db_session.session.query(WeeklyStats).filter_by(user_id=admin_user.id, week=10).one()
    db_session.session.refresh(admin_user)
    assert (admin_user.account_balance, admin_user.total_pnl) == (1330.0, 430.0)
    assert (week.bets_placed, week.bets_won, week.settled_pnl, week.active_bets_amount) == (5, 2, 430.0, 100.0)


def test_settling_skips_each_bet_that_no_longer_stands_as_shown(
    admin_client, betting_period, seeded_analytics, db_session
):
    moneyline, team_total, highest, lowest, first_place = _place(admin_client, WEEK_OF_BETS)
    admin_client.post("/api/admin/settle_bet", json={"bet_id": highest, "won": False})
    set_points(db_session.session, SCORES)
    shown = [
        {"id": team_total, "outcome": "won"},
        {"id": highest, "outcome": "lost"},
        {"id": 999, "outcome": "won"},
        {"id": first_place, "outcome": "won"},
    ]

    reply = _settle(admin_client, shown)

    assert reply == {
        "success": True,
        "settled": [],
        "skipped": [
            {"id": team_total, "reason": "scores changed: now push"},
            {"id": highest, "reason": "already settled"},
            {"id": 999, "reason": "not found"},
            {"id": first_place, "reason": "undecided"},
        ],
    }
    statuses = [bet.status for bet in db_session.session.query(Bet).order_by(Bet.id)]
    assert statuses == ["pending", "pending", "lost", "pending", "pending"]


def test_bets_settled_before_a_failure_stand_and_the_rest_stay_pending(
    admin_client, admin_user, betting_period, seeded_analytics, db_session, monkeypatch
):
    moneyline, _, _, lowest, _ = _place(admin_client, WEEK_OF_BETS)
    set_points(db_session.session, SCORES)
    settle = ledger.settle

    def settle_until_lowest(bet, won, **outcome):
        if bet.id == lowest:
            raise RuntimeError("connection lost")
        return settle(bet, won, **outcome)

    monkeypatch.setattr(ledger, "settle", settle_until_lowest)

    reply = _settle(admin_client, [{"id": moneyline, "outcome": "won"}, {"id": lowest, "outcome": "won"}])

    db_session.session.refresh(admin_user)
    assert reply == {"success": False, "error": "connection lost"}
    assert (db_session.session.get(Bet, moneyline).status, db_session.session.get(Bet, lowest).status) == (
        "won",
        "pending",
    )
    assert admin_user.account_balance == 500.0 + 230.0


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/admin/settlement_preview?week=10", None),
        ("POST", "/api/admin/settle_outcomes", {"week": 10, "bets": [{"id": 1, "outcome": "won"}]}),
        ("POST", "/api/admin/void_bet", {"bet_id": 1}),
    ],
)
def test_the_settlement_routes_turn_a_bettor_away(
    logged_in_client, betting_period, seeded_analytics, db_session, method, path, body
):
    [bet_id] = _place(logged_in_client, [MONEYLINE_BET])
    set_points(db_session.session, SCORES)

    response = logged_in_client.open(path, method=method, json=body)

    assert response.status_code == 302
    assert db_session.session.get(Bet, bet_id).status == "pending"


def test_voiding_a_bet_refunds_it_and_stops_counting_it(
    admin_client, admin_user, betting_period, seeded_analytics, db_session
):
    [bet_id] = _place(admin_client, [MONEYLINE_BET])

    reply = admin_client.post("/api/admin/void_bet", json={"bet_id": bet_id}).get_json()

    bet = db_session.session.get(Bet, bet_id)
    [leg] = bet.legs
    week = db_session.session.query(WeeklyStats).filter_by(user_id=admin_user.id, week=10).one()
    db_session.session.refresh(admin_user)
    assert reply == {"success": True}
    assert (bet.status, bet.result, leg.status) == ("void", 0.0, "void")
    assert bet.settled_at is not None
    assert leg.settled_at == bet.settled_at
    assert (admin_user.account_balance, admin_user.total_pnl) == (1000.0, 0.0)
    assert (week.bets_placed, week.active_bets_amount) == (0, 0.0)


def test_only_a_pending_bet_can_be_voided(admin_client, betting_period, seeded_analytics):
    [bet_id] = _place(admin_client, [MONEYLINE_BET])
    admin_client.post("/api/admin/void_bet", json={"bet_id": bet_id})

    again = admin_client.post("/api/admin/void_bet", json={"bet_id": bet_id}).get_json()
    unknown = admin_client.post("/api/admin/void_bet", json={"bet_id": 999}).get_json()

    assert again == {"success": False, "error": "Bet already settled"}
    assert unknown == {"success": False, "error": "Bet not found"}


def test_the_account_page_shows_a_push_and_a_void_as_neither_won_nor_lost(
    admin_client, betting_period, seeded_analytics, db_session
):
    team_total, moneyline = _place(admin_client, [TEAM_TOTAL_BET, MONEYLINE_BET])
    set_points(db_session.session, SCORES)
    _settle(admin_client, [{"id": team_total, "outcome": "push"}])
    admin_client.post("/api/admin/void_bet", json={"bet_id": moneyline})

    page = admin_client.get("/account").get_data(as_text=True)

    assert "Push" in page
    assert "Void" in page
    assert "-$100.00" not in page


def _placed_in_week_8(session):
    """Move every bet placed so far, and its weekly stats, back to week 8."""
    session.execute(text("UPDATE bets SET week = 8"))
    session.execute(text("UPDATE weekly_stats SET week = 8"))
    session.commit()


def _play_the_season(session):
    """Alice A wins all ten weeks of the seeded league's regular season: 10-0 on 1,200.25 to Bob B's 0-10 on 1,100.00."""
    play_weeks(session, {1: [120.0] * 9, 2: [110.0] * 9})
    set_points(session, {1: 120.25, 2: 110.0})


def _weekly(user_id, week):
    stats = db.session.query(WeeklyStats).filter_by(user_id=user_id, week=week).one()
    return stats.bets_placed, stats.bets_won, stats.active_bets_amount, stats.settled_pnl


def test_the_last_weeks_preview_judges_the_standings_futures_of_every_week_and_posts_them_there(
    admin_client, admin_user, betting_period, seeded_analytics, db_session
):
    first_place, playoffs, last_place, champion = _place(
        admin_client, [FIRST_PLACE_BET, PLAYOFFS_BET, LAST_PLACE_BET, CHAMPION_BET]
    )
    _placed_in_week_8(db_session.session)
    _play_the_season(db_session.session)

    preview = _preview(admin_client)
    reply = _settle(admin_client, _decided(preview))

    assert [(row["id"], row["outcome"], row["reason"]) for row in preview["bets"]] == [
        (first_place, "lost", "2nd of 2: 0-10, 1,100.00 pts"),
        (playoffs, "won", "1st of 2: 10-0, 1,200.25 pts"),
        (last_place, "won", "2nd of 2: 0-10, 1,100.00 pts"),
    ]
    assert reply == {"success": True, "settled": [first_place, playoffs, last_place], "skipped": []}
    assert db_session.session.get(Bet, champion).status == "pending"
    db_session.session.refresh(admin_user)
    assert (admin_user.account_balance, admin_user.total_pnl) == (1055.0, 155.0)
    # The results post to week 10; week 8 keeps the placements and the champion's stake.
    assert _weekly(admin_user.id, 10) == (0, 2, 0.0, 155.0)
    assert _weekly(admin_user.id, 8) == (4, 0, 100.0, 0.0)


def test_before_the_last_week_the_preview_lists_only_the_weeks_own_futures(
    admin_client, betting_period, seeded_analytics, db_session
):
    _place(admin_client, [FIRST_PLACE_BET, LAST_PLACE_BET])
    _placed_in_week_8(db_session.session)
    [playoffs] = _place(admin_client, [PLAYOFFS_BET])
    settings = {"num_teams": 2, "playoff_teams": 1, "playoff_week_start": 15}
    db_session.session.execute(
        text("UPDATE sleeper_leagues SET settings = :settings"), {"settings": json.dumps(settings)}
    )
    _play_the_season(db_session.session)

    preview = _preview(admin_client)

    assert [(row["id"], row["outcome"], row["reason"]) for row in preview["bets"]] == [
        (playoffs, "undecided", "futures: judged from the final standings"),
    ]


def test_a_futures_bet_settled_by_hand_posts_its_result_to_the_current_week(
    admin_client, admin_user, betting_period, seeded_analytics, db_session
):
    [champion] = _place(admin_client, [CHAMPION_BET])
    _placed_in_week_8(db_session.session)

    listed = admin_client.get("/api/admin/pending_bets").get_json()
    reply = admin_client.post("/api/admin/settle_bet", json={"bet_id": champion, "won": True}).get_json()

    assert [bet["id"] for bet in listed] == [champion]
    assert reply == {"success": True}
    db_session.session.refresh(admin_user)
    assert (admin_user.account_balance, admin_user.total_pnl) == (1233.0, 233.0)
    assert _weekly(admin_user.id, 10) == (0, 1, 0.0, 233.0)
    assert _weekly(admin_user.id, 8) == (1, 0, 0.0, 0.0)


def test_a_weekly_bet_settled_by_hand_posts_its_result_to_its_own_week(
    admin_client, admin_user, betting_period, seeded_analytics, db_session
):
    [moneyline] = _place(admin_client, [MONEYLINE_BET])
    _placed_in_week_8(db_session.session)

    admin_client.post("/api/admin/settle_bet", json={"bet_id": moneyline, "won": True})

    assert _weekly(admin_user.id, 8) == (1, 1, 0.0, 130.0)
    assert db_session.session.query(WeeklyStats).filter_by(week=10).count() == 0
