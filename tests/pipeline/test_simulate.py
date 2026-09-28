import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pytest

from pipeline.db import connect
from pipeline.runner import StepContext, StepResult
from pipeline.settings import Settings
from pipeline.steps import league, simulate

FIXTURES = Path(__file__).parent / "fixtures" / "simulate"
RUN_ID = "2026w04-20260929T140000"
# The Tuesday before week 4's games, a minute into the run.
MADE_AT = datetime(2026, 9, 29, 14, 1, 30, tzinfo=UTC)

# (slot, position, nfl_team, mu, sigma); roster r's players get mu + r, so its expected total is 54 + 4r.
LINEUP = [
    ("QB", "QB", "KC", 20.0, 7.0),
    ("RB1", "RB", "KC", 14.0, 9.0),
    ("WR1", "WR", "BUF", 12.0, 10.0),
    ("K", "K", "BUF", 8.0, 4.0),
]
ROSTER_POSITIONS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN"]
LEAGUE_SETTINGS = {
    "playoff_week_start": 15,
    "playoff_teams": 6,
    "waiver_type": 2,
    "waiver_budget": 250,
    "num_teams": 12,
}
# (roster_id, wins, losses, ties) once Sleeper has counted week 3.
RECORDS = [(1, 2, 1, 0), (2, 1, 1, 1)]
# (week, team, opponent, is_home, is_bye, game_date): week 4 from Thursday to Monday night with a bye, then week 5.
SCHEDULE = [
    (4, "CLE", "PIT", 1, 0, "2026-10-02T00:15:00+00:00"),
    (4, "PIT", "CLE", 0, 0, "2026-10-02T00:15:00+00:00"),
    (4, "WAS", "IND", 1, 0, "2026-10-04T13:30:00+00:00"),
    (4, "IND", "WAS", 0, 0, "2026-10-04T13:30:00+00:00"),
    (4, "KC", "BUF", 1, 0, "2026-10-06T00:15:00+00:00"),
    (4, "BUF", "KC", 0, 0, "2026-10-06T00:15:00+00:00"),
    (4, "SEA", None, 0, 1, None),
    (5, "CLE", "NYJ", 1, 0, "2026-10-09T00:15:00+00:00"),
]
# simulation_runs as an odds.db made before the run columns were added holds it.
OLD_SIMULATION_RUNS_DDL = """
CREATE TABLE simulation_runs (
  run_id TEXT PRIMARY KEY,
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  seed INTEGER NOT NULL,
  n_sims INTEGER NOT NULL,
  model_version TEXT NOT NULL,
  n_teams INTEGER NOT NULL,
  draws_path TEXT NOT NULL,
  created_at TEXT NOT NULL
)
"""


@pytest.fixture
def settings(tmp_path):
    return Settings(season=2026, week=4, league_id="L2026", n_sims=2000, data_dir=tmp_path, model_version="v1")


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    monkeypatch.setattr(simulate, "utc_now", lambda: MADE_AT)


def write_league(
    settings: Settings,
    matchups: list[tuple[int, int | None]] = (),
    records: list[tuple[int, int, int, int]] = RECORDS,
    schedule: list[tuple] = SCHEDULE,
) -> None:
    conn = connect(settings, "league")
    conn.execute(
        "CREATE TABLE leagues (league_id TEXT PRIMARY KEY, roster_positions TEXT, settings TEXT, previous_league_id TEXT)"
    )
    conn.execute(
        "INSERT INTO leagues VALUES (?, ?, ?, ?)",
        (settings.league_id, json.dumps(ROSTER_POSITIONS), json.dumps(LEAGUE_SETTINGS), None),
    )
    conn.execute("CREATE TABLE matchups (league_id TEXT, week INTEGER, roster_id INTEGER, matchup_id_number INTEGER)")
    conn.executemany(
        "INSERT INTO matchups VALUES (?, ?, ?, ?)",
        [(settings.league_id, settings.week, roster_id, matchup_id) for roster_id, matchup_id in matchups],
    )
    conn.execute("CREATE TABLE rosters (league_id TEXT, roster_id INTEGER, wins INTEGER, losses INTEGER, ties INTEGER)")
    conn.executemany(
        "INSERT INTO rosters VALUES (?, ?, ?, ?, ?)", [(settings.league_id, *record) for record in records]
    )
    conn.executescript(league.SCHEDULE_TABLE)
    conn.executemany(
        "INSERT INTO nfl_schedules (season, week, team, opponent, is_home, is_bye, game_date, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, '')",
        [(settings.season, *game) for game in schedule],
    )
    conn.commit()
    conn.close()


