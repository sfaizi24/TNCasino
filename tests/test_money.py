from app.models import Bet, BetLeg, WeeklyStats


def single(user, market, selection, amount, probability=0.5, status="pending"):
    return Bet(
        user_id=user.id,
        bet_type="single",
        description=market,
        amount=amount,
        odds="+100",
        potential_win=amount,
        status=status,
        week=10,
        legs=[
            BetLeg(
                season=2026,
                week=10 if "-w" in market else None,
                market=market,
                selection=selection,
                price=100,
                probability=probability,
            )
        ],
    )


def test_money_shows_the_stakes_on_each_side_without_naming_a_bettor(
    client, seeded_analytics, betting_period, user, db_session
):
    db_session.session.add_all(
        [
            single(user, "2026-w10-moneyline-1v2", "1", 30.0, probability=0.6),
            single(user, "2026-w10-moneyline-1v2", "1", 20.0, probability=0.6),
            single(user, "2026-w10-moneyline-1v2", "2", 50.0, probability=0.4, status="removed"),
            single(user, "2026-w10-highest_scorer", "2", 10.0, probability=0.4),
        ]
    )
    db_session.session.commit()

    data = client.get("/api/money").get_json()

    moneyline, highest = data["markets"]
    assert moneyline == {
        "title": "Alice A vs Bob B",
        "stake": 50.0,
        "sides": [{"label": "Alice A", "stake": 50.0, "bets": 2}, {"label": "Bob B", "stake": 0.0, "bets": 0}],
    }
    assert highest["title"] == "Highest scorer"
    assert data["favorite_share"] == 100.0
    assert user.email not in str(data)


def test_money_keeps_futures_and_parlays_apart_from_the_week(
    client, seeded_analytics, betting_period, user, db_session
):
    parlay = Bet(
        user_id=user.id,
        bet_type="parlay",
        description="2-leg parlay",
        amount=15.0,
        odds="+300",
        potential_win=45.0,
        week=10,
        legs=[
            BetLeg(season=2026, week=10, market="2026-w10-moneyline-1v2", selection="2", price=150, probability=0.4),
            BetLeg(season=2026, week=10, market="2026-w10-lowest_scorer", selection="1", price=100, probability=0.5),
        ],
    )
    db_session.session.add_all([single(user, "2026-make_playoffs-1", "no", 25.0), parlay])
    db_session.session.commit()

    data = client.get("/api/money").get_json()

    assert data["markets"] == []
    assert data["futures"][0]["title"] == "Alice A makes playoffs"
    assert data["futures"][0]["sides"][0] == {"label": "No", "stake": 25.0, "bets": 1}
    assert data["parlays"] == {"slips": 1, "stake": 15.0}
    assert data["favorite_share"] is None


def test_money_reverses_the_bettors_settled_results_for_the_house(
    client, seeded_analytics, betting_period, user, admin_user, db_session
):
    db_session.session.add_all(
        [
            WeeklyStats(user_id=user.id, week=10, starting_balance=1000, ending_balance=1030, pnl=30, settled_pnl=30),
            WeeklyStats(
                user_id=admin_user.id, week=10, starting_balance=1000, ending_balance=950, pnl=-50, settled_pnl=-50
            ),
            WeeklyStats(
                user_id=admin_user.id, week=9, starting_balance=1000, ending_balance=900, pnl=-100, settled_pnl=-100
            ),
        ]
    )
    db_session.session.commit()

    assert client.get("/api/money").get_json()["house_net"] == 20.0
