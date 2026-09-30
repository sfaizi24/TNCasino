import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.backfill import backfill
from pipeline.db import connect
from pipeline.settings import Settings
from pipeline.sources import sleeper
from pipeline.sources.base import POSITIONS, Projection
from pipeline.steps import scrape

FIXTURE = Path(__file__).parent / "fixtures" / "sleeper" / "projections_2026_w4.json"
PLAYED_WEEKS = [10, 11]
OPTIONS = {"sources": ["fftoday"], "no_charts": True, "dry_run": False}


class FakeSource:
    """Serves the Sleeper fixture's players under its own website, stamped with the requested season and week.

    Points move by a hundredth of the week, so consecutive weeks never repeat and freshness passes.
    """

    positions = POSITIONS
    has_week_stamp = True

    def __init__(self, name: str, rows: list[Projection]):
        self.name = name
        self.website = f"{name}.com"
        self.rows = rows
        self.error = None

    def fetch(self, season: int, week: int) -> list[Projection]:
        if self.error is not None:
            raise self.error
        return [
            replace(row, source=self.website, season=season, week=week, points=row.points + week / 100)
            for row in self.rows
        ]


@pytest.fixture(scope="module")
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fixture_rows(payload):
    return sleeper.parse(payload, 2026, 4)


@pytest.fixture
def settings(tmp_path, payload, fixture_rows):
    """A 2025 data directory with every fixture player, actual scores for weeks 10 and 11 and Sleeper's
    projections for them."""
    settings = Settings(season=2025, week=10, league_id="L2025", data_dir=tmp_path)
    players = []
    for row in payload:
        player = row["player"]
        players.append(
            (row["player_id"], player["first_name"], player["last_name"], player["position"], player["team"])
        )
    league = connect(settings, "league")
    league.execute(
        "CREATE TABLE nfl_players "
        "(player_id TEXT, first_name TEXT, last_name TEXT, position TEXT, team TEXT, fantasy_positions TEXT)"
    )
    league.executemany("INSERT INTO nfl_players VALUES (?, ?, ?, ?, ?, NULL)", players)
    league.execute("CREATE TABLE player_stats (season TEXT, week INTEGER, player_id TEXT, pts_ppr REAL)")
    league.executemany(
        "INSERT INTO player_stats VALUES (?, ?, ?, ?)", [(2025, week, "4984", 21.5) for week in PLAYED_WEEKS]
    )
    league.commit()
    league.close()

    projections = connect(settings, "projections")
    projections.executescript(scrape.PROJECTIONS_DDL)
    for week in PLAYED_WEEKS:
        scrape.insert_projections(projections, [replace(row, season=2025, week=week) for row in fixture_rows])
    projections.close()
    return settings


@pytest.fixture
def fftoday(monkeypatch, fixture_rows) -> FakeSource:
    fake = FakeSource("fftoday", fixture_rows)
    monkeypatch.setitem(sys.modules, "pipeline.sources.fftoday", SimpleNamespace(SOURCE=fake))
    return fake


def run_backfill(settings: Settings, weeks: list[int]) -> int:
    return backfill(settings, weeks, "fftoday", OPTIONS)


def stored_counts(settings: Settings, table: str) -> dict[int, tuple[int, int]]:
    """fftoday.com's rows in `table` by week, with how many of them carry a Sleeper id."""
    conn = connect(settings, "projections")
    matched = "COUNT(sleeper_player_id)" if table == "projections_with_sleeper" else "0"
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (table,)).fetchone()
    rows = []
    if exists:
        rows = conn.execute(
            f"SELECT week, COUNT(*), {matched} FROM {table} "
            "WHERE source_website = 'fftoday.com' AND season = 2025 GROUP BY week"
        ).fetchall()
    conn.close()
    return {week: (count, n_matched) for week, count, n_matched in rows}


def reverse_points(rows: list[Projection], position: str) -> list[Projection]:
    """Give the best player at `position` the worst player's points, the second best the second worst, and so on."""
    ranked = sorted([row for row in rows if row.position == position], key=lambda row: row.points)
    points = [row.points for row in reversed(ranked)]
    reversed_rows = [replace(row, points=new_points) for row, new_points in zip(ranked, points, strict=True)]
    return [row for row in rows if row.position != position] + reversed_rows


def printed(lines: list[str], cells: str) -> bool:
    """Whether a table line holds exactly these space-separated cells, however wide its columns are."""
    return any(re.sub(r" {2,}", " ", line) == cells for line in lines)


def test_value_agreement_failure_is_advisory_and_the_week_is_stored(settings, fftoday, fixture_rows, capsys):
    fftoday.rows = reverse_points(fixture_rows, "QB")

    assert run_backfill(settings, [10]) == 0

    output = capsys.readouterr().out
    assert stored_counts(settings, "projections") == {10: (188, 0)}
    assert stored_counts(settings, "projections_with_sleeper") == {10: (188, 188)}
    assert "  [backfill] week 10: stored 188 rows, 188 matched to Sleeper players" in output
    assert "  [backfill] week 10: value_agreement fail: QB r=-" in output
    assert printed(output.splitlines(), "10 stored 188 188 fail")


def test_week_without_actual_scores_is_refused_and_the_others_still_store(settings, fftoday, capsys):
    assert run_backfill(settings, [10, 12]) == 1

    output = capsys.readouterr().out
    assert "  [backfill] week 12: no actual scores in league.db; backfill is for weeks already played" in output
    assert stored_counts(settings, "projections") == {10: (188, 0)}
    assert printed(output.splitlines(), "12 not played - - -")


def test_structural_failure_refuses_the_week(settings, fftoday, fixture_rows, capsys):
    fftoday.rows = [row for row in fixture_rows if row.position != "WR"]

    assert run_backfill(settings, [10]) == 1

    output = capsys.readouterr().out
    assert "  [backfill] week 10: refused, position_counts" in output
    assert stored_counts(settings, "projections") == {}
    assert stored_counts(settings, "projections_with_sleeper") == {}
    assert printed(output.splitlines(), "10 refused - - ok")


def test_fetch_that_raises_refuses_the_week(settings, fftoday, capsys):
    fftoday.error = ConnectionError("www.fftoday.com timed out")

    assert run_backfill(settings, [10]) == 1

    output = capsys.readouterr().out
    assert "  [backfill] week 10: refused, fetch" in output
    assert stored_counts(settings, "projections") == {}
    assert printed(output.splitlines(), "10 refused - - -")


def test_every_week_in_the_range_is_stored_and_summarised(settings, fftoday, capsys):
    assert run_backfill(settings, PLAYED_WEEKS) == 0

    lines = capsys.readouterr().out.splitlines()
    assert stored_counts(settings, "projections_with_sleeper") == {10: (188, 188), 11: (188, 188)}
    assert printed(lines, "week status rows matched value_agreement")
    assert printed(lines, "10 stored 188 188 ok")
    assert printed(lines, "11 stored 188 188 ok")
    assert all(line.isascii() for line in lines)