def write_lineups(settings: Settings, roster_ids: list[int], hole_mu: float | None = None) -> None:
    """Every roster starts LINEUP; with hole_mu set, each also has an unresolved TE slot projected at hole_mu."""
    rows = []
    for roster_id in roster_ids:
        players = [
            (f"{roster_id}-{slot}", slot, position, team, mu + roster_id, sigma)
            for slot, position, team, mu, sigma in LINEUP
        ]
        if hole_mu is not None:
            players.append((None, "TE", "TE", None, hole_mu, 8.0))
        for player_id, slot, position, team, mu, sigma in players:
            rows.append(
                {
                    "season": settings.season,
                    "week": settings.week,
                    "roster_id": roster_id,
                    "team_name": f"Team {roster_id}",
                    "owner": f"owner{roster_id}",
                    "record": "3-0",
                    "slot": slot,
                    "sleeper_player_id": player_id,
                    "player_name": player_id,
                    "position": position,
                    "nfl_team": team,
                    "mu": mu,
                    "sigma": sigma,
                    "var": sigma**2,
                    "n_sources": 3,
                    "timestamp": "2026-09-29T13:00:00+00:00",
                }
            )
    conn = connect(settings, "projections")
    conn.executescript((FIXTURES / "team_lineups.sql").read_text())
    pd.DataFrame(rows).to_sql("team_lineups", conn, if_exists="append", index=False)
    conn.close()


def run_simulate(settings: Settings) -> StepResult:
    with StepContext(settings, run_id=RUN_ID, options={}, step="simulate") as ctx:
        return simulate.run(ctx)


def recorded_runs(settings: Settings) -> list[dict]:
    conn = connect(settings, "odds")
    runs = [dict(row) for row in conn.execute("SELECT * FROM simulation_runs ORDER BY created_at")]
    conn.close()
    return runs


def test_simulate_writes_long_parquet_draws_and_records_the_run(settings):
    write_league(settings)
    write_lineups(settings, [1, 2], hole_mu=10.0)

    result = run_simulate(settings)

    draws_path = f"sims/2026/wk04/{RUN_ID}.parquet"
    draws = pd.read_parquet(settings.data_dir / draws_path)
    assert list(draws.columns) == ["sim_id", "roster_id", "total_points"]
    assert [str(dtype) for dtype in draws.dtypes] == ["int32", "int16", "float32"]
    assert len(draws) == 2 * 2000
    assert draws.groupby("roster_id")["sim_id"].nunique().to_dict() == {1: 2000, 2: 2000}
    means = draws.groupby("roster_id")["total_points"].mean()
    assert means[1] == pytest.approx(58.0, rel=0.03)
    assert means[2] == pytest.approx(62.0, rel=0.03)

    assert recorded_runs(settings) == [
        {
            "run_id": RUN_ID,
            "season": 2026,
            "week": 4,
            "seed": 1738,
            "n_sims": 2000,
            "model_version": "v1",
            "n_teams": 2,
            "draws_path": draws_path,
            "created_at": "2026-09-29T14:01:30+00:00",
            "n_locked": 0,
            "window_closes_at": "2026-10-02T00:15:00+00:00",
            "standings_through_week": 3,
        }
    ]
    assert result.warnings == []


def test_simulate_summarises_each_team(settings):
    write_league(settings)
    write_lineups(settings, [1, 2])

    summary = run_simulate(settings).summary

    assert json.loads(json.dumps(summary)) == summary
    assert (summary["run_id"], summary["n_sims"], summary["seed"], summary["model_version"]) == (
        RUN_ID,
        2000,
        1738,
        "v1",
    )
    assert (summary["n_locked"], summary["window_closes_at"], summary["standings_through_week"]) == (
        0,
        "2026-10-02T00:15:00+00:00",
        3,
    )
    assert summary["elapsed_s"] >= 0
    assert [team["owner"] for team in summary["teams"]] == ["owner2", "owner1"]
    for team in summary["teams"]:
        assert set(team) == {"owner", "mean", "p10", "p50", "p90"}
        assert team["p10"] < team["p50"] < team["p90"]


