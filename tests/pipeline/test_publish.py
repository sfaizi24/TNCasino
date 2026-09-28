import json
import re
import subprocess
from dataclasses import replace
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import StatementError

from pipeline import markets
from pipeline.db import DB_NAMES, connect, ensure_run_tables
from pipeline.runner import StepContext
from pipeline.settings import Settings
from pipeline.steps import publish, simulate
from pipeline.steps.publish import PublishError, read_tables, write_tables

RUN_ID = "2026w04-20260926T140000"
EARLIER_RUN_ID = "2026w04-20260926T100000"
LAST_SEASON_RUN_ID = "2025w16-20251217T160000"
FINISHED = datetime(2026, 9, 26, 14, 0, 47, tzinfo=UTC)

WEEK_3_SIMULATION = "2026w03-20260916T030000"
WEEK_4_SIMULATION = "2026w04-20260923T030000"
WEEK_4_RERUN = "2026w04-20260925T030000"
# Four sims of three teams, in the order simulate met the rosters; production stores them by ascending roster id.
SIMULATED_ROSTERS = [3, 1, 2]
SCORES = np.array(
    [[120.0, 101.25, 88.5], [99.5, 95.0, 110.75], [105.0, 130.5, 70.25], [111.25, 88.0, 92.0]], dtype=np.float32
)
STORED_TOTALS = [[101.25, 88.5, 120.0], [95.0, 110.75, 99.5], [130.5, 70.25, 105.0], [88.0, 92.0, 111.25]]


@pytest.fixture
def settings(tmp_path):
    return Settings(season=2026, week=4, league_id="L2026", data_dir=tmp_path)


@pytest.fixture
def local(settings):
    connections = {name: connect(settings, name) for name in DB_NAMES}
    ensure_run_tables(connections["pipeline"])
    yield connections
    for conn in connections.values():
        conn.close()


@pytest.fixture
def target_path(tmp_path):
    return tmp_path / "target.db"


@pytest.fixture
def target(target_path):
    engine = create_engine(f"sqlite:///{target_path.as_posix()}")
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def environment(monkeypatch, target_path):
    """Importing app loads the developer's .env, so pin everything publish reads from the environment."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{target_path.as_posix()}")
    monkeypatch.delenv("PUBLISH_CHARTS_TARGET", raising=False)
    monkeypatch.setattr(publish, "utc_now", lambda: FINISHED)


@pytest.fixture(autouse=True)
def scp_calls(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(publish.subprocess, "run", fake_run)
    return calls


def add_rows(conn, table, rows):
    pd.DataFrame(rows).to_sql(table, conn, if_exists="append", index=False)
    conn.commit()


def add_runs(pipeline, validate_status="ok"):
    """Run records as the runner leaves them while publish runs, beside an earlier run and one from last season."""
    add_rows(
        pipeline,
        "pipeline_runs",
        [
            {
                "run_id": LAST_SEASON_RUN_ID,
                "season": 2025,
                "week": 16,
                "started_at": "2025-12-17T16:00:00+00:00",
                "finished_at": "2025-12-17T16:03:00+00:00",
                "status": "ok",
                "steps": '["odds"]',
            },
            {
                "run_id": EARLIER_RUN_ID,
                "season": 2026,
                "week": 4,
                "started_at": "2026-09-26T10:00:00+00:00",
                "finished_at": "2026-09-26T10:00:09+00:00",
                "status": "failed",
                "steps": '["publish"]',
                "error": "publish: PublishError: chart upload failed",
            },
            {
                "run_id": RUN_ID,
                "season": 2026,
                "week": 4,
                "started_at": "2026-09-26T14:00:00+00:00",
                "status": "running",
                "steps": '["validate", "publish"]',
            },
        ],
    )
    add_rows(
        pipeline,
        "pipeline_steps",
        [
            {"run_id": LAST_SEASON_RUN_ID, "step": "odds", "started_at": "2025-12-17T16:00:00+00:00", "status": "ok"},
            {
                "run_id": EARLIER_RUN_ID,
                "step": "publish",
                "started_at": "2026-09-26T10:00:00+00:00",
                "finished_at": "2026-09-26T10:00:09+00:00",
                "duration_s": 9.0,
                "status": "failed",
                "warnings": "[]",
                "error": "PublishError: chart upload failed",
            },
            {
                "run_id": RUN_ID,
                "step": "validate",
                "started_at": "2026-09-26T14:00:01+00:00",
                "finished_at": "2026-09-26T14:00:04+00:00",
                "duration_s": 3.0,
                "status": validate_status,
            },
            {"run_id": RUN_ID, "step": "publish", "started_at": "2026-09-26T14:00:05+00:00", "status": "running"},
        ],
    )


def create_every_table(local):
    """Give every published table a local table, so that a publish has nothing to skip."""
    for database, source, _ in publish.TABLES:
        local[database].execute(f"CREATE TABLE IF NOT EXISTS {source} (week INTEGER)")


def add_simulation(settings, odds, run_id, week, created_at):
    """Save a run's draws and record it as the simulate step does; returns where the draws went."""
    draws_path = simulate.save_draws(replace(settings, week=week), run_id, SCORES, SIMULATED_ROSTERS)
    odds.execute(simulate.SIMULATION_RUNS_DDL)
    run = {
        "run_id": run_id,
        "season": 2026,
        "week": week,
        "seed": 1738,
        "n_sims": len(SCORES),
        "model_version": "v2.2",
        "n_teams": len(SIMULATED_ROSTERS),
        "draws_path": draws_path,
        "created_at": created_at,
    }
    add_rows(odds, "simulation_runs", [run])
    return draws_path


