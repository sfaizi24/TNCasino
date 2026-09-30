"""Projections in the owner's own Fleaflicker league's scoring, read through the documented API under the owner's
letter of 2026-09-28: two operations, FetchLeagueRules and FetchPlayerListing, and nothing else. The points cannot be
rescored, so every run first compares the league's rules with the pinned set.
"""

import json
import os
from collections import Counter
from pathlib import Path
from urllib.parse import urlencode

from pipeline.sources.base import Projection, ProjectionSource, get
from pipeline.sources.teams import DEF_NAMES, normalize_team

API_URL = "https://www.fleaflicker.com/api/{operation}"
WEBSITE = "fleaflicker.com"
LEAGUE_ID_VARIABLE = "FLEAFLICKER_LEAGUE_ID"
PAGE_SIZE = 30
MAX_PAGES = 10

POSITIONS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "D/ST": "DEF"}  # filter label -> canonical
HEADERS = {"Accept": "application/json"}
RULES_PATH = Path(__file__).with_name("fleaflicker_rules.json")


class FleaflickerSource(ProjectionSource):
    name = "fleaflicker"
    website = WEBSITE
    supports_future_weeks = False
    positions = frozenset(POSITIONS.values())

    def fetch(self, season: int, week: int) -> list[Projection]:
        league_id = os.environ.get(LEAGUE_ID_VARIABLE, "").strip()
        if not league_id:
            raise RuntimeError(f"{LEAGUE_ID_VARIABLE} is not set; the Fleaflicker source reads the owner's own league")

        rules = get(api_url("FetchLeagueRules", {"sport": "NFL", "league_id": league_id}), headers=HEADERS)
        check_rules(rules.json())
        pages = {label: listing_pages(league_id, season, week, label) for label in POSITIONS}
        return parse(pages, season, week)


def api_url(operation: str, params: dict) -> str:
    return f"{API_URL.format(operation=operation)}?{urlencode(params)}"


def listing_pages(league_id: str, season: int, week: int, label: str) -> list[dict]:
    """The label's players by projection, page after page, until the projections run out."""
    pages = []
    offset = 0
    while offset is not None:
        if len(pages) == MAX_PAGES:
            raise ValueError(
                f"Fleaflicker listed more than {MAX_PAGES} pages of {label} players for week {week}: "
                "the position filter or the projection sort was ignored"
            )
        params = {
            "sport": "NFL",
            "league_id": league_id,
            "sort": "SORT_PROJECTIONS",
            "sort_season": season,
            "sort_period": week,
            "filter.position.eligibility": label,
            "result_offset": offset,
        }
        page = get(api_url("FetchPlayerListing", params), headers=HEADERS).json()
        pages.append(page)
        offset = next_offset(page)
    return pages


def next_offset(page: dict) -> int | None:
    players = page.get("players", [])
    if "resultOffsetNext" not in page or not players:
        return None
    last_points = projected_points(players[-1])
    if last_points is None or last_points <= 0:
        return None
    return page["resultOffsetNext"]


def parse(pages: dict[str, list[dict]], season: int, week: int) -> list[Projection]:
    """`pages` maps each position filter label to its listing pages. Rows carry the week the listing is for."""
    projections = []
    seen_ids = set()
    for label, label_pages in pages.items():
        check_listing(label, label_pages, week)
        for page in label_pages:
            for row in page.get("players", []):
                player = row["proPlayer"]
                points = projected_points(row)
                if points is None or points <= 0 or player["id"] in seen_ids:
                    continue
                seen_ids.add(player["id"])

                position = POSITIONS[player["position"]]
                team = normalize_team(player.get("proTeamAbbreviation"))
                if position == "DEF":
                    first_name, last_name = DEF_NAMES[team]
                else:
                    first_name, last_name = player["nameFirst"], player["nameLast"]
                projections.append(
                    Projection(
                        source=WEBSITE,
                        season=season,
                        week=row["requestedGamesPeriod"]["ordinal"],
                        first_name=first_name,
                        last_name=last_name,
                        position=position,
                        team=team,
                        points=points,
                        external_id=str(player["id"]),
                    )
                )
    return projections


def check_listing(label: str, label_pages: list[dict], week: int) -> None:
    strays = set()
    for page in label_pages:
        for row in page.get("players", []):
            if label not in row["proPlayer"]["positionEligibility"]:
                strays.add(row["proPlayer"]["position"])
    if strays:
        raise ValueError(
            f"the Fleaflicker listing for {label} holds {', '.join(sorted(strays))} players: "
            f"the league does not start {label}, so the filter fell back to ALL"
        )

    first_page = label_pages[0].get("players", [])
    if all(projected_points(row) is None for row in first_page):
        raise ValueError(f"Fleaflicker has no {label} projections for week {week}: it projects only the week in play")


def projected_points(row: dict) -> float | None:
    """None when the player has no game that week or the week is not in play. A value of 0 is omitted on the wire."""
    games = row.get("requestedGames")
    if not games or "pointsProjected" not in games[0]:
        return None
    return round(games[0]["pointsProjected"].get("value", 0.0), 2)


def check_rules(payload: dict) -> None:
    league_rules = Counter()
    descriptions = {}
    for group in payload["groups"]:
        for rule in group.get("scoringRules", []):
            key = rule_key(rule)
            league_rules[key] += 1
            descriptions[key] = rule["description"]

    differences = [f"not pinned: {descriptions[key]}" for key in (league_rules - PINNED_RULES).elements()]
    for key in (PINNED_RULES - league_rules).elements():
        differences.append(f"missing: category {key[0]} at {key[1]} points")
    if differences:
        raise ValueError("the Fleaflicker league's scoring differs from the pinned rules: " + "; ".join(differences))


def rule_key(rule: dict) -> tuple:
    """The fields that decide a rule's points. Protobuf leaves out a field at its default, so absent is 0 or false."""
    applies_to = "ALL" if rule.get("applyToAll") else tuple(sorted(rule["applyTo"]))
    return (
        rule["category"]["id"],
        rule["points"]["value"],
        rule.get("forEvery"),
        rule.get("boundLower"),
        rule.get("boundUpper"),
        bool(rule.get("isBonus")),
        rule.get("rangeType"),
        applies_to,
    )


def pinned_key(rule: dict) -> tuple:
    applies_to = "ALL" if rule["applyTo"] == "ALL" else tuple(rule["applyTo"])
    return (
        rule["category"],
        rule["points"],
        rule["forEvery"],
        rule["boundLower"],
        rule["boundUpper"],
        rule["isBonus"],
        rule["rangeType"],
        applies_to,
    )


PINNED_RULES = Counter(pinned_key(rule) for rule in json.loads(RULES_PATH.read_text(encoding="utf-8")))

SOURCE = FleaflickerSource()
