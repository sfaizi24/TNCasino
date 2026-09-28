import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from pipeline.db import connect
from pipeline.runner import StepContext, StepResult
from pipeline.settings import Settings
from pipeline.steps import odds, simulate

FIXTURES = Path(__file__).parent / "fixtures" / "simulate"
SIMULATION_RUN_ID = "2026w04-20260929T140000"
ODDS_RUN_ID = "2026w04-20260929T160000"
N_SIMS = 1000
SIM_IDS = np.arange(N_SIMS)

# (roster_id, matchup_id_number) rows as Sleeper lists them; the higher roster comes first on purpose.
MATCHUPS = [(2, 1), (1, 1), (4, 2), (3, 2)]

ROWS_PER_TABLE = {
    "betting_odds_team_ou": 4,
    "betting_odds_matchup_ou": 2,
    "betting_odds_matchup_ml": 2,
    "betting_odds_highest_scorer": 4,
    "betting_odds_lowest_scorer": 4,
    "team_distribution_curves": 4,
    "team_matchup_margin_curves": 12,
}

# Design doc 6.1: the columns Flask reads by name.
FLASK_COLUMNS = {
    "betting_odds_matchup_ml": [
        "week",
        "matchup",
        "team1_id",
        "team1_name",
        "team1_win_prob",
        "team1_ml",
        "team2_id",
        "team2_name",
        "team2_win_prob",
        "team2_ml",
        "ties",
    ],
    "betting_odds_team_ou": [
        "week",
        "team_id",
        "team_name",
        "owner",
        "line",
        "over_prob",
        "over_odds",
        "under_prob",
        "under_odds",
        "push_count",
    ],
    "betting_odds_highest_scorer": ["week", "owner", "probability", "odds"],
    "betting_odds_lowest_scorer": ["week", "owner", "probability", "odds"],
    "team_distribution_curves": [
        "week",
        "owner",
        "x_values",
        "density_values",
        "cdf_values",
        "mean",
        "p10",
        "p50",
        "p90",
    ],
    "team_matchup_margin_curves": [
        "week",
        "team_owner",
        "opponent_owner",
        "team_win_prob",
        "opponent_win_prob",
        "left_x_values",
        "left_y_values",
        "right_x_values",
        "right_y_values",
    ],
}


@pytest.fixture
def settings(tmp_path):
    return Settings(season=2026, week=4, league_id="L2026", n_sims=N_SIMS, data_dir=tmp_path)


def head_to_head_draws() -> dict[int, np.ndarray]:
    """Roster 1 beats roster 2 in 500 sims, ties 100 and loses 400; roster 3 beats roster 4 in 750 and loses 250."""
    roster2 = 100.0 + SIM_IDS % 10
    roster1 = roster2 + np.repeat([5.0, 0.0, -5.0], [500, 100, 400])
    roster4 = 90.0 + SIM_IDS % 7
    roster3 = roster4 + np.repeat([3.0, -3.0], [750, 250])
    return {1: roster1, 2: roster2, 3: roster3, 4: roster4}


def write_simulation(settings: Settings, draws: dict[int, np.ndarray], run_id: str = SIMULATION_RUN_ID) -> None:
    roster_ids = sorted(draws)
    matrix = np.column_stack([draws[roster_id] for roster_id in roster_ids]).astype(np.float32)
    with StepContext(settings, run_id=run_id, options={}, step="simulate") as ctx:
        draws_path = simulate.save_draws(settings, run_id, matrix, roster_ids)
        simulate.record_run(ctx, len(roster_ids), draws_path)


def write_league(settings: Settings, matchups: list[tuple[int, int | None]]) -> None:
    conn = connect(settings, "league")
    conn.execute("CREATE TABLE matchups (league_id TEXT, week INTEGER, roster_id INTEGER, matchup_id_number INTEGER)")
    conn.executemany(
        "INSERT INTO matchups VALUES (?, ?, ?, ?)",
        [(settings.league_id, settings.week, roster_id, matchup_id) for roster_id, matchup_id in matchups],
    )
    conn.commit()
    conn.close()


