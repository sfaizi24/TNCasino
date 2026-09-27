"""Season-to-date calibration of the model as it ran; nothing is refitted.

Every completed week before the current one is scored with what the pipeline stored at the time: the player
distributions in player_week_stats and each week's latest team intervals and moneylines, against what players and
teams actually scored. The metrics are recorded at the current week, so calibration_metrics shows how the
season-to-date numbers moved from week to week.
"""

import pandas as pd

from pipeline.model import calibration_charts
from pipeline.model.evaluate import COVERAGE_BANDS, MIN_MU, brier_score, load_actuals, pit, player_coverage
from pipeline.model.params import load_params
from pipeline.runner import StepContext, StepResult, print_table, timestamp, utc_now
from pipeline.steps.publish import keep_latest_run
from pipeline.steps.stats import POSITION_ORDER

NAME = "calibrate"

CALIBRATION_DDL = """
CREATE TABLE IF NOT EXISTS calibration_metrics (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  model_version TEXT NOT NULL,
  -- by position: player_coverage_50, player_coverage_80, player_coverage_95, player_zero_share, n_player_rows;
  -- at ALL only: team_coverage_80, n_team_weeks, moneyline_brier, n_matchups
  metric TEXT NOT NULL,
  position TEXT NOT NULL,
  value REAL NOT NULL,
  computed_at TEXT NOT NULL,
  PRIMARY KEY (season, week, model_version, metric, position)
);
"""

STORED_PLAYERS = """
SELECT week, sleeper_player_id, position, mu, sigma, model_version
FROM player_week_stats
WHERE season = ? AND week < ? AND mu >= ?
"""
STORED_CURVES = "SELECT run_id, week, owner, p10, p90, created_at FROM team_distribution_curves WHERE season = ?"
STORED_MONEYLINES = """
SELECT run_id, week, team1_id, team2_id, team1_win_prob, created_at
FROM betting_odds_matchup_ml
WHERE season = ?
"""
ROSTER_OWNERS = "SELECT DISTINCT week, roster_id, owner FROM team_lineups WHERE season = ?"
TEAM_POINTS = "SELECT week, roster_id, points FROM matchups WHERE league_id = ?"


def run(ctx: StepContext) -> StepResult:
    settings = ctx.settings
    weeks = completed_weeks(ctx)
    if not weeks:
        return StepResult(summary={}, warnings=["no completed weeks with actuals yet"])

    players = score_players(ctx, weeks)
    versions = ", ".join(sorted(players["model_version"].unique()))
    ctx.log(f"weeks {weeks}: distributions stored by {versions}, recorded under {settings.model_version}")
    coverage = player_coverage(players)
    print_coverage(coverage)

    points = pd.read_sql_query(TEAM_POINTS, ctx.db("league"), params=(settings.league_id,))
    teams = score_teams(ctx, weeks, points)
    games = score_moneylines(ctx, weeks, points)
    metrics = {**player_metrics(coverage), **team_metrics(teams), **moneyline_metrics(games)}
    save_metrics(ctx, metrics)

    weeks_without_curves = sorted(set(weeks) - set(teams["week"]))
    summary = summarise(settings.model_version, weeks, weeks_without_curves, metrics)
    ctx.log(f"team [p10, p90] coverage {summary['team_coverage_80']} over {summary['n_team_weeks']} team-weeks")
    ctx.log(f"weeks without team curves: {weeks_without_curves or 'none'}")
    ctx.log(f"moneyline Brier {summary['moneyline_brier']} over {summary['n_matchups']} games")

    written = []
    if not ctx.options.get("no_charts"):
        chart = calibration_charts.coverage_chart(
            settings.images_dir, settings.week, summary["player_coverage_80"], summary["team_coverage_80"]
        )
        written.append(chart)
    return StepResult(summary=summary, charts=written)


def completed_weeks(ctx: StepContext) -> list[int]:
    """Weeks before the current one with both stored player distributions and actual player scores."""
    season, week = ctx.settings.season, ctx.settings.week
    modelled = ctx.db("projections").execute(
        "SELECT DISTINCT week FROM player_week_stats WHERE season = ? AND week < ?", (season, week)
    )
    played = ctx.db("league").execute(
        "SELECT DISTINCT week FROM player_stats WHERE season = ? AND week < ?", (season, week)
    )
    return sorted({row["week"] for row in modelled} & {row["week"] for row in played})


def score_players(ctx: StepContext, weeks: list[int]) -> pd.DataFrame:
    """The eligible stored distributions of `weeks` beside the actual points, with each row's PIT interval taken
    under the dud block of the model version that stored it."""
    settings = ctx.settings
    stored = pd.read_sql_query(STORED_PLAYERS, ctx.db("projections"), params=(settings.season, settings.week, MIN_MU))
    stored = stored[stored["week"].isin(weeks) & stored["position"].isin(POSITION_ORDER)]
    actuals = load_actuals(ctx.db("league"), settings.season)
    players = stored.merge(actuals, on=["sleeper_player_id", "week"], how="left")
    # A player without a stats row did not play and scored 0.
    players["actual"] = players["actual"].fillna(0.0)

    scored = []
    for version, group in players.groupby("model_version"):
        u_low, u_high = pit(group, load_params(version)["dud"])
        scored.append(group.assign(u_low=u_low, u_high=u_high))
    return pd.concat(scored, ignore_index=True)


