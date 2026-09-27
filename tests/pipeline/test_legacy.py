import hashlib
import json
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pipeline import legacy
from pipeline.db import connect
from pipeline.settings import DATA_DIR, Settings

FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
REAL_DB_DIR = DATA_DIR / "databases"

LATER_WEEK_3_RUN = "seed_1738_20250918_100000"
WEEK_4_RUN = "seed_1738_20250924_100000"

REBUILT_TABLES = [
    ("projections", "projections"),
    ("projections", "projections_with_sleeper"),
    ("projections", "player_week_stats"),
    ("projections", "team_lineups"),
    ("projections", "team_projections_summary"),
    ("league", "projections_rosters"),
    ("league", "nfl_schedules"),
]
POSITION_TABLES = [
    ("projections", "projections"),
    ("projections", "projections_with_sleeper"),
    ("projections", "player_week_stats"),
    ("projections", "team_lineups"),
    ("league", "projections_rosters"),
    ("league", "nfl_players"),
]
REAL_STRAY_TABLES = {
    "betting_odds_highest_scorer",
    "betting_odds_lowest_scorer",
    "betting_odds_matchup_ml",
    "betting_odds_matchup_ou",
    "betting_odds_team_ou",
    "player_stats",
}


@pytest.fixture
def settings(tmp_path):
    """league.db, projections.db and odds.db built from the SQL fixtures, as the 2025 notebooks left them."""
    settings = Settings(season=2025, week=16, league_id="L2025", data_dir=tmp_path)
    for name in legacy.LEGACY_DATABASES:
        with closing(connect(settings, name)) as conn:
            conn.executescript((FIXTURES / f"{name}.sql").read_text())
    return settings


def fetch(settings, name, sql):
    with closing(connect(settings, name)) as conn:
        return [dict(row) for row in conn.execute(sql)]


def primary_key(settings, table):
    columns = fetch(settings, "odds", f"PRAGMA table_info({table})")
    return [column["name"] for column in sorted(columns, key=lambda column: column["pk"]) if column["pk"]]


def file_hashes(settings):
    return {name: hashlib.sha256(settings.db_paths[name].read_bytes()).hexdigest() for name in legacy.LEGACY_DATABASES}


def test_projections_get_a_season_integer_weeks_and_def(settings):
    legacy.migrate(settings)

    rows = fetch(settings, "projections", "SELECT * FROM projections ORDER BY id")
    assert [(row["id"], row["season"], row["week"], row["position"]) for row in rows] == [
        (11, 2025, 3, "WR"),
        (12, 2025, 3, "WR"),
        (13, 2025, 3, "WR"),
        (14, 2025, 3, "DEF"),
        (15, 2025, 12, "RB"),
    ]
    assert rows[3] == {
        "id": 14,
        "source_website": "fantasypros.com",
        "season": 2025,
        "week": 3,
        "player_first_name": "Seattle Seahawks",
        "player_last_name": "Defense",
        "position": "DEF",
        "team": "SEA",
        "projected_points": 8.2,
        "external_id": None,
        "created_at": "2025-09-17 10:00:00",
    }


def test_matched_projections_keep_their_player_and_method(settings):
    legacy.migrate(settings)

    rows = fetch(settings, "projections", "SELECT * FROM projections_with_sleeper ORDER BY id")
    assert [
        (row["id"], row["week"], row["position"], row["sleeper_player_id"], row["match_method"]) for row in rows
    ] == [
        (21, 3, "WR", "7547", "automatic"),
        (22, 3, "WR", "7547", "automatic"),
        (23, 3, "WR", "7547", "automatic"),
        (24, 3, "DEF", "SEA", "dst_team_match"),
        (25, 12, "RB", None, None),
    ]
    assert {(row["season"], row["external_id"]) for row in rows} == {(2025, None)}


def test_player_week_stats_recover_the_spread_behind_sigma(settings):
    legacy.migrate(settings)

    rows = {row["sleeper_player_id"]: row for row in fetch(settings, "projections", "SELECT * FROM player_week_stats")}
    assert rows["7547"]["spread"] == pytest.approx(3.0)
    assert (rows["7547"]["mu"], rows["7547"]["sigma"]) == (18.0, 11.661903789690601)
    assert (rows["SEA"]["spread"], rows["SEA"]["position"]) == (0.0, "DEF")
    assert {(row["season"], row["week"], row["team"], row["model_version"]) for row in rows.values()} == {
        (2025, 3, None, "v1")
    }


