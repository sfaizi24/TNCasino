import json

import pytest

from pipeline.db import connect
from pipeline.runner import StepContext
from pipeline.settings import Settings
from pipeline.steps import league, lineups

LEAGUE_ID = "300"
ROSTER_POSITIONS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"]
LEAGUE_SETTINGS = {"playoff_teams": 2, "playoff_week_start": 15, "waiver_type": 2, "waiver_budget": 100, "num_teams": 3}

PLAYER_WEEK_STATS = """
CREATE TABLE IF NOT EXISTS player_week_stats (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  sleeper_player_id TEXT NOT NULL,
  player_name TEXT NOT NULL,
  position TEXT NOT NULL,
  team TEXT,
  mu REAL NOT NULL,
  sigma REAL NOT NULL,
  var REAL NOT NULL,
  n_sources INTEGER NOT NULL,
  spread REAL,
  model_version TEXT NOT NULL,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (season, week, sleeper_player_id)
);
"""

# Every roster starts QB 20, RB 14 and 12, WR 15 and 13, TE 10, FLEX 8 (its third RB), K 8 and DEF 6: 106 points.
ROSTER_PLAYERS = [
    ("qb", "QB", 20.0),
    ("rb1", "RB", 14.0),
    ("rb2", "RB", 12.0),
    ("rb3", "RB", 8.0),
    ("wr1", "WR", 15.0),
    ("wr2", "WR", 13.0),
    ("te", "TE", 10.0),
    ("k", "K", 8.0),
    ("qb2", "QB", 10.0),
]
DEFENSES = {1: "SEA", 2: "MIN", 3: "DAL"}
FREE_AGENTS = [
    ("fa_qb", "QB", 12.0),
    ("fa_rb", "RB", 9.0),
    ("fa_wr_a", "WR", 20.0),
    ("fa_wr_b", "WR", 11.0),
    ("fa_wr_c", "WR", 7.0),
    ("fa_te", "TE", 6.0),
    ("fa_k", "K", 7.0),
]


@pytest.fixture
def settings(tmp_path):
    return Settings(season=2026, week=4, league_id=LEAGUE_ID, data_dir=tmp_path)


@pytest.fixture
def league_db(settings):
    conn = connect(settings, "league")
    conn.executescript(league.MIRROR_TABLES + league.SCHEDULE_TABLE)
    yield conn
    conn.close()


@pytest.fixture
def projections_db(settings):
    conn = connect(settings, "projections")
    conn.executescript(PLAYER_WEEK_STATS)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def synthetic_league(league_db, projections_db):
    """Three rosters of healthy KC players, plus NYG free agents. Roster 1 has 40 FAAB left; rosters 2 and 3 have 80,
    with roster 3 ahead in waiver order."""
    league_db.execute(
        "INSERT INTO leagues (league_id, name, season, roster_positions, settings) VALUES (?, 'Test', '2026', ?, ?)",
        (LEAGUE_ID, json.dumps(ROSTER_POSITIONS), json.dumps(LEAGUE_SETTINGS)),
    )
    league_db.executemany(
        "INSERT INTO users (user_id, username, display_name) VALUES (?, ?, ?)",
        [("u1", "alice_login", "alice"), ("u2", "bob", None)],
    )
    rosters = [
        roster_row(1, "u1", None, 3, 0, budget_used=60, waiver_position=1, extras=["1ir", "1rookie"], reserve=["1ir"]),
        roster_row(2, "u2", "Second Team", 1, 2, budget_used=20, waiver_position=3, extras=["2taxi"], taxi=["2taxi"]),
        roster_row(3, None, None, 2, 1, budget_used=20, waiver_position=2),
    ]
    league.insert_rows(league_db, "rosters", rosters)

    for roster_id, defense in DEFENSES.items():
        for suffix, position, mu in ROSTER_PLAYERS:
            add_player(league_db, projections_db, f"{roster_id}{suffix}", position, mu)
        add_player(league_db, projections_db, defense, "DEF", 6.0, team=defense)
    add_player(league_db, projections_db, "1ir", "RB", 30.0)
    add_player(league_db, projections_db, "1rookie", "WR", None)
    add_player(league_db, projections_db, "2taxi", "WR", 25.0)

    for player_id, position, mu in FREE_AGENTS:
        add_player(league_db, projections_db, player_id, position, mu, team="NYG", sigma=3.0)
    add_player(league_db, projections_db, "NYJ", "DEF", 5.0, team="NYJ", sigma=3.0)
    add_player(league_db, projections_db, "fa_solo", "WR", 25.0, team="NYG", sigma=3.0, n_sources=1)
    add_player(league_db, projections_db, "fa_out", "RB", 30.0, team="NYG", sigma=3.0, injury_status="Out")
    add_player(league_db, projections_db, "fa_buf", "WR", 22.0, team="BUF", sigma=3.0)

    league_db.executemany(
        "INSERT INTO nfl_schedules (season, week, team, is_home, is_bye, updated_at) VALUES (2026, ?, ?, 0, 1, '')",
        [(4, "BUF"), (5, "KC")],
    )
    league_db.commit()
    projections_db.commit()


