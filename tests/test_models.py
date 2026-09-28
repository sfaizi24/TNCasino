from datetime import UTC

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.database import db
from app.migrations import run_schema_migrations
from app.models import Bet, BettingPeriod, User, WeeklyStats

# The bets table as it stood before bets recorded their market.
LEGACY_BETS_TABLE = """
    CREATE TABLE bets (
        id INTEGER PRIMARY KEY, user_id VARCHAR NOT NULL, bet_type VARCHAR NOT NULL,
        description TEXT NOT NULL, amount FLOAT NOT NULL, odds VARCHAR NOT NULL,
        potential_win FLOAT NOT NULL, status VARCHAR, result FLOAT, week INTEGER,
        created_at DATETIME, settled_at DATETIME
    )
"""
# One bet under each old type name, and one whose type keeps its name.
LEGACY_BETS = [
    ("first_seed", "Ammady: #1 Seed +250"),
    ("team_ou", "Ammady O/U 101.5: Under"),
    ("ammad_playoff", "Samer: Ammad Playoff -150"),
    ("moneyline", "Samer vs Ammad: Samer -143"),
]


def test_user_defaults(db_session):
    user = User(id="u1")
    db_session.session.add(user)
    db_session.session.commit()

    assert user.account_balance == 1000.0
    assert user.total_pnl == 0.0
    assert user.is_admin is False
    assert user.created_at is not None


def test_bet_defaults(db_session, user):
    bet = Bet(
        user_id=user.id,
        bet_type="moneyline",
        description="Test bet",
        amount=50.0,
        odds="+100",
        potential_win=50.0,
    )
    db_session.session.add(bet)
    db_session.session.commit()

    assert bet.status == "pending"
    assert bet.result == 0.0


def test_betting_period_defaults(db_session):
    from datetime import datetime

    period = BettingPeriod(week=1, lock_time=datetime.now(UTC))
    db_session.session.add(period)
    db_session.session.commit()

    assert period.is_locked is False
    assert period.is_settled is False


def test_weekly_stats_unique_constraint(db_session, user):
    stat1 = WeeklyStats(
        user_id=user.id,
        week=10,
        starting_balance=1000.0,
        ending_balance=900.0,
        pnl=-100.0,
    )
    db_session.session.add(stat1)
    db_session.session.commit()

    stat2 = WeeklyStats(
        user_id=user.id,
        week=10,
        starting_balance=1000.0,
        ending_balance=800.0,
        pnl=-200.0,
    )
    db_session.session.add(stat2)

    with pytest.raises(IntegrityError):
        db_session.session.commit()


def test_user_bets_relationship(db_session, user):
    bet = Bet(
        user_id=user.id,
        bet_type="moneyline",
        description="Test bet",
        amount=50.0,
        odds="+100",
        potential_win=50.0,
    )
    db_session.session.add(bet)
    db_session.session.commit()

    db_session.session.refresh(user)
    assert len(user.bets) == 1
    assert user.bets[0].description == "Test bet"


def test_migrations_add_the_quote_columns_and_rename_legacy_bet_types(file_backed_app, caplog):
    with file_backed_app.app_context():
        with db.engine.begin() as conn:
            conn.execute(text("DROP TABLE bets"))
            conn.execute(text(LEGACY_BETS_TABLE))
            for bet_type, description in LEGACY_BETS:
                conn.execute(
                    text(
                        "INSERT INTO bets (user_id, bet_type, description, amount, odds, potential_win, status, week) "
                        "VALUES ('u1', :bet_type, :description, 10.0, '+100', 10.0, 'pending', 9)"
                    ),
                    {"bet_type": bet_type, "description": description},
                )

        run_schema_migrations()
        run_schema_migrations()

        columns = {column["name"] for column in inspect(db.engine).get_columns("bets")}
        rows = db.session.execute(text("SELECT bet_type, description FROM bets ORDER BY id")).all()

    assert "Migration error" not in caplog.text
    assert {"run_id", "price", "probability"} <= columns
    assert [tuple(row) for row in rows] == [
        ("first_place", "Ammady: #1 Seed +250"),
        ("team_total", "Ammady O/U 101.5: Under"),
        ("make_playoffs", "Samer: Ammad Playoff -150"),
        ("moneyline", "Samer vs Ammad: Samer -143"),
    ]
