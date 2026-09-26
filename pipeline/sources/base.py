"""The contract every projection source implements: fetch one week and return Projection rows."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Projection:
    source: str  # website key, e.g. "espn.com"
    season: int
    week: int
    first_name: str
    last_name: str
    position: str  # canonical QB/RB/WR/TE/K/DEF
    team: str | None  # canonical Sleeper code or None
    points: float  # PPR projected points
    external_id: str | None = None  # source's own player id when available


class ProjectionSource:
    name: str  # short key: sleeper, espn, fantasysharks, fantasypros, firstdown, fanduel
    website: str  # stored in projections.source_website
    supports_future_weeks: bool

    def fetch(self, season: int, week: int) -> list[Projection]:
        raise NotImplementedError