def roster_row(
    roster_id, owner_id, team_name, wins, losses, budget_used, waiver_position, extras=(), reserve=None, taxi=None
):
    players = [f"{roster_id}{suffix}" for suffix, _, _ in ROSTER_PLAYERS] + [DEFENSES[roster_id], *extras]
    return {
        "roster_id": roster_id,
        "league_id": LEAGUE_ID,
        "owner_id": owner_id,
        "team_name": team_name,
        "players": json.dumps(players),
        "reserve": json.dumps(reserve),
        "taxi": json.dumps(taxi),
        "wins": wins,
        "losses": losses,
        "waiver_budget_used": budget_used,
        "waiver_position": waiver_position,
    }


def add_player(
    league_db, projections_db, player_id, position, mu, team="KC", sigma=2.0, n_sources=3, injury_status=None
):
    league_db.execute(
        "INSERT INTO nfl_players (player_id, first_name, last_name, position, team, injury_status, fantasy_positions) "
        "VALUES (?, 'First', ?, ?, ?, ?, ?)",
        (player_id, f"Last {player_id}", position, team, injury_status, json.dumps([position])),
    )
    if mu is not None:
        projections_db.execute(
            "INSERT INTO player_week_stats VALUES (2026, 4, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'v1', '')",
            (player_id, f"Player {player_id}", position, team, mu, sigma, sigma**2, n_sources),
        )


def set_player(league_db, player_id, **columns):
    for column, value in columns.items():
        league_db.execute(f"UPDATE nfl_players SET {column} = ? WHERE player_id = ?", (value, player_id))
    league_db.commit()


def run_lineups(settings):
    with StepContext(settings, run_id="2026w04-20260929T140000", step="lineups") as ctx:
        return lineups.run(ctx)


def lineup(projections_db, roster_id):
    rows = projections_db.execute("SELECT * FROM team_lineups WHERE week = 4 AND roster_id = ?", (roster_id,))
    return {row["slot"]: row for row in rows}


def starters(projections_db, roster_id):
    return {slot: row["sleeper_player_id"] for slot, row in lineup(projections_db, roster_id).items()}


def team_totals(projections_db):
    rows = projections_db.execute("SELECT * FROM team_projections_summary WHERE week = 4")
    return {row["roster_id"]: (row["total_mu"], row["total_var"], row["waiver_pickups"]) for row in rows}


def roster_players(league_db, roster_id):
    rows = league_db.execute("SELECT * FROM projections_rosters WHERE week = 4 AND roster_id = ?", (roster_id,))
    return {row["sleeper_player_id"]: row for row in rows}


def test_healthy_rosters_start_their_best_players_and_nobody_is_replaced(settings, projections_db):
    result = run_lineups(settings)

    assert result.warnings == []
    assert starters(projections_db, 1) == {
        "QB": "1qb",
        "RB1": "1rb1",
        "RB2": "1rb2",
        "WR1": "1wr1",
        "WR2": "1wr2",
        "TE": "1te",
        "FLEX": "1rb3",
        "K": "1k",
        "DEF": "SEA",
    }
    assert team_totals(projections_db) == {1: (106.0, 36.0, 0), 2: (106.0, 36.0, 0), 3: (106.0, 36.0, 0)}
    sigmas = projections_db.execute("SELECT combined_sigma FROM team_projections_summary").fetchall()
    assert [row["combined_sigma"] for row in sigmas] == [6.0, 6.0, 6.0]
    assert result.summary["n_replacements"] == 0
    assert result.summary["unresolved"] == []
    assert result.summary["pool_sizes"] == {"QB": 1, "RB": 1, "WR": 3, "TE": 1, "K": 1, "DEF": 1}
    assert result.summary["cap_by_position"] == {"QB": 20, "RB": 13, "WR": 14, "TE": 10, "K": 8, "DEF": 6, "FLEX": 8}


