"""Playoffs step: price the first-place and make-the-playoffs futures from simulated final standings.

The current week comes from the simulate step's draws. Each later regular-season week is scraped, matched and
simulated here from the rosters as they stand, and every simulated season is ranked on top of the record to date.
"""

import json
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from pipeline import playoff_charts, standings
from pipeline.model.params import load_params
from pipeline.model.sampling import simulate_teams
from pipeline.runner import StepContext, StepResult, delete_source_projections, print_table, timestamp, utc_now
from pipeline.settings import sleeper_get
from pipeline.sources import SOURCE_NAMES, load_source
from pipeline.sources.base import ProjectionSource
from pipeline.steps import clean, lineups, match, odds, scrape, stats
from pipeline.steps.league import insert_rows, load_league_settings

NAME = "playoffs"

N_SIMS = 20_000
# Futures this likely or this unlikely are not offered; the standings matrix keeps every probability.
MIN_PROBABILITY = 0.01
MAX_PROBABILITY = 0.99
TOLERANCE = 1e-6
TOP_N = 5
STARTER_COLUMNS = ["roster_id", "owner", "slot", "sleeper_player_id", "position", "nfl_team", "mu", "sigma"]

FUTURES_DDL = """
CREATE TABLE IF NOT EXISTS betting_odds_first_place (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  probability REAL NOT NULL,
  american_odds TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  season INTEGER NOT NULL,
  UNIQUE (run_id, week, team_id)
);
CREATE TABLE IF NOT EXISTS betting_odds_make_playoffs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  probability REAL NOT NULL,
  american_odds TEXT NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  season INTEGER NOT NULL,
  UNIQUE (run_id, week, team_id)
);
CREATE TABLE IF NOT EXISTS standings_probability_matrix (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  position INTEGER NOT NULL,
  probability REAL NOT NULL,
  count INTEGER NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  season INTEGER NOT NULL,
  UNIQUE (run_id, week, team_id, position)
);
"""

# Sleeper keeps a roster's points as whole points plus hundredths; notebook 09 added the hundredths unscaled.
RECORDS = """
SELECT roster_id, wins, losses, ties, fpts + fpts_decimal / 100.0 AS points
FROM rosters
WHERE league_id = ?
"""

WEEK_SOURCES = "SELECT DISTINCT source_website FROM projections WHERE season = ? AND week = ? ORDER BY source_website"


@dataclass
class SimulatedWeek:
    week: int
    scores: np.ndarray  # (n_sims, n_teams), teams in column order
    pairs: list[tuple[int, int]]  # the week's head-to-head games as pairs of team columns


@dataclass
class ProjectedWeek:
    sources: list[str]  # websites whose projections the week's player stats use
    failures: dict[str, str]  # website -> its failed checks, for each source dropped this run
    n_players: int
    warnings: list[str]


def run(ctx: StepContext) -> StepResult:
    settings = ctx.settings
    started = time.perf_counter()
    league_conn = ctx.db("league")
    league = load_league_settings(league_conn, settings.league_id)
    if settings.week >= league.playoff_week_start:
        return StepResult({}, warnings=["playoffs have started; no futures markets"])
    divisions = division_count(league_conn, settings.league_id)
    if divisions > 0:
        raise RuntimeError(f"the league has {divisions} divisions, but these standings rank one table")

    teams = lineups.load_teams(league_conn, settings.league_id, league.waiver_budget)
    columns = {team.roster_id: column for column, team in enumerate(teams)}
    current_week = simulated_current_week(ctx, columns)
    n_sims = len(current_week.scores)
    future_weeks = list(range(settings.week + 1, league.playoff_week_start))
    future, projections, warnings = simulate_future_weeks(ctx, future_weeks, league.slots, teams, columns, n_sims)
    weeks = [current_week, *future]
    records = record_to_date(league_conn, settings.league_id, list(columns))
    warnings += standings_warnings(weeks, records, settings.week)

    counts = finishing_counts(records, weeks)
    first_place = counts[:, 0] / n_sims
    make_playoffs = counts[:, : league.playoff_teams].sum(axis=1) / n_sims
    check_totals(first_place, make_playoffs, league.playoff_teams)
    save_tables(
        ctx,
        {
            "betting_odds_first_place": market_rows(teams, first_place),
            "betting_odds_make_playoffs": market_rows(teams, make_playoffs),
            "standings_probability_matrix": matrix_rows(teams, counts, n_sims),
        },
    )
    print_futures(teams, first_place, make_playoffs)

    owners = [team.owner for team in teams]
    first_place_by_owner = pd.Series(first_place, index=owners)
    make_playoffs_by_owner = pd.Series(make_playoffs, index=owners)
    written = []
    if not ctx.options.get("no_charts"):
        written = [
            playoff_charts.playoff_probability(
                settings.images_dir, settings.week, make_playoffs_by_owner, league.playoff_teams
            ),
            playoff_charts.first_place_race(settings.images_dir, settings.week, first_place_by_owner),
        ]

    summary = {
        "future_weeks": future_weeks,
        "projections": projections,
        "first_place": leaders(first_place_by_owner),
        "make_playoffs": leaders(make_playoffs_by_owner),
        "n_sims": n_sims,
        "elapsed_s": round(time.perf_counter() - started, 2),
    }
    return StepResult(summary, warnings, written)


