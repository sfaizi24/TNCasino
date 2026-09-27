from contextlib import closing

import numpy as np
import pandas as pd
import pytest
from scipy import stats
from scipy.special import expit

from pipeline.db import connect
from pipeline.model.evaluate import (
    COVERAGE_BANDS,
    brier_difference,
    brier_errors,
    eligible_rows,
    gate_failures,
    leave_one_week_out,
    load_team_weeks,
    pit,
    player_coverage,
    pooled_metrics,
    recompute_starters,
    simulate_week,
)
from pipeline.model.params import load_params
from pipeline.model.sampling import player_points
from pipeline.settings import Settings
from pipeline.steps.league import MIRROR_TABLES
from pipeline.steps.lineups import LINEUP_TABLES

DUD = {"threshold_ratio": 0.25, "by_position": {"RB": {"c": -1.0, "d": -0.10}, "WR": {"c": -0.5, "d": -0.12}}}
LEAGUE_ID = "L2025"
CREATED_AT = "2026-01-06T12:00:00+00:00"


@pytest.mark.parametrize("dud", [None, DUD], ids=["lognormal", "with duds"])
def test_a_correctly_specified_model_covers_each_band_at_its_nominal_rate(dud):
    rng = np.random.default_rng(5)
    n_players = 50_000
    players = pd.DataFrame({"position": rng.choice(["QB", "RB", "WR"], n_players), "mu": rng.uniform(4, 20, n_players)})
    players["sigma"] = 1 + 0.35 * players["mu"]
    players["actual"] = player_points(rng.standard_normal((1, n_players)), players, dud)[0]

    u_low, u_high = pit(players, dud)
    coverage = player_coverage(players.assign(u_low=u_low, u_high=u_high))["ALL"]

    for level in COVERAGE_BANDS:
        assert coverage[level] == pytest.approx(level / 100, abs=0.01)


def test_a_score_of_zero_or_less_is_the_whole_dud_component_of_the_distribution():
    players = pd.DataFrame({"position": ["WR", "WR", "DEF"], "mu": 10.0, "sigma": 4.0, "actual": [0.0, -1.5, 0.0]})
    coefficients = DUD["by_position"]["WR"]
    p_dud = expit(coefficients["c"] + coefficients["d"] * 10.0)

    u_low, u_high = pit(players, DUD)

    assert u_low.tolist() == [0.0, 0.0, 0.0]
    # A defense has no dud coefficients, so its zero is a point at the bottom of the distribution.
    assert u_high.tolist() == pytest.approx([p_dud, p_dud, 0.0])
    for bound in pit(players, None):
        assert bound.tolist() == [0.0, 0.0, 0.0]


def test_pit_is_the_cdf_of_the_dud_and_lognormal_mixture():
    mu, sd, threshold = 12.0, 5.0, DUD["threshold_ratio"]
    actual = np.array([1.0, 2.9, 3.5, 12.0, 30.0])
    players = pd.DataFrame({"position": "WR", "mu": mu, "sigma": sd, "actual": actual})
    coefficients = DUD["by_position"]["WR"]
    p_dud = expit(coefficients["c"] + coefficients["d"] * mu)
    lognormal_mean = (mu - p_dud * threshold * mu / 2) / (1 - p_dud)
    shape = np.sqrt(np.log(1 + (sd / lognormal_mean) ** 2))
    lognormal = stats.lognorm(shape, scale=lognormal_mean * np.exp(-(shape**2) / 2))

    expected = p_dud * np.minimum(actual / (threshold * mu), 1) + (1 - p_dud) * lognormal.cdf(actual)

    u_low, u_high = pit(players, DUD)
    np.testing.assert_allclose(u_low, expected)
    np.testing.assert_array_equal(u_high, u_low)


def test_each_week_is_held_out_once_in_season_and_week_order():
    rows = pd.DataFrame({"season": [2026, 2025, 2025, 2026, 2025], "week": [1, 16, 15, 1, 1], "row": range(5)})

    folds = list(leave_one_week_out(rows))

    assert [week for week, _, _ in folds] == [(2025, 1), (2025, 15), (2025, 16), (2026, 1)]
    assert {week: test["row"].tolist() for week, _, test in folds} == {
        (2025, 1): [4],
        (2025, 15): [2],
        (2025, 16): [1],
        (2026, 1): [0, 3],
    }
    for _, train, test in folds:
        assert sorted([*train["row"], *test["row"]]) == list(range(5))


