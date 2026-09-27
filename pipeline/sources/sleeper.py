"""Sleeper's weekly PPR projections from its public JSON API. Every other source is checked against these."""

import requests

from pipeline.sources.base import POSITIONS, Projection, ProjectionSource
from pipeline.sources.teams import normalize_team

URL = (
    "https://api.sleeper.com/projections/nfl/{season}/{week}?season_type=regular"
    "&position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=K&position[]=DEF&order_by=pts_ppr"
)
WEBSITE = "sleeper.com"


class SleeperSource(ProjectionSource):
    name = "sleeper"
    website = WEBSITE
    supports_future_weeks = True

    def fetch(self, season: int, week: int) -> list[Projection]:
        response = requests.get(URL.format(season=season, week=week), timeout=30)
        response.raise_for_status()
        return parse(response.json(), season, week)


def parse(payload: list[dict], season: int, week: int) -> list[Projection]:
    """Rows without a PPR projection are players Sleeper lists but does not project; they are skipped."""
    projections = []
    for row in payload:
        player = row["player"]
        points = row["stats"].get("pts_ppr")
        if points is None or points <= 0 or player["position"] not in POSITIONS:
            continue
        projections.append(
            Projection(
                source=WEBSITE,
                season=season,
                week=row["week"],
                first_name=player["first_name"],
                last_name=player["last_name"],
                position=player["position"],
                team=normalize_team(player["team"]),
                points=points,
                external_id=row["player_id"],
            )
        )
    return projections


SOURCE = SleeperSource()
