import json
import re
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.db import connect
from pipeline.runner import StepContext, StepResult
from pipeline.settings import Settings
from pipeline.sources import SOURCE_NAMES, sleeper
from pipeline.sources.base import POSITIONS, Projection
from pipeline.steps import scrape

FIXTURE = Path(__file__).parent / "fixtures" / "sleeper" / "projections_2026_w4.json"
WEBSITES = [f"{name}.com" for name in SOURCE_NAMES]


class FakeSource:
    """Serves the Sleeper fixture's players under its own website, stamped with the requested week."""

    positions = POSITIONS

    def __init__(self, name: str, rows: list[Projection]):
        self.name = name
        self.website = f"{name}.com"
        self.rows = rows
        self.error = None
        self.fetches = 0

    def fetch(self, season: int, week: int) -> list[Projection]:
        self.fetches += 1
        if self.error is not None:
            raise self.error
        return [replace(row, source=self.website, week=week) for row in self.rows]


@pytest.fixture(scope="module")
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fixture_rows(payload):
    return sleeper.parse(payload, 2026, 4)


@pytest.fixture
def settings(tmp_path, payload):
    """Settings over a data directory whose league.db lists every player in the fixture."""
    settings = Settings(season=2026, week=4, league_id="L2026", data_dir=tmp_path)
    players = []
    for row in payload:
        player = row["player"]
        players.append(
            (row["player_id"], player["first_name"], player["last_name"], player["position"], player["team"])
        )
    conn = connect(settings, "league")
    conn.execute("CREATE TABLE nfl_players (player_id TEXT, first_name TEXT, last_name TEXT, position TEXT, team TEXT)")
    conn.executemany("INSERT INTO nfl_players VALUES (?, ?, ?, ?, ?)", players)
    conn.commit()
    conn.close()
    return settings


@pytest.fixture
def sources(monkeypatch, fixture_rows) -> dict[str, FakeSource]:
    fakes = {}
    for name in SOURCE_NAMES:
        fakes[name] = FakeSource(name, fixture_rows)
        monkeypatch.setitem(sys.modules, f"pipeline.sources.{name}", SimpleNamespace(SOURCE=fakes[name]))
    return fakes


def run_scrape(settings: Settings, requested: list[str] | None = None) -> StepResult:
    options = {"sources": requested, "no_charts": False, "dry_run": False}
    with StepContext(settings, run_id="2026w04-20260929T140000", options=options, step="scrape") as ctx:
        return scrape.run(ctx)


def stored_counts(settings: Settings, week: int = 4) -> dict[str, int]:
    conn = connect(settings, "projections")
    rows = conn.execute(
        "SELECT source_website, COUNT(*) FROM projections WHERE season = 2026 AND week = ? GROUP BY source_website",
        (week,),
    ).fetchall()
    conn.close()
    return {website: count for website, count in rows}


def stored_points(settings: Settings, website: str, first_name: str, last_name: str) -> list[tuple[str, float]]:
    conn = connect(settings, "projections")
    rows = conn.execute(
        "SELECT team, projected_points FROM projections "
        "WHERE source_website = ? AND player_first_name = ? AND player_last_name = ?",
        (website, first_name, last_name),
    ).fetchall()
    conn.close()
    return [tuple(row) for row in rows]


def entries_by_source(result: StepResult) -> dict[str, dict]:
    return {entry["source"]: entry for entry in result.summary["sources"]}


def checks_by_name(entry: dict) -> dict[str, dict]:
    return {check["name"]: check for check in entry["checks"]}


def scaled(rows: list[Projection], factor: float) -> list[Projection]:
    return [replace(row, points=round(row.points * factor, 2)) for row in rows]


def printed(lines: list[str], cells: str) -> bool:
    """Whether a table line holds exactly these space-separated cells, however wide its columns are."""
    return any(re.sub(r" {2,}", " ", line) == cells for line in lines)


def test_full_run_stores_every_source_that_passes(settings, sources):
    result = run_scrape(settings)

    assert stored_counts(settings) == {website: 188 for website in WEBSITES}
    assert [entry["status"] for entry in result.summary["sources"]] == ["ok"] * 6
    assert (result.summary["n_ok_sources"], result.summary["dropped"]) == (6, [])
    assert result.warnings == []


def test_summary_lists_every_source_with_its_checks(settings, sources):
    result = run_scrape(settings)

    summary = result.summary
    espn = entries_by_source(result)["espn.com"]
    assert json.loads(json.dumps(summary)) == summary
    assert [entry["source"] for entry in summary["sources"]] == WEBSITES
    assert set(espn) == {"source", "status", "n_rows", "elapsed_s", "checks"}
    assert espn["n_rows"] == 188
    assert list(checks_by_name(espn)) == [
        "position_agreement",
        "duplicate_positions",
        "position_counts",
        "value_agreement",
        "team_codes",
        "week_stamp",
        "freshness",
        "top_players",
    ]
    assert set(espn["checks"][0]) == {"name", "status", "detail"}


def test_sleeper_runs_first_so_the_others_are_checked_against_it(settings, sources):
    entries = entries_by_source(run_scrape(settings, ["espn", "sleeper"]))

    sleeper_value = checks_by_name(entries["sleeper.com"])["value_agreement"]
    espn_value = checks_by_name(entries["espn.com"])["value_agreement"]
    assert sleeper_value["detail"] == "n/a: no Sleeper projections to compare with"
    assert espn_value["detail"].startswith("QB r=1.00 MAD=0.00 n=24, RB r=1.00")


