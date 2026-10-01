import numpy as np
import pytest

from pipeline.db import connect
from pipeline.runner import StepContext, StepResult
from pipeline.settings import Settings
from pipeline.steps import accuracy, league, lineups, match, odds, simulate, stats

SEASON = 2026
WEEK = 11
EVALUATED = WEEK - 1
LEAGUE_ID = "L2026"
# sleeper_player_id: (position, consensus projection, actual points or None for a player without a stat line)
PLAYERS = {
    "qb1": ("QB", 18.0, 20.0),
    "rb1": ("RB", 12.0, 10.0),
    "rb2": ("RB", 6.0, None),
    "wr1": ("WR", 14.0, 15.0),
    "wr2": ("WR", 7.0, 3.0),
    "bench": ("WR", 1.0, 12.0),
}
SOURCE_POINTS = {
    "sleeper.com": {"qb1": 17.0, "rb1": 11.0, "rb2": 5.0, "wr1": 13.0, "wr2": 8.0, "bench": 1.5},
    "espn.com": {"qb1": 21.0, "rb1": 14.0, "wr1": 16.0, "bench": 0.5},
}
# roster_id: (projected total, Sleeper's matchup number, points scored); roster 5 had no opponent that week
TEAMS = {1: (115.0, 1, 120.0), 2: (105.0, 1, 100.0), 3: (100.0, 2, 110.0), 4: (112.0, 2, 110.0), 5: (90.0, None, 95.0)}
# The latest unlocked run's 10th and 90th percentiles by owner, and its moneylines; owner4 has no curve, roster 5 no
# line. Its curve means are the projected totals above.
RANGES = {"owner1": (95.0, 135.0), "owner2": (105.0, 140.0), "owner3": (80.0, 120.0), "owner5": (70.0, 110.0)}
MONEYLINES = [(1, 0.6, 2, 0.4), (3, 0.45, 4, 0.55)]
# The week's NFL games as (home, away), all final.
GAMES = [("KC", "DEN"), ("BUF", "MIA")]
EARLIER_RUN = ("2026w10-20261109T140000", "2026-11-09T14:00:00")
LATEST_RUN = ("2026w10-20261110T140000", "2026-11-10T14:00:00")
# A rerun after Thursday's game, with three players fixed at their real points.
LOCKED_RUN = ("2026w10-20261113T140000", "2026-11-13T14:00:00")
NO_UNLOCKED_RUN = "week 10 has no run without locked players; team ranges and moneylines not scored"
MEASURES = ["n", "mae", "bias", "corr"]


@pytest.fixture
def settings(tmp_path):
    return Settings(season=SEASON, week=WEEK, league_id=LEAGUE_ID, data_dir=tmp_path)


@pytest.fixture(autouse=True)
def played_week(settings):
    """Week 10 as the pipeline leaves it: final games, stat lines, scores, projections, lineups and two unlocked
    runs."""
    write_league(settings)
    write_projections(settings)
    write_odds(settings)


def write_league(settings: Settings) -> None:
    conn = connect(settings, "league")
    conn.executescript(league.MIRROR_TABLES + league.SCHEDULE_TABLE)
    for home, away in GAMES:
        add_game(conn, home, away)
    for player_id, (_, _, actual) in PLAYERS.items():
        if actual is not None:
            add_stat_line(conn, player_id, EVALUATED, actual)
    add_stat_line(conn, "qb1", EVALUATED - 1, 40.0)
    for roster_id, (_, matchup_number, points) in TEAMS.items():
        conn.execute(
            "INSERT INTO matchups (matchup_id, league_id, week, roster_id, matchup_id_number, points) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (f"{LEAGUE_ID}_{EVALUATED}_{roster_id}", LEAGUE_ID, EVALUATED, roster_id, matchup_number, points),
        )
    conn.commit()
    conn.close()


def add_game(conn, home: str, away: str) -> None:
    for team, opponent, is_home in [(home, away, 1), (away, home, 0)]:
        conn.execute(
            "INSERT INTO nfl_schedules (season, week, team, opponent, is_home, is_bye, game_date, status, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 0, '2026-11-15T18:00:00', 'STATUS_FINAL', '2026-11-17T12:00:00')",
            (SEASON, EVALUATED, team, opponent, is_home),
        )


