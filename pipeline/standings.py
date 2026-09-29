"""Final regular-season standings for every simulation, from the record to date plus simulated weekly scores, and the
playoff bracket they seed.

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


def playoff_seeds(positions: np.ndarray, playoff_teams: int) -> np.ndarray:
    """seeds[sim, seed - 1]: the column of the team that finished in that place."""
    return np.argsort(positions, axis=1)[:, :playoff_teams]


def bracket_order(playoff_teams: int) -> list[int]:
    """The seeds in the order the bracket pairs them, neighbours meeting in each round: 1, 8, 4, 5, 2, 7, 3, 6 for
    eight teams, so seed i opens against seed playoff_teams + 1 - i and the 1v8 winner meets the 4v5 winner."""
    order = [1]
    while len(order) < playoff_teams:
        size = 2 * len(order)
        order = [seed for top in order for seed in (top, size + 1 - top)]
    return order


def play_round(bracket: np.ndarray, seed_scores: np.ndarray) -> np.ndarray:
    """The winners of one round, in bracket order. bracket[sim] lists the seeds still in (0 is the top seed), and
    seed_scores[sim, seed] is that seed's score for the week. The higher score wins; a tie advances the higher seed."""
    first, second = bracket[:, 0::2], bracket[:, 1::2]
    first_scores = np.take_along_axis(seed_scores, first, axis=1)
    second_scores = np.take_along_axis(seed_scores, second, axis=1)
    higher_seed = np.minimum(first, second)
    return np.where(first_scores > second_scores, first, np.where(second_scores > first_scores, second, higher_seed))


def play_bracket(seeds: np.ndarray, weeks: list[np.ndarray]) -> np.ndarray:
    """Each sim's champion as a column: the seeds play one round a week, given as that week's scores by column,
    with no byes and no reseeding."""
    bracket = np.tile(np.array(bracket_order(seeds.shape[1])) - 1, (len(seeds), 1))
    for scores in weeks:
        bracket = play_round(bracket, np.take_along_axis(scores, seeds, axis=1))
    return np.take_along_axis(seeds, bracket, axis=1)[:, 0]
