"""FFToday's weekly projections, read from the server-rendered projections table, one or two pages per position.

Points are rescored from the stat line with league scoring, because the site's PPR preset scores quarterbacks differently.
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from pipeline.names import split_full_name
from pipeline.sources.base import Projection, ProjectionSource, get
from pipeline.sources.teams import normalize_team

URL = "https://www.fftoday.com/rankings/playerwkproj.php?Season={season}&GameWeek={week}&PosID={code}&LeagueID=107644"
CRAWL_SPACING_S = 5.0  # no robots.txt; six requests a week, five seconds apart, is our own policy
WEBSITE = "fftoday.com"

POSITION_CODES = {"QB": 10, "RB": 20, "WR": 30, "TE": 40}

# (column header, league points per unit). The headers repeat across groups, so cells are read by index.
PASSING = [("Comp", 0), ("Att", 0), ("Yard", 0.04), ("TD", 4), ("INT", -2)]
RUSHING = [("Att", 0), ("Yard", 0.1), ("TD", 6)]
RECEIVING = [("Rec", 1), ("Yard", 0.1), ("TD", 6)]
STAT_COLUMNS = {
    "QB": PASSING + RUSHING,
    "RB": RUSHING + RECEIVING,
    "WR": RUSHING + RECEIVING,
    "TE": RUSHING + RECEIVING,
}
LEADING_COLUMNS = ["Chg", "Player", "Team", "Opp"]
TEAM_COLUMN = 2


class FFTodaySource(ProjectionSource):
    name = "fftoday"
    website = WEBSITE
    supports_future_weeks = False
    positions = frozenset(POSITION_CODES)  # kicks carry no distances and no defenses are published

    def fetch(self, season: int, week: int) -> list[Projection]:
        pages = {}
        for position, code in POSITION_CODES.items():
            url = URL.format(season=season, week=week, code=code)
            first_page = get(url, spacing_s=CRAWL_SPACING_S).text
            pages[position] = [first_page]
            second_url = next_page_url(first_page, url)
            if second_url is not None:
                pages[position].append(get(second_url, spacing_s=CRAWL_SPACING_S).text)
        return parse(pages, season, week)


def parse(pages: dict[str, list[str]], season: int, week: int) -> list[Projection]:
    """`pages` maps each position to the HTML of its pages. Rows carry the week the page itself shows."""
    projections = []
    for position, htmls in pages.items():
        for html in htmls:
            projections.extend(parse_page(html, position, season, week))
    return projections


def parse_page(html: str, position: str, season: int, week: int) -> list[Projection]:
    soup = BeautifulSoup(html, "lxml")
    header = projections_header(soup, position, week)
    page_week = shown_week(soup)
    stat_columns = STAT_COLUMNS[position]

    projections = []
    for row in header.find_next_siblings("tr"):
        link = row.find("a", href=re.compile(r"/stats/players/\d+/"))
        cells = row.find_all("td")
        stats = cells[len(LEADING_COLUMNS) : len(LEADING_COLUMNS) + len(stat_columns)]
        points = 0.0
        for cell, (_, weight) in zip(stats, stat_columns, strict=True):
            points += weight * float(cell.get_text(strip=True))
        points = round(points, 2)
        if points <= 0:
            continue

        first_name, last_name = split_full_name(link.get_text(strip=True))
        projections.append(
            Projection(
                source=WEBSITE,
                season=season,
                week=page_week,
                first_name=first_name,
                last_name=last_name,
                position=position,
                team=normalize_team(cells[TEAM_COLUMN].get_text(strip=True)),
                points=points,
                external_id=re.search(r"/stats/players/(\d+)/", link["href"]).group(1),
            )
        )
    return projections


def projections_header(soup: BeautifulSoup, position: str, week: int):
    header = soup.find("tr", class_="tableclmhdr")
    if header is None:
        if "No Player Found!" in soup.get_text():
            raise ValueError(f'FFToday has no {position} table for week {week}: the page reads "No Player Found!"')
        raise ValueError(f"FFToday has no {position} table for week {week}")

    # The Player cell also holds its sort links ("Player Sort First: Last:"), so each cell is named by its first word.
    shown = [cell.get_text(" ", strip=True).partition(" ")[0] for cell in header.find_all("td")]
    expected = LEADING_COLUMNS + [name for name, _ in STAT_COLUMNS[position]] + ["FPts"]
    if shown != expected:
        raise ValueError(f"the FFToday {position} table for week {week} has columns {shown}, expected {expected}")
    return header


def shown_week(soup: BeautifulSoup) -> int:
    stamp = soup.find("td", class_="update")
    if stamp is None:
        raise ValueError("the FFToday page has no week stamp")
    match = re.search(r"(\d{4}) Week (\d+)", stamp.get_text())
    if match is None:
        raise ValueError(f"the FFToday week stamp reads {stamp.get_text(strip=True)!r}, not a week")
    return int(match.group(2))


def next_page_url(html: str, url: str) -> str | None:
    link = BeautifulSoup(html, "lxml").find("a", string="Next Page")
    if link is None:
        return None
    return urljoin(url, link["href"])


SOURCE = FFTodaySource()
