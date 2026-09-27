"""FantasyPros' consensus weekly PPR projections, read from the server-rendered table, one page per position.

Logged-out visitors see the top 10 players per position; the rest of each table sits behind a registration wall.
"""

import re

import requests
from bs4 import BeautifulSoup

from pipeline.names import split_full_name
from pipeline.sources.base import Projection, ProjectionSource
from pipeline.sources.teams import DEF_NAMES, normalize_team, team_from_def_name

URL = "https://www.fantasypros.com/nfl/projections/{page}.php"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36"
)
WEBSITE = "fantasypros.com"

PAGES = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k", "DEF": "dst"}


class FantasyProsSource(ProjectionSource):
    name = "fantasypros"
    website = WEBSITE
    supports_future_weeks = False

    def fetch(self, season: int, week: int) -> list[Projection]:
        pages = {}
        for position, page in PAGES.items():
            response = requests.get(
                URL.format(page=page),
                params={"week": week, "scoring": "PPR"},
                headers={"User-Agent": USER_AGENT},
                timeout=30,
            )
            response.raise_for_status()
            pages[position] = response.text
        return parse(pages, season, week)


def parse(pages: dict[str, str], season: int, week: int) -> list[Projection]:
    """`pages` maps each position to its page's HTML. Rows carry the week the page itself shows."""
    projections = []
    for position, html in pages.items():
        projections.extend(parse_page(html, position, season))
    return projections


def parse_page(html: str, position: str, season: int) -> list[Projection]:
    soup = BeautifulSoup(html, "lxml")
    page_week = shown_week(soup)

    projections = []
    for row in soup.select("table#data tbody tr"):
        # FPTS is the last column; its sort value keeps the decimals the cell text rounds away.
        points = float(row.find_all("td")[-1]["data-sort-value"])
        if points <= 0:
            continue

        link = row.find("a", class_="player-name")
        name = link["fp-player-name"]
        if position == "DEF":
            team = team_from_def_name(name)  # defenses are listed by full team name, "Kansas City Chiefs"
            first_name, last_name = DEF_NAMES[team]
        else:
            team = normalize_team(link.next_sibling)  # the text after the link, " BUF"
            first_name, last_name = split_full_name(name)
        projections.append(
            Projection(
                source=WEBSITE,
                season=season,
                week=page_week,
                first_name=first_name,
                last_name=last_name,
                position=position,
                team=team,
                points=points,
                external_id=re.search(r"fp-id-(\d+)", " ".join(link["class"])).group(1),
            )
        )
    return projections


def shown_week(soup: BeautifulSoup) -> int:
    """The title reads "Week 3 QB Projections - ...". For a week outside the season the site serves the current one."""
    title = soup.title.get_text(strip=True)
    match = re.search(r"Week (\d+)", title)
    if match is None:
        raise ValueError(f"the page title {title!r} names no week")
    return int(match.group(1))


SOURCE = FantasyProsSource()
