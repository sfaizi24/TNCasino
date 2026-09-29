import json
import os
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.pool import StaticPool

from app import create_app, windows
from app.database import db as _db
from app.models import BettingPeriod, User
from pipeline.markets import encode_totals

TEST_CONFIG = {
    "TESTING": True,
    "SQLALCHEMY_DATABASE_URI": "sqlite://",
    "SQLALCHEMY_ENGINE_OPTIONS": {
        "poolclass": StaticPool,
        "connect_args": {"check_same_thread": False},
    },
    "WTF_CSRF_ENABLED": False,
    "SECRET_KEY": "test-secret",
    "GOOGLE_OAUTH_CLIENT_ID": "",
    "GOOGLE_OAUTH_CLIENT_SECRET": "",
}


@pytest.fixture(scope="session", autouse=True)
def no_production_database():
    """Importing app loads .env, so anything reading DATABASE_URL would otherwise reach production."""
    os.environ["DATABASE_URL"] = "sqlite://"


@pytest.fixture(scope="session")
def app():
    app = create_app(TEST_CONFIG)
    yield app


@pytest.fixture
def file_backed_app(tmp_path):
    """An app on a SQLite file, where every request gets its own connection, so requests can race."""
    app = create_app(
        {
            **TEST_CONFIG,
            "SQLALCHEMY_DATABASE_URI": f"sqlite:///{(tmp_path / 'race.db').as_posix()}",
            # Writers queue for the lock instead of failing with "database is locked".
            "SQLALCHEMY_ENGINE_OPTIONS": {"connect_args": {"timeout": 30}},
        }
    )
    yield app
    with app.app_context():
        _db.engine.dispose()


@pytest.fixture(autouse=True)
def db_session(app):
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.rollback()
        _db.drop_all()


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def user(db_session):
    u = User(
        id="test-user-1",
        username="testuser",
        email="test@example.com",
        first_name="Test",
        last_name="User",
        account_balance=1000.0,
        total_pnl=0.0,
        is_admin=False,
    )
    db_session.session.add(u)
    db_session.session.commit()
    return u


@pytest.fixture
def admin_user(db_session):
    u = User(
        id="admin-user-1",
        username="adminuser",
        email="admin@example.com",
        first_name="Admin",
        last_name="User",
        account_balance=1000.0,
        total_pnl=0.0,
        is_admin=True,
    )
    db_session.session.add(u)
    db_session.session.commit()
    return u


@pytest.fixture
def logged_in_client(client, user):
    with client.session_transaction() as sess:
        sess["_user_id"] = user.id
    return client


@pytest.fixture
def admin_client(client, admin_user):
    with client.session_transaction() as sess:
        sess["_user_id"] = admin_user.id
    return client


@pytest.fixture
def captured_templates(app):
    recorded = []

    def record(sender, template, context, **extra):
        recorded.append((template, context))

    from flask import template_rendered

    template_rendered.connect(record, app)
    yield recorded
    template_rendered.disconnect(record, app)


@pytest.fixture
def betting_period(db_session):
    period = BettingPeriod(
        week=10,
        lock_time=datetime.now(UTC) + timedelta(days=7),
        is_locked=False,
        is_settled=False,
    )
    db_session.session.add(period)
    db_session.session.commit()
    return period


ANALYTICS_TABLES = [
    "betting_odds_matchup_ml",
    "betting_odds_team_ou",
    "betting_odds_highest_scorer",
    "betting_odds_lowest_scorer",
    "betting_odds_first_place",
    "betting_odds_make_playoffs",
    "team_lineups",
    "team_distribution_curves",
    "team_matchup_margin_curves",
    "sleeper_rosters",
    "sleeper_users",
    "sleeper_matchups",
    "projections_rosters",
    "simulation_runs",
    "simulation_totals",
]

# The pipeline run that published every seeded odds row, and the window it opened.
RUN_ID = "2026w10-20261110T140000"
RUN_CREATED_AT = "2026-11-10T14:00:00+00:00"
WINDOW_CLOSES_AT = "2026-11-13T00:15:00+00:00"