def add_stat_line(conn, player_id: str, week: int, points: float) -> None:
    conn.execute(
        "INSERT INTO player_stats (stat_id, player_id, season, week, pts_ppr) VALUES (?, ?, ?, ?, ?)",
        (f"{player_id}_{SEASON}_{week}", player_id, SEASON, week, points),
    )


def write_projections(settings: Settings) -> None:
    conn = connect(settings, "projections")
    conn.executescript(match.PROJECTIONS_WITH_SLEEPER_DDL + stats.PLAYER_WEEK_STATS_DDL + lineups.LINEUP_TABLES)
    for source, points in SOURCE_POINTS.items():
        for player_id, projected in points.items():
            add_source_projection(conn, source, player_id, PLAYERS[player_id][0], projected)
    add_source_projection(conn, "espn.com", None, "RB", 9.0, name="Unmatched")
    for player_id, (position, mu, _) in PLAYERS.items():
        add_consensus(conn, player_id, position, mu)
    for roster_id, (projected, _, _) in TEAMS.items():
        conn.execute(
            "INSERT INTO team_projections_summary (season, week, roster_id, team_name, owner, record, total_mu, "
            "combined_sigma, total_var, waiver_pickups, timestamp) VALUES (?, ?, ?, ?, ?, '5-4', ?, 20, 400, 0, ?)",
            (SEASON, EVALUATED, roster_id, f"Team {roster_id}", f"owner{roster_id}", projected, "2026-11-10T12:00:00"),
        )
    conn.commit()
    conn.close()


def add_source_projection(
    conn, source: str, player_id: str | None, position: str, projected: float, name: str = ""
) -> None:
    row = {
        "source_website": source,
        "season": SEASON,
        "week": EVALUATED,
        "player_first_name": "Player",
        "player_last_name": name or player_id,
        "position": position,
        "team": "KC",
        "projected_points": projected,
        "external_id": None,
        "sleeper_player_id": player_id,
        "match_method": "exact",
        "created_at": "2026-11-10T12:00:00",
    }
    conn.execute(match.INSERT_MATCH, row)


def add_consensus(conn, player_id: str, position: str, mu: float) -> None:
    row = {
        "season": SEASON,
        "week": EVALUATED,
        "sleeper_player_id": player_id,
        "player_name": f"Player {player_id}",
        "position": position,
        "team": "KC",
        "mu": mu,
        "sigma": 3.0,
        "var": 9.0,
        "n_sources": 2,
        "spread": 1.0,
        "p10": None,
        "p90": None,
        "source_low": None,
        "source_high": None,
        "model_version": "v1",
        "computed_at": "2026-11-10T12:00:00",
    }
    conn.execute(stats.INSERT_STATS, row)


def write_odds(settings: Settings) -> None:
    """The latest run as RANGES and MONEYLINES describe it, after an earlier run that priced every team differently."""
    conn = connect(settings, "odds")
    conn.executescript(odds.ODDS_DDL + simulate.SIMULATION_RUNS_DDL)
    add_run(conn, EARLIER_RUN, n_locked=0)
    for owner in ["owner1", "owner4"]:
        add_curve(conn, EARLIER_RUN, owner, 55.0, 50.0, 60.0)
    add_moneyline(conn, EARLIER_RUN, (1, 0.9, 2, 0.1))
    add_moneyline(conn, EARLIER_RUN, (5, 0.5, 4, 0.5))
    add_run(conn, LATEST_RUN, n_locked=0)
    for owner, (p10, p90) in RANGES.items():
        roster_id = int(owner.removeprefix("owner"))
        add_curve(conn, LATEST_RUN, owner, TEAMS[roster_id][0], p10, p90)
    for line in MONEYLINES:
        add_moneyline(conn, LATEST_RUN, line)
    conn.commit()
    conn.close()


