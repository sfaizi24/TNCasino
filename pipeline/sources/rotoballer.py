"""RotoBaller's weekly projections, read from the table in the week's article, which is found through the news sitemap.

Points are rescored from the stat line with league scoring, because the article's points are half PPR.
Read under the owner's permission letter of 2026-09-28: nothing is fetched but the sitemap and the week's article.
"""

import re
from datetime import datetime
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from pipeline.names import split_full_name
from pipeline.sources.base import Projection, ProjectionSource, get
from pipeline.sources.teams import normalize_team

SITEMAP_URL = "https://www.rotoballer.com/google-news-sitemap.xml"
WEBSITE = "rotoballer.com"

SITEMAP_NAMESPACES = {
    "sitemap": "http://www.sitemaps.org/schemas/sitemap/0.9",
    "news": "http://www.google.com/schemas/sitemap-news/0.9",
}

PLAYER_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})  # the table also lists kickers and defenses, without their stats

# (column header, league points per unit). Cells are read by header name, so a dropped column is noticed.
STAT_COLUMNS = [
    ("Pass Yards", 0.04),
    ("PASS TDs", 4),
    ("INTs", -2),
    ("Ru. Yards", 0.1),
    ("Ru. TDs", 6),
    ("Rec", 1),
    ("Rec. Yards", 0.1),
    ("Rec. TDs", 6),
]


class RotoBallerSource(ProjectionSource):
    name = "rotoballer"
    website = WEBSITE
    supports_future_weeks = False
    positions = PLAYER_POSITIONS

    def fetch(self, season: int, week: int) -> list[Projection]:
        sitemap = get(SITEMAP_URL)
        article = get(article_url(sitemap.text, season, week))
        return parse(article.text, season, week)


def article_url(sitemap_xml: str, season: int, week: int) -> str:
    """The week's projections article; when it has been updated, the latest one wins."""
    urls_by_date = {}
    for entry in ElementTree.fromstring(sitemap_xml).iterfind("sitemap:url", SITEMAP_NAMESPACES):
        url = entry.findtext("sitemap:loc", namespaces=SITEMAP_NAMESPACES).strip()
        if f"fantasy-football-projections-for-week-{week}-" in url and f"-{season}/" in url:
            date = entry.findtext("news:news/news:publication_date", namespaces=SITEMAP_NAMESPACES)
            urls_by_date[datetime.fromisoformat(date.strip())] = url
    if not urls_by_date:
        raise ValueError(f"RotoBaller has not posted week {week}: no projections article in the news sitemap")
    return urls_by_date[max(urls_by_date)]


def parse(html: str, season: int, week: int) -> list[Projection]:
    """Rows carry the week the article's title shows. Empty stat cells are zeros."""
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table")
    if table is None:
        raise ValueError(f"the RotoBaller page for week {week} has no projections table")
    header, *rows = table.find_all("tr")
    headers = [cell.get_text(" ", strip=True) for cell in header.find_all("td")]
    column = {name: column_index(headers, name, week) for name in ["Player Name", "Team", "Pos", "Fan Points"]}
    stat_columns = [(column_index(headers, name, week), weight) for name, weight in STAT_COLUMNS]
    page_week = shown_week(soup)

    projections = []
    for row in rows:
        cells = row.find_all("td")
        position = cells[column["Pos"]].get_text(strip=True)
        if position not in PLAYER_POSITIONS:
            continue
        points = 0.0
        for index, weight in stat_columns:
            points += weight * cell_value(cells[index])
        points = round(points, 2)
        if points <= 0:
            continue

        player_cell = cells[column["Player Name"]]
        link = player_cell.find("a", attrs={"data-id": True})  # a few players are not linked and have no id
        first_name, last_name = split_full_name(" ".join(player_cell.get_text().split()))
        projections.append(
            Projection(
                source=WEBSITE,
                season=season,
                week=page_week,
                first_name=first_name,
                last_name=last_name,
                position=position,
                team=normalize_team(cells[column["Team"]].get_text(strip=True)),
                points=points,
                external_id=link["data-id"] if link else None,
            )
        )
    return projections


def column_index(headers: list[str], name: str, week: int) -> int:
    if name not in headers:
        raise ValueError(f'the RotoBaller page for week {week} has no "{name}" column')
    return headers.index(name)


def cell_value(cell) -> float:
    text = cell.get_text(strip=True)
    return float(text) if text else 0.0


def shown_week(soup: BeautifulSoup) -> int:
    title = soup.find("h1")
    if title is None:
        raise ValueError("the RotoBaller page has no title to read the week from")
    match = re.search(r"Week (\d+)", title.get_text())
    if match is None:
        raise ValueError(f"the RotoBaller title reads {title.get_text(strip=True)!r}, not a week")
    return int(match.group(1))


SOURCE = RotoBallerSource()