def stored_run_ids(target):
    return sorted(row["run_id"] for row in target_rows(target, "simulation_totals"))


def add_chart(settings, name):
    settings.images_dir.mkdir(parents=True, exist_ok=True)
    (settings.images_dir / name).write_bytes(b"")


def run_publish(settings, **options):
    with StepContext(settings, RUN_ID, options, step="publish") as ctx:
        return publish.run(ctx)


def target_rows(target, table):
    with target.connect() as conn:
        return [dict(row) for row in conn.execute(text(f"SELECT * FROM {table}")).mappings()]


def local_rows(conn, table, run_id):
    return [dict(row) for row in conn.execute(f"SELECT * FROM {table} WHERE run_id = ?", (run_id,))]


def current_run_rows(target):
    """Production's row for the current run and for its publish step."""
    run = next(row for row in target_rows(target, "pipeline_runs") if row["run_id"] == RUN_ID)
    steps = target_rows(target, "pipeline_steps")
    step = next(row for row in steps if row["run_id"] == RUN_ID and row["step"] == "publish")
    return run, step


def table_names(target):
    return set(inspect(target).get_table_names())


def test_sleeper_tables_publish_under_their_production_names(local, target):
    add_rows(local["league"], "rosters", [{"roster_id": 1, "league_id": "L2026", "owner_id": "u1"}])
    add_rows(local["league"], "users", [{"user_id": "u1", "username": "sam", "display_name": "Sam"}])
    add_rows(local["league"], "matchups", [{"league_id": "L2026", "week": 4, "roster_id": 1, "points": 101.5}])

    tables, _ = read_tables(local, 2026, "L2026")
    write_tables(target, tables)

    assert {"sleeper_rosters", "sleeper_users", "sleeper_matchups"} <= table_names(target)
    assert not {"rosters", "users", "matchups"} & table_names(target)
    assert target_rows(target, "sleeper_users") == [{"user_id": "u1", "username": "sam", "display_name": "Sam"}]


def test_tables_with_a_season_column_keep_only_that_season(local):
    # Legacy tables store the season as text, which SQLite still matches against an integer.
    local["projections"].execute("CREATE TABLE team_lineups (season TEXT, week INTEGER, owner TEXT)")
    add_rows(
        local["projections"],
        "team_lineups",
        [{"season": "2025", "week": 17, "owner": "sam"}, {"season": "2026", "week": 4, "owner": "sam"}],
    )
    add_rows(
        local["pipeline"],
        "source_reviews",
        [
            {"season": 2025, "week": 17, "source": "espn.com", "verdict": "ok", "reviewed_at": "2025-12-24"},
            {"season": 2026, "week": 4, "source": "espn.com", "verdict": "reject", "reviewed_at": "2026-09-26"},
        ],
    )

    tables, _ = read_tables(local, 2026, "L2026")

    assert tables["team_lineups"]["week"].tolist() == [4]
    assert tables["source_reviews"]["verdict"].tolist() == ["reject"]