def add_run(conn, odds_run: tuple[str, str], n_locked: int) -> None:
    run_id, created_at = odds_run
    conn.execute(
        "INSERT INTO simulation_runs (run_id, season, week, seed, n_sims, model_version, n_teams, draws_path, "
        "created_at, n_locked) VALUES (?, ?, ?, 1738, 1000, 'v2.3', 5, 'draws.parquet', ?, ?)",
        (run_id, SEASON, EVALUATED, created_at, n_locked),
    )


def add_curve(conn, odds_run: tuple[str, str], owner: str, mean: float, p10: float, p90: float) -> None:
    run_id, created_at = odds_run
    conn.execute(
        "INSERT INTO team_distribution_curves (run_id, week, season, owner, x_values, density_values, cdf_values, "
        "mean, p10, p50, p90, n_sims, created_at) VALUES (?, ?, ?, ?, '[]', '[]', '[]', ?, ?, ?, ?, 1000, ?)",
        (run_id, EVALUATED, SEASON, owner, mean, p10, mean, p90, created_at),
    )


def add_locked_rerun(settings: Settings) -> None:
    """Friday's rerun: newer than every unlocked run, priced differently, with a summary it overwrote to count the
    Thursday players' real points."""
    conn = connect(settings, "odds")
    add_run(conn, LOCKED_RUN, n_locked=3)
    for roster_id in TEAMS:
        add_curve(conn, LOCKED_RUN, f"owner{roster_id}", 150.0, 140.0, 160.0)
    add_moneyline(conn, LOCKED_RUN, (1, 0.99, 2, 0.01))
    add_moneyline(conn, LOCKED_RUN, (3, 0.99, 4, 0.01))
    conn.commit()
    conn.close()
    conn = connect(settings, "projections")
    conn.execute("UPDATE team_projections_summary SET total_mu = total_mu + 20")
    conn.commit()
    conn.close()


def add_moneyline(conn, odds_run: tuple[str, str], line: tuple[int, float, int, float]) -> None:
    run_id, created_at = odds_run
    team1_id, team1_win_prob, team2_id, team2_win_prob = line
    conn.execute(
        "INSERT INTO betting_odds_matchup_ml (run_id, week, season, team1_id, team1_win_prob, team2_id, "
        "team2_win_prob, ties, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (run_id, EVALUATED, SEASON, team1_id, team1_win_prob, team2_id, team2_win_prob, created_at),
    )


def run_accuracy(settings: Settings, no_charts: bool = True) -> StepResult:
    options = {"no_charts": no_charts}
    with StepContext(settings, run_id="2026w11-20261117T140000", options=options, step=accuracy.NAME) as ctx:
        return accuracy.run(ctx)


def read_rows(settings: Settings, query: str) -> list[dict]:
    conn = connect(settings, "projections")
    rows = [dict(row) for row in conn.execute(query)]
    conn.close()
    return rows


def player_accuracy(settings: Settings) -> dict[tuple[str, str], dict]:
    rows = read_rows(settings, "SELECT * FROM prediction_accuracy")
    return {(row["source"], row["position"]): row for row in rows}


def team_accuracy(settings: Settings) -> dict[int, dict]:
    rows = read_rows(settings, "SELECT * FROM team_accuracy ORDER BY roster_id")
    return {row["roster_id"]: row for row in rows}


def measures(row: dict) -> dict:
    return {measure: row[measure] for measure in MEASURES}


def test_each_source_and_the_consensus_are_scored_by_position_and_overall(settings):
    run_accuracy(settings)

    rows = player_accuracy(settings)
    sources = ["consensus", "sleeper.com", "espn.com"]
    assert set(rows) == {(source, position) for source in sources for position in ["QB", "RB", "WR", "ALL"]}
    consensus = rows[("consensus", "ALL")]
    assert (consensus["season"], consensus["week"]) == (SEASON, EVALUATED)
    expected_corr = np.corrcoef([18, 12, 6, 14, 7], [20, 10, 0, 15, 3])[0, 1]
    assert measures(consensus) == pytest.approx({"n": 5, "mae": 3.0, "bias": 1.8, "corr": expected_corr})
    assert measures(rows[("consensus", "RB")]) == pytest.approx({"n": 2, "mae": 4.0, "bias": 4.0, "corr": None})
    assert measures(rows[("consensus", "WR")]) == pytest.approx({"n": 2, "mae": 2.5, "bias": 1.5, "corr": None})
    expected_corr = np.corrcoef([17, 11, 5, 13, 8], [20, 10, 0, 15, 3])[0, 1]
    assert measures(rows[("sleeper.com", "ALL")]) == pytest.approx(
        {"n": 5, "mae": 3.2, "bias": 1.2, "corr": expected_corr}
    )


