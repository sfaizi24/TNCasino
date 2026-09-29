import json
import shutil
import sqlite3
from contextlib import closing
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pipeline.runner import StepContext, StepFailed, StepResult
from pipeline.settings import Settings
from pipeline.steps import odds, playoffs, simulate, validate
from pipeline.steps.league import MIRROR_TABLES, insert_rows
from pipeline.steps.lineups import LINEUP_TABLES, ROSTER_TABLE

N_SIMS = 1000
SIMULATION_RUN_ID = "2026w04-20260929T140000"
NEWER_RUN_ID = "2026w04-20260929T150000"
WRITTEN_AT = "2026-09-29T14:00:00+00:00"
SLOTS = ["QB", "RB", "WR"]
USERS = [
    {"user_id": "U1", "username": None, "display_name": "alice"},
    {"user_id": "U2", "username": None, "display_name": "bob"},
    {"user_id": "U3", "username": None, "display_name": "carol"},
    {"user_id": "U4", "username": "dave", "display_name": None},
]
OWNERS = {1: "alice", 2: "bob", 3: "carol", 4: "dave"}
GAMES = {1: 1, 2: 1, 3: 2, 4: 2}  # roster_id -> matchup_id_number

BREAKS = [
    ("lineup_slots", "projections", "DELETE FROM team_lineups WHERE roster_id = 3 AND slot = 'WR'", "(3, WR)"),
    (
        "probabilities",
        "odds",
        "UPDATE betting_odds_team_ou SET over_prob = 1.5 WHERE team_id = 2",
        "betting_odds_team_ou.over_prob = 1.5",
    ),
    (
        "moneyline_sums",
        "odds",
        "UPDATE betting_odds_matchup_ml SET ties = ties + 5 WHERE team1_id = 3",
        "Team 3 vs Team 4 sums to 1.005000",
    ),
    (
        "matchup_consistency",
        "odds",
        "DELETE FROM betting_odds_matchup_ml WHERE team1_id = 3",
        "(3, 4) only in league.db",
    ),
    ("matchup_consistency", "odds", "DELETE FROM betting_odds_team_ou WHERE team_id = 4", "team 4 has no team O/U"),
    (
        "scorer_markets",
        "odds",
        "DELETE FROM betting_odds_lowest_scorer WHERE team_id = 2",
        "betting_odds_lowest_scorer misses team 2",
    ),
    ("curves", "odds", "DELETE FROM team_distribution_curves WHERE owner = 'bob'", "no distribution curve for bob"),
    (
        "curves",
        "odds",
        "DELETE FROM team_matchup_margin_curves WHERE team_owner = 'dave' AND opponent_owner = 'carol'",
        "no margin curve for dave vs carol",
    ),
    ("simulation_draws", "odds", "UPDATE simulation_runs SET draws_path = 'sims/gone.parquet'", "sims/gone.parquet"),
    ("simulation_draws", "odds", "UPDATE simulation_runs SET n_teams = 3", "4000 rows, not 1000 sims x 3 teams"),
    ("frozen_tables", "league", "DELETE FROM projections_rosters", "in projections_rosters"),
    (
        "probabilities",
        "odds",
        "UPDATE betting_odds_champion SET probability = -0.25 WHERE team_id = 3",
        "betting_odds_champion.probability = -0.25",
    ),
    (
        "owners",
        "odds",
        "UPDATE betting_odds_highest_scorer SET owner = 'mallory' WHERE team_id = 1",
        "'mallory' in betting_odds_highest_scorer.owner",
    ),
    (
        "owners",
        "odds",
        "UPDATE betting_odds_last_place SET owner = 'mallory' WHERE team_id = 4",
        "'mallory' in betting_odds_last_place.owner",
    ),
    ("unique_orderings", "odds", "UPDATE betting_odds_team_ou SET owner = 'alice' WHERE team_id = 2", "'alice' x2"),
    (
        "unique_orderings",
        "odds",
        "UPDATE betting_odds_matchup_ml SET matchup = 'Team 1 vs Team 2' WHERE team1_id = 3",
        "'Team 1 vs Team 2' x2",
    ),
]


