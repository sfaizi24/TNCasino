from app.models import Bet, BetLeg, BettingPeriod, User, WeeklyStats
from tests.conftest import RUN_ID


def _seed_leaderboard_data(db_session):
    """Create 3 users with bets and weekly stats for leaderboard testing."""
    users = [
        User(
            id="lb-1", username="alice", email="alice@test.com", first_name="Alice", last_name="Smith", total_pnl=500.0
        ),
        User(id="lb-2", username="bob", email="bob@test.com", first_name="Bob", last_name="Jones", total_pnl=200.0),
        User(
            id="lb-3", username="carol", email="carol@test.com", first_name="Carol", last_name="Davis", total_pnl=-300.0
        ),
    ]
    db_session.session.add_all(users)

    period = BettingPeriod(
        week=10,
        lock_time=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        is_locked=True,
        is_settled=False,
    )
    db_session.session.add(period)

    bets = [
        # Won bets with different odds
        Bet(
            user_id="lb-1",
            bet_type="highest_scorer",
            description="Alice bet",
            amount=100.0,
            odds="+300",
            potential_win=300.0,
            status="won",
            result=300.0,
            week=10,
        ),
        Bet(
            user_id="lb-2",
            bet_type="moneyline",
            description="Bob bet",
            amount=200.0,
            odds="+150",
            potential_win=300.0,
            status="won",
            result=300.0,
            week=10,
        ),
        Bet(
            user_id="lb-1",
            bet_type="highest_scorer",
            description="Alice bet 2",
            amount=50.0,
            odds="-110",
            potential_win=45.45,
            status="won",
            result=45.45,
            week=10,
        ),
        # Lost bets
        Bet(
            user_id="lb-3",
            bet_type="moneyline",
            description="Carol loss 1",
            amount=400.0,
            odds="+100",
            potential_win=400.0,
            status="lost",
            result=-400.0,
            week=10,
        ),
        Bet(
            user_id="lb-2",
            bet_type="team_total",
            description="Bob loss",
            amount=100.0,
            odds="EVEN",
            potential_win=100.0,
            status="lost",
            result=-100.0,
            week=10,
        ),
        # Pending bet (should not appear in settled stats)
        Bet(
            user_id="lb-1",
            bet_type="highest_scorer",
            description="Alice pending",
            amount=50.0,
            odds="+200",
            potential_win=100.0,
            status="pending",
            result=0.0,
            week=10,
        ),
    ]
    db_session.session.add_all(bets)

    stats = [
        WeeklyStats(
            user_id="lb-1",
            week=10,
            starting_balance=1000.0,
            ending_balance=1295.45,
            pnl=295.45,
            settled_pnl=345.45,
            bets_placed=3,
            bets_won=2,
        ),
        WeeklyStats(
            user_id="lb-2",
            week=10,
            starting_balance=1000.0,
            ending_balance=1200.0,
            pnl=200.0,
            settled_pnl=200.0,
            bets_placed=2,
            bets_won=1,
        ),
        WeeklyStats(
            user_id="lb-3",
            week=10,
            starting_balance=1000.0,
            ending_balance=600.0,
            pnl=-400.0,
            settled_pnl=-400.0,
            bets_placed=1,
            bets_won=0,
        ),
    ]
    db_session.session.add_all(stats)
    db_session.session.commit()


def test_alltime_top_ranking(client, db_session, captured_templates):
    _seed_leaderboard_data(db_session)
    client.get("/leaderboard")

    context = captured_templates[0][1]
    top = context["alltime_top"]

    assert len(top) == 3
    assert top[0].total_pnl == 500.0  # Alice
    assert top[1].total_pnl == 200.0  # Bob
    assert top[2].total_pnl == -300.0  # Carol


def test_alltime_bottom_ranking(client, db_session, captured_templates):
    _seed_leaderboard_data(db_session)
    client.get("/leaderboard")

    context = captured_templates[0][1]
    bottom = context["alltime_bottom"]

    assert len(bottom) == 2
    assert bottom[0].total_pnl == -300.0  # Carol (lowest)
    assert bottom[1].total_pnl == 200.0  # Bob


def test_weekly_top_bottom(client, db_session, captured_templates):
    _seed_leaderboard_data(db_session)
    client.get("/leaderboard?week=10")

    context = captured_templates[0][1]
    weekly_top = context["weekly_top"]
    weekly_bottom = context["weekly_bottom"]

    assert len(weekly_top) == 3
    assert weekly_top[0].settled_pnl == 345.45  # Alice
    assert weekly_top[1].settled_pnl == 200.0  # Bob

    assert len(weekly_bottom) == 2
    assert weekly_bottom[0].settled_pnl == -400.0  # Carol


def test_best_odds_bet(client, db_session, captured_templates):
    _seed_leaderboard_data(db_session)
    client.get("/leaderboard")

    context = captured_templates[0][1]
    best = context["best_odds_bet"]

    assert best is not None
    # +300 has the highest numeric odds value among won bets
    assert best.odds == "+300"


def test_most_money_won(client, db_session, captured_templates):
    _seed_leaderboard_data(db_session)
    client.get("/leaderboard")

    context = captured_templates[0][1]
    most = context["most_money_won"]

    assert most is not None
    # Alice's +300 bet: result=300, Bob's +150 bet: result=300
    # Both have result=300, either could be first — just verify it's 300
    assert most.result == 300.0


