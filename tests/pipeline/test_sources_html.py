import copy
import json
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest
from bs4 import BeautifulSoup

from pipeline.sources import fanduel, fftoday, firstdown, load_source, rotoballer
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


def test_firstdown_says_when_the_week_is_not_posted_yet():
    data = {
        "snapshot": None,
        "state": "unavailable",
        "target": {"season": SEASON, "week": 4, "nextBoundary": "2026-10-05T20:30:00-07:00"},
    }
    chunk = json.dumps([1, '12:[["$","$L1c","4",{"data":' + json.dumps(data) + "}]]"])
    html = f"<script>self.__next_f.push({chunk})</script>"

    with pytest.raises(ValueError, match="has not posted week 4: rankings unavailable, next update 2026-10-05"):
        firstdown.parse(html, SEASON, 4)


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


def test_fftoday_fetch_follows_each_next_page_link_five_seconds_apart(serve, clock, fftoday_pages):
    position_by_code = {code: position for position, code in fftoday.POSITION_CODES.items()}

    def page_for(url):
        position = position_by_code[int(re.search(r"PosID=(\d+)", url).group(1))]
        page_index = 1 if "cur_page=1" in url else 0
        return fftoday_pages[position][page_index]

    sent = serve(page_for)

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


# RotoBaller

WEDNESDAY_COUNTS = {"QB": 32, "RB": 76, "WR": 123, "TE": 69}
SUNDAY_COUNTS = {"QB": 32, "RB": 89, "WR": 139, "TE": 76}
SUNDAY_ARTICLE_URL = (
    "https://www.rotoballer.com/updated-fantasy-football-projections-for-week-3-rb-wr-te-qb-d-st-k-2026/1952030"
)


