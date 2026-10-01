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
from pipeline.steps.odds import ODDS_DDL

FIXTURES = Path(__file__).parent / "fixtures" / "legacy"
REAL_DB_DIR = DATA_DIR / "databases"


def real_2025_originals() -> Path | None:
    # Once migrate-legacy has run on the real databases, the untouched 2025 files live in its backup folder.
    for folder in (REAL_DB_DIR / legacy.BACKUP_DIR_NAME, REAL_DB_DIR):
        if (folder / "projections.db").exists():
            return folder
    return None


LATER_WEEK_3_RUN = "seed_1738_20250918_100000"
WEEK_4_RUN = "seed_1738_20250924_100000"

ODDS_STEP_TABLES = [
    "betting_odds_team_ou",
    "betting_odds_matchup_ou",
    "betting_odds_matchup_ml",
    "betting_odds_highest_scorer",
    "betting_odds_lowest_scorer",
    "team_distribution_curves",
    "team_matchup_margin_curves",
]
# (name, type, notnull, pk) of notebook 09's tables, which keep their autoincrement id and gain a trailing season.
STANDINGS_TEAM_COLUMNS = [
    ("id", "INTEGER", 0, 1),
    ("run_id", "TEXT", 1, 0),
    ("week", "INTEGER", 1, 0),
    ("team_id", "INTEGER", 1, 0),
    ("team_name", "TEXT", 1, 0),
    ("owner", "TEXT", 1, 0),
]
CREATED_AT_AND_SEASON = [("created_at", "TIMESTAMP", 0, 0), ("season", "INTEGER", 1, 0)]
TEAM_PROBABILITY_COLUMNS = [
    *STANDINGS_TEAM_COLUMNS,
    ("probability", "REAL", 1, 0),
    ("american_odds", "TEXT", 1, 0),
    *CREATED_AT_AND_SEASON,
]
LEGACY_STANDINGS_SHAPES = {
    "betting_odds_make_playoffs": (TEAM_PROBABILITY_COLUMNS, ["run_id", "week", "team_id"]),
    "standings_probability_matrix": (
        [
            *STANDINGS_TEAM_COLUMNS,
            ("position", "INTEGER", 1, 0),
            ("probability", "REAL", 1, 0),
            ("count", "INTEGER", 1, 0),
            *CREATED_AT_AND_SEASON,
        ],
        ["run_id", "week", "team_id", "position"],
    ),
}

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


def unique_constraints(settings, table):
    indexes = fetch(settings, "odds", f"PRAGMA index_list({table})")
    return [
        [column["name"] for column in fetch(settings, "odds", f"PRAGMA index_info({index['name']})")]
        for index in indexes
        if index["origin"] == "u"
    ]


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
    assert {(row["season"], row["week"], row["model_version"]) for row in rows.values()} == {(2025, 3, "v1")}


def test_lineups_and_summaries_get_an_integer_season(settings):
    legacy.migrate(settings)

    lineups = fetch(settings, "projections", "SELECT * FROM team_lineups ORDER BY roster_id, slot")
    assert [(row["season"], row["week"], row["roster_id"], row["slot"], row["position"]) for row in lineups] == [
        (2025, 3, 1, "DEF", "DEF"),
        (2025, 3, 1, "QB", "QB"),
        (2025, 3, 1, "WR1", "WR"),
        (2025, 3, 2, "DEF", "DEF"),
        (2025, 3, 2, "QB", "QB"),
        (2025, 3, 2, "WR1", "WR"),
    ]
    summaries = fetch(settings, "projections", "SELECT season, week, roster_id, owner FROM team_projections_summary")
    assert summaries == [{"season": 2025, "week": 3, "roster_id": 1, "owner": "alice"}]


def test_stats_get_the_nfl_team_of_their_player(settings):
    legacy.migrate(settings)

    rows = fetch(settings, "projections", "SELECT sleeper_player_id, team FROM player_week_stats")
    assert {row["sleeper_player_id"]: row["team"] for row in rows} == {
        "7547": "DET",
        "SEA": "SEA",
        "4984": "BUF",
        "5001": None,
        "5002": None,
    }


def test_lineups_find_their_player_through_a_unique_name_in_the_weeks_stats(settings):
    result = legacy.migrate(settings)

    lineups = fetch(
        settings,
        "projections",
        "SELECT roster_id, slot, player_name, sleeper_player_id, nfl_team FROM team_lineups ORDER BY roster_id, slot",
    )
    assert [tuple(row.values()) for row in lineups] == [
        (1, "DEF", "SEA Defense", "SEA", "SEA"),
        (1, "QB", "Waiver Pickup", None, None),
        (1, "WR1", "Amon-Ra St. Brown", "7547", "DET"),
        (2, "DEF", "BUF Defense", None, None),
        (2, "QB", "Josh Allen", "4984", "BUF"),
        (2, "WR1", "Mike Williams", None, None),
    ]
    assert result["actions"]["backfill"] == [
        "player_week_stats 2025: filled team on 3 rows, now set on 3 of 5",
        "team_lineups 2025: filled sleeper_player_id on 3 rows, now set on 3 of 6; waiver pickups 1, unresolved names 2",
        "team_lineups 2025: filled nfl_team on 3 rows, now set on 3 of 6",
    ]


