import shutil
from contextlib import closing

import numpy as np
import pandas as pd
import pytest
from scipy.special import expit

from pipeline.db import connect
from pipeline.model import evaluate
from pipeline.model import params as model_params
from pipeline.model.evaluate import player_sigmas, player_weeks
from pipeline.model.fit import (
    MIN_EIGENVALUE,
    clip_correlations,
    fit_and_write,
    fit_params,
    fit_sigma,
    fit_sources,
    load_training_rows,
    shrink_to_positive_definite,
    sigma_bins,
    smallest_eigenvalue,
)
from pipeline.model.params import load_params
from pipeline.model.sampling import player_points, teammate_matrix
from pipeline.settings import Settings
from pipeline.steps.league import MIRROR_TABLES
from pipeline.steps.lineups import LINEUP_TABLES
from pipeline.steps.match import PROJECTIONS_WITH_SLEEPER_DDL
from pipeline.steps.stats import DEFAULT_SOURCE, PLAYER_WEEK_STATS_DDL, compute_player_stats

# The parameters synthetic seasons are drawn from: sigma = a + b * mu, a dud logistic for RB and WR only, and the
# correlation of teammates' scores.
SIGMA = {
    "QB": (3.0, 0.20),
    "RB": (0.5, 0.30),
    "WR": (0.5, 0.30),
    "TE": (0.5, 0.30),
    "K": (1.0, 0.25),
    "DEF": (1.0, 0.20),
}
MU_RANGE = {"QB": (12, 26), "RB": (4, 20), "WR": (4, 20), "TE": (4, 14), "K": (6, 11), "DEF": (4, 10)}
DUD = {"threshold_ratio": 0.25, "by_position": {"RB": {"c": -1.0, "d": -0.10}, "WR": {"c": -0.5, "d": -0.12}}}
CORRELATION = {"QB-WR": 0.30, "QB-TE": 0.20, "QB-RB": 0.10, "RB-WR": -0.05}
BIASES = {"high.com": 1.5, "low.com": -1.0}

# The starters every NFL team fields each week, and the lineup slots they fill when one team is a whole lineup.
TEAM = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "K", "DEF"]
SLOTS = ["QB", "RB1", "RB2", "WR1", "WR2", "FLEX", "TE", "K", "DEF"]

LEAGUE_ID = "L2025"
CREATED_AT = "2026-01-06T12:00:00+00:00"


def synthetic_players(n_weeks: int, n_teams: int, seed: int) -> pd.DataFrame:
    """Player-weeks of `n_teams` NFL teams fielding TEAM every week, each player with a true mu drawn from his
    position's MU_RANGE and actual points drawn from the mixture with SIGMA, DUD and CORRELATION."""
    rng = np.random.default_rng(seed)
    n_groups = n_weeks * n_teams
    positions = np.tile(TEAM, n_groups)
    low, high = np.array([MU_RANGE[position] for position in positions]).T
    a, b = np.array([SIGMA[position] for position in positions]).T
    mu = rng.uniform(low, high)
    players = pd.DataFrame(
        {
            "season": 2025,
            "week": np.repeat(np.arange(1, n_weeks + 1), n_teams * len(TEAM)),
            "nfl_team": np.repeat(np.tile([f"T{team}" for team in range(n_teams)], n_weeks), len(TEAM)),
            "sleeper_player_id": [f"p{index}" for index in range(len(positions))],
            "position": positions,
            "mu": mu,
            "sigma": a + b * mu,
        }
    )
    cholesky = np.linalg.cholesky(teammate_matrix(TEAM, CORRELATION))
    normals = rng.standard_normal((n_groups, len(TEAM))) @ cholesky.T
    players["actual"] = player_points(normals.reshape(1, -1), players, DUD)[0]
    return players


def source_rows(players: pd.DataFrame, biases: dict[str, float]) -> pd.DataFrame:
    """One projection of every player per source, off his true mu by the source's bias."""
    rows = [
        players.assign(source_website=source, projected_points=players["mu"] + bias) for source, bias in biases.items()
    ]
    return pd.concat(rows, ignore_index=True).drop(columns=["mu", "sigma"])


