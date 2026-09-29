import copy
import json
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from pipeline.sources import base, fanduel, fftoday, firstdown, load_source
from pipeline.sources.base import POSITIONS, USER_AGENT, Projection
from pipeline.sources.teams import CANONICAL_TEAMS

FIXTURES = Path(__file__).parent / "fixtures"
SEASON = 2026
WEEK = 3


@pytest.fixture(scope="module")
def firstdown_html() -> str:
    return (FIXTURES / "firstdown" / "rankings.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def fanduel_items() -> list[dict]:
    return json.loads((FIXTURES / "fanduel" / "getProjections.json").read_text(encoding="utf-8"))


def find(rows: list[Projection], first_name: str, last_name: str) -> Projection:
    matches = [row for row in rows if (row.first_name, row.last_name) == (first_name, last_name)]
    assert len(matches) == 1, f"expected one {first_name} {last_name}, found {len(matches)}"
    return matches[0]


def count_by_position(rows: list[Projection]) -> dict[str, int]:
    return dict(Counter(row.position for row in rows))


# FirstDown


def test_firstdown_reads_every_position_from_one_page(firstdown_html):
    rows = firstdown.parse(firstdown_html, SEASON, WEEK)
    assert count_by_position(rows) == {"QB": 32, "RB": 48, "WR": 61, "TE": 26, "K": 30}


def test_firstdown_player_row(firstdown_html):
    rows = firstdown.parse(firstdown_html, SEASON, WEEK)
    assert find(rows, "Josh", "Allen") == Projection(
        source="firstdown.studio",
        season=2026,
        week=3,
        first_name="Josh",
        last_name="Allen",
        position="QB",
        team="BUF",
        points=23.74,
        external_id="d647a471-4e45-431d-8a08-bdc4b5f6c801",
    )


def test_firstdown_reads_ppr_not_the_half_ppr_the_table_shows(firstdown_html):
    rows = firstdown.parse(firstdown_html, SEASON, WEEK)
    assert find(rows, "Jahmyr", "Gibbs").points == 24.57  # the table shows 22.3
    assert find(rows, "Jaxon", "Smith-Njigba").points == 19.46  # the table shows 16.1


def test_firstdown_kicker_gets_kicking_points_not_team_points(firstdown_html):
    rows = firstdown.parse(firstdown_html, SEASON, WEEK)
    pineiro = find(rows, "Eddy", "Pineiro")  # the K table shows Kick Pts 8.6 beside Proj. Team Pts 28.0
    assert (pineiro.position, pineiro.team, pineiro.points) == ("K", "SF", 8.57)


def test_firstdown_stamps_the_snapshot_week(firstdown_html):
    rows = firstdown.parse(firstdown_html, SEASON, 4)
    assert {row.week for row in rows} == {3}


def test_firstdown_rejects_a_page_without_a_snapshot():
    with pytest.raises(ValueError, match="no rankings snapshot"):
        firstdown.parse("<html><body><table></table></body></html>", SEASON, WEEK)


def test_firstdown_skips_players_projected_for_nothing(firstdown_html):
    html = firstdown_html.replace("24.56727045366198", "0")  # Jahmyr Gibbs's PPR points in the snapshot
    rows = firstdown.parse(html, SEASON, WEEK)
    assert len(rows) == 196
    assert ("Jahmyr", "Gibbs") not in {(row.first_name, row.last_name) for row in rows}


def test_firstdown_serves_no_defenses():
    assert firstdown.SOURCE.positions == {"QB", "RB", "WR", "TE", "K"}


# FanDuel


def test_fanduel_canonicalises_positions(fanduel_items):
    rows = fanduel.parse(fanduel_items, SEASON, WEEK)
    assert count_by_position(rows) == {"QB": 21, "RB": 23, "WR": 22, "TE": 21, "K": 10, "DEF": 13}


def test_fanduel_player_row(fanduel_items):
    rows = fanduel.parse(fanduel_items, SEASON, WEEK)
    assert find(rows, "Josh", "Allen") == Projection(
        source="fanduel.com",
        season=2026,
        week=3,
        first_name="Josh",
        last_name="Allen",
        position="QB",
        team="BUF",
        points=22.52,
        external_id="53775",
    )


def test_fanduel_defense_takes_the_sleeper_form(fanduel_items):
    rows = fanduel.parse(fanduel_items, SEASON, WEEK)
    chiefs = find(rows, "Kansas City", "Chiefs")  # position "D", named "Kansas City D/ST"
    assert (chiefs.position, chiefs.team, chiefs.points) == ("DEF", "KC", 9.27)
    assert find(rows, "Los Angeles", "Rams").team == "LAR"
    assert find(rows, "Washington", "Commanders").team == "WAS"


def test_fanduel_keeps_suffixes(fanduel_items):
    rows = fanduel.parse(fanduel_items, SEASON, WEEK)
    assert find(rows, "Kenneth", "Walker III").points == 16.2
    assert find(rows, "Michael", "Penix Jr.").team == "ATL"
    assert find(rows, "Ollie", "Gordon II").team == "MIA"


@pytest.mark.parametrize(
    ("first_name", "last_name", "team"),
    [("Brian", "Thomas Jr.", "JAX"), ("Puka", "Nacua", "LAR"), ("Terry", "McLaurin", "WAS")],
)
def test_fanduel_normalises_team_aliases(fanduel_items, first_name, last_name, team):
    rows = fanduel.parse(fanduel_items, SEASON, WEEK)
    assert find(rows, first_name, last_name).team == team


def test_fanduel_skips_players_projected_for_nothing(fanduel_items):
    rows = fanduel.parse(fanduel_items, SEASON, WEEK)
    zero_items = [item for item in fanduel_items if item["fantasy"] <= 0]
    assert len(zero_items) == 12
    assert len(rows) == len(fanduel_items) - len(zero_items)


def test_fanduel_whole_number_points_become_floats(fanduel_items):
    jefferson = find(fanduel.parse(fanduel_items, SEASON, WEEK), "Justin", "Jefferson")
    assert jefferson.points == 14.0
    assert isinstance(jefferson.points, float)


def test_fanduel_stamps_the_requested_week(fanduel_items):
    rows = fanduel.parse(fanduel_items, SEASON, 4)
    assert {row.week for row in rows} == {4}


def graphql_response(method: str, url: str, body: dict | None) -> SimpleNamespace:
    return SimpleNamespace(request=SimpleNamespace(method=method, url=url, post_data_json=body))


def test_fanduel_recognises_the_projections_response_by_its_selection():
    url = "https://www.fanduel.com/research/api/graphql"
    body = {
        "operationName": "GetProjections",
        "variables": {"input": {"type": "PPR", "position": "NFL_SKILL", "sport": "NFL"}},
    }
    assert fanduel.requested_selection(graphql_response("POST", url, body)) == ("PPR", "NFL_SKILL")
    assert fanduel.requested_selection(graphql_response("POST", url, {"operationName": "GetMenus"})) is None
    assert fanduel.requested_selection(graphql_response("GET", url, None)) is None
    other_url = "https://www.fanduel.com/research/nfl/fantasy/ppr"
    assert fanduel.requested_selection(graphql_response("POST", other_url, body)) is None


# FFToday

FFTODAY_PAGE_FILES = {
    "QB": ["projections_2026_w3_qb.html"],
    "RB": ["projections_2026_w3_rb.html", "projections_2026_w3_rb_page2.html"],
    "WR": ["projections_2026_w3_wr.html", "projections_2026_w3_wr_page2.html"],
    "TE": ["projections_2026_w3_te.html"],
}


def fftoday_page(file_name: str) -> str:
    return (FIXTURES / "fftoday" / file_name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def fftoday_pages() -> dict[str, list[str]]:
    return {position: [fftoday_page(name) for name in names] for position, names in FFTODAY_PAGE_FILES.items()}


@pytest.fixture(scope="module")
def fftoday_rows(fftoday_pages) -> list[Projection]:
    return fftoday.parse(fftoday_pages, SEASON, WEEK)


def test_fftoday_reads_every_page_of_every_position(fftoday_rows):
    assert count_by_position(fftoday_rows) == {"QB": 32, "RB": 72, "WR": 99, "TE": 42}


def test_fftoday_rescore_matches_the_ppr_preset_outside_quarterbacks(fftoday_pages, fftoday_rows):
    points_by_id = {row.external_id: row.points for row in fftoday_rows}
    compared = 0
    for position in ("RB", "WR", "TE"):
        for html in fftoday_pages[position]:
            header = BeautifulSoup(html, "lxml").find("tr", class_="tableclmhdr")
            for row in header.find_next_siblings("tr"):
                player_id = re.search(r"/stats/players/(\d+)/", row.find("a")["href"]).group(1)
                page_points = float(row.find_all("td")[-1].get_text(strip=True))
                assert points_by_id[player_id] == page_points, player_id
                compared += 1
    assert compared == 72 + 99 + 42


def test_fftoday_player_row(fftoday_rows):
    # 0.1 * 95 rushing yards + 6 * 1.0 rushing TD + 4 catches + 0.1 * 32 receiving yards + 6 * 0.1 receiving TD
    assert find(fftoday_rows, "Jahmyr", "Gibbs") == Projection(
        source="fftoday.com",
        season=2026,
        week=3,
        first_name="Jahmyr",
        last_name="Gibbs",
        position="RB",
        team="DET",
        points=23.3,
        external_id="18522",
    )


def test_fftoday_rescores_quarterbacks_with_league_scoring(fftoday_rows):
    # 257 passing yards, 1.7 TD, 0.3 INT, 38 rushing yards, 0.9 rushing TD; the page shows 28.9
    rescore = round(0.04 * 257 + 4 * 1.7 - 2 * 0.3 + 0.1 * 38 + 6 * 0.9, 2)
    allen = find(fftoday_rows, "Josh", "Allen")
    assert (allen.points, allen.team, allen.external_id) == (rescore, "BUF", "16228")
    assert rescore == 25.68


def test_fftoday_week_not_posted_is_refused():
    html = fftoday_page("projections_2026_w4_not_posted.html")
    with pytest.raises(ValueError, match='no QB table for week 4: the page reads "No Player Found!"'):
        fftoday.parse({"QB": [html]}, SEASON, 4)


def test_fftoday_rejects_a_changed_header(fftoday_pages):
    html = fftoday_pages["RB"][0].replace("<B>Yard</B>", "<B>Yds</B>", 1)
    with pytest.raises(ValueError, match="FFToday RB table for week 3 has columns"):
        fftoday.parse({"RB": [html]}, SEASON, WEEK)


def test_fftoday_stamps_the_page_week(fftoday_pages):
    rows = fftoday.parse(fftoday_pages, SEASON, 4)
    assert {row.week for row in rows} == {3}


@pytest.mark.parametrize(
    ("first_name", "last_name", "position"),
    [("Brian", "Thomas Jr.", "WR"), ("De'Von", "Achane", "RB"), ("Jaxon", "Smith-Njigba", "WR")],
)
def test_fftoday_keeps_suffixes_apostrophes_and_hyphens(fftoday_rows, first_name, last_name, position):
    assert find(fftoday_rows, first_name, last_name).position == position


def test_fftoday_normalises_jac(fftoday_rows):
    assert find(fftoday_rows, "Jakobi", "Meyers").team == "JAX"  # WR page 2 writes JAC


def test_fftoday_skips_players_projected_for_nothing(fftoday_pages):
    cell = '<TD class="smallbody" ALIGN="center" BGCOLOR="#ffffff">{}</TD>'
    gibbs_stats = "\n".join(cell.format(value) for value in ["19.0", "95.0", "1.0", "4.0", "32.0", "0.1"])
    zeros = "\n".join(cell.format("0.0") for _ in range(6))
    assert gibbs_stats in fftoday_pages["RB"][0]
    html = fftoday_pages["RB"][0].replace(gibbs_stats, zeros)
    rows = fftoday.parse({"RB": [html]}, SEASON, WEEK)
    assert len(rows) == 49
    assert ("Jahmyr", "Gibbs") not in {(row.first_name, row.last_name) for row in rows}


class Clock:
    """A fake for base.time's clock: time passes only when something sleeps, and each sleep is recorded."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(base.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(base.time, "sleep", clock.sleep)
    monkeypatch.setattr(base, "_last_request_at", {})
    return clock


def serve(monkeypatch, body_for_url) -> list[dict]:
    """Answers base.get's requests with body_for_url(url) and returns the list each request is recorded in."""
    sent = []

    def fake_get(url, headers, timeout):
        sent.append({"url": url, "headers": headers, "timeout": timeout})
        return SimpleNamespace(text=body_for_url(url), raise_for_status=lambda: None)

    monkeypatch.setattr(base.requests, "get", fake_get)
    return sent


def test_fftoday_fetch_follows_each_next_page_link_five_seconds_apart(monkeypatch, clock, fftoday_pages):
    position_by_code = {code: position for position, code in fftoday.POSITION_CODES.items()}

    def page_for(url):
        position = position_by_code[int(re.search(r"PosID=(\d+)", url).group(1))]
        page_index = 1 if "cur_page=1" in url else 0
        return fftoday_pages[position][page_index]

    sent = serve(monkeypatch, page_for)

    rows = fftoday.SOURCE.fetch(2026, 3)

    base_url = "https://www.fftoday.com/rankings/playerwkproj.php?Season=2026&GameWeek=3&PosID={}&LeagueID=107644"
    next_page = "&order_by=FFPts&sort_order=DESC&cur_page=1"
    assert [request["url"] for request in sent] == [
        base_url.format(10),
        base_url.format(20),
        base_url.format(20) + next_page,
        base_url.format(30),
        base_url.format(30) + next_page,
        base_url.format(40),
    ]
    assert all(request["headers"] == {"User-Agent": USER_AGENT} for request in sent)
    assert clock.sleeps == [5.0] * 5
    assert count_by_position(rows) == {"QB": 32, "RB": 72, "WR": 99, "TE": 42}


# Every source

# Each source module with the name of the fixture holding its captured payload.
SOURCES = [(firstdown, "firstdown_html"), (fanduel, "fanduel_items"), (fftoday, "fftoday_pages")]
SOURCE_IDS = ["firstdown", "fanduel", "fftoday"]


@pytest.mark.parametrize(("module", "payload_fixture"), SOURCES, ids=SOURCE_IDS)
def test_parse_is_pure(module, payload_fixture, request):
    payload = request.getfixturevalue(payload_fixture)
    untouched = copy.deepcopy(payload)
    assert module.parse(payload, SEASON, WEEK) == module.parse(payload, SEASON, WEEK)
    assert payload == untouched


@pytest.mark.parametrize(("module", "payload_fixture"), SOURCES, ids=SOURCE_IDS)
def test_rows_are_canonical(module, payload_fixture, request):
    payload = request.getfixturevalue(payload_fixture)
    for row in module.parse(payload, SEASON, WEEK):
        assert row.source == module.WEBSITE
        assert row.season == SEASON
        assert row.position in POSITIONS
        assert row.team in CANONICAL_TEAMS
        assert row.points > 0
        assert row.external_id


@pytest.mark.parametrize(
    ("name", "website"),
    [("firstdown", "firstdown.studio"), ("fanduel", "fanduel.com"), ("fftoday", "fftoday.com")],
)
def test_sources_are_registered(name, website):
    source = load_source(name)
    assert (source.name, source.website, source.supports_future_weeks) == (name, website, False)
