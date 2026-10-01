import math
from dataclasses import replace

import pytest

from pipeline.db import connect
from pipeline.runner import StepContext
from pipeline.settings import Settings
from pipeline.steps import stats
from pipeline.steps.match import PROJECTIONS_WITH_SLEEPER_DDL

WEEK = 4

NFL_PLAYERS_DDL = """
CREATE TABLE nfl_players (player_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT, position TEXT, team TEXT)
"""

PLAYERS = [
    ("7547", "Amon-Ra", "St. Brown", "WR", "DET"),
    ("4046", "Patrick", "Mahomes", "QB", "KC"),
    ("4666", "Younghoe", "Koo", "K", "NYG"),
    ("7149", "Tommy", "Stevens", "TE", "NYG"),
]


@pytest.fixture
def settings(tmp_path):
    settings = Settings(season=2026, week=WEEK, league_id="L2026", data_dir=tmp_path, model_version="v1")
    conn = connect(settings, "league")
    conn.execute(NFL_PLAYERS_DDL)
    with conn:
        conn.executemany("INSERT INTO nfl_players VALUES (?, ?, ?, ?, ?)", PLAYERS)
    conn.close()
    return settings


def add_matches(settings, rows, week=WEEK):
    """rows: (source_website, sleeper_player_id, projected_points, position as the source listed it)."""
    conn = connect(settings, "projections")
    conn.executescript(PROJECTIONS_WITH_SLEEPER_DDL)
    with conn:
        for index, (source, player_id, points, position) in enumerate(rows):
            conn.execute(
                """
                INSERT INTO projections_with_sleeper (source_website, season, week, player_first_name,
                    player_last_name, position, team, projected_points, sleeper_player_id, match_method, created_at)
                VALUES (?, 2026, ?, 'Source', ?, ?, NULL, ?, ?, 'exact', '2026-09-30T12:00:00+00:00')
                """,
                (source, week, f"Spelling {index}", position, points, player_id),
            )
    conn.close()


def run_stats(settings):
    with StepContext(settings, run_id="2026w04-test", step=stats.NAME) as ctx:
        return stats.run(ctx)


def stored_stats(settings):
    conn = connect(settings, "projections")
    rows = conn.execute(
        "SELECT * FROM player_week_stats WHERE season = 2026 AND week = ? ORDER BY sleeper_player_id", (settings.week,)
    ).fetchall()
    conn.close()
    return {row["sleeper_player_id"]: dict(row) for row in rows}


def test_mu_is_the_mean_and_spread_the_sample_deviation_across_sources(settings):
    add_matches(
        settings,
        [
            ("sleeper.com", "7547", 16.0, "WR"),
            ("espn.com", "7547", 18.0, "WR"),
            ("fantasypros.com", "7547", 20.0, "WR"),
        ],
    )

    run_stats(settings)

    row = stored_stats(settings)["7547"]
    assert (row["mu"], row["spread"], row["n_sources"]) == (18.0, 2.0, 3)
    assert row["sigma"] == pytest.approx(math.sqrt((2.0 * 2.0) ** 2 + 10.0**2))
    assert row["var"] == pytest.approx(row["sigma"] ** 2)
    assert row["model_version"] == "v1"


def test_a_single_source_leaves_sigma_at_the_position_baseline(settings):
    add_matches(settings, [("sleeper.com", "4666", 8.0, "K")])

    run_stats(settings)

    row = stored_stats(settings)["4666"]
    assert (row["mu"], row["spread"], row["sigma"], row["var"]) == (8.0, 0.0, 4.0, 16.0)


def test_name_position_and_team_come_from_sleeper(settings):
    add_matches(settings, [("espn.com", "7149", 3.5, "QB")])

    run_stats(settings)

    row = stored_stats(settings)["7149"]
    assert (row["player_name"], row["position"], row["team"]) == ("Tommy Stevens", "TE", "NYG")
    assert row["sigma"] == 8.0


