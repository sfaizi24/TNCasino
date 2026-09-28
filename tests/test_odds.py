import json

import pytest
from sqlalchemy import text

from app.routes.helpers import get_team_mapping, query_analytics
from tests.conftest import RUN_ID


def test_query_analytics_returns_dicts(seeded_analytics, betting_period):
    rows = query_analytics(
        "SELECT owner FROM betting_odds_team_ou WHERE week = :week",
        {"week": 10},
    )
    assert len(rows) == 2
    assert isinstance(rows[0], dict)
    assert "owner" in rows[0]


def test_query_analytics_empty_result(seeded_analytics, betting_period):
    rows = query_analytics(
        "SELECT * FROM betting_odds_team_ou WHERE week = :week",
        {"week": 999},
    )
    assert rows == []


def test_get_team_mapping(seeded_analytics, betting_period):
    mapping = get_team_mapping(10)
    assert mapping[1] == "Alice A"
    assert mapping[2] == "Bob B"


def test_get_matchups(client, seeded_analytics, betting_period):
    resp = client.get("/api/matchups")
    data = resp.get_json()

    assert len(data) == 1
    assert data[0]["team1_name"] == "Alice A"
    assert data[0]["team2_name"] == "Bob B"
    assert data[0]["team1_ml"] == "-150"
    assert data[0]["team2_ml"] == "+130"
    assert (data[0]["team1_id"], data[0]["team2_id"]) == (1, 2)
    assert data[0]["market"] == "2026-w10-moneyline-1v2"
    assert data[0]["run_id"] == RUN_ID


def test_get_team_performance(client, seeded_analytics, betting_period):
    resp = client.get("/api/team_performance")
    data = resp.get_json()

    assert len(data) == 2
    owners = {d["owner"] for d in data}
    assert "Alice A" in owners
    assert "Bob B" in owners
    assert data[0]["line"] is not None

    alice = next(d for d in data if d["team_id"] == 1)
    assert alice["market"] == "2026-w10-team_total-1"
    assert alice["run_id"] == RUN_ID
    assert (alice["over_odds"], alice["under_odds"]) == ("-120", "+100")


def test_get_highest_scorer(client, seeded_analytics, betting_period):
    resp = client.get("/api/highest_scorer")
    data = resp.get_json()

    assert len(data) == 2
    assert data[0]["win_prob"] == 35.0
    assert data[0]["odds"] == "+185"
    assert data[0]["team_id"] == 1
    assert data[0]["market"] == "2026-w10-highest_scorer"
    assert data[0]["run_id"] == RUN_ID


def test_get_lowest_scorer(client, seeded_analytics, betting_period):
    resp = client.get("/api/lowest_scorer")
    data = resp.get_json()

    assert len(data) == 2
    assert data[0]["win_prob"] == 30.0
    assert data[0]["owner"] == "Bob B"
    assert data[0]["team_id"] == 2
    assert data[0]["market"] == "2026-w10-lowest_scorer"
    assert data[0]["run_id"] == RUN_ID


def test_get_first_place(client, seeded_analytics, betting_period):
    resp = client.get("/api/first_place")
    data = resp.get_json()

    assert len(data) == 2
    assert data[0]["owner"] == "Alice A"
    assert data[0]["odds"] == "-120"
    assert data[0]["team_id"] == 1
    assert data[0]["market"] == "2026-first_place"
    assert data[0]["run_id"] == RUN_ID
    assert data[0]["week"] == 10


def test_get_make_playoffs(client, seeded_analytics, betting_period):
    resp = client.get("/api/make_playoffs")
    data = resp.get_json()

    assert len(data) == 2
    assert data[0]["win_prob"] == 80.0
    assert data[0]["odds"] == "-400"
    assert data[0]["team_id"] == 1
    assert data[0]["market"] == "2026-make_playoffs-1"
    assert data[0]["run_id"] == RUN_ID
    assert data[0]["week"] == 10


@pytest.mark.parametrize(
    ("table", "url"),
    [("betting_odds_first_place", "/api/first_place"), ("betting_odds_make_playoffs", "/api/make_playoffs")],
)
def test_futures_list_only_the_highest_published_week(client, seeded_analytics, betting_period, db_session, table, url):
    db_session.session.execute(
        text(
            f"INSERT INTO {table} (run_id, week, season, team_id, owner, probability, american_odds) "
            "VALUES ('2026w12-20261124T140000', 12, 2026, 1, 'Alice A', 0.55, '-122')"
        )
    )
    db_session.session.commit()

    data = client.get(url).get_json()

    assert [(d["team_id"], d["week"], d["run_id"]) for d in data] == [(1, 12, "2026w12-20261124T140000")]