def quartile_mus(position: str) -> np.ndarray:
    low, high = MU_RANGE[position]
    return np.array([low + 0.25 * (high - low), low + 0.75 * (high - low)])


@pytest.fixture(scope="module")
def recovered() -> dict:
    return fit_params(source_rows(synthetic_players(n_weeks=64, n_teams=32, seed=1), BIASES), [], "vtest")


def test_fit_recovers_each_source_bias(recovered):
    for source, bias in BIASES.items():
        assert recovered["sources"][source]["bias"] == pytest.approx(bias, abs=0.15)


@pytest.mark.parametrize("position", list(SIGMA))
def test_fit_recovers_the_sigma_line(recovered, position):
    # Intercept and slope trade off against each other, so the line is checked where the data is.
    fitted = recovered["sigma"]["by_position"][position]
    a, b = SIGMA[position]
    mus = quartile_mus(position)

    np.testing.assert_allclose(fitted["a"] + fitted["b"] * mus, a + b * mus, rtol=0.15)


def test_fit_recovers_the_dud_probability_only_where_players_dud(recovered):
    dud = recovered["dud"]

    assert dud["threshold_ratio"] == DUD["threshold_ratio"]
    assert set(dud["by_position"]) == set(DUD["by_position"])
    for position, drawn in DUD["by_position"].items():
        fitted = dud["by_position"][position]
        mus = quartile_mus(position)
        np.testing.assert_allclose(
            expit(fitted["c"] + fitted["d"] * mus), expit(drawn["c"] + drawn["d"] * mus), atol=0.03
        )


def test_fit_recovers_teammate_correlations(recovered):
    assert recovered["correlation"]["same_nfl_team"] == pytest.approx(CORRELATION, abs=0.07)


def test_sources_are_weighted_by_the_inverse_of_their_error_variance():
    rng = np.random.default_rng(3)
    players = pd.DataFrame(
        {
            "season": 2025,
            "week": np.repeat(np.arange(1, 7), 400),
            "sleeper_player_id": [f"p{index}" for index in range(2400)],
            "position": "WR",
            "nfl_team": "KC",
            "actual": rng.uniform(5, 20, 2400),
        }
    )
    # (bias, error sd): error variances 1, 4 and 100 are precisions 1, 0.25 and 0.01, which average 0.42.
    accuracy = {"sharp.com": (2.0, 1.0), "vague.com": (-1.0, 2.0), "wild.com": (0.0, 10.0)}
    rows = [
        players.assign(source_website=source, projected_points=players["actual"] + bias + rng.normal(0, sd, 2400))
        for source, (bias, sd) in accuracy.items()
    ]
    # rare.com projects two weeks, too few to judge it by, and gets them exactly right.
    early = players[players["week"] <= 2]
    rows.append(early.assign(source_website="rare.com", projected_points=early["actual"]))
    rows = pd.concat(rows, ignore_index=True)

    sources = fit_sources(rows)

    assert sources["sharp.com"]["weight"] == pytest.approx(1 / 0.42, rel=0.1)
    assert sources["vague.com"]["weight"] == pytest.approx(0.25 / 0.42, rel=0.1)
    assert sources["wild.com"]["weight"] == 0.25
    assert sources["rare.com"] == DEFAULT_SOURCE
    assert sources["sharp.com"]["bias"] == pytest.approx(2.0, abs=0.1)
    assert sources["vague.com"]["bias"] == pytest.approx(-1.0, abs=0.1)
    consensus = player_weeks(rows, sources)
    assert (consensus["mu"] - consensus["actual"]).mean() == pytest.approx(0.0, abs=0.05)