def test_lineups_and_summaries_get_an_integer_season(settings):
    legacy.migrate(settings)

    lineups = fetch(settings, "projections", "SELECT * FROM team_lineups ORDER BY slot")
    assert [(row["season"], row["week"], row["roster_id"], row["slot"], row["position"]) for row in lineups] == [
        (2025, 3, 1, "DEF", "DEF"),
        (2025, 3, 1, "WR1", "WR"),
    ]
    assert {(row["sleeper_player_id"], row["nfl_team"]) for row in lineups} == {(None, None)}
    summaries = fetch(settings, "projections", "SELECT season, week, roster_id, owner FROM team_projections_summary")
    assert summaries == [{"season": 2025, "week": 3, "roster_id": 1, "owner": "alice"}]


def test_projections_rosters_get_a_roster_status(settings):
    legacy.migrate(settings)

    rows = fetch(settings, "league", "SELECT * FROM projections_rosters ORDER BY sleeper_player_id")
    assert [
        (row["sleeper_player_id"], row["position"], row["roster_status"], row["mu"], row["var"]) for row in rows
    ] == [
        ("4984", "QB", "bench", 21.0, 49.0),
        ("7547", "WR", "starter", 18.0, 136.0),
        ("9999", "RB", "unprojected", 0.0, 0.0),
        ("SEA", "DEF", "starter", 8.2, 49.0),
    ]
    assert {row["season"] for row in rows} == {2025}


def test_schedules_are_rebuilt_with_an_integer_season_and_no_status(settings, monkeypatch):
    monkeypatch.setattr(legacy, "utc_now", lambda: datetime(2026, 9, 1, 12, 0, tzinfo=UTC))

    legacy.migrate(settings)

    common = {"season": 2025, "team": "SEA", "is_home": 0, "status": None, "updated_at": "2026-09-01T12:00:00+00:00"}
    assert fetch(settings, "league", "SELECT * FROM nfl_schedules ORDER BY week") == [
        {**common, "week": 3, "opponent": "ARI", "is_bye": 0, "game_date": "2025-09-25"},
        {**common, "week": 8, "opponent": None, "is_bye": 1, "game_date": None},
    ]


def test_defenses_become_def_in_nfl_players(settings):
    legacy.migrate(settings)

    rows = fetch(settings, "league", "SELECT player_id, position FROM nfl_players ORDER BY player_id")
    assert [(row["player_id"], row["position"]) for row in rows] == [("7547", "WR"), ("9999", "RB"), ("SEA", "DEF")]


def test_sleeper_reprs_become_json(settings):
    legacy.migrate(settings)

    league = fetch(settings, "league", "SELECT roster_positions, settings FROM leagues")[0]
    assert json.loads(league["roster_positions"]) == ["QB", "WR", "DEF", "BN"]
    assert json.loads(league["settings"])["playoff_week_start"] == 15
    assert fetch(
        settings, "league", "SELECT starters, co_owners, reserve, metadata FROM rosters ORDER BY roster_id"
    ) == [
        {
            "starters": '["7547", "SEA"]',
            "co_owners": "null",
            "reserve": "null",
            "metadata": '{"record": "WW", "streak": "2W"}',
        },
        {"starters": '["4984"]', "co_owners": "null", "reserve": "[]", "metadata": None},
    ]
    assert fetch(settings, "league", "SELECT fantasy_positions, metadata FROM nfl_players WHERE player_id = 'SEA'") == [
        {"fantasy_positions": '["DEF"]', "metadata": "null"}
    ]


