import time

import numpy as np
import pandas as pd
import pytest

from pipeline.model.sampling import lognormal_params, simulate_teams
from pipeline.sources.teams import CANONICAL_TEAMS

V1_PARAMS = {"dud": None, "correlation": None}
CORRELATION = {"same_nfl_team": {"QB-WR": 0.25, "QB-TE": 0.20, "QB-RB": 0.05, "RB-WR": -0.05}}
LINEUP_POSITIONS = ["QB", "RB", "RB", "WR", "WR", "TE", "WR", "K", "DEF"]


def starters(*rows) -> pd.DataFrame:
    columns = ["roster_id", "sleeper_player_id", "position", "nfl_team", "mu", "sigma"]
    return pd.DataFrame(rows, columns=columns)


def dud_table(p: float) -> dict:
    """Skill positions dud with probability p at any mu (d = 0 makes the logistic flat); K and DEF never dud."""
    c = np.log(p / (1 - p))
    by_position = {position: {"c": c, "d": 0.0} for position in ["QB", "RB", "WR", "TE"]}
    return {"threshold_ratio": 0.25, "by_position": by_position}


def full_league() -> pd.DataFrame:
    """12 rosters of 9 starters; NFL teams are dealt round-robin so each has 3-4 starters at distinct slots."""
    nfl_teams = sorted(CANONICAL_TEAMS)
    rows = []
    for player_number in range(12 * 9):
        roster_id = player_number // 9 + 1
        slot_index = player_number % 9
        mu = 6.0 + 2.0 * slot_index
        rows.append(
            (
                roster_id,
                f"p{player_number}",
                LINEUP_POSITIONS[slot_index],
                nfl_teams[player_number % 32],
                mu,
                0.5 * mu,
            )
        )
    return starters(*rows)


def test_lognormal_params_reproduce_the_mean_and_standard_deviation():
    mu_ln, sigma_ln = lognormal_params(15.0, 7.0)

    variance = (np.exp(sigma_ln**2) - 1) * np.exp(2 * mu_ln + sigma_ln**2)
    assert np.exp(mu_ln + sigma_ln**2 / 2) == pytest.approx(15.0)
    assert variance == pytest.approx(49.0)


def test_lognormal_params_without_spread_are_a_point_mass():
    mu_ln, sigma_ln = lognormal_params(12.0, 0.0)
    assert mu_ln == pytest.approx(np.log(12.0))
    assert sigma_ln == 1e-9

    assert lognormal_params(0.0, 0.0) == (0, 1e-9)


def test_lognormal_params_floor_a_non_positive_mean():
    assert lognormal_params(-3.0, 4.0) == lognormal_params(1e-6, 4.0)


def test_without_duds_draws_are_lognormal_with_the_requested_moments():
    draws, _ = simulate_teams(starters((1, "rb", "RB", "KC", 14.0, 9.0)), V1_PARAMS, n_sims=200_000, seed=11)
    mu_ln, sigma_ln = lognormal_params(14.0, 9.0)

    assert draws.mean() == pytest.approx(14.0, rel=0.01)
    assert draws.std() == pytest.approx(9.0, rel=0.02)
    assert np.log(draws).mean() == pytest.approx(mu_ln, abs=0.01)
    assert np.log(draws).std() == pytest.approx(sigma_ln, rel=0.01)


# Spreads are narrow enough that the lognormal part almost never lands in the dud range [0, 0.25 * mu].
@pytest.mark.parametrize(("mu", "sigma", "p"), [(4.0, 1.5, 0.05), (12.0, 7.0, 0.15), (22.0, 9.0, 0.3)])
def test_the_dud_mixture_keeps_the_mean_at_mu(mu, sigma, p):
    params = {"dud": dud_table(p), "correlation": None}

    draws, _ = simulate_teams(starters((1, "wr", "WR", "KC", mu, sigma)), params, n_sims=200_000, seed=7)

    assert draws.mean() == pytest.approx(mu, rel=0.01)
    assert (draws <= 0.25 * mu).mean() == pytest.approx(p, abs=0.005)


