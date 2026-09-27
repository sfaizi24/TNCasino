import copy
from collections import Counter
from pathlib import Path

import pytest

from pipeline.sources import fantasypros, load_source
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


# Every source

# Each source module with the name of the fixture holding its captured payload.
SOURCES = [(fantasypros, "fantasypros_pages")]
SOURCE_IDS = ["fantasypros"]


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
    [("fantasypros", "fantasypros.com")],
)
def test_sources_are_registered(name, website):
    source = load_source(name)
    assert (source.name, source.website, source.supports_future_weeks) == (name, website, False)
