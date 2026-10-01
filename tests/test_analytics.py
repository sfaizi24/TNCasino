import pytest
from sqlalchemy import text


def add_places(session, week, chances):
    """Publish each roster's chance of every final place for a week, as the playoffs step does."""
    for team_id, (owner, places) in chances.items():
        for position, probability in enumerate(places, start=1):
            session.execute(
                text("""
                INSERT INTO standings_probability_matrix (run_id, season, week, team_id, owner, position, probability)
                VALUES ('run', 2026, :week, :team_id, :owner, :position, :probability)
            """),
                {"week": week, "team_id": team_id, "owner": owner, "position": position, "probability": probability},
            )
    session.commit()


@pytest.fixture
def week_ten_places(seeded_analytics, db_session):
    add_places(db_session.session, 10, {1: ("Alice A", [0.7, 0.3]), 2: ("Bob B", [0.3, 0.7])})


def test_playoff_picture_lists_each_place_with_the_playoff_chance(client, week_ten_places, betting_period):
    data = client.get("/api/playoff_picture").get_json()

    assert data["week"] == 10
    assert data["playoff_cutoff"] == 1
    alice, bob = data["teams"]
    assert alice == {"label": "Alice A", "places": [70.0, 30.0], "playoffs": 70.0, "expected_place": 1.3}
    assert bob["places"] == [30.0, 70.0]
    assert bob["playoffs"] == 30.0


def test_playoff_picture_reads_only_the_latest_week(client, week_ten_places, betting_period, db_session):
    add_places(db_session.session, 9, {1: ("Alice A", [0.1, 0.9]), 2: ("Bob B", [0.9, 0.1])})

    data = client.get("/api/playoff_picture").get_json()

    assert data["week"] == 10
    assert [team["label"] for team in data["teams"]] == ["Alice A", "Bob B"]


def test_playoff_picture_is_empty_before_any_futures_run(client, seeded_analytics, betting_period):
    assert client.get("/api/playoff_picture").get_json() == {"week": None, "playoff_cutoff": None, "teams": []}


def test_season_race_follows_each_team_week_by_week(client, week_ten_places, betting_period, db_session):
    add_places(db_session.session, 9, {1: ("Alice A", [0.6, 0.4]), 2: ("Bob B", [0.4, 0.6])})
    db_session.session.execute(
        text("""
        INSERT INTO betting_odds_champion (run_id, week, season, team_id, owner, probability, american_odds)
        VALUES ('run', 9, 2026, 1, 'Alice A', 0.20, '+400'), ('run', 9, 2026, 2, 'Bob B', 0.10, '+900')
    """)
    )
    db_session.session.commit()

    data = client.get("/api/season_race").get_json()

    assert data["weeks"] == [9, 10]
    alice, bob = data["teams"]
    assert alice == {"label": "Alice A", "playoffs": [60.0, 70.0], "title": [20.0, 30.0]}
    assert bob == {"label": "Bob B", "playoffs": [40.0, 30.0], "title": [10.0, 15.0]}


def test_season_race_gives_no_title_chance_to_a_team_the_market_left_out(
    client, week_ten_places, betting_period, db_session
):
    db_session.session.execute(text("DELETE FROM betting_odds_champion WHERE team_id = 2"))
    db_session.session.commit()

    bob = client.get("/api/season_race").get_json()["teams"][1]

    assert bob["title"] == [0.0]


@pytest.fixture
def graded_weeks(seeded_analytics, db_session):
    """Weeks 8 and 9 graded, and week 9's starters with their projections and points."""
    session = db_session.session
    session.execute(
        text("""
        INSERT INTO team_accuracy (season, week, roster_id, owner, projected, actual, covered, win_prob, won)
        VALUES (2026, 8, 1, 'Alice A', 110, 100, 1, 0.7, 0), (2026, 8, 2, 'Bob B', 100, 105, 1, 0.3, 1),
               (2026, 9, 1, 'Alice A', 110, 120, 1, 0.6, 1), (2026, 9, 2, 'Bob B', 100, 60, 0, 0.4, 0)
    """)
    )
    session.execute(
        text("""
        INSERT INTO sleeper_matchups (league_id, week, roster_id, matchup_id_number, starters, players_points)
        VALUES ('league1', 9, 1, 1, '["p1", "p2"]', '{"p1": 30.0, "p2": 5.0, "p9": 40.0}'),
               ('league1', 9, 2, 1, '["p3"]', '{"p3": 12.0}')
    """)
    )
    session.execute(
        text("""
        INSERT INTO projections_rosters (roster_id, sleeper_player_id, first_name, last_name, position, week, season, mu)
        VALUES (1, 'p1', 'Boom', 'Back', 'RB', 9, '2026', 15.0), (1, 'p2', 'Bust', 'Wide', 'WR', 9, '2026', 14.0),
               (1, 'p9', 'Bench', 'Guy', 'WR', 9, '2026', 10.0), (2, 'p3', 'Spot', 'On', 'TE', 9, '2026', 12.0)
    """)
    )
    session.commit()


def test_model_report_grades_last_week_and_the_season(client, graded_weeks, betting_period):
    data = client.get("/api/model_report").get_json()

    assert data["week"] == 9
    assert data["moneyline"] == {"week": {"won": 1, "lost": 0}, "season": {"won": 1, "lost": 1}}
    assert data["coverage"] == {"week": {"inside": 1, "teams": 2}, "season": {"inside": 3, "teams": 4}}


def test_model_report_lists_the_starters_who_missed_their_projection(client, graded_weeks, betting_period):
    data = client.get("/api/model_report").get_json()

    assert data["booms"] == [
        {"player": "Boom Back", "position": "RB", "owner": "Alice A", "projected": 15.0, "actual": 30.0}
    ]
    assert [bust["player"] for bust in data["busts"]] == ["Bust Wide"]


def test_model_report_is_empty_before_the_first_graded_week(client, seeded_analytics, betting_period, db_session):
    assert client.get("/api/model_report").get_json() == {"week": None}

    db_session.session.execute(text("DROP TABLE team_accuracy"))
    db_session.session.commit()

    assert client.get("/api/model_report").get_json() == {"week": None}
