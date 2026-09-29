"""Publish step: upload the charts, store each new run's score matrix, then replace production's copy of this
season's analytics and run records.

Every replaced table is staged and row-counted before all of them are swapped in together, as scripts/publish.py
did. The score matrices are only ever appended to.
"""

import json
import os
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import Column, Integer, LargeBinary, MetaData, Table, Text, create_engine, text
from sqlalchemy.engine import URL, Engine, make_url

from pipeline import markets
from pipeline.db import DB_NAMES
from pipeline.runner import StepContext, StepResult, print_table, timestamp, utc_now
from pipeline.settings import Settings
from pipeline.steps.odds import load_draws

NAME = "publish"
DEFAULT_CHARTS_TARGET = "root@143.198.183.213:/var/lib/tncasino/analytics/"
SCP_TIMEOUT_S = 60

# (local database, local table, production table), in upload order: analytics first, run records last.
TABLES = [
    ("odds", "betting_odds_matchup_ml", "betting_odds_matchup_ml"),
    ("odds", "betting_odds_team_ou", "betting_odds_team_ou"),
    ("odds", "betting_odds_matchup_ou", "betting_odds_matchup_ou"),
    ("odds", "betting_odds_highest_scorer", "betting_odds_highest_scorer"),
    ("odds", "betting_odds_lowest_scorer", "betting_odds_lowest_scorer"),
    ("odds", "betting_odds_first_place", "betting_odds_first_place"),
    ("odds", "betting_odds_make_playoffs", "betting_odds_make_playoffs"),
    ("odds", "standings_probability_matrix", "standings_probability_matrix"),
    ("odds", "team_distribution_curves", "team_distribution_curves"),
    ("odds", "team_matchup_margin_curves", "team_matchup_margin_curves"),
    ("projections", "team_lineups", "team_lineups"),
    ("league", "rosters", "sleeper_rosters"),
    ("league", "users", "sleeper_users"),
    ("league", "matchups", "sleeper_matchups"),
    ("league", "projections_rosters", "projections_rosters"),
    ("odds", "simulation_runs", "simulation_runs"),
    ("odds", "calibration_metrics", "calibration_metrics"),
    ("projections", "prediction_accuracy", "prediction_accuracy"),
    ("projections", "team_accuracy", "team_accuracy"),
    ("pipeline", "pipeline_runs", "pipeline_runs"),
    ("pipeline", "pipeline_steps", "pipeline_steps"),
    ("pipeline", "source_reviews", "source_reviews"),
]
# Owned by the Flask app: `users` holds the site's accounts, while Sleeper's users publish as sleeper_users.
PROTECTED_TABLES = {"users", "bets", "bet_legs", "weekly_stats", "betting_periods", "parlay_refusals"}
# Never swapped, because a swap would drop the matrices of earlier runs, and bets are re-priced at the run they
# were placed on.
APPEND_ONLY_TABLES = {"simulation_totals"}
# The dashboard lists every run of the season, so these keep all their runs instead of the latest per week.
RUN_HISTORY = {"pipeline_runs", "pipeline_steps"}

# LargeBinary is a BLOB in SQLite and a bytea in PostgreSQL.
SIMULATION_TOTALS = Table(
    "simulation_totals",
    MetaData(),
    Column("run_id", Text, primary_key=True),
    Column("season", Integer, nullable=False),
    Column("week", Integer, nullable=False),
    Column("created_at", Text, nullable=False),
    Column("n_sims", Integer, nullable=False),
    Column("roster_ids", Text, nullable=False),
    Column("totals", LargeBinary, nullable=False),
)
INSERT_TOTALS = text(
    "INSERT INTO simulation_totals (run_id, season, week, created_at, n_sims, roster_ids, totals) "
    "VALUES (:run_id, :season, :week, :created_at, :n_sims, :roster_ids, :totals) "
    "ON CONFLICT (run_id) DO NOTHING"
)


class PublishError(Exception):
    pass