def write_lineups(settings: Settings, roster_ids: list[int]) -> None:
    rows = [
        {
            "season": settings.season,
            "week": settings.week,
            "roster_id": roster_id,
            "team_name": f"Club {roster_id}",
            "owner": f"owner{roster_id}",
            "record": "3-0",
            "slot": "QB",
            "position": "QB",
            "mu": 20.0,
            "sigma": 7.0,
            "var": 49.0,
            "n_sources": 3,
            "timestamp": "2026-09-29T13:00:00+00:00",
        }
        for roster_id in roster_ids
    ]
    conn = connect(settings, "projections")
    conn.executescript((FIXTURES / "team_lineups.sql").read_text())
    pd.DataFrame(rows).to_sql("team_lineups", conn, if_exists="append", index=False)
    conn.close()


def write_week(settings: Settings, draws: dict[int, np.ndarray], matchups: list[tuple[int, int | None]] = ()) -> None:
    write_simulation(settings, draws)
    write_league(settings, matchups)
    write_lineups(settings, sorted(draws))


def run_odds(settings: Settings, no_charts: bool = True) -> StepResult:
    with StepContext(settings, run_id=ODDS_RUN_ID, options={"no_charts": no_charts}, step="odds") as ctx:
        return odds.run(ctx)


def read_rows(settings: Settings, table: str, order_by: str) -> list[dict]:
    conn = connect(settings, "odds")
    rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order_by}")]
    conn.close()
    return rows


def pick(rows: list[dict], *columns: str) -> list[tuple]:
    return [tuple(row[column] for column in columns) for row in rows]


def outcome_probabilities(margin_row: dict) -> tuple[float, float, float]:
    return margin_row["team_win_prob"], margin_row["tie_prob"], margin_row["opponent_win_prob"]


def rows_per_run(settings: Settings) -> dict[str, dict[str, int]]:
    conn = connect(settings, "odds")
    counts = {}
    for table in ROWS_PER_TABLE:
        rows = conn.execute(f"SELECT run_id, COUNT(*) FROM {table} GROUP BY run_id")
        counts[table] = {run_id: count for run_id, count in rows}
    conn.close()
    return counts


@pytest.mark.parametrize(
    ("probability", "american_odds"),
    [
        (0.5, "-100"),
        (0.75, "-300"),
        (0.2, "+400"),
        (1 / 50000, "+4999900"),
        (49999 / 50000, "-4999900"),
        (1.0, None),
        (0.0, None),
        (0.50004, "-100"),
        (0.49996, "+100"),
    ],
)
def test_american_odds_are_fair_and_unclamped_with_no_price_at_zero_or_one(probability, american_odds):
    assert odds.probability_to_american_odds(probability) == american_odds


def test_over_under_lines_sit_at_the_median_and_pushes_pay_neither_side(settings):
    write_week(
        settings,
        {1: np.repeat([80.0, 100.0, 120.0], [200, 600, 200]), 2: 0.25 * SIM_IDS},
        matchups=[(1, 1), (2, 1)],
    )

    run_odds(settings)

    team_lines = read_rows(settings, "betting_odds_team_ou", "team_id")
    columns = ["team_id", "owner", "line", "push_count", "over_prob", "over_odds", "under_prob", "under_odds"]
    assert pick(team_lines, *columns) == [
        (1, "owner1", 100.0, 600, 0.2, "+400", 0.2, "+400"),
        (2, "owner2", 124.88, 0, 0.5, "-100", 0.5, "-100"),
    ]
    # The combined total keeps its unrounded median (100 + 124.875), as notebook 07 priced it.
    matchup_lines = read_rows(settings, "betting_odds_matchup_ou", "matchup")
    assert pick(matchup_lines, "matchup", "line", "over_prob", "under_prob") == [
        ("Team 1 vs Team 2", 224.875, 0.5, 0.5)
    ]


def test_moneylines_count_ties_and_list_the_lower_roster_first(settings):
    write_week(settings, head_to_head_draws(), matchups=[*MATCHUPS, (5, None)])

    run_odds(settings)

    moneylines = read_rows(settings, "betting_odds_matchup_ml", "matchup")
    assert pick(moneylines, "matchup", "team1_id", "team1_name", "team2_id", "team2_name") == [
        ("Team 1 vs Team 2", 1, "Club 1", 2, "Club 2"),
        ("Team 3 vs Team 4", 3, "Club 3", 4, "Club 4"),
    ]
    assert pick(moneylines, "team1_win_prob", "team1_ml", "team2_win_prob", "team2_ml", "ties") == [
        (0.5, "-100", 0.4, "+150", 100),
        (0.75, "-300", 0.25, "+300", 0),
    ]
    for row in moneylines:
        assert row["team1_win_prob"] + row["team2_win_prob"] + row["ties"] / N_SIMS == pytest.approx(1.0)