@pytest.fixture(scope="module")
def consistent_week(tmp_path_factory) -> Path:
    data_dir = tmp_path_factory.mktemp("week")
    build_week(week_settings(data_dir))
    return data_dir


@pytest.fixture
def settings(consistent_week, tmp_path) -> Settings:
    """A copy of the consistent week for one test to break."""
    shutil.copytree(consistent_week, tmp_path, dirs_exist_ok=True)
    return week_settings(tmp_path)


def week_settings(data_dir: Path) -> Settings:
    return Settings(season=2026, week=4, league_id="L2026", n_sims=N_SIMS, data_dir=data_dir)


def build_week(settings: Settings, games: dict = GAMES, futures: bool = True, playoff_week_start: int = 15) -> None:
    """A week every check passes: lineups for each roster, the simulation of the teams with a game and its odds."""
    with StepContext(settings, SIMULATION_RUN_ID) as ctx:
        write_league(ctx.db("league"), settings, games, playoff_week_start)
        write_lineups(ctx.db("projections"), settings)
        if futures:
            write_futures(ctx.db("odds"), settings)
    with_a_game = [roster_id for roster_id, game in games.items() if game is not None]
    write_simulation(settings, SIMULATION_RUN_ID, with_a_game or list(OWNERS))
    with StepContext(settings, SIMULATION_RUN_ID, options={"no_charts": True}, step="odds") as ctx:
        odds.run(ctx)


def write_league(conn: sqlite3.Connection, settings: Settings, games: dict, playoff_week_start: int) -> None:
    conn.executescript(MIRROR_TABLES + ROSTER_TABLE)
    league_settings = {
        "playoff_teams": 2,
        "playoff_week_start": playoff_week_start,
        "waiver_type": 2,
        "waiver_budget": 100,
        "num_teams": len(OWNERS),
    }
    league = {
        "league_id": settings.league_id,
        "name": "Test League",
        "season": str(settings.season),
        "roster_positions": json.dumps([*SLOTS, "BN"]),
        "settings": json.dumps(league_settings),
    }
    insert_rows(conn, "leagues", [league])
    insert_rows(conn, "users", USERS)
    rosters = [
        {"roster_id": roster_id, "league_id": settings.league_id, "owner_id": f"U{roster_id}"} for roster_id in OWNERS
    ]
    insert_rows(conn, "rosters", rosters)
    matchups = [
        {
            "matchup_id": f"{settings.week}-{roster_id}",
            "league_id": settings.league_id,
            "week": settings.week,
            "roster_id": roster_id,
            "matchup_id_number": game,
        }
        for roster_id, game in games.items()
    ]
    insert_rows(conn, "matchups", matchups)
    roster_rows = [
        {
            "season": settings.season,
            "week": settings.week,
            "roster_id": roster_id,
            "team_name": f"Team {roster_id}",
            "sleeper_player_id": f"{roster_id}-{slot}",
            "mu": 10.0,
            "var": 16.0,
            "starting_status": 1,
            "roster_status": "starter",
            "timestamp": WRITTEN_AT,
        }
        for roster_id in OWNERS
        for slot in SLOTS
    ]
    insert_rows(conn, "projections_rosters", roster_rows)
    conn.commit()


def write_lineups(conn: sqlite3.Connection, settings: Settings) -> None:
    conn.executescript(LINEUP_TABLES)
    rows = [
        {
            "season": settings.season,
            "week": settings.week,
            "roster_id": roster_id,
            "team_name": f"Team {roster_id}",
            "owner": owner,
            "record": "2-1",
            "slot": slot,
            "sleeper_player_id": f"{roster_id}-{slot}",
            "player_name": f"Player {roster_id}-{slot}",
            "position": slot,
            "nfl_team": "KC",
            "mu": 10.0,
            "sigma": 4.0,
            "var": 16.0,
            "n_sources": 3,
            "timestamp": WRITTEN_AT,
        }
        for roster_id, owner in OWNERS.items()
        for slot in SLOTS
    ]
    insert_rows(conn, "team_lineups", rows)
    conn.commit()


