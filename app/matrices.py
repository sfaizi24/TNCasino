"""A published run's score matrix, read once per worker, and the win rules applied to it.

Every bet is priced, re-priced and settled against the score matrix of some run, so the matrix
is decoded once and kept: about 4.8 MB a run in float64, and a week has at most a few runs.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from pipeline import markets as win_rules

from .routes.helpers import query_analytics

MATRIX_SQL = "SELECT n_sims, roster_ids, totals FROM simulation_totals WHERE run_id = :run_id"


class MissingMatrix(LookupError):
    """The run's matrix was never stored, so nothing can be priced at it."""


@dataclass(frozen=True)
class Matrix:
    scores: np.ndarray  # (n_sims, n_teams), float64
    columns: dict  # roster id → column


@lru_cache(maxsize=4)
def score_matrix(run_id):
    rows = query_analytics(MATRIX_SQL, {"run_id": run_id})
    if not rows:
        raise MissingMatrix(run_id)
    [row] = rows
    roster_ids = [int(roster_id) for roster_id in row["roster_ids"].split(",")]
    scores = win_rules.decode_totals(row["totals"], row["n_sims"], len(roster_ids))
    return Matrix(scores, {roster_id: column for column, roster_id in enumerate(roster_ids)})


def leg_outcome(market, selection, line, matrix):
    """Per sim, whether the pick wins and whether it pushes. A roster the run did not simulate raises KeyError."""
    scores, columns = matrix.scores, matrix.columns
    if market.name == "moneyline":
        picked, other = _matchup_sides(market, selection)
        return win_rules.moneyline(scores, columns[picked], columns[other])
    if market.name == "spread":
        picked, other = _matchup_sides(market, selection)
        return win_rules.spread(scores, columns[picked], columns[other], line)
    if market.name == "team_total":
        [roster_id] = market.teams
        return win_rules.team_total(scores, columns[roster_id], line, selection)
    if market.name == "highest_scorer":
        return win_rules.highest_scorer(scores, columns[int(selection)])
    return win_rules.lowest_scorer(scores, columns[int(selection)])


def joint_probability(outcomes):
    """The share of sims in which every pick wins."""
    won = np.logical_and.reduce([outcome.won for outcome in outcomes])
    return float(won.mean())


def _matchup_sides(market, selection):
    """The picked roster and the other roster of a matchup's key."""
    first, second = market.teams
    picked = int(selection)
    return picked, second if picked == first else first
