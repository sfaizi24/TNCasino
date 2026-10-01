import pytest

from app.models import Bet
from app.routes.account import bet_history


def _bet(status, result=0.0, week=4, bet_type="moneyline", amount=50.0, description="Hasan ML"):
    return Bet(
        user_id="test-user-1",
        bet_type=bet_type,
        description=description,
        amount=amount,
        odds="+100",
        potential_win=amount,
        status=status,
        result=result,
        week=week,
    )


def test_history_groups_weeks_newest_first_then_futures():
    bets = [_bet("won", 50.0, week=3), _bet("pending", week=4, bet_type="make_playoffs"), _bet("lost", -50.0, week=4)]

    history = bet_history(bets)

    assert [group.label for group in history] == ["Week 4", "Week 3", "Futures"]
    assert history[2].bets == [bets[1]]


def test_a_groups_pnl_sums_its_settled_results():
    bets = [_bet("won", 80.0), _bet("lost", -50.0), _bet("cashed_out", 11.4), _bet("pending"), _bet("push")]

    (week,) = bet_history(bets)

    assert week.pnl == pytest.approx(41.4)


def test_removed_bets_are_left_out_but_cash_outs_stay():
    removed = _bet("removed")
    cashed = _bet("cashed_out", -8.0)

    (week,) = bet_history([removed, cashed])

    assert week.bets == [cashed]


def test_a_week_of_only_removed_bets_has_no_group():
    assert bet_history([_bet("removed")]) == []


def test_account_page_shows_balance_in_play_and_record(logged_in_client, user, db_session):
    user.account_balance = 1234.5
    db_session.session.add_all(
        [
            _bet("pending", amount=150.0, description="Ammad ML"),
            _bet("won", 50.0),
            _bet("lost", -50.0),
            _bet("removed", description="Mehdi: Highest Scorer"),
        ]
    )
    db_session.session.commit()

    page = logged_in_client.get("/account").get_data(as_text=True)

    assert "$1,234.50" in page
    assert "$150.00 in play" in page
    assert "1&ndash;1" in page
    assert "To win $150.00" in page
    assert "Mehdi: Highest Scorer" not in page