def test_positions_without_dud_coefficients_keep_the_plain_lognormal_draws():
    team = starters((1, "k", "K", "KC", 8.0, 4.0), (2, "def", "DEF", "KC", 7.0, 7.0))

    with_table, _ = simulate_teams(team, {"dud": dud_table(0.2), "correlation": None}, n_sims=1000, seed=3)
    without_table, _ = simulate_teams(team, V1_PARAMS, n_sims=1000, seed=3)

    np.testing.assert_array_equal(with_table, without_table)


def test_same_nfl_team_starters_are_correlated_by_position_pair():
    # One starter per roster, so each column of the totals is a single player's draws.
    team = starters(
        (1, "wr_kc", "WR", "KC", 14.0, 10.0),
        (2, "qb_kc", "QB", "KC", 20.0, 7.0),
        (3, "te_kc", "TE", "KC", 9.0, 8.0),
        (4, "wr_buf", "WR", "BUF", 13.0, 10.0),
    )

    draws, _ = simulate_teams(team, {"dud": None, "correlation": CORRELATION}, n_sims=50_000, seed=5)

    # Log draws are the correlated normals rescaled, so their correlation is the table entry itself.
    correlation = np.corrcoef(np.log(draws), rowvar=False)
    assert correlation[0, 1] == pytest.approx(0.25, abs=0.03)
    assert correlation[1, 2] == pytest.approx(0.20, abs=0.03)
    assert correlation[0, 2] == pytest.approx(0.0, abs=0.03)
    assert correlation[0, 3] == pytest.approx(0.0, abs=0.03)
    assert correlation[1, 3] == pytest.approx(0.0, abs=0.03)


def test_without_a_correlation_table_teammates_are_independent():
    team = starters((1, "qb_kc", "QB", "KC", 20.0, 7.0), (2, "wr_kc", "WR", "KC", 14.0, 10.0))

    draws, _ = simulate_teams(team, V1_PARAMS, n_sims=50_000, seed=5)

    assert np.corrcoef(np.log(draws), rowvar=False)[0, 1] == pytest.approx(0.0, abs=0.03)


def test_a_locked_player_scores_the_same_points_in_every_sim():
    team = starters((1, "qb", "QB", "KC", 20.0, 7.0), (2, "wr", "WR", "KC", 14.0, 10.0))

    free, _ = simulate_teams(team, V1_PARAMS, n_sims=1000, seed=9)
    locked, _ = simulate_teams(team, V1_PARAMS, n_sims=1000, seed=9, locked_points={"qb": 31.5})

    assert np.all(locked[:, 0] == np.float32(31.5))
    np.testing.assert_array_equal(locked[:, 1], free[:, 1])


def test_totals_are_float32_with_columns_in_ascending_roster_order():
    team = starters(
        (7, "qb", "QB", "KC", 20.0, 7.0),
        (7, "wr", "WR", "KC", 10.0, 5.0),
        (2, "rb", "RB", "BUF", 5.0, 2.0),
    )

    draws, roster_ids = simulate_teams(team, V1_PARAMS, n_sims=20_000, seed=1)

    assert roster_ids == [2, 7]
    assert draws.dtype == np.float32
    assert draws.shape == (20_000, 2)
    assert draws[:, 0].mean() == pytest.approx(5.0, rel=0.02)
    assert draws[:, 1].mean() == pytest.approx(30.0, rel=0.02)


def test_the_same_seed_reproduces_the_same_draws():
    params = {"dud": dud_table(0.1), "correlation": CORRELATION}
    league = full_league()

    first, _ = simulate_teams(league, params, n_sims=1000, seed=42)
    second, _ = simulate_teams(league, params, n_sims=1000, seed=42)
    other_seed, _ = simulate_teams(league, params, n_sims=1000, seed=43)

    np.testing.assert_array_equal(first, second)
    assert not np.array_equal(first, other_seed)


def test_a_full_league_simulates_50k_times_in_under_five_seconds():
    params = {"dud": dud_table(0.1), "correlation": CORRELATION}

    started = time.perf_counter()
    draws, roster_ids = simulate_teams(full_league(), params, n_sims=50_000, seed=1738)
    elapsed = time.perf_counter() - started

    assert elapsed < 5
    assert draws.shape == (50_000, 12)
    assert roster_ids == list(range(1, 13))
