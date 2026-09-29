"""FantasySharks' weekly PPR projections, read from the server-rendered projections table, one page per position."""

import re

from bs4 import BeautifulSoup

from pipeline.sources.base import POSITIONS, Projection, ProjectionSource, get
from pipeline.sources.teams import DEF_NAMES, normalize_team, team_from_def_name

URL = (
    "https://www.fantasysharks.com/apps/bert/forecasts/projections.php"
    "?League=-1&Position={code}&scoring=2&Segment={segment}&uid=4"
)
CRAWL_DELAY_S = 60  # robots.txt: "Crawl-delay: 60"
WEBSITE = "fantasysharks.com"

# Position -> (the page's Position parameter, the label its Position menu shows as selected).
PAGES = {
    "QB": (1, "Quarterback"),
    "RB": (2, "Running Back"),
    "WR": (4, "Wide Receiver"),
    "TE": (5, "Tight End"),
    "K": (7, "Kicker"),
    "DEF": (6, "Defense"),
}

# The site numbers weeks with a running Segment id: week N of 2026 is segment 882 + N.
SEGMENT_OFFSETS = {2026: 882}


class FantasySharksSource(ProjectionSource):
    name = "fantasysharks"
    website = WEBSITE
    supports_future_weeks = True
    # Its quarterback numbers are flat: every starter within a few points of the best, r 0.4 to 0.7
    # against Sleeper on the full week-4 page and three future weeks (2026-09-29). Rescoring the stat
    # line with league scoring removes the level gap but not the disagreement, so QB is not read.
    positions = POSITIONS - {"QB"}

    def fetch(self, season: int, week: int) -> list[Projection]:
        if season not in SEGMENT_OFFSETS:
            raise ValueError(f"no FantasySharks segment offset for season {season}; add one to SEGMENT_OFFSETS")
        segment = SEGMENT_OFFSETS[season] + week
        pages = {}
        for position, (code, _) in PAGES.items():
            if position not in self.positions:
                continue
            url = URL.format(code=code, segment=segment)
            pages[position] = get(url, spacing_s=CRAWL_DELAY_S).text
        return parse(pages, season, week)


def parse(pages: dict[str, str], season: int, week: int) -> list[Projection]:
    """`pages` maps each position to its page's HTML. Rows carry the week the page itself shows."""
    projections = []
    for position, html in pages.items():
        projections.extend(parse_page(html, position, season))
    return projections


def parse_page(html: str, position: str, season: int) -> list[Projection]:
    soup = BeautifulSoup(html, "lxml")
    shown_position = selected_option(soup, "Position")
    expected_position = PAGES[position][1]
    if shown_position != expected_position:
        raise ValueError(f"the {position} page shows {shown_position!r}, so the site ignored the Position parameter")
    page_week = shown_week(soup)

    table = soup.find("table", id="toolData")
    headers = [cell.get_text(strip=True) for cell in table.find("tr").find_all("th")]
    team_column = headers.index("Tm")
    points_column = headers.index("Pts")

    projections = []
    for row in table.find_all("tr"):
        link = row.find("a", href=re.compile(r"playerpage\.php\?id="))
        if link is None:
            continue  # the header, "Points Awarded" and "Tier N" rows
        cells = row.find_all("td")
        points = float(cells[points_column].get_text(strip=True))
        if points <= 0:
            continue

        name = link.get_text(strip=True)  # "Allen, Josh"; defenses read "Seahawks, Seattle"
        if position == "DEF":
            team = team_from_def_name(name)
            first_name, last_name = DEF_NAMES[team]
        else:
            team = normalize_team(cells[team_column].get_text(strip=True))
            last_name, _, first_name = name.partition(", ")
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
                external_id=re.search(r"id=(\d+)", link["href"]).group(1),
            )
        )
    return projections


def selected_option(soup: BeautifulSoup, menu: str) -> str:
    return soup.find("select", attrs={"name": menu}).find("option", selected=True).get_text(strip=True)


def shown_week(soup: BeautifulSoup) -> int:
    label = selected_option(soup, "Segment")
    match = re.fullmatch(r"Week (\d+)", label)
    if match is None:
        raise ValueError(f"the page shows segment {label!r}, not a week")
    return int(match.group(1))


SOURCE = FantasySharksSource()
