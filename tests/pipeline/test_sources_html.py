import copy
import json
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.sources import fanduel, fantasypros, firstdown, load_source
from pipeline.sources.base import POSITIONS, Projection
from pipeline.sources.teams import CANONICAL_TEAMS

FIXTURES = Path(__file__).parent / "fixtures"
SEASON = 2026
WEEK = 3


@pytest.fixture(scope="module")
def fantasypros_pages() -> dict[str, str]:
    pages = {}
    for position, page in fantasypros.PAGES.items():
        pages[position] = (FIXTURES / "fantasypros" / f"{page}.html").read_text(encoding="utf-8")
    return pages


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


# FantasyPros


def test_fantasypros_reads_the_ten_players_a_logged_out_visitor_sees(fantasypros_pages):
    rows = fantasypros.parse(fantasypros_pages, SEASON, WEEK)
    assert count_by_position(rows) == {"QB": 10, "RB": 10, "WR": 10, "TE": 10, "K": 10, "DEF": 10}


def test_fantasypros_player_row(fantasypros_pages):
    rows = fantasypros.parse(fantasypros_pages, SEASON, WEEK)
    assert find(rows, "Josh", "Allen") == Projection(
        source="fantasypros.com",
        season=2026,
        week=3,
        first_name="Josh",
        last_name="Allen",
        position="QB",
        team="BUF",
        points=23.723,
        external_id="17298",
    )


def test_fantasypros_defense_takes_the_sleeper_form(fantasypros_pages):
    rows = fantasypros.parse(fantasypros_pages, SEASON, WEEK)
    chiefs = find(rows, "Kansas City", "Chiefs")  # listed as "Kansas City Chiefs"
    assert (chiefs.position, chiefs.team, chiefs.points, chiefs.external_id) == ("DEF", "KC", 7.654, "8150")
    assert find(rows, "New York", "Giants").team == "NYG"


def test_fantasypros_keeps_suffixes_and_normalises_team_aliases(fantasypros_pages):
    rows = fantasypros.parse(fantasypros_pages, SEASON, WEEK)
    assert find(rows, "Patrick", "Mahomes II").team == "KC"
    assert find(rows, "Kenneth", "Walker III").team == "KC"
    assert find(rows, "Trevor", "Lawrence").team == "JAX"  # the page writes JAC


def test_fantasypros_stamps_the_week_the_page_shows(fantasypros_pages):
    rows = fantasypros.parse(fantasypros_pages, SEASON, 4)
    assert {row.week for row in rows} == {3}


def test_fantasypros_rejects_a_page_without_a_week(fantasypros_pages):
    page = fantasypros_pages["QB"].replace("Week 3", "Draft")
    with pytest.raises(ValueError, match="names no week"):
        fantasypros.parse({"QB": page}, SEASON, WEEK)


def test_fantasypros_skips_players_projected_for_nothing(fantasypros_pages):
    page = fantasypros_pages["QB"].replace('data-sort-value="23.723"', 'data-sort-value="0"')
    rows = fantasypros.parse({"QB": page}, SEASON, WEEK)
    assert len(rows) == 9
    assert ("Josh", "Allen") not in {(row.first_name, row.last_name) for row in rows}


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


# Every source

# Each source module with the name of the fixture holding its captured payload.
SOURCES = [(fantasypros, "fantasypros_pages"), (firstdown, "firstdown_html"), (fanduel, "fanduel_items")]
SOURCE_IDS = ["fantasypros", "firstdown", "fanduel"]


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
    [("fantasypros", "fantasypros.com"), ("firstdown", "firstdown.studio"), ("fanduel", "fanduel.com")],
)
def test_sources_are_registered(name, website):
    source = load_source(name)
    assert (source.name, source.website, source.supports_future_weeks) == (name, website, False)
