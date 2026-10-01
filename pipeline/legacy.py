"""One-off migration of the 2025 notebook databases to the pipeline's schema.

The notebooks kept weeks as "Week N" strings, seasons as text or not at all, defenses as DST and
Sleeper's nested fields as Python reprs. migrate() copies league.db, projections.db and odds.db to
backup-2025/ and then rewrites them in place, so 2025 reads like any pipeline season. A table
already in the new form (an INTEGER season column) is skipped, and the closing back-fill of NFL
teams and lineup player ids only fills NULLs, so a second run changes nothing.
montecarlo.db is left alone; the pipeline no longer reads it.
"""

import ast
import json
import math
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path

from pipeline.db import connect
from pipeline.runner import print_table, timestamp, utc_now
from pipeline.settings import Settings
from pipeline.steps.clean import PROJECTIONS_DDL
from pipeline.steps.match import PROJECTIONS_WITH_SLEEPER_DDL
from pipeline.steps.odds import ODDS_DDL
from pipeline.steps.stats import PLAYER_WEEK_STATS_DDL

LEGACY_SEASON = 2025
LEGACY_MODEL_VERSION = "v1"
LEGACY_DATABASES = ["league", "projections", "odds"]
BACKUP_DIR_NAME = "backup-2025"
WAIVER_PICKUP = "Waiver Pickup"

# Notebook 07's markets, rebuilt so season sits where ODDS_DDL puts it.
WEEKLY_ODDS_TABLES = [
    "betting_odds_team_ou",
    "betting_odds_matchup_ou",
    "betting_odds_matchup_ml",
    "betting_odds_highest_scorer",
    "betting_odds_lowest_scorer",
]
# Notebook 09's tables keep their legacy shape, autoincrement id and all, and gain a trailing season.
STANDINGS_TABLES = ["betting_odds_make_playoffs", "standings_probability_matrix"]

# league.db columns the notebooks wrote with str(); the league step writes them with json.dumps.
REPR_COLUMNS = {
    "leagues": ["roster_positions", "scoring_settings", "settings"],
    "users": ["metadata"],
    "rosters": ["co_owners", "starters", "players", "reserve", "taxi", "settings", "metadata"],
    "matchups": ["starters", "players", "players_points"],
    "transactions": [
        "roster_ids",
        "settings",
        "metadata",
        "adds",
        "drops",
        "draft_picks",
        "waiver_budget",
        "consenter_ids",
    ],
    "nfl_players": ["fantasy_positions", "metadata"],
}

TEAM_LINEUPS_DDL = """
CREATE TABLE IF NOT EXISTS team_lineups (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  roster_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  record TEXT NOT NULL,
  slot TEXT NOT NULL,
  sleeper_player_id TEXT,
  player_name TEXT,
  position TEXT NOT NULL,
  nfl_team TEXT,
  mu REAL NOT NULL,
  sigma REAL NOT NULL,
  var REAL NOT NULL,
  n_sources INTEGER NOT NULL,
  is_replacement INTEGER NOT NULL DEFAULT 0,
  timestamp TEXT NOT NULL,
  PRIMARY KEY (season, week, roster_id, slot)
);
"""

TEAM_PROJECTIONS_SUMMARY_DDL = """
CREATE TABLE IF NOT EXISTS team_projections_summary (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  roster_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  record TEXT NOT NULL,
  total_mu REAL NOT NULL,
  combined_sigma REAL NOT NULL,
  total_var REAL NOT NULL,
  waiver_pickups INTEGER NOT NULL,
  timestamp TEXT NOT NULL,
  PRIMARY KEY (season, week, roster_id)
);
"""

PROJECTIONS_ROSTERS_DDL = """
CREATE TABLE IF NOT EXISTS projections_rosters (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  roster_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  sleeper_player_id TEXT NOT NULL,
  first_name TEXT,
  last_name TEXT,
  position TEXT,
  nfl_team TEXT,
  mu REAL NOT NULL,
  var REAL NOT NULL,
  starting_status INTEGER NOT NULL,
  roster_status TEXT NOT NULL,
  timestamp TEXT NOT NULL,
  PRIMARY KEY (season, week, sleeper_player_id)
);
"""