def division_count(conn: sqlite3.Connection, league_id: str) -> int:
    (sleeper_settings,) = conn.execute("SELECT settings FROM leagues WHERE league_id = ?", (league_id,)).fetchone()
    return json.loads(sleeper_settings).get("divisions") or 0


def simulated_current_week(ctx: StepContext, columns: dict[int, int]) -> SimulatedWeek:
    """The first N_SIMS sims of the simulate step's latest run; a team it did not simulate scores 0."""
    settings = ctx.settings
    simulation = odds.latest_simulation(ctx)
    draws = odds.load_draws(settings, simulation["draws_path"]).iloc[:N_SIMS]
    scores = draws.reindex(columns=list(columns), fill_value=0.0).to_numpy()
    ctx.log(f"week {settings.week}: {len(scores)} sims of simulation {simulation['run_id']}")
    return SimulatedWeek(settings.week, scores, column_pairs(odds.load_matchups(ctx), columns))


def simulate_future_weeks(
    ctx: StepContext,
    future_weeks: list[int],
    slots: dict[str, str],
    teams: list[lineups.Team],
    columns: dict[int, int],
    n_sims: int,
) -> tuple[list[SimulatedWeek], list[dict], list[str]]:
    """Project, pick and simulate each week after the current one. Also returns a summary entry per week, and the
    warnings: the sub-steps' own, and one per dropped source listing the weeks it was dropped for."""
    settings = ctx.settings
    league_conn = ctx.db("league")
    sources = future_sources(ctx.options.get("sources"))
    sleeper_players = scrape.read_sleeper_players(league_conn)
    players = lineups.load_players(league_conn)
    params = load_params(settings.model_version)

    simulated = []
    entries = []
    dropped_weeks = defaultdict(list)
    warnings = []
    for week in future_weeks:
        week_started = time.perf_counter()
        projected = project_week(ctx, week, sources, sleeper_players)
        starters, empty_slots = pick_starters(ctx, week, teams, players, slots)
        scores = team_scores(starters, params, n_sims, settings.seed + week, columns)
        pairs = column_pairs(fetch_pairings(settings.league_id, week), columns)
        simulated.append(SimulatedWeek(week, scores, pairs))

        entries.append(
            {"week": week, "sources": projected.sources, "n_players": projected.n_players, "empty_slots": empty_slots}
        )
        for website, failed_checks in projected.failures.items():
            dropped_weeks[(website, failed_checks)].append(str(week))
        for warning in projected.warnings:
            if warning not in warnings:
                warnings.append(warning)
        ctx.log(
            f"week {week}: {', '.join(projected.sources)}; {projected.n_players} players with stats; "
            f"{empty_slots} empty slots; {time.perf_counter() - week_started:.1f}s"
        )

    for (website, failed_checks), weeks in dropped_weeks.items():
        warnings.append(f"dropped {website} for weeks {', '.join(weeks)}: {failed_checks} failed")
    return simulated, entries, warnings


def future_sources(requested: list[str] | None) -> list[ProjectionSource]:
    """The sources to scrape for later weeks: those that publish them, Sleeper first. As in the scrape step,
    --sources picks which are scraped and the rest keep the rows they have, but Sleeper, which every week needs, is
    always scraped."""
    sources = []
    for name in SOURCE_NAMES:
        if name != "sleeper" and requested is not None and name not in requested:
            continue
        source = load_source(name)
        if source.supports_future_weeks:
            sources.append(source)
    return sources


