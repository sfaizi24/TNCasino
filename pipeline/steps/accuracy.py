"""Accuracy step: score the previous week's projections, team totals and moneylines against what happened.

Every source, and the consensus the model used, is scored by position on the players the consensus projected for at
least MIN_CONSENSUS_POINTS. Each team's projected total is scored against its result, its simulated 10th to 90th
percentile range and its moneyline, all from the week's latest run without locked players: a rerun after the week's
first games fixes those players at their real points, which would flatter the model. A team without a curve or a
moneyline keeps those columns empty.
"""

import sqlite3
from collections import defaultdict

import pandas as pd

from pipeline import accuracy_charts
from pipeline.runner import StepContext, StepResult, print_table, timestamp, utc_now
from pipeline.steps.league import insert_rows

NAME = "accuracy"

CONSENSUS = "consensus"
ALL = "ALL"
POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF", ALL]
# Players the consensus barely projects are mostly benchwarmers who score 0, which would flatter every source.
MIN_CONSENSUS_POINTS = 2.0
MIN_PLAYERS_FOR_CORR = 3
# A source is only named the most accurate at a position where it projected at least this many players.
MIN_PLAYERS_FOR_BEST = 20

ACCURACY_DDL = """
CREATE TABLE IF NOT EXISTS prediction_accuracy (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,             -- the evaluated week
  source TEXT NOT NULL,              -- source_website or 'consensus'
  position TEXT NOT NULL,            -- QB | RB | WR | TE | K | DEF | ALL
  n INTEGER NOT NULL,
  mae REAL NOT NULL,
  bias REAL NOT NULL,
  corr REAL,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (season, week, source, position)
);
CREATE TABLE IF NOT EXISTS team_accuracy (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,             -- the evaluated week
  roster_id INTEGER NOT NULL,
  owner TEXT NOT NULL,
  projected REAL NOT NULL,
  actual REAL NOT NULL,
  p10 REAL,
  p90 REAL,
  covered INTEGER,
  win_prob REAL,
  won INTEGER,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (season, week, roster_id)
);
"""

# A source that lists a player twice, under two spellings of his name, is scored once on the mean of the two.
SOURCE_PROJECTIONS = """
SELECT source_website AS source, sleeper_player_id, AVG(projected_points) AS projected
FROM projections_with_sleeper
WHERE season = :season AND week = :week AND sleeper_player_id IS NOT NULL
GROUP BY source_website, sleeper_player_id
"""

CONSENSUS_PROJECTIONS = """
SELECT sleeper_player_id, position, mu AS projected
FROM player_week_stats
WHERE season = :season AND week = :week
"""

ACTUAL_POINTS = """
SELECT player_id AS sleeper_player_id, pts_ppr AS actual
FROM player_stats
WHERE season = :season AND week = :week
"""

# Each game counted once, from its home side. The league step refreshes ESPN's status along with the stat lines;
# schedules migrated from 2025 carry no status, and their games count as played.
GAMES_NOT_FINAL = """
SELECT COUNT(*) FILTER (WHERE status != 'STATUS_FINAL') AS not_final, COUNT(*) AS games
FROM nfl_schedules
WHERE season = :season AND week = :week AND is_home = 1 AND status IS NOT NULL
"""

TEAM_PROJECTIONS = """
SELECT roster_id, owner, total_mu AS projected
FROM team_projections_summary
WHERE season = :season AND week = :week
ORDER BY roster_id
"""

LINEUP_TOTALS = """
SELECT roster_id, owner, SUM(mu) AS projected
FROM team_lineups
WHERE season = :season AND week = :week
GROUP BY roster_id, owner
ORDER BY roster_id
"""

SCORES = """
SELECT roster_id, matchup_id_number, points
FROM matchups
WHERE league_id = :league_id AND week = :week
"""

GRADED_RUN = """
SELECT run_id
FROM simulation_runs
WHERE season = :season AND week = :week AND n_locked = 0
ORDER BY created_at DESC, run_id DESC
LIMIT 1
"""