def test_worst_odds_bet(client, db_session, captured_templates):
    _seed_leaderboard_data(db_session)
    client.get("/leaderboard")

    context = captured_templates[0][1]
    worst = context["worst_odds_bet"]

    assert worst is not None
    # EVEN maps to 0 via REPLACE, +100 maps to 100 — EVEN is lowest
    assert worst.odds == "EVEN"


def test_biggest_loss(client, db_session, captured_templates):
    _seed_leaderboard_data(db_session)
    client.get("/leaderboard")

    context = captured_templates[0][1]
    biggest = context["biggest_loss"]

    assert biggest is not None
    assert biggest.amount == 400.0  # Carol's $400 lost bet


def test_popular_bet_stats(client, db_session, captured_templates):
    _seed_leaderboard_data(db_session)

    # Add extra bets with the same description to test group-by aggregation
    extra_bets = [
        Bet(
            user_id="lb-2",
            bet_type="highest_scorer",
            description="Alice bet",
            amount=75.0,
            odds="+200",
            potential_win=150.0,
            status="lost",
            result=-75.0,
            week=10,
        ),
        Bet(
            user_id="lb-3",
            bet_type="highest_scorer",
            description="Alice bet",
            amount=50.0,
            odds="+200",
            potential_win=100.0,
            status="pending",
            result=0.0,
            week=10,
        ),
        Bet(
            user_id="lb-3",
            bet_type="highest_scorer",
            description="Alice bet",
            amount=25.0,
            odds="+200",
            potential_win=50.0,
            status="removed",
            week=10,
        ),
    ]
    db_session.session.add_all(extra_bets)
    db_session.session.commit()

    client.get("/leaderboard")

    context = captured_templates[0][1]
    popular = context["popular_highest"]

    assert popular is not None
    # "Alice bet" counts 3 times: 1 won + 1 lost + 1 pending; the removed one is left out
    assert popular.count == 3
    assert popular.wins == 1
    assert popular.losses == 1
    assert popular.pending == 1


def test_empty_leaderboard(client, db_session, captured_templates):
    # No data seeded — just a betting period so get_current_week works
    from datetime import UTC, datetime

    period = BettingPeriod(
        week=10,
        lock_time=datetime.now(UTC),
        is_settled=False,
    )
    db_session.session.add(period)
    db_session.session.commit()

    resp = client.get("/leaderboard")
    assert resp.status_code == 200


def test_new_bets_rank_beside_legacy_ones_and_removed_bets_are_left_out(client, db_session):
    _seed_leaderboard_data(db_session)
    won = Bet(
        user_id="lb-2",
        bet_type="highest_scorer",
        description="Bob B: Highest Scorer +500",
        amount=50.0,
        odds="+500",
        potential_win=250.0,
        status="won",
        result=250.0,
        week=10,
        run_id=RUN_ID,
        price=500,
        probability=0.17,
    )
    won.legs = [
        BetLeg(
            season=2026,
            week=10,
            market="2026-w10-highest_scorer",
            selection="2",
            price=500,
            probability=0.17,
            status="won",
        )
    ]
    removed = Bet(
        user_id="lb-3",
        bet_type="lowest_scorer",
        description="Carol D: Lowest Scorer +230",
        amount=50.0,
        odds="+230",
        potential_win=115.0,
        status="removed",
        week=10,
        run_id=RUN_ID,
        price=230,
        probability=0.3,
    )
    removed.legs = [
        BetLeg(
            season=2026,
            week=10,
            market="2026-w10-lowest_scorer",
            selection="3",
            price=230,
            probability=0.3,
            status="void",
        )
    ]
    db_session.session.add_all([won, removed])
    db_session.session.commit()

    resp = client.get("/leaderboard")
    page = resp.get_data(as_text=True)

    assert resp.status_code == 200
    assert "Bob B: Highest Scorer +500" in page  # the best odds that won
    assert "Bob loss" in page  # a legacy row, the worst odds that lost
    assert "Carol D: Lowest Scorer" not in page
    assert "Most Popular Lowest Scorer" not in page


def _lowest_scorer_bet(user_id, description, status):
    return Bet(
        user_id=user_id,
        bet_type="lowest_scorer",
        description=description,
        amount=50.0,
        odds="+230",
        potential_win=115.0,
        status=status,
        result=0.0,
        week=10,
    )


def test_a_popular_bet_on_the_line_shows_as_a_push_and_void_bets_are_left_out(client, db_session, captured_templates):
    _seed_leaderboard_data(db_session)
    db_session.session.add_all(
        [
            _lowest_scorer_bet("lb-1", "Bob J: Lowest Scorer +230", "push"),
            _lowest_scorer_bet("lb-3", "Bob J: Lowest Scorer +230", "push"),
            _lowest_scorer_bet("lb-1", "Carol D: Lowest Scorer +230", "void"),
            _lowest_scorer_bet("lb-2", "Carol D: Lowest Scorer +230", "void"),
            _lowest_scorer_bet("lb-3", "Carol D: Lowest Scorer +230", "void"),
        ]
    )
    db_session.session.commit()

    page = client.get("/leaderboard").get_data(as_text=True)

    popular = captured_templates[0][1]["popular_lowest"]
    assert (popular.description, popular.count, popular.pushes) == ("Bob J: Lowest Scorer +230", 2, 2)
    assert '<div class="tnc-lb-outcome tnc-lb-outcome-push">Push</div>' in page
    assert "Carol D: Lowest Scorer" not in page