def project_week(
    ctx: StepContext, week: int, sources: list[ProjectionSource], sleeper_players: list[dict]
) -> ProjectedWeek:
    """Scrape, clean, match and compute player stats for a future week as the weekly steps do for the current one.

    A source that fails its checks is dropped for the week, except Sleeper: the others are checked against it.
    """
    settings = replace(ctx.settings, week=week)
    with StepContext(settings, ctx.run_id, ctx.options, step=NAME) as week_ctx:
        conn = week_ctx.db("projections")
        conn.executescript(scrape.PROJECTIONS_DDL)
        failures = {}
        sleeper_rows = []
        for source in sources:
            rows, report, _ = scrape.scrape_source(week_ctx, source, sleeper_players, sleeper_rows)
            delete_source_projections(conn, settings.season, week, source.website)
            if report.status == "fail":
                failed = [check for check in report.checks if check.status == "fail"]
                if source.name == "sleeper":
                    details = "; ".join(f"{check.name}: {check.detail}" for check in failed)
                    raise RuntimeError(f"{source.website} failed its checks for week {week}: {details}")
                failures[source.website] = ", ".join(check.name for check in failed)
                continue
            scrape.insert_projections(conn, rows)
            if source.name == "sleeper":
                sleeper_rows = rows

        warnings = clean.run(week_ctx).warnings + match.run(week_ctx).warnings
        week_stats = stats.run(week_ctx)
        websites = [row["source_website"] for row in conn.execute(WEEK_SOURCES, (settings.season, week))]
    return ProjectedWeek(websites, failures, week_stats.summary["n_players"], warnings + week_stats.warnings)


def pick_starters(
    ctx: StepContext, week: int, teams: list[lineups.Team], players: dict, slots: dict[str, str]
) -> tuple[pd.DataFrame, int]:
    """Each roster's best lineup from its own players for a future week, and how many slots were left empty.

    Unlike the lineups step nobody is picked up from waivers, so an empty slot scores 0.
    """
    season = ctx.settings.season
    byes = lineups.load_byes(ctx.db("league"), season, week)
    projections = lineups.load_projections(ctx.db("projections"), season, week)
    starters = []
    empty_slots = 0
    for team in teams:
        statuses = lineups.roster_statuses(team, players, projections, byes)
        candidates = [
            lineups.projected_player(players[player_id], projections[player_id])
            for player_id, status in statuses.items()
            if status == "bench"
        ]
        lineup = lineups.pick_lineup(slots, candidates)
        empty_slots += len(slots) - len(lineup)
        for slot, player in lineup.items():
            starters.append(
                {
                    "roster_id": team.roster_id,
                    "owner": team.owner,
                    "slot": slot,
                    "sleeper_player_id": player.sleeper_player_id,
                    "position": player.position,
                    "nfl_team": player.nfl_team,
                    "mu": player.mu,
                    "sigma": player.sigma,
                }
            )
    return pd.DataFrame(starters, columns=STARTER_COLUMNS), empty_slots


def team_scores(starters: pd.DataFrame, params: dict, n_sims: int, seed: int, columns: dict[int, int]) -> np.ndarray:
    """Simulated totals with teams in column order; a team with nobody to start scores 0."""
    totals, roster_ids = simulate_teams(starters, params, n_sims, seed)
    scores = np.zeros((n_sims, len(columns)))
    for index, roster_id in enumerate(roster_ids):
        scores[:, columns[roster_id]] = totals[:, index]
    return scores


def fetch_pairings(league_id: str, week: int) -> list[tuple[int, int]]:
    """A future week's head-to-head pairs, lower roster_id first; Sleeper posts the regular season up front."""
    rosters_by_matchup = defaultdict(list)
    for row in sleeper_get(f"/league/{league_id}/matchups/{week}"):
        if row["matchup_id"] is not None:
            rosters_by_matchup[row["matchup_id"]].append(row["roster_id"])
    return [tuple(sorted(rosters)) for rosters in rosters_by_matchup.values() if len(rosters) == 2]


def column_pairs(pairs: list[tuple[int, int]], columns: dict[int, int]) -> list[tuple[int, int]]:
    return [(columns[first], columns[second]) for first, second in pairs]


