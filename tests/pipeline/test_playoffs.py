import json
import sys
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from pipeline import markets
from pipeline.db import connect
from pipeline.model.params import load_params
from pipeline.runner import StepContext, StepResult
from pipeline.settings import Settings
from pipeline.sources import SOURCE_NAMES
from pipeline.sources.base import POSITIONS, Projection
from pipeline.sources.verify import Check, SourceReport
from pipeline.steps import clean, league, lineups, match, odds, playoffs, scrape, simulate, stats

LEAGUE_ID = "L2026"
# The playoffs start in week 14 with a one-round bracket, so the step projects, picks and simulates weeks 12 and 13
# of the regular season and the final in week 14 itself.
WEEK = 11
N_SIMS = 400
RUN_ID = "2026w11-20261117T140000"
SIMULATION_RUN_ID = "2026w11-20261117T120000"
ROSTER_POSITIONS = ["QB", "RB", "WR", "FLEX", "BN", "BN"]
LEAGUE_SETTINGS = {
    "playoff_teams": 2,
    "playoff_week_start": 14,
    "playoff_seed_type": 0,
    "playoff_round_type": 0,
    "waiver_type": 2,
    "waiver_budget": 100,
    "num_teams": 4,
}
NFL_TEAMS = {1: "KC", 2: "BUF", 3: "DAL", 4: "SEA"}
# Every roster has these five players; the second running back starts only when a starter cannot.
ROSTER_PLAYERS = [("qb", "QB", 20.0), ("rb", "RB", 14.0), ("wr", "WR", 15.0), ("wr2", "WR", 12.0), ("rb2", "RB", 6.0)]
PAIRINGS = {11: [(1, 2), (3, 4)], 12: [(1, 3), (2, 4)], 13: [(1, 4), (2, 3)]}
FUTURE_SOURCES = {"sleeper", "espn", "fantasysharks"}
MARKET_TABLES = [
    "betting_odds_make_playoffs",
    "betting_odds_last_place",
    "betting_odds_champion",
]
FUTURES_TABLES = [*MARKET_TABLES, "standings_probability_matrix"]


class FakeSource:
    """Projects every rostered player for whichever week is asked, scaled by his roster's strength."""

    positions = POSITIONS
    has_week_stamp = True

    def __init__(self, name: str, strengths: dict[int, float]):
        self.name = name
        self.website = f"{name}.com"
        self.supports_future_weeks = name in FUTURE_SOURCES
        self.strengths = strengths
        self.weeks = []

    def fetch(self, season: int, week: int) -> list[Projection]:
        self.weeks.append(week)
        rows = []
        for roster_id, strength in self.strengths.items():
            for suffix, position, points in ROSTER_PLAYERS:
                player_id = f"{roster_id}{suffix}"
                external_id = player_id if self.name == "sleeper" else None
                team = NFL_TEAMS[roster_id]
                rows.append(
                    Projection(
                        self.website, season, week, "Player", player_id, position, team, points * strength, external_id
                    )
                )
        return rows


@pytest.fixture
def settings(tmp_path):
    return Settings(season=2026, week=WEEK, league_id=LEAGUE_ID, n_sims=N_SIMS, data_dir=tmp_path)


@pytest.fixture
def league_db(settings):
    conn = connect(settings, "league")
    conn.executescript(league.MIRROR_TABLES + league.SCHEDULE_TABLE)
    yield conn
    conn.close()