# What the app takes as now when it asks whether the seeded run's window is open: an hour after the run.
WINDOW_NOW = datetime(2026, 11, 10, 15, tzinfo=UTC)

# The seeded run's score matrix: 20 sims, one row each, with columns for rosters 1 and 2. Roster 1
# outscores roster 2 in 11 sims, ties in one and loses 8; against its 110.5 line it is over in 9 sims,
# on it in one and under in 10.
SEEDED_TOTALS = np.array(
    [
        [130.0, 100.0],
        [128.0, 105.0],
        [125.0, 110.0],
        [122.0, 95.0],
        [120.0, 115.0],
        [118.0, 90.0],
        [116.0, 112.0],
        [114.0, 140.0],
        [112.0, 135.0],
        [110.5, 101.0],
        [108.0, 99.0],
        [106.0, 97.0],
        [104.0, 104.0],
        [102.0, 120.0],
        [100.0, 111.0],
        [98.0, 103.0],
        [96.0, 125.0],
        [94.0, 107.0],
        [92.0, 88.0],
        [90.0, 118.0],
    ]
)


@pytest.fixture(autouse=True)
def window_clock(monkeypatch):
    monkeypatch.setattr(windows, "utc_now", lambda: WINDOW_NOW)


def create_analytics_tables(session):
    """Create analytics tables that mirror what publish.py pushes to PostgreSQL."""
    for table in ANALYTICS_TABLES:
        session.execute(text(f"DROP TABLE IF EXISTS {table}"))

    session.execute(
        text("""
        CREATE TABLE betting_odds_matchup_ml (
            run_id TEXT, week INTEGER, season INTEGER, matchup TEXT,
            team1_id INTEGER, team1_name TEXT, team1_win_prob REAL, team1_ml TEXT,
            team2_id INTEGER, team2_name TEXT, team2_win_prob REAL, team2_ml TEXT,
            ties INTEGER, created_at TIMESTAMP
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE betting_odds_team_ou (
            run_id TEXT, week INTEGER, season INTEGER, team_id INTEGER, team_name TEXT, owner TEXT,
            line REAL, over_prob REAL, over_odds TEXT, under_prob REAL, under_odds TEXT,
            push_count INTEGER, created_at TIMESTAMP
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE betting_odds_highest_scorer (
            run_id TEXT, week INTEGER, season INTEGER, team_id INTEGER, team_name TEXT, owner TEXT,
            count INTEGER, probability REAL, odds TEXT, created_at TIMESTAMP
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE betting_odds_lowest_scorer (
            run_id TEXT, week INTEGER, season INTEGER, team_id INTEGER, team_name TEXT, owner TEXT,
            count INTEGER, probability REAL, odds TEXT, created_at TIMESTAMP
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE betting_odds_first_place (
            id INTEGER PRIMARY KEY, run_id TEXT, week INTEGER, season INTEGER,
            team_id INTEGER, team_name TEXT, owner TEXT,
            probability REAL, american_odds TEXT, created_at TIMESTAMP
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE betting_odds_make_playoffs (
            id INTEGER PRIMARY KEY, run_id TEXT, week INTEGER, season INTEGER,
            team_id INTEGER, team_name TEXT, owner TEXT,
            probability REAL, american_odds TEXT, created_at TIMESTAMP
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE team_lineups (
            roster_id INTEGER, team_name TEXT, owner TEXT, record TEXT,
            slot TEXT, player_name TEXT, position TEXT,
            mu REAL, sigma REAL, var REAL, n_sources INTEGER,
            is_replacement INTEGER, week INTEGER, season TEXT, timestamp TEXT
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE team_distribution_curves (
            week INTEGER, owner TEXT,
            x_values TEXT, density_values TEXT, cdf_values TEXT,
            mean REAL, p10 REAL, p50 REAL, p90 REAL,
            n_sims INTEGER, created_at TIMESTAMP,
            PRIMARY KEY (week, owner)
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE team_matchup_margin_curves (
            week INTEGER, team_owner TEXT, opponent_owner TEXT,
            team_win_prob REAL, opponent_win_prob REAL, tie_prob REAL,
            left_x_values TEXT, left_y_values TEXT,
            right_x_values TEXT, right_y_values TEXT,
            created_at TIMESTAMP,
            PRIMARY KEY (week, team_owner, opponent_owner)
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE sleeper_rosters (
            roster_id INTEGER, league_id TEXT, owner_id TEXT,
            co_owners TEXT, team_name TEXT, starters TEXT, players TEXT,
            reserve TEXT, taxi TEXT, settings TEXT, metadata TEXT,
            wins INTEGER, losses INTEGER, ties INTEGER,
            fpts REAL, fpts_against REAL, fpts_decimal REAL, fpts_against_decimal REAL,
            total_moves INTEGER, waiver_position INTEGER, waiver_budget_used INTEGER,
            created_at TIMESTAMP, updated_at TIMESTAMP
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE sleeper_users (
            user_id TEXT, username TEXT, display_name TEXT,
            avatar TEXT, metadata TEXT, created_at TIMESTAMP, updated_at TIMESTAMP
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE sleeper_matchups (
            matchup_id TEXT, league_id TEXT, week INTEGER, roster_id INTEGER,
            matchup_id_number INTEGER, starters TEXT, players TEXT,
            points REAL, custom_points REAL, players_points TEXT,
            created_at TIMESTAMP, updated_at TIMESTAMP
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE projections_rosters (
            roster_id INTEGER, team_name TEXT, sleeper_player_id TEXT,
            first_name TEXT, last_name TEXT, position TEXT, nfl_team TEXT,
            week INTEGER, season TEXT, mu REAL, var REAL,
            starting_status INTEGER, timestamp TEXT
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE simulation_runs (
            run_id TEXT PRIMARY KEY, season INTEGER, week INTEGER, seed INTEGER, n_sims INTEGER,
            model_version TEXT, n_teams INTEGER, draws_path TEXT, created_at TEXT,
            n_locked INTEGER, window_closes_at TEXT, standings_through_week INTEGER
        )
    """)
    )
    session.execute(
        text("""
        CREATE TABLE simulation_totals (
            run_id TEXT PRIMARY KEY, season INTEGER, week INTEGER, created_at TEXT,
            n_sims INTEGER, roster_ids TEXT, totals BLOB
        )
    """)
    )
    session.commit()