def test_only_scoring_positions_projected_for_at_least_two_points_are_eligible():
    rows = pd.DataFrame(
        [("a", "WR", 2.5), ("a", "WR", 1.5), ("b", "RB", 2.5), ("b", "RB", 1.0), ("c", "LB", 9.0)],
        columns=["sleeper_player_id", "position", "projected_points"],
    ).assign(season=2025, week=3)

    assert eligible_rows(rows)["sleeper_player_id"].tolist() == ["a", "a"]


def test_coverage_is_the_share_of_pits_inside_each_central_band_by_position_then_all():
    u = [0.5, 0.95, 0.2, 0.05]
    players = pd.DataFrame({"position": ["WR", "WR", "WR", "QB"], "actual": 10.0, "u_low": u, "u_high": u})

    coverage = player_coverage(players)

    assert list(coverage) == ["QB", "WR", "ALL"]
    assert coverage["WR"] == pytest.approx({"n": 3, "zero_share": 0.0, 50: 1 / 3, 80: 2 / 3, 95: 1.0})
    assert coverage["ALL"] == pytest.approx({"n": 4, "zero_share": 0.0, 50: 0.25, 80: 0.5, 95: 1.0})


def test_a_zero_is_covered_by_the_share_of_its_dud_component_inside_each_band():
    players = pd.DataFrame({"position": ["WR"], "actual": [0.0], "u_low": [0.0], "u_high": [0.2]})

    coverage = player_coverage(players)["WR"]

    # [0, 0.2] against [0.25, 0.75], [0.10, 0.90] and [0.025, 0.975].
    assert coverage == pytest.approx({"n": 1, "zero_share": 1.0, 50: 0.0, 80: 0.5, 95: 0.875})


def test_the_gate_reports_the_share_of_zeros_by_position_then_all():
    players = pd.DataFrame(
        {
            "position": ["WR", "WR", "WR", "QB"],
            "actual": [0.0, -1.5, 3.0, 12.0],
            "u_low": [0.0, 0.0, 0.4, 0.5],
            "u_high": [0.2, 0.2, 0.4, 0.5],
        }
    )
    teams = pd.DataFrame({"mean": [100.0], "p10": [80.0], "p90": [120.0], "points": [110.0]})
    games = pd.DataFrame({"p_first": [0.6], "first_points": [110.0], "second_points": [100.0]})

    metrics = pooled_metrics(players, teams, games)

    assert metrics["player_zero_share"] == {"QB": 0.0, "WR": 0.6667, "ALL": 0.5}


def test_a_tie_is_left_out_of_the_brier_score_and_the_games_counted():
    players = pd.DataFrame({"position": ["WR"], "actual": [10.0], "u_low": [0.5], "u_high": [0.5]})
    teams = pd.DataFrame({"mean": [100.0], "p10": [80.0], "p90": [120.0], "points": [110.0]})
    games = pd.DataFrame({"p_first": [0.8, 0.3, 0.6], "first_points": [100, 90, 95], "second_points": [90, 100, 95]})

    metrics = pooled_metrics(players, teams, games)

    assert brier_errors(games).to_dict() == pytest.approx({0: 0.2**2, 1: 0.3**2})
    assert (metrics["moneyline_brier"], metrics["n_matchups"]) == (0.065, 2)


def test_the_brier_difference_pairs_each_decided_game_with_the_baseline_forecast_of_it():
    games = pd.DataFrame(
        {"p_first": [0.6, 0.5, 0.3, 0.9], "first_points": [100, 95, 90, 120], "second_points": [90, 95, 100, 80]}
    )
    baseline_games = games.assign(p_first=[0.7, 0.2, 0.3, 0.5])

    delta, se = brier_difference(games, baseline_games)

    # The second game is a tie. Of the others the squared errors are 0.16, 0.09 and 0.01 against the baseline's
    # 0.09, 0.09 and 0.25.
    differences = pd.Series([0.07, 0.0, -0.24])
    assert delta == pytest.approx(differences.mean())
    assert se == pytest.approx(differences.std(ddof=1) / np.sqrt(3))


CALIBRATED = {"QB": 0.8, "RB": 0.8, "WR": 0.8, "TE": 0.8}