def test_only_matched_players_the_consensus_projects_for_two_points_are_scored(settings):
    run_accuracy(settings)

    rows = player_accuracy(settings)
    assert rows[("sleeper.com", "WR")]["n"] == 2
    expected_corr = np.corrcoef([21, 14, 16], [20, 10, 15])[0, 1]
    assert measures(rows[("espn.com", "ALL")]) == pytest.approx(
        {"n": 3, "mae": 2.0, "bias": 2.0, "corr": expected_corr}
    )


def test_a_player_a_source_lists_twice_is_scored_once_on_the_mean(settings):
    conn = connect(settings, "projections")
    add_source_projection(conn, "espn.com", "wr1", "WR", 18.0, name="wr1 Jr.")
    conn.commit()
    conn.close()

    run_accuracy(settings)

    assert measures(player_accuracy(settings)[("espn.com", "WR")]) == pytest.approx(
        {"n": 1, "mae": 2.0, "bias": 2.0, "corr": None}
    )


TEAM_COLUMNS = ["owner", "projected", "actual", "p10", "p90", "covered", "win_prob", "won"]


def team_grades(settings: Settings) -> dict[int, list]:
    return {roster_id: [team[column] for column in TEAM_COLUMNS] for roster_id, team in team_accuracy(settings).items()}


def test_teams_are_scored_against_the_latest_unlocked_run(settings):
    result = run_accuracy(settings)

    assert team_grades(settings) == {
        1: ["owner1", 115.0, 120.0, 95.0, 135.0, 1, 0.6, 1],
        2: ["owner2", 105.0, 100.0, 105.0, 140.0, 0, 0.4, 0],
        3: ["owner3", 100.0, 110.0, 80.0, 120.0, 1, 0.45, None],
        4: ["owner4", 112.0, 110.0, None, None, None, 0.55, None],
        5: ["owner5", 90.0, 95.0, 70.0, 110.0, 1, None, None],
    }
    assert result.warnings == [
        "1 of 5 teams have no score distribution for week 10; coverage left empty",
        "1 of 5 teams have no moneyline for week 10; win probability left empty",
    ]


def test_a_newer_run_with_locked_players_is_not_graded(settings):
    add_locked_rerun(settings)

    run_accuracy(settings)

    # Projected is the unlocked run's curve mean; owner4, without a curve in that run, keeps the overwritten summary's.
    assert team_grades(settings) == {
        1: ["owner1", 115.0, 120.0, 95.0, 135.0, 1, 0.6, 1],
        2: ["owner2", 105.0, 100.0, 105.0, 140.0, 0, 0.4, 0],
        3: ["owner3", 100.0, 110.0, 80.0, 120.0, 1, 0.45, None],
        4: ["owner4", 132.0, 110.0, None, None, None, 0.55, None],
        5: ["owner5", 90.0, 95.0, 70.0, 110.0, 1, None, None],
    }


def test_a_week_with_only_locked_runs_scores_players_but_no_team_ranges_or_moneylines(settings):
    add_locked_rerun(settings)
    conn = connect(settings, "odds")
    conn.execute("UPDATE simulation_runs SET n_locked = 2 WHERE n_locked = 0")
    conn.commit()
    conn.close()

    result = run_accuracy(settings)

    assert result.warnings == [NO_UNLOCKED_RUN]
    assert player_accuracy(settings)[("consensus", "ALL")]["n"] == 5
    teams = list(team_accuracy(settings).values())
    ranges_and_lines = [(team["p10"], team["p90"], team["covered"], team["win_prob"]) for team in teams]
    assert ranges_and_lines == [(None, None, None, None)] * 5
    assert [team["won"] for team in teams] == [1, 0, None, None, None]