@pytest.fixture(autouse=True)
def synthetic_league(settings, league_db):
    """Four 5-5 rosters of healthy players, with the current week simulated as four evenly matched teams."""
    league_db.execute(
        "INSERT INTO leagues (league_id, name, season, roster_positions, settings) VALUES (?, 'Test', '2026', ?, ?)",
        (LEAGUE_ID, json.dumps(ROSTER_POSITIONS), json.dumps(LEAGUE_SETTINGS)),
    )
    rosters = []
    for roster_id, nfl_team in NFL_TEAMS.items():
        league_db.execute(
            "INSERT INTO users (user_id, username, display_name) VALUES (?, ?, ?)",
            (f"u{roster_id}", f"login{roster_id}", f"owner{roster_id}"),
        )
        player_ids = []
        for suffix, position, _ in ROSTER_PLAYERS:
            player_ids.append(f"{roster_id}{suffix}")
            league_db.execute(
                "INSERT INTO nfl_players (player_id, first_name, last_name, position, team, fantasy_positions) "
                "VALUES (?, 'Player', ?, ?, ?, ?)",
                (player_ids[-1], player_ids[-1], position, nfl_team, json.dumps([position])),
            )
        rosters.append(
            {
                "roster_id": roster_id,
                "league_id": LEAGUE_ID,
                "owner_id": f"u{roster_id}",
                "players": json.dumps(player_ids),
                "reserve": "null",
                "taxi": "null",
                "wins": 5,
                "losses": 5,
                "fpts": 1100,
                "fpts_decimal": 0,
            }
        )
    league.insert_rows(league_db, "rosters", rosters)

    for matchup_number, pair in enumerate(PAIRINGS[WEEK], start=1):
        for roster_id in pair:
            league_db.execute(
                "INSERT INTO matchups (matchup_id, league_id, week, roster_id, matchup_id_number) VALUES (?, ?, ?, ?, ?)",
                (f"{WEEK}_{roster_id}", LEAGUE_ID, WEEK, roster_id, matchup_number),
            )
    league_db.commit()

    rng = np.random.default_rng(WEEK)
    write_simulation(settings, {roster_id: rng.normal(100.0, 15.0, N_SIMS) for roster_id in NFL_TEAMS})


@pytest.fixture
def strengths() -> dict[int, float]:
    return dict.fromkeys(NFL_TEAMS, 1.0)


@pytest.fixture(autouse=True)
def sources(monkeypatch, strengths) -> dict[str, FakeSource]:
    fakes = {}
    for name in SOURCE_NAMES:
        fakes[name] = FakeSource(name, strengths)
        monkeypatch.setitem(sys.modules, f"pipeline.sources.{name}", SimpleNamespace(SOURCE=fakes[name]))
    return fakes


@pytest.fixture
def verified() -> list[tuple[int, bool]]:
    """(week, future_week) for every source the checks were asked to verify."""
    return []


@pytest.fixture(autouse=True)
def failing(monkeypatch, verified) -> set[tuple[str, int]]:
    """(website, week) pairs whose rows fail their checks; every other scrape passes."""
    failing = set()

    def verify_source(rows, week, *references, future_week=False, has_week_stamp=True):
        verified.append((week, future_week))
        website = rows[0].source
        status = "fail" if (website, week) in failing else "ok"
        return SourceReport(website, status, [Check("value_agreement", status, "QB points disagree")], len(rows))

    monkeypatch.setattr(scrape, "verify_source", verify_source)
    return failing


@pytest.fixture(autouse=True)
def pairings(monkeypatch) -> dict[int, list[tuple[int, int]]]:
    """Sleeper's matchups endpoint, answered from these pairings instead of the network."""
    pairings = dict(PAIRINGS)

    def sleeper_get(path):
        endpoint, week = path.rsplit("/", 1)
        assert endpoint == f"/league/{LEAGUE_ID}/matchups"
        rows = []
        for matchup_id, pair in enumerate(pairings[int(week)], start=1):
            rows += [{"roster_id": roster_id, "matchup_id": matchup_id} for roster_id in pair]
        return rows

    monkeypatch.setattr(playoffs, "sleeper_get", sleeper_get)
    return pairings


def write_simulation(settings: Settings, draws: dict[int, np.ndarray], run_id: str = SIMULATION_RUN_ID) -> None:
    roster_ids = sorted(draws)
    matrix = np.column_stack([draws[roster_id] for roster_id in roster_ids]).astype(np.float32)
    with StepContext(settings, run_id=run_id, options={}, step="simulate") as ctx:
        draws_path = simulate.save_draws(settings, ctx.run_id, matrix, roster_ids)
        simulate.record_run(ctx, len(roster_ids), draws_path)


def run_weekly_steps(settings: Settings) -> None:
    """The current week's projections, matches and player stats, as a full run leaves them for the playoffs step."""
    for step in [scrape, clean, match, stats]:
        with StepContext(settings, run_id="2026w11-20261117T100000", options={}, step=step.NAME) as ctx:
            step.run(ctx)


def run_playoffs(
    settings: Settings, requested: list[str] | None = None, no_charts: bool = True, run_id: str = RUN_ID
) -> StepResult:
    options = {"sources": requested, "no_charts": no_charts, "dry_run": False}
    with StepContext(settings, run_id=run_id, options=options, step="playoffs") as ctx:
        return playoffs.run(ctx)


