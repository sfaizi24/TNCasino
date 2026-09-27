"""Score model parameters against what players and teams actually scored.

A player's score is judged by its probability integral transform (PIT), the quantile of the actual points under
the player's predicted distribution: when the model is calibrated, 80% of actuals land inside the central 80%
interval. Team totals are simulated from the week's lineups and judged by how often the actual total lands inside
[p10, p90], and moneylines by their Brier score. Pooled over held-out weeks, these metrics are the gate a fitted
parameter version must pass, reported beside the frozen v1 baseline, whose moneylines are compared with the fit's
game by game.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing

import numpy as np
import pandas as pd
from scipy.special import ndtr

from pipeline.db import connect
from pipeline.model.params import load_params
from pipeline.model.sampling import dud_probabilities, lognormal_means, lognormal_params, simulate_teams
from pipeline.model.sigma import sigma
from pipeline.settings import Settings
from pipeline.steps.simulate import SLOT_RANK
from pipeline.steps.stats import DEFAULT_SOURCE, POSITION_ORDER

PLAYER_WEEK = ["season", "week", "sleeper_player_id"]
MIN_MU = 2.0
COVERAGE_BANDS = {50: (0.25, 0.75), 80: (0.10, 0.90), 95: (0.025, 0.975)}
GATED_POSITIONS = ["QB", "RB", "WR", "TE"]
PLAYER_GATE = (0.70, 0.90)
TEAM_GATE = (0.72, 0.88)
TEAM_SIMS = 20_000

ACTUALS = "SELECT player_id AS sleeper_player_id, week, pts_ppr AS actual FROM player_stats WHERE season = ?"

# Lineups migrated from 2025 carry no Sleeper ids, so a starter is found by name and position among the players
# the stats step described that week.
LINEUPS = """
SELECT l.season, l.week, l.roster_id, l.slot, l.position, l.mu AS legacy_mu,
       COALESCE(l.sleeper_player_id, s.sleeper_player_id) AS sleeper_player_id
FROM team_lineups l
LEFT JOIN player_week_stats s
  ON l.sleeper_player_id IS NULL AND s.season = l.season AND s.week = l.week
 AND s.player_name = l.player_name AND s.position = l.position