def write_futures(conn: sqlite3.Connection, settings: Settings) -> None:
    """The playoffs step's tables: every market priced for every roster, and each roster's finishing positions."""
    conn.executescript(playoffs.FUTURES_DDL)
    stamp = {"run_id": SIMULATION_RUN_ID, "week": settings.week, "season": settings.season}
    markets = [
        ("betting_odds_first_place", 0.25),
        ("betting_odds_make_playoffs", 0.5),
        ("betting_odds_last_place", 0.25),
        ("betting_odds_champion", 0.25),
    ]
    for table, probability in markets:
        rows = [
            stamp
            | {
                "team_id": roster_id,
                "team_name": f"Team {roster_id}",
                "owner": owner,
                "probability": probability,
                "american_odds": odds.probability_to_american_odds(probability),
            }
            for roster_id, owner in OWNERS.items()
        ]
        insert_rows(conn, table, rows)
    matrix = [
        stamp
        | {
            "team_id": roster_id,
            "team_name": f"Team {roster_id}",
            "owner": owner,
            "position": position,
            "probability": 0.25,
            "count": N_SIMS // 4,
        }
        for roster_id, owner in OWNERS.items()
        for position in range(1, len(OWNERS) + 1)
    ]
    insert_rows(conn, "standings_probability_matrix", matrix)
    conn.commit()


def write_simulation(settings: Settings, run_id: str, roster_ids: list[int]) -> None:
    draws = np.random.default_rng(7).normal(100, 15, (N_SIMS, len(roster_ids))).astype(np.float32)
    draws_path = simulate.save_draws(settings, run_id, draws, roster_ids)
    with StepContext(settings, run_id, step="simulate") as ctx:
        simulate.record_run(ctx, len(roster_ids), draws_path)


def execute(settings: Settings, database: str, statement: str) -> None:
    with closing(sqlite3.connect(settings.db_paths[database])) as conn:
        conn.execute(statement)
        conn.commit()


def run_validate(settings: Settings) -> StepResult:
    with StepContext(settings, "validate-run", step="validate") as ctx:
        return validate.run(ctx)


def failed_checks(settings: Settings) -> dict[str, str]:
    with pytest.raises(StepFailed) as failure:
        run_validate(settings)
    return {check["name"]: check["detail"] for check in failure.value.summary["checks"] if check["status"] == "fail"}


def statuses(result: StepResult) -> dict[str, str]:
    return {check["name"]: check["status"] for check in result.summary["checks"]}


def test_a_consistent_week_passes_every_check_and_logs_each_one(settings, capsys):
    result = run_validate(settings)

    assert statuses(result) == {check.__name__: "ok" for check in validate.CHECKS}
    assert (result.summary["n_ok"], result.summary["n_warn"], result.summary["n_fail"]) == (12, 0, 0)
    assert result.warnings == []
    logged = [line for line in capsys.readouterr().out.splitlines() if line.startswith("  [validate]")]
    assert logged == [f"  [validate] {check['name']}: ok - {check['detail']}" for check in result.summary["checks"]]


@pytest.mark.parametrize(("check", "database", "statement", "culprit"), BREAKS)
def test_each_break_fails_the_check_that_names_it(settings, check, database, statement, culprit):
    execute(settings, database, statement)

    failures = failed_checks(settings)

    assert list(failures) == [check]
    assert culprit in failures[check]


def test_a_failure_keeps_every_check_on_record(settings):
    execute(settings, "odds", "UPDATE betting_odds_team_ou SET over_prob = 1.5 WHERE team_id = 2")

    with pytest.raises(StepFailed) as failure:
        run_validate(settings)

    summary = failure.value.summary
    assert str(failure.value) == "1 of 12 checks failed: probabilities"
    assert [check["name"] for check in summary["checks"]] == [check.__name__ for check in validate.CHECKS]
    assert (summary["n_ok"], summary["n_warn"], summary["n_fail"]) == (11, 0, 1)