def record_to_date(conn: sqlite3.Connection, league_id: str, roster_ids: list[int]) -> pd.DataFrame:
    records = pd.read_sql_query(RECORDS, conn, params=(league_id,), index_col="roster_id")
    return records.loc[roster_ids]


def standings_warnings(weeks: list[SimulatedWeek], records: pd.DataFrame, week: int) -> list[str]:
    """Gaps that leave the simulated standings short: weeks without pairings, and records missing games."""
    warnings = []
    unpaired = [str(simulated.week) for simulated in weeks if not simulated.pairs]
    if unpaired:
        warnings.append(f"no matchups for weeks {', '.join(unpaired)}; their scores add points but no wins")
    games_played = sorted(set(records["wins"] + records["losses"] + records["ties"]))
    if games_played != [week - 1]:
        played = ", ".join(str(games) for games in games_played)
        warnings.append(f"rosters show {played} games played, not {week - 1}; the standings miss the difference")
    return warnings


def finishing_counts(records: pd.DataFrame, weeks: list[SimulatedWeek]) -> np.ndarray:
    """counts[team, place - 1] over the simulated seasons, with teams in column order."""
    wins, ties, points = standings.season_totals(
        records["wins"].to_numpy(),
        records["ties"].to_numpy(),
        records["points"].to_numpy(),
        [(week.scores, week.pairs) for week in weeks],
    )
    return standings.position_counts(standings.finishing_positions(wins, ties, points))


def check_totals(first_place: np.ndarray, make_playoffs: np.ndarray, playoff_teams: int) -> None:
    """Every simulated season crowns one first-place team and sends exactly playoff_teams to the playoffs."""
    if abs(first_place.sum() - 1) > TOLERANCE:
        raise RuntimeError(f"first-place probabilities sum to {first_place.sum():.6f}, not 1")
    if abs(make_playoffs.sum() - playoff_teams) > TOLERANCE:
        raise RuntimeError(f"make-playoffs probabilities sum to {make_playoffs.sum():.6f}, not {playoff_teams}")


def market_rows(teams: list[lineups.Team], probabilities: np.ndarray) -> list[dict]:
    rows = []
    for team, probability in zip(teams, probabilities, strict=True):
        if MIN_PROBABILITY <= probability <= MAX_PROBABILITY:
            rows.append(
                {
                    "team_id": team.roster_id,
                    "team_name": team.team_name,
                    "owner": team.owner,
                    "probability": probability,
                    "american_odds": odds.probability_to_american_odds(probability),
                }
            )
    return rows


def matrix_rows(teams: list[lineups.Team], counts: np.ndarray, n_sims: int) -> list[dict]:
    """Every team at every finishing position, including the positions it never reached."""
    rows = []
    for team, team_counts in zip(teams, counts, strict=True):
        for position, count in enumerate(team_counts, start=1):
            rows.append(
                {
                    "team_id": team.roster_id,
                    "team_name": team.team_name,
                    "owner": team.owner,
                    "position": position,
                    "probability": count / n_sims,
                    "count": int(count),
                }
            )
    return rows


def save_tables(ctx: StepContext, tables: dict[str, list[dict]]) -> None:
    """Replace the week's rows in each table, whichever run wrote them, so a week has one set of futures."""
    settings = ctx.settings
    conn = ctx.db("odds")
    conn.executescript(FUTURES_DDL)
    stamp = {"run_id": ctx.run_id, "week": settings.week, "season": settings.season, "created_at": timestamp(utc_now())}
    for table, rows in tables.items():
        conn.execute(f"DELETE FROM {table} WHERE season = ? AND week = ?", (settings.season, settings.week))
        insert_rows(conn, table, [stamp | row for row in rows])
    conn.commit()


def print_futures(teams: list[lineups.Team], first_place: np.ndarray, make_playoffs: np.ndarray) -> None:
    order = sorted(range(len(teams)), key=lambda column: (-make_playoffs[column], -first_place[column]))
    rows = [[teams[column].owner, f"{first_place[column]:.1%}", f"{make_playoffs[column]:.1%}"] for column in order]
    print_table(["owner", "first place", "make playoffs"], rows)


def leaders(probabilities: pd.Series) -> list[dict]:
    ranked = probabilities.sort_values(ascending=False, kind="stable").head(TOP_N)
    return [{"owner": owner, "probability": round(float(probability), 3)} for owner, probability in ranked.items()]
