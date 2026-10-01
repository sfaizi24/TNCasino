"""The win rule of every market, applied to a run's score matrix or its standings matrix, and both stored encodings.

A score matrix is a float64 array of shape (n_sims, n_teams) whose columns follow a list of roster ids; `team`,
`opponent`, `team1` and `team2` are column indexes into it. Scores are compared exactly. A standings matrix is the
same run's simulated seasons: each team's finishing place in the same column order, and each season's champion as a
column. The module imports numpy and the standard library only, so the Flask app can price bets with the rules the
odds and playoffs steps priced the markets with.
"""

import zlib
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Outcome:
    """Per sim, whether the selection won and whether it pushed; never both."""

    won: np.ndarray
    pushed: np.ndarray


def moneyline(scores: np.ndarray, team: int, opponent: int) -> Outcome:
    return Outcome(won=scores[:, team] > scores[:, opponent], pushed=scores[:, team] == scores[:, opponent])


def team_total(scores: np.ndarray, team: int, line: float, side: str) -> Outcome:
    return over_under(scores[:, team], line, side)


def matchup_total(scores: np.ndarray, team1: int, team2: int, line: float, side: str) -> Outcome:
    return over_under(scores[:, team1] + scores[:, team2], line, side)


def spread(scores: np.ndarray, team: int, opponent: int, line: float) -> Outcome:
    adjusted = scores[:, team] + line
    return Outcome(won=adjusted > scores[:, opponent], pushed=adjusted == scores[:, opponent])


def highest_scorer(scores: np.ndarray, team: int) -> Outcome:
    """Every team sharing a sim's top score wins that sim."""
    won = scores[:, team] == scores.max(axis=1)
    return Outcome(won=won, pushed=np.zeros_like(won))


def lowest_scorer(scores: np.ndarray, team: int) -> Outcome:
    """Every team sharing a sim's bottom score wins that sim."""
    won = scores[:, team] == scores.min(axis=1)
    return Outcome(won=won, pushed=np.zeros_like(won))


def probability(outcome: Outcome) -> float:
    """The share of all sims the selection wins, so a push counts against it."""
    return float(outcome.won.mean())


def over_under(points: np.ndarray, line: float, side: str) -> Outcome:
    if side == "over":
        won = points > line
    elif side == "under":
        won = points < line
    else:
        raise ValueError(f"side is 'over' or 'under', not {side!r}")
    return Outcome(won=won, pushed=points == line)


def make_playoffs(positions: np.ndarray, team: int, playoff_teams: int, side: str) -> Outcome:
    """`positions[sim, team]` is the team's finishing place, 1 first; the top playoff_teams make it."""
    if side == "yes":
        won = positions[:, team] <= playoff_teams
    elif side == "no":
        won = positions[:, team] > playoff_teams
    else:
        raise ValueError(f"side is 'yes' or 'no', not {side!r}")
    return Outcome(won=won, pushed=np.zeros_like(won))


def last_place(positions: np.ndarray, team: int) -> Outcome:
    won = positions[:, team] == positions.shape[1]
    return Outcome(won=won, pushed=np.zeros_like(won))


def champion(champions: np.ndarray, team: int) -> Outcome:
    won = champions == team
    return Outcome(won=won, pushed=np.zeros_like(won))


def encode_totals(scores: np.ndarray) -> bytes:
    """float32, little-endian, one sim's scores after another, zlib-compressed."""
    return zlib.compress(np.ascontiguousarray(scores, dtype="<f4").tobytes())


def decode_totals(blob: bytes, n_sims: int, n_teams: int) -> np.ndarray:
    matrix = np.frombuffer(zlib.decompress(blob), dtype="<f4").reshape(n_sims, n_teams)
    return matrix.astype(np.float64)


def encode_standings(positions: np.ndarray, champions: np.ndarray) -> bytes:
    """uint8, one sim's places then its champion's column, zlib-compressed."""
    return zlib.compress(np.column_stack([positions, champions]).astype(np.uint8).tobytes())


def decode_standings(blob: bytes, n_sims: int, n_teams: int) -> tuple[np.ndarray, np.ndarray]:
    matrix = np.frombuffer(zlib.decompress(blob), dtype=np.uint8).reshape(n_sims, n_teams + 1).astype(np.int64)
    return matrix[:, :n_teams], matrix[:, n_teams]