def test_the_backfill_only_fills_2025_nulls(settings):
    legacy.migrate(settings)
    with closing(connect(settings, "projections")) as conn:
        with conn:
            conn.execute("UPDATE player_week_stats SET team = 'DAL' WHERE sleeper_player_id = '7547'")
            conn.execute(
                "INSERT INTO player_week_stats (season, week, sleeper_player_id, player_name, position, mu, sigma, "
                "var, n_sources, spread, model_version, computed_at) VALUES (2026, 1, '4984', 'Josh Allen', 'QB', "
                "22.0, 7.0, 49.0, 1, 0.0, 'v1', '2026-09-09T10:00:00+00:00')"
            )

    legacy.migrate(settings)

    rows = fetch(
        settings,
        "projections",
        "SELECT season, sleeper_player_id, team FROM player_week_stats WHERE sleeper_player_id IN ('4984', '7547') "
        "ORDER BY season, sleeper_player_id",
    )
    assert [tuple(row.values()) for row in rows] == [(2025, "4984", "BUF"), (2025, "7547", "DAL"), (2026, "4984", None)]


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
    assert [(row["player_id"], row["position"]) for row in rows] == [
        ("4984", "QB"),
        ("7547", "WR"),
        ("9999", "RB"),
        ("SEA", "DEF"),
    ]


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

    for table in [*ODDS_STEP_TABLES, *LEGACY_STANDINGS_SHAPES]:
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


def test_weekly_odds_tables_take_the_odds_step_shape(settings):
    legacy.migrate(settings)

    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.row_factory = sqlite3.Row
        scratch.executescript(ODDS_DDL)
        for table in ODDS_STEP_TABLES:
            expected = [dict(row) for row in scratch.execute(f"PRAGMA table_info({table})")]
            assert fetch(settings, "odds", f"PRAGMA table_info({table})") == expected, table
    assert fetch(settings, "odds", "SELECT * FROM betting_odds_highest_scorer") == [
        {
            "run_id": LATER_WEEK_3_RUN,
            "week": 3,
            "season": 2025,
            "team_id": 1,
            "team_name": "Team Alice",
            "owner": "alice",
            "count": 27500,
            "probability": 0.55,
            "odds": "-122",
            "created_at": "2025-09-18 10:04:00",
        }
    ]


def test_standings_tables_keep_their_legacy_shape_and_gain_a_season(settings):
    legacy.migrate(settings)

    for table, (columns, unique) in LEGACY_STANDINGS_SHAPES.items():
        info = fetch(settings, "odds", f"PRAGMA table_info({table})")
        assert [(column["name"], column["type"], column["notnull"], column["pk"]) for column in info] == columns, table
        assert unique_constraints(settings, table) == [unique], table
    assert fetch(settings, "odds", "SELECT id, run_id, probability, season FROM betting_odds_make_playoffs") == [
        {"id": 1, "run_id": "standings_3_20250918_100500", "probability": 0.9, "season": 2025}
    ]


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
        "player_week_stats": 5,
        "projections": 5,
        "projections_with_sleeper": 5,
        "team_lineups": 6,
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
    assert second["actions"]["backfill"] == [
        "player_week_stats 2025: filled team on 0 rows, now set on 3 of 5",
        "team_lineups 2025: filled sleeper_player_id on 0 rows, now set on 3 of 6; waiver pickups 1, unresolved names 2",
        "team_lineups 2025: filled nfl_team on 0 rows, now set on 3 of 6",
    ]


def test_missing_databases_stop_the_migration(tmp_path):
    settings = Settings(season=2025, week=16, league_id="L2025", data_dir=tmp_path)

    with pytest.raises(FileNotFoundError, match="league.db, projections.db, odds.db not found"):
        legacy.migrate(settings)


@pytest.mark.skipif(real_2025_originals() is None, reason="the 2025 databases are not on this machine")
def test_the_real_2025_databases_migrate_without_losing_a_row(tmp_path):
    settings = Settings(season=2025, week=16, league_id="L2025", data_dir=tmp_path)
    settings.db_paths["league"].parent.mkdir()
    for name in legacy.LEGACY_DATABASES:
        shutil.copy(real_2025_originals() / f"{name}.db", settings.db_paths[name])

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
    unresolved = fetch(
        settings,
        "projections",
        "SELECT week, player_name FROM team_lineups WHERE sleeper_player_id IS NULL AND player_name != 'Waiver Pickup'",
    )
    assert unresolved == []

    migrated = file_hashes(settings)
    legacy.migrate(settings)
    assert file_hashes(settings) == migrated
