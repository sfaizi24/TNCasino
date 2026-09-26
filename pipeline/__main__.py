"""Command line for the weekly pipeline: run steps, check their status, review sources, fit the model."""

import argparse
import json
import sqlite3
import sys

from pipeline.db import connect, ensure_run_tables
from pipeline.runner import (
    delete_source_projections,
    latest_step_rows,
    print_table,
    record_review,
    run_steps,
    step_note,
)
from pipeline.settings import SettingsError, load_settings
from pipeline.sources import SOURCE_NAMES
from pipeline.steps import STEP_ORDER, resolve_steps


def run_command(args: argparse.Namespace) -> int:
    try:
        steps = resolve_steps(args.steps, args.from_step)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    settings = load_settings(week=args.week, season=args.season)
    options = {"sources": args.sources, "no_charts": args.no_charts}
    outcome = run_steps(settings, steps, options)
    return 1 if outcome.status == "failed" else 0


def status_command(args: argparse.Namespace) -> int:
    settings = load_settings(week=args.week, season=args.season)
    conn = connect(settings, "pipeline")
    ensure_run_tables(conn)
    latest = latest_step_rows(conn, settings.season, settings.week)
    conn.close()

    print(f"Season {settings.season} week {settings.week}")
    rows = [status_row(step, latest.get(step)) for step in STEP_ORDER]
    print_table(["step", "status", "duration", "finished", "run_id", "notes"], rows)
    return 0


def status_row(step: str, row: sqlite3.Row | None) -> list[str]:
    if row is None:
        return [step, "-", "", "", "", ""]
    duration = "" if row["duration_s"] is None else f"{row['duration_s']:.1f}s"
    warnings = json.loads(row["warnings"]) if row["warnings"] else []
    notes = step_note(warnings, row["error"])
    return [step, row["status"], duration, row["finished_at"] or "", row["run_id"], notes]


def review_command(args: argparse.Namespace) -> int:
    settings = load_settings(week=args.week, season=args.season)
    conn = connect(settings, "pipeline")
    ensure_run_tables(conn)
    record_review(conn, settings.season, settings.week, args.source, args.verdict, args.note)
    conn.close()
    print(f"Recorded {args.verdict} for {args.source}, season {settings.season} week {settings.week}")

    if args.verdict == "reject":
        conn = connect(settings, "projections")
        deleted = delete_source_projections(conn, settings.season, settings.week, args.source)
        conn.close()
        print(f"Deleted {deleted} {args.source} projections for the week")
        print(f"Next: python -m pipeline run --week {settings.week} --from clean")
    return 0


def fit_model_command(args: argparse.Namespace) -> int:
    # Imported on use, like the legacy migration below, so the weekly commands never depend on them.
    from pipeline.model.fit import fit_and_write

    fit_and_write(load_settings(), args.season, args.weeks, args.out)
    return 0


def migrate_legacy_command(args: argparse.Namespace) -> int:
    from pipeline.legacy import migrate

    migrate(load_settings())
    return 0


def source_list(value: str) -> list[str]:
    names = [name.strip() for name in value.split(",")]
    unknown = [name for name in names if name not in SOURCE_NAMES]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown sources {unknown}; choose from {', '.join(SOURCE_NAMES)}")
    return names


def week_range(value: str) -> list[int]:
    """Parse "12" or "10-16" into the weeks it covers."""
    first, dash, last = value.partition("-")
    if not dash:
        last = first
    if not (first.isdigit() and last.isdigit()) or int(first) > int(last):
        raise argparse.ArgumentTypeError(f"expected a week or a range like 10-16, got {value!r}")
    return list(range(int(first), int(last) + 1))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m pipeline", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="run pipeline steps for a week")
    run.add_argument("--week", type=int, required=True)
    run.add_argument("--season", type=int)
    selection = run.add_mutually_exclusive_group()
    selection.add_argument("--steps", help=f"comma-separated, from: {', '.join(STEP_ORDER)}")
    selection.add_argument("--from", dest="from_step", metavar="STEP", help="this step and every default step after it")
    run.add_argument("--sources", type=source_list, help=f"comma-separated, from: {', '.join(SOURCE_NAMES)}")
    run.add_argument("--no-charts", action="store_true", help="skip rendering charts")
    run.set_defaults(handler=run_command)

    status = commands.add_parser("status", help="latest status of every step for a week")
    status.add_argument("--week", type=int)
    status.add_argument("--season", type=int)
    status.set_defaults(handler=status_command)

    review = commands.add_parser("review", help="record a verdict on a source's projections for a week")
    review.add_argument("--week", type=int, required=True)
    review.add_argument("--season", type=int)
    review.add_argument("--source", required=True, help="source website, e.g. espn.com")
    review.add_argument("--verdict", choices=["ok", "reject"], required=True)
    review.add_argument("--note")
    review.set_defaults(handler=review_command)

    fit_model = commands.add_parser("fit-model", help="fit model parameters from a past season")
    fit_model.add_argument("--season", type=int, required=True)
    fit_model.add_argument("--weeks", type=week_range, required=True, help="a week or a range, e.g. 10-16")
    fit_model.add_argument("--out", required=True, help="parameter version to write, e.g. v2")
    fit_model.set_defaults(handler=fit_model_command)

    migrate_legacy = commands.add_parser(
        "migrate-legacy", help="convert the 2025 databases to integer seasons and weeks"
    )
    migrate_legacy.set_defaults(handler=migrate_legacy_command)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except SettingsError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    # Agents read this output through a pipe on Windows: replace characters the console encoding
    # cannot show (emoji in league names) instead of crashing, and flush each line as it is printed.
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main())