def test_futures_missing_in_the_regular_season_are_a_warning(tmp_path):
    settings = week_settings(tmp_path)
    build_week(settings, futures=False)

    result = run_validate(settings)

    assert statuses(result)["frozen_tables"] == "warn"
    assert (result.summary["n_ok"], result.summary["n_warn"], result.summary["n_fail"]) == (11, 1, 0)
    assert result.warnings == [
        "frozen_tables: no standings for week 4 in standings_probability_matrix; has the playoffs step run?"
    ]


def test_every_futures_market_offered_is_counted(settings):
    result = run_validate(settings)

    details = {check["name"]: check["detail"] for check in result.summary["checks"]}
    assert details["frozen_tables"] == "9 tables have rows for week 4; 4 futures markets offered"


def test_empty_futures_markets_are_fine_once_the_standings_are_written(tmp_path):
    """Every team outside the 1-99% band leaves a market empty; the standings matrix shows the step ran."""
    settings = week_settings(tmp_path)
    build_week(settings)
    for table in validate.FUTURES_TABLES:
        execute(settings, "odds", f"DELETE FROM {table}")

    result = run_validate(settings)

    assert statuses(result)["frozen_tables"] == "ok"
    details = {check["name"]: check["detail"] for check in result.summary["checks"]}
    assert details["frozen_tables"] == "9 tables have rows for week 4; 0 futures markets offered"


def test_a_playoff_week_expects_odds_only_for_the_teams_with_a_game(tmp_path):
    settings = week_settings(tmp_path)
    build_week(settings, games={1: 1, 2: 1, 3: None, 4: None}, futures=False, playoff_week_start=4)

    result = run_validate(settings)

    assert result.summary["n_ok"] == 12
    details = {check["name"]: check["detail"] for check in result.summary["checks"]}
    assert details["scorer_markets"] == "highest and lowest scorer price the 2 teams in play"
    assert details["lineup_slots"] == "4 rosters fill all 3 slots"


def test_a_week_without_matchups_expects_every_roster_priced(tmp_path):
    settings = week_settings(tmp_path)
    build_week(settings, games={})

    failures = failed_checks(settings)

    assert list(failures) == ["matchup_consistency", "frozen_tables"]
    assert failures["matchup_consistency"].startswith("team 1 has 0 moneylines")
    assert "matchups" in failures["frozen_tables"]


def test_an_older_broken_run_is_ignored(settings):
    # A higher run_id, but created earlier: publish would upload the newer run, so validate reads that one.
    execute(
        settings,
        "odds",
        "INSERT INTO betting_odds_team_ou SELECT '2026w04-20260930T000000', week, season, team_id, team_name, owner, "
        "line, 1.5, over_odds, under_prob, under_odds, push_count, '2026-09-01T00:00:00+00:00' FROM betting_odds_team_ou",
    )

    result = run_validate(settings)

    assert result.summary["n_fail"] == 0


def test_odds_left_on_an_older_simulation_fail(settings):
    write_simulation(settings, NEWER_RUN_ID, list(OWNERS))

    failures = failed_checks(settings)

    assert failures == {
        "odds_run": f"7 odds tables are priced from {SIMULATION_RUN_ID}, not the latest simulation {NEWER_RUN_ID}"
    }


def test_lineup_mu_names_a_row_without_a_spread(settings):
    with StepContext(settings, "validate-run") as ctx:
        week = validate.load_week(ctx)
    # team_lineups declares mu, sigma and var NOT NULL, so only a doctored week can hold the break.
    first, *others = week.rows["team_lineups"]
    doctored = replace(week, rows={**week.rows, "team_lineups": [{**dict(first), "sigma": None}, *others]})

    status, detail = validate.lineup_mu(doctored)

    assert status == "fail"
    assert f"({first['roster_id']}, {first['slot']})" in detail


def test_details_stay_ascii_when_an_owner_is_not(settings):
    owner = "Zo\N{LATIN SMALL LETTER E WITH DIAERESIS}"
    execute(settings, "odds", f"UPDATE betting_odds_highest_scorer SET owner = '{owner}' WHERE team_id = 1")

    failures = failed_checks(settings)

    assert list(failures) == ["owners"]
    assert failures["owners"].isascii()
    assert "Zo\\xeb" in failures["owners"]
