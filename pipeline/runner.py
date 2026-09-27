"""Run pipeline steps in canonical order and record every run and step in pipeline.db."""

import json
import sqlite3
import subprocess
import sys
import traceback
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Self

from pipeline.db import connect, ensure_run_tables
from pipeline.settings import PROJECT_ROOT, Settings
from pipeline.steps import STEP_ORDER, load_step


@dataclass
class StepContext:
    settings: Settings
    run_id: str
    options: dict = field(default_factory=dict)
    step: str = ""
    _connections: dict[str, sqlite3.Connection] = field(default_factory=dict, init=False, repr=False)

    def db(self, name: str) -> sqlite3.Connection:
        """One connection per database for the whole step: commit your writes and leave closing to the runner."""
        if name not in self._connections:
            self._connections[name] = connect(self.settings, name)
        return self._connections[name]

    def log(self, message: str) -> None:
        print(f"  [{self.step}] {message}")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info) -> None:
        for conn in self._connections.values():
            conn.close()
        self._connections.clear()


@dataclass
class StepResult:
    summary: dict
    warnings: list[str] = field(default_factory=list)
    charts: list[str] = field(default_factory=list)


class StepFailed(Exception):
    """Raised by a step that finished its checks and must stop the run, keeping its summary on record."""

    def __init__(self, message: str, summary: dict | None = None):
        super().__init__(message)
        self.summary = summary or {}


@dataclass
class StepOutcome:
    step: str
    status: str
    duration_s: float
    warnings: list[str]
    error: str | None


@dataclass
class RunOutcome:
    run_id: str
    status: str
    steps: list[StepOutcome]


def run_steps(settings: Settings, step_names: list[str], options: dict) -> RunOutcome:
    steps = sorted(step_names, key=STEP_ORDER.index)
    conn = connect(settings, "pipeline")
    ensure_run_tables(conn)
    run_id = start_run(conn, settings, steps)
    print(f"Run {run_id} for season {settings.season} week {settings.week} (league {settings.league_id})")
    print(f"Steps: {', '.join(steps)}")

    outcomes = []
    for number, name in enumerate(steps, start=1):
        print(f"[{number}/{len(steps)}] {name}")
        outcome = run_step(conn, settings, run_id, name, options)
        outcomes.append(outcome)
        if outcome.status == "failed":
            break

    status = finish_run(conn, run_id, outcomes)
    conn.close()
    print_run_summary(run_id, status, steps, outcomes)
    return RunOutcome(run_id, status, outcomes)


def start_run(conn: sqlite3.Connection, settings: Settings, steps: list[str]) -> str:
    started = utc_now()
    run_id = make_run_id(settings.season, settings.week, started)
    conn.execute(
        "INSERT INTO pipeline_runs (run_id, season, week, started_at, status, steps, git_sha) "
        "VALUES (?, ?, ?, ?, 'running', ?, ?)",
        (run_id, settings.season, settings.week, timestamp(started), json.dumps(steps), git_sha()),
    )
    conn.commit()
    return run_id


def run_step(conn: sqlite3.Connection, settings: Settings, run_id: str, name: str, options: dict) -> StepOutcome:
    started = utc_now()
    conn.execute(
        "INSERT INTO pipeline_steps (run_id, step, started_at, status) VALUES (?, ?, ?, 'running')",
        (run_id, name, timestamp(started)),
    )
    conn.commit()

    summary = None
    warnings = []
    charts = []
    error = None
    try:
        with StepContext(settings, run_id, options, step=name) as ctx:
            result = load_step(name).run(ctx)
        summary = json.dumps(result.summary)
        warnings = result.warnings
        charts = result.charts
        status = "warn" if warnings else "ok"
    except StepFailed as failed:
        summary = json.dumps(failed.summary)
        error = str(failed)
        status = "failed"
        print(f"ERROR: {error}", file=sys.stderr)
    except Exception:
        error = traceback.format_exc()
        status = "failed"
        print(error, end="", file=sys.stderr)

    finished = utc_now()
    duration_s = round((finished - started).total_seconds(), 2)
    conn.execute(
        "UPDATE pipeline_steps SET finished_at = ?, duration_s = ?, status = ?, summary = ?, warnings = ?, "
        "charts = ?, error = ? WHERE run_id = ? AND step = ?",
        (
            timestamp(finished),
            duration_s,
            status,
            summary,
            json.dumps(warnings),
            json.dumps(charts),
            error,
            run_id,
            name,
        ),
    )
    conn.commit()
    for warning in warnings:
        print(f"  [{name}] warning: {warning}")
    return StepOutcome(name, status, duration_s, warnings, error)