def test_tables_without_a_season_column_keep_only_the_league(local):
    add_rows(
        local["league"], "rosters", [{"roster_id": 1, "league_id": "L2025"}, {"roster_id": 1, "league_id": "L2026"}]
    )
    add_rows(local["league"], "users", [{"user_id": "u1"}, {"user_id": "u2"}])

    tables, _ = read_tables(local, 2026, "L2026")

    assert tables["sleeper_rosters"]["league_id"].tolist() == ["L2026"]
    assert tables["sleeper_users"]["user_id"].tolist() == ["u1", "u2"]


def test_each_week_keeps_only_its_latest_run(local):
    # The run ids sort the opposite way to their creation times, so only created_at can pick the latest.
    add_rows(
        local["odds"],
        "betting_odds_team_ou",
        [
            {"season": 2026, "week": 3, "run_id": "b", "created_at": "2026-09-19 10:00:00", "owner": "sam"},
            {"season": 2026, "week": 4, "run_id": "c", "created_at": "2026-09-26 09:00:00", "owner": "sam"},
            {"season": 2026, "week": 4, "run_id": "c", "created_at": "2026-09-26 09:00:00", "owner": "ali"},
            {"season": 2026, "week": 4, "run_id": "a", "created_at": "2026-09-26 12:00:00", "owner": "sam"},
            {"season": 2026, "week": 4, "run_id": "a", "created_at": "2026-09-26 12:00:00", "owner": "ali"},
        ],
    )

    tables, _ = read_tables(local, 2026, "L2026")

    kept = tables["betting_odds_team_ou"][["week", "run_id", "owner"]]
    assert kept.to_dict("records") == [
        {"week": 3, "run_id": "b", "owner": "sam"},
        {"week": 4, "run_id": "a", "owner": "sam"},
        {"week": 4, "run_id": "a", "owner": "ali"},
    ]


def test_the_latest_run_is_chosen_within_the_season(local):
    add_rows(
        local["odds"],
        "betting_odds_matchup_ml",
        [
            {"season": 2025, "week": 4, "run_id": "run-2025", "created_at": "2025-09-28 10:00:00"},
            {"season": 2026, "week": 4, "run_id": "run-2026", "created_at": "2026-09-26 10:00:00"},
        ],
    )

    tables, _ = read_tables(local, 2025, "L2025")

    assert tables["betting_odds_matchup_ml"]["run_id"].tolist() == ["run-2025"]


def test_tables_without_created_at_take_run_times_from_simulation_runs(local):
    add_rows(
        local["odds"],
        "simulation_runs",
        [
            {"run_id": "a", "season": 2026, "week": 4, "created_at": "2026-09-26T12:00:00+00:00"},
            {"run_id": "b", "season": 2026, "week": 4, "created_at": "2026-09-26T09:00:00+00:00"},
        ],
    )
    add_rows(
        local["odds"],
        "team_distribution_curves",
        [
            {"season": 2026, "week": 4, "run_id": "b", "owner": "sam"},
            {"season": 2026, "week": 4, "run_id": "a", "owner": "sam"},
        ],
    )

    tables, _ = read_tables(local, 2026, "L2026")

    assert tables["team_distribution_curves"]["run_id"].tolist() == ["a"]
    assert tables["simulation_runs"]["run_id"].tolist() == ["a"]


@pytest.mark.parametrize("table", ["betting_odds_matchup_ou", "standings_probability_matrix"])
def test_matchup_totals_and_the_standings_matrix_publish_their_latest_run(local, target, table):
    add_rows(
        local["odds"],
        table,
        [
            {"season": 2026, "week": 4, "run_id": "a", "created_at": "2026-09-23 03:01:00"},
            {"season": 2026, "week": 4, "run_id": "b", "created_at": "2026-09-25 03:01:00"},
        ],
    )

    tables, _ = read_tables(local, 2026, "L2026")
    write_tables(target, tables)

    assert [row["run_id"] for row in target_rows(target, table)] == ["b"]


