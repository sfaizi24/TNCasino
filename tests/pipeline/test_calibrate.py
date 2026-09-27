import json
from contextlib import closing
from dataclasses import replace
from math import sqrt

import pandas as pd
import pytest

from pipeline.db import connect
from pipeline.model import params as model_params
from pipeline.model.evaluate import COVERAGE_BANDS
from pipeline.runner import StepContext, StepResult
from pipeline.settings import Settings
from pipeline.steps import accuracy, calibrate
from pipeline.steps.league import MIRROR_TABLES
from pipeline.steps.stats import PLAYER_WEEK_STATS_DDL

LEAGUE_ID = "L2026"
RUN_ID = "2026w04-20260929T180000"
COMPUTED_AT = "2026-09-20T12:00:00+00:00"


def median(mu: float, sd: float) -> float:
    """The median of the lognormal with this mean and standard deviation, where its PIT is 0.5."""
    return mu**2 / sqrt(mu**2 + sd**2)


def insert(settings: Settings, database: str, table: str, rows: pd.DataFrame) -> None:
    with closing(connect(settings, database)) as conn:
        rows.to_sql(table, conn, if_exists="append", index=False)


def write_players(settings: Settings, rows: list[tuple], model_version: str = "v1") -> None:
    """Stored distributions as (week, player id, position, mu, sigma)."""
    players = pd.DataFrame(rows, columns=["week", "sleeper_player_id", "position", "mu", "sigma"])
    players = players.assign(
        season=settings.season,
        player_name=players["sleeper_player_id"],
        var=players["sigma"] ** 2,
        n_sources=4,
        model_version=model_version,
        computed_at=COMPUTED_AT,
    )
    insert(settings, "projections", "player_week_stats", players)


def write_actuals(settings: Settings, rows: list[tuple]) -> None:
    """Actual PPR points as (week, player id, points)."""
    actuals = pd.DataFrame(rows, columns=["week", "player_id", "pts_ppr"])
    stat_ids = actuals["player_id"] + "_" + actuals["week"].astype(str)
    insert(settings, "league", "player_stats", actuals.assign(stat_id=stat_ids, season=str(settings.season)))


def write_grades(settings: Settings, table: str, rows: pd.DataFrame) -> None:
    """Rows into one of the accuracy step's tables, created as the step creates them."""
    with closing(connect(settings, "projections")) as conn:
        conn.executescript(accuracy.ACCURACY_DDL)
        rows.to_sql(table, conn, if_exists="append", index=False)


def grade_weeks(settings: Settings, weeks: list[int]) -> None:
    """The consensus row the accuracy step writes for each week it grades, once all of the week's games are final."""
    graded = pd.DataFrame({"week": weeks}).assign(
        season=settings.season, source="consensus", position="ALL", n=1, mae=2.0, bias=0.0, computed_at=COMPUTED_AT
    )
    write_grades(settings, "prediction_accuracy", graded)


def write_team_grades(settings: Settings, week: int, teams: list[tuple]) -> None:
    """The accuracy step's grades of a week's teams as (roster id, covered, win prob, won)."""
    grades = pd.DataFrame(teams, columns=["roster_id", "covered", "win_prob", "won"])
    grades = grades.assign(
        season=settings.season,
        week=week,
        owner=grades["roster_id"].map("owner{}".format),
        projected=100.0,
        actual=100.0,
        computed_at=COMPUTED_AT,
    )
    write_grades(settings, "team_accuracy", grades)


def run_calibrate(settings: Settings, charts: bool = False) -> StepResult:
    with StepContext(settings, run_id=RUN_ID, options={"no_charts": not charts}, step=calibrate.NAME) as ctx:
        return calibrate.run(ctx)