WHERE l.season = ?
"""

MATCHUPS = "SELECT week, roster_id, matchup_id_number, points FROM matchups WHERE league_id = ?"


def load_actuals(league: sqlite3.Connection, season: int) -> pd.DataFrame:
    """PPR points by player and week; a player without a row did not play and scored 0."""
    return pd.read_sql_query(ACTUALS, league, params=(season,))


def eligible_rows(rows: pd.DataFrame) -> pd.DataFrame:
    """Source rows of player-weeks at a scoring position whose sources average at least MIN_MU, so deep-bench
    players nobody starts do not dominate a fit or a score."""
    rows = rows[rows["position"].isin(POSITION_ORDER)]
    mean_projection = rows.groupby(PLAYER_WEEK)["projected_points"].transform("mean")
    return rows[mean_projection >= MIN_MU]


def player_weeks(rows: pd.DataFrame, sources: dict) -> pd.DataFrame:
    """One row per player-week with mu, spread and n_sources as the stats step computes them from the source rows."""
    weights = {source: values["weight"] for source, values in sources.items()}
    biases = {source: values["bias"] for source, values in sources.items()}
    weight = rows["source_website"].map(weights).fillna(DEFAULT_SOURCE["weight"])
    points = rows["projected_points"] - rows["source_website"].map(biases).fillna(DEFAULT_SOURCE["bias"])
    frame = rows.assign(points=points, weight=weight, weighted=points * weight)
    players = (
        frame.groupby(PLAYER_WEEK)
        .agg(
            position=("position", "first"),
            nfl_team=("nfl_team", "first"),
            actual=("actual", "first"),
            n_sources=("points", "size"),
            spread=("points", "std"),
            weighted=("weighted", "sum"),
            weight=("weight", "sum"),
        )
        .reset_index()
    )
    players["mu"] = players["weighted"] / players["weight"]
    players["spread"] = players["spread"].fillna(0.0)
    return players.drop(columns=["weighted", "weight"])


def player_sigmas(players: pd.DataFrame, params: dict) -> list[float]:
    columns = zip(players["mu"], players["position"], players["spread"], strict=True)
    return [sigma(mu, position, spread, params) for mu, position, spread in columns]


def pit(players: pd.DataFrame, dud: dict | None) -> tuple[np.ndarray, np.ndarray]:
    """Each actual's quantile under the player's score distribution, the lognormal mixed with a uniform dud on
    [0, t * mu] when `dud` is set: u = p * min(x / (t * mu), 1) + (1 - p) * F_lognormal(x), returned as the interval
    (u_low, u_high), a single point for a score above 0.

    A score of 0 or less counts as a dud, whose PIT is uniform on [0, p], so its interval is the whole dud component
    [0, p]: the non-randomized PIT of a point mass (Czado, Gneiting and Held 2009, "Predictive model assessment for
    count data"). Without a dud chance, under v1 or for a defense, that is [0, 0].
    """
    mu = players["mu"].to_numpy(dtype=float)
    actual = players["actual"].to_numpy(dtype=float).clip(min=0.0)
    p_dud = dud_probabilities(players, dud)
    threshold = dud["threshold_ratio"] if dud else 0.0
    lognormal_mu = lognormal_means(mu, p_dud, threshold)
    columns = zip(lognormal_mu, players["sigma"], strict=True)
    mu_ln, sigma_ln = np.array([lognormal_params(mean, sd) for mean, sd in columns]).T
    with np.errstate(divide="ignore"):
        u = ndtr((np.log(actual) - mu_ln) / sigma_ln)
    if dud is not None:
        dud_cdf = np.minimum(actual / (threshold * mu), 1.0)
        u = p_dud * dud_cdf + (1 - p_dud) * u
    scored_nothing = actual == 0
    return np.where(scored_nothing, 0.0, u), np.where(scored_nothing, p_dud, u)


def player_coverage(players: pd.DataFrame) -> dict[str, dict]:
    """Row count, share of actuals of 0 or less, and mean share of the PIT intervals [u_low, u_high] inside each
    central band, by position in canonical order, then ALL."""
    groups = {position: players[players["position"] == position] for position in POSITION_ORDER}
    groups["ALL"] = players
    coverage = {}
    for name, group in groups.items():
        if group.empty:
            continue
        coverage[name] = {"n": len(group), "zero_share": float((group["actual"] <= 0).mean())}
        u_low, u_high = group["u_low"].to_numpy(), group["u_high"].to_numpy()
        for level, (low, high) in COVERAGE_BANDS.items():
            coverage[name][level] = float(band_coverage(u_low, u_high, low, high).mean())
    return coverage


def band_coverage(u_low: np.ndarray, u_high: np.ndarray, low: float, high: float) -> np.ndarray:
    """How much of each PIT interval lies inside [low, high]: the overlapping share of an interval, and 1 or 0 for
    a point inside or outside."""
    width = u_high - u_low
    overlap = np.maximum(np.minimum(u_high, high) - np.maximum(u_low, low), 0.0)
    point_inside = (low <= u_low) & (u_low <= high)
    # A point has no width, and its 0 / 0 is replaced by whether it is inside.
    with np.errstate(invalid="ignore"):
        return np.where(width > 0, overlap / width, point_inside)


def brier_errors(games: pd.DataFrame) -> pd.Series:
    """Each game's squared error of the chance that the first team wins against the result, a tie counting as half
    a win. The mean over games is the Brier score."""
    first, second = games["first_points"], games["second_points"]
    first_won = (first > second) + 0.5 * (first == second)
    return (games["p_first"] - first_won) ** 2


def brier_difference(games: pd.DataFrame, baseline_games: pd.DataFrame) -> tuple[float, float]:
    """The mean, game by game, of how much worse the forecasts in `games` did than the baseline's forecasts of the
    same games, and its standard error."""
    assert len(games) == len(baseline_games), "both versions must be scored on the same games"
    differences = brier_errors(games) - brier_errors(baseline_games)
    if len(differences) < 2:
        # One game has no spread to measure; a standard error of 0 makes any difference count.
        return float(differences.mean()), 0.0
    return float(differences.mean()), float(differences.std(ddof=1) / np.sqrt(len(differences)))


def load_team_weeks(settings: Settings, season: int, weeks: list[int]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The starting lineups and matchup results of `weeks`. Starters carry their Sleeper id and NFL team where
    known and are ordered by roster, then slot, as the simulate step orders them."""
    with closing(connect(settings, "projections")) as conn:
        lineups = pd.read_sql_query(LINEUPS, conn, params=(season,))
    with closing(connect(settings, "league")) as conn:
        teams = pd.read_sql_query("SELECT player_id AS sleeper_player_id, team AS nfl_team FROM nfl_players", conn)
        matchups = pd.read_sql_query(MATCHUPS, conn, params=(settings.league_id,))

    # Migrated week 10 still holds two retired slots, "RB" and "WR", beside the canonical nine.
    lineups = lineups[lineups["week"].isin(weeks) & lineups["slot"].isin(list(SLOT_RANK))]
    matchups = matchups[matchups["week"].isin(weeks)].assign(season=season)
    missing = sorted(set(weeks) - (set(lineups["week"]) & set(matchups["week"])))
    if missing:
        raise LookupError(
            f"season {season} weeks {missing} need both team_lineups and league {settings.league_id} matchups"
        )

    lineups = lineups.merge(teams, on="sleeper_player_id", how="left")
    lineups = lineups.assign(slot_rank=lineups["slot"].map(SLOT_RANK))
    return lineups.sort_values(["week", "roster_id", "slot_rank"], ignore_index=True), matchups


def leave_one_week_out(rows: pd.DataFrame) -> Iterator[tuple[tuple[int, int], pd.DataFrame, pd.DataFrame]]:
    """Each (season, week) of the rows in order, with every other week's rows to train on and its own to test."""
    weeks = sorted({(int(season), int(week)) for season, week in zip(rows["season"], rows["week"], strict=True)})
    for season, week in weeks:
        held_out = (rows["season"] == season) & (rows["week"] == week)
        yield (season, week), rows[~held_out], rows[held_out]


def evaluate_gate(
    rows: pd.DataFrame, team_weeks: tuple[pd.DataFrame, pd.DataFrame], folds: dict[tuple[int, int], dict], seed: int
) -> dict:
    """Held-out metrics of the fitted folds beside v1's on the same weeks, how much the fit's moneyline Brier score
    differs from v1's game by game, with the standard error of that difference, and whether the fit passes the gate.

    `rows` are the training rows, `team_weeks` what load_team_weeks returns for them, and `folds` maps each
    (season, week) of the rows to the parameters fitted without that week.
    """
    fitted, games = held_out_metrics(rows, team_weeks, folds, seed)
    v1 = load_params("v1")
    baseline, baseline_games = held_out_metrics(rows, team_weeks, dict.fromkeys(folds, v1), seed)
    delta, se = brier_difference(games, baseline_games)
    gate = {**fitted, "moneyline_brier_delta": round(delta, 4), "moneyline_brier_delta_se": round(se, 4)}
    return {"passed": not gate_failures(gate, baseline), **gate, "v1": baseline}


def held_out_metrics(
    rows: pd.DataFrame, team_weeks: tuple[pd.DataFrame, pd.DataFrame], folds: dict[tuple[int, int], dict], seed: int
) -> tuple[dict, pd.DataFrame]:
    """The metrics pooled over the held-out weeks, and the held-out games in fold order, so that two versions'
    forecasts of the same games can be paired."""
    lineups, matchups = team_weeks
    players, teams, games = [], [], []
    for (season, week), _, test in leave_one_week_out(rows):
        params = folds[(season, week)]
        players.append(score_players(player_weeks(eligible_rows(test), params["sources"]), params))

        week_lineups = lineups[(lineups["season"] == season) & (lineups["week"] == week)]
        week_matchups = matchups[(matchups["season"] == season) & (matchups["week"] == week)]
        starters = recompute_starters(week_lineups, player_weeks(test, params["sources"]), params)
        week_teams, week_games = simulate_week(starters, week_matchups, params, seed)
        teams.append(week_teams)
        games.append(week_games)
    players = pd.concat(players, ignore_index=True)
    teams = pd.concat(teams, ignore_index=True)
    games = pd.concat(games, ignore_index=True)
    return pooled_metrics(players, teams, games), games


def score_players(players: pd.DataFrame, params: dict) -> pd.DataFrame:
    players = players.assign(sigma=player_sigmas(players, params))
    u_low, u_high = pit(players, params["dud"])
    return players.assign(u_low=u_low, u_high=u_high)


def recompute_starters(lineups: pd.DataFrame, consensus: pd.DataFrame, params: dict) -> pd.DataFrame:
    """The week's starters with mu and sigma recomputed from their projections under `params`. A starter without
    projections, such as a waiver pickup, keeps the mu its lineup was built with and gets a single source's sigma."""
    starters = lineups.merge(consensus[["sleeper_player_id", "mu", "spread"]], on="sleeper_player_id", how="left")
    starters["mu"] = starters["mu"].fillna(starters["legacy_mu"])
    starters["spread"] = starters["spread"].fillna(0.0)
    starters["sigma"] = player_sigmas(starters, params)
    return starters


def simulate_week(
    starters: pd.DataFrame, matchups: pd.DataFrame, params: dict, seed: int
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Each roster's simulated mean and [p10, p90] beside its actual points, and each game's chance that the lower
    roster id wins beside both teams' points."""
    totals, roster_ids = simulate_teams(starters, params, TEAM_SIMS, seed)
    draws = pd.DataFrame(totals.astype(np.float64), columns=roster_ids)
    points = matchups.set_index("roster_id")["points"]
    teams = pd.DataFrame(
        {
            "roster_id": roster_ids,
            "mean": draws.mean().to_numpy(),
            "p10": np.percentile(draws, 10, axis=0),
            "p90": np.percentile(draws, 90, axis=0),
            "points": points.reindex(roster_ids).to_numpy(),
        }
    )
    games = []
    for _, pair in matchups.groupby("matchup_id_number"):
        first, second = sorted(pair["roster_id"])
        games.append(
            {
                "p_first": float((draws[first] > draws[second]).mean()),
                "first_points": points[first],
                "second_points": points[second],
            }
        )
    return teams, pd.DataFrame(games)


def pooled_metrics(players: pd.DataFrame, teams: pd.DataFrame, games: pd.DataFrame) -> dict:
    coverage = player_coverage(players)
    inside = teams["points"].between(teams["p10"], teams["p90"])
    game_errors = brier_errors(games)
    return {
        "player_coverage_80": {name: round(values[80], 4) for name, values in coverage.items()},
        "player_zero_share": {name: round(values["zero_share"], 4) for name, values in coverage.items()},
        "team_coverage_80": round(float(inside.mean()), 4),
        "team_mae": round(float((teams["mean"] - teams["points"]).abs().mean()), 2),
        "moneyline_brier": round(float(game_errors.mean()), 4),
        "player_coverage_50": {name: round(values[50], 4) for name, values in coverage.items()},
        "player_coverage_95": {name: round(values[95], 4) for name, values in coverage.items()},
        "n_player_rows": {name: values["n"] for name, values in coverage.items()},
        "n_team_weeks": len(teams),
        "n_matchups": len(game_errors),
    }


def gate_failures(fitted: dict, baseline: dict) -> list[str]:
    """Why the fitted metrics miss the gate; empty when they pass.

    Over a few dozen games two versions' Brier scores differ by chance alone, so the fit fails on the moneyline only
    when it does worse than v1 by more than two standard errors of the game-by-game difference.
    """
    failures = []
    low, high = PLAYER_GATE
    for position in GATED_POSITIONS:
        coverage = fitted["player_coverage_80"][position]
        if not low <= coverage <= high:
            failures.append(f"{position} 80% coverage {coverage:.3f} is outside [{low:.2f}, {high:.2f}]")
    low, high = TEAM_GATE
    if not low <= fitted["team_coverage_80"] <= high:
        failures.append(f"team 80% coverage {fitted['team_coverage_80']:.3f} is outside [{low:.2f}, {high:.2f}]")
    delta, se = fitted["moneyline_brier_delta"], fitted["moneyline_brier_delta_se"]
    if delta > 2 * se:
        failures.append(
            f"moneyline Brier {fitted['moneyline_brier']:.4f} is above v1's {baseline['moneyline_brier']:.4f} "
            f"by more than two standard errors ({delta:+.4f}, se {se:.4f})"
        )
    return failures
