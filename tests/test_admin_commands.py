from datetime import datetime

import pytest

from app.models import Bet, BettingPeriod, User
from tests.conftest import set_points
from tests.test_settlement import MONEYLINE_BET, SCORES, WEEK_OF_BETS, _place, _place_parlay


@pytest.fixture
def run(app):
    runner = app.test_cli_runner()

    def invoke(*args):
        return runner.invoke(args=["admin", *args])

    return invoke


def _statuses(session):
    session.expire_all()
    return [bet.status for bet in session.query(Bet).order_by(Bet.id)]


def test_set_period_creates_a_week_then_moves_its_lock_and_reopens_it(run, db_session):
    assert (
        run("set-period", "--week", "5", "--lock", "2026-10-11T13:30").output == "week 5: locks 2026-10-11 13:30 UTC\n"
    )
    period = db_session.session.query(BettingPeriod).filter_by(week=5).one()
    period.is_locked = True
    db_session.session.commit()

    run("set-period", "--week", "5", "--lock", "2026-10-12T00:15")

    db_session.session.expire_all()
    assert (period.lock_time.replace(tzinfo=None), period.is_locked) == (datetime(2026, 10, 12, 0, 15), False)
    assert run("periods").output == "week  5  lock 2026-10-12 00:15 UTC  open\n"


def test_set_period_refuses_a_lock_time_it_cannot_read(run):
    result = run("set-period", "--week", "5", "--lock", "Sunday 1pm")

    assert result.exit_code != 0
    assert isinstance(result.exception, ValueError)


def test_settle_shows_the_outcomes_and_leaves_every_bet_pending(
    run, admin_client, betting_period, seeded_analytics, db_session
):
    moneyline, *_ = _place(admin_client, WEEK_OF_BETS)
    set_points(db_session.session, SCORES)

    result = run("settle", "--week", "10")

    assert result.exit_code == 0
    assert "Alice A                  110.50\nBob B                    120.25\n" in result.output
    assert f"#{moneyline:<5} won       $100.00  " in result.output
    assert "       Bob B 120.25 vs Alice A 110.50\n" in result.output
    assert result.output.endswith("\n4 decided, 1 undecided\n")
    assert _statuses(db_session.session) == ["pending"] * 5


def test_settle_with_apply_settles_the_decided_bets_and_leaves_the_rest(
    run, admin_client, admin_user, betting_period, seeded_analytics, db_session
):
    moneyline, team_total, highest, lowest, last_place = _place(admin_client, WEEK_OF_BETS)
    set_points(db_session.session, SCORES)

    result = run("settle", "--week", "10", "--apply")

    assert result.output.endswith(f"#{moneyline}: won\n#{team_total}: push\n#{highest}: lost\n#{lowest}: won\n")
    assert _statuses(db_session.session) == ["won", "push", "lost", "won", "pending"]
    assert db_session.session.get(User, admin_user.id).account_balance == 1330.0


def test_settle_bet_settles_a_single_by_hand_and_refuses_a_weekly_parlay(
    run, admin_client, user, betting_period, seeded_analytics, db_session
):
    (moneyline,) = _place(admin_client, [MONEYLINE_BET])
    parlay = _place_parlay(user)

    assert run("settle-bet", str(moneyline), "--won").output == f"#{moneyline}: won\n"
    refused = run("settle-bet", str(parlay), "--lost")
    again = run("settle-bet", str(moneyline), "--lost")

    assert "a weekly parlay settles through `settle`, leg by leg" in refused.output
    assert f"bet {moneyline} is already won" in again.output
    assert _statuses(db_session.session) == ["won", "pending"]


def test_void_refunds_a_pending_bet(run, admin_client, admin_user, betting_period, seeded_analytics, db_session):
    (moneyline,) = _place(admin_client, [MONEYLINE_BET])

    assert run("void", str(moneyline)).output == f"#{moneyline}: void\n"
    assert _statuses(db_session.session) == ["void"]
    assert db_session.session.get(User, admin_user.id).account_balance == 1000.0
    assert "bet 999 not found" in run("void", "999").output


def test_close_week_marks_the_period_settled(run, betting_period, db_session):
    assert run("close-week", "--week", "10").output == "week 10: settled\n"
    db_session.session.expire_all()
    assert betting_period.is_settled
    assert "week 11 has no betting period" in run("close-week", "--week", "11").output