def gate_metrics(
    coverage: dict[str, float], team_coverage: float, brier: float, delta: float = 0.0, se: float = 0.0
) -> dict:
    return {
        "player_coverage_80": coverage,
        "team_coverage_80": team_coverage,
        "moneyline_brier": brier,
        "moneyline_brier_delta": delta,
        "moneyline_brier_delta_se": se,
    }


def test_the_gate_bounds_are_inclusive_and_kickers_and_defenses_are_not_gated():
    coverage = {"QB": 0.70, "RB": 0.90, "WR": 0.8, "TE": 0.8, "K": 0.2, "DEF": 0.2}

    assert gate_failures(gate_metrics(coverage, 0.72, 0.2365), gate_metrics({}, 0.8, 0.2365)) == []


@pytest.mark.parametrize(
    ("delta", "passed"),
    [(0.0003, True), (0.0034, True), (0.0035, False)],
    ids=["well within two standard errors", "at two standard errors", "beyond two standard errors"],
)
def test_a_moneyline_worse_than_v1s_fails_the_gate_only_beyond_two_standard_errors(delta, passed):
    fitted = gate_metrics(CALIBRATED, 0.8, 0.2365 + delta, delta, se=0.0017)

    assert (gate_failures(fitted, gate_metrics({}, 0.8, 0.2365)) == []) == passed


def test_a_single_game_has_no_standard_error_so_any_worse_moneyline_fails():
    games = pd.DataFrame({"p_first": [0.6], "first_points": [110.0], "second_points": [100.0]})

    delta, se = brier_difference(games, games.assign(p_first=0.61))

    assert (delta, se) == pytest.approx((0.4**2 - 0.39**2, 0.0))
    assert gate_failures(gate_metrics(CALIBRATED, 0.8, 0.16, delta, se), gate_metrics({}, 0.8, 0.1521)) == [
        "moneyline Brier 0.1600 is above v1's 0.1521 by more than two standard errors (+0.0079, se 0.0000)"
    ]


def test_every_way_of_missing_the_gate_is_named():
    coverage = {"QB": 0.75, "RB": 0.695, "WR": 0.6129, "TE": 0.95}

    fitted = gate_metrics(coverage, 0.89, 0.2400, delta=0.0035, se=0.0017)
    failures = gate_failures(fitted, gate_metrics({}, 0.8, 0.2365))

    assert failures == [
        "RB 80% coverage 0.695 is outside [0.70, 0.90]",
        "WR 80% coverage 0.613 is outside [0.70, 0.90]",
        "TE 80% coverage 0.950 is outside [0.70, 0.90]",
        "team 80% coverage 0.890 is outside [0.72, 0.88]",
        "moneyline Brier 0.2400 is above v1's 0.2365 by more than two standard errors (+0.0035, se 0.0017)",
    ]


def insert(settings: Settings, database: str, table: str, rows: pd.DataFrame) -> None:
    with closing(connect(settings, database)) as conn:
        rows.to_sql(table, conn, if_exists="append", index=False)


@pytest.fixture
def settings(tmp_path):
    """Week 10's lineups, with a waiver pickup, who has no id, and a starter in a retired slot, week 11's, a week
    with lineups but no results and a week with results but no lineups."""
    settings = Settings(season=2025, week=17, league_id=LEAGUE_ID, data_dir=tmp_path)
    for database, ddl in {"league": MIRROR_TABLES, "projections": LINEUP_TABLES}.items():
        with closing(connect(settings, database)) as conn:
            conn.executescript(ddl)

    lineups = pd.DataFrame(
        [
            (11, 1, "QB", "4046", "Patrick Mahomes", "QB", 20.0),
            (10, 2, "WR1", "6794", "Justin Jefferson", "WR", 17.0),
            (10, 1, "RB1", None, "Waiver Pickup", "RB", 18.0),
            (10, 1, "QB", "4046", "Patrick Mahomes", "QB", 21.0),
            (10, 1, "RB", "4035", "Alvin Kamara", "RB", 9.0),
            (10, 2, "QB", "7000", "Practice Squad", "QB", 15.0),
            (12, 1, "QB", "4046", "Patrick Mahomes", "QB", 19.0),
        ],
        columns=["week", "roster_id", "slot", "sleeper_player_id", "player_name", "position", "mu"],
    )
    lineups = lineups.assign(
        season=2025,
        team_name=lineups["roster_id"].map("Team {}".format),
        owner=lineups["roster_id"].map("owner{}".format),
        record="5-4",
        sigma=5.0,
        var=25.0,
        n_sources=4,
        timestamp=CREATED_AT,
    )
    insert(settings, "projections", "team_lineups", lineups)

    teams = [("4046", "KC"), ("6794", "MIN"), ("4035", "NO")]
    insert(settings, "league", "nfl_players", pd.DataFrame(teams, columns=["player_id", "team"]))
    matchups = pd.DataFrame(
        [
            ("10_1", LEAGUE_ID, 10, 1, 120.5),
            ("10_2", LEAGUE_ID, 10, 2, 110.0),
            ("11_1", LEAGUE_ID, 11, 1, 100.0),
            ("11_2", LEAGUE_ID, 11, 2, 95.0),
            ("13_1", LEAGUE_ID, 13, 1, 90.0),
            ("10_1_old", "L2024", 10, 1, 999.0),
        ],
        columns=["matchup_id", "league_id", "week", "roster_id", "points"],
    )
    insert(settings, "league", "matchups", matchups.assign(matchup_id_number=1))
    return settings