def stored_metrics(settings: Settings) -> dict[tuple[str, str], float]:
    """The calibration_metrics values recorded at the settings' week under its model version."""
    query = (
        "SELECT metric, position, value FROM calibration_metrics WHERE season = ? AND week = ? AND model_version = ?"
    )
    with closing(connect(settings, "odds")) as conn:
        rows = conn.execute(query, (settings.season, settings.week, settings.model_version)).fetchall()
    return {(row["metric"], row["position"]): row["value"] for row in rows}


@pytest.fixture
def settings(tmp_path):
    settings = Settings(season=2026, week=4, league_id=LEAGUE_ID, data_dir=tmp_path)
    for database, ddl in {"league": MIRROR_TABLES, "projections": PLAYER_WEEK_STATS_DDL}.items():
        with closing(connect(settings, database)) as conn:
            conn.executescript(ddl)
    return settings


def test_nothing_is_scored_until_the_accuracy_step_has_graded_a_week(settings):
    write_players(settings, [(3, "qb", "QB", 20.0, 7.0)])
    # Week 3 has stat lines, but until the accuracy step grades it one of its games may still be to come.
    write_actuals(settings, [(3, "qb", 18.0)])

    result = run_calibrate(settings)

    assert result.summary == {}
    assert result.warnings == ["no week graded by the accuracy step yet"]
    with closing(connect(settings, "odds")) as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert "calibration_metrics" not in tables

    grade_weeks(settings, [3])

    assert run_calibrate(settings).summary["weeks_evaluated"] == [3]


def test_player_coverage_scores_each_stored_distribution_against_the_actual_points(settings):
    write_players(
        settings,
        [
            (1, "qb", "QB", 20.0, 7.0),
            (1, "wr1", "WR", 10.0, 5.0),
            (1, "wr2", "WR", 8.0, 4.0),  # no stats row: did not play, scored 0
            (1, "bench", "WR", 1.5, 2.0),  # projected under two points
            (1, "lb", "LB", 6.0, 3.0),
            (2, "wr1", "WR", 12.0, 5.0),
            (4, "wr1", "WR", 10.0, 5.0),  # this week, not complete yet
        ],
    )
    write_actuals(
        settings,
        [
            (1, "qb", median(20.0, 7.0)),
            (1, "wr1", median(10.0, 5.0)),
            (1, "bench", 30.0),
            (1, "lb", 30.0),
            (2, "wr1", median(12.0, 5.0)),
            (4, "wr1", 40.0),
        ],
    )
    grade_weeks(settings, [1, 2])

    summary = run_calibrate(settings).summary

    assert summary == {
        "model_version": "v1",
        "weeks_evaluated": [1, 2],
        "weeks_without_curves": [1, 2],
        "player_coverage_80": {"QB": 1.0, "WR": 0.6667, "ALL": 0.75},
        "team_coverage_80": None,
        "moneyline_brier": None,
        "n_player_rows": 4,
        "n_team_weeks": 0,
        "n_matchups": 0,
    }
    assert json.loads(json.dumps(summary)) == summary
    expected = {("n_team_weeks", "ALL"): 0, ("n_matchups", "ALL"): 0}
    for position, n_rows, zero_share, coverage in [
        ("QB", 1, 0.0, 1.0),
        ("WR", 3, 1 / 3, 2 / 3),
        ("ALL", 4, 0.25, 0.75),
    ]:
        expected[("n_player_rows", position)] = n_rows
        expected[("player_zero_share", position)] = zero_share
        for level in COVERAGE_BANDS:
            expected[(f"player_coverage_{level}", position)] = coverage
    assert stored_metrics(settings) == pytest.approx(expected)