NFL_SCHEDULES_DDL = """
CREATE TABLE IF NOT EXISTS nfl_schedules (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  team TEXT NOT NULL,
  opponent TEXT,
  is_home INTEGER NOT NULL,
  is_bye INTEGER NOT NULL,
  game_date TEXT,
  status TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (season, week, team)
);
"""

# Each COPY_* statement fills the rebuilt table from the old rows, renamed to {table}_legacy.
COPY_PROJECTIONS = """
INSERT INTO projections (id, source_website, season, week, player_first_name, player_last_name, position, team,
                         projected_points, external_id, created_at)
SELECT id, source_website, :season, CAST(replace(week, 'Week ', '') AS INTEGER), player_first_name, player_last_name,
       replace(position, 'DST', 'DEF'), team, projected_points, NULL, created_at
FROM projections_legacy
"""

COPY_PROJECTIONS_WITH_SLEEPER = """
INSERT INTO projections_with_sleeper (id, source_website, season, week, player_first_name, player_last_name, position,
                                      team, projected_points, external_id, sleeper_player_id, match_method, created_at)
SELECT id, source_website, :season, CAST(replace(week, 'Week ', '') AS INTEGER), player_first_name, player_last_name,
       replace(position, 'DST', 'DEF'), team, projected_points, NULL, sleeper_player_id, match_method, created_at
FROM projections_with_sleeper_legacy
"""

COPY_PLAYER_WEEK_STATS = """
INSERT INTO player_week_stats (season, week, sleeper_player_id, player_name, position, team, mu, sigma, var,
                               n_sources, spread, model_version, computed_at)
SELECT :season, week, sleeper_player_id, player_name, replace(position, 'DST', 'DEF'), NULL, mu, sigma, var,
       n_sources, legacy_spread(sigma, n_sources, alpha, beta, pos_sigma), :model_version, computed_at
FROM player_week_stats_legacy
"""

COPY_TEAM_LINEUPS = """
INSERT INTO team_lineups (season, week, roster_id, team_name, owner, record, slot, sleeper_player_id, player_name,
                          position, nfl_team, mu, sigma, var, n_sources, is_replacement, timestamp)
SELECT :season, week, roster_id, team_name, owner, record, slot, NULL, player_name,
       replace(position, 'DST', 'DEF'), NULL, mu, sigma, var, n_sources, is_replacement, timestamp
FROM team_lineups_legacy
"""

COPY_TEAM_PROJECTIONS_SUMMARY = """
INSERT INTO team_projections_summary (season, week, roster_id, team_name, owner, record, total_mu, combined_sigma,
                                      total_var, waiver_pickups, timestamp)
SELECT :season, week, roster_id, team_name, owner, record, total_mu, combined_sigma, total_var, waiver_pickups,
       timestamp
FROM team_projections_summary_legacy
"""

COPY_PROJECTIONS_ROSTERS = """
INSERT INTO projections_rosters (season, week, roster_id, team_name, sleeper_player_id, first_name, last_name, position,
                                 nfl_team, mu, var, starting_status, roster_status, timestamp)
SELECT :season, week, roster_id, team_name, sleeper_player_id, first_name, last_name, replace(position, 'DST', 'DEF'),
       nfl_team, COALESCE(mu, 0), COALESCE(var, 0), starting_status,
       CASE WHEN starting_status = 1 THEN 'starter' WHEN mu IS NULL THEN 'unprojected' ELSE 'bench' END,
       timestamp
FROM projections_rosters_legacy
"""

# The notebooks never recorded home or away, so is_home is 0 throughout 2025.
COPY_NFL_SCHEDULES = """
INSERT INTO nfl_schedules (season, week, team, opponent, is_home, is_bye, game_date, status, updated_at)
SELECT CAST(season AS INTEGER), week, team, opponent, COALESCE(is_home, 0), COALESCE(is_bye, 0), game_date, NULL, :now
FROM nfl_schedules_legacy
"""

# The 2025 curves carry no run id; they belong to the week's odds run, found in betting_odds_team_ou.
COPY_TEAM_DISTRIBUTION_CURVES = """
INSERT INTO team_distribution_curves (run_id, week, season, owner, x_values, density_values, cdf_values, mean, p10, p50,
                                      p90, n_sims, created_at)
SELECT (SELECT MAX(run_id) FROM betting_odds_team_ou AS team_ou WHERE team_ou.week = curves.week), week, :season,
       owner, x_values, density_values, cdf_values, mean, p10, p50, p90, n_sims, created_at
FROM team_distribution_curves_legacy AS curves
"""