def test_player_weeks_match_what_the_stats_step_computes():
    params = {
        "sources": {"espn.com": {"weight": 1.6, "bias": 0.5}, "sleeper.com": {"weight": 0.7, "bias": -0.4}},
        "sigma": {"formula": "linear", "by_position": {"WR": {"a": 2.0, "b": 0.3}, "QB": {"a": 5.0, "b": 0.1}}},
    }
    # ranks.com has no fitted parameters, and the QB has a single source.
    projections = {
        ("p1", "WR"): [("espn.com", 14.0), ("sleeper.com", 12.5), ("ranks.com", 13.0)],
        ("p2", "QB"): [("espn.com", 21.0)],
    }
    columns = ["season", "week", "sleeper_player_id", "position", "nfl_team", "actual", "source_website"]
    rows = pd.DataFrame(
        [
            (2025, 3, player_id, position, "KC", 10.0, source, points)
            for (player_id, position), sources in projections.items()
            for source, points in sources
        ],
        columns=[*columns, "projected_points"],
    )

    players = player_weeks(rows, params["sources"]).set_index("sleeper_player_id")
    players["sigma"] = player_sigmas(players, params)

    for (player_id, position), sources in projections.items():
        source_projections = [{"source_website": source, "projected_points": points} for source, points in sources]
        player = {"player_id": player_id, "first_name": "", "last_name": "", "position": position, "team": "KC"}
        expected = compute_player_stats(source_projections, player, params)
        fitted = players.loc[player_id]
        assert (fitted["mu"], fitted["spread"], fitted["n_sources"], fitted["sigma"]) == pytest.approx(
            (expected["mu"], expected["spread"], expected["n_sources"], expected["sigma"])
        )


def test_correlations_are_clipped_and_sparse_pair_types_zeroed():
    measured = pd.DataFrame(
        {"rho": [0.55, 0.2349, -0.3, 0.3], "n": [400, 400, 400, 99]}, index=["QB-WR", "QB-TE", "QB-RB", "RB-WR"]
    )

    assert clip_correlations(measured) == {"QB-WR": 0.4, "QB-TE": 0.23, "QB-RB": -0.1, "RB-WR": 0.0}


def test_a_positive_definite_table_is_written_as_measured():
    correlations = {"QB-WR": 0.22, "QB-TE": 0.21, "QB-RB": 0.07, "RB-WR": -0.05}

    assert shrink_to_positive_definite(correlations) == (correlations, 1.0)


def test_correlations_shrink_together_until_a_full_team_of_starters_can_be_correlated():
    # A QB correlated 0.4 with ten otherwise uncorrelated teammates has smallest eigenvalue 1 - 0.4 * sqrt(10) < 0.
    correlations = {"QB-WR": 0.4, "QB-TE": 0.4, "QB-RB": 0.4, "RB-WR": 0.0}

    shrunk, scale = shrink_to_positive_definite(correlations)

    assert smallest_eigenvalue(correlations) < 0
    assert (shrunk, scale) == ({"QB-WR": 0.3, "QB-TE": 0.3, "QB-RB": 0.3, "RB-WR": 0.0}, 0.75)
    assert smallest_eigenvalue(shrunk) >= MIN_EIGENVALUE
    # One step less shrinkage is not enough.
    assert smallest_eigenvalue({"QB-WR": 0.32, "QB-TE": 0.32, "QB-RB": 0.32, "RB-WR": 0.0}) < MIN_EIGENVALUE


@pytest.mark.parametrize(
    ("n_rows", "sizes", "mean_mus"),
    [(100, [50, 50], [24.5, 74.5]), (130, [50, 50, 30], [24.5, 74.5, 114.5]), (120, [50, 70], [24.5, 84.5])],
)
def test_residuals_are_binned_by_mu_and_a_short_last_bin_joins_the_one_before(n_rows, sizes, mean_mus):
    rng = np.random.default_rng(0)
    residuals = pd.DataFrame({"mu": rng.permutation(n_rows).astype(float), "residual": rng.standard_normal(n_rows)})

    bins = sigma_bins(residuals)

    assert bins["n"].tolist() == sizes
    assert bins["mean_mu"].tolist() == mean_mus