WEEK_RUNS = "SELECT COUNT(*) FROM simulation_runs WHERE season = :season AND week = :week"

RUN_CURVES = """
SELECT owner, mean, p10, p90
FROM team_distribution_curves
WHERE run_id = :run_id
"""

RUN_MONEYLINES = """
SELECT team1_id, team1_win_prob, team2_id, team2_win_prob
FROM betting_odds_matchup_ml
WHERE run_id = :run_id
"""


def run(ctx: StepContext) -> StepResult:
    settings = ctx.settings
    week = settings.week - 1
    params = {"season": settings.season, "week": week, "league_id": settings.league_id}
    league_conn = ctx.db("league")
    actuals = pd.read_sql_query(ACTUAL_POINTS, league_conn, params=params)
    if actuals.empty:
        return StepResult({}, warnings=[f"no actuals for week {week} yet"])
    # A player whose game is still to come has no stat line yet and would be scored as if he did not play.
    not_final, games = league_conn.execute(GAMES_NOT_FINAL, params).fetchone()
    if not_final:
        return StepResult({}, warnings=[f"week {week} is not over: {not_final} of {games} games not final"])
    projections_conn = ctx.db("projections")
    sources = pd.read_sql_query(SOURCE_PROJECTIONS, projections_conn, params=params)
    consensus = pd.read_sql_query(CONSENSUS_PROJECTIONS, projections_conn, params=params)
    if sources.empty or consensus.empty:
        return StepResult({}, warnings=[f"no projections for week {week}"])

    accuracy = accuracy_rows(scored_players(sources, consensus, actuals))
    teams, warnings = team_rows(ctx, params)
    save(ctx, week, accuracy, teams)
    summary = summarize(week, accuracy, teams)
    ctx.log(f"week {week}: {summary['n_players']} players and {summary['n_teams']} teams scored")
    print_player_accuracy(accuracy)
    if teams:
        print_team_accuracy(teams)

    written = []
    if not ctx.options.get("no_charts"):
        written.append(accuracy_charts.mae_by_position(settings.images_dir, week, accuracy, POSITION_ORDER))
        if teams:
            written.append(accuracy_charts.team_totals(settings.images_dir, week, teams))
    return StepResult(summary, warnings, written)


def scored_players(sources: pd.DataFrame, consensus: pd.DataFrame, actuals: pd.DataFrame) -> pd.DataFrame:
    """One row per source and eligible player, the consensus counted as a source, with the player's position as
    Sleeper lists it and his actual points: 0 without a stat line, as he did not play."""
    eligible = consensus[consensus["projected"] >= MIN_CONSENSUS_POINTS]
    consensus_rows = eligible[["sleeper_player_id", "projected"]].assign(source=CONSENSUS)
    projected = pd.concat([sources, consensus_rows], ignore_index=True)
    players = projected.merge(eligible[["sleeper_player_id", "position"]], on="sleeper_player_id")
    players = players.merge(actuals, on="sleeper_player_id", how="left")
    players["actual"] = players["actual"].fillna(0.0)
    return players


def accuracy_rows(players: pd.DataFrame) -> list[dict]:
    """n, mae, bias and corr for each source at each position and over all of them."""
    by_position = pd.concat([players, players.assign(position=ALL)], ignore_index=True)
    rows = []
    for (source, position), group in by_position.groupby(["source", "position"]):
        errors = group["projected"] - group["actual"]
        rows.append(
            {
                "source": source,
                "position": position,
                "n": len(group),
                "mae": float(errors.abs().mean()),
                "bias": float(errors.mean()),
                "corr": correlation(group["projected"], group["actual"]),
            }
        )
    return rows


def correlation(projected: pd.Series, actual: pd.Series) -> float | None:
    """Pearson's r, or None for too few players or when either side does not vary."""
    if len(projected) < MIN_PLAYERS_FOR_CORR or projected.nunique() < 2 or actual.nunique() < 2:
        return None
    return float(projected.corr(actual))