def test_the_flask_users_table_is_never_replaced(local, target):
    with target.begin() as conn:
        conn.execute(text("CREATE TABLE users (id INTEGER, email TEXT)"))
        conn.execute(text("INSERT INTO users VALUES (1, 'player@example.com')"))
    add_rows(local["league"], "users", [{"user_id": "u1", "username": "sam"}])

    tables, _ = read_tables(local, 2026, "L2026")
    write_tables(target, tables)

    assert target_rows(target, "users") == [{"id": 1, "email": "player@example.com"}]
    assert target_rows(target, "sleeper_users") == [{"user_id": "u1", "username": "sam"}]


@pytest.mark.parametrize("name", sorted(publish.PROTECTED_TABLES))
def test_write_tables_refuses_the_tables_flask_owns(target, name):
    with pytest.raises(PublishError, match=name):
        write_tables(target, {"sleeper_users": pd.DataFrame({"user_id": ["u1"]}), name: pd.DataFrame({"id": [1]})})

    assert table_names(target) == set()


def test_write_tables_refuses_to_replace_the_stored_score_matrices(target):
    tables = {"sleeper_users": pd.DataFrame({"user_id": ["u1"]}), "simulation_totals": pd.DataFrame({"run_id": ["a"]})}

    with pytest.raises(PublishError, match="refusing to replace append-only tables: simulation_totals"):
        write_tables(target, tables)

    assert table_names(target) == set()


@pytest.mark.parametrize(("validate_status", "run_status"), [("ok", "ok"), ("warn", "warn")])
def test_production_sees_the_current_run_as_finished(settings, local, target, validate_status, run_status):
    create_every_table(local)
    add_runs(local["pipeline"], validate_status)

    result = run_publish(settings, no_charts=True)

    run, step = current_run_rows(target)
    assert step["status"] == "ok"
    assert step["finished_at"] == "2026-09-26T14:00:47+00:00"
    assert step["duration_s"] == 42.0
    assert json.loads(step["summary"]) == result.summary
    assert json.loads(step["warnings"]) == []
    assert run["status"] == run_status
    assert run["finished_at"] == "2026-09-26T14:00:47+00:00"
    published = [table["name"] for table in result.summary["tables"]]
    assert published[-3:] == ["pipeline_runs", "pipeline_steps", "source_reviews"]


def test_other_runs_are_copied_as_recorded_and_the_local_record_is_untouched(settings, local, target):
    create_every_table(local)
    add_runs(local["pipeline"])

    run_publish(settings, no_charts=True)

    assert {row["run_id"] for row in target_rows(target, "pipeline_runs")} == {EARLIER_RUN_ID, RUN_ID}
    for table in ["pipeline_runs", "pipeline_steps"]:
        earlier = [row for row in target_rows(target, table) if row["run_id"] == EARLIER_RUN_ID]
        assert earlier == local_rows(local["pipeline"], table, EARLIER_RUN_ID)
    assert local_rows(local["pipeline"], "pipeline_runs", RUN_ID)[0]["status"] == "running"
    local_steps = {row["step"]: row["status"] for row in local_rows(local["pipeline"], "pipeline_steps", RUN_ID)}
    assert local_steps["publish"] == "running"


def test_a_table_missing_locally_is_skipped_with_a_warning(settings, local, target):
    create_every_table(local)
    local["projections"].execute("DROP TABLE team_accuracy")
    add_runs(local["pipeline"])

    result = run_publish(settings, no_charts=True)

    assert result.summary["skipped"] == ["team_accuracy"]
    assert result.warnings == ["team_accuracy skipped: its local table does not exist yet"]
    assert "team_accuracy" not in table_names(target)
    _, step = current_run_rows(target)
    assert step["status"] == "warn"


def test_a_new_run_stores_its_score_matrix_by_ascending_roster_id(settings, local, target):
    add_simulation(settings, local["odds"], WEEK_4_SIMULATION, 4, "2026-09-23T03:01:00+00:00")
    add_runs(local["pipeline"])

    result = run_publish(settings, no_charts=True)

    assert result.summary["totals_stored"] == [WEEK_4_SIMULATION]
    [row] = target_rows(target, "simulation_totals")
    assert {column: value for column, value in row.items() if column != "totals"} == {
        "run_id": WEEK_4_SIMULATION,
        "season": 2026,
        "week": 4,
        "created_at": "2026-09-23T03:01:00+00:00",
        "n_sims": 4,
        "roster_ids": "1,2,3",
    }
    assert markets.decode_totals(row["totals"], n_sims=4, n_teams=3).tolist() == STORED_TOTALS