COPY_TEAM_MATCHUP_MARGIN_CURVES = """
INSERT INTO team_matchup_margin_curves (run_id, week, season, team_owner, opponent_owner, team_win_prob,
                                        opponent_win_prob, tie_prob, left_x_values, left_y_values, right_x_values,
                                        right_y_values, created_at)
SELECT (SELECT MAX(run_id) FROM betting_odds_team_ou AS team_ou WHERE team_ou.week = curves.week), week, :season,
       team_owner, opponent_owner, team_win_prob, opponent_win_prob, tie_prob, left_x_values, left_y_values,
       right_x_values, right_y_values, created_at
FROM team_matchup_margin_curves_legacy AS curves
"""

CURVE_COPIES = {
    "team_distribution_curves": COPY_TEAM_DISTRIBUTION_CURVES,
    "team_matchup_margin_curves": COPY_TEAM_MATCHUP_MARGIN_CURVES,
}

# The notebooks stored neither NFL teams nor lineup player ids, so the FILL_* statements back-fill 2025's NULLs.
# league.db's nfl_players is a snapshot taken in week 16, after the trade deadline: a player traded mid-season
# carries his new team in every week.
FILL_STATS_TEAMS = """
UPDATE player_week_stats SET team = players.team
FROM league.nfl_players AS players
WHERE player_week_stats.season = :season AND player_week_stats.team IS NULL
  AND players.player_id = player_week_stats.sleeper_player_id AND players.team IS NOT NULL
"""

# A lineup names its players; the id comes from the one stats row of that week with the same name and position.
FILL_LINEUP_IDS = """
UPDATE team_lineups SET sleeper_player_id = matches.sleeper_player_id
FROM (
  SELECT season, week, player_name, position, MIN(sleeper_player_id) AS sleeper_player_id
  FROM player_week_stats
  WHERE season = :season
  GROUP BY season, week, player_name, position
  HAVING COUNT(*) = 1
) AS matches
WHERE team_lineups.sleeper_player_id IS NULL AND matches.season = team_lineups.season
  AND matches.week = team_lineups.week AND matches.player_name = team_lineups.player_name
  AND matches.position = team_lineups.position
"""

FILL_LINEUP_TEAMS = """
UPDATE team_lineups SET nfl_team = players.team
FROM league.nfl_players AS players
WHERE team_lineups.season = :season AND team_lineups.nfl_team IS NULL
  AND players.player_id = team_lineups.sleeper_player_id AND players.team IS NOT NULL
"""

COUNT_STATS_FILLS = """
SELECT COUNT(*) AS n_rows, COUNT(team) AS n_teams FROM player_week_stats WHERE season = :season
"""

COUNT_LINEUP_FILLS = """
SELECT COUNT(*) AS n_rows, COUNT(sleeper_player_id) AS n_ids, COUNT(nfl_team) AS n_teams,
       SUM(player_name IS :waiver_pickup) AS n_waiver,
       SUM(sleeper_player_id IS NULL AND player_name IS NOT :waiver_pickup) AS n_unresolved
FROM team_lineups
WHERE season = :season
"""


def migrate(settings: Settings) -> dict:
    db_dir = settings.db_paths["league"].parent
    missing = [f"{name}.db" for name in LEGACY_DATABASES if not settings.db_paths[name].exists()]
    if missing:
        raise FileNotFoundError(f"{', '.join(missing)} not found in {db_dir}; there is nothing to migrate")

    before = count_rows(settings)
    actions = {
        "backup": [back_up(settings, db_dir / BACKUP_DIR_NAME)],
        "projections.db": migrate_projections_db(settings),
        "league.db": migrate_league_db(settings),
        "odds.db": migrate_odds_db(settings),
        "backfill": backfill_players(settings),
    }
    after = count_rows(settings)
    print_report(db_dir, actions, before, after)
    return {"actions": actions, "row_counts": {"before": before, "after": after}}


def back_up(settings: Settings, backup_dir: Path) -> str:
    backup_dir.mkdir(exist_ok=True)
    copied = []
    for name in LEGACY_DATABASES:
        source = settings.db_paths[name]
        target = backup_dir / source.name
        if not target.exists():
            shutil.copy2(source, target)
            copied.append(source.name)
    if not copied:
        return f"{backup_dir.name} already holds a copy of every database"
    return f"copied {', '.join(copied)} to {backup_dir.name}"


