import itertools
import sys
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

import pipeline.__main__ as cli
from pipeline import runner
from pipeline.db import connect
from pipeline.runner import StepResult
from pipeline.settings import Settings, SettingsError
from pipeline.steps import DEFAULT_STEPS, STEP_ORDER, resolve_steps


@pytest.fixture
def settings(tmp_path):
    return Settings(season=2026, week=4, league_id="L2026", data_dir=tmp_path)


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch, settings):
    def load_settings(week=None, season=None):
        return replace(settings, week=week or settings.week, season=season or settings.season)

    monkeypatch.setattr(cli, "load_settings", load_settings)


@pytest.fixture(autouse=True)
def ticking_clock(monkeypatch):
    start = datetime(2026, 9, 26, 14, 0, 0, tzinfo=UTC)
    moments = (start + timedelta(seconds=tick) for tick in itertools.count())
    monkeypatch.setattr(runner, "utc_now", lambda: next(moments))
    monkeypatch.setattr(runner, "git_sha", lambda: "abc1234")


def install_steps(monkeypatch, steps: dict) -> list[tuple[str, dict]]:
    """Serve fake step modules whose run functions come from `steps`; returns (name, options) as they run."""
    calls = []

    def load_fake_step(name):
        def run(ctx):
            calls.append((name, ctx.options))
            return steps[name](ctx)

        return SimpleNamespace(NAME=name, run=run)

    monkeypatch.setattr(runner, "load_step", load_fake_step)
    return calls


def ok_step(ctx):
    return StepResult(summary={"rows": 1})


def warn_step(ctx):
    return StepResult(summary={"rows": 1}, warnings=["espn.com failed verification"])


def failing_step(ctx):
    raise RuntimeError("boom")


def printed_rows(output: str) -> dict[str, list[str]]:
    """Index printed table lines by their first cell."""
    rows = {}
    for line in output.splitlines():
        cells = line.split()
        if cells:
            rows[cells[0]] = cells
    return rows


def create_projections(settings):
    conn = connect(settings, "projections")
    conn.execute("CREATE TABLE projections (source_website TEXT, season INTEGER, week INTEGER, last_name TEXT)")
    conn.executemany(
        "INSERT INTO projections VALUES (?, ?, ?, ?)",
        [
            ("espn.com", 2026, 4, "Allen"),
            ("espn.com", 2026, 4, "Chase"),
            ("espn.com", 2026, 3, "Allen"),
            ("sleeper.com", 2026, 4, "Allen"),
        ],
    )
    conn.commit()
    conn.close()


def remaining_projections(settings) -> list[tuple]:
    conn = connect(settings, "projections")
    rows = conn.execute("SELECT source_website, week, last_name FROM projections ORDER BY 1, 2, 3").fetchall()
    conn.close()
    return [tuple(row) for row in rows]


def reviews(settings) -> list[tuple]:
    conn = connect(settings, "pipeline")
    rows = conn.execute("SELECT season, week, source, verdict, note FROM source_reviews").fetchall()
    conn.close()
    return [tuple(row) for row in rows]


def test_run_exits_zero_when_every_step_is_ok_or_warn(monkeypatch):
    install_steps(monkeypatch, {"league": ok_step, "scrape": warn_step})

    assert cli.main(["run", "--week", "4", "--steps", "league,scrape"]) == 0


def test_run_exits_one_and_stops_when_a_step_fails(monkeypatch, capsys):
    calls = install_steps(monkeypatch, {"league": failing_step, "scrape": ok_step})

    assert cli.main(["run", "--week", "4", "--steps", "league,scrape"]) == 1
    assert [name for name, _ in calls] == ["league"]
    assert "Not run: scrape" in capsys.readouterr().out


def test_run_passes_sources_and_chart_options_to_steps(monkeypatch):
    calls = install_steps(monkeypatch, {"scrape": ok_step})

    cli.main(["run", "--week", "4", "--steps", "scrape", "--sources", "espn,sleeper", "--no-charts"])

    assert calls == [("scrape", {"sources": ["espn", "sleeper"], "no_charts": True, "dry_run": False})]


def test_run_options_default_to_every_source_with_charts(monkeypatch):
    calls = install_steps(monkeypatch, {"scrape": ok_step})

    cli.main(["run", "--week", "4", "--steps", "scrape"])

    assert calls == [("scrape", {"sources": None, "no_charts": False, "dry_run": False})]


def test_run_from_a_step_runs_the_rest_without_publish(monkeypatch):
    calls = install_steps(monkeypatch, {"playoffs": ok_step, "validate": ok_step})

    assert cli.main(["run", "--week", "4", "--from", "playoffs"]) == 0
    assert [name for name, _ in calls] == ["playoffs", "validate"]


def test_run_uses_the_requested_week_and_season(monkeypatch, settings):
    install_steps(monkeypatch, {"league": ok_step})

    cli.main(["run", "--week", "12", "--season", "2025", "--steps", "league"])

    conn = connect(settings, "pipeline")
    run = conn.execute("SELECT run_id, season, week FROM pipeline_runs").fetchone()
    conn.close()
    assert tuple(run) == ("2025w12-20260926T140000", 2025, 12)


def test_run_rejects_unknown_steps(capsys):
    assert cli.main(["run", "--week", "4", "--steps", "league,scrap"]) == 2
    assert "ERROR: unknown steps ['scrap']" in capsys.readouterr().err