def test_out_starter_leaves_a_hole_filled_at_the_capped_mu(settings, league_db, projections_db):
    set_player(league_db, "1wr1", injury_status="Out")

    result = run_lineups(settings)

    team_lineup = lineup(projections_db, 1)
    assert team_lineup["WR1"]["sleeper_player_id"] == "1wr2"
    pickup = team_lineup["WR2"]
    assert (pickup["sleeper_player_id"], pickup["nfl_team"], pickup["is_replacement"]) == ("fa_wr_a", "NYG", 1)
    # fa_wr_a projects 20 but is capped at the median WR starter (13); the spread stays his own.
    assert (pickup["mu"], pickup["sigma"], pickup["var"], pickup["n_sources"]) == (13.0, 3.0, 9.0, 3)
    assert team_totals(projections_db) == {1: (104.0, 41.0, 1), 2: (106.0, 36.0, 0), 3: (106.0, 36.0, 0)}
    assert result.summary["teams"] == [
        {
            "roster_id": 1,
            "owner": "alice",
            "total_mu": 104.0,
            "holes": [{"slot": "WR2", "replacement": "Player fa_wr_a", "mu": 13.0}],
        },
        {"roster_id": 2, "owner": "bob", "total_mu": 106.0, "holes": []},
        {"roster_id": 3, "owner": "Unknown", "total_mu": 106.0, "holes": []},
    ]
    assert result.summary["n_replacements"] == 1


def test_teams_claim_in_faab_order_with_ties_broken_by_waiver_position(settings, league_db, projections_db):
    for player_id in ("1wr1", "2wr1", "3wr1"):
        set_player(league_db, player_id, injury_status="Out")

    run_lineups(settings)

    pickups = {roster_id: lineup(projections_db, roster_id)["WR2"] for roster_id in (1, 2, 3)}
    assert {roster_id: (row["sleeper_player_id"], row["mu"]) for roster_id, row in pickups.items()} == {
        3: ("fa_wr_a", 13.0),
        2: ("fa_wr_b", 11.0),
        1: ("fa_wr_c", 7.0),
    }


def test_flex_is_filled_last_from_the_leftover_pool(settings, league_db, projections_db):
    set_player(league_db, "2rb3", injury_status="Out")
    set_player(league_db, "1wr1", injury_status="Out")

    result = run_lineups(settings)

    assert lineup(projections_db, 1)["WR2"]["sleeper_player_id"] == "fa_wr_a"
    flex = lineup(projections_db, 2)["FLEX"]
    assert (flex["sleeper_player_id"], flex["mu"], flex["is_replacement"]) == ("fa_wr_b", 8.0, 1)
    assert result.summary["cap_by_position"]["FLEX"] == 8


def test_bye_players_sit_and_stay_out_of_the_pool(settings, league_db, projections_db):
    set_player(league_db, "1rb1", team="BUF")

    run_lineups(settings)

    team_lineup = starters(projections_db, 1)
    assert (team_lineup["RB1"], team_lineup["RB2"], team_lineup["FLEX"]) == ("1rb2", "1rb3", "fa_wr_a")
    assert lineup(projections_db, 1)["FLEX"]["mu"] == 8.0
    assert roster_players(league_db, 1)["1rb1"]["roster_status"] == "bye"
    fa_buf_rows = projections_db.execute("SELECT COUNT(*) FROM team_lineups WHERE sleeper_player_id = 'fa_buf'")
    assert fa_buf_rows.fetchone()[0] == 0


def test_one_source_players_stay_out_of_the_pool_but_start_for_their_roster(settings, league_db, projections_db):
    set_player(league_db, "1wr1", injury_status="Out")
    projections_db.execute("UPDATE player_week_stats SET n_sources = 1 WHERE sleeper_player_id = '1te'")
    projections_db.commit()

    result = run_lineups(settings)

    team_lineup = starters(projections_db, 1)
    assert (team_lineup["WR2"], team_lineup["TE"]) == ("fa_wr_a", "1te")
    assert result.summary["pool_sizes"]["WR"] == 3


def test_empty_pool_leaves_an_unresolved_hole_and_warns(settings, league_db, projections_db):
    set_player(league_db, "1k", injury_status="Out")
    set_player(league_db, "fa_k", injury_status="Out")

    result = run_lineups(settings)

    kicker = lineup(projections_db, 1)["K"]
    assert (kicker["sleeper_player_id"], kicker["player_name"], kicker["position"]) == (None, None, "K")
    assert (kicker["mu"], kicker["sigma"], kicker["var"]) == (0, 0, 0)
    assert (kicker["n_sources"], kicker["is_replacement"]) == (0, 0)
    assert team_totals(projections_db)[1] == (98.0, 32.0, 0)
    assert result.warnings == ["no free agent left for K on roster 1"]
    assert result.summary["unresolved"] == [{"roster_id": 1, "owner": "alice", "slot": "K"}]
    assert result.summary["teams"][0]["holes"] == [{"slot": "K", "replacement": None, "mu": 0.0}]