def test_playoff_weeks_simulate_only_rosters_with_a_matchup(tmp_path):
    settings = Settings(season=2026, week=15, league_id="L2026", n_sims=500, data_dir=tmp_path)
    write_league(settings, matchups=[(1, 1), (2, None), (3, 1), (4, None)])
    write_lineups(settings, [1, 2, 3, 4])

    summary = run_simulate(settings).summary

    draws = pd.read_parquet(settings.data_dir / f"sims/2026/wk15/{RUN_ID}.parquet")
    assert sorted(draws["roster_id"].unique()) == [1, 3]
    assert sorted(team["owner"] for team in summary["teams"]) == ["owner1", "owner3"]


def test_a_week_without_lineups_fails(settings):
    write_league(settings)
    write_lineups(settings, [])

    with pytest.raises(LookupError, match="no starters to simulate"):
        run_simulate(settings)


@pytest.mark.parametrize(
    ("made_at", "closes_at"),
    [
        pytest.param(MADE_AT, "2026-10-02T00:15:00+00:00", id="tuesday"),
        pytest.param(datetime(2026, 10, 2, 5, 10, tzinfo=UTC), "2026-10-04T13:30:00+00:00", id="after thursday"),
        pytest.param(datetime(2026, 10, 4, 13, 30, tzinfo=UTC), "2026-10-06T00:15:00+00:00", id="at a kickoff"),
    ],
)
def test_the_betting_window_closes_at_the_first_kickoff_after_the_run(settings, monkeypatch, made_at, closes_at):
    write_league(settings)
    write_lineups(settings, [1, 2])
    monkeypatch.setattr(simulate, "utc_now", lambda: made_at)

    run_simulate(settings)

    assert recorded_runs(settings)[0]["window_closes_at"] == closes_at


@pytest.mark.parametrize(
    ("made_at", "schedule"),
    [
        # Monday night's game has begun, and week 5's Thursday game belongs to another week.
        pytest.param(datetime(2026, 10, 6, 1, 0, tzinfo=UTC), SCHEDULE, id="after the last kickoff"),
        pytest.param(MADE_AT, [], id="no schedule rows"),
        # The migrated 2025 weeks list their games without kickoff times.
        pytest.param(MADE_AT, [(4, "CLE", "PIT", 1, 0, None), (4, "PIT", "CLE", 0, 0, None)], id="no kickoff times"),
    ],
)
def test_a_run_with_no_kickoff_left_has_no_betting_window_and_warns(settings, monkeypatch, made_at, schedule):
    write_league(settings, schedule=schedule)
    write_lineups(settings, [1, 2])
    monkeypatch.setattr(simulate, "utc_now", lambda: made_at)

    result = run_simulate(settings)

    assert recorded_runs(settings)[0]["window_closes_at"] is None
    assert result.summary["window_closes_at"] is None
    assert result.warnings == ["no week 4 kickoff after this run in nfl_schedules; it has no betting window"]


def test_the_standings_count_the_fewest_games_a_roster_of_the_league_has_played(settings):
    # Roster 2's record still misses week 3.
    write_league(settings, records=[(1, 2, 1, 0), (2, 1, 1, 0)])
    write_lineups(settings, [1, 2])
    conn = connect(settings, "league")
    conn.execute("INSERT INTO rosters VALUES ('another league', 1, 0, 0, 0)")
    conn.commit()
    conn.close()

    run_simulate(settings)

    assert recorded_runs(settings)[0]["standings_through_week"] == 2


def test_an_odds_db_from_before_the_run_columns_gains_them_with_their_defaults(settings):
    write_league(settings)
    write_lineups(settings, [1, 2])
    conn = connect(settings, "odds")
    conn.execute(OLD_SIMULATION_RUNS_DDL)
    conn.execute(
        "INSERT INTO simulation_runs VALUES (?, 2026, 3, 1738, 50000, 'v2.1', 12, ?, '2026-09-22T14:00:00+00:00')",
        ("2026w03-20260922T140000", "sims/2026/wk03/2026w03-20260922T140000.parquet"),
    )
    conn.commit()
    conn.close()

    run_simulate(settings)

    runs = recorded_runs(settings)
    assert list(runs[1]) == [
        "run_id",
        "season",
        "week",
        "seed",
        "n_sims",
        "model_version",
        "n_teams",
        "draws_path",
        "created_at",
        "n_locked",
        "window_closes_at",
        "standings_through_week",
    ]
    assert [(run["n_locked"], run["window_closes_at"], run["standings_through_week"]) for run in runs] == [
        (0, None, 0),
        (0, "2026-10-02T00:15:00+00:00", 3),
    ]
