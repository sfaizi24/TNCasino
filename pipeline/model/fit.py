"""Fit model parameters from a past season's projections and the points the players actually scored.

The fit runs in stages, each built on the one before: source weights and biases make the consensus mu, the
consensus sets the dud probability, the dud probability sets the lognormal mean that sigma is measured around,
and sigma standardises the residuals whose teammate correlations finish the fit. fit_and_write then scores the
fit with each week held out in turn, beside v1, and writes it where load_params finds it.
"""

import json
from contextlib import closing

import numpy as np
import pandas as pd
from scipy.optimize import minimize
from scipy.special import expit, log_expit, logit

from pipeline.db import connect
from pipeline.model import params as model_params
from pipeline.model.evaluate import (
    eligible_rows,
    evaluate_gate,
    gate_failures,
    leave_one_week_out,
    load_actuals,
    load_team_weeks,
    player_sigmas,
    player_weeks,
)
from pipeline.model.sampling import dud_probabilities, lognormal_means, teammate_matrix
from pipeline.runner import print_table, timestamp, utc_now
from pipeline.settings import Settings
from pipeline.steps.stats import DEFAULT_SOURCE, POSITION_ORDER

MIN_SOURCE_WEEKS = 3
WEIGHT_RANGE = (0.25, 4.0)
DUD_THRESHOLD = 0.25
MIN_DUD_ROWS = 50
MIN_DUDS = 5
BIN_SIZE = 50
MIN_TRAILING_BIN = 25
MIN_SIGMA_ROWS = 100
PAIR_TYPES = ["QB-WR", "QB-TE", "QB-RB", "RB-WR"]
CORRELATION_RANGE = (-0.1, 0.4)
MIN_PAIRS = 100
# The most starters one NFL team can plausibly place across the league's lineups in a week.
LARGEST_TEAM_GROUP = ["QB"] + ["RB"] * 3 + ["WR"] * 5 + ["TE"] * 2
MIN_EIGENVALUE = 0.05
SCALE_STEPS = 20

PLAYERS = "SELECT player_id AS sleeper_player_id, position, team AS nfl_team FROM nfl_players"

PROJECTIONS = """
SELECT source_website, season, week, sleeper_player_id, projected_points
FROM projections_with_sleeper
WHERE season = ? AND sleeper_player_id IS NOT NULL
"""


