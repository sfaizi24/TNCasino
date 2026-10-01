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