def seed_analytics(session):
    """Seed analytics tables with test data for week 10."""
    session.execute(
        text("""
        INSERT INTO sleeper_users (user_id, username, display_name)
        VALUES ('u1', 'alice', 'Alice A'),
               ('u2', 'bob', 'Bob B'),
               ('u3', 'old-alice', 'Alice A')
    """)
    )
    session.execute(
        text("""
        INSERT INTO sleeper_rosters (roster_id, league_id, owner_id)
        VALUES (1, 'league1', 'u1'),
               (2, 'league1', 'u2'),
               (99, 'old-league', 'u1'),
               (100, 'old-league', 'u3')
    """)
    )
    session.execute(
        text("""
        INSERT INTO sleeper_matchups (league_id, week, roster_id, matchup_id_number)
        VALUES ('league1', 10, 1, 1), ('league1', 10, 2, 1)
    """)
    )
    session.execute(
        text("""
        INSERT INTO simulation_runs
            (run_id, season, week, seed, n_sims, model_version, n_teams, draws_path, created_at,
             n_locked, window_closes_at, standings_through_week)
        VALUES (:run_id, 2026, 10, 1738, 50000, 'v2', 12, 'draws/2026w10.npy', :created_at, 0, :closes_at, 9)
    """),
        {"run_id": RUN_ID, "created_at": RUN_CREATED_AT, "closes_at": WINDOW_CLOSES_AT},
    )
    session.execute(
        text("""
        INSERT INTO simulation_totals (run_id, season, week, created_at, n_sims, roster_ids, totals)
        VALUES (:run_id, 2026, 10, :created_at, 20, '1,2', :totals)
    """),
        {"run_id": RUN_ID, "created_at": RUN_CREATED_AT, "totals": encode_totals(SEEDED_TOTALS)},
    )
    session.execute(
        text("""
        INSERT INTO betting_odds_matchup_ml
            (run_id, week, season, matchup, team1_id, team1_name, team1_win_prob, team1_ml,
             team2_id, team2_name, team2_win_prob, team2_ml, ties)
        VALUES (:run_id, 10, 2026, 'Matchup 1', 1, 'Team1', 0.6, '-150', 2, 'Team2', 0.4, '+130', 0)
    """),
        {"run_id": RUN_ID},
    )
    session.execute(
        text("""
        INSERT INTO betting_odds_team_ou
            (run_id, week, season, team_id, team_name, owner, line, over_prob, over_odds, under_prob, under_odds)
        VALUES (:run_id, 10, 2026, 1, 'Team1', 'Alice A', 110.5, 0.55, '-120', 0.45, '+100'),
               (:run_id, 10, 2026, 2, 'Team2', 'Bob B', 95.0, 0.48, '+105', 0.52, '-125')
    """),
        {"run_id": RUN_ID},
    )
    session.execute(
        text("""
        INSERT INTO betting_odds_highest_scorer (run_id, week, season, team_id, owner, probability, odds)
        VALUES (:run_id, 10, 2026, 1, 'Alice A', 0.35, '+185'), (:run_id, 10, 2026, 2, 'Bob B', 0.25, '+300')
    """),
        {"run_id": RUN_ID},
    )
    session.execute(
        text("""
        INSERT INTO betting_odds_lowest_scorer (run_id, week, season, team_id, owner, probability, odds)
        VALUES (:run_id, 10, 2026, 1, 'Alice A', 0.20, '+400'), (:run_id, 10, 2026, 2, 'Bob B', 0.30, '+230')
    """),
        {"run_id": RUN_ID},
    )
    session.execute(
        text("""
        INSERT INTO betting_odds_first_place (run_id, week, season, team_id, owner, probability, american_odds)
        VALUES (:run_id, 10, 2026, 1, 'Alice A', 0.45, '-120'), (:run_id, 10, 2026, 2, 'Bob B', 0.30, '+150')
    """),
        {"run_id": RUN_ID},
    )
    session.execute(
        text("""
        INSERT INTO betting_odds_make_playoffs (run_id, week, season, team_id, owner, probability, american_odds)
        VALUES (:run_id, 10, 2026, 1, 'Alice A', 0.80, '-400'), (:run_id, 10, 2026, 2, 'Bob B', 0.60, '-150')
    """),
        {"run_id": RUN_ID},
    )
    session.execute(
        text("""
        INSERT INTO team_lineups (roster_id, owner, week, slot, player_name, position, mu, var)
        VALUES (1, 'Alice A', 10, 'QB', 'Patrick Mahomes', 'QB', 22.5, 7.0),
               (1, 'Alice A', 10, 'RB1', 'Derrick Henry', 'RB', 15.0, 8.0),
               (1, 'Alice A', 10, 'WR1', 'Tyreek Hill', 'WR', 18.0, 9.0),
               (2, 'Bob B', 10, 'QB', 'Josh Allen', 'QB', 21.0, 6.5)
    """)
    )
    session.execute(
        text("""
        INSERT INTO projections_rosters (roster_id, first_name, last_name, position, week, mu, var, starting_status)
        VALUES (1, 'Patrick', 'Mahomes', 'QB', 10, 22.5, 7.0, 1),
               (1, 'Bench', 'Player', 'WR', 10, 5.0, 3.0, 0),
               (99, 'Wrong', 'League', 'QB', 10, 99.0, 99.0, 1)
    """)
    )

    x_vals = json.dumps([90.0, 100.0, 110.0, 120.0])
    density_a = json.dumps([0.010, 0.020, 0.025, 0.015])
    cdf_a = json.dumps([0.10, 0.40, 0.85, 1.00])
    density_b = json.dumps([0.020, 0.025, 0.015, 0.010])
    cdf_b = json.dumps([0.20, 0.65, 0.90, 1.00])
    left_x = json.dumps([-40.0, -20.0, 0.0])
    left_y = json.dumps([0.05, 0.20, 0.40])
    right_x = json.dumps([0.0, 20.0, 40.0])
    right_y = json.dumps([0.60, 0.20, 0.05])

    session.execute(
        text("""
        INSERT INTO team_distribution_curves
            (week, owner, x_values, density_values, cdf_values, mean, p10, p50, p90, n_sims)
        VALUES (10, 'alice', :x, :da, :ca, 110.0, 90.0, 105.0, 130.0, 50000),
               (10, 'bob', :x, :db, :cb, 95.0, 80.0, 95.0, 115.0, 50000)
    """),
        {"x": x_vals, "da": density_a, "ca": cdf_a, "db": density_b, "cb": cdf_b},
    )
    session.execute(
        text("""
        INSERT INTO team_matchup_margin_curves
            (week, team_owner, opponent_owner, team_win_prob, opponent_win_prob, tie_prob,
             left_x_values, left_y_values, right_x_values, right_y_values)
        VALUES (10, 'alice', 'bob', 0.60, 0.40, 0.00, :lx, :ly, :rx, :ry),
               (10, 'bob', 'alice', 0.40, 0.60, 0.00, :lx, :ly, :rx, :ry)
    """),
        {"lx": left_x, "ly": left_y, "rx": right_x, "ry": right_y},
    )
    session.commit()


