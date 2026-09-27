"""Final regular-season standings for every simulation, from the record to date plus simulated weekly scores.

Teams are columns throughout: arrays have shape (n_sims, n_teams), and a matchup pairs two column indexes.
"""

import numpy as np


def week_results(scores: np.ndarray, pairs: list[tuple[int, int]]) -> tuple[np.ndarray, np.ndarray]:
    """Wins and ties per sim and team for one week. The higher score wins; an exact tie is a tie for both."""
    wins = np.zeros(scores.shape, dtype=np.int32)
    ties = np.zeros(scores.shape, dtype=np.int32)
    for first, second in pairs:
        wins[:, first] = scores[:, first] > scores[:, second]
        wins[:, second] = scores[:, second] > scores[:, first]
        tied = scores[:, first] == scores[:, second]
        ties[:, first] = tied
        ties[:, second] = tied
    return wins, ties


def season_totals(
    wins: np.ndarray, ties: np.ndarray, points: np.ndarray, weeks: list[tuple[np.ndarray, list[tuple[int, int]]]]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Each sim's final wins, ties and points: the record to date (one value per team) plus every simulated week,
    given as its scores and its head-to-head pairs."""
    n_sims = len(weeks[0][0])
    total_wins = np.tile(wins, (n_sims, 1)).astype(np.int32)
    total_ties = np.tile(ties, (n_sims, 1)).astype(np.int32)
    total_points = np.tile(points, (n_sims, 1)).astype(np.float64)
    for scores, pairs in weeks:
        week_wins, week_ties = week_results(scores, pairs)
        total_wins += week_wins
        total_ties += week_ties
        total_points += scores
    return total_wins, total_ties, total_points


def finishing_positions(wins: np.ndarray, ties: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Each team's place in each sim, 1 being first: most wins, then most ties, then most points, and the lower
    column last."""
    columns = np.broadcast_to(np.arange(wins.shape[1]), wins.shape)
    # lexsort sorts by its last key first, so this orders by wins, then ties, then points, then column.
    ranking = np.lexsort((columns, -points, -ties, -wins), axis=1)
    # ranking[sim] lists columns from first place down; its inverse permutation gives each column's place.
    return np.argsort(ranking, axis=1) + 1


def position_counts(positions: np.ndarray) -> np.ndarray:
    """counts[team, place - 1]: the number of sims in which the team finished in that place."""
    n_teams = positions.shape[1]
    return np.stack([np.bincount(positions[:, team] - 1, minlength=n_teams) for team in range(n_teams)])
