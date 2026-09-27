import json
from pathlib import Path

import pytest

from pipeline.db import connect
from pipeline.runner import StepContext
from pipeline.settings import Settings
from pipeline.steps import league

FIXTURES = Path(__file__).parent / "fixtures" / "sleeper_league"
LEAGUE_ID = "1387602586542018560"
PREVIOUS_LEAGUE_ID = "1226433368405585920"


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def scoreboard(season, week):
    payload = load("espn_scoreboard_w4.json")
    payload["week"]["number"] = week
    return payload


@pytest.fixture
def settings(tmp_path):
    return Settings(season=2026, week=4, league_id=LEAGUE_ID, data_dir=tmp_path)


@pytest.fixture
def sleeper(monkeypatch):
    """Serve the captured 2026 payloads: every week's matchups are week 3's and only week 2 has transactions."""
    responses = {
        f"/league/{LEAGUE_ID}": load("league.json"),
        f"/league/{LEAGUE_ID}/users": load("users.json"),
        f"/league/{LEAGUE_ID}/rosters": load("rosters.json"),
        "/players/nfl": load("players.json"),
    }
    for week in range(1, 5):
        responses[f"/league/{LEAGUE_ID}/matchups/{week}"] = load("matchups_w3.json")
        responses[f"/league/{LEAGUE_ID}/transactions/{week}"] = load("transactions_w2.json") if week == 2 else []
    for week in range(1, 4):
        responses[f"/stats/nfl/regular/2026/{week}"] = load("stats_w2.json")
    monkeypatch.setattr(league, "sleeper_get", lambda path: responses[path])
    monkeypatch.setattr(league, "fetch_scoreboard", scoreboard)
    return responses


def run_league(settings):
    with StepContext(settings, run_id="2026w04-20260929T140000", step="league") as ctx:
        return league.run(ctx)


def count(conn, table):
    return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def test_run_mirrors_the_league_and_schedule(settings, sleeper):
    result = run_league(settings)

    assert result.summary == {
        "n_users": 12,
        "n_rosters": 12,
        "n_matchup_rows": 48,
        "n_transactions": 36,
        "n_players": 28,
        "n_stat_rows": 600,
        "n_schedule_rows": 576,
        "byes_this_week": [],
        "owner_changes": [],
    }
    assert result.warnings == []
    conn = connect(settings, "league")
    assert count(conn, "rosters") == 12
    assert count(conn, "matchups") == 48
    assert count(conn, "transactions") == 36
    assert count(conn, "player_stats") == 600
    assert count(conn, "nfl_schedules") == 576


def test_rerun_replaces_rows(settings, sleeper):
    run_league(settings)
    run_league(settings)

    conn = connect(settings, "league")
    counts = {table: count(conn, table) for table in ("leagues", "users", "rosters", "matchups", "transactions")}
    assert counts == {"leagues": 1, "users": 12, "rosters": 12, "matchups": 48, "transactions": 36}
    assert count(conn, "nfl_players") == 28
    assert count(conn, "player_stats") == 600
    assert count(conn, "nfl_schedules") == 576


def test_rows_keep_sleeper_values(settings, sleeper):
    run_league(settings)

    conn = connect(settings, "league")
    roster = conn.execute("SELECT * FROM rosters WHERE roster_id = 1").fetchone()
    assert (roster["wins"], roster["fpts"], roster["waiver_position"], roster["waiver_budget_used"]) == (2, 280, 8, 73)
    assert roster["team_name"] is None
    matchup = conn.execute("SELECT * FROM matchups WHERE matchup_id = ?", (f"{LEAGUE_ID}_3_1",)).fetchone()
    assert (matchup["week"], matchup["matchup_id_number"], matchup["points"]) == (3, 3, 22.6)
    stats = conn.execute("SELECT * FROM player_stats WHERE stat_id = '4984_2026_2'").fetchone()
    assert (stats["pts_ppr"], stats["pass_yd"], stats["fgm"]) == (40.82, 248.0, 0)


def test_players_keep_fantasy_positions_and_defenses_as_sent(settings, sleeper):
    run_league(settings)

    conn = connect(settings, "league")
    positions = {row["position"] for row in conn.execute("SELECT position FROM nfl_players")}
    assert positions == {"QB", "RB", "WR", "TE", "K", "DEF"}
    defense = conn.execute("SELECT * FROM nfl_players WHERE player_id = 'SEA'").fetchone()
    assert (defense["full_name"], defense["first_name"], defense["last_name"]) == (None, "Seattle", "Seahawks")
    assert (defense["position"], defense["team"]) == ("DEF", "SEA")