def test_failing_source_is_dropped_and_its_stale_rows_deleted(settings, sources, fixture_rows):
    run_scrape(settings)
    sources["espn"].rows = [row for row in fixture_rows if row.position != "WR"]

    result = run_scrape(settings)

    assert stored_counts(settings) == {website: 188 for website in WEBSITES if website != "espn.com"}
    assert entries_by_source(result)["espn.com"]["status"] == "fail"
    assert (result.summary["n_ok_sources"], result.summary["dropped"]) == (5, ["espn.com"])
    assert result.warnings == ["dropped espn.com (position_counts)"]


def test_fetch_that_raises_fails_only_its_own_source(settings, sources):
    sources["fanduel"].error = ConnectionError("fanduel.com timed out\nCall log: waiting for the projections")

    result = run_scrape(settings)

    fanduel = entries_by_source(result)["fanduel.com"]
    assert (fanduel["status"], fanduel["n_rows"]) == ("fail", 0)
    assert fanduel["checks"] == [
        {"name": "fetch", "status": "fail", "detail": "ConnectionError: fanduel.com timed out"}
    ]
    assert sorted(stored_counts(settings)) == sorted(website for website in WEBSITES if website != "fanduel.com")
    assert result.warnings == ["dropped fanduel.com (fetch)"]


def test_source_repeating_last_weeks_numbers_is_dropped(settings, sources, fixture_rows):
    run_scrape(replace(settings, week=3))
    for name in SOURCE_NAMES:
        if name != "espn":
            sources[name].rows = scaled(fixture_rows, 1.1)

    result = run_scrape(settings)

    assert result.warnings == ["dropped espn.com (freshness)"]
    assert "espn.com" not in stored_counts(settings)
    assert stored_counts(settings, week=3)["espn.com"] == 188


def test_full_run_fails_without_sleeper(settings, sources):
    sources["sleeper"].error = ConnectionError("api.sleeper.com unreachable")

    with pytest.raises(RuntimeError, match="needs 3 usable sources including sleeper.com"):
        run_scrape(settings)


def test_full_run_needs_three_usable_sources(settings, sources):
    for name in ["fantasypros", "firstdown", "fanduel"]:
        sources[name].error = TimeoutError("no response")
    assert run_scrape(settings).summary["n_ok_sources"] == 3

    sources["fantasysharks"].error = TimeoutError("no response")

    with pytest.raises(RuntimeError, match=r"usable: sleeper.com, espn.com; dropped: fantasysharks.com \(fetch\)"):
        run_scrape(settings)


def test_subset_run_fails_when_a_requested_source_fails(settings, sources):
    sources["fanduel"].error = ConnectionError("fanduel.com timed out")

    with pytest.raises(RuntimeError, match=r"requested sources failed: fanduel.com \(fetch\)"):
        run_scrape(settings, ["espn", "fanduel"])


def test_subset_run_lists_the_sources_it_left_alone_as_kept(settings, sources):
    run_scrape(settings, ["sleeper", "espn"])

    result = run_scrape(settings, ["fantasysharks"])

    summary = result.summary
    assert [(entry["source"], entry["status"], entry["n_rows"]) for entry in summary["sources"]] == [
        ("sleeper.com", "kept", 188),
        ("espn.com", "kept", 188),
        ("fantasysharks.com", "ok", 188),
        ("fantasypros.com", "kept", 0),
        ("firstdown.com", "kept", 0),
        ("fanduel.com", "kept", 0),
    ]
    kept = summary["sources"][0]
    assert (kept["elapsed_s"], kept["checks"]) == (0, [])
    assert summary["n_ok_sources"] == 3
    assert [sources[name].fetches for name in SOURCE_NAMES] == [1, 1, 1, 0, 0, 0]
    assert result.warnings == []


def test_rerun_replaces_the_weeks_rows(settings, sources, fixture_rows):
    run_scrape(settings)
    sources["espn"].rows = scaled(fixture_rows, 1.1)

    run_scrape(settings)

    assert stored_points(settings, "espn.com", "Josh", "Allen") == [("BUF", 25.6)]
    assert stored_counts(settings)["espn.com"] == 188


def test_namesakes_at_one_position_keep_the_better_projection(settings, sources, fixture_rows):
    josh_allen = next(row for row in fixture_rows if (row.first_name, row.last_name) == ("Josh", "Allen"))
    sources["espn"].rows = [replace(josh_allen, team="NYJ", points=21.0), *fixture_rows]

    run_scrape(settings)

    assert stored_points(settings, "espn.com", "Josh", "Allen") == [("BUF", 23.27)]


def test_missing_player_table_asks_for_the_league_step(tmp_path, sources):
    settings = Settings(season=2026, week=4, league_id="L2026", data_dir=tmp_path)

    with pytest.raises(RuntimeError, match="run the league step first"):
        run_scrape(settings)


def test_prints_checks_and_top_players_in_ascii(settings, sources, capsys):
    run_scrape(settings, ["sleeper"])

    lines = capsys.readouterr().out.splitlines()
    assert all(line.isascii() for line in lines)
    assert any(line.startswith("  [scrape] sleeper.com: ok, 188 rows in ") for line in lines)
    assert printed(lines, "value_agreement ok n/a: no Sleeper projections to compare with")
    assert printed(lines, "QB team pts RB team pts WR team pts")
    assert printed(lines, "Josh Allen BUF 23.3 Jahmyr Gibbs DET 23.2 Jaxon Smith-Njigba SEA 20.3")
    assert printed(lines, "TE team pts K team pts DEF team pts")
    assert printed(lines, "espn.com kept 0 0.0")