def test_source_weights_and_biases_come_from_the_models_parameters(settings, monkeypatch):
    # The player is a QB, so only espn.com's QB bias applies.
    params = {
        "version": "test",
        "sources": {"espn.com": {"weight": 3.0, "bias": {"QB": 2.0, "WR": -5.0}}},
        "sigma": {"formula": "linear", "by_position": {"QB": {"a": 2.0, "b": 0.5}}},
        "dud": None,
        "floor": {"by_position": {}},
    }
    requested = []

    def fake_load_params(version):
        requested.append(version)
        return params

    monkeypatch.setattr(stats, "load_params", fake_load_params)
    add_matches(settings, [("espn.com", "4046", 20.0, "QB"), ("sleeper.com", "4046", 10.0, "QB")])

    run_stats(replace(settings, model_version="test"))

    row = stored_stats(settings)["4046"]
    assert requested == ["test"]
    assert row["mu"] == 16.0
    assert row["spread"] == pytest.approx(math.sqrt(32.0))
    assert row["sigma"] == 10.0
    assert row["model_version"] == "test"


def test_unmatched_rows_and_other_weeks_are_ignored(settings):
    add_matches(settings, [("espn.com", "7547", 18.0, "WR"), ("espn.com", None, 30.0, "WR")])
    add_matches(settings, [("espn.com", "4046", 25.0, "QB")], week=WEEK - 1)

    result = run_stats(settings)

    assert list(stored_stats(settings)) == ["7547"]
    assert result.summary["n_players"] == 1


def test_rerunning_replaces_the_weeks_rows(settings):
    add_matches(settings, [("espn.com", "7547", 18.0, "WR")])
    run_stats(settings)

    run_stats(settings)

    assert list(stored_stats(settings)) == ["7547"]


def test_summary_groups_by_position_and_ranks_by_mu(settings):
    add_matches(
        settings,
        [
            ("espn.com", "7547", 18.0, "WR"),
            ("espn.com", "4046", 21.0, "QB"),
            ("sleeper.com", "4046", 20.0, "QB"),
            ("espn.com", "4666", 8.0, "K"),
        ],
    )

    summary = run_stats(settings).summary

    # Mahomes: spread = sqrt(0.5), so sigma = sqrt((2 * spread) ** 2 + 7 ** 2) = sqrt(51) = 7.14.
    assert summary["n_players"] == 3
    assert summary["by_position"] == [
        {"position": "QB", "n": 1, "mean_mu": 20.5, "mean_sigma": 7.14},
        {"position": "WR", "n": 1, "mean_mu": 18.0, "mean_sigma": 10.0},
        {"position": "K", "n": 1, "mean_mu": 8.0, "mean_sigma": 4.0},
    ]
    assert [player["name"] for player in summary["top"]] == ["Patrick Mahomes", "Amon-Ra St. Brown", "Younghoe Koo"]
    assert summary["top"][0] == {"name": "Patrick Mahomes", "position": "QB", "mu": 20.5, "sigma": 7.14}


def test_a_week_without_matched_projections_fails(settings):
    add_matches(settings, [("espn.com", None, 30.0, "WR")])

    with pytest.raises(RuntimeError, match="run the match step first"):
        run_stats(settings)


def test_each_player_gets_his_10th_and_90th_percentiles(settings):
    add_matches(settings, [("sleeper.com", "7547", 16.0, "WR"), ("espn.com", "7547", 20.0, "WR")])

    run_stats(settings)

    row = stored_stats(settings)["7547"]
    assert 0 < row["p10"] < row["mu"] < row["p90"]


def test_source_low_and_high_are_the_projections_as_published(settings, monkeypatch):
    params = stats.load_params("v1") | {"sources": {"espn.com": {"weight": 1.0, "bias": {"WR": 3.0}}}}
    monkeypatch.setattr(stats, "load_params", lambda version: params)
    add_matches(
        settings, [("sleeper.com", "7547", 16.0, "WR"), ("espn.com", "7547", 24.0, "WR"), ("x.com", "7547", 19.0, "WR")]
    )

    run_stats(settings)

    row = stored_stats(settings)["7547"]
    assert (row["source_low"], row["source_high"]) == (16.0, 24.0)
