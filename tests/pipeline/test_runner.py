import itertools
import json
import re
import sqlite3
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from pipeline import runner
from pipeline.db import connect, ensure_run_tables
from pipeline.runner import (
    StepContext,
    StepFailed,
    StepResult,
    delete_source_projections,
    latest_step_rows,
    make_run_id,
    record_review,
    run_steps,
)
from pipeline.settings import Settings
from pipeline.sources import load_source
from pipeline.steps import load_step

REAL_GIT_SHA = runner.git_sha


@pytest.fixture
def settings(tmp_path):
    return Settings(season=2026, week=4, league_id="L2026", data_dir=tmp_path)


@pytest.fixture(autouse=True)
def ticking_clock(monkeypatch):
    """Every clock read is one second after the previous one, so run ids and durations are predictable."""
    start = datetime(2026, 9, 26, 14, 0, 0, tzinfo=UTC)
    moments = (start + timedelta(seconds=tick) for tick in itertools.count())
    monkeypatch.setattr(runner, "utc_now", lambda: next(moments))
    monkeypatch.setattr(runner, "git_sha", lambda: "abc1234")


def install_steps(monkeypatch, steps: dict) -> list[str]:
    """Serve fake step modules whose run functions come from `steps`; returns the names as they run."""
    called = []

    def load_fake_step(name):
        def run(ctx):
            called.append(name)
            return steps[name](ctx)

        return SimpleNamespace(NAME=name, run=run)

    monkeypatch.setattr(runner, "load_step", load_fake_step)
    return called


def ok_step(ctx):
    return StepResult(summary={"rosters": 12})


def warn_step(ctx):
    return StepResult(
        summary={"sources": [{"source": "espn.com", "status": "fail"}]},
        warnings=["espn.com failed verification", "fanduel.com returned no kickers"],
        charts=["scrape_counts.png"],
    )


def failing_step(ctx):
    raise RuntimeError("boom")


def step_rows(settings) -> dict[str, dict]:
    conn = connect(settings, "pipeline")
    rows = {row["step"]: dict(row) for row in conn.execute("SELECT * FROM pipeline_steps")}
    conn.close()
    return rows


def run_row(settings, run_id) -> dict:
    conn = connect(settings, "pipeline")
    row = dict(conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone())
    conn.close()
    return row


def test_run_id_is_season_week_and_start_time():
    run_id = make_run_id(2026, 4, datetime(2026, 9, 26, 14, 5, 9, tzinfo=UTC))

    assert run_id == "2026w04-20260926T140509"
    assert re.fullmatch(r"\d{4}w\d{2}-\d{8}T\d{6}", run_id)


def test_ok_and_warn_steps_are_recorded(monkeypatch, settings):
    install_steps(monkeypatch, {"league": ok_step, "scrape": warn_step})

    outcome = run_steps(settings, ["league", "scrape"], {})

    rows = step_rows(settings)
    assert rows["league"]["status"] == "ok"
    assert json.loads(rows["league"]["summary"]) == {"rosters": 12}
    assert json.loads(rows["league"]["warnings"]) == []
    assert rows["league"]["duration_s"] == 1.0
    assert rows["scrape"]["status"] == "warn"
    assert json.loads(rows["scrape"]["summary"]) == {"sources": [{"source": "espn.com", "status": "fail"}]}
    assert json.loads(rows["scrape"]["warnings"]) == [
        "espn.com failed verification",
        "fanduel.com returned no kickers",
    ]
    assert json.loads(rows["scrape"]["charts"]) == ["scrape_counts.png"]
    assert rows["scrape"]["error"] is None

    assert outcome.status == "warn"
    assert outcome.run_id == "2026w04-20260926T140000"
    run = run_row(settings, outcome.run_id)
    assert (run["season"], run["week"], run["status"]) == (2026, 4, "warn")
    assert json.loads(run["steps"]) == ["league", "scrape"]
    assert run["git_sha"] == "abc1234"
    assert run["started_at"] == "2026-09-26T14:00:00+00:00"
    assert run["finished_at"] == "2026-09-26T14:00:05+00:00"
    assert run["error"] is None


