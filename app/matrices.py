"""A published run's two matrices, each read once per worker, and the win rules applied to them.

A weekly bet is priced, re-priced and settled against the score matrix of some run: every roster's
score in every sim, about 4.8 MB a run in float64. A futures parlay is priced and re-priced against
the standings matrix of a playoffs run: every roster's finishing place and the champion in every
simulated season, 20,000 × 13 bytes or about 260 KB a run before compression. Both are decoded once
and kept, and a week has at most a few runs.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

from pipeline import markets as win_rules

from .routes.helpers import query_analytics

MATRIX_SQL = "SELECT n_sims, roster_ids, totals FROM simulation_totals WHERE run_id = :run_id"
STANDINGS_SQL = "SELECT n_sims, playoff_teams, roster_ids, standings FROM simulation_standings WHERE run_id = :run_id"


class MissingMatrix(LookupError):
    """The run's matrix was never stored, so nothing can be priced at it."""


@dataclass(frozen=True)
class Matrix:
    scores: np.ndarray  # (n_sims, n_teams), float64
    columns: dict  # roster id → column


@dataclass(frozen=True)
class Standings:
    positions: np.ndarray  # (n_sims, n_teams), each roster's finishing place, 1 first
    champions: np.ndarray  # (n_sims,), the champion's column
    columns: dict  # roster id → column
    playoff_teams: int


@lru_cache(maxsize=4)
def score_matrix(run_id):
    rows = query_analytics(MATRIX_SQL, {"run_id": run_id})
    if not rows:
        raise MissingMatrix(run_id)
    [row] = rows
    columns = _columns(row["roster_ids"])
    scores = win_rules.decode_totals(row["totals"], row["n_sims"], len(columns))
    return Matrix(scores, columns)


@lru_cache(maxsize=4)
def standings_matrix(run_id):
    rows = query_analytics(STANDINGS_SQL, {"run_id": run_id})
    if not rows:
        raise MissingMatrix(run_id)
    [row] = rows
    columns = _columns(row["roster_ids"])
    positions, champions = win_rules.decode_standings(row["standings"], row["n_sims"], len(columns))
    return Standings(positions, champions, columns, row["playoff_teams"])


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


def futures_outcome(market, selection, standings):
    """Per sim, whether the futures pick wins; nothing pushes. A roster the run did not simulate raises KeyError."""
    positions, columns = standings.positions, standings.columns
    if market.name == "make_playoffs":
        [roster_id] = market.teams
        return win_rules.make_playoffs(positions, columns[roster_id], standings.playoff_teams, selection)
    if market.name == "last_place":
        return win_rules.last_place(positions, columns[int(selection)])
    return win_rules.champion(standings.champions, columns[int(selection)])


def joint_probability(outcomes):
    """The share of sims in which every pick wins."""
    won = np.logical_and.reduce([outcome.won for outcome in outcomes])
    return float(won.mean())


def _matchup_sides(market, selection):
    """The picked roster and the other roster of a matchup's key."""
    first, second = market.teams
    picked = int(selection)
    return picked, second if picked == first else first


def _columns(roster_ids):
    """Each roster's column, from the comma-separated roster ids a matrix is stored with."""
    return {int(roster_id): column for column, roster_id in enumerate(roster_ids.split(","))}
