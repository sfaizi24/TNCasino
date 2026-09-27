import json
from dataclasses import replace

import pytest

from pipeline.db import connect
from pipeline.runner import StepContext
from pipeline.settings import Settings
from pipeline.steps import match
from pipeline.steps.clean import PROJECTIONS_DDL

WEEK = 4

NFL_PLAYERS_DDL = """
CREATE TABLE nfl_players (
  player_id TEXT PRIMARY KEY, first_name TEXT, last_name TEXT, position TEXT, team TEXT, fantasy_positions TEXT
)
"""

# (player_id, first_name, last_name, position, team, fantasy_positions)
PLAYERS = [
    ("4046", "Patrick", "Mahomes", "QB", "KC", ["QB"]),
    ("4984", "Josh", "Allen", "QB", "BUF", ["QB"]),
    ("KC", "KC", "Defense", "DEF", "KC", ["DEF"]),
    ("8122", "Zonovan", "Knight", "RB", "ARI", ["RB"]),
    ("7547", "Amon-Ra", "St. Brown", "WR", "DET", ["WR"]),
    ("6943", "Gabriel", "Davis", "WR", "BUF", ["WR"]),
    ("5001", "Chris", "Johnson", "WR", "LAR", ["WR"]),
    ("5002", "Chris", "Johnson", "WR", "NYJ", ["WR"]),
    ("3164", "Javorius", "Allen", "RB", None, ["RB"]),
    ("7149", "Tommy", "Stevens", "TE", "NYG", ["QB"]),
]


@pytest.fixture
def settings(tmp_path):
    return Settings(season=2026, week=WEEK, league_id="L2026", data_dir=tmp_path)


def add_players(settings, players):
    conn = connect(settings, "league")
    conn.execute(NFL_PLAYERS_DDL)
    with conn:
        conn.executemany(
            "INSERT INTO nfl_players VALUES (?, ?, ?, ?, ?, ?)",
            [(*player[:5], json.dumps(player[5])) for player in players],
        )
    conn.close()