def test_summary_reports_the_consensus_and_the_teams(settings):
    result = run_accuracy(settings)

    expected_corr = round(np.corrcoef([18, 12, 6, 14, 7], [20, 10, 0, 15, 3])[0, 1], 3)
    assert result.summary == {
        "week_evaluated": 10,
        "n_players": 5,
        "consensus": {"ALL": {"mae": 3.0, "bias": 1.8, "corr": expected_corr}},
        "best_source_by_position": {},
        "team_mae": 5.4,
        "coverage_80": 0.75,
        "moneyline_brier": 0.16,
        "n_teams": 5,
    }


def test_the_best_source_at_a_position_projected_at_least_twenty_players_there(settings):
    projections = connect(settings, "projections")
    stat_lines = connect(settings, "league")
    for number in range(20):
        player_id = f"te{number}"
        add_consensus(projections, player_id, "TE", 10.0)
        add_stat_line(stat_lines, player_id, EVALUATED, 8.0)
        add_source_projection(projections, "sleeper.com", player_id, "TE", 11.0)
        add_source_projection(projections, "espn.com", player_id, "TE", 12.0)
    # Perfect, but on too few tight ends to be named the best.
    for number in range(19):
        add_source_projection(projections, "fantasypros.com", f"te{number}", "TE", 8.0)
    for conn in [projections, stat_lines]:
        conn.commit()
        conn.close()

    result = run_accuracy(settings)

    assert player_accuracy(settings)[("fantasypros.com", "TE")]["mae"] == 0.0
    assert result.summary["best_source_by_position"] == {"TE": "sleeper.com", "ALL": "sleeper.com"}


def test_without_a_run_or_a_summary_a_team_is_projected_the_sum_of_its_lineup(settings):
    conn = connect(settings, "odds")
    conn.execute("DROP TABLE simulation_runs")
    conn.close()
    conn = connect(settings, "projections")
    conn.execute("DELETE FROM team_projections_summary")
    for roster_id, (projected, _, _) in TEAMS.items():
        for slot in ["QB", "FLEX"]:
            conn.execute(
                "INSERT INTO team_lineups (season, week, roster_id, team_name, owner, record, slot, position, mu, "
                "sigma, var, n_sources, timestamp) VALUES (?, ?, ?, ?, ?, '5-4', ?, 'QB', ?, 3, 9, 2, ?)",
                (SEASON, EVALUATED, roster_id, "Team", f"owner{roster_id}", slot, projected / 2, "2026-11-10"),
            )
    conn.commit()
    conn.close()

    run_accuracy(settings)

    teams = team_accuracy(settings)
    assert {roster_id: (team["owner"], team["projected"]) for roster_id, team in teams.items()} == {
        roster_id: (f"owner{roster_id}", projected) for roster_id, (projected, _, _) in TEAMS.items()
    }


def test_teams_are_not_scored_for_a_week_without_lineups(settings):
    conn = connect(settings, "projections")
    conn.executescript("DROP TABLE team_projections_summary; DROP TABLE team_lineups;")
    conn.close()

    result = run_accuracy(settings)

    assert result.warnings == ["no lineups for week 10; teams not scored"]
    assert team_accuracy(settings) == {}
    assert player_accuracy(settings)[("consensus", "ALL")]["n"] == 5
    team_measures = {key: result.summary[key] for key in ["team_mae", "coverage_80", "moneyline_brier", "n_teams"]}
    assert team_measures == {"team_mae": None, "coverage_80": None, "moneyline_brier": None, "n_teams": 0}


def test_without_odds_the_ranges_and_win_probabilities_are_left_empty(settings):
    conn = connect(settings, "odds")
    conn.executescript("DROP TABLE team_distribution_curves; DROP TABLE betting_odds_matchup_ml;")
    conn.close()

    result = run_accuracy(settings)

    teams = list(team_accuracy(settings).values())
    ranges_and_lines = [(team["p10"], team["p90"], team["covered"], team["win_prob"]) for team in teams]
    assert ranges_and_lines == [(None, None, None, None)] * 5
    assert [team["won"] for team in teams] == [1, 0, None, None, None]
    assert result.warnings == [
        "5 of 5 teams have no score distribution for week 10; coverage left empty",
        "5 of 5 teams have no moneyline for week 10; win probability left empty",
    ]
    team_measures = {key: result.summary[key] for key in ["team_mae", "coverage_80", "moneyline_brier"]}
    assert team_measures == {"team_mae": 5.4, "coverage_80": None, "moneyline_brier": None}