def migrate_projections_db(settings: Settings) -> list[str]:
    with closing(connect(settings, "projections")) as conn:
        conn.create_function("legacy_spread", 5, legacy_spread, deterministic=True)
        actions = [
            rebuild(conn, "projections", PROJECTIONS_DDL, COPY_PROJECTIONS),
            rebuild(conn, "projections_with_sleeper", PROJECTIONS_WITH_SLEEPER_DDL, COPY_PROJECTIONS_WITH_SLEEPER),
            rebuild(conn, "player_week_stats", PLAYER_WEEK_STATS_DDL, COPY_PLAYER_WEEK_STATS),
            rebuild(conn, "team_lineups", TEAM_LINEUPS_DDL, COPY_TEAM_LINEUPS),
            rebuild(conn, "team_projections_summary", TEAM_PROJECTIONS_SUMMARY_DDL, COPY_TEAM_PROJECTIONS_SUMMARY),
        ]
        actions.append(drop_stray_tables(conn))
    return actions


def migrate_league_db(settings: Settings) -> list[str]:
    with closing(connect(settings, "league")) as conn:
        actions = [
            rebuild(conn, "projections_rosters", PROJECTIONS_ROSTERS_DDL, COPY_PROJECTIONS_ROSTERS),
            rebuild(conn, "nfl_schedules", NFL_SCHEDULES_DDL, COPY_NFL_SCHEDULES),
            rename_defense_position(conn),
        ]
        for table, columns in REPR_COLUMNS.items():
            actions.append(convert_reprs(conn, table, columns))
    return actions


def migrate_odds_db(settings: Settings) -> list[str]:
    odds_ddl = odds_ddl_by_table()
    with closing(connect(settings, "odds")) as conn:
        actions = [
            rebuild(conn, table, odds_ddl[table], copy_adding_season(conn, table)) for table in WEEKLY_ODDS_TABLES
        ]
        actions += [rebuild(conn, table, odds_ddl[table], copy_sql) for table, copy_sql in CURVE_COPIES.items()]
        actions += [add_season(conn, table) for table in STANDINGS_TABLES]
    return actions


def odds_ddl_by_table() -> dict[str, str]:
    """ODDS_DDL as one CREATE TABLE statement per table, since rebuild() runs a single statement."""
    with closing(sqlite3.connect(":memory:")) as scratch:
        scratch.executescript(ODDS_DDL)
        return dict(scratch.execute("SELECT name, sql FROM sqlite_master WHERE type = 'table'"))


def copy_adding_season(conn: sqlite3.Connection, table: str) -> str:
    """The copy for a table whose legacy columns carry over unchanged, read before rebuild() renames it."""
    columns = ", ".join(row["name"] for row in conn.execute(f"PRAGMA table_info({table})"))
    return f"INSERT INTO {table} ({columns}, season) SELECT {columns}, :season FROM {table}_legacy"


def backfill_players(settings: Settings) -> list[str]:
    params = {"season": LEGACY_SEASON, "waiver_pickup": WAIVER_PICKUP}
    with closing(connect(settings, "projections")) as conn:
        conn.execute("ATTACH DATABASE ? AS league", (str(settings.db_paths["league"]),))
        with conn:
            n_stats_teams = conn.execute(FILL_STATS_TEAMS, params).rowcount
            n_lineup_ids = conn.execute(FILL_LINEUP_IDS, params).rowcount
            n_lineup_teams = conn.execute(FILL_LINEUP_TEAMS, params).rowcount
        stats = conn.execute(COUNT_STATS_FILLS, params).fetchone()
        lineups = conn.execute(COUNT_LINEUP_FILLS, params).fetchone()
    return [
        f"player_week_stats {LEGACY_SEASON}: filled team on {n_stats_teams} rows, "
        f"now set on {stats['n_teams']} of {stats['n_rows']}",
        f"team_lineups {LEGACY_SEASON}: filled sleeper_player_id on {n_lineup_ids} rows, "
        f"now set on {lineups['n_ids']} of {lineups['n_rows']}; "
        f"waiver pickups {lineups['n_waiver']}, unresolved names {lineups['n_unresolved']}",
        f"team_lineups {LEGACY_SEASON}: filled nfl_team on {n_lineup_teams} rows, "
        f"now set on {lineups['n_teams']} of {lineups['n_rows']}",
    ]