def test_a_failed_step_stops_the_run(monkeypatch, settings):
    called = install_steps(monkeypatch, {"league": ok_step, "scrape": failing_step, "clean": ok_step})

    outcome = run_steps(settings, ["league", "scrape", "clean"], {})

    assert outcome.status == "failed"
    assert called == ["league", "scrape"]
    rows = step_rows(settings)
    assert set(rows) == {"league", "scrape"}
    assert rows["scrape"]["status"] == "failed"
    assert rows["scrape"]["error"].startswith("Traceback")
    assert "RuntimeError: boom" in rows["scrape"]["error"]
    assert rows["scrape"]["summary"] is None
    assert run_row(settings, outcome.run_id)["error"] == "scrape: RuntimeError: boom"


def test_a_step_may_fail_with_its_summary_on_record(monkeypatch, settings):
    def validate(ctx):
        raise StepFailed("2 of 3 checks failed", summary={"checks": [{"name": "owners", "status": "fail"}]})

    install_steps(monkeypatch, {"validate": validate})

    outcome = run_steps(settings, ["validate"], {})

    assert outcome.status == "failed"
    row = step_rows(settings)["validate"]
    assert row["error"] == "2 of 3 checks failed"
    assert json.loads(row["summary"]) == {"checks": [{"name": "owners", "status": "fail"}]}
    assert run_row(settings, outcome.run_id)["error"] == "validate: 2 of 3 checks failed"


def test_a_summary_that_is_not_json_fails_the_step(monkeypatch, settings):
    install_steps(monkeypatch, {"league": lambda ctx: StepResult(summary={"fetched_at": datetime(2026, 9, 26)})})

    outcome = run_steps(settings, ["league"], {})

    assert outcome.status == "failed"
    assert "is not JSON serializable" in step_rows(settings)["league"]["error"]


def test_steps_run_in_canonical_order(monkeypatch, settings):
    called = install_steps(monkeypatch, {"league": ok_step, "clean": ok_step, "stats": ok_step})

    run_steps(settings, ["stats", "league", "clean"], {})

    assert called == ["league", "clean", "stats"]


def test_steps_see_their_name_the_run_id_and_the_options(monkeypatch, settings):
    seen = {}

    def inspect_context(ctx):
        seen.update(step=ctx.step, run_id=ctx.run_id, options=ctx.options, week=ctx.settings.week)
        return StepResult(summary={})

    install_steps(monkeypatch, {"scrape": inspect_context})

    outcome = run_steps(settings, ["scrape"], {"sources": ["espn"], "no_charts": True})

    assert seen == {
        "step": "scrape",
        "run_id": outcome.run_id,
        "options": {"sources": ["espn"], "no_charts": True},
        "week": 4,
    }


def test_run_prints_progress_and_a_summary_table(monkeypatch, settings, capsys):
    install_steps(monkeypatch, {"league": ok_step, "scrape": failing_step})

    run_steps(settings, ["league", "scrape", "clean"], {})

    captured = capsys.readouterr()
    assert "[1/3] league" in captured.out
    assert "[2/3] scrape" in captured.out
    assert re.search(r"^scrape\s+failed\s+1\.0s\s+RuntimeError: boom$", captured.out, re.MULTILINE)
    assert "Not run: clean" in captured.out
    assert captured.out.rstrip().endswith("Run 2026w04-20260926T140000: failed")
    assert captured.out.isascii()
    assert "RuntimeError: boom" in captured.err


def test_warnings_are_printed_and_summarised(monkeypatch, settings, capsys):
    install_steps(monkeypatch, {"scrape": warn_step})

    run_steps(settings, ["scrape"], {})

    out = capsys.readouterr().out
    assert "  [scrape] warning: fanduel.com returned no kickers" in out
    assert re.search(r"^scrape\s+warn\s+1\.0s\s+espn\.com failed verification \(\+1 more\)$", out, re.MULTILINE)


