"""FirstDown Studio's weekly PPR projections, read from the rankings snapshot its rankings page is rendered from.

The page's table shows half-PPR points, so we read the snapshot, which carries PPR, half-PPR and standard.
"""

import json
import re

import requests

from pipeline.names import split_full_name
from pipeline.sources.base import Projection, ProjectionSource
from pipeline.sources.teams import normalize_team

URL = "https://www.firstdown.studio/rankings"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
WEBSITE = "firstdown.studio"

POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K"})

# Next.js streams the page's data as <script>self.__next_f.push([1, "..."])</script> chunks.
DATA_CHUNK = re.compile(r"self\.__next_f\.push\((\[.*?\])\)</script>", re.S)


class FirstDownSource(ProjectionSource):
    name = "firstdown"
    website = WEBSITE
    supports_future_weeks = False
    positions = POSITIONS

    def fetch(self, season: int, week: int) -> list[Projection]:
        response = requests.get(URL, headers={"User-Agent": USER_AGENT}, timeout=30)
        response.raise_for_status()
        return parse(response.text, season, week)


def parse(html: str, season: int, week: int) -> list[Projection]:
    """Every rankings page embeds the snapshot for all positions. A kicker's `ppr` is its kicking points."""
    projections = []
    for row in rankings_snapshot(html)["rows"]:
        points = row["ppr"]
        if points <= 0 or row["position"] not in POSITIONS:
            continue

        first_name, last_name = split_full_name(row["name"])
        projections.append(
            Projection(
                source=WEBSITE,
                season=season,
                week=row["week"],
                first_name=first_name,
                last_name=last_name,
                position=row["position"],
                team=normalize_team(row["team"]),
                points=round(points, 2),
                external_id=row["player_id"],
            )
        )
    return projections


def rankings_snapshot(html: str) -> dict:
    pieces = []
    for chunk in DATA_CHUNK.findall(html):
        message = json.loads(chunk)
        if message[0] == 1:  # [1, text] carries page data; the other kinds bootstrap the client
            pieces.append(message[1])
    text = "".join(pieces)

    start = text.find('{"snapshot_id"')
    if start == -1:
        raise ValueError("the FirstDown page has no rankings snapshot")
    snapshot, _ = json.JSONDecoder().raw_decode(text, start)
    return snapshot


SOURCE = FirstDownSource()
