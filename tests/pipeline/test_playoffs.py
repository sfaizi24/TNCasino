import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from pipeline.db import connect
from pipeline.runner import StepContext, StepResult
from pipeline.settings import Settings
from pipeline.sources import SOURCE_NAMES
from pipeline.sources.base import POSITIONS, Projection
from pipeline.sources.verify import Check, SourceReport
from pipeline.steps import league, odds, playoffs, scrape, simulate

LEAGUE_ID = "L2026"
WEEK = 11  # the playoffs start in week 14, so the step projects, picks and simulates weeks 12 and 13 itself
N_SIMS = 400
RUN_ID = "2026w11-20261117T140000"
ROSTER_POSITIONS = ["QB", "RB", "WR", "FLEX", "BN", "BN"]
LEAGUE_SETTINGS = {"playoff_teams": 2, "playoff_week_start": 14, "waiver_type": 2, "waiver_budget": 100, "num_teams": 4}
NFL_TEAMS = {1: "KC", 2: "BUF", 3: "DAL", 4: "SEA"}
# Every roster has these five players; the second running back starts only when a starter cannot.
ROSTER_PLAYERS = [("qb", "QB", 20.0), ("rb", "RB", 14.0), ("wr", "WR", 15.0), ("wr2", "WR", 12.0), ("rb2", "RB", 6.0)]
PAIRINGS = {11: [(1, 2), (3, 4)], 12: [(1, 3), (2, 4)], 13: [(1, 4), (2, 3)]}
FUTURE_SOURCES = {"sleeper", "espn", "fantasysharks"}
FUTURES_TABLES = ["betting_odds_first_place", "betting_odds_make_playoffs", "standings_probability_matrix"]


class FakeSource:
    """Projects every rostered player for whichever week is asked, scaled by his roster's strength."""

    positions = POSITIONS

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


@pytest.fixture(autouse=True)
def failing(monkeypatch) -> set[str]:
    """Websites whose rows fail their checks; every other source passes."""
    failing = set()

    def verify_source(rows, week, *references):
        website = rows[0].source
        status = "fail" if website in failing else "ok"
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


def write_simulation(settings: Settings, draws: dict[int, np.ndarray]) -> None:
    roster_ids = sorted(draws)
    matrix = np.column_stack([draws[roster_id] for roster_id in roster_ids]).astype(np.float32)
    with StepContext(settings, run_id="2026w11-20261117T120000", options={}, step="simulate") as ctx:
        draws_path = simulate.save_draws(settings, ctx.run_id, matrix, roster_ids)
        simulate.record_run(ctx, len(roster_ids), draws_path)


def run_playoffs(
    settings: Settings, requested: list[str] | None = None, no_charts: bool = True, run_id: str = RUN_ID
) -> StepResult:
    options = {"sources": requested, "no_charts": no_charts, "dry_run": False}
    with StepContext(settings, run_id=run_id, options=options, step="playoffs") as ctx:
        return playoffs.run(ctx)


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


def position_probabilities(settings: Settings) -> np.ndarray:
    """probabilities[roster_id - 1, position - 1] as the standings matrix stores them."""
    probabilities = np.zeros((len(NFL_TEAMS), len(NFL_TEAMS)))
    for row in read_table(settings, "standings_probability_matrix"):
        probabilities[row["team_id"] - 1, row["position"] - 1] = row["probability"]
    return probabilities


def probabilities_by_team(settings: Settings, table: str) -> dict[int, float]:
    return {row["team_id"]: row["probability"] for row in read_table(settings, table)}


def test_every_simulated_season_crowns_one_team_and_sends_two_to_the_playoffs(settings):
    run_playoffs(settings)

    probabilities = position_probabilities(settings)
    assert len(read_table(settings, "standings_probability_matrix")) == 16
    assert probabilities.sum(axis=1) == pytest.approx(np.ones(4))
    assert probabilities[:, 0].sum() == pytest.approx(1.0)
    assert probabilities[:, :2].sum() == pytest.approx(2.0)
    expected_first_place = dict(enumerate(probabilities[:, 0], start=1))
    expected_make_playoffs = dict(enumerate(probabilities[:, :2].sum(axis=1), start=1))
    assert probabilities_by_team(settings, "betting_odds_first_place") == pytest.approx(expected_first_place)
    assert probabilities_by_team(settings, "betting_odds_make_playoffs") == pytest.approx(expected_make_playoffs)


def test_futures_are_priced_as_fair_american_odds(settings):
    run_playoffs(settings)

    for table in ["betting_odds_first_place", "betting_odds_make_playoffs"]:
        for row in read_table(settings, table):
            assert row["american_odds"] == odds.probability_to_american_odds(row["probability"])
            assert (row["run_id"], row["season"], row["week"]) == (RUN_ID, 2026, WEEK)
            assert (row["team_name"], row["owner"]) == (f"Team {row['team_id']}", f"owner{row['team_id']}")


