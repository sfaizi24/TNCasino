"""Store one source's projections for weeks already played, so the model can be fitted on them.

The structural checks still refuse a week, but value_agreement is only advisory: it measures the source against
Sleeper's own past-season level, so its numbers are printed and the rows stored regardless. Only a week with
actual scores in league.db's player_stats can be backfilled, which keeps the week in play out.
"""

from dataclasses import replace

from pipeline.runner import StepContext, delete_source_projections, make_run_id, print_table, utc_now
from pipeline.settings import Settings
from pipeline.sources import load_source
from pipeline.sources.base import ProjectionSource
from pipeline.steps import clean, match, scrape

ADVISORY_CHECK = "value_agreement"


def backfill(settings: Settings, weeks: list[int], source_name: str, options: dict) -> int:
    source = load_source(source_name)
    run_id = make_run_id(settings.season, weeks[0], utc_now())
    summary_rows = []
    for week in weeks:
        with StepContext(replace(settings, week=week), run_id, options, step="backfill") as ctx:
            summary_rows.append(backfill_week(ctx, source))

    print()
    print_table(["week", "status", "rows", "matched", ADVISORY_CHECK], summary_rows)
    every_week_stored = all(row[1] == "stored" for row in summary_rows)
    return 0 if every_week_stored else 1


def backfill_week(ctx: StepContext, source: ProjectionSource) -> list[str]:
    """Fetch, check and store the source's week; returns its line of the summary table."""
    season, week = ctx.settings.season, ctx.settings.week
    conn = ctx.db("projections")
    conn.executescript(scrape.PROJECTIONS_DDL)
    played = ctx.db("league").execute(
        "SELECT 1 FROM player_stats WHERE season = ? AND week = ? LIMIT 1", (season, week)
    )
    if played.fetchone() is None:
        ctx.log(f"week {week}: no actual scores in league.db; backfill is for weeks already played")
        return [str(week), "not played", "-", "-", "-"]

    sleeper_players = scrape.read_sleeper_players(ctx.db("league"))
    sleeper_rows = scrape.read_projections(conn, season, week, match.SLEEPER_WEBSITE)
    rows, report, elapsed_s = scrape.scrape_source(ctx, source, sleeper_players, sleeper_rows)
    scrape.print_source(ctx, source.website, report, rows, elapsed_s)

    checks = {check.name: check for check in report.checks}
    advisory = checks.get(ADVISORY_CHECK)  # absent when the fetch failed and nothing was checked
    refused = [name for name, check in checks.items() if check.status == "fail" and name != ADVISORY_CHECK]
    if refused:
        ctx.log(f"week {week}: refused, {', '.join(refused)}")
        return [str(week), "refused", "-", "-", advisory.status if advisory else "-"]

    delete_source_projections(conn, season, week, source.website)
    scrape.insert_projections(conn, rows)
    # Clean and match rework the whole week for every source, exactly as a weekly run does.
    clean.run(ctx)
    match.run(ctx)
    n_rows, n_matched = conn.execute(
        "SELECT COUNT(*), COUNT(sleeper_player_id) FROM projections_with_sleeper "
        "WHERE source_website = ? AND season = ? AND week = ?",
        (source.website, season, week),
    ).fetchone()
    ctx.log(f"week {week}: stored {n_rows} rows, {n_matched} matched to Sleeper players")
    if advisory.status != "ok":
        ctx.log(f"week {week}: {ADVISORY_CHECK} {advisory.status}: {advisory.detail}")
    return [str(week), "stored", str(n_rows), str(n_matched), advisory.status]
