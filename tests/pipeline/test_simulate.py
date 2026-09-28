import json
from pathlib import Path

import pandas as pd
import pytest

from pipeline.db import connect
from pipeline.runner import StepContext
from pipeline.settings import Settings
from pipeline.steps import simulate

FIXTURES = Path(__file__).parent / "fixtures" / "simulate"
RUN_ID = "2026w04-20260929T140000"

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


@pytest.fixture
def settings(tmp_path):
    return Settings(season=2026, week=4, league_id="L2026", n_sims=2000, data_dir=tmp_path, model_version="v1")


def write_league(settings: Settings, matchups: list[tuple[int, int | None]] = ()) -> None:
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


def run_simulate(settings: Settings) -> dict:
    with StepContext(settings, run_id=RUN_ID, options={}, step="simulate") as ctx:
        return simulate.run(ctx).summary


def test_simulate_writes_long_parquet_draws_and_records_the_run(settings):
    write_league(settings)
    write_lineups(settings, [1, 2], hole_mu=10.0)

    run_simulate(settings)

    draws_path = f"sims/2026/wk04/{RUN_ID}.parquet"
    draws = pd.read_parquet(settings.data_dir / draws_path)
    assert list(draws.columns) == ["sim_id", "roster_id", "total_points"]
    assert [str(dtype) for dtype in draws.dtypes] == ["int32", "int16", "float32"]
    assert len(draws) == 2 * 2000
    assert draws.groupby("roster_id")["sim_id"].nunique().to_dict() == {1: 2000, 2: 2000}
    means = draws.groupby("roster_id")["total_points"].mean()
    assert means[1] == pytest.approx(58.0, rel=0.03)
    assert means[2] == pytest.approx(62.0, rel=0.03)

    conn = connect(settings, "odds")
    run = dict(conn.execute("SELECT * FROM simulation_runs").fetchone())
    conn.close()
    assert run == {
        "run_id": RUN_ID,
        "season": 2026,
        "week": 4,
        "seed": 1738,
        "n_sims": 2000,
        "model_version": "v1",
        "n_teams": 2,
        "draws_path": draws_path,
        "created_at": run["created_at"],
    }
    assert run["created_at"].endswith("+00:00")


def test_simulate_summarises_each_team(settings):
    write_league(settings)
    write_lineups(settings, [1, 2])

    summary = run_simulate(settings)

    assert json.loads(json.dumps(summary)) == summary
    assert (summary["run_id"], summary["n_sims"], summary["seed"], summary["model_version"]) == (
        RUN_ID,
        2000,
        1738,
        "v1",
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

    summary = run_simulate(settings)

    draws = pd.read_parquet(settings.data_dir / f"sims/2026/wk15/{RUN_ID}.parquet")
    assert sorted(draws["roster_id"].unique()) == [1, 3]
    assert sorted(team["owner"] for team in summary["teams"]) == ["owner1", "owner3"]


def test_a_week_without_lineups_fails(settings):
    write_league(settings)
    write_lineups(settings, [])

    with pytest.raises(LookupError, match="no starters to simulate"):
        run_simulate(settings)