def test_every_team_sharing_the_top_or_bottom_score_counts_the_sim(settings):
    # Rosters 1 and 2 share the top score in the first half; roster 3 tops the second half, where 2 and 4 share the bottom.
    halves = [500, 500]
    write_week(
        settings,
        {
            1: np.repeat([150.0, 120.0], halves),
            2: np.repeat([150.0, 60.0], halves),
            3: np.repeat([100.0, 160.0], halves),
            4: np.repeat([50.0, 60.0], halves),
        },
    )

    run_odds(settings)

    highest = read_rows(settings, "betting_odds_highest_scorer", "team_id")
    assert pick(highest, "team_id", "count", "probability", "odds") == [
        (1, 500, 0.5, "-100"),
        (2, 500, 0.5, "-100"),
        (3, 500, 0.5, "-100"),
        (4, 0, 0.0, None),
    ]
    lowest = read_rows(settings, "betting_odds_lowest_scorer", "team_id")
    assert pick(lowest, "team_id", "count", "probability", "odds") == [
        (1, 0, 0.0, None),
        (2, 500, 0.5, "-100"),
        (3, 0, 0.0, None),
        (4, 1000, 1.0, None),
    ]


def test_a_side_that_never_wins_keeps_its_row_without_a_price(settings):
    # Roster 2 scores 100 in every sim; roster 1 beats it in 900 and ties the other 100.
    write_week(
        settings,
        {1: np.repeat([105.0, 100.0], [900, 100]), 2: np.full(N_SIMS, 100.0)},
        matchups=[(1, 1), (2, 1)],
    )

    run_odds(settings)

    moneylines = read_rows(settings, "betting_odds_matchup_ml", "matchup")
    assert pick(moneylines, "team1_win_prob", "team1_ml", "team2_win_prob", "team2_ml", "ties") == [
        (0.9, "-900", 0.0, None, 100)
    ]
    team_lines = read_rows(settings, "betting_odds_team_ou", "team_id")
    columns = ["team_id", "line", "push_count", "over_prob", "over_odds", "under_prob", "under_odds"]
    assert pick(team_lines, *columns) == [
        (1, 105.0, 900, 0.0, None, 0.1, "+900"),
        (2, 100.0, 1000, 0.0, None, 0.0, None),
    ]


def test_distribution_curves_use_the_fixed_300_point_grid(settings):
    rng = np.random.default_rng(7)
    write_week(settings, {roster_id: rng.normal(90 + 10 * roster_id, 15, N_SIMS) for roster_id in [1, 2, 3, 4]})

    run_odds(settings)

    curves = read_rows(settings, "team_distribution_curves", "owner")
    assert [row["owner"] for row in curves] == ["owner1", "owner2", "owner3", "owner4"]
    for row in curves:
        x = json.loads(row["x_values"])
        density = json.loads(row["density_values"])
        cdf = json.loads(row["cdf_values"])
        assert (len(x), len(density), len(cdf)) == (300, 300, 300)
        assert (x[0], x[-1]) == (0.0, 300.0)
        assert np.trapezoid(density, x) == pytest.approx(1.0, abs=0.01)
        assert np.all(np.diff(cdf) >= 0)
        assert cdf[-1] == 1.0
        assert row["p10"] < row["p50"] < row["p90"]
        assert row["n_sims"] == N_SIMS


def test_margin_curves_split_at_zero_and_agree_with_the_moneylines(settings):
    write_week(settings, head_to_head_draws(), MATCHUPS)

    run_odds(settings)

    rows = read_rows(settings, "team_matchup_margin_curves", "team_owner, opponent_owner")
    margins = {(row["team_owner"], row["opponent_owner"]): row for row in rows}
    assert len(margins) == 12
    # The moneyline test prices roster 1 over roster 2 at 0.5, with 0.1 ties and 0.4 for roster 2.
    assert outcome_probabilities(margins["owner1", "owner2"]) == pytest.approx((0.5, 0.1, 0.4))
    assert outcome_probabilities(margins["owner2", "owner1"]) == pytest.approx((0.4, 0.1, 0.5))
    for row in margins.values():
        left_x = json.loads(row["left_x_values"])
        left_y = json.loads(row["left_y_values"])
        right_x = json.loads(row["right_x_values"])
        right_y = json.loads(row["right_y_values"])
        assert (len(left_x), len(left_y), len(right_x), len(right_y)) == (81, 81, 81, 81)
        assert (left_x[0], left_x[-1], right_x[0], right_x[-1]) == (-40.0, 0.0, 0.0, 40.0)
        # At zero the left curve is P(margin <= 0) and the right curve P(margin > 0), the team's win probability.
        assert right_y[0] == pytest.approx(row["team_win_prob"])
        assert left_y[-1] + right_y[0] == pytest.approx(1.0)


