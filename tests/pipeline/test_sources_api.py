import copy
import json
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

from pipeline.sources import espn, fantasysharks, sleeper
from pipeline.sources.base import Projection
from pipeline.sources.teams import CANONICAL_TEAMS

FIXTURES = Path(__file__).parent / "fixtures"
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]


def load_json(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_sharks_pages() -> dict[str, str]:
    pages = {}
    for position in POSITIONS:
        path = FIXTURES / "fantasysharks" / f"projections_2026_w4_{position.lower()}.html"
        pages[position] = path.read_text(encoding="utf-8")
    return pages


def find(rows: list[Projection], first_name: str, last_name: str) -> Projection:
    matches = [row for row in rows if row.first_name == first_name and row.last_name == last_name]
    assert len(matches) == 1, f"{first_name} {last_name}: {len(matches)} rows"
    return matches[0]


def names(rows: list[Projection]) -> set[tuple[str, str]]:
    return {(row.first_name, row.last_name) for row in rows}


def rows_per_position(rows: list[Projection]) -> dict[str, int]:
    return dict(Counter(row.position for row in rows))


@pytest.fixture(scope="module")
def sleeper_rows():
    return sleeper.parse(load_json("sleeper/projections_2026_w4.json"), 2026, 4)


@pytest.fixture(scope="module")
def espn_payload():
    return load_json("espn/projections_2026_w4.json")


@pytest.fixture(scope="module")
def espn_rows(espn_payload):
    return espn.parse(espn_payload, 2026, 4)


@pytest.fixture(scope="module")
def sharks_pages():
    return load_sharks_pages()


@pytest.fixture(scope="module")
def sharks_rows(sharks_pages):
    return fantasysharks.parse(sharks_pages, 2026, 4)


def test_sleeper_rows_per_position(sleeper_rows):
    assert rows_per_position(sleeper_rows) == {"QB": 24, "RB": 44, "WR": 54, "TE": 24, "K": 18, "DEF": 24}


def test_sleeper_player_row(sleeper_rows):
    assert find(sleeper_rows, "Josh", "Allen") == Projection(
        "sleeper.com", 2026, 4, "Josh", "Allen", "QB", "BUF", 23.27, external_id="4984"
    )


def test_sleeper_defense_row(sleeper_rows):
    assert find(sleeper_rows, "Minnesota", "Vikings") == Projection(
        "sleeper.com", 2026, 4, "Minnesota", "Vikings", "DEF", "MIN", 9.94, external_id="MIN"
    )


def test_sleeper_names_have_no_suffixes(sleeper_rows):
    walker = find(sleeper_rows, "Kenneth", "Walker")

    assert (walker.position, walker.team) == ("RB", "KC")


def test_sleeper_skips_unprojected_zero_and_other_positions(sleeper_rows):
    parsed = names(sleeper_rows)

    assert ("Greg", "Ward") not in parsed  # listed without a projection
    assert ("Le'Veon", "Moss") not in parsed  # projected 0.0
    assert ("Travis", "Hunter") not in parsed  # DB
    assert ("Kyle", "Juszczyk") not in parsed  # FB


def test_sleeper_rows_carry_the_payload_week():
    rows = sleeper.parse(load_json("sleeper/projections_2026_w4.json"), 2026, 5)

    assert {row.week for row in rows} == {4}


def test_espn_rows_per_position(espn_rows):
    assert rows_per_position(espn_rows) == {"QB": 11, "RB": 30, "WR": 36, "TE": 13, "K": 20, "DEF": 20}


def test_espn_player_row(espn_rows):
    assert find(espn_rows, "Josh", "Allen") == Projection(
        "espn.com", 2026, 4, "Josh", "Allen", "QB", "BUF", 22.69, external_id="3918298"
    )


def test_espn_defense_row_takes_sleeper_form(espn_rows):
    assert find(espn_rows, "Seattle", "Seahawks") == Projection(
        "espn.com", 2026, 4, "Seattle", "Seahawks", "DEF", "SEA", 7.92, external_id="-16026"
    )


def test_espn_keeps_name_suffixes(espn_rows):
    assert find(espn_rows, "Marvin", "Harrison Jr.").team == "ARI"
    assert find(espn_rows, "Kenneth", "Walker III").team == "KC"


def test_espn_free_agent_has_no_team(espn_rows):
    assert find(espn_rows, "Zach", "Ertz").team is None


def test_espn_skips_players_without_a_positive_projection(espn_rows):
    parsed = names(espn_rows)

    assert ("A.J.", "Brown") not in parsed  # projected 0.0
    assert ("Jayden", "Daniels") not in parsed  # projected 0.0
    assert ("Tyreek", "Hill") not in parsed  # no stats for the week


def test_espn_skips_positions_outside_the_six(espn_payload):
    item = copy.deepcopy(espn_payload["players"][0])
    assert len(espn.parse({"players": [item]}, 2026, 4)) == 1

    item["player"]["defaultPositionId"] = 7

    assert espn.parse({"players": [item]}, 2026, 4) == []


def test_espn_projection_is_the_projected_entry_for_the_week():
    stats = [
        {"statSourceId": 0, "statSplitTypeId": 1, "scoringPeriodId": 4, "appliedTotal": 31.0},
        {"statSourceId": 1, "statSplitTypeId": 0, "scoringPeriodId": 0, "appliedTotal": 350.0},
        {"statSourceId": 1, "statSplitTypeId": 1, "scoringPeriodId": 3, "appliedTotal": 18.0},
        {"statSourceId": 1, "statSplitTypeId": 1, "scoringPeriodId": 4, "appliedTotal": 21.5},
    ]

    assert espn.weekly_projection(stats, 4)["appliedTotal"] == 21.5
    assert espn.weekly_projection(stats[:3], 4) is None


def test_espn_filter_header_matches_the_design():
    design = (
        '{"players":{"filterSlotIds":{"value":[0,2,4,6,17,16]},'
        '"filterStatsForCurrentSeasonScoringPeriodId":{"value":[4]},'
        '"limit":600,"sortPercOwned":{"sortPriority":1,"sortAsc":false}}}'
    )

    assert espn.player_filter(4) == json.loads(design)


def test_sharks_rows_per_position(sharks_rows):
    assert rows_per_position(sharks_rows) == {"QB": 40, "RB": 40, "WR": 40, "TE": 43, "K": 33, "DEF": 32}


def test_sharks_player_row(sharks_rows):
    assert find(sharks_rows, "Josh", "Allen") == Projection(
        "fantasysharks.com", 2026, 4, "Josh", "Allen", "QB", "BUF", 27.9, external_id="13589"
    )


def test_sharks_defense_row_takes_sleeper_form(sharks_rows):
    assert find(sharks_rows, "Seattle", "Seahawks") == Projection(
        "fantasysharks.com", 2026, 4, "Seattle", "Seahawks", "DEF", "SEA", 17.4, external_id="515"
    )


def test_sharks_keeps_name_suffixes(sharks_rows):
    assert find(sharks_rows, "Marvin", "Harrison Jr.").external_id == "16614"
    assert find(sharks_rows, "Kenneth", "Walker III").team == "KC"


def test_sharks_normalises_team_aliases(sharks_rows):
    assert find(sharks_rows, "Drake", "Maye").team == "NE"  # the page says NEP
    assert {row.team for row in sharks_rows} <= CANONICAL_TEAMS


def test_sharks_skips_zero_and_negative_projections(sharks_rows):
    parsed = names(sharks_rows)

    assert ("Kyle", "Allen") not in parsed  # 0.0
    assert ("Jarrett", "Stidham") not in parsed  # -0.1
    assert ("Patrick", "Ricard") not in parsed  # 0.0


def test_sharks_rows_carry_the_week_the_page_shows(sharks_pages):
    rows = fantasysharks.parse(sharks_pages, 2026, 5)

    assert {row.week for row in rows} == {4}


def test_sharks_page_for_another_position_raises(sharks_pages):
    with pytest.raises(ValueError, match="RB page shows 'Quarterback'"):
        fantasysharks.parse({"RB": sharks_pages["QB"]}, 2026, 4)


def test_sharks_page_showing_no_week_raises(sharks_pages):
    html = sharks_pages["K"].replace('<option selected="" value="886">', '<option value="886">')
    html = html.replace('<option value="901">', '<option selected="" value="901">')

    with pytest.raises(ValueError, match="segment 'Wild Card', not a week"):
        fantasysharks.parse({"K": html}, 2026, 4)


def test_sharks_fetch_requests_the_six_pages_for_the_week_segment(monkeypatch, sharks_pages):
    position_by_code = {code: position for position, (code, _) in fantasysharks.PAGES.items()}
    requested = []

    def fake_get(url, headers, timeout):
        requested.append(url)
        code = int(re.search(r"Position=(\d+)", url).group(1))
        return SimpleNamespace(text=sharks_pages[position_by_code[code]], raise_for_status=lambda: None)

    monkeypatch.setattr(fantasysharks.requests, "get", fake_get)

    rows = fantasysharks.SOURCE.fetch(2026, 4)

    assert len(requested) == 6
    assert all("Segment=886" in url for url in requested)
    assert len(rows) == 228


def test_sharks_fetch_needs_the_season_segment_offset():
    with pytest.raises(ValueError, match="season 2027"):
        fantasysharks.SOURCE.fetch(2027, 1)
