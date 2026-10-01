from sqlalchemy import text


def add_player(session, player_id, name, position, mu, spread=1.0, sources=4, roster_id=None, starting=0, week=10):
    """A player's week as the stats step stores it, on a roster as the lineups step lists him when roster_id is set."""
    session.execute(
        text("""
        INSERT INTO player_week_stats (season, week, sleeper_player_id, player_name, position, team, mu, sigma, var,
                                       n_sources, spread, p10, p90, source_low, source_high)
        VALUES (2026, :week, :player_id, :name, :position, 'DET', :mu, 5, 25, :sources, :spread, :p10, :p90,
                :low, :high)
    """),
        {
            "week": week,
            "player_id": player_id,
            "name": name,
            "position": position,
            "mu": mu,
            "sources": sources,
            "spread": spread,
            "p10": mu - 8,
            "p90": mu + 10,
            "low": mu - 2 * spread,
            "high": mu + 2 * spread,
        },
    )
    if roster_id is not None:
        session.execute(
            text("""
            INSERT INTO projections_rosters (roster_id, sleeper_player_id, position, season, week, mu, starting_status)
            VALUES (:roster_id, :player_id, :position, '2026', :week, :mu, :starting)
        """),
            {
                "roster_id": roster_id,
                "player_id": player_id,
                "position": position,
                "week": week,
                "mu": mu,
                "starting": starting,
            },
        )
    session.commit()


def test_player_report_lists_the_top_projections_with_their_ranges(
    client, seeded_analytics, betting_period, db_session
):
    session = db_session.session
    add_player(session, "a", "Rostered Back", "RB", 20.0, roster_id=1, starting=1)
    add_player(session, "b", "Free Back", "RB", 22.0)
    add_player(session, "c", "Next Week Back", "RB", 30.0, week=11)

    data = client.get("/api/player_report").get_json()

    assert data["week"] == 10
    assert data["top"]["RB"] == [
        {"player": "Free Back", "team": "DET", "owner": None, "projected": 22.0, "low": 14.0, "high": 32.0},
        {"player": "Rostered Back", "team": "DET", "owner": "Alice A", "projected": 20.0, "low": 12.0, "high": 30.0},
    ]
    assert data["top"]["QB"] == []


def test_player_report_ranks_starters_by_how_far_apart_the_sources_are(
    client, seeded_analytics, betting_period, db_session
):
    session = db_session.session
    add_player(session, "wide", "Coin Flip", "WR", 10.0, spread=4.0, roster_id=1, starting=1)
    add_player(session, "star", "Star", "WR", 25.0, spread=5.0, roster_id=2, starting=1)
    add_player(session, "tight", "Steady", "TE", 12.0, spread=0.5, roster_id=2, starting=1)
    add_player(session, "bench", "Benched", "WR", 9.0, spread=6.0, roster_id=1)
    add_player(session, "thin", "Two Sources", "QB", 18.0, spread=9.0, sources=2, roster_id=1, starting=1)
    add_player(session, "deep", "Deep Flex", "RB", 4.0, spread=3.0, roster_id=2, starting=1)

    data = client.get("/api/player_report").get_json()

    assert [player["player"] for player in data["split"]] == ["Coin Flip", "Star", "Steady"]
    assert [player["player"] for player in data["agree"]] == ["Steady", "Star", "Coin Flip"]
    assert data["split"][0] == {
        "player": "Coin Flip",
        "position": "WR",
        "owner": "Alice A",
        "projected": 10.0,
        "low": 2.0,
        "high": 18.0,
        "sources": 4,
    }


def test_player_report_is_empty_before_the_first_publish(client, seeded_analytics, betting_period, db_session):
    assert client.get("/api/player_report").get_json() == {"week": None}

    db_session.session.execute(text("DROP TABLE player_week_stats"))
    db_session.session.commit()

    assert client.get("/api/player_report").get_json() == {"week": None}