def test_a_second_publish_leaves_a_stored_matrix_as_it_was(settings, local, target):
    add_simulation(settings, local["odds"], WEEK_4_SIMULATION, 4, "2026-09-23T03:01:00+00:00")
    add_runs(local["pipeline"])
    run_publish(settings, no_charts=True)
    first_publish = target_rows(target, "simulation_totals")
    # Different draws under the same run id would show in the matrix if it were ever written again.
    simulate.save_draws(settings, WEEK_4_SIMULATION, SCORES + 1, SIMULATED_ROSTERS)

    result = run_publish(settings, no_charts=True)

    assert result.summary["totals_stored"] == []
    assert target_rows(target, "simulation_totals") == first_publish


def test_only_runs_production_has_not_stored_are_read(monkeypatch, settings, local, target):
    add_simulation(settings, local["odds"], WEEK_3_SIMULATION, 3, "2026-09-16T03:01:00+00:00")
    add_simulation(settings, local["odds"], WEEK_4_SIMULATION, 4, "2026-09-23T03:01:00+00:00")
    add_runs(local["pipeline"])
    run_publish(settings, no_charts=True)
    rerun_path = add_simulation(settings, local["odds"], WEEK_4_RERUN, 4, "2026-09-25T03:01:00+00:00")
    original_load_draws = publish.load_draws
    reads = []

    def recording_load_draws(run_settings, draws_path):
        reads.append(draws_path)
        return original_load_draws(run_settings, draws_path)

    monkeypatch.setattr(publish, "load_draws", recording_load_draws)

    result = run_publish(settings, no_charts=True)

    assert reads == [rerun_path]
    assert result.summary["totals_stored"] == [WEEK_4_RERUN]
    # The rerun replaces week 4's first run everywhere except here, where bets placed on the first run are re-priced.
    assert stored_run_ids(target) == [WEEK_3_SIMULATION, WEEK_4_SIMULATION, WEEK_4_RERUN]
    assert [row["run_id"] for row in target_rows(target, "simulation_runs")] == [WEEK_3_SIMULATION, WEEK_4_RERUN]


def test_a_run_whose_draws_are_gone_is_skipped_with_a_warning(settings, local, target):
    draws_path = add_simulation(settings, local["odds"], WEEK_4_SIMULATION, 4, "2026-09-23T03:01:00+00:00")
    (settings.data_dir / draws_path).unlink()
    create_every_table(local)
    add_runs(local["pipeline"])

    result = run_publish(settings, no_charts=True)

    assert result.warnings == [f"run {WEEK_4_SIMULATION} has no draws at {draws_path}, so its matrix was not stored"]
    assert result.summary["totals_stored"] == []
    assert stored_run_ids(target) == []
    assert [row["run_id"] for row in target_rows(target, "simulation_runs")] == [WEEK_4_SIMULATION]
    _, step = current_run_rows(target)
    assert step["status"] == "warn"


def test_a_matrix_stays_stored_when_the_swap_after_it_fails(monkeypatch, settings, local, target):
    add_simulation(settings, local["odds"], WEEK_4_SIMULATION, 4, "2026-09-23T03:01:00+00:00")
    add_runs(local["pipeline"])

    def failing_swap(engine, tables):
        raise PublishError("the swap failed")

    monkeypatch.setattr(publish, "write_tables", failing_swap)

    with pytest.raises(PublishError, match="the swap failed"):
        run_publish(settings, no_charts=True)
    assert stored_run_ids(target) == [WEEK_4_SIMULATION]
    assert table_names(target) == {"simulation_totals"}


def test_no_staging_table_is_left_after_a_publish(local, target):
    add_rows(local["league"], "users", [{"user_id": "u1"}])

    tables, _ = read_tables(local, 2026, "L2026")
    write_tables(target, tables)

    assert [name for name in table_names(target) if name.endswith("_staging")] == []