def run(ctx: StepContext) -> StepResult:
    started = time.perf_counter()
    url = database_url()
    destination = url.host or url.database
    settings = ctx.settings
    local = {name: ctx.db(name) for name in DB_NAMES}
    tables, skipped = read_tables(local, settings.season, settings.league_id)
    # The latest run of each week, whose score matrix is stored unless production has it already.
    runs = tables.get("simulation_runs", pd.DataFrame())
    counts = [[name, str(len(frame))] for name, frame in tables.items()]
    print_table(["table", "rows"], [*counts, ["simulation_totals", str(len(runs))]])
    warnings = [f"{name} skipped: its local table does not exist yet" for name in skipped]

    dry_run = ctx.options.get("dry_run")
    charts_uploaded = 0
    if dry_run:
        warnings.append(f"dry run: nothing was written to {destination}")
    elif not ctx.options.get("no_charts"):
        charts_uploaded = upload_charts(settings.images_dir)
        if charts_uploaded == 0:
            warnings.append(f"no charts in {settings.images_dir} to upload")

    summary = {
        "tables": [{"name": name, "rows": len(frame)} for name, frame in tables.items()],
        "skipped": skipped,
        "charts_uploaded": charts_uploaded,
        "totals_stored": [],
        "elapsed_s": round(time.perf_counter() - started, 2),
        "target_host": url.host,
    }
    if dry_run:
        return StepResult(summary, warnings)

    engine = create_engine(url)
    try:
        # Stored before the swap, so every run the site can price bets at already has its matrix.
        stored, totals_warnings = store_totals(engine, settings, runs)
        summary["totals_stored"] = stored
        warnings += totals_warnings
        ctx.log(f"new runs in simulation_totals: {', '.join(stored) or 'none'}")
        # The runner marks this step and its run finished only after we return, so production gets that record now.
        finished = utc_now()
        steps = finish_publish_row(tables["pipeline_steps"], ctx.run_id, summary, warnings, finished)
        tables["pipeline_steps"] = steps
        tables["pipeline_runs"] = finish_run_row(tables["pipeline_runs"], steps, ctx.run_id, finished)
        write_tables(engine, tables)
    finally:
        engine.dispose()
    ctx.log(f"published {len(tables)} tables to {destination}")
    return StepResult(summary, warnings)


def database_url() -> URL:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise PublishError("DATABASE_URL is not set; add it to .env to publish")
    return make_url(value)