@pytest.mark.parametrize(
    "argv",
    [
        ["run", "--week", "4", "--sources", "espn,yahoo"],
        ["run", "--week", "4", "--steps", "league", "--from", "clean"],
        ["run", "--steps", "league"],
        ["review", "--week", "4", "--source", "espn.com", "--verdict", "maybe"],
    ],
)
def test_usage_errors_exit_two(argv):
    with pytest.raises(SystemExit) as exited:
        cli.main(argv)

    assert exited.value.code == 2


def test_resolve_steps_uses_the_canonical_order():
    assert resolve_steps("stats,league,publish", None) == ["league", "stats", "publish"]


def test_resolve_steps_from_runs_the_remaining_default_steps():
    assert resolve_steps(None, "odds") == ["odds", "playoffs", "validate"]


def test_resolve_steps_defaults_to_everything_but_publish():
    assert resolve_steps(None, None) == DEFAULT_STEPS
    assert DEFAULT_STEPS == STEP_ORDER[:-1]
    assert STEP_ORDER[-1] == "publish"


def test_resolve_steps_rejects_unknown_names():
    with pytest.raises(ValueError, match="unknown steps"):
        resolve_steps("league,scrap", None)
    with pytest.raises(ValueError, match="--from must be one of"):
        resolve_steps(None, "publish")


def test_status_shows_the_latest_row_for_every_step(monkeypatch, settings, capsys):
    install_steps(monkeypatch, {"league": ok_step, "scrape": failing_step})
    cli.main(["run", "--week", "4", "--steps", "league,scrape"])
    install_steps(monkeypatch, {"scrape": warn_step})
    cli.main(["run", "--week", "4", "--steps", "scrape"])
    conn = connect(settings, "pipeline")
    first_run, second_run = [row["run_id"] for row in conn.execute("SELECT run_id FROM pipeline_runs ORDER BY 1")]
    conn.close()
    capsys.readouterr()

    assert cli.main(["status", "--week", "4"]) == 0

    output = capsys.readouterr().out
    rows = printed_rows(output)
    assert output.startswith("Season 2026 week 4\n")
    assert rows["league"][:3] == ["league", "ok", "1.0s"]
    assert first_run in rows["league"]
    assert rows["scrape"][:3] == ["scrape", "warn", "1.0s"]
    assert second_run in rows["scrape"]
    assert rows["clean"] == ["clean", "-"]
    assert rows["publish"] == ["publish", "-"]
    assert output.isascii()


def test_review_ok_records_the_verdict_and_keeps_the_projections(settings):
    create_projections(settings)

    argv = ["review", "--week", "4", "--source", "espn.com", "--verdict", "ok", "--note", "top 15 look right"]
    assert cli.main(argv) == 0

    assert reviews(settings) == [(2026, 4, "espn.com", "ok", "top 15 look right")]
    assert len(remaining_projections(settings)) == 4


def test_review_reject_deletes_that_sources_rows_for_the_week(settings, capsys):
    create_projections(settings)

    argv = ["review", "--week", "4", "--source", "espn.com", "--verdict", "reject", "--note", "stale numbers"]
    assert cli.main(argv) == 0

    assert reviews(settings) == [(2026, 4, "espn.com", "reject", "stale numbers")]
    assert remaining_projections(settings) == [("espn.com", 3, "Allen"), ("sleeper.com", 4, "Allen")]
    output = capsys.readouterr().out
    assert "Deleted 2 espn.com projections" in output
    assert "python -m pipeline run --week 4 --from clean" in output


def test_fit_model_passes_the_season_weeks_and_version(monkeypatch, settings):
    calls = []
    monkeypatch.setitem(
        sys.modules, "pipeline.model.fit", SimpleNamespace(fit_and_write=lambda *args: calls.append(args))
    )

    assert cli.main(["fit-model", "--season", "2025", "--weeks", "10-16", "--out", "v2"]) == 0
    assert calls == [(settings, 2025, [10, 11, 12, 13, 14, 15, 16], "v2")]


@pytest.mark.parametrize(("value", "weeks"), [("12", [12]), ("10-12", [10, 11, 12]), ("7-7", [7])])
def test_week_range_accepts_a_week_or_a_range(value, weeks):
    assert cli.week_range(value) == weeks


@pytest.mark.parametrize("value", ["16-10", "ten", "10-", "-3", "10-12-14"])
def test_week_range_rejects_anything_else(value):
    with pytest.raises(SystemExit) as exited:
        cli.main(["fit-model", "--season", "2025", "--weeks", value, "--out", "v2"])

    assert exited.value.code == 2


def test_migrate_legacy_runs_the_migration(monkeypatch, settings):
    calls = []
    monkeypatch.setitem(sys.modules, "pipeline.legacy", SimpleNamespace(migrate=calls.append))

    assert cli.main(["migrate-legacy"]) == 0
    assert calls == [settings]


def test_settings_errors_exit_one_with_the_reason(monkeypatch, capsys):
    def unavailable_settings(week=None, season=None):
        raise SettingsError("LEAGUE_ID is not set")

    monkeypatch.setattr(cli, "load_settings", unavailable_settings)

    assert cli.main(["status", "--week", "4"]) == 1
    assert capsys.readouterr().err == "ERROR: LEAGUE_ID is not set\n"