def score_teams(ctx: StepContext, weeks: list[int], points: pd.DataFrame) -> pd.DataFrame:
    """Each team-week's latest [p10, p90] beside the points the team scored. Curves name teams by owner, so the
    week's lineups map each owner to its roster."""
    settings = ctx.settings
    curves = pd.read_sql_query(STORED_CURVES, ctx.db("odds"), params=(settings.season,))
    curves = keep_latest_run(curves, ctx.db("odds"))
    owners = pd.read_sql_query(ROSTER_OWNERS, ctx.db("projections"), params=(settings.season,))
    teams = curves[curves["week"].isin(weeks)].merge(owners, on=["week", "owner"])
    return teams.merge(points, on=["week", "roster_id"])


def score_moneylines(ctx: StepContext, weeks: list[int], points: pd.DataFrame) -> pd.DataFrame:
    """Each game's latest chance that team1 wins beside both teams' points."""
    lines = pd.read_sql_query(STORED_MONEYLINES, ctx.db("odds"), params=(ctx.settings.season,))
    lines = keep_latest_run(lines, ctx.db("odds"))
    team1 = points.rename(columns={"roster_id": "team1_id", "points": "team1_points"})
    team2 = points.rename(columns={"roster_id": "team2_id", "points": "team2_points"})
    games = lines[lines["week"].isin(weeks)].merge(team1, on=["week", "team1_id"])
    return games.merge(team2, on=["week", "team2_id"])


def player_metrics(coverage: dict[str, dict]) -> dict[tuple[str, str], float]:
    metrics = {}
    for position, values in coverage.items():
        for level in COVERAGE_BANDS:
            metrics[(f"player_coverage_{level}", position)] = values[level]
        metrics[("player_zero_share", position)] = values["zero_share"]
        metrics[("n_player_rows", position)] = values["n"]
    return metrics


def team_metrics(teams: pd.DataFrame) -> dict[tuple[str, str], float]:
    metrics = {("n_team_weeks", "ALL"): len(teams)}
    if not teams.empty:
        inside = teams["points"].between(teams["p10"], teams["p90"])
        metrics[("team_coverage_80", "ALL")] = float(inside.mean())
    return metrics


def moneyline_metrics(games: pd.DataFrame) -> dict[tuple[str, str], float]:
    metrics = {("n_matchups", "ALL"): len(games)}
    if not games.empty:
        brier = brier_score(games["team1_win_prob"], games["team1_points"], games["team2_points"])
        metrics[("moneyline_brier", "ALL")] = brier
    return metrics


def print_coverage(coverage: dict[str, dict]) -> None:
    rows = []
    for position, values in coverage.items():
        bands = [f"{values[level]:.3f}" for level in COVERAGE_BANDS]
        rows.append([position, str(values["n"]), f"{values['zero_share']:.3f}", *bands])
    print_table(["position", "n", "zeros", *(f"{level}%" for level in COVERAGE_BANDS)], rows)


def save_metrics(ctx: StepContext, metrics: dict[tuple[str, str], float]) -> None:
    """Replace this week's rows for the model version, so rerunning the week is idempotent."""
    settings = ctx.settings
    computed_at = timestamp(utc_now())
    rows = [
        (settings.season, settings.week, settings.model_version, metric, position, float(value), computed_at)
        for (metric, position), value in metrics.items()
    ]
    conn = ctx.db("odds")
    conn.executescript(CALIBRATION_DDL)
    with conn:
        conn.execute(
            "DELETE FROM calibration_metrics WHERE season = ? AND week = ? AND model_version = ?",
            (settings.season, settings.week, settings.model_version),
        )
        conn.executemany("INSERT INTO calibration_metrics VALUES (?, ?, ?, ?, ?, ?, ?)", rows)


def summarise(
    model_version: str, weeks: list[int], weeks_without_curves: list[int], metrics: dict[tuple[str, str], float]
) -> dict:
    player_coverage_80 = {
        position: round(value, 4) for (metric, position), value in metrics.items() if metric == "player_coverage_80"
    }
    team_coverage = metrics.get(("team_coverage_80", "ALL"))
    brier = metrics.get(("moneyline_brier", "ALL"))
    return {
        "model_version": model_version,
        "weeks_evaluated": weeks,
        "weeks_without_curves": weeks_without_curves,
        "player_coverage_80": player_coverage_80,
        "team_coverage_80": None if team_coverage is None else round(team_coverage, 4),
        "moneyline_brier": None if brier is None else round(brier, 4),
        "n_player_rows": metrics[("n_player_rows", "ALL")],
        "n_team_weeks": metrics[("n_team_weeks", "ALL")],
        "n_matchups": metrics[("n_matchups", "ALL")],
    }
