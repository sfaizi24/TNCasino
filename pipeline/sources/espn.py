"""ESPN's weekly PPR projections from the fantasy API behind its player pages (replaces the Selenium scraper)."""

import json

from pipeline.sources.base import Projection, ProjectionSource, get
from pipeline.sources.teams import DEF_NAMES

URL = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leaguedefaults/3"
    "?scoringPeriodId={week}&view=kona_player_info"
)
WEBSITE = "espn.com"

POSITIONS_BY_ID = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "DEF"}

# proTeamId -> Sleeper code. 0 is a free agent and maps to no team.
TEAMS_BY_ID = {
    1: "ATL",
    2: "BUF",
    3: "CHI",
    4: "CIN",
    5: "CLE",
    6: "DAL",
    7: "DEN",
    8: "DET",
    9: "GB",
    10: "TEN",
    11: "IND",
    12: "KC",
    13: "LV",
    14: "LAR",
    15: "MIA",
    16: "MIN",
    17: "NE",
    18: "NO",
    19: "NYG",
    20: "NYJ",
    21: "PHI",
    22: "ARI",
    23: "PIT",
    24: "LAC",
    25: "SF",
    26: "SEA",
    27: "TB",
    28: "WAS",
    29: "CAR",
    30: "JAX",
    33: "BAL",
    34: "HOU",
}

# A stats entry is identified by statSourceId (0 actual, 1 projected) and statSplitTypeId (0 season, 1 week).
PROJECTED = 1
SINGLE_WEEK = 1


class EspnSource(ProjectionSource):
    name = "espn"
    website = WEBSITE
    supports_future_weeks = True

    def fetch(self, season: int, week: int) -> list[Projection]:
        headers = {"x-fantasy-filter": json.dumps(player_filter(week))}
        response = get(URL.format(season=season, week=week), headers=headers)
        return parse(response.json(), season, week)


def player_filter(week: int) -> dict:
    """The 600 most-owned players at QB, RB, WR, TE, K and DEF, with stats for `week` only."""
    return {
        "players": {
            "filterSlotIds": {"value": [0, 2, 4, 6, 17, 16]},
            "filterStatsForCurrentSeasonScoringPeriodId": {"value": [week]},
            "limit": 600,
            "sortPercOwned": {"sortPriority": 1, "sortAsc": False},
        }
    }


def parse(payload: dict, season: int, week: int) -> list[Projection]:
    projections = []
    for item in payload["players"]:
        player = item["player"]
        position = POSITIONS_BY_ID.get(player["defaultPositionId"])
        projection = weekly_projection(player["stats"], week)
        if position is None or projection is None or projection["appliedTotal"] <= 0:
            continue

        team = TEAMS_BY_ID.get(player["proTeamId"])
        if position == "DEF":
            first_name, last_name = DEF_NAMES[team]
        else:
            first_name, last_name = player["firstName"], player["lastName"]
        projections.append(
            Projection(
                source=WEBSITE,
                season=season,
                week=projection["scoringPeriodId"],
                first_name=first_name,
                last_name=last_name,
                position=position,
                team=team,
                points=round(projection["appliedTotal"], 2),
                external_id=str(player["id"]),
            )
        )
    return projections


def weekly_projection(stats: list[dict], week: int) -> dict | None:
    for entry in stats:
        if (
            entry["statSourceId"] == PROJECTED
            and entry["statSplitTypeId"] == SINGLE_WEEK
            and entry["scoringPeriodId"] == week
        ):
            return entry
    return None


SOURCE = EspnSource()