def write_lineup(settings: Settings) -> None:
    """One team_lineups row for the week, as only the lineups step of a weekly run leaves behind."""
    conn = connect(settings, "projections")
    conn.executescript(lineups.LINEUP_TABLES)
    conn.execute(
        "INSERT INTO team_lineups (season, week, roster_id, team_name, owner, record, slot, sleeper_player_id, "
        "player_name, position, mu, sigma, var, n_sources, timestamp) "
        "VALUES (?, ?, 1, 'Team 1', 'owner1', '5-5', 'QB', '1qb', 'Player 1qb', 'QB', 20.0, 5.0, 25.0, 1, '')",
        (settings.season, settings.week),
    )
    conn.commit()
    conn.close()


def set_record(league_db, roster_id: int, wins: int, losses: int) -> None:
    league_db.execute("UPDATE rosters SET wins = ?, losses = ? WHERE roster_id = ?", (wins, losses, roster_id))
    league_db.commit()


def read_table(settings: Settings, table: str) -> list[dict]:
    conn = connect(settings, "odds")
    rows = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY week, team_id, id")]
    conn.close()
    return rows


def odds_tables(settings: Settings) -> set[str]:
    conn = connect(settings, "odds")
    tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    return tables


def shared_rows(settings: Settings) -> dict[str, list[dict]]:
    """Every row of the weekly steps' tables that the playoffs step projects its later weeks through, by table."""
    conn = connect(settings, "projections")
    rows = {}
    for table in playoffs.SHARED_TABLES:
        rows[table] = [dict(row) for row in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
    conn.close()
    return rows


def weeks_held(rows_by_table: dict[str, list[dict]]) -> dict[str, list[int]]:
    return {table: sorted({row["week"] for row in rows}) for table, rows in rows_by_table.items()}


def position_probabilities(settings: Settings) -> np.ndarray:
    """probabilities[roster_id - 1, position - 1] as the standings matrix stores them."""
    probabilities = np.zeros((len(NFL_TEAMS), len(NFL_TEAMS)))
    for row in read_table(settings, "standings_probability_matrix"):
        probabilities[row["team_id"] - 1, row["position"] - 1] = row["probability"]
    return probabilities


def probabilities_by_team(settings: Settings, table: str) -> dict[int, float]:
    return {row["team_id"]: row["probability"] for row in read_table(settings, table)}


def standings_rows(settings: Settings) -> list[dict]:
    conn = connect(settings, "odds")
    rows = [dict(row) for row in conn.execute("SELECT * FROM simulation_standings ORDER BY run_id")]
    conn.close()
    return rows


def test_every_simulated_season_crowns_one_team_first_one_last_one_champion_and_sends_two_to_the_playoffs(settings):
    run_playoffs(settings)

    probabilities = position_probabilities(settings)
    assert len(read_table(settings, "standings_probability_matrix")) == 16
    assert probabilities.sum(axis=1) == pytest.approx(np.ones(4))
    assert probabilities[:, 0].sum() == pytest.approx(1.0)
    assert probabilities[:, :2].sum() == pytest.approx(2.0)
    assert probabilities[:, 3].sum() == pytest.approx(1.0)
    expected_make_playoffs = dict(enumerate(probabilities[:, :2].sum(axis=1), start=1))
    expected_last_place = dict(enumerate(probabilities[:, 3], start=1))
    assert probabilities_by_team(settings, "betting_odds_make_playoffs") == pytest.approx(expected_make_playoffs)
    assert probabilities_by_team(settings, "betting_odds_last_place") == pytest.approx(expected_last_place)
    # Four evenly matched teams: each wins the title often enough to be offered, so the offered chances sum to 1.
    champion = probabilities_by_team(settings, "betting_odds_champion")
    assert list(champion) == [1, 2, 3, 4]
    assert sum(champion.values()) == pytest.approx(1.0)


def test_futures_are_priced_as_fair_american_odds(settings):
    run_playoffs(settings)

    for table in MARKET_TABLES:
        for row in read_table(settings, table):
            assert row["american_odds"] == odds.probability_to_american_odds(row["probability"])
            assert (row["team_name"], row["owner"]) == (f"Team {row['team_id']}", f"owner{row['team_id']}")


def test_make_playoffs_prices_no_as_the_share_of_seasons_the_team_misses(settings):
    run_playoffs(settings)

    rows = read_table(settings, "betting_odds_make_playoffs")
    assert len(rows) == 4
    for row in rows:
        assert row["no_probability"] == 1 - row["probability"]
        assert row["no_american_odds"] == odds.probability_to_american_odds(row["no_probability"])


def test_every_team_gets_a_make_playoffs_row_with_no_price_on_a_settled_side(settings, league_db):
    set_record(league_db, 1, wins=10, losses=0)
    set_record(league_db, 4, wins=0, losses=10)

    run_playoffs(settings)

    rows = {row["team_id"]: row for row in read_table(settings, "betting_odds_make_playoffs")}
    assert list(rows) == [1, 2, 3, 4]
    sides = ["probability", "american_odds", "no_probability", "no_american_odds"]
    assert {column: rows[1][column] for column in sides} == {
        "probability": 1.0,
        "american_odds": None,
        "no_probability": 0.0,
        "no_american_odds": None,
    }
    assert {column: rows[4][column] for column in sides} == {
        "probability": 0.0,
        "american_odds": None,
        "no_probability": 1.0,
        "no_american_odds": None,
    }


def test_an_odds_db_from_before_the_no_side_is_rebuilt_keeping_its_rows(settings, league_db):
    conn = connect(settings, "odds")
    conn.executescript(playoffs.MARKET_DDL.format(table="betting_odds_make_playoffs"))
    conn.execute(
        "INSERT INTO betting_odds_make_playoffs (run_id, week, team_id, team_name, owner, probability, american_odds, "
        "season) VALUES ('2026w10-20261110T120000', 10, 1, 'Team 1', 'owner1', 0.5, '-100', 2026)"
    )
    conn.commit()
    conn.close()
    set_record(league_db, 1, wins=10, losses=0)

    run_playoffs(settings)

    earlier, *this_week = read_table(settings, "betting_odds_make_playoffs")
    assert (earlier["week"], earlier["american_odds"], earlier["no_probability"]) == (10, "-100", None)
    assert [row["team_id"] for row in this_week] == [1, 2, 3, 4]
    assert this_week[0]["american_odds"] is None
    assert "make_playoffs_before_no_side" not in odds_tables(settings)


def test_first_place_is_not_priced(settings):
    summary = run_playoffs(settings).summary

    assert "betting_odds_first_place" not in odds_tables(settings)
    assert {"make_playoffs", "last_place", "champion"} <= summary.keys()
    assert "first_place" not in summary


def test_the_simulated_seasons_are_stored_under_the_simulation_run(settings):
    run_playoffs(settings)

    [row] = standings_rows(settings)
    assert {column: value for column, value in row.items() if column not in ("created_at", "standings")} == {
        "run_id": SIMULATION_RUN_ID,
        "season": 2026,
        "week": WEEK,
        "n_sims": N_SIMS,
        "playoff_teams": 2,
        "roster_ids": "1,2,3,4",
    }
    positions, champions = markets.decode_standings(row["standings"], N_SIMS, 4)
    counts = {(team + 1, place): int((positions[:, team] == place).sum()) for team in range(4) for place in range(1, 5)}
    matrix = read_table(settings, "standings_probability_matrix")
    assert counts == {(matrix_row["team_id"], matrix_row["position"]): matrix_row["count"] for matrix_row in matrix}
    champion = probabilities_by_team(settings, "betting_odds_champion")
    assert champion == pytest.approx({team_id: (champions == team_id - 1).mean() for team_id in NFL_TEAMS})


def test_a_rerun_on_the_same_simulation_replaces_its_stored_seasons(settings, monkeypatch):
    monkeypatch.setattr(playoffs, "utc_now", lambda: datetime(2026, 11, 17, 14, tzinfo=UTC))
    run_playoffs(settings)
    monkeypatch.setattr(playoffs, "utc_now", lambda: datetime(2026, 11, 17, 15, tzinfo=UTC))
    run_playoffs(settings)

    [row] = standings_rows(settings)
    assert (row["run_id"], row["created_at"]) == (SIMULATION_RUN_ID, "2026-11-17T15:00:00+00:00")


def test_every_row_carries_the_simulation_it_was_priced_from(settings):
    run_playoffs(settings, run_id=RUN_ID)

    for table in FUTURES_TABLES:
        rows = read_table(settings, table)
        assert rows
        assert {(row["run_id"], row["season"], row["week"]) for row in rows} == {(SIMULATION_RUN_ID, 2026, WEEK)}


def test_the_champion_wins_the_bracket_not_the_regular_season(settings, league_db, strengths):
    """Team 1 is sure to finish first and team 2 second, but team 2 projects at six times team 1 in the final."""
    set_record(league_db, 1, wins=10, losses=0)
    set_record(league_db, 2, wins=6, losses=4)
    set_record(league_db, 3, wins=0, losses=10)
    set_record(league_db, 4, wins=0, losses=10)
    strengths[1] = 0.5
    strengths[2] = 3.0

    summary = run_playoffs(settings).summary

    assert position_probabilities(settings)[0, 0] == 1.0
    assert summary["champion"] == [
        {"owner": "owner2", "probability": 1.0},
        {"owner": "owner1", "probability": 0.0},
        {"owner": "owner3", "probability": 0.0},
        {"owner": "owner4", "probability": 0.0},
    ]
    assert read_table(settings, "betting_odds_champion") == []


def test_the_summary_lists_each_future_week_and_the_leaders(settings):
    result = run_playoffs(settings)

    summary = result.summary
    assert summary["future_weeks"] == [12, 13, 14]
    assert summary["playoff_weeks"] == [14]
    week_entry = {"sources": ["espn.com", "fantasysharks.com", "sleeper.com"], "n_players": 20, "empty_slots": 0}
    retained = {12: 0.95, 13: 0.9025, 14: 0.8574}
    assert summary["projections"] == [
        {"week": week} | week_entry | {"edge_retained": retained[week]} for week in [12, 13, 14]
    ]
    assert summary["n_sims"] == N_SIMS
    assert summary["standings_bytes"] == len(standings_rows(settings)[0]["standings"])
    make_playoffs = probabilities_by_team(settings, "betting_odds_make_playoffs")
    listed = {leader["owner"]: leader["probability"] for leader in summary["make_playoffs"]}
    assert listed == {f"owner{team_id}": round(probability, 3) for team_id, probability in make_playoffs.items()}
    assert list(listed.values()) == sorted(listed.values(), reverse=True)
    assert json.loads(json.dumps(summary)) == summary
    assert result.charts == []


def test_a_settled_race_is_not_offered_but_stays_in_the_matrix(settings, league_db):
    set_record(league_db, 1, wins=10, losses=0)
    set_record(league_db, 4, wins=0, losses=10)

    run_playoffs(settings)

    probabilities = position_probabilities(settings)
    assert probabilities[0, 0] == 1.0
    assert probabilities[3, 3] == 1.0
    assert read_table(settings, "betting_odds_last_place") == []
    assert list(probabilities_by_team(settings, "betting_odds_champion")) == [1, 2, 3]
    assert len(read_table(settings, "standings_probability_matrix")) == 16


def test_stronger_projections_make_a_team_the_favourite(settings, strengths):
    strengths[1] = 3.0
    strengths[4] = 0.3

    run_playoffs(settings)

    assert position_probabilities(settings)[0, 0] > 0.6


def test_players_on_a_bye_or_ruled_out_leave_their_slots_empty(settings, league_db):
    league_db.execute(
        "INSERT INTO nfl_schedules (season, week, team, is_home, is_bye, updated_at) VALUES (2026, 12, 'SEA', 0, 1, '')"
    )
    league_db.execute("UPDATE nfl_players SET injury_status = 'Out' WHERE player_id = '2qb'")
    league_db.commit()

    summary = run_playoffs(settings).summary

    # Week 12: roster 4 is on a bye (4 slots) and roster 2 has no quarterback (1); weeks 13 and 14 only the latter.
    assert [week["empty_slots"] for week in summary["projections"]] == [5, 1, 1]


def test_a_later_week_keeps_part_of_each_rosters_edge(settings):
    starters = pd.DataFrame({"position": ["QB", "QB", "RB", "RB"], "mu": [24.0, 16.0, 10.0, 10.0]})

    discounted = playoffs.discount_edges(starters, playoffs.edge_retained(2, load_params("v3")))

    # The quarterbacks' edges shrink by 0.95^2 toward their mean of 20; running backs already at the mean stay.
    assert discounted["mu"].tolist() == pytest.approx([23.61, 16.39, 10.0, 10.0])
    assert starters["mu"].tolist() == [24.0, 16.0, 10.0, 10.0]


def test_a_model_without_a_horizon_keeps_the_whole_edge():
    assert playoffs.edge_retained(6, load_params("v2.3")) == 1.0
    assert playoffs.edge_retained(0, load_params("v3")) == 1.0


def test_future_lineups_stay_in_memory(settings):
    run_playoffs(settings)

    conn = connect(settings, "projections")
    tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert "player_week_stats" in tables
    assert "team_lineups" not in tables


def test_the_later_weeks_projections_last_only_for_the_run(settings):
    run_weekly_steps(settings)
    current_week = shared_rows(settings)

    result = run_playoffs(settings)

    assert [week["n_players"] for week in result.summary["projections"]] == [20, 20, 20]
    after = shared_rows(settings)
    assert weeks_held(after) == {table: [WEEK] for table in playoffs.SHARED_TABLES}
    assert after == current_week


def test_a_run_that_fails_on_a_later_week_clears_the_weeks_before_it(settings, failing):
    run_weekly_steps(settings)
    current_week = shared_rows(settings)
    failing.add(("sleeper.com", 13))  # after week 12 has been scraped, matched and given player stats

    with pytest.raises(RuntimeError, match="sleeper.com failed its checks for week 13"):
        run_playoffs(settings)

    after = shared_rows(settings)
    assert weeks_held(after) == {table: [WEEK] for table in playoffs.SHARED_TABLES}
    assert after == current_week


def test_a_week_the_weekly_steps_have_moved_past_is_refused(settings):
    """Its cleanup would delete the later week's projections along with the run's own scratch rows."""
    later = replace(settings, week=12)
    run_weekly_steps(later)
    write_lineup(later)
    before = shared_rows(settings)

    with pytest.raises(RuntimeError, match="week 12 has already been run"):
        run_playoffs(settings)

    assert shared_rows(settings) == before
    assert "betting_odds_make_playoffs" not in odds_tables(settings)


def test_a_source_failing_its_checks_is_dropped_for_the_week(settings, failing):
    failing.update({("espn.com", 12), ("espn.com", 14)})

    result = run_playoffs(settings)

    assert "dropped espn.com for weeks 12, 14: value_agreement: QB points disagree" in result.warnings
    assert [week["sources"] for week in result.summary["projections"]] == [
        ["fantasysharks.com", "sleeper.com"],
        ["espn.com", "fantasysharks.com", "sleeper.com"],
        ["fantasysharks.com", "sleeper.com"],
    ]


def test_later_weeks_are_verified_as_future_weeks(settings, verified):
    run_playoffs(settings)

    assert sorted(set(verified)) == [(12, True), (13, True), (14, True)]


def test_sleeper_failing_its_checks_stops_the_step_before_anything_is_priced(settings, failing):
    failing.add(("sleeper.com", 12))

    with pytest.raises(RuntimeError, match="sleeper.com failed its checks for week 12: value_agreement"):
        run_playoffs(settings)

    assert not set(FUTURES_TABLES) & odds_tables(settings)


def test_only_the_requested_sources_are_scraped_but_sleeper_always_is(settings, sources):
    run_playoffs(settings, requested=["espn", "firstdown"])  # firstdown publishes the current week only

    scraped = {"sleeper", "espn"}
    assert {name: fake.weeks for name, fake in sources.items()} == {
        name: [12, 13, 14] if name in scraped else [] for name in SOURCE_NAMES
    }


def test_a_rerun_on_the_same_simulation_keeps_its_run_id(settings):
    run_playoffs(settings, run_id="2026w11-20261117T140000")
    run_playoffs(settings, run_id="2026w11-20261117T150000")

    for table in FUTURES_TABLES:
        assert {row["run_id"] for row in read_table(settings, table)} == {SIMULATION_RUN_ID}
    assert len(read_table(settings, "standings_probability_matrix")) == 16


def test_a_rerun_on_a_newer_simulation_replaces_the_weeks_futures_and_leaves_other_weeks_alone(settings):
    conn = connect(settings, "odds")
    conn.executescript(playoffs.FUTURES_DDL)
    conn.execute(
        "INSERT INTO betting_odds_last_place (run_id, week, team_id, team_name, owner, probability, american_odds, "
        "season) VALUES ('2026w10-20261110T120000', 10, 1, 'Team 1', 'owner1', 0.5, '-100', 2026)"
    )
    conn.commit()
    conn.close()
    run_playoffs(settings)
    newer_simulation = "2026w11-20261118T120000"
    rng = np.random.default_rng(WEEK + 1)
    write_simulation(
        settings, {roster_id: rng.normal(100.0, 15.0, N_SIMS) for roster_id in NFL_TEAMS}, newer_simulation
    )

    run_playoffs(settings)

    for table in FUTURES_TABLES:
        assert {row["run_id"] for row in read_table(settings, table) if row["week"] == WEEK} == {newer_simulation}
    last_place_runs = {row["run_id"] for row in read_table(settings, "betting_odds_last_place")}
    assert "2026w10-20261110T120000" in last_place_runs
    assert [row["run_id"] for row in standings_rows(settings)] == [SIMULATION_RUN_ID, newer_simulation]
    assert len(read_table(settings, "standings_probability_matrix")) == 16


def test_the_futures_close_once_the_playoffs_start(settings):
    result = run_playoffs(replace(settings, week=14))

    assert result == StepResult({}, warnings=["playoffs have started; no futures markets"])
    assert not set(FUTURES_TABLES) & odds_tables(settings)


@pytest.mark.parametrize(
    ("setting", "refusal"),
    [
        ({"divisions": 2}, "the league has 2 divisions"),
        ({"playoff_seed_type": 1}, "playoff_seed_type is 1"),
        ({"playoff_round_type": 1}, "playoff_round_type is 1"),
        ({"playoff_teams": 3}, "playoff_teams is 3"),
    ],
)
def test_a_league_the_standings_or_the_bracket_cannot_price_is_refused(settings, league_db, setting, refusal):
    league_db.execute("UPDATE leagues SET settings = ?", (json.dumps(LEAGUE_SETTINGS | setting),))
    league_db.commit()

    with pytest.raises(RuntimeError, match=refusal):
        run_playoffs(settings)

    assert not set(FUTURES_TABLES) & odds_tables(settings)


def test_the_current_week_must_be_simulated_first(settings):
    with pytest.raises(LookupError, match="no simulation run for season 2026 week 12"):
        run_playoffs(replace(settings, week=12))


def test_missing_games_and_pairings_are_flagged(settings, league_db, pairings):
    set_record(league_db, 1, wins=4, losses=5)
    pairings[13] = []

    warnings = run_playoffs(settings).warnings

    assert "no matchups for weeks 13; their scores add points but no wins" in warnings
    assert "rosters show 9, 10 games played, not 10; the standings miss the difference" in warnings


def test_charts_are_drawn_unless_switched_off(settings):
    result = run_playoffs(settings, no_charts=False)

    assert result.charts == ["playoff_probability_week_11.png"]
    assert all((settings.images_dir / name).exists() for name in result.charts)


def test_future_pairings_come_from_sleepers_matchup_ids(monkeypatch):
    rows = [
        {"roster_id": 3, "matchup_id": 1},
        {"roster_id": 1, "matchup_id": 2},
        {"roster_id": 2, "matchup_id": 1},
        {"roster_id": 5, "matchup_id": None},
        {"roster_id": 4, "matchup_id": 2},
    ]
    monkeypatch.setattr(playoffs, "sleeper_get", lambda path: rows)

    assert playoffs.fetch_pairings(LEAGUE_ID, 12) == [(2, 3), (1, 4)]


def test_points_to_date_read_sleepers_decimal_column_as_hundredths(league_db):
    league_db.execute("UPDATE rosters SET fpts = 1100, fpts_decimal = 36 WHERE roster_id = 1")
    league_db.commit()

    records = playoffs.record_to_date(league_db, LEAGUE_ID, [1, 2])

    assert records["points"].tolist() == pytest.approx([1100.36, 1100.0])


@pytest.mark.parametrize(
    ("market", "chances", "refusal"),
    [
        ("make_playoffs", [1.0, 0.5], "make_playoffs probabilities sum to 1.500000, not 2"),
        ("last_place", [0.7, 0.4], "last_place probabilities sum to 1.100000, not 1"),
        ("champion", [0.0, 0.0], "champion probabilities sum to 0.000000, not 1"),
    ],
)
def test_probabilities_that_do_not_add_up_stop_the_step(market, chances, refusal):
    balanced = {
        "make_playoffs": np.array([1.0, 1.0]),
        "last_place": np.array([0.5, 0.5]),
        "champion": np.array([0.5, 0.5]),
    }

    with pytest.raises(RuntimeError, match=refusal):
        playoffs.check_totals(balanced | {market: np.array(chances)}, 2)
