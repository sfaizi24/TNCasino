"""Publish local SQLite analytics data to production PostgreSQL.

Reads notebook-produced SQLite files and pushes the tables the Flask app
needs into PostgreSQL. Uses a staging+swap strategy: data lands in
temporary tables first, gets validated, then atomically replaces the
live tables.

Usage:
    python -m scripts.publish              # publish all tables
    python -m scripts.publish --dry-run    # validate without swapping
"""

import os
import sqlite3
import sys
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

DATABASE_URL = os.environ.get("DATABASE_URL")
if not DATABASE_URL:
    print("ERROR: DATABASE_URL not set in environment or .env")
    sys.exit(1)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ODDS_DB = str(PROJECT_ROOT / "backend/data/databases/odds.db")
PROJECTIONS_DB = str(PROJECT_ROOT / "backend/data/databases/projections.db")
LEAGUE_DB = str(PROJECT_ROOT / "backend/data/databases/league.db")

# SQLite source → (table_name, PostgreSQL target_name)
TABLE_MAP = [
    (ODDS_DB, "betting_odds_matchup_ml", "betting_odds_matchup_ml"),
    (ODDS_DB, "betting_odds_team_ou", "betting_odds_team_ou"),
    (ODDS_DB, "betting_odds_highest_scorer", "betting_odds_highest_scorer"),
    (ODDS_DB, "betting_odds_lowest_scorer", "betting_odds_lowest_scorer"),
    (ODDS_DB, "betting_odds_first_place", "betting_odds_first_place"),
    (ODDS_DB, "betting_odds_make_playoffs", "betting_odds_make_playoffs"),
    (ODDS_DB, "team_distribution_curves", "team_distribution_curves"),
    (ODDS_DB, "team_matchup_margin_curves", "team_matchup_margin_curves"),
    (PROJECTIONS_DB, "team_lineups", "team_lineups"),
    (LEAGUE_DB, "rosters", "sleeper_rosters"),
    (LEAGUE_DB, "users", "sleeper_users"),
    (LEAGUE_DB, "matchups", "sleeper_matchups"),
    (LEAGUE_DB, "projections_rosters", "projections_rosters"),
]

# Tables protected from DROP: owned by the Flask ORM, plus the pipeline's append-only score matrices
PROTECTED_TABLES = {"users", "bets", "bet_legs", "weekly_stats", "betting_periods", "simulation_totals"}


def read_sqlite_table(db_path, table_name):
    conn = sqlite3.connect(db_path)
    df = pd.read_sql(f"SELECT * FROM {table_name}", conn)
    conn.close()
    return df


def publish(dry_run=False):
    engine = create_engine(DATABASE_URL)
    staged = []
    errors = []

    print("=" * 60)
    print("Publishing analytics data to PostgreSQL")
    print("=" * 60)

    # Stage all tables
    for db_path, src_table, target_table in TABLE_MAP:
        staging_name = f"{target_table}_staging"

        if target_table in PROTECTED_TABLES:
            errors.append(f"BLOCKED: {target_table} is a protected ORM table")
            continue

        try:
            df = read_sqlite_table(db_path, src_table)
            src_rows = len(df)

            # Write to staging table
            df.to_sql(staging_name, engine, if_exists="replace", index=False)

            # Validate row count
            with engine.connect() as conn:
                result = conn.execute(text(f"SELECT COUNT(*) FROM {staging_name}"))
                pg_rows = result.scalar()

            if pg_rows != src_rows:
                errors.append(f"MISMATCH: {target_table} — source={src_rows}, staging={pg_rows}")
                continue

            staged.append((staging_name, target_table, src_rows))
            print(f"  OK  {target_table}: {src_rows} rows staged")

        except Exception as e:
            errors.append(f"FAILED: {target_table} — {e}")

    print()

    # Abort if any errors
    if errors:
        print("ERRORS — aborting publish, live tables unchanged:")
        for err in errors:
            print(f"  {err}")
        _cleanup_staging(engine, staged)
        sys.exit(1)

    if dry_run:
        print("DRY RUN — validation passed, skipping swap")
        _cleanup_staging(engine, staged)
        return

    # Swap staging → live atomically
    print("Swapping staging tables to live...")
    with engine.begin() as conn:
        for staging_name, target_table, _ in staged:
            conn.execute(text(f"DROP TABLE IF EXISTS {target_table}"))
            conn.execute(text(f"ALTER TABLE {staging_name} RENAME TO {target_table}"))

    print()
    print("PUBLISHED SUCCESSFULLY")
    print("-" * 40)
    for _, target_table, row_count in staged:
        print(f"  {target_table}: {row_count} rows")


def _cleanup_staging(engine, staged):
    with engine.begin() as conn:
        for staging_name, _, _ in staged:
            conn.execute(text(f"DROP TABLE IF EXISTS {staging_name}"))


if __name__ == "__main__":
    dry_run = "--dry-run" in sys.argv
    publish(dry_run=dry_run)