def test_team_weeks_carry_each_starters_sleeper_id_and_team_in_roster_and_slot_order(settings):
    lineups, matchups = load_team_weeks(settings, 2025, [10, 11])

    columns = ["week", "roster_id", "slot", "sleeper_player_id", "nfl_team", "legacy_mu"]
    assert lineups[columns].fillna({"sleeper_player_id": "-", "nfl_team": "-"}).values.tolist() == [
        [10, 1, "QB", "4046", "KC", 21.0],
        [10, 1, "RB1", "-", "-", 18.0],
        [10, 2, "QB", "7000", "-", 15.0],
        [10, 2, "WR1", "6794", "MIN", 17.0],
        [11, 1, "QB", "4046", "KC", 20.0],
    ]
    assert matchups.sort_values(["week", "roster_id"])[["season", "week", "roster_id", "points"]].values.tolist() == [
        [2025, 10, 1, 120.5],
        [2025, 10, 2, 110.0],
        [2025, 11, 1, 100.0],
        [2025, 11, 2, 95.0],
    ]


def test_a_week_without_both_lineups_and_results_cannot_be_scored(settings):
    with pytest.raises(LookupError, match=r"weeks \[12, 13\] need both team_lineups and league L2025 matchups"):
        load_team_weeks(settings, 2025, [10, 12, 13])


def test_a_simulated_week_scores_each_team_and_game_beside_what_happened():
    starters = pd.DataFrame(
        {
            "roster_id": [2, 1],
            "sleeper_player_id": ["a", "b"],
            "position": "QB",
            "nfl_team": ["KC", "BUF"],
            "mu": [30.0, 5.0],
            "sigma": [3.0, 1.0],
        }
    )
    matchups = pd.DataFrame({"roster_id": [2, 1], "matchup_id_number": 1, "points": [25.0, 40.0]})

    teams, games = simulate_week(starters, matchups, {"dud": None, "correlation": None}, seed=0)

    assert teams["roster_id"].tolist() == [1, 2]
    assert teams["mean"].tolist() == pytest.approx([5.0, 30.0], rel=0.02)
    assert teams["points"].tolist() == [40.0, 25.0]
    assert (teams["p10"] < teams["mean"]).all() and (teams["mean"] < teams["p90"]).all()
    # The game is seen from the lower roster id, the underdog that won.
    assert games[["first_points", "second_points"]].values.tolist() == [[40.0, 25.0]]
    assert games["p_first"].item() == pytest.approx(0.0, abs=1e-3)


def test_starters_take_their_consensus_projection_or_keep_the_one_their_lineup_was_built_with():
    lineups = pd.DataFrame({"sleeper_player_id": ["a", "b"], "position": ["WR", "RB"], "legacy_mu": [11.0, 7.5]})
    consensus = pd.DataFrame({"sleeper_player_id": ["a"], "mu": [12.0], "spread": [2.0]})

    starters = recompute_starters(lineups, consensus, load_params("v1"))

    assert starters["mu"].tolist() == [12.0, 7.5]
    assert starters["spread"].tolist() == [2.0, 0.0]
    # v1: sqrt((2 * spread)^2 + position sigma^2), with WR at 10 and RB at 9.
    assert starters["sigma"].tolist() == pytest.approx([np.sqrt(116.0), 9.0])
