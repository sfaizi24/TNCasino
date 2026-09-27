"""Scrape step: fetch every projection source for the week, verify its rows, and store the ones that pass.

A source that fails is dropped and its rows for the week are deleted, so a stale copy never outlives a bad fetch.
"""

import sqlite3
import time
import traceback
from dataclasses import asdict, astuple

from pipeline.runner import StepContext, StepResult, delete_source_projections, print_table, timestamp, utc_now
from pipeline.sources import SOURCE_NAMES, load_source
from pipeline.sources.base import Projection, ProjectionSource
from pipeline.sources.verify import Check, SourceReport, display_name, verify_source

NAME = "scrape"
MIN_SOURCES = 3  # a full run needs this many usable sources, Sleeper among them
REVIEW_DEPTH = 15
REVIEW_BLOCKS = [["QB", "RB", "WR"], ["TE", "K", "DEF"]]

PROJECTIONS_DDL = """
CREATE TABLE IF NOT EXISTS projections (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_website TEXT NOT NULL,
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  player_first_name TEXT NOT NULL,
  player_last_name TEXT NOT NULL,
  position TEXT NOT NULL,
  team TEXT,
  projected_points REAL NOT NULL,
  external_id TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (source_website, season, week, player_first_name, player_last_name, position)
);
"""
# In Projection's field order, so a row converts to and from a Projection positionally.
PROJECTION_COLUMNS = (
    "source_website, season, week, player_first_name, player_last_name, position, team, projected_points, external_id"
)


def run(ctx: StepContext) -> StepResult:
    season = ctx.settings.season
    week = ctx.settings.week
    conn = ctx.db("projections")
    conn.executescript(PROJECTIONS_DDL)
    sleeper_players = read_sleeper_players(ctx.db("league"))
    sleeper_website = load_source("sleeper").website
    requested = ctx.options.get("sources")

    entries = []
    for name in SOURCE_NAMES:
        source = load_source(name)
        if requested is not None and name not in requested:
            entries.append(kept_entry(conn, season, week, source.website))
            continue
        # Sleeper is the yardstick for the rest. It comes first in SOURCE_NAMES, so when it is scraped
        # the other sources are checked against the rows it has just stored.
        if name == "sleeper":
            sleeper_rows = []
        else:
            sleeper_rows = read_projections(conn, season, week, sleeper_website)
        rows, report, elapsed_s = scrape_source(ctx, source, sleeper_players, sleeper_rows)
        delete_source_projections(conn, season, week, source.website)
        if report.status != "fail":
            insert_projections(conn, rows)
        print_source(ctx, source.website, report, rows, elapsed_s)
        entries.append(source_entry(source.website, report, elapsed_s))

    print()
    print_table(
        ["source", "status", "rows", "seconds"],
        [[entry["source"], entry["status"], str(entry["n_rows"]), f"{entry['elapsed_s']:.1f}"] for entry in entries],
    )
    return finish(entries, requested, sleeper_website)


def read_sleeper_players(conn: sqlite3.Connection) -> list[dict]:
    table = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'nfl_players'").fetchone()
    if table is None:
        raise RuntimeError("league.db has no nfl_players table; run the league step first")
    rows = conn.execute("SELECT player_id, first_name, last_name, position, team FROM nfl_players").fetchall()
    return [dict(row) for row in rows]


def read_projections(conn: sqlite3.Connection, season: int, week: int, website: str) -> list[Projection]:
    rows = conn.execute(
        f"SELECT {PROJECTION_COLUMNS} FROM projections WHERE source_website = ? AND season = ? AND week = ?",
        (website, season, week),
    ).fetchall()
    return [Projection(*row) for row in rows]


def scrape_source(
    ctx: StepContext, source: ProjectionSource, sleeper_players: list[dict], sleeper_rows: list[Projection]
) -> tuple[list[Projection], SourceReport, float]:
    """Fetch and verify one source. A fetch that raises fails this source, not the step."""
    season = ctx.settings.season
    week = ctx.settings.week
    started = time.perf_counter()
    try:
        rows = source.fetch(season, week)
    except Exception as error:
        traceback.print_exc()
        detail = f"{type(error).__name__}: {error}".splitlines()[0]
        report = SourceReport(source.website, "fail", [Check("fetch", "fail", detail)], 0)
        return [], report, time.perf_counter() - started
    elapsed_s = time.perf_counter() - started

    previous_rows = read_projections(ctx.db("projections"), season, week - 1, source.website)
    report = verify_source(rows, week, sleeper_players, sleeper_rows, previous_rows, source.positions)
    return rows, report, elapsed_s