def team_rows(ctx: StepContext, params: dict) -> tuple[list[dict], list[str]]:
    """Each team's projected total against its score, its simulated 10th to 90th percentile range and its moneyline,
    from the graded run for the week."""
    week = params["week"]
    projections_conn = ctx.db("projections")
    teams = read_if_present(projections_conn, "team_projections_summary", TEAM_PROJECTIONS, params)
    if not teams:
        teams = read_if_present(projections_conn, "team_lineups", LINEUP_TOTALS, params)
    if not teams:
        return [], [f"no lineups for week {week}; teams not scored"]

    scores = ctx.db("league").execute(SCORES, params).fetchall()
    points = {score["roster_id"]: score["points"] for score in scores}
    results = game_results(scores)
    odds_conn = ctx.db("odds")
    run_id, run_warnings = graded_run(odds_conn, params)
    graded = {"run_id": run_id}
    curves = read_if_present(odds_conn, "team_distribution_curves", RUN_CURVES, graded)
    ranges = {curve["owner"]: (curve["p10"], curve["p90"]) for curve in curves}
    # Projected from the graded run: the summary is overwritten by every run, and a rerun's counts real points.
    means = {curve["owner"]: curve["mean"] for curve in curves}
    moneylines = read_if_present(odds_conn, "betting_odds_matchup_ml", RUN_MONEYLINES, graded)
    win_probabilities = moneyline_probabilities(moneylines)

    rows = []
    for team in teams:
        actual = points[team["roster_id"]]
        p10, p90 = ranges.get(team["owner"], (None, None))
        rows.append(
            {
                "roster_id": team["roster_id"],
                "owner": team["owner"],
                "projected": means.get(team["owner"], team["projected"]),
                "actual": actual,
                "p10": p10,
                "p90": p90,
                "covered": None if p10 is None else int(p10 <= actual <= p90),
                "win_prob": win_probabilities.get(team["roster_id"]),
                "won": results.get(team["roster_id"]),
            }
        )
    return rows, run_warnings or missing_odds_warnings(rows, week)


def graded_run(conn: sqlite3.Connection, params: dict) -> tuple[str | None, list[str]]:
    """The week's latest run without locked players, or None, with a warning when every run of the week has some."""
    if not table_exists(conn, "simulation_runs"):
        return None, []
    row = conn.execute(GRADED_RUN, params).fetchone()
    if row is not None:
        return row["run_id"], []
    (n_runs,) = conn.execute(WEEK_RUNS, params).fetchone()
    if n_runs:
        week = params["week"]
        return None, [f"week {week} has no run without locked players; team ranges and moneylines not scored"]
    return None, []


def read_if_present(conn: sqlite3.Connection, table: str, query: str, params: dict) -> list[sqlite3.Row]:
    """The query's rows, or none when the step that creates the table has not run in this data dir."""
    if not table_exists(conn, table):
        return []
    return conn.execute(query, params).fetchall()


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    return exists is not None


def game_results(scores: list[sqlite3.Row]) -> dict[int, int]:
    """1 for a win and 0 for a loss by roster_id; a tie, or a week without an opponent, has no entry."""
    games = defaultdict(list)
    for score in scores:
        if score["matchup_id_number"] is not None:
            games[score["matchup_id_number"]].append(score)
    results = {}
    for game in games.values():
        if len(game) != 2 or game[0]["points"] == game[1]["points"]:
            continue
        first, second = game
        results[first["roster_id"]] = int(first["points"] > second["points"])
        results[second["roster_id"]] = int(second["points"] > first["points"])
    return results


def moneyline_probabilities(moneylines: list[sqlite3.Row]) -> dict[int, float]:
    probabilities = {}
    for line in moneylines:
        probabilities[line["team1_id"]] = line["team1_win_prob"]
        probabilities[line["team2_id"]] = line["team2_win_prob"]
    return probabilities