def test_projections_rosters_label_every_rostered_player(settings, league_db, projections_db):
    set_player(league_db, "1wr1", injury_status="Out")
    set_player(league_db, "1rb1", team="BUF")

    run_lineups(settings)

    players = roster_players(league_db, 1)
    assert {player_id: row["roster_status"] for player_id, row in players.items()} == {
        "1qb": "starter",
        "1qb2": "bench",
        "1rb1": "bye",
        "1rb2": "starter",
        "1rb3": "starter",
        "1wr1": "out",
        "1wr2": "starter",
        "1te": "starter",
        "1k": "starter",
        "SEA": "starter",
        "1ir": "out",
        "1rookie": "unprojected",
    }
    assert roster_players(league_db, 2)["2taxi"]["roster_status"] == "out"
    own_starters = {row["sleeper_player_id"] for row in lineup(projections_db, 1).values() if not row["is_replacement"]}
    assert {player_id for player_id, row in players.items() if row["starting_status"] == 1} == own_starters
    assert (players["1wr1"]["mu"], players["1qb2"]["mu"]) == (15.0, 10.0)
    assert (players["1rookie"]["mu"], players["1rookie"]["var"]) == (0.0, 0.0)
    sea = players["SEA"]
    assert (sea["team_name"], sea["first_name"], sea["last_name"]) == ("Team 1", "First", "Last SEA")
    assert (sea["position"], sea["nfl_team"]) == ("DEF", "SEA")


def test_teams_are_labelled_by_the_display_rules(settings, projections_db):
    run_lineups(settings)

    rows = projections_db.execute("SELECT roster_id, team_name, owner, record FROM team_projections_summary")
    assert sorted(tuple(row) for row in rows) == [
        (1, "Team 1", "alice", "3-0"),
        (2, "Second Team", "bob", "1-2"),
        (3, "Team 3", "Unknown", "2-1"),
    ]


def test_slot_eligibility_follows_fantasy_positions(settings, league_db, projections_db):
    set_player(league_db, "1wr1", injury_status="Out")
    set_player(league_db, "1rb3", fantasy_positions=json.dumps(["RB", "WR"]))
    set_player(league_db, "1te", fantasy_positions="null")

    run_lineups(settings)

    team_lineup = lineup(projections_db, 1)
    assert (team_lineup["WR2"]["sleeper_player_id"], team_lineup["TE"]["sleeper_player_id"]) == ("1rb3", "1te")
    assert (team_lineup["FLEX"]["sleeper_player_id"], team_lineup["FLEX"]["is_replacement"]) == ("fa_wr_a", 1)


def test_rostered_player_missing_from_nfl_players_warns(settings, league_db, projections_db):
    roster = league_db.execute("SELECT players FROM rosters WHERE roster_id = 2").fetchone()
    players = json.loads(roster["players"]) + ["ghost"]
    league_db.execute("UPDATE rosters SET players = ? WHERE roster_id = 2", (json.dumps(players),))
    league_db.commit()

    result = run_lineups(settings)

    assert result.warnings == ["roster 2 lists player ghost, who is not in nfl_players"]
    ghost = roster_players(league_db, 2)["ghost"]
    assert (ghost["roster_status"], ghost["starting_status"], ghost["mu"]) == ("unprojected", 0, 0.0)
    assert ghost["first_name"] is None
    assert team_totals(projections_db)[2] == (106.0, 36.0, 0)


def test_rerun_replaces_only_the_weeks_rows(settings, league_db, projections_db):
    tables = [
        (projections_db, "team_lineups", 27),
        (projections_db, "team_projections_summary", 3),
        (league_db, "projections_rosters", 33),
    ]
    run_lineups(settings)
    # The first run's rows, moved to week 3, stand in for last week's.
    for conn, table, _ in tables:
        conn.execute(f"UPDATE {table} SET week = 3")
        conn.commit()

    run_lineups(settings)
    run_lineups(settings)

    for conn, table, rows_per_week in tables:
        counts = conn.execute(f"SELECT week, COUNT(*) FROM {table} GROUP BY week ORDER BY week").fetchall()
        assert [tuple(row) for row in counts] == [(3, rows_per_week), (4, rows_per_week)]


def test_missing_projections_stop_the_step(settings, projections_db):
    projections_db.execute("DELETE FROM player_week_stats")
    projections_db.commit()

    with pytest.raises(LookupError, match="run the stats step first"):
        run_lineups(settings)