def finish_run(conn: sqlite3.Connection, run_id: str, outcomes: list[StepOutcome]) -> str:
    failures = [outcome for outcome in outcomes if outcome.status == "failed"]
    error = None
    if failures:
        status = "failed"
        error = f"{failures[0].step}: {step_note(failures[0].warnings, failures[0].error)}"
    elif any(outcome.status == "warn" for outcome in outcomes):
        status = "warn"
    else:
        status = "ok"

    conn.execute(
        "UPDATE pipeline_runs SET finished_at = ?, status = ?, error = ? WHERE run_id = ?",
        (timestamp(utc_now()), status, error, run_id),
    )
    conn.commit()
    return status


def print_run_summary(run_id: str, status: str, steps: list[str], outcomes: list[StepOutcome]) -> None:
    rows = []
    for outcome in outcomes:
        note = step_note(outcome.warnings, outcome.error)
        rows.append([outcome.step, outcome.status, f"{outcome.duration_s:.1f}s", note])
    print()
    print_table(["step", "status", "duration", "notes"], rows)
    not_run = steps[len(outcomes) :]
    if not_run:
        print(f"Not run: {', '.join(not_run)}")
    print(f"Run {run_id}: {status}")


def step_note(warnings: list[str], error: str | None) -> str:
    """One line for a table cell: the exception line of a failure, else the first warning."""
    if error:
        return error.strip().splitlines()[-1]
    if not warnings:
        return ""
    note = warnings[0]
    if len(warnings) > 1:
        note += f" (+{len(warnings) - 1} more)"
    return note


def print_table(headers: list[str], rows: list[list[str]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(cell))
    separator = ["-" * width for width in widths]
    for line in [headers, separator, *rows]:
        cells = [cell.ljust(width) for cell, width in zip(line, widths, strict=True)]
        print("  ".join(cells).rstrip())


def latest_step_rows(conn: sqlite3.Connection, season: int, week: int) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT s.* FROM pipeline_steps s JOIN pipeline_runs r ON r.run_id = s.run_id "
        "WHERE r.season = ? AND r.week = ? ORDER BY s.started_at, s.run_id",
        (season, week),
    ).fetchall()
    # Oldest first, so each step's most recent row overwrites the earlier ones.
    return {row["step"]: row for row in rows}


def record_review(
    conn: sqlite3.Connection, season: int, week: int, source: str, verdict: str, note: str | None
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO source_reviews (season, week, source, verdict, note, reviewed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (season, week, source, verdict, note, timestamp(utc_now())),
    )
    conn.commit()


def delete_source_projections(conn: sqlite3.Connection, season: int, week: int, source: str) -> int:
    """Remove a rejected source's rows for the week; nothing to do before the first scrape creates the table."""
    table = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'projections'").fetchone()
    if table is None:
        return 0
    cursor = conn.execute(
        "DELETE FROM projections WHERE source_website = ? AND season = ? AND week = ?",
        (source, season, week),
    )
    conn.commit()
    return cursor.rowcount


def make_run_id(season: int, week: int, started: datetime) -> str:
    return f"{season}w{week:02d}-{started:%Y%m%dT%H%M%S}"


def git_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def utc_now() -> datetime:
    return datetime.now(UTC)


def timestamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")