def test_a_row_from_an_older_season_is_not_listed(client, seeded_analytics, betting_period, db_session):
    db_session.session.execute(text("UPDATE betting_odds_highest_scorer SET season = 2025 WHERE team_id = 2"))
    db_session.session.commit()

    data = client.get("/api/highest_scorer").get_json()

    assert [d["market"] for d in data] == ["2026-w10-highest_scorer"]
    assert data[0]["team_id"] == 1


@pytest.mark.parametrize(
    ("table", "column", "url", "field"),
    [
        ("betting_odds_matchup_ml", "team1_ml", "/api/matchups", "team1_ml"),
        ("betting_odds_team_ou", "over_odds", "/api/team_performance", "over_odds"),
        ("betting_odds_lowest_scorer", "odds", "/api/lowest_scorer", "odds"),
        ("betting_odds_make_playoffs", "american_odds", "/api/make_playoffs", "odds"),
    ],
)
def test_a_side_without_a_price_is_listed_with_a_null_price(
    client, seeded_analytics, betting_period, db_session, table, column, url, field
):
    db_session.session.execute(text(f"UPDATE {table} SET {column} = NULL"))
    db_session.session.commit()

    data = client.get(url).get_json()

    assert data
    assert [d[field] for d in data] == [None] * len(data)


def test_get_lineup(client, seeded_analytics, betting_period):
    resp = client.get("/api/lineup/Alice A")
    data = resp.get_json()

    assert len(data) == 3
    assert data[0]["slot"] == "QB"
    assert data[0]["player_name"] == "Patrick Mahomes"
    assert data[0]["projected_points"] == 22.5


def test_get_teams(logged_in_client, seeded_analytics, betting_period):
    resp = logged_in_client.get("/api/teams")
    data = resp.get_json()

    assert len(data["teams"]) == 2
    slugs = [t["slug"] for t in data["teams"]]
    assert "alice" in slugs
    assert "bob" in slugs
    assert slugs.count("alice") == 1


def test_get_team_players(logged_in_client, seeded_analytics, betting_period):
    resp = logged_in_client.get("/api/team_players?team=alice")
    data = resp.get_json()

    assert len(data["starters"]) == 1
    assert data["starters"][0]["player_first_name"] == "Patrick"
    assert len(data["bench"]) == 1
    assert data["bench"][0]["player_last_name"] == "Player"
    assert data["starters"][0]["player_last_name"] != "League"


def test_get_team_players_falls_back_to_current_week_lineup(logged_in_client, seeded_analytics, betting_period):
    resp = logged_in_client.get("/api/team_players?team=bob")
    data = resp.get_json()

    assert len(data["starters"]) == 1
    assert data["starters"][0]["player_first_name"] == "Josh"
    assert data["starters"][0]["player_last_name"] == "Allen"
    assert data["starters"][0]["mu"] == 21.0
    assert data["bench"] == []


def test_matchups_empty_week(client, analytics_tables, betting_period):
    resp = client.get("/api/matchups")
    data = resp.get_json()
    assert data == []


def test_team_players_missing_param(logged_in_client, seeded_analytics, betting_period):
    resp = logged_in_client.get("/api/team_players")
    assert resp.status_code == 400


def test_team_players_not_found(logged_in_client, seeded_analytics, betting_period):
    resp = logged_in_client.get("/api/team_players?team=nonexistent")
    assert resp.status_code == 404


def test_team_distribution_missing_team_param(logged_in_client, seeded_analytics, betting_period):
    resp = logged_in_client.get("/api/team_distribution")
    assert resp.status_code == 400


def test_team_distribution_missing_curve(logged_in_client, seeded_analytics, betting_period):
    resp = logged_in_client.get("/api/team_distribution?team=nonexistent")
    assert resp.status_code == 404


def test_team_distribution_scheduled_pair_uses_stored_moneylines(logged_in_client, seeded_analytics, betting_period):
    resp = logged_in_client.get("/api/team_distribution?team=alice")
    assert resp.status_code == 200
    data = resp.get_json()

    assert data["team"]["owner"] == "alice"
    assert data["team"]["moneyline"] == "-150"
    assert data["team"]["win_prob"] == 0.60
    assert data["opponent"]["owner"] == "bob"
    assert data["opponent"]["moneyline"] == "+130"
    assert data["opponent"]["win_prob"] == 0.40
    assert "margin" in data
    assert data["margin"]["left_x"][-1] == 0.0
    assert data["margin"]["right_x"][0] == 0.0