def rebuild(conn: sqlite3.Connection, table: str, ddl: str, copy_sql: str) -> str:
    """Recreate table from ddl and copy its rows across in one transaction, so a failure leaves it untouched."""
    if is_migrated(conn, table):
        return f"{table}: already migrated"
    params = {"season": LEGACY_SEASON, "model_version": LEGACY_MODEL_VERSION, "now": timestamp(utc_now())}
    with conn:
        conn.execute("BEGIN")
        conn.execute(f"ALTER TABLE {table} RENAME TO {table}_legacy")
        conn.execute(ddl)
        conn.execute(copy_sql, params)
        conn.execute(f"DROP TABLE {table}_legacy")
    n_rows = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return f"{table}: rebuilt, {n_rows} rows"


def add_season(conn: sqlite3.Connection, table: str) -> str:
    if is_migrated(conn, table):
        return f"{table}: already migrated"
    with conn:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN season INTEGER NOT NULL DEFAULT {LEGACY_SEASON}")
    return f"{table}: added season {LEGACY_SEASON}"


def is_migrated(conn: sqlite3.Connection, table: str) -> bool:
    columns = {row["name"]: row["type"] for row in conn.execute(f"PRAGMA table_info({table})")}
    return columns.get("season") == "INTEGER"


def drop_stray_tables(conn: sqlite3.Connection) -> str:
    """projections.db holds stale copies of the odds tables and an empty player_stats table the pipeline never reads."""
    stray = [table for table in table_names(conn) if table.startswith("betting_odds_") or table == "player_stats"]
    if not stray:
        return "no stray tables"
    with conn:
        for table in stray:
            conn.execute(f"DROP TABLE {table}")
    return f"dropped {', '.join(stray)}"


def rename_defense_position(conn: sqlite3.Connection) -> str:
    with conn:
        n_renamed = conn.execute("UPDATE nfl_players SET position = 'DEF' WHERE position = 'DST'").rowcount
    return f"nfl_players: {n_renamed} positions DST -> DEF"


def convert_reprs(conn: sqlite3.Connection, table: str, columns: list[str]) -> str:
    rows = conn.execute(f"SELECT rowid, {', '.join(columns)} FROM {table}").fetchall()
    n_converted = 0
    with conn:
        for row in rows:
            for column in columns:
                value = repr_to_json(row[column])
                if value != row[column]:
                    conn.execute(f"UPDATE {table} SET {column} = ? WHERE rowid = ?", (value, row["rowid"]))
                    n_converted += 1
    return f"{table}: {n_converted} values rewritten from Python repr to JSON"


def repr_to_json(value: str | None) -> str | None:
    """ "{'fpts': 1750}" becomes '{"fpts": 1750}' and "None" becomes "null"; JSON and NULL come back unchanged."""
    if value is None:
        return None
    try:
        json.loads(value)
    except json.JSONDecodeError:
        return json.dumps(ast.literal_eval(value))
    return value


def legacy_spread(sigma: float, n_sources: int, alpha: float, beta: float, pos_sigma: float) -> float:
    """Invert notebook 05's sigma = sqrt((alpha * spread)^2 + (beta * pos_sigma)^2) to recover the spread."""
    if n_sources == 1:
        return 0.0
    return math.sqrt(max(sigma**2 - (beta * pos_sigma) ** 2, 0.0)) / alpha


def table_names(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [row["name"] for row in rows]


def count_rows(settings: Settings) -> dict[str, dict[str, int]]:
    counts = {}
    for name in LEGACY_DATABASES:
        with closing(connect(settings, name)) as conn:
            counts[f"{name}.db"] = {
                table: conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in table_names(conn)
            }
    return counts


def print_report(db_dir: Path, actions: dict[str, list[str]], before: dict, after: dict) -> None:
    print(f"migrate-legacy in {db_dir}")
    for group, messages in actions.items():
        for message in messages:
            print(f"  {group}: {message}")
    rows = []
    for database in before:
        for table in sorted(before[database].keys() | after[database].keys()):
            n_before = before[database].get(table, "-")
            n_after = after[database].get(table, "-")
            rows.append([database, table, str(n_before), str(n_after)])
    print()
    print_table(["database", "table", "rows before", "rows after"], rows)