def insert_projections(conn: sqlite3.Connection, rows: list[Projection]) -> None:
    created_at = timestamp(utc_now())
    # Namesakes at one position share the unique key; inserting the best projected first keeps theirs.
    ranked = sorted(rows, key=lambda row: row.points, reverse=True)
    conn.executemany(
        f"INSERT OR IGNORE INTO projections ({PROJECTION_COLUMNS}, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [(*astuple(row), created_at) for row in ranked],
    )
    conn.commit()


def kept_entry(conn: sqlite3.Connection, season: int, week: int, website: str) -> dict:
    """A source this run left alone, listed with the rows it already has so the dashboard still shows it."""
    (n_rows,) = conn.execute(
        "SELECT COUNT(*) FROM projections WHERE source_website = ? AND season = ? AND week = ?",
        (website, season, week),
    ).fetchone()
    return {"source": website, "status": "kept", "n_rows": n_rows, "elapsed_s": 0, "checks": []}


def source_entry(website: str, report: SourceReport, elapsed_s: float) -> dict:
    return {
        "source": website,
        "status": report.status,
        "n_rows": report.n_rows,
        "elapsed_s": round(elapsed_s, 2),
        "checks": [asdict(check) for check in report.checks],
    }


def print_source(
    ctx: StepContext, website: str, report: SourceReport, rows: list[Projection], elapsed_s: float
) -> None:
    ctx.log(f"{website}: {report.status}, {report.n_rows} rows in {elapsed_s:.1f}s")
    print_table(["check", "status", "detail"], [[check.name, check.status, check.detail] for check in report.checks])
    if rows:
        print_review(rows)
    print()


def print_review(rows: list[Projection]) -> None:
    """The top players at each position side by side, for a reviewer to eyeball before the week is used."""
    for block in REVIEW_BLOCKS:
        headers = []
        lines = [[] for _ in range(REVIEW_DEPTH)]
        for position in block:
            headers += [position, "team", "pts"]
            for line, cells in zip(lines, review_column(rows, position), strict=True):
                line.extend(cells)
        print()
        print_table(headers, [line for line in lines if any(line)])


def review_column(rows: list[Projection], position: str) -> list[list[str]]:
    """REVIEW_DEPTH lines of name, team and points, padded with blanks when the position runs short."""
    at_position = sorted([row for row in rows if row.position == position], key=lambda row: row.points, reverse=True)
    column = [[display_name(row), row.team or "-", f"{row.points:.1f}"] for row in at_position[:REVIEW_DEPTH]]
    while len(column) < REVIEW_DEPTH:
        column.append(["", "", ""])
    return column


def finish(entries: list[dict], requested: list[str] | None, sleeper_website: str) -> StepResult:
    """A full run needs MIN_SOURCES usable sources including Sleeper; a subset run needs every source it asked for."""
    usable = []
    dropped = []
    failures = []
    for entry in entries:
        if entry["status"] == "fail":
            failed_checks = [check["name"] for check in entry["checks"] if check["status"] == "fail"]
            dropped.append(entry["source"])
            failures.append(f"{entry['source']} ({', '.join(failed_checks)})")
        elif entry["status"] != "kept" or entry["n_rows"] > 0:
            usable.append(entry["source"])

    if requested is None and (len(usable) < MIN_SOURCES or sleeper_website not in usable):
        raise RuntimeError(
            f"a full run needs {MIN_SOURCES} usable sources including {sleeper_website}; "
            f"usable: {', '.join(usable) or 'none'}; dropped: {', '.join(failures)}"
        )
    if requested is not None and dropped:
        raise RuntimeError(f"requested sources failed: {', '.join(failures)}")

    summary = {"sources": entries, "n_ok_sources": len(usable), "dropped": dropped}
    return StepResult(summary, warnings=[f"dropped {failure}" for failure in failures])