def missing_odds_warnings(teams: list[dict], week: int) -> list[str]:
    warnings = []
    without_range = sum(team["p10"] is None for team in teams)
    if without_range:
        warnings.append(
            f"{without_range} of {len(teams)} teams have no score distribution for week {week}; coverage left empty"
        )
    without_moneyline = sum(team["win_prob"] is None for team in teams)
    if without_moneyline:
        warnings.append(
            f"{without_moneyline} of {len(teams)} teams have no moneyline for week {week}; win probability left empty"
        )
    return warnings


def save(ctx: StepContext, week: int, accuracy: list[dict], teams: list[dict]) -> None:
    """Replace the evaluated week's rows in both tables."""
    season = ctx.settings.season
    conn = ctx.db("projections")
    conn.executescript(ACCURACY_DDL)
    stamp = {"season": season, "week": week, "computed_at": timestamp(utc_now())}
    for table, rows in [("prediction_accuracy", accuracy), ("team_accuracy", teams)]:
        conn.execute(f"DELETE FROM {table} WHERE season = ? AND week = ?", (season, week))
        insert_rows(conn, table, [stamp | row for row in rows])
    conn.commit()


def print_player_accuracy(accuracy: list[dict]) -> None:
    """Each source's MAE by position, the most accurate overall first, then its players, bias and corr overall."""
    mae = {(row["source"], row["position"]): row["mae"] for row in accuracy}
    overall = sorted((row for row in accuracy if row["position"] == ALL), key=lambda row: row["mae"])
    rows = []
    for row in overall:
        by_position = [cell(mae.get((row["source"], position)), ".2f") for position in POSITION_ORDER]
        rows.append([row["source"], *by_position, str(row["n"]), cell(row["bias"], "+.2f"), cell(row["corr"], ".3f")])
    print_table(["source", *POSITION_ORDER, "players", "bias", "corr"], rows)


def print_team_accuracy(teams: list[dict]) -> None:
    rows = []
    for team in teams:
        rows.append(
            [
                team["owner"],
                cell(team["projected"], ".1f"),
                cell(team["actual"], ".1f"),
                cell(team["p10"], ".1f"),
                cell(team["p90"], ".1f"),
                cell(team["win_prob"], ".3f"),
                cell(team["won"], "d"),
            ]
        )
    print_table(["owner", "projected", "actual", "p10", "p90", "win prob", "won"], rows)


def cell(value: float | None, spec: str) -> str:
    return "-" if value is None else format(value, spec)


def summarize(week: int, accuracy: list[dict], teams: list[dict]) -> dict:
    consensus = next(row for row in accuracy if row["source"] == CONSENSUS and row["position"] == ALL)
    errors = [abs(team["projected"] - team["actual"]) for team in teams]
    covered = [team["covered"] for team in teams if team["covered"] is not None]
    squared_errors = [
        (team["win_prob"] - team["won"]) ** 2
        for team in teams
        if team["win_prob"] is not None and team["won"] is not None
    ]
    return {
        "week_evaluated": week,
        "n_players": consensus["n"],
        "consensus": {
            ALL: {
                "mae": round(consensus["mae"], 2),
                "bias": round(consensus["bias"], 2),
                "corr": None if consensus["corr"] is None else round(consensus["corr"], 3),
            }
        },
        "best_source_by_position": best_sources(accuracy),
        "team_mae": rounded_mean(errors, 2),
        "coverage_80": rounded_mean(covered, 3),
        "moneyline_brier": rounded_mean(squared_errors, 3),
        "n_teams": len(teams),
    }


def best_sources(accuracy: list[dict]) -> dict[str, str]:
    """The lowest-MAE source at each position, among those that projected MIN_PLAYERS_FOR_BEST players there."""
    best = {}
    for position in POSITION_ORDER:
        candidates = [
            row
            for row in accuracy
            if row["position"] == position and row["source"] != CONSENSUS and row["n"] >= MIN_PLAYERS_FOR_BEST
        ]
        if candidates:
            best[position] = min(candidates, key=lambda row: row["mae"])["source"]
    return best


def rounded_mean(values: list[float], digits: int) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), digits)