def fit_and_write(settings: Settings, season: int, weeks: list[int], out: str, excluded_sources: list[str]) -> dict:
    """Fit version `out` on the season's weeks, score it against v1 with each week held out in turn, print both
    and write the parameters where load_params finds them."""
    if out == "v1":
        raise ValueError("v1 is the frozen legacy baseline; write the fit to a new version")
    rows = load_training_rows(settings, [(season, weeks)])
    if rows.empty:
        raise LookupError(f"no matched projections in season {season} weeks {weeks}")
    unknown = sorted(set(excluded_sources) - set(rows["source_website"]))
    if unknown:
        raise ValueError(f"excluded sources {unknown} have no projections in season {season} weeks {weeks}")
    rows = rows[~rows["source_website"].isin(excluded_sources)]

    params = fit_params(rows, excluded_sources, out)
    eligible = eligible_rows(rows)
    players = player_weeks(eligible, params["sources"])
    print_fit(eligible, players, params)

    folds = {week: fit_params(train, excluded_sources, out) for week, train, _ in leave_one_week_out(rows)}
    gate = evaluate_gate(rows, load_team_weeks(settings, season, weeks), folds, settings.seed)
    print_gate(gate)

    params["fitted_on"] = {
        "season": season,
        "weeks": weeks,
        "excluded_sources": excluded_sources,
        "n_player_rows": len(players),
        "fitted_at": timestamp(utc_now()),
    }
    params["gate"] = gate
    path = model_params.PARAMS_DIR / f"{out}.json"
    path.write_text(json.dumps(params, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {path}")
    return params


def load_training_rows(settings: Settings, windows: list[tuple[int, list[int]]]) -> pd.DataFrame:
    """One row per source projection of a matched player in the (season, weeks) windows, with the player's
    position and NFL team from Sleeper and the PPR points he scored, 0 when he has no stats row."""
    frames = []
    with closing(connect(settings, "projections")) as projections, closing(connect(settings, "league")) as league:
        players = pd.read_sql_query(PLAYERS, league)
        for season, weeks in windows:
            rows = pd.read_sql_query(PROJECTIONS, projections, params=(season,))
            rows = rows[rows["week"].isin(weeks)].merge(players, on="sleeper_player_id")
            frames.append(rows.merge(load_actuals(league, season), on=["sleeper_player_id", "week"], how="left"))
    rows = pd.concat(frames, ignore_index=True)
    return rows.assign(actual=rows["actual"].fillna(0.0))


def fit_params(rows: pd.DataFrame, excluded_sources: list[str], version: str) -> dict:
    """Model parameters fitted on the eligible rows of the sources that are not excluded."""
    rows = eligible_rows(rows[~rows["source_website"].isin(excluded_sources)])
    sources = fit_sources(rows)
    players = player_weeks(rows, sources)
    dud = fit_dud(players)
    sigma = fit_sigma(players, dud)
    measured = teammate_correlations(players, {"sigma": sigma})
    correlation, _ = shrink_to_positive_definite(clip_correlations(measured))
    return {
        "version": version,
        "sources": sources,
        "sigma": sigma,
        "dud": dud,
        "correlation": {"same_nfl_team": correlation},
    }


def source_errors(rows: pd.DataFrame) -> pd.DataFrame:
    """Per source: distinct weeks, rows, mean over-projection (projected - actual) and the mean squared error
    left once that bias is taken out, which is the variance of the error."""
    errors = rows.assign(error=rows["projected_points"] - rows["actual"]).groupby("source_website")["error"]
    weeks = rows.drop_duplicates(["source_website", "season", "week"])["source_website"].value_counts()
    return pd.DataFrame({"weeks": weeks, "n": errors.size(), "bias": errors.mean(), "mse": errors.var(ddof=0)})


def fit_sources(rows: pd.DataFrame) -> dict:
    """Bias and weight per source seen in at least MIN_SOURCE_WEEKS weeks, the weight being the inverse of the
    source's error variance scaled so these sources average 1; any other source keeps weight 1 and bias 0."""
    errors = source_errors(rows)
    fitted = errors[errors["weeks"] >= MIN_SOURCE_WEEKS]
    precision = 1 / fitted["mse"]
    weights = (precision / precision.mean()).clip(*WEIGHT_RANGE)
    sources = {}
    for source in errors.index:
        if source in fitted.index:
            sources[source] = {
                "weight": round(float(weights[source]), 3),
                "bias": round(float(fitted.at[source, "bias"]), 3),
            }
        else:
            sources[source] = dict(DEFAULT_SOURCE)
    return sources


def dud_rows(players: pd.DataFrame) -> pd.DataFrame:
    """Player-weeks with at least two sources, flagged as a dud when the player scored under DUD_THRESHOLD * mu."""
    rows = players[players["n_sources"] >= 2]
    return rows.assign(dud=rows["actual"] < DUD_THRESHOLD * rows["mu"])


def fit_dud(players: pd.DataFrame) -> dict:
    """Logistic dud probability in mu per position with at least MIN_DUD_ROWS rows and MIN_DUDS duds; the sampler
    never duds a position left out."""
    rows = dud_rows(players)
    by_position = {}
    for position in POSITION_ORDER:
        group = rows[rows["position"] == position]
        if len(group) < MIN_DUD_ROWS or group["dud"].sum() < MIN_DUDS:
            continue
        c, d = fit_logistic(group["mu"].to_numpy(), group["dud"].to_numpy(dtype=float))
        by_position[position] = {"c": round(c, 4), "d": round(d, 4)}
    return {"threshold_ratio": DUD_THRESHOLD, "by_position": by_position}


def fit_logistic(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Maximum likelihood (c, d) of P(y = 1) = expit(c + d * x)."""
    design = np.column_stack([np.ones_like(x), x])

    def negative_log_likelihood(coefficients: np.ndarray) -> float:
        z = design @ coefficients
        return -np.sum(y * log_expit(z) + (1 - y) * log_expit(-z))

    def gradient(coefficients: np.ndarray) -> np.ndarray:
        return design.T @ (expit(design @ coefficients) - y)

    start = np.array([logit(y.mean()), 0.0])
    c, d = minimize(negative_log_likelihood, start, jac=gradient, method="BFGS").x
    return float(c), float(d)


def sigma_residuals(players: pd.DataFrame, dud: dict) -> pd.DataFrame:
    """The player-weeks the lognormal part of the mixture explains, with their residual from the lognormal mean
    the sampler draws around. At a position with a fitted dud, the scores under the dud threshold belong to the
    dud part and are left out."""
    threshold = dud["threshold_ratio"]
    lognormal_mu = lognormal_means(players["mu"].to_numpy(), dud_probabilities(players, dud), threshold)
    duds = (players["actual"] < threshold * players["mu"]) & players["position"].isin(list(dud["by_position"]))
    return players.assign(residual=players["actual"] - lognormal_mu)[~duds]


def sigma_bins(residuals: pd.DataFrame) -> pd.DataFrame:
    """Mean mu, rows and residual standard deviation of bins of BIN_SIZE rows by mu; a trailing bin under
    MIN_TRAILING_BIN rows joins the bin before it."""
    ordered = residuals.sort_values("mu", ignore_index=True)
    labels = np.arange(len(ordered)) // BIN_SIZE
    trailing = len(ordered) % BIN_SIZE
    if 0 < trailing < MIN_TRAILING_BIN:
        labels[-trailing:] -= 1
    return ordered.groupby(labels).agg(mean_mu=("mu", "mean"), n=("mu", "size"), std=("residual", "std"))


def fit_sigma(players: pd.DataFrame, dud: dict) -> dict:
    """sigma = a + b * mu per position: a least-squares line through the binned residual spread, weighted by bin
    size. A position with under MIN_SIGMA_ROWS rows gets its overall residual spread as a flat sigma."""
    residuals = sigma_residuals(players, dud)
    by_position = {}
    for position in POSITION_ORDER:
        group = residuals[residuals["position"] == position]
        if len(group) < MIN_SIGMA_ROWS:
            a, b = group["residual"].std(), 0.0
        else:
            bins = sigma_bins(group)
            b, a = np.polyfit(bins["mean_mu"], bins["std"], 1, w=np.sqrt(bins["n"]))
        by_position[position] = {"a": round(float(a), 4), "b": round(float(b), 4)}
    return {"formula": "linear", "by_position": by_position}


def teammate_correlations(players: pd.DataFrame, params: dict) -> pd.DataFrame:
    """Per pair type, the Pearson correlation and count of the standardised residuals (actual - mu) / sigma of
    every pair of players at the two positions on the same NFL team in the same week."""
    players = players.dropna(subset=["nfl_team"])
    z = (players["actual"] - players["mu"]) / player_sigmas(players, params)
    scored = players.assign(z=z)[["season", "week", "nfl_team", "position", "z"]]
    measured = {}
    for pair in PAIR_TYPES:
        first, second = pair.split("-")
        pairs = scored[scored["position"] == first].merge(
            scored[scored["position"] == second], on=["season", "week", "nfl_team"], suffixes=("_first", "_second")
        )
        measured[pair] = {"rho": pairs["z_first"].corr(pairs["z_second"]), "n": len(pairs)}
    return pd.DataFrame.from_dict(measured, orient="index")


def clip_correlations(measured: pd.DataFrame) -> dict[str, float]:
    """Measured correlations clipped to CORRELATION_RANGE; a pair type seen fewer than MIN_PAIRS times gets 0."""
    low, high = CORRELATION_RANGE
    clipped = {}
    for pair in measured.itertuples():
        clipped[pair.Index] = round(float(np.clip(pair.rho, low, high)), 2) if pair.n >= MIN_PAIRS else 0.0
    return clipped


def shrink_to_positive_definite(correlations: dict[str, float]) -> tuple[dict[str, float], float]:
    """The correlations scaled down together, a step at a time, until the matrix the sampler builds for
    LARGEST_TEAM_GROUP has no eigenvalue under MIN_EIGENVALUE, and the scale that took. np.linalg.cholesky, which
    correlates teammates in every simulation, fails on a matrix that is not positive definite."""
    for step in range(SCALE_STEPS, -1, -1):
        scale = step / SCALE_STEPS
        scaled = {pair: round(scale * rho, 2) for pair, rho in correlations.items()}
        if smallest_eigenvalue(scaled) >= MIN_EIGENVALUE:
            return scaled, scale


def smallest_eigenvalue(correlations: dict[str, float]) -> float:
    return float(np.linalg.eigvalsh(teammate_matrix(LARGEST_TEAM_GROUP, correlations)).min())


def print_fit(eligible: pd.DataFrame, players: pd.DataFrame, params: dict) -> None:
    """The fitted parameters beside the numbers they were fitted from."""
    print_sources(eligible, params["sources"])
    print_dud(players, params["dud"])
    print_sigma(players, params)
    print_correlation(players, params)


def print_sources(eligible: pd.DataFrame, sources: dict) -> None:
    table = []
    for source in source_errors(eligible).itertuples():
        written = sources[source.Index]
        cells = [str(source.weeks), str(source.n), f"{source.bias:.2f}", f"{np.sqrt(source.mse):.2f}"]
        table.append([source.Index, *cells, f"{written['bias']:.3f}", f"{written['weight']:.3f}"])
    print_table(["source", "weeks", "rows", "mean error", "rmse", "bias", "weight"], table)


def print_dud(players: pd.DataFrame, dud: dict) -> None:
    rows = dud_rows(players)
    table = []
    for position in POSITION_ORDER:
        group = rows[rows["position"] == position]
        fitted = dud["by_position"].get(position)
        coefficients = [f"{fitted['c']:.3f}", f"{fitted['d']:.4f}"] if fitted else ["-", "-"]
        counts = [str((players["position"] == position).sum()), str(len(group)), str(group["dud"].sum())]
        table.append([position, *counts, f"{group['dud'].mean():.3f}", *coefficients])
    print_table(["position", "player-weeks", "2+ sources", "duds", "dud rate", "c", "d"], table)


def print_sigma(players: pd.DataFrame, params: dict) -> None:
    residuals = sigma_residuals(players, params["dud"])
    lines, bins = [], []
    for position in POSITION_ORDER:
        group = residuals[residuals["position"] == position]
        line = params["sigma"]["by_position"][position]
        lines.append([position, str(len(group)), f"{line['a']:.3f}", f"{line['b']:.4f}"])
        if len(group) < MIN_SIGMA_ROWS:
            continue
        for sigma_bin in sigma_bins(group).itertuples():
            fitted_sigma = max(1.0, line["a"] + line["b"] * sigma_bin.mean_mu)
            cells = [f"{sigma_bin.mean_mu:.1f}", str(sigma_bin.n), f"{sigma_bin.std:.2f}", f"{fitted_sigma:.2f}"]
            bins.append([position, *cells])
    print_table(["position", "rows", "a", "b"], lines)
    print_table(["position", "mean mu", "n", "std", "sigma"], bins)


def print_correlation(players: pd.DataFrame, params: dict) -> None:
    written = params["correlation"]["same_nfl_team"]
    measured = teammate_correlations(players, params)
    clipped = clip_correlations(measured)
    table = []
    for pair in measured.itertuples():
        cells = [str(pair.n), f"{pair.rho:.3f}", f"{clipped[pair.Index]:.2f}", f"{written[pair.Index]:.2f}"]
        table.append([pair.Index, *cells])
    print_table(["pair", "pairs", "measured", "clipped", "written"], table)
    _, scale = shrink_to_positive_definite(clipped)
    group = " ".join(LARGEST_TEAM_GROUP)
    print(f"scaled by {scale:.2f}; smallest eigenvalue {smallest_eigenvalue(written):.3f} for teammates {group}")


def print_gate(gate: dict) -> None:
    """The held-out metrics of the fit beside v1's, and the gate's verdict."""
    table = []
    for metric in ["player_coverage_50", "player_coverage_80", "player_coverage_95", "n_player_rows"]:
        for position, value in gate[metric].items():
            table.append([metric, position, str(value), str(gate["v1"][metric][position])])
    for metric in ["team_coverage_80", "team_mae", "moneyline_brier", "n_team_weeks", "n_matchups"]:
        table.append([metric, "", str(gate[metric]), str(gate["v1"][metric])])
    print_table(["metric", "position", "fitted", "v1"], table)
    failures = gate_failures(gate, gate["v1"])
    print("gate passed" if gate["passed"] else "gate failed: " + "; ".join(failures))