def read_tables(
    local: dict[str, sqlite3.Connection], season: int, league_id: str
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """The season's rows of every published table by production name, and the tables not created locally yet."""
    tables = {}
    skipped = []
    for database, source, target in TABLES:
        conn = local[database]
        columns = {name for (name,) in conn.execute("SELECT name FROM pragma_table_info(?)", (source,))}
        if not columns:
            skipped.append(target)
            continue
        frame = pd.read_sql(season_query(source, columns), conn, params={"season": season, "league_id": league_id})
        if "run_id" in columns and source not in RUN_HISTORY:
            frame = keep_latest_run(frame, local["odds"])
        tables[target] = frame
    return tables, skipped


def season_query(table: str, columns: set[str]) -> str:
    if table == "pipeline_steps":
        return "SELECT * FROM pipeline_steps WHERE run_id IN (SELECT run_id FROM pipeline_runs WHERE season = :season)"
    if "season" in columns:
        return f"SELECT * FROM {table} WHERE season = :season"
    if "league_id" in columns:
        return f"SELECT * FROM {table} WHERE league_id = :league_id"
    return f"SELECT * FROM {table}"


def keep_latest_run(frame: pd.DataFrame, odds: sqlite3.Connection) -> pd.DataFrame:
    """Keep each week's rows from its most recently created run: Flask expects one row set per week."""
    if "created_at" in frame.columns:
        created_at = frame.groupby("run_id")["created_at"].max()
    else:
        simulation_runs = pd.read_sql("SELECT run_id, created_at FROM simulation_runs", odds)
        created_at = simulation_runs.set_index("run_id")["created_at"]
    week_runs = frame[["week", "run_id"]].drop_duplicates()
    week_runs = week_runs.assign(created_at=week_runs["run_id"].map(created_at))
    # Oldest first, so the last run of each week is its latest; a run with no known creation time counts as oldest.
    oldest_first = week_runs.sort_values(["created_at", "run_id"], na_position="first")
    latest = oldest_first.drop_duplicates("week", keep="last")
    return frame.merge(latest[["week", "run_id"]], on=["week", "run_id"])


def finish_publish_row(
    steps: pd.DataFrame, run_id: str, summary: dict, warnings: list[str], finished: datetime
) -> pd.DataFrame:
    """The current run's publish row as the runner will record it once this step returns."""
    steps = steps.copy()
    row = (steps["run_id"] == run_id) & (steps["step"] == NAME)
    started = datetime.fromisoformat(steps.loc[row, "started_at"].item())
    steps.loc[row, "finished_at"] = timestamp(finished)
    steps.loc[row, "duration_s"] = round((finished - started).total_seconds(), 2)
    steps.loc[row, "status"] = "warn" if warnings else "ok"
    steps.loc[row, "summary"] = json.dumps(summary)
    steps.loc[row, "warnings"] = json.dumps(warnings)
    steps.loc[row, "charts"] = json.dumps([])
    return steps


def finish_run_row(runs: pd.DataFrame, steps: pd.DataFrame, run_id: str, finished: datetime) -> pd.DataFrame:
    """The current run's row as the runner will record it: publish is always the last step to run."""
    runs = runs.copy()
    run_statuses = steps.loc[steps["run_id"] == run_id, "status"]
    row = runs["run_id"] == run_id
    runs.loc[row, "finished_at"] = timestamp(finished)
    runs.loc[row, "status"] = "warn" if (run_statuses == "warn").any() else "ok"
    return runs


def upload_charts(images_dir: Path) -> int:
    names = sorted(path.name for path in images_dir.glob("*.png"))
    if not names:
        return 0
    target = os.environ.get("PUBLISH_CHARTS_TARGET", DEFAULT_CHARTS_TARGET)
    # Bare names run from inside the directory, because scp can read "C:/..." as a host called C.
    # BatchMode makes a missing key fail at once instead of waiting at a password prompt.
    command = ["scp", "-o", "BatchMode=yes", *names, target]
    try:
        subprocess.run(command, cwd=images_dir, check=True, timeout=SCP_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError) as error:
        raise PublishError(f"chart upload to {target} failed, so no table was published: {error}") from error
    return len(names)


def store_totals(engine: Engine, settings: Settings, runs: pd.DataFrame) -> tuple[list[str], list[str]]:
    """Append the score matrix of each run production has not stored yet; return those runs and any warnings."""
    stored_now = []
    warnings = []
    with engine.begin() as conn:
        SIMULATION_TOTALS.create(conn, checkfirst=True)
        query = text("SELECT run_id FROM simulation_totals WHERE season = :season")
        stored = set(conn.execute(query, {"season": settings.season}).scalars())
        for run in runs.to_dict("records"):
            if run["run_id"] in stored:
                continue
            if not (settings.data_dir / run["draws_path"]).exists():
                warnings.append(
                    f"run {run['run_id']} has no draws at {run['draws_path']}, so its matrix was not stored"
                )
                continue
            draws = load_draws(settings, run["draws_path"])
            row = {
                "run_id": run["run_id"],
                "season": run["season"],
                "week": run["week"],
                "created_at": run["created_at"],
                "n_sims": len(draws),
                "roster_ids": ",".join(str(roster_id) for roster_id in draws.columns),
                "totals": markets.encode_totals(draws.to_numpy()),
            }
            conn.execute(INSERT_TOTALS, row)
            stored_now.append(run["run_id"])
    return stored_now, warnings


def write_tables(engine: Engine, tables: dict[str, pd.DataFrame]) -> None:
    """Stage every table and check its row count, then swap them all into place in one transaction."""
    protected = sorted(PROTECTED_TABLES & tables.keys())
    if protected:
        raise PublishError(f"refusing to replace tables the Flask app owns: {', '.join(protected)}")
    append_only = sorted(APPEND_ONLY_TABLES & tables.keys())
    if append_only:
        raise PublishError(f"refusing to replace append-only tables: {', '.join(append_only)}")

    staged = []
    try:
        for name, frame in tables.items():
            staging = f"{name}_staging"
            staged.append(staging)
            frame.to_sql(staging, engine, if_exists="replace", index=False)
            with engine.connect() as conn:
                staged_rows = conn.execute(text(f"SELECT COUNT(*) FROM {staging}")).scalar()
            if staged_rows != len(frame):
                raise PublishError(f"{staging} holds {staged_rows} rows, expected {len(frame)}")
        with engine.begin() as conn:
            for name in tables:
                conn.execute(text(f"DROP TABLE IF EXISTS {name}"))
                conn.execute(text(f"ALTER TABLE {name}_staging RENAME TO {name}"))
    except Exception:
        # Live tables change only when the swap commits, so removing the staging tables restores production.
        with engine.begin() as conn:
            for staging in staged:
                conn.execute(text(f"DROP TABLE IF EXISTS {staging}"))
        raise