def set_points(session, points):
    """Publish week 10 scores by roster id, the way the league step records them once games are played."""
    for roster_id, score in points.items():
        session.execute(
            text("UPDATE sleeper_matchups SET points = :points WHERE week = 10 AND roster_id = :roster_id"),
            {"points": score, "roster_id": roster_id},
        )
    session.commit()


@pytest.fixture
def analytics_tables(db_session):
    create_analytics_tables(db_session.session)


@pytest.fixture
def seeded_analytics(analytics_tables, db_session):
    seed_analytics(db_session.session)


@pytest.fixture
def pipeline_tables(db_session):
    """Create the pipeline run tables; week 4 has a run that failed at playoffs and a rerun of simulate and odds."""
    db_session.session.execute(
        text("""
        CREATE TABLE pipeline_runs (
            run_id TEXT PRIMARY KEY,
            season INTEGER NOT NULL, week INTEGER NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
            steps TEXT NOT NULL,
            git_sha TEXT, error TEXT
        )
    """)
    )
    db_session.session.execute(
        text("""
        CREATE TABLE pipeline_steps (
            run_id TEXT NOT NULL, step TEXT NOT NULL,
            started_at TEXT NOT NULL, finished_at TEXT, duration_s REAL,
            status TEXT NOT NULL,
            summary TEXT, warnings TEXT, charts TEXT, error TEXT,
            PRIMARY KEY (run_id, step)
        )
    """)
    )
    db_session.session.execute(
        text("""
        CREATE TABLE source_reviews (
            season INTEGER NOT NULL, week INTEGER NOT NULL, source TEXT NOT NULL,
            verdict TEXT NOT NULL,
            note TEXT, reviewed_at TEXT NOT NULL,
            PRIMARY KEY (season, week, source)
        )
    """)
    )

    first_run = "2026w04-20260929T140000"
    rerun = "2026w04-20260929T160000"
    db_session.session.execute(
        text("""
        INSERT INTO pipeline_runs (run_id, season, week, started_at, finished_at, status, steps, git_sha, error)
        VALUES (:first, 2026, 4, '2026-09-29T14:00:00+00:00', '2026-09-29T14:06:10+00:00', 'failed',
                :first_steps, 'abc1234', 'step playoffs failed'),
               (:rerun, 2026, 4, '2026-09-29T16:00:00+00:00', '2026-09-29T16:00:40+00:00', 'ok',
                :rerun_steps, 'abc1234', NULL)
    """),
        {
            "first": first_run,
            "rerun": rerun,
            "first_steps": json.dumps(["league", "scrape", "calibrate", "simulate", "odds", "playoffs"]),
            "rerun_steps": json.dumps(["simulate", "odds"]),
        },
    )

    scrape_summary = {
        "sources": [
            {
                "source": "sleeper.com",
                "status": "ok",
                "n_rows": 412,
                "elapsed_s": 1.2,
                "checks": [{"name": "position_counts", "status": "ok", "detail": "all positions in range"}],
            },
            {
                "source": "espn.com",
                "status": "fail",
                "n_rows": 380,
                "elapsed_s": 3.4,
                "checks": [
                    {"name": "position_agreement", "status": "fail", "detail": "3.1% of matched rows disagree"},
                ],
            },
        ],
        "dropped": ["espn.com"],
    }
    db_session.session.execute(
        text("""
        INSERT INTO pipeline_steps
            (run_id, step, started_at, finished_at, duration_s, status, summary, warnings, charts, error)
        VALUES
            (:first, 'league', '2026-09-29T14:00:00+00:00', '2026-09-29T14:00:20+00:00', 20.0, 'ok',
             :league, '[]', '[]', NULL),
            (:first, 'scrape', '2026-09-29T14:00:20+00:00', '2026-09-29T14:03:10+00:00', 170.0, 'warn',
             :scrape, :scrape_warnings, '[]', NULL),
            (:first, 'calibrate', '2026-09-29T14:03:10+00:00', '2026-09-29T14:04:00+00:00', 50.0, 'ok',
             :calibrate, '[]', :calibrate_charts, NULL),
            (:first, 'simulate', '2026-09-29T14:04:00+00:00', '2026-09-29T14:05:30+00:00', 90.0, 'ok',
             :first_simulate, '[]', '[]', NULL),
            (:first, 'odds', '2026-09-29T14:05:30+00:00', '2026-09-29T14:05:40+00:00', 10.0, 'ok',
             :odds, '[]', '[]', NULL),
            (:first, 'playoffs', '2026-09-29T14:05:40+00:00', '2026-09-29T14:06:10+00:00', 30.0, 'failed',
             NULL, NULL, NULL, :playoffs_error),
            (:rerun, 'simulate', '2026-09-29T16:00:00+00:00', '2026-09-29T16:00:30+00:00', 30.0, 'ok',
             :rerun_simulate, '[]', '[]', NULL),
            (:rerun, 'odds', '2026-09-29T16:00:30+00:00', '2026-09-29T16:00:40+00:00', 10.0, 'ok',
             :odds, '[]', '[]', NULL)
    """),
        {
            "first": first_run,
            "rerun": rerun,
            "league": json.dumps({"n_users": 12, "n_rosters": 12, "byes_this_week": ["DET", "LV"]}),
            "scrape": json.dumps(scrape_summary),
            "scrape_warnings": json.dumps(["espn.com failed verification and was dropped"]),
            "calibrate": json.dumps({"model_version": "v2", "team_coverage_80": 0.8125}),
            "calibrate_charts": json.dumps(["calibration_week_4.png"]),
            "first_simulate": json.dumps({"n_sims": 50000, "seed": 1738}),
            "rerun_simulate": json.dumps({"n_sims": 50000, "seed": 1739}),
            "odds": json.dumps({"n_matchups": 6}),
            "playoffs_error": "Traceback (most recent call last):\n  ...\nKeyError: 'playoff_week_start'",
        },
    )
    db_session.session.execute(
        text("""
        INSERT INTO source_reviews (season, week, source, verdict, note, reviewed_at)
        VALUES (2026, 4, 'sleeper.com', 'ok', 'top 15 look right', '2026-09-29T14:10:00+00:00')
    """)
    )
    db_session.session.commit()

    yield

    # db_session only drops ORM tables, and the in-memory database outlives each test.
    for table in ("pipeline_runs", "pipeline_steps", "source_reviews"):
        db_session.session.execute(text(f"DROP TABLE {table}"))
    db_session.session.commit()