def test_the_summary_lists_each_future_week_and_the_leaders(settings):
    result = run_playoffs(settings)

    summary = result.summary
    assert summary["future_weeks"] == [12, 13]
    week_entry = {"sources": ["espn.com", "fantasysharks.com", "sleeper.com"], "n_players": 20, "empty_slots": 0}
    assert summary["projections"] == [{"week": 12} | week_entry, {"week": 13} | week_entry]
    assert summary["n_sims"] == N_SIMS
    first_place = probabilities_by_team(settings, "betting_odds_first_place")
    listed = {leader["owner"]: leader["probability"] for leader in summary["first_place"]}
    assert listed == {f"owner{team_id}": round(probability, 3) for team_id, probability in first_place.items()}
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
    assert read_table(settings, "betting_odds_first_place") == []
    assert list(probabilities_by_team(settings, "betting_odds_make_playoffs")) == [2, 3]
    assert len(read_table(settings, "standings_probability_matrix")) == 16


def test_stronger_projections_make_a_team_the_favourite(settings, strengths):
    strengths[1] = 3.0
    strengths[4] = 0.3

    summary = run_playoffs(settings).summary

    assert summary["first_place"][0]["owner"] == "owner1"
    assert summary["first_place"][0]["probability"] > 0.6


def test_players_on_a_bye_or_ruled_out_leave_their_slots_empty(settings, league_db):
    league_db.execute(
        "INSERT INTO nfl_schedules (season, week, team, is_home, is_bye, updated_at) VALUES (2026, 12, 'SEA', 0, 1, '')"
    )
    league_db.execute("UPDATE nfl_players SET injury_status = 'Out' WHERE player_id = '2qb'")
    league_db.commit()

    summary = run_playoffs(settings).summary

    # Week 12: roster 4 is on a bye (4 slots) and roster 2 has no quarterback (1); week 13 only the latter.
    assert [week["empty_slots"] for week in summary["projections"]] == [5, 1]


def test_future_lineups_stay_in_memory(settings):
    run_playoffs(settings)

    conn = connect(settings, "projections")
    tables = {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    conn.close()
    assert "player_week_stats" in tables
    assert "team_lineups" not in tables


def test_a_source_failing_its_checks_is_dropped_for_the_week(settings, failing):
    failing.add("espn.com")

    result = run_playoffs(settings)

    assert "dropped espn.com for weeks 12, 13: value_agreement failed" in result.warnings
    assert [week["sources"] for week in result.summary["projections"]] == [["fantasysharks.com", "sleeper.com"]] * 2


def test_sleeper_failing_its_checks_stops_the_step_before_anything_is_priced(settings, failing):
    failing.add("sleeper.com")

    with pytest.raises(RuntimeError, match="sleeper.com failed its checks for week 12: value_agreement"):
        run_playoffs(settings)

    assert not set(FUTURES_TABLES) & odds_tables(settings)


def test_only_the_requested_sources_are_scraped_but_sleeper_always_is(settings, sources):
    run_playoffs(settings, requested=["espn", "fantasypros"])

    assert {name: fake.weeks for name, fake in sources.items()} == {
        "sleeper": [12, 13],
        "espn": [12, 13],
        "fantasysharks": [],
        "fantasypros": [],  # it publishes the current week only
        "firstdown": [],
        "fanduel": [],
    }


def test_a_rerun_replaces_the_weeks_futures_and_leaves_other_weeks_alone(settings):
    conn = connect(settings, "odds")
    conn.executescript(playoffs.FUTURES_DDL)
    conn.execute(
        "INSERT INTO betting_odds_first_place (run_id, week, team_id, team_name, owner, probability, american_odds, "
        "season) VALUES ('2026w10-20261110T140000', 10, 1, 'Team 1', 'owner1', 0.5, '-100', 2026)"
    )
    conn.commit()
    conn.close()

    run_playoffs(settings, run_id="2026w11-20261117T140000")
    run_playoffs(settings, run_id="2026w11-20261117T150000")

    for table in FUTURES_TABLES:
        runs = {(row["week"], row["run_id"]) for row in read_table(settings, table)}
        assert (11, "2026w11-20261117T140000") not in runs
        assert (11, "2026w11-20261117T150000") in runs
    first_place_runs = {row["run_id"] for row in read_table(settings, "betting_odds_first_place")}
    assert "2026w10-20261110T140000" in first_place_runs
    assert len(read_table(settings, "standings_probability_matrix")) == 16


def test_the_futures_close_once_the_playoffs_start(settings):
    result = run_playoffs(replace(settings, week=14))

    assert result == StepResult({}, warnings=["playoffs have started; no futures markets"])
    assert not set(FUTURES_TABLES) & odds_tables(settings)


def test_a_league_with_divisions_is_refused(settings, league_db):
    league_db.execute("UPDATE leagues SET settings = ?", (json.dumps(LEAGUE_SETTINGS | {"divisions": 2}),))
    league_db.commit()

    with pytest.raises(RuntimeError, match="2 divisions"):
        run_playoffs(settings)


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

    assert result.charts == ["playoff_probability_week_11.png", "first_place_race_week_11.png"]
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


def test_probabilities_that_do_not_add_up_stop_the_step():
    with pytest.raises(RuntimeError, match="first-place probabilities sum to 0.900000, not 1"):
        playoffs.check_totals(np.array([0.5, 0.4]), np.array([1.0, 1.0]), 2)
    with pytest.raises(RuntimeError, match="make-playoffs probabilities sum to 1.500000, not 2"):
        playoffs.check_totals(np.array([0.5, 0.5]), np.array([1.0, 0.5]), 2)