def test_a_failed_publish_drops_its_staging_tables_and_keeps_the_live_ones(target):
    with target.begin() as conn:
        conn.execute(text("CREATE TABLE sleeper_users (user_id TEXT)"))
        conn.execute(text("INSERT INTO sleeper_users VALUES ('u1')"))
    tables = {
        "sleeper_users": pd.DataFrame({"user_id": ["u2"]}),
        "unstorable": pd.DataFrame({"value": [{"a": "dict"}]}),
    }

    with pytest.raises(StatementError):
        write_tables(target, tables)

    assert table_names(target) == {"sleeper_users"}
    assert target_rows(target, "sleeper_users") == [{"user_id": "u1"}]


def test_a_dry_run_prints_the_counts_and_writes_nothing(settings, local, target_path, scp_calls, capsys):
    add_rows(local["league"], "users", [{"user_id": "u1"}, {"user_id": "u2"}])
    add_simulation(settings, local["odds"], WEEK_4_SIMULATION, 4, "2026-09-23T03:01:00+00:00")
    add_runs(local["pipeline"])
    add_chart(settings, "chart.png")

    result = run_publish(settings, dry_run=True)

    assert not target_path.exists()
    assert scp_calls == []
    assert {"name": "sleeper_users", "rows": 2} in result.summary["tables"]
    assert result.summary["totals_stored"] == []
    assert result.warnings[-1].startswith("dry run: nothing was written")
    output = capsys.readouterr().out
    assert re.search(r"^sleeper_users +2$", output, re.MULTILINE)
    assert re.search(r"^simulation_totals +1$", output, re.MULTILINE)
    assert output.isascii()


def test_no_charts_leaves_scp_alone(settings, local, scp_calls):
    add_runs(local["pipeline"])
    add_chart(settings, "chart.png")

    result = run_publish(settings, no_charts=True)

    assert scp_calls == []
    assert result.summary["charts_uploaded"] == 0


def test_charts_are_uploaded_with_scp(settings, local, scp_calls):
    add_runs(local["pipeline"])
    for name in ["b_chart.png", "a_chart.png", "notes.txt"]:
        add_chart(settings, name)

    result = run_publish(settings)

    assert scp_calls == [
        (
            [
                "scp",
                "-o",
                "BatchMode=yes",
                "a_chart.png",
                "b_chart.png",
                "root@143.198.183.213:/var/lib/tncasino/analytics/",
            ],
            {"cwd": settings.images_dir, "check": True, "timeout": 60},
        )
    ]
    assert result.summary["charts_uploaded"] == 2


def test_the_charts_target_can_be_overridden(monkeypatch, settings, scp_calls):
    monkeypatch.setenv("PUBLISH_CHARTS_TARGET", "deploy@example.com:/srv/charts/")
    add_chart(settings, "chart.png")

    assert publish.upload_charts(settings.images_dir) == 1
    assert scp_calls[0][0][-1] == "deploy@example.com:/srv/charts/"


@pytest.mark.parametrize(
    "failure",
    [subprocess.CalledProcessError(255, "scp"), subprocess.TimeoutExpired("scp", 60), FileNotFoundError("scp")],
)
def test_a_failed_chart_upload_fails_before_any_table_is_written(monkeypatch, settings, local, target_path, failure):
    def failing_scp(command, **kwargs):
        raise failure

    monkeypatch.setattr(publish.subprocess, "run", failing_scp)
    add_runs(local["pipeline"])
    add_chart(settings, "chart.png")

    with pytest.raises(PublishError, match="no table was published"):
        run_publish(settings)
    assert not target_path.exists()


def test_no_charts_to_upload_is_a_warning(settings, local, scp_calls):
    add_runs(local["pipeline"])

    result = run_publish(settings)

    assert scp_calls == []
    assert f"no charts in {settings.images_dir} to upload" in result.warnings


def test_publishing_needs_a_database_url(monkeypatch, settings):
    monkeypatch.delenv("DATABASE_URL")

    with pytest.raises(PublishError, match="DATABASE_URL is not set"):
        run_publish(settings)


def test_the_summary_names_the_target_host_but_never_the_credentials(monkeypatch, settings, local):
    monkeypatch.setenv("DATABASE_URL", "postgresql://publisher:hunter2@db.example.com:5432/tncasino")
    add_runs(local["pipeline"])

    result = run_publish(settings, dry_run=True)

    assert result.summary["target_host"] == "db.example.com"
    assert "hunter2" not in json.dumps(result.summary) + json.dumps(result.warnings)