def test_odds_tables_get_a_season_and_curves_the_run_of_their_week(settings):
    legacy.migrate(settings)

    for table in [
        "betting_odds_team_ou",
        "betting_odds_matchup_ml",
        "betting_odds_first_place",
        "standings_probability_matrix",
    ]:
        assert fetch(settings, "odds", f"SELECT DISTINCT season FROM {table}") == [{"season": 2025}], table
    assert fetch(
        settings, "odds", "SELECT run_id, week, season, owner FROM team_distribution_curves ORDER BY week"
    ) == [
        {"run_id": LATER_WEEK_3_RUN, "week": 3, "season": 2025, "owner": "alice"},
        {"run_id": LATER_WEEK_3_RUN, "week": 3, "season": 2025, "owner": "bob"},
        {"run_id": WEEK_4_RUN, "week": 4, "season": 2025, "owner": "alice"},
    ]
    assert fetch(settings, "odds", "SELECT run_id, week, season FROM team_matchup_margin_curves") == [
        {"run_id": LATER_WEEK_3_RUN, "week": 3, "season": 2025}
    ]
    assert primary_key(settings, "team_distribution_curves") == ["run_id", "week", "owner"]
    assert primary_key(settings, "team_matchup_margin_curves") == ["run_id", "week", "team_owner", "opponent_owner"]


def test_originals_are_backed_up_and_stray_tables_dropped(settings):
    result = legacy.migrate(settings)

    backup_dir = settings.data_dir / "databases" / "backup-2025"
    assert sorted(path.name for path in backup_dir.iterdir()) == ["league.db", "odds.db", "projections.db"]
    with closing(sqlite3.connect(backup_dir / "projections.db")) as backup:
        assert backup.execute("SELECT DISTINCT week FROM projections ORDER BY week").fetchall() == [
            ("Week 12",),
            ("Week 3",),
        ]
    before, after = result["row_counts"]["before"], result["row_counts"]["after"]
    kept = {
        "player_week_stats": 2,
        "projections": 5,
        "projections_with_sleeper": 5,
        "team_lineups": 2,
        "team_projections_summary": 1,
    }
    assert before["projections.db"] == {**kept, "betting_odds_team_ou": 1, "player_stats": 0}
    assert after["projections.db"] == kept
    assert (after["league.db"], after["odds.db"]) == (before["league.db"], before["odds.db"])


def test_a_second_run_changes_nothing(settings):
    legacy.migrate(settings)
    migrated = file_hashes(settings)

    second = legacy.migrate(settings)

    assert file_hashes(settings) == migrated
    assert second["actions"]["backup"] == ["backup-2025 already holds a copy of every database"]
    assert second["actions"]["projections.db"] == [
        "projections: already migrated",
        "projections_with_sleeper: already migrated",
        "player_week_stats: already migrated",
        "team_lineups: already migrated",
        "team_projections_summary: already migrated",
        "no stray tables",
    ]


def test_missing_databases_stop_the_migration(tmp_path):
    settings = Settings(season=2025, week=16, league_id="L2025", data_dir=tmp_path)

    with pytest.raises(FileNotFoundError, match="league.db, projections.db, odds.db not found"):
        legacy.migrate(settings)


@pytest.mark.skipif(not (REAL_DB_DIR / "projections.db").exists(), reason="the 2025 databases are not on this machine")
def test_the_real_2025_databases_migrate_without_losing_a_row(tmp_path):
    settings = Settings(season=2025, week=16, league_id="L2025", data_dir=tmp_path)
    settings.db_paths["league"].parent.mkdir()
    for name in legacy.LEGACY_DATABASES:
        shutil.copy(REAL_DB_DIR / f"{name}.db", settings.db_paths[name])

    result = legacy.migrate(settings)

    before, after = result["row_counts"]["before"], result["row_counts"]["after"]
    assert set(before["projections.db"]) - set(after["projections.db"]) == REAL_STRAY_TABLES
    for database, counts in after.items():
        assert counts == {table: before[database][table] for table in counts}, database
    for name, table in REBUILT_TABLES:
        types = fetch(settings, name, f"SELECT DISTINCT typeof(season) AS season, typeof(week) AS week FROM {table}")
        assert types == [{"season": "integer", "week": "integer"}], table
    for name, table in POSITION_TABLES:
        assert fetch(settings, name, f"SELECT position FROM {table} WHERE position = 'DST'") == [], table
    for table, columns in legacy.REPR_COLUMNS.items():
        for column in columns:
            invalid = fetch(settings, "league", f"SELECT {column} FROM {table} WHERE NOT json_valid({column})")
            assert invalid == [], f"{table}.{column}"

    migrated = file_hashes(settings)
    legacy.migrate(settings)
    assert file_hashes(settings) == migrated