def test_a_position_with_too_few_rows_to_bin_gets_a_flat_sigma():
    rng = np.random.default_rng(4)
    kickers = pd.DataFrame({"position": "K", "mu": rng.uniform(6, 11, 60)})
    kickers["actual"] = kickers["mu"] + rng.normal(0, 3, 60)

    sigma = fit_sigma(kickers, {"threshold_ratio": 0.25, "by_position": {}})

    assert sigma["by_position"]["K"] == {"a": round((kickers["actual"] - kickers["mu"]).std(), 4), "b": 0.0}


def insert(settings: Settings, database: str, table: str, rows: pd.DataFrame) -> None:
    with closing(connect(settings, database)) as conn:
        rows.to_sql(table, conn, if_exists="append", index=False)


@pytest.fixture
def settings(tmp_path, monkeypatch):
    """Empty league and projections databases, and a params directory holding only v1."""
    params_dir = tmp_path / "params"
    params_dir.mkdir()
    shutil.copy(model_params.PARAMS_DIR / "v1.json", params_dir)
    monkeypatch.setattr(model_params, "PARAMS_DIR", params_dir)
    monkeypatch.setattr(evaluate, "TEAM_SIMS", 2000)
    settings = Settings(season=2025, week=17, league_id=LEAGUE_ID, data_dir=tmp_path)
    tables = {
        "league": MIRROR_TABLES,
        "projections": PROJECTIONS_WITH_SLEEPER_DDL + PLAYER_WEEK_STATS_DDL + LINEUP_TABLES,
    }
    for database, ddl in tables.items():
        with closing(connect(settings, database)) as conn:
            conn.executescript(ddl)
    return settings


def write_projections(settings: Settings, projections: pd.DataFrame) -> None:
    """Matched projections; the fit takes positions from Sleeper, so every source lists the player as a WR."""
    rows = projections[["source_website", "season", "week", "sleeper_player_id", "projected_points"]].assign(
        player_first_name=[f"Player {index}" for index in range(len(projections))],
        player_last_name="",
        position="WR",
        created_at=CREATED_AT,
    )
    insert(settings, "projections", "projections_with_sleeper", rows)


def write_season(settings: Settings, players: pd.DataFrame, biases: dict[str, float]) -> None:
    """Each source's projections of the synthetic players, their Sleeper positions, teams and points, and every
    week the starters of NFL teams T0 and T1 as the lineups of rosters 1 and 2, playing each other."""
    write_projections(settings, source_rows(players, biases))
    sleeper = players.rename(columns={"sleeper_player_id": "player_id", "nfl_team": "team"})
    insert(settings, "league", "nfl_players", sleeper[["player_id", "position", "team"]])
    stats = sleeper[["player_id", "week"]].assign(
        stat_id=sleeper["player_id"], season="2025", pts_ppr=sleeper["actual"]
    )
    insert(settings, "league", "player_stats", stats)

    starters = players[players["nfl_team"].isin(["T0", "T1"])]
    starters = starters.assign(
        roster_id=starters["nfl_team"].map({"T0": 1, "T1": 2}), slot=np.tile(SLOTS, 2 * players["week"].nunique())
    )
    lineups = starters[["season", "week", "roster_id", "slot", "sleeper_player_id", "position", "nfl_team", "mu"]]
    lineups = lineups.assign(
        team_name=lineups["roster_id"].map("Team {}".format),
        owner=lineups["roster_id"].map("owner{}".format),
        record="0-0",
        player_name=lineups["sleeper_player_id"],
        sigma=starters["sigma"],
        var=starters["sigma"] ** 2,
        n_sources=len(biases),
        timestamp=CREATED_AT,
    )
    insert(settings, "projections", "team_lineups", lineups)
    matchups = starters.groupby(["week", "roster_id"], as_index=False).agg(points=("actual", "sum"))
    matchups = matchups.assign(
        matchup_id=matchups["week"].astype(str) + "_" + matchups["roster_id"].astype(str),
        league_id=LEAGUE_ID,
        matchup_id_number=1,
    )
    insert(settings, "league", "matchups", matchups)