def test_a_game_result_needs_both_scores_and_a_winner():
    scores = [
        {"roster_id": 1, "matchup_id_number": 1, "points": 120.0},
        {"roster_id": 2, "matchup_id_number": 1, "points": 100.0},
        {"roster_id": 3, "matchup_id_number": 2, "points": 110.0},
        {"roster_id": 4, "matchup_id_number": 2, "points": 110.0},
        {"roster_id": 5, "matchup_id_number": 3, "points": 95.0},
        {"roster_id": 6, "matchup_id_number": None, "points": 80.0},
    ]

    assert accuracy.game_results(scores) == {1: 1, 2: 0}


def test_nothing_is_scored_before_the_week_has_stat_lines(settings):
    conn = connect(settings, "league")
    conn.execute("DELETE FROM player_stats WHERE week = ?", (EVALUATED,))
    conn.commit()
    conn.close()

    result = run_accuracy(settings)

    assert result == StepResult({}, warnings=["no actuals for week 10 yet"])
    assert read_rows(settings, "SELECT name FROM sqlite_master WHERE name LIKE '%accuracy'") == []


def test_nothing_is_scored_while_games_of_the_week_are_still_to_finish(settings):
    conn = connect(settings, "league")
    conn.execute("UPDATE nfl_schedules SET status = 'STATUS_SCHEDULED' WHERE team IN ('BUF', 'MIA')")
    conn.commit()
    conn.close()

    result = run_accuracy(settings)

    assert result == StepResult({}, warnings=["week 10 is not over: 1 of 2 games not final"])
    assert read_rows(settings, "SELECT name FROM sqlite_master WHERE name LIKE '%accuracy'") == []


def test_games_without_a_status_count_as_played(settings):
    conn = connect(settings, "league")
    conn.execute("UPDATE nfl_schedules SET status = NULL")
    conn.commit()
    conn.close()

    run_accuracy(settings)

    assert player_accuracy(settings)[("consensus", "ALL")]["n"] == 5


@pytest.mark.parametrize("table", ["projections_with_sleeper", "player_week_stats"])
def test_nothing_is_scored_for_a_week_without_projections(settings, table):
    conn = connect(settings, "projections")
    conn.execute(f"DELETE FROM {table}")
    conn.commit()
    conn.close()

    result = run_accuracy(settings)

    assert result == StepResult({}, warnings=["no projections for week 10"])
    assert read_rows(settings, "SELECT name FROM sqlite_master WHERE name LIKE '%accuracy'") == []


def test_a_rerun_replaces_the_evaluated_week_and_keeps_the_others(settings):
    run_accuracy(settings)
    conn = connect(settings, "projections")
    conn.execute(
        "INSERT INTO prediction_accuracy SELECT season, week - 1, source, position, n, mae, bias, corr, computed_at "
        "FROM prediction_accuracy"
    )
    conn.execute(
        "INSERT INTO team_accuracy SELECT season, week - 1, roster_id, owner, projected, actual, p10, p90, covered, "
        "win_prob, won, computed_at FROM team_accuracy"
    )
    conn.commit()
    conn.close()

    run_accuracy(settings)

    for table, rows_per_week in [("prediction_accuracy", 12), ("team_accuracy", 5)]:
        counts = read_rows(settings, f"SELECT week, COUNT(*) AS n FROM {table} GROUP BY week ORDER BY week")
        assert counts == [{"week": 9, "n": rows_per_week}, {"week": 10, "n": rows_per_week}]


def test_charts_show_the_error_by_position_and_the_team_totals(settings):
    result = run_accuracy(settings, no_charts=False)

    assert result.charts == ["accuracy_mae_week_10.png", "accuracy_teams_week_10.png"]
    for name in result.charts:
        assert (settings.images_dir / name).stat().st_size > 0
