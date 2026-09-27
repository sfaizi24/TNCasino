"""Season-to-date calibration of the model as it ran; nothing is refitted.

A week is scored once the accuracy step has graded it, which it does only after every game of the week is final.
Each player distribution stored in player_week_stats is judged against the points the player scored, and each team's
[p10, p90] and moneyline by the accuracy step's grades in team_accuracy, taken from the week's latest odds run. The
metrics are recorded at the current week, so calibration_metrics shows how the season-to-date numbers moved from
week to week.
"""

import pandas as pd

from pipeline.model import calibration_charts
from pipeline.model.evaluate import COVERAGE_BANDS, MIN_MU, load_actuals, pit, player_coverage
from pipeline.model.params import load_params
from pipeline.runner import StepContext, StepResult, print_table, timestamp, utc_now
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
GRADED_WEEKS = """
SELECT DISTINCT week
FROM prediction_accuracy
WHERE season = ? AND week < ? AND week IN (SELECT week FROM player_week_stats WHERE season = ?)
ORDER BY week
"""
TEAM_GRADES = "SELECT week, covered, win_prob, won FROM team_accuracy WHERE season = ? AND week < ?"


def run(ctx: StepContext) -> StepResult:
    settings = ctx.settings
    weeks = completed_weeks(ctx)
    if not weeks:
        return StepResult(summary={}, warnings=["no week graded by the accuracy step yet"])

    players = score_players(ctx, weeks)
    versions = ", ".join(sorted(players["model_version"].unique()))
    ctx.log(f"weeks {weeks}: distributions stored by {versions}, recorded under {settings.model_version}")
    coverage = player_coverage(players)
    print_coverage(coverage)

    teams = team_grades(ctx, weeks)
    metrics = {**player_metrics(coverage), **team_metrics(teams), **moneyline_metrics(teams)}
    save_metrics(ctx, metrics)

    weeks_without_curves = sorted(set(weeks) - set(teams.loc[teams["covered"].notna(), "week"]))
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
    """Weeks before the current one that the accuracy step has graded and whose player distributions are stored."""
    conn = ctx.db("projections")
    tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    # The accuracy step creates its tables the first time it grades a week.
    if "prediction_accuracy" not in tables:
        return []
    season, week = ctx.settings.season, ctx.settings.week
    return [row["week"] for row in conn.execute(GRADED_WEEKS, (season, week, season))]


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


def team_grades(ctx: StepContext, weeks: list[int]) -> pd.DataFrame:
    """The accuracy step's grade of each team-week in `weeks`: covered is null where the week had no team curve,
    win_prob where it had no moneyline, and won after a tie or a bye."""
    settings = ctx.settings
    teams = pd.read_sql_query(TEAM_GRADES, ctx.db("projections"), params=(settings.season, settings.week))
    return teams[teams["week"].isin(weeks)]


def player_metrics(coverage: dict[str, dict]) -> dict[tuple[str, str], float]:
    metrics = {}
    for position, values in coverage.items():
        for level in COVERAGE_BANDS:
            metrics[(f"player_coverage_{level}", position)] = values[level]
        metrics[("player_zero_share", position)] = values["zero_share"]
        metrics[("n_player_rows", position)] = values["n"]
    return metrics


def team_metrics(teams: pd.DataFrame) -> dict[tuple[str, str], float]:
    covered = teams["covered"].dropna()
    metrics = {("n_team_weeks", "ALL"): len(covered)}
    if not covered.empty:
        metrics[("team_coverage_80", "ALL")] = float(covered.mean())
    return metrics


def moneyline_metrics(teams: pd.DataFrame) -> dict[tuple[str, str], float]:
    """The Brier score over team-weeks with both a win chance and a result. The two sides of a game carry the same
    squared error, so it is also the mean over games."""
    graded = teams.dropna(subset=["win_prob", "won"])
    # The accuracy step writes results and win chances for both sides of a game or for neither, so the count is even.
    metrics = {("n_matchups", "ALL"): len(graded) // 2}
    if not graded.empty:
        metrics[("moneyline_brier", "ALL")] = float(((graded["win_prob"] - graded["won"]) ** 2).mean())
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