def test_rerunning_a_week_replaces_its_metrics_and_leaves_other_weeks_alone(settings):
    write_players(settings, [(1, "qb", "QB", 20.0, 7.0), (2, "qb", "QB", 20.0, 7.0)])
    write_actuals(settings, [(1, "qb", 20.0), (2, "qb", 20.0)])
    grade_weeks(settings, [1, 2])
    run_calibrate(replace(settings, week=3))
    run_calibrate(settings)

    write_players(settings, [(3, "qb", "QB", 20.0, 7.0)])
    write_actuals(settings, [(3, "qb", 60.0)])
    grade_weeks(settings, [3])
    run_calibrate(settings)

    with closing(connect(settings, "odds")) as conn:
        rows = conn.execute("SELECT week, COUNT(*) AS n FROM calibration_metrics GROUP BY week").fetchall()
    assert {row["week"]: row["n"] for row in rows} == {3: 12, 4: 12}
    assert stored_metrics(replace(settings, week=3))[("player_coverage_80", "ALL")] == 1.0
    assert stored_metrics(settings)[("player_coverage_80", "ALL")] == pytest.approx(2 / 3)


def test_teams_and_moneylines_are_scored_from_the_accuracy_steps_grades(settings):
    write_players(settings, [(1, "qb", "QB", 20.0, 7.0), (2, "qb", "QB", 20.0, 7.0)])
    write_actuals(settings, [(1, "qb", 20.0), (2, "qb", 20.0)])
    grade_weeks(settings, [1, 2])
    # Rosters 2 and 4 tied in week 1, so neither side has a result.
    write_team_grades(settings, 1, [(1, 1, 0.2, 0), (2, 0, 0.5, None), (3, 1, 0.8, 1), (4, 1, 0.5, None)])
    # Week 2's odds run drew no team curves.
    write_team_grades(settings, 2, [(1, None, 0.6, 1), (3, None, 0.4, 0)])

    summary = run_calibrate(settings).summary

    assert summary["weeks_without_curves"] == [2]
    assert (summary["team_coverage_80"], summary["n_team_weeks"]) == (0.75, 4)
    # 0.2^2 and 0.4^2 for both sides of week 1's and week 2's decided games.
    assert summary["moneyline_brier"] == pytest.approx(0.1)
    assert summary["n_matchups"] == 2
    metrics = stored_metrics(settings)
    assert (metrics[("team_coverage_80", "ALL")], metrics[("moneyline_brier", "ALL")]) == pytest.approx((0.75, 0.1))


def test_each_distribution_is_scored_under_the_version_that_stored_it(settings, tmp_path, monkeypatch):
    params_dir = tmp_path / "params"
    params_dir.mkdir()
    dud = {"threshold_ratio": 0.25, "by_position": {"WR": {"c": 5.0, "d": 0.0}}}
    for version, version_dud in [("va", None), ("vb", dud)]:
        (params_dir / f"{version}.json").write_text(json.dumps({"version": version, "dud": version_dud}))
    monkeypatch.setattr(model_params, "PARAMS_DIR", params_dir)
    write_players(settings, [(1, "wr_a", "WR", 10.0, 3.0)], model_version="va")
    write_players(settings, [(1, "wr_b", "WR", 10.0, 3.0)], model_version="vb")
    # Halfway up the dud range but deep in the lognormal's lower tail, so only vb's dud block covers it.
    write_actuals(settings, [(1, "wr_a", 1.25), (1, "wr_b", 1.25)])
    grade_weeks(settings, [1])
    v2 = replace(settings, model_version="v2")

    summary = run_calibrate(v2).summary

    assert summary["model_version"] == "v2"
    assert summary["player_coverage_80"]["WR"] == 0.5
    assert stored_metrics(v2)[("player_coverage_80", "WR")] == 0.5


def test_the_chart_is_drawn_unless_disabled(settings):
    write_players(settings, [(1, "qb", "QB", 20.0, 7.0)])
    write_actuals(settings, [(1, "qb", 20.0)])
    grade_weeks(settings, [1])

    assert run_calibrate(settings).charts == []
    assert not settings.images_dir.exists()

    assert run_calibrate(settings, charts=True).charts == ["calibration_week_4.png"]
    assert (settings.images_dir / "calibration_week_4.png").stat().st_size > 0
