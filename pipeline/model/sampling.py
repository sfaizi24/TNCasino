"""Joint sampling of starters' fantasy points: lognormal scores, an optional dud mixture, and a
Gaussian copula that correlates starters who play for the same NFL team."""

from itertools import combinations

import numpy as np
import pandas as pd
from scipy.special import ndtr, ndtri


def lognormal_params(mu: float, sigma: float) -> tuple[float, float]:
    """Log-space (mu_ln, sigma_ln) of the lognormal whose mean is mu and standard deviation is sigma."""
    if sigma < 1e-10:
        sigma_ln = 1e-9
        mu_ln = np.log(mu) if mu > 0 else 0
        return mu_ln, sigma_ln
    if mu <= 0:
        mu = 1e-6
    phi = np.sqrt(sigma**2 + mu**2)
    sigma_ln = np.sqrt(np.log((phi / mu) ** 2))
    mu_ln = np.log(mu**2 / phi)
    return mu_ln, sigma_ln


def simulate_teams(
    starters: pd.DataFrame,
    params: dict,
    n_sims: int,
    seed: int,
    locked_points: dict[str, float] | None = None,
) -> tuple[np.ndarray, list[int]]:
    """Simulated team totals, float32 of shape (n_sims, n_teams), with columns in ascending roster_id order.

    One standard normal per starter and sim is drawn in the starters' row order, so the same rows and
    seed always reproduce the same totals. A starter in `locked_points` scores that value in every sim.
    """
    normals = np.random.default_rng(seed).standard_normal((n_sims, len(starters)))
    if params["correlation"] is not None:
        correlate_teammates(normals, starters, params["correlation"]["same_nfl_team"])
    points = player_points(normals, starters, params["dud"])

    locked_points = locked_points or {}
    for column, player_id in enumerate(starters["sleeper_player_id"]):
        if player_id in locked_points:
            points[:, column] = locked_points[player_id]

    column_rosters = starters["roster_id"].to_numpy()
    roster_ids = sorted(starters["roster_id"].unique().tolist())
    totals = np.empty((n_sims, len(roster_ids)), dtype=np.float32)
    for index, roster_id in enumerate(roster_ids):
        totals[:, index] = points[:, column_rosters == roster_id].sum(axis=1)
    return totals, roster_ids


def correlate_teammates(normals: np.ndarray, starters: pd.DataFrame, pair_correlations: dict[str, float]) -> None:
    """Correlate, in place, the normals of starters who share an NFL team; unlisted position pairs stay at 0."""
    positions = starters["position"].to_numpy()
    for columns in starters.groupby("nfl_team").indices.values():
        matrix = teammate_matrix(positions[columns], pair_correlations)
        normals[:, columns] = normals[:, columns] @ np.linalg.cholesky(matrix).T


def teammate_matrix(positions: list[str], pair_correlations: dict[str, float]) -> np.ndarray:
    """Correlation matrix of teammates playing these positions; unlisted position pairs stay at 0."""
    matrix = np.eye(len(positions))
    for i, j in combinations(range(len(positions)), 2):
        first, second = positions[i], positions[j]
        rho = pair_correlations.get(f"{first}-{second}", pair_correlations.get(f"{second}-{first}", 0.0))
        matrix[i, j] = matrix[j, i] = rho
    return matrix


def player_points(normals: np.ndarray, starters: pd.DataFrame, dud: dict | None) -> np.ndarray:
    """Map each starter's normals to fantasy points whose mean is the starter's mu."""
    mu = starters["mu"].to_numpy(dtype=float)
    sigma = starters["sigma"].to_numpy(dtype=float)
    p_dud = dud_probabilities(starters, dud)
    threshold = dud["threshold_ratio"] if dud else 0.0
    lognormal_mu = lognormal_means(mu, p_dud, threshold)
    mu_ln, sigma_ln = np.array([lognormal_params(m, s) for m, s in zip(lognormal_mu, sigma, strict=True)]).T

    points = np.exp(mu_ln + sigma_ln * normals)
    mixed = p_dud > 0
    if mixed.any():
        u = ndtr(normals[:, mixed])
        p = p_dud[mixed]
        lognormal = np.exp(mu_ln[mixed] + sigma_ln[mixed] * ndtri((u - p) / (1 - p)))
        points[:, mixed] = np.where(u < p, u / p * threshold * mu[mixed], lognormal)
    return points


def lognormal_means(mu: np.ndarray, p_dud: np.ndarray, threshold: float) -> np.ndarray:
    """Mean of the lognormal part of the mixture. A dud scores uniformly on [0, threshold * mu], so it
    averages threshold * mu / 2 and the lognormal part carries the rest of the mean mu."""
    return (mu - p_dud * threshold * mu / 2) / (1 - p_dud)


def dud_probabilities(starters: pd.DataFrame, dud: dict | None) -> np.ndarray:
    """Logistic chance of a dud game per starter; positions without coefficients never dud."""
    p_dud = np.zeros(len(starters))
    if dud is None:
        return p_dud
    for index, (position, mu) in enumerate(zip(starters["position"], starters["mu"], strict=True)):
        coefficients = dud["by_position"].get(position)
        if coefficients is not None:
            p_dud[index] = 1 / (1 + np.exp(-(coefficients["c"] + coefficients["d"] * mu)))
    return p_dud