def test_list_fields_round_trip_as_json(settings, sleeper):
    run_league(settings)

    conn = connect(settings, "league")
    stored_league = conn.execute("SELECT * FROM leagues").fetchone()
    assert json.loads(stored_league["roster_positions"])[:3] == ["QB", "RB", "RB"]
    assert json.loads(stored_league["settings"])["waiver_budget"] == 250
    roster = conn.execute("SELECT * FROM rosters WHERE roster_id = 1").fetchone()
    assert json.loads(roster["starters"])[-1] == "MIN"
    assert json.loads(roster["reserve"]) == ["8142"]
    assert json.loads(roster["taxi"]) is None
    player = conn.execute("SELECT * FROM nfl_players WHERE player_id = '8223'").fetchone()
    assert json.loads(player["fantasy_positions"]) == ["RB"]
    transaction = conn.execute("SELECT * FROM transactions WHERE adds = ?", (json.dumps({"9228": 11}),)).fetchone()
    assert json.loads(transaction["settings"]) == {"seq": 3, "waiver_bid": 45}


def test_teams_without_a_game_are_on_bye(settings, sleeper, monkeypatch):
    def scoreboard_without_first_game(season, week):
        payload = scoreboard(season, week)
        if week == 4:
            payload["events"].pop(0)
        return payload

    monkeypatch.setattr(league, "fetch_scoreboard", scoreboard_without_first_game)
    result = run_league(settings)

    assert result.summary["byes_this_week"] == ["CLE", "PIT"]
    conn = connect(settings, "league")
    bye = conn.execute("SELECT * FROM nfl_schedules WHERE week = 4 AND team = 'CLE'").fetchone()
    assert (bye["is_bye"], bye["is_home"], bye["opponent"], bye["game_date"], bye["status"]) == (1, 0, None, None, None)
    assert count(conn, "nfl_schedules") == 576


def test_parse_scoreboard_writes_one_row_per_team():
    rows = league.parse_scoreboard(load("espn_scoreboard_w4.json"), 2026, "2026-09-29T14:00:00+00:00")

    assert len(rows) == 32
    assert not any(row["is_bye"] for row in rows)
    by_team = {row["team"]: row for row in rows}
    assert "WSH" not in by_team
    assert by_team["WAS"]["opponent"] == "IND"
    assert by_team["CLE"] == {
        "season": 2026,
        "week": 4,
        "team": "CLE",
        "opponent": "PIT",
        "is_home": 1,
        "is_bye": 0,
        "game_date": "2026-10-02T00:15:00+00:00",
        "status": "STATUS_SCHEDULED",
        "updated_at": "2026-09-29T14:00:00+00:00",
    }
    assert (by_team["PIT"]["is_home"], by_team["PIT"]["opponent"]) == (0, "CLE")


def test_empty_stats_for_a_played_week_warns(settings, sleeper):
    sleeper["/stats/nfl/regular/2026/3"] = {}

    result = run_league(settings)

    assert result.warnings == ["Sleeper returned no player stats for week 3"]
    assert result.summary["n_stat_rows"] == 400


def test_load_league_settings(settings, sleeper):
    run_league(settings)

    league_settings = league.load_league_settings(connect(settings, "league"), LEAGUE_ID)

    assert league_settings.roster_positions == ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
    assert (league_settings.playoff_teams, league_settings.playoff_week_start) == (8, 15)
    assert (league_settings.waiver_type, league_settings.waiver_budget, league_settings.num_teams) == (2, 250, 12)
    assert league_settings.previous_league_id == PREVIOUS_LEAGUE_ID
    assert league_settings.slots == {
        "QB": "QB",
        "RB1": "RB",
        "RB2": "RB",
        "WR1": "WR",
        "WR2": "WR",
        "TE": "TE",
        "FLEX": "FLEX",
        "K": "K",
        "DEF": "DEF",
    }


def test_load_league_settings_needs_the_league_step(settings):
    conn = connect(settings, "league")
    conn.executescript(league.MIRROR_TABLES)

    with pytest.raises(LookupError, match="run the league step first"):
        league.load_league_settings(conn, LEAGUE_ID)


def test_owner_changes_compare_roster_ids_with_last_season(settings, sleeper):
    run_league(settings)
    conn = connect(settings, "league")
    previous_owners = {row["roster_id"]: row["owner_id"] for row in conn.execute("SELECT * FROM rosters")}
    previous_owners[9] = "1005346205276155904"
    previous_owners[12] = "former_owner_not_in_users"
    conn.execute("INSERT INTO users (user_id, display_name) VALUES ('1005346205276155904', 'Bilal879')")
    conn.executemany(
        "INSERT INTO rosters (roster_id, league_id, owner_id) VALUES (?, ?, ?)",
        [(roster_id, PREVIOUS_LEAGUE_ID, owner_id) for roster_id, owner_id in previous_owners.items()],
    )

    changes = league.find_owner_changes(conn, LEAGUE_ID, PREVIOUS_LEAGUE_ID)

    assert changes == [
        {"roster_id": 9, "previous_owner": "Bilal879", "owner": "fajandfoujee"},
        {"roster_id": 12, "previous_owner": "Unknown", "owner": "sfaizi24"},
    ]
    assert league.find_owner_changes(conn, LEAGUE_ID, None) == []