def test_league_overview_returns_standings_with_projection_and_win_prob(client, seeded_analytics, betting_period):
    resp = client.get("/api/league_overview")
    data = resp.get_json()

    assert data["week"] == 10
    assert data["playoff_cutoff"] == 8
    assert len(data["teams"]) == 2

    alice = next(t for t in data["teams"] if t["label"] == "alice")
    assert alice["proj_mean"] == 110.0
    assert alice["p10"] == 90.0
    assert alice["p90"] == 130.0
    assert alice["win_prob"] == 60.0
    assert alice["record"] == "0-0"


def test_league_overview_orders_by_wins_then_points(client, seeded_analytics, betting_period, db_session):
    db_session.session.execute(
        text("""
        INSERT INTO sleeper_matchups (league_id, week, roster_id, matchup_id_number, points)
        VALUES ('league1', 9, 1, 1, 95.0), ('league1', 9, 2, 1, 110.0)
    """)
    )
    db_session.session.commit()

    resp = client.get("/api/league_overview")
    teams = resp.get_json()["teams"]

    assert teams[0]["label"] == "bob"
    assert teams[0]["rank"] == 1
    assert teams[0]["record"] == "1-0"
    assert teams[1]["label"] == "alice"
    assert teams[1]["record"] == "0-1"


def test_league_overview_empty_when_no_matchups(client, analytics_tables, betting_period):
    resp = client.get("/api/league_overview")
    assert resp.get_json() == {"week": 10, "teams": []}


def test_position_strength_buckets_flex_separately(client, seeded_analytics, betting_period, db_session):
    db_session.session.execute(
        text("""
        INSERT INTO team_lineups (roster_id, owner, week, slot, player_name, position, mu, var)
        VALUES (1, 'Alice A', 10, 'FLEX', 'Joe Mixon', 'RB', 13.0, 7.0)
    """)
    )
    db_session.session.commit()

    resp = client.get("/api/position_strength")
    alice = next(t for t in resp.get_json()["teams"] if t["owner"] == "Alice A")

    assert alice["by_position"]["FLEX"]["mu"] == 13.0
    assert alice["by_position"]["RB"]["mu"] == 15.0
    flex_names = [p["name"] for p in alice["by_position"]["FLEX"]["players"]]
    assert "Joe Mixon" in flex_names
    assert "Derrick Henry" not in flex_names


def test_position_strength_returns_all_seven_groups(client, analytics_tables, betting_period):
    resp = client.get("/api/position_strength")
    data = resp.get_json()

    assert data["positions"] == ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF"]
    assert data["teams"] == []


def test_team_distribution_arbitrary_pair_has_null_moneylines(
    logged_in_client, seeded_analytics, betting_period, db_session
):
    db_session.session.execute(
        text("INSERT INTO sleeper_users (user_id, username, display_name) VALUES ('u4', 'charlie', 'Charlie C')")
    )
    db_session.session.execute(
        text("INSERT INTO sleeper_rosters (roster_id, league_id, owner_id) VALUES (3, 'league1', 'u4')")
    )
    x_vals = json.dumps([90.0, 100.0, 110.0, 120.0])
    densities = json.dumps([0.005, 0.015, 0.030, 0.020])
    cdf_vals = json.dumps([0.05, 0.30, 0.80, 1.00])
    db_session.session.execute(
        text("""
            INSERT INTO team_distribution_curves
                (week, owner, x_values, density_values, cdf_values, mean, p10, p50, p90, n_sims)
            VALUES (10, 'charlie', :x, :d, :c, 105.0, 85.0, 105.0, 125.0, 50000)
        """),
        {"x": x_vals, "d": densities, "c": cdf_vals},
    )
    left_x = json.dumps([-40.0, -20.0, 0.0])
    left_y = json.dumps([0.10, 0.30, 0.45])
    right_x = json.dumps([0.0, 20.0, 40.0])
    right_y = json.dumps([0.55, 0.20, 0.05])
    db_session.session.execute(
        text("""
            INSERT INTO team_matchup_margin_curves
                (week, team_owner, opponent_owner, team_win_prob, opponent_win_prob, tie_prob,
                 left_x_values, left_y_values, right_x_values, right_y_values)
            VALUES (10, 'alice', 'charlie', 0.55, 0.45, 0.00, :lx, :ly, :rx, :ry)
        """),
        {"lx": left_x, "ly": left_y, "rx": right_x, "ry": right_y},
    )
    db_session.session.commit()

    resp = logged_in_client.get("/api/team_distribution?team=alice&opponent=charlie")
    assert resp.status_code == 200
    data = resp.get_json()

    assert data["team"]["moneyline"] is None
    assert data["opponent"]["moneyline"] is None
    assert data["team"]["win_prob"] == 0.55
    assert data["opponent"]["win_prob"] == 0.45
