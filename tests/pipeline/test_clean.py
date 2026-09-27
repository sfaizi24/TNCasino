import pytest

from pipeline.db import connect
from pipeline.runner import StepContext
from pipeline.settings import Settings
from pipeline.steps import clean
from pipeline.steps.clean import PROJECTIONS_DDL

WEEK = 4


@pytest.fixture
def settings(tmp_path):
    return Settings(season=2026, week=WEEK, league_id="L2026", data_dir=tmp_path)


def projection(first, last, position="WR", team="ARI", points=10.0, source="espn.com", week=WEEK):
    return {
        "source_website": source,
        "week": week,
        "player_first_name": first,
        "player_last_name": last,
        "position": position,
        "team": team,
        "projected_points": points,
    }


def add_projections(settings, rows):
    conn = connect(settings, "projections")
    conn.executescript(PROJECTIONS_DDL)
    with conn:
        conn.executemany(
            """
            INSERT INTO projections (source_website, season, week, player_first_name, player_last_name, position,
                                     team, projected_points, external_id, created_at)
            VALUES (:source_website, 2026, :week, :player_first_name, :player_last_name, :position,
                    :team, :projected_points, NULL, '2026-09-30T12:00:00+00:00')
            """,
            rows,
        )
    conn.close()


def run_clean(settings):
    with StepContext(settings, run_id="2026w04-test", step=clean.NAME) as ctx:
        return clean.run(ctx)


def stored_rows(settings, week=WEEK):
    conn = connect(settings, "projections")
    rows = conn.execute("SELECT * FROM projections WHERE season = 2026 AND week = ? ORDER BY id", (week,)).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def stored_names(settings):
    return [(row["player_first_name"], row["player_last_name"]) for row in stored_rows(settings)]


def test_names_lose_injury_tags_and_suffixes_but_keep_their_spelling(settings):
    add_projections(
        settings,
        [
            projection("Marvin", "Harrison Jr. Q"),
            projection("D'Andre", "Swift O", position="RB", team="CHI"),
            projection("Amon-Ra", "St. Brown", team="DET"),
            projection("Kenneth", "Walker III", position="RB", team="SEA"),
            projection("  Puka ", " Nacua IR ", team="LAR"),
        ],
    )

    result = run_clean(settings)

    assert stored_names(settings) == [
        ("Marvin", "Harrison"),
        ("D'Andre", "Swift"),
        ("Amon-Ra", "St. Brown"),
        ("Kenneth", "Walker"),
        ("Puka", "Nacua"),
    ]
    assert result.summary["n_renamed"] == 4


def test_a_one_word_name_that_looks_like_a_tag_is_kept(settings):
    add_projections(settings, [projection("Justin", "D", position="K")])

    run_clean(settings)

    assert stored_names(settings) == [("Justin", "D")]


def test_legacy_positions_and_team_codes_become_canonical(settings):
    add_projections(
        settings,
        [
            projection("Travis", "Etienne", position="RB", team="JAC"),
            projection("Terry", "McLaurin", team="WSH"),
            projection("Tyler", "Lockett", team="FA"),
            projection("Stefon", "Diggs", team=None),
            projection("Jaguars", "D/ST", position="DST", team="JAC"),
        ],
    )

    result = run_clean(settings)

    rows = stored_rows(settings)
    assert [(row["position"], row["team"]) for row in rows] == [
        ("RB", "JAX"),
        ("WR", "WAS"),
        ("WR", None),
        ("WR", None),
        ("DEF", "JAX"),
    ]
    assert result.summary["unknown_teams"] == ["FA"]


def test_defenses_take_sleepers_city_and_nickname(settings):
    add_projections(
        settings,
        [
            projection("Texans", "Defense", position="DEF", team="HOU"),
            projection("Seahawks", "D/ST", position="DEF", team=None),
            projection("Los Angeles", "D/ST", position="DEF", team="LAC"),
            projection("Houston", "Texans", position="DEF", team="HOU", source="sleeper.com"),
        ],
    )

    result = run_clean(settings)

    rows = stored_rows(settings)
    assert [(row["player_first_name"], row["player_last_name"], row["team"]) for row in rows] == [
        ("Houston", "Texans", "HOU"),
        ("Seattle", "Seahawks", "SEA"),
        ("Los Angeles", "Chargers", "LAC"),
        ("Houston", "Texans", "HOU"),
    ]
    assert result.summary["n_renamed"] == 3


def test_rows_that_clean_to_the_same_player_keep_the_highest_projection(settings):
    add_projections(
        settings,
        [
            projection("Marvin", "Harrison Jr.", points=12.5),
            projection("Marvin", "Harrison", points=14.0),
            projection("Marvin", "Harrison Jr. Q", points=9.0),
            projection("Marvin", "Harrison", points=11.0, source="fantasypros.com"),
        ],
    )
    highest_id = stored_rows(settings)[1]["id"]

    result = run_clean(settings)

    rows = stored_rows(settings)
    assert [(row["id"], row["source_website"], row["projected_points"]) for row in rows] == [
        (highest_id, "espn.com", 14.0),
        (highest_id + 2, "fantasypros.com", 11.0),
    ]
    assert result.summary == {
        "n_in": 4,
        "n_out": 2,
        "n_renamed": 2,
        "n_dropped_duplicates": 2,
        "unknown_teams": [],
    }


def test_other_weeks_are_left_alone(settings):
    add_projections(
        settings,
        [
            projection("Marvin", "Harrison Jr.", week=WEEK - 1),
            projection("Marvin", "Harrison Jr."),
        ],
    )
    last_week = stored_rows(settings, week=WEEK - 1)

    run_clean(settings)

    assert stored_rows(settings, week=WEEK - 1) == last_week
    assert stored_names(settings) == [("Marvin", "Harrison")]


def test_running_twice_changes_nothing_the_second_time(settings):
    add_projections(
        settings,
        [
            projection("Marvin", "Harrison Jr. Q"),
            projection("Marvin", "Harrison", points=14.0),
            projection("Seahawks", "D/ST", position="DST", team=None),
        ],
    )
    run_clean(settings)
    once = stored_rows(settings)

    result = run_clean(settings)

    assert stored_rows(settings) == once
    assert result.summary["n_renamed"] == 0
    assert result.summary["n_dropped_duplicates"] == 0


def test_a_week_without_projections_fails(settings):
    add_projections(settings, [projection("Marvin", "Harrison", week=WEEK - 1)])

    with pytest.raises(RuntimeError, match="run the scrape step first"):
        run_clean(settings)
