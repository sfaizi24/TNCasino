"""Fill lineup holes from the waiver wire: teams claim in FAAB order, and a pickup never projects above the league
median starter at its position."""

import math
from dataclasses import dataclass

FLEX_POSITIONS = frozenset({"RB", "WR", "TE"})
ALLOCATION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF", "FLEX"]


@dataclass(frozen=True)
class Hole:
    roster_id: int
    slot: str
    position: str  # the slot's position: QB, RB, WR, TE, K, DEF or FLEX
    faab_remaining: int
    waiver_position: int


@dataclass(frozen=True)
class ProjectedPlayer:
    sleeper_player_id: str
    player_name: str
    position: str
    positions: frozenset[str]  # every position the player may start at (Sleeper's fantasy_positions)
    nfl_team: str | None
    mu: float
    sigma: float
    var: float
    n_sources: int

    def can_play(self, slot_position: str) -> bool:
        eligible = FLEX_POSITIONS if slot_position == "FLEX" else {slot_position}
        return not self.positions.isdisjoint(eligible)


@dataclass(frozen=True)
class Assignment:
    hole: Hole
    free_agent: ProjectedPlayer | None  # None when the pool ran out
    mu: float


def allocate(holes: list[Hole], pool: list[ProjectedPlayer], cap_by_position: dict[str, float]) -> list[Assignment]:
    """Position by position, FLEX last: the i-th team in waiver order gets the i-th best free agent left.

    Waiver order is most FAAB remaining first, then the lower waiver_position. A position with no cap is uncapped.
    """
    available = sorted(pool, key=lambda free_agent: free_agent.mu, reverse=True)
    assignments = []
    for position in ALLOCATION_ORDER:
        claimants = [hole for hole in holes if hole.position == position]
        claimants.sort(key=lambda hole: (-hole.faab_remaining, hole.waiver_position))
        for hole in claimants:
            free_agent = next((fa for fa in available if fa.can_play(position)), None)
            if free_agent is None:
                assignments.append(Assignment(hole, None, 0.0))
                continue
            available.remove(free_agent)
            cap = cap_by_position.get(position, math.inf)
            assignments.append(Assignment(hole, free_agent, min(free_agent.mu, cap)))
    return assignments