# Synthetic pages in RotoBaller's markup: names, teams and positions as published, ids and numbers made up.
def rotoballer_file(file_name: str) -> str:
    return (FIXTURES / "rotoballer" / file_name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rotoballer_html() -> str:
    return rotoballer_file("projections_2026_w3.html")


@pytest.fixture(scope="module")
def rotoballer_updated_html() -> str:
    return rotoballer_file("updated_projections_2026_w3.html")


@pytest.fixture(scope="module")
def rotoballer_sitemap() -> str:
    return rotoballer_file("google_news_sitemap_2026-09-27.xml")


def sitemap_entry(url: str, published: str) -> str:
    return (
        f"<url><loc>{url}</loc><news:news><news:publication><news:name>RotoBaller</news:name>"
        f"<news:language>en</news:language></news:publication>"
        f"<news:publication_date>{published}</news:publication_date><news:title>Projections</news:title>"
        f"</news:news></url>"
    )


@pytest.mark.parametrize(
    ("page_fixture", "counts"),
    [("rotoballer_html", WEDNESDAY_COUNTS), ("rotoballer_updated_html", SUNDAY_COUNTS)],
)
def test_rotoballer_reads_every_player_and_skips_kickers_and_defenses(page_fixture, counts, request):
    rows = rotoballer.parse(request.getfixturevalue(page_fixture), SEASON, WEEK)
    assert count_by_position(rows) == counts


@pytest.mark.parametrize("page_fixture", ["rotoballer_html", "rotoballer_updated_html"])
def test_rotoballer_rescore_is_the_half_ppr_page_plus_half_a_point_a_catch(page_fixture, request):
    html = request.getfixturevalue(page_fixture)
    table = BeautifulSoup(html, "lxml").find("table")
    header, *page_rows = table.find_all("tr")
    headers = [cell.get_text(strip=True) for cell in header.find_all("td")]
    player_rows = [row for row in page_rows if row.find_all("td")[2].get_text(strip=True) in {"QB", "RB", "WR", "TE"}]

    parsed_rows = rotoballer.parse(html, SEASON, WEEK)
    for page_row, parsed in zip(player_rows, parsed_rows, strict=True):
        cells = [cell.get_text(strip=True) for cell in page_row.find_all("td")]
        receptions = float(cells[headers.index("Rec")] or 0)
        page_points = float(cells[headers.index("Fan Points")])
        assert round(abs(parsed.points - 0.5 * receptions - page_points), 2) <= 0.6, cells[0]


def test_rotoballer_player_row(rotoballer_html):
    # 0.04 * 256.0 passing yards + 4 * 1.4 TD - 2 * 1.2 INT + 0.1 * 15.0 rushing yards + 6 * 0.6 rushing TD; the page shows 18.5
    assert find(rotoballer.parse(rotoballer_html, SEASON, WEEK), "Josh", "Allen") == Projection(
        source="rotoballer.com",
        season=2026,
        week=3,
        first_name="Josh",
        last_name="Allen",
        position="QB",
        team="BUF",
        points=18.54,
        external_id="90209",
    )


def test_rotoballer_reads_the_update_without_its_comp_column(rotoballer_html, rotoballer_updated_html):
    wednesday = find(rotoballer.parse(rotoballer_html, SEASON, WEEK), "Lamar", "Jackson")
    sunday = find(rotoballer.parse(rotoballer_updated_html, SEASON, WEEK), "Lamar", "Jackson")
    assert sunday == wednesday
    assert sunday.points == round(0.04 * 239.1 + 4 * 2.5 - 2 * 0.9 + 0.1 * 20.5 + 6 * 0.0, 2)


def test_rotoballer_stamps_the_page_week(rotoballer_html):
    rows = rotoballer.parse(rotoballer_html, SEASON, 4)
    assert {row.week for row in rows} == {3}


def test_rotoballer_rejects_a_page_without_a_table():
    html = "<html><body><h1>Week 3 Fantasy Football Projections (Half PPR)</h1></body></html>"
    with pytest.raises(ValueError, match="the RotoBaller page for week 3 has no projections table"):
        rotoballer.parse(html, SEASON, WEEK)


def test_rotoballer_rejects_a_page_without_a_week(rotoballer_html):
    html = rotoballer_html.replace("<h1", "<h2").replace("</h1>", "</h2>")
    with pytest.raises(ValueError, match="no title to read the week from"):
        rotoballer.parse(html, SEASON, WEEK)


def test_rotoballer_rejects_a_missing_stat_column(rotoballer_html):
    html = rotoballer_html.replace("<td><strong>Rec. TDs</strong></td>", "")
    with pytest.raises(ValueError, match=re.escape('the RotoBaller page for week 3 has no "Rec. TDs" column')):
        rotoballer.parse(html, SEASON, WEEK)


def test_rotoballer_finds_the_latest_article_for_the_week(rotoballer_sitemap):
    assert rotoballer.article_url(rotoballer_sitemap, 2026, 3) == SUNDAY_ARTICLE_URL

    wednesday_url = "https://www.rotoballer.com/fantasy-football-projections-for-week-3-rb-wr-qb-te-2026/1948517"
    with_earlier = rotoballer_sitemap.replace(
        "</urlset>", sitemap_entry(wednesday_url, "2026-09-23T15:30:59-04:00") + "</urlset>"
    )
    assert rotoballer.article_url(with_earlier, 2026, 3) == SUNDAY_ARTICLE_URL

    with_later = rotoballer_sitemap.replace(
        "</urlset>", sitemap_entry(wednesday_url, "2026-09-27T09:00:00-04:00") + "</urlset>"
    )
    assert rotoballer.article_url(with_later, 2026, 3) == wednesday_url


def test_rotoballer_refuses_a_week_not_in_the_sitemap(rotoballer_sitemap):
    with pytest.raises(
        ValueError, match="RotoBaller has not posted week 4: no projections article in the news sitemap"
    ):
        rotoballer.article_url(rotoballer_sitemap, 2026, 4)


@pytest.mark.parametrize(
    ("page_fixture", "first_name", "last_name", "position"),
    [
        ("rotoballer_html", "Brian", "Thomas Jr.", "WR"),
        ("rotoballer_html", "De'Von", "Achane", "RB"),
        ("rotoballer_html", "Amon-Ra", "St. Brown", "WR"),
        ("rotoballer_html", "Patrick", "Mahomes II", "QB"),  # the suffix is a second link
        ("rotoballer_updated_html", "Patrick", "Mahomes II", "QB"),  # the suffix is text after the link
        ("rotoballer_updated_html", "J.", "Michael Sturdivant", "WR"),  # only in the Sunday update
    ],
)
def test_rotoballer_keeps_names_as_the_page_spells_them(page_fixture, first_name, last_name, position, request):
    rows = rotoballer.parse(request.getfixturevalue(page_fixture), SEASON, WEEK)
    assert find(rows, first_name, last_name).position == position


def test_rotoballer_keeps_players_without_a_link_and_without_an_id(rotoballer_html):
    rows = rotoballer.parse(rotoballer_html, SEASON, WEEK)
    unlinked = {row.first_name + " " + row.last_name for row in rows if row.external_id is None}
    assert unlinked == {
        "KC Concepcion",
        "Devaughn Vele",
        "Kenneth Gainwell",
        "Tre Harris",
        "Ted Hurst",
        "Marquise Brown",
    }
    assert find(rows, "KC", "Concepcion").team == "CLE"


def test_rotoballer_skips_players_projected_for_nothing(rotoballer_html):
    gibbs_stats = "<td>11.8</td>\n<td>36.4</td>\n<td>0.4</td>\n<td>5.5</td>\n<td>38.3</td>\n<td>0.4</td>"
    assert rotoballer_html.count(gibbs_stats) == 1
    html = rotoballer_html.replace(gibbs_stats, "\n".join(["<td></td>"] * 6))
    rows = rotoballer.parse(html, SEASON, WEEK)
    assert len(rows) == 299
    assert ("Jahmyr", "Gibbs") not in {(row.first_name, row.last_name) for row in rows}


def test_rotoballer_fetch_reads_the_sitemap_then_the_article(serve, clock, rotoballer_sitemap, rotoballer_updated_html):
    sent = serve({rotoballer.SITEMAP_URL: rotoballer_sitemap, SUNDAY_ARTICLE_URL: rotoballer_updated_html}.get)

    rows = rotoballer.SOURCE.fetch(2026, 3)

    assert [request["url"] for request in sent] == [rotoballer.SITEMAP_URL, SUNDAY_ARTICLE_URL]
    assert all(request["headers"] == {"User-Agent": USER_AGENT} for request in sent)
    assert clock.sleeps == [2.0]
    assert count_by_position(rows) == SUNDAY_COUNTS


def test_rotoballer_fetch_stops_at_the_sitemap_when_the_week_is_not_posted(serve, clock, rotoballer_sitemap):
    sent = serve({rotoballer.SITEMAP_URL: rotoballer_sitemap}.get)

    with pytest.raises(ValueError, match="has not posted week 4"):
        rotoballer.SOURCE.fetch(2026, 4)

    assert [request["url"] for request in sent] == [rotoballer.SITEMAP_URL]


# Every source

# Each source module with the name of the fixture holding its page or payload.
SOURCES = [
    (firstdown, "firstdown_html"),
    (fanduel, "fanduel_items"),
    (fftoday, "fftoday_pages"),
    (rotoballer, "rotoballer_html"),
]
SOURCE_IDS = ["firstdown", "fanduel", "fftoday", "rotoballer"]


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
        assert row.external_id or module is rotoballer  # RotoBaller leaves a few players unlinked, without an id


@pytest.mark.parametrize(
    ("name", "website"),
    [
        ("firstdown", "firstdown.studio"),
        ("fanduel", "fanduel.com"),
        ("fftoday", "fftoday.com"),
        ("rotoballer", "rotoballer.com"),
    ],
)
def test_sources_are_registered(name, website):
    source = load_source(name)
    assert (source.name, source.website, source.supports_future_weeks) == (name, website, False)