def test_step_context_shares_one_connection_per_database_and_closes_it(settings, tmp_path):
    with StepContext(settings, "run-1", {}, step="league") as ctx:
        league = ctx.db("league")
        assert ctx.db("league") is league
        assert league.row_factory is sqlite3.Row

    assert (tmp_path / "databases" / "league.db").exists()
    with pytest.raises(sqlite3.ProgrammingError):
        league.execute("SELECT 1")


def test_log_prefixes_the_step_name(settings, capsys):
    StepContext(settings, "run-1", {}, step="scrape").log("fetched 250 rows")

    assert capsys.readouterr().out == "  [scrape] fetched 250 rows\n"


def test_latest_step_rows_prefer_the_most_recent_run(monkeypatch, settings):
    install_steps(monkeypatch, {"league": ok_step, "scrape": failing_step})
    first = run_steps(settings, ["league", "scrape"], {})
    install_steps(monkeypatch, {"scrape": warn_step})
    second = run_steps(settings, ["scrape"], {})
    run_steps(replace(settings, week=5), ["scrape"], {})

    conn = connect(settings, "pipeline")
    latest = latest_step_rows(conn, 2026, 4)
    conn.close()

    assert set(latest) == {"league", "scrape"}
    assert latest["league"]["run_id"] == first.run_id
    assert latest["scrape"]["run_id"] == second.run_id
    assert latest["scrape"]["status"] == "warn"


def test_record_review_keeps_one_verdict_per_source_and_week(settings):
    conn = connect(settings, "pipeline")
    ensure_run_tables(conn)

    record_review(conn, 2026, 4, "espn.com", "ok", "top 15 look right")
    record_review(conn, 2026, 4, "espn.com", "reject", "last week's numbers")
    record_review(conn, 2026, 4, "sleeper.com", "ok", None)

    rows = conn.execute("SELECT source, verdict, note, reviewed_at FROM source_reviews ORDER BY source").fetchall()
    conn.close()
    assert [tuple(row) for row in rows] == [
        ("espn.com", "reject", "last week's numbers", "2026-09-26T14:00:01+00:00"),
        ("sleeper.com", "ok", None, "2026-09-26T14:00:02+00:00"),
    ]


def create_projections(conn):
    conn.execute("CREATE TABLE projections (source_website TEXT, season INTEGER, week INTEGER, last_name TEXT)")
    conn.executemany(
        "INSERT INTO projections VALUES (?, ?, ?, ?)",
        [
            ("espn.com", 2026, 4, "Allen"),
            ("espn.com", 2026, 4, "Chase"),
            ("espn.com", 2026, 3, "Allen"),
            ("espn.com", 2025, 4, "Allen"),
            ("sleeper.com", 2026, 4, "Allen"),
        ],
    )
    conn.commit()


def test_delete_source_projections_only_touches_that_source_and_week(tmp_path):
    conn = sqlite3.connect(tmp_path / "projections.db")
    create_projections(conn)

    deleted = delete_source_projections(conn, 2026, 4, "espn.com")

    remaining = conn.execute("SELECT source_website, season, week FROM projections ORDER BY 1, 2, 3").fetchall()
    conn.close()
    assert deleted == 2
    assert remaining == [("espn.com", 2025, 4), ("espn.com", 2026, 3), ("sleeper.com", 2026, 4)]


def test_delete_source_projections_before_any_scrape(tmp_path):
    conn = sqlite3.connect(tmp_path / "projections.db")

    assert delete_source_projections(conn, 2026, 4, "espn.com") == 0
    conn.close()


def test_git_sha_is_empty_without_git(monkeypatch):
    def missing_git(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(runner.subprocess, "run", missing_git)

    assert REAL_GIT_SHA() == ""


def test_load_step_imports_the_step_module_by_name(monkeypatch):
    module = SimpleNamespace(NAME="league")
    monkeypatch.setitem(sys.modules, "pipeline.steps.league", module)

    assert load_step("league") is module


def test_load_source_returns_the_module_source(monkeypatch):
    source = object()
    monkeypatch.setitem(sys.modules, "pipeline.sources.espn", SimpleNamespace(SOURCE=source))

    assert load_source("espn") is source