def test_training_rows_carry_sleeper_positions_teams_and_points(settings):
    players = pd.DataFrame([("p1", "WR", "KC"), ("p2", "RB", "BUF")], columns=["player_id", "position", "team"])
    insert(settings, "league", "nfl_players", players)
    stats = [("s1", "p1", "2025", 3, 15.5), ("s2", "p1", "2025", 4, 30.0)]
    insert(
        settings,
        "league",
        "player_stats",
        pd.DataFrame(stats, columns=["stat_id", "player_id", "season", "week", "pts_ppr"]),
    )
    columns = ["source_website", "season", "week", "sleeper_player_id", "projected_points"]
    projections = [
        ("espn.com", 2025, 3, "p1", 12.0),
        ("sleeper.com", 2025, 3, "p1", 10.0),
        ("espn.com", 2025, 3, "p2", 8.0),
        ("espn.com", 2025, 3, None, 9.0),
        ("espn.com", 2025, 4, "p1", 11.0),
        ("espn.com", 2024, 3, "p1", 13.0),
    ]
    write_projections(settings, pd.DataFrame(projections, columns=columns))

    rows = load_training_rows(settings, [(2025, [3])])

    # p2 has no stats row: he did not play and scored 0.
    rows = rows.sort_values(["sleeper_player_id", "source_website"])
    assert rows[[*columns, "position", "nfl_team", "actual"]].values.tolist() == [
        ["espn.com", 2025, 3, "p1", 12.0, "WR", "KC", 15.5],
        ["sleeper.com", 2025, 3, "p1", 10.0, "WR", "KC", 15.5],
        ["espn.com", 2025, 3, "p2", 8.0, "RB", "BUF", 0.0],
    ]


def test_v1_is_never_refitted(settings):
    with pytest.raises(ValueError, match="v1 is the frozen legacy baseline"):
        fit_and_write(settings, 2025, [1], "v1", [])


def test_excluding_a_source_without_projections_in_the_window_raises(settings):
    write_season(settings, synthetic_players(n_weeks=1, n_teams=2, seed=0), BIASES)

    with pytest.raises(ValueError, match=r"excluded sources \['ranks.com'\] have no projections"):
        fit_and_write(settings, 2025, [1], "vtest", ["ranks.com"])
    assert not (model_params.PARAMS_DIR / "vtest.json").exists()


def test_a_window_without_matched_projections_raises(settings):
    write_season(settings, synthetic_players(n_weeks=1, n_teams=2, seed=0), BIASES)

    with pytest.raises(LookupError, match=r"no matched projections in season 2025 weeks \[9\]"):
        fit_and_write(settings, 2025, [9], "vtest", [])
    assert not (model_params.PARAMS_DIR / "vtest.json").exists()


def test_the_fit_is_scored_beside_v1_and_written_where_load_params_finds_it(settings):
    write_season(settings, synthetic_players(n_weeks=4, n_teams=32, seed=2), {**BIASES, "ranks.com": 0.0})

    params = fit_and_write(settings, 2025, [1, 2, 3, 4], "vtest", ["ranks.com"])

    assert load_params("vtest") == params
    assert (params["version"], sorted(params["sources"])) == ("vtest", sorted(BIASES))
    fitted_on = params["fitted_on"]
    assert (fitted_on["season"], fitted_on["weeks"], fitted_on["excluded_sources"]) == (
        2025,
        [1, 2, 3, 4],
        ["ranks.com"],
    )
    assert fitted_on["n_player_rows"] == 4 * 32 * len(TEAM)

    gate = params["gate"]
    assert isinstance(gate["passed"], bool)
    assert set(gate["v1"]) == set(gate) - {"passed", "v1", "moneyline_brier_delta", "moneyline_brier_delta_se"}
    assert (gate["n_player_rows"]["ALL"], gate["n_team_weeks"], gate["n_matchups"]) == (4 * 32 * len(TEAM), 8, 4)
    fitted_minus_v1 = gate["moneyline_brier"] - gate["v1"]["moneyline_brier"]
    assert gate["moneyline_brier_delta"] == pytest.approx(fitted_minus_v1, abs=2e-4)
    # The data was drawn from the model being fitted, and each week is scored by a fit that never saw it.
    assert gate["player_coverage_80"]["ALL"] == pytest.approx(0.80, abs=0.05)