def projection(first, last, position="WR", team=None, points=10.0, source="espn.com", external_id=None, week=WEEK):
    return {
        "source_website": source,
        "week": week,
        "player_first_name": first,
        "player_last_name": last,
        "position": position,
        "team": team,
        "projected_points": points,
        "external_id": external_id,
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
                    :team, :projected_points, :external_id, '2026-09-30T12:00:00+00:00')
            """,
            rows,
        )
    conn.close()


def run_match(settings):
    with StepContext(settings, run_id="2026w04-test", step=match.NAME) as ctx:
        return match.run(ctx)


def matches(settings):
    conn = connect(settings, "projections")
    rows = conn.execute(
        """
        SELECT player_first_name, player_last_name, sleeper_player_id, match_method
        FROM projections_with_sleeper WHERE season = 2026 AND week = ? ORDER BY id
        """,
        (settings.week,),
    ).fetchall()
    conn.close()
    return [tuple(row) for row in rows]


def test_each_rule_links_the_row_it_is_for(settings):
    add_players(settings, PLAYERS)
    add_projections(
        settings,
        [
            projection("Pat", "Mahomes", position="QB", team="KC", source="sleeper.com", external_id="4046"),
            projection("Kansas City", "Chiefs", position="DEF", team="KC"),
            projection("Bam", "Knight", position="RB", team="ARI"),
            projection("Chris", "Johnson", team="NYJ"),
            projection("Amon-Ra", "St. Brown"),
            projection("Gabe", "Davis", team="BUF"),
        ],
    )

    result = run_match(settings)

    assert matches(settings) == [
        ("Pat", "Mahomes", "4046", "external_id"),
        ("Kansas City", "Chiefs", "KC", "def_team"),
        ("Bam", "Knight", "8122", "hardcoded"),
        ("Chris", "Johnson", "5002", "exact_team"),
        ("Amon-Ra", "St. Brown", "7547", "exact"),
        ("Gabe", "Davis", "6943", "last_initial"),
    ]
    assert result.warnings == []


def test_a_sleeper_id_missing_from_nfl_players_falls_through_to_the_names(settings):
    add_players(settings, PLAYERS)
    add_projections(
        settings, [projection("Josh", "Allen", position="QB", team="BUF", source="sleeper.com", external_id="99999")]
    )

    run_match(settings)

    assert matches(settings) == [("Josh", "Allen", "4984", "exact_team")]


def test_a_name_shared_by_two_players_needs_the_team_to_pick_one(settings):
    add_players(settings, PLAYERS)
    add_projections(
        settings,
        [
            projection("Chris", "Johnson", team="LAR"),
            projection("Chris", "Johnson", source="fantasypros.com"),
            projection("Chris", "Johnson", team="MIA", source="fanduel.com"),
        ],
    )

    run_match(settings)

    assert matches(settings) == [
        ("Chris", "Johnson", "5001", "exact_team"),
        ("Chris", "Johnson", None, None),
        ("Chris", "Johnson", None, None),
    ]


def test_a_player_matches_at_any_of_his_fantasy_positions(settings):
    add_players(settings, PLAYERS)
    add_projections(
        settings,
        [
            projection("Tommy", "Stevens", position="QB", team="NYG"),
            projection("Tommy", "Stevens", position="WR", team="NYG"),
        ],
    )

    run_match(settings)

    assert matches(settings) == [("Tommy", "Stevens", "7149", "exact_team"), ("Tommy", "Stevens", None, None)]


def test_a_first_initial_match_needs_the_team_to_agree(settings):
    # ESPN once listed Josh Allen at running back, where his initial reaches the retired Javorius Allen.
    add_players(settings, PLAYERS)
    add_projections(
        settings,
        [
            projection("Josh", "Allen", position="RB", team="BUF"),
            projection("J.", "Allen", position="RB", source="fantasypros.com"),
        ],
    )

    run_match(settings)

    assert matches(settings) == [("Josh", "Allen", None, None), ("J.", "Allen", "3164", "last_initial")]


def test_a_defense_without_a_sleeper_entry_stays_unmatched(settings):
    add_players(settings, PLAYERS)
    add_projections(settings, [projection("New York", "Jets", position="DEF", team="NYJ")])

    run_match(settings)

    assert matches(settings) == [("New York", "Jets", None, None)]


def test_a_hardcoded_id_missing_from_nfl_players_is_skipped_with_a_warning(settings):
    add_players(settings, [player for player in PLAYERS if player[0] != "8122"])
    add_projections(settings, [projection("Bam", "Knight", position="RB", team="ARI")])

    result = run_match(settings)

    assert matches(settings) == [("Bam", "Knight", None, None)]
    assert result.warnings == ["hardcoded match Bam Knight -> 8122 is not in nfl_players; skipped"]


def test_summary_counts_the_methods_and_lists_the_biggest_misses(settings):
    add_players(settings, PLAYERS)
    misses = [projection(f"Rookie{number}", "Nobody", points=float(number)) for number in range(1, 13)]
    add_projections(settings, [projection("Amon-Ra", "St. Brown", team="DET", points=15.0), *misses])

    summary = run_match(settings).summary

    assert summary["n_rows"] == 13
    assert summary["n_matched"] == 1
    assert summary["match_rate"] == 0.077
    assert summary["by_method"] == {
        "external_id": 0,
        "def_team": 0,
        "hardcoded": 0,
        "exact_team": 1,
        "exact": 0,
        "last_initial": 0,
    }
    assert [miss["points"] for miss in summary["unmatched_top"]] == [
        12.0,
        11.0,
        10.0,
        9.0,
        8.0,
        7.0,
        6.0,
        5.0,
        4.0,
        3.0,
    ]
    assert summary["unmatched_top"][0] == {
        "name": "Rookie12 Nobody",
        "position": "WR",
        "source": "espn.com",
        "points": 12.0,
    }


def test_rerunning_replaces_only_that_weeks_matches(settings):
    add_players(settings, PLAYERS)
    add_projections(
        settings,
        [projection("Amon-Ra", "St. Brown", team="DET", week=WEEK - 1), projection("Amon-Ra", "St. Brown", team="DET")],
    )

    run_match(replace(settings, week=WEEK - 1))
    run_match(settings)
    run_match(settings)

    conn = connect(settings, "projections")
    counts = conn.execute("SELECT week, COUNT(*) FROM projections_with_sleeper GROUP BY week ORDER BY week").fetchall()
    conn.close()
    assert [tuple(row) for row in counts] == [(WEEK - 1, 1), (WEEK, 1)]


def test_a_week_without_projections_fails(settings):
    add_players(settings, PLAYERS)
    add_projections(settings, [projection("Amon-Ra", "St. Brown", week=WEEK - 1)])

    with pytest.raises(RuntimeError, match="run the scrape step first"):
        run_match(settings)