def test_every_table_keeps_the_columns_flask_reads(settings):
    write_week(settings, head_to_head_draws(), MATCHUPS)

    run_odds(settings)

    conn = connect(settings, "odds")
    for table, flask_columns in FLASK_COLUMNS.items():
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        assert set(flask_columns) | {"run_id", "season"} <= columns, table
    conn.close()


def test_rows_carry_the_simulation_run_and_a_rerun_replaces_them(settings):
    write_week(settings, head_to_head_draws(), MATCHUPS)

    run_odds(settings)
    run_odds(settings)

    expected = {table: {SIMULATION_RUN_ID: count} for table, count in ROWS_PER_TABLE.items()}
    assert rows_per_run(settings) == expected
    seasons = {row["season"] for row in read_rows(settings, "team_distribution_curves", "owner")}
    assert seasons == {2026}


def test_a_newer_simulation_is_priced_alongside_the_older_rows(settings):
    newer_run_id = "2026w04-20260929T150000"
    write_week(settings, head_to_head_draws(), MATCHUPS)
    run_odds(settings)

    write_simulation(
        settings, {roster_id: points + 1.0 for roster_id, points in head_to_head_draws().items()}, newer_run_id
    )
    summary = run_odds(settings).summary

    assert summary["run_id"] == newer_run_id
    expected = {table: {SIMULATION_RUN_ID: count, newer_run_id: count} for table, count in ROWS_PER_TABLE.items()}
    assert rows_per_run(settings) == expected


def test_charts_are_drawn_unless_disabled(settings):
    write_week(settings, head_to_head_draws(), MATCHUPS)

    assert run_odds(settings, no_charts=True).charts == []
    assert not settings.images_dir.exists()

    charts = run_odds(settings, no_charts=False).charts
    assert charts == ["simulation_distributions_overlay_week_4.png", "simulation_boxplot_week_4.png"]
    for name in charts:
        assert (settings.images_dir / name).stat().st_size > 0


def test_summary_names_favourites_lines_and_likeliest_top_scorers(settings):
    write_week(settings, head_to_head_draws(), MATCHUPS)

    summary = run_odds(settings).summary

    assert json.loads(json.dumps(summary)) == summary
    assert (summary["run_id"], summary["n_matchups"]) == (SIMULATION_RUN_ID, 2)
    assert summary["favourites"] == [
        {"matchup": "Team 1 vs Team 2", "favourite": "owner1", "prob": 0.5},
        {"matchup": "Team 3 vs Team 4", "favourite": "owner3", "prob": 0.75},
    ]
    lines = [entry["line"] for entry in summary["ou_lines"]]
    assert lines == sorted(lines, reverse=True)
    assert sorted(entry["owner"] for entry in summary["ou_lines"]) == ["owner1", "owner2", "owner3", "owner4"]
    top_probabilities = [entry["prob"] for entry in summary["highest"]]
    assert len(top_probabilities) == 3
    assert top_probabilities == sorted(top_probabilities, reverse=True)


def test_a_week_without_matchups_prices_only_the_team_markets(settings):
    write_week(settings, head_to_head_draws())

    result = run_odds(settings)

    assert result.warnings == ["league.db has no matchups for week 4; only team markets were priced"]
    assert read_rows(settings, "betting_odds_matchup_ml", "matchup") == []
    assert read_rows(settings, "betting_odds_matchup_ou", "matchup") == []
    assert len(read_rows(settings, "betting_odds_team_ou", "team_id")) == 4
    assert result.summary["favourites"] == []


def test_odds_needs_a_simulation_of_the_week(settings):
    write_simulation(replace(settings, week=3), head_to_head_draws())

    with pytest.raises(LookupError, match="no simulation run for season 2026 week 4"):
        run_odds(settings)
