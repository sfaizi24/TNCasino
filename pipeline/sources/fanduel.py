"""FanDuel Research's weekly projections, captured from the GraphQL responses its projection pages load.

The PPR page covers QB, RB, WR and TE; kickers and defenses come from the weekly K and D/ST pages, whose points do
not depend on reception scoring. The pages render client-side, so a headless browser loads them.
"""

from playwright.sync_api import Browser, Response, sync_playwright

from pipeline.names import split_full_name
from pipeline.sources.base import Projection, ProjectionSource
from pipeline.sources.teams import DEF_NAMES, normalize_team

RESEARCH_URL = "https://www.fanduel.com/research/nfl/fantasy/"
GRAPHQL_PATH = "/research/api/graphql"
WEBSITE = "fanduel.com"
TIMEOUT_MS = 60_000

# Page under RESEARCH_URL -> the (projection type, position group) its GetProjections request selects.
# No request names a week: every page serves the current one.
PAGES = {
    "ppr": ("PPR", "NFL_SKILL"),
    "fantasy-football-projections/k": ("WEEKLY", "NFL_KICKER"),
    "fantasy-football-projections/defense": ("WEEKLY", "NFL_D_ST"),
}

POSITIONS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "K": "K", "D": "DEF"}


class FanDuelSource(ProjectionSource):
    name = "fanduel"
    website = WEBSITE
    supports_future_weeks = False

    def fetch(self, season: int, week: int) -> list[Projection]:
        items = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            for path, selection in PAGES.items():
                items.extend(capture_projections(browser, path, selection))
            browser.close()
        return parse(items, season, week)


def capture_projections(browser: Browser, path: str, selection: tuple[str, str]) -> list[dict]:
    """Open one projections page and return the getProjections items of the response it waits on."""
    page = browser.new_page()
    with page.expect_response(
        lambda response: requested_selection(response) == selection, timeout=TIMEOUT_MS
    ) as captured:
        page.goto(RESEARCH_URL + path, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
    items = captured.value.json()["data"]["getProjections"]
    page.close()
    return items


def requested_selection(response: Response) -> tuple[str, str] | None:
    """The (projection type, position group) a GetProjections response answers; None for any other response."""
    request = response.request
    if request.method != "POST" or GRAPHQL_PATH not in request.url:
        return None
    body = request.post_data_json
    if body.get("operationName") != "GetProjections":
        return None
    selected = body["variables"]["input"]
    return selected["type"], selected["position"]


def parse(items: list[dict], season: int, week: int) -> list[Projection]:
    """`items` are getProjections entries from all three pages. They name no week, so rows get the requested one."""
    projections = []
    for item in items:
        player = item["player"]
        position = POSITIONS.get(player["position"])
        if position is None or item["fantasy"] <= 0:
            continue

        team = normalize_team(item["team"]["abbreviation"])  # FanDuel writes JAC, LA and WSH
        if position == "DEF":
            first_name, last_name = DEF_NAMES[team]  # the player is named "Kansas City D/ST"
        else:
            first_name, last_name = split_full_name(player["name"])
        projections.append(
            Projection(
                source=WEBSITE,
                season=season,
                week=week,
                first_name=first_name,
                last_name=last_name,
                position=position,
                team=team,
                points=float(item["fantasy"]),
                external_id=str(player["numberFireId"]),
            )
        )
    return projections


SOURCE = FanDuelSource()
