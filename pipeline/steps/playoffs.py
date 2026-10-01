"""Playoffs step: price the season's futures from simulated final standings and the playoff bracket they seed.

The current week comes from the simulate step's draws. Each later week, the playoff weeks included, is scraped,
matched and simulated here from the rosters as they stand; every simulated season is ranked on top of the record to
date, and its seeds play the bracket on the playoff weeks' scores. The markets are priced through pipeline.markets
on those simulated seasons, which are stored as the run's standings matrix so the app can price futures parlays on
them. Make playoffs is priced YES and NO for every team; last place and champion only for the teams between 1% and
99%.
"""

import json
import math
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, replace

import numpy as np
import pandas as pd

from pipeline import markets, playoff_charts, standings
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
# Last place and champion are not offered this likely or this unlikely; make playoffs is offered for every team, and
# the standings matrix keeps every probability.
MIN_PROBABILITY = 0.01
MAX_PROBABILITY = 0.99
TOLERANCE = 1e-6
TOP_N = 5
STARTER_COLUMNS = ["roster_id", "owner", "slot", "sleeper_player_id", "position", "nfl_team", "mu", "sigma"]

MARKET_DDL = """
CREATE TABLE IF NOT EXISTS {table} (
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
"""
# Both sides of every team: a side at a chance of 0 or 1 has no price, and a week priced before the NO side has none.
MAKE_PLAYOFFS_DDL = """
CREATE TABLE IF NOT EXISTS betting_odds_make_playoffs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  week INTEGER NOT NULL,
  team_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  probability REAL NOT NULL,
  american_odds TEXT,
  no_probability REAL,
  no_american_odds TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  season INTEGER NOT NULL,
  UNIQUE (run_id, week, team_id)
);
"""
STANDINGS_DDL = """
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
# One row per simulation run: every simulated season's places and champion, teams in the order roster_ids lists them.
SIMULATION_STANDINGS_DDL = """
CREATE TABLE IF NOT EXISTS simulation_standings (
  run_id TEXT PRIMARY KEY,
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  n_sims INTEGER NOT NULL,
  playoff_teams INTEGER NOT NULL,
  roster_ids TEXT NOT NULL,
  standings BLOB NOT NULL
);
"""
FUTURES_DDL = (
    MAKE_PLAYOFFS_DDL
    + MARKET_DDL.format(table="betting_odds_last_place")
    + MARKET_DDL.format(table="betting_odds_champion")
    + STANDINGS_DDL
    + SIMULATION_STANDINGS_DDL
)

# Sleeper keeps a roster's points as whole points plus hundredths; notebook 09 added the hundredths unscaled.
RECORDS = """
SELECT roster_id, wins, losses, ties, fpts + fpts_decimal / 100.0 AS points
FROM rosters
WHERE league_id = ?
"""

WEEK_SOURCES = "SELECT DISTINCT source_website FROM projections WHERE season = ? AND week = ? ORDER BY source_website"
# The weekly steps' tables each later week passes through; the step clears that week's rows from them when it is done.
SHARED_TABLES = ["projections", "projections_with_sleeper", "player_week_stats"]


@dataclass
class SimulatedWeek:
    week: int
    scores: np.ndarray  # (n_sims, n_teams), teams in column order
    pairs: list[tuple[int, int]]  # the week's head-to-head games as pairs of team columns


@dataclass
class ProjectedWeek:
    sources: list[str]  # websites whose projections the week's player stats use
    failures: dict[str, str]  # website -> its failed checks and their details, for each source dropped this run
    n_players: int
    warnings: list[str]


def run(ctx: StepContext) -> StepResult:
    settings = ctx.settings
    started = time.perf_counter()
    league_conn = ctx.db("league")
    league = load_league_settings(league_conn, settings.league_id)
    if settings.week >= league.playoff_week_start:
        return StepResult({}, warnings=["playoffs have started; no futures markets"])
    refuse_unsupported_league(read_sleeper_settings(league_conn, settings.league_id))

    season_weeks = list(range(settings.week + 1, league.playoff_week_start))
    rounds = int(math.log2(league.playoff_teams))
    playoff_weeks = list(range(league.playoff_week_start, league.playoff_week_start + rounds))
    future_weeks = season_weeks + playoff_weeks
    refuse_weeks_already_run(ctx.db("projections"), settings.season, future_weeks)

    teams = lineups.load_teams(league_conn, settings.league_id, league.waiver_budget)
    columns = {team.roster_id: column for column, team in enumerate(teams)}
    simulation = odds.latest_simulation(ctx)
    current_week = simulated_current_week(ctx, simulation, columns)
    n_sims = len(current_week.scores)
    try:
        scores, projections, warnings = simulate_future_weeks(ctx, future_weeks, league.slots, teams, columns, n_sims)
    finally:
        clear_future_weeks(ctx.db("projections"), settings.season, future_weeks)
    weeks = [current_week]
    for week in season_weeks:
        pairs = column_pairs(fetch_pairings(settings.league_id, week), columns)
        weeks.append(SimulatedWeek(week, scores[week], pairs))
    records = record_to_date(league_conn, settings.league_id, list(columns))
    warnings += standings_warnings(weeks, records, settings.week)

    positions = final_standings(records, weeks)
    seeds = standings.playoff_seeds(positions, league.playoff_teams)
    champions = standings.play_bracket(seeds, [scores[week] for week in playoff_weeks])
    chances = futures_chances(positions, champions, league.playoff_teams)
    check_totals(chances, league.playoff_teams)
    tables = {
        "betting_odds_make_playoffs": make_playoffs_rows(teams, chances["make_playoffs"]),
        "betting_odds_last_place": market_rows(teams, chances["last_place"]),
        "betting_odds_champion": market_rows(teams, chances["champion"]),
        "standings_probability_matrix": matrix_rows(teams, standings.position_counts(positions), n_sims),
    }
    standings_row = {
        "n_sims": n_sims,
        "playoff_teams": league.playoff_teams,
        "roster_ids": ",".join(str(team.roster_id) for team in teams),
        "standings": markets.encode_standings(positions, champions),
    }
    save_tables(ctx, simulation["run_id"], tables, standings_row)
    ctx.log(f"simulation_standings: {len(standings_row['standings']) / 1024:.1f} KB for run {simulation['run_id']}")
    print_futures(teams, chances)

    owners = [team.owner for team in teams]
    by_owner = {market: pd.Series(chance, index=owners) for market, chance in chances.items()}
    written = []
    if not ctx.options.get("no_charts"):
        written = [
            playoff_charts.playoff_probability(
                settings.images_dir, settings.week, by_owner["make_playoffs"], league.playoff_teams
            )
        ]

    summary = {
        "future_weeks": future_weeks,
        "playoff_weeks": playoff_weeks,
        "projections": projections,
        **{market: leaders(chance) for market, chance in by_owner.items()},
        "n_sims": n_sims,
        "standings_bytes": len(standings_row["standings"]),
        "elapsed_s": round(time.perf_counter() - started, 2),
    }
    return StepResult(summary, warnings, written)


def read_sleeper_settings(conn: sqlite3.Connection, league_id: str) -> dict:
    """The league's settings as Sleeper sent them, the ones LeagueSettings leaves out included."""
    (settings,) = conn.execute("SELECT settings FROM leagues WHERE league_id = ?", (league_id,)).fetchone()
    return json.loads(settings)


def refuse_unsupported_league(sleeper_settings: dict) -> None:
    """The standings rank one table, and the champion is priced on Sleeper's fixed bracket: no reseeding, a round a
    week and no byes."""
    divisions = sleeper_settings.get("divisions") or 0
    if divisions > 0:
        raise RuntimeError(f"the league has {divisions} divisions, but these standings rank one table")
    seed_type = sleeper_settings["playoff_seed_type"]
    if seed_type != 0:
        raise RuntimeError(f"playoff_seed_type is {seed_type}, but the bracket is priced without reseeding (0)")
    round_type = sleeper_settings["playoff_round_type"]
    if round_type != 0:
        raise RuntimeError(f"playoff_round_type is {round_type}, but the bracket is priced a round a week (0)")
    playoff_teams = sleeper_settings["playoff_teams"]
    if not math.log2(playoff_teams).is_integer():
        raise RuntimeError(f"playoff_teams is {playoff_teams}, but a bracket without byes needs a power of two")


def simulated_current_week(ctx: StepContext, simulation: sqlite3.Row, columns: dict[int, int]) -> SimulatedWeek:
    """The first N_SIMS sims of the simulate step's latest run; a team it did not simulate scores 0."""
    settings = ctx.settings
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
) -> tuple[dict[int, np.ndarray], list[dict], list[str]]:
    """Project, pick and simulate each week after the current one, returning its scores by week. Also returns a
    summary entry per week, and the warnings: the sub-steps' own, and one per dropped source listing the weeks it was
    dropped for."""
    settings = ctx.settings
    league_conn = ctx.db("league")
    sources = future_sources(ctx.options.get("sources"))
    sleeper_players = scrape.read_sleeper_players(league_conn)
    players = lineups.load_players(league_conn)
    params = load_params(settings.model_version)

    scores = {}
    entries = []
    dropped_weeks = defaultdict(list)
    warnings = []
    for week in future_weeks:
        week_started = time.perf_counter()
        projected = project_week(ctx, week, sources, sleeper_players)
        starters, empty_slots = pick_starters(ctx, week, teams, players, slots)
        scores[week] = team_scores(starters, params, n_sims, settings.seed + week, columns)

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
        warnings.append(f"dropped {website} for weeks {', '.join(weeks)}: {failed_checks}")
    return scores, entries, warnings


def future_sources(requested: list[str] | None) -> list[ProjectionSource]:
    """The sources to scrape for later weeks: those that publish them, Sleeper first. --sources narrows them, but
    Sleeper, which every week needs, is always scraped."""
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
            rows, report, _ = scrape.scrape_source(week_ctx, source, sleeper_players, sleeper_rows, future_week=True)
            delete_source_projections(conn, settings.season, week, source.website)
            if report.status == "fail":
                failed = [check for check in report.checks if check.status == "fail"]
                details = "; ".join(f"{check.name}: {check.detail}" for check in failed)
                if source.name == "sleeper":
                    raise RuntimeError(f"{source.website} failed its checks for week {week}: {details}")
                failures[source.website] = details
                continue
            scrape.insert_projections(conn, rows)
            if source.name == "sleeper":
                sleeper_rows = rows

        warnings = clean.run(week_ctx).warnings + match.run(week_ctx).warnings
        week_stats = stats.run(week_ctx)
        websites = [row["source_website"] for row in conn.execute(WEEK_SOURCES, (settings.season, week))]
    return ProjectedWeek(websites, failures, week_stats.summary["n_players"], warnings + week_stats.warnings)


def refuse_weeks_already_run(conn: sqlite3.Connection, season: int, weeks: list[int]) -> None:
    """Only a weekly run writes team_lineups, so rows for a later week mean the season has moved past this one, and
    clearing that week's projections afterwards would wipe the run that made them."""
    if not weeks or "team_lineups" not in table_names(conn):
        return
    placeholders = ", ".join("?" * len(weeks))
    query = f"SELECT DISTINCT week FROM team_lineups WHERE season = ? AND week IN ({placeholders}) ORDER BY week"
    already_run = [week for (week,) in conn.execute(query, (season, *weeks))]
    if already_run:
        listed = ", ".join(map(str, already_run))
        raise RuntimeError(f"week {listed} has already been run; pricing an earlier week would clear its projections")


def clear_future_weeks(conn: sqlite3.Connection, season: int, weeks: list[int]) -> None:
    """Delete the later weeks' rows from the weekly steps' tables: they were made for this run's simulation, and left
    in place they would pass for the week's own projections. A table not created yet has nothing to delete."""
    existing = table_names(conn)
    week_keys = [(season, week) for week in weeks]
    with conn:
        for table in SHARED_TABLES:
            if table in existing:
                conn.executemany(f"DELETE FROM {table} WHERE season = ? AND week = ?", week_keys)


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


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


def final_standings(records: pd.DataFrame, weeks: list[SimulatedWeek]) -> np.ndarray:
    """positions[sim, team]: each team's place in each simulated season, with teams in column order."""
    wins, ties, points = standings.season_totals(
        records["wins"].to_numpy(),
        records["ties"].to_numpy(),
        records["points"].to_numpy(),
        [(week.scores, week.pairs) for week in weeks],
    )
    return standings.finishing_positions(wins, ties, points)


def futures_chances(positions: np.ndarray, champions: np.ndarray, playoff_teams: int) -> dict[str, np.ndarray]:
    """Each team's chance in every market, by the win rules the app settles the bets with: in the top playoff_teams
    and last in the standings, and the bracket won."""
    columns = range(positions.shape[1])
    outcomes = {
        "make_playoffs": [markets.make_playoffs(positions, column, playoff_teams, "yes") for column in columns],
        "last_place": [markets.last_place(positions, column) for column in columns],
        "champion": [markets.champion(champions, column) for column in columns],
    }
    return {
        market: np.array([markets.probability(outcome) for outcome in by_team]) for market, by_team in outcomes.items()
    }


def check_totals(chances: dict[str, np.ndarray], playoff_teams: int) -> None:
    """Every simulated season sends playoff_teams to the playoffs and has one team last and one champion."""
    expected = {"make_playoffs": playoff_teams, "last_place": 1, "champion": 1}
    for market, total in expected.items():
        chance_sum = chances[market].sum()
        if abs(chance_sum - total) > TOLERANCE:
            raise RuntimeError(f"{market} probabilities sum to {chance_sum:.6f}, not {total}")


def market_rows(teams: list[lineups.Team], probabilities: np.ndarray) -> list[dict]:
    """The teams priced inside the band; the others are not offered."""
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


def make_playoffs_rows(teams: list[lineups.Team], probabilities: np.ndarray) -> list[dict]:
    """Both sides for every team: a team misses the playoffs in every sim it does not make them."""
    rows = []
    for team, probability in zip(teams, probabilities, strict=True):
        rows.append(
            {
                "team_id": team.roster_id,
                "team_name": team.team_name,
                "owner": team.owner,
                "probability": probability,
                "american_odds": odds.probability_to_american_odds(probability),
                "no_probability": 1 - probability,
                "no_american_odds": odds.probability_to_american_odds(1 - probability),
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


def save_tables(ctx: StepContext, simulation_run_id: str, tables: dict[str, list[dict]], standings_row: dict) -> None:
    """Replace the week's rows in each table, whichever run wrote them, so a week has one set of futures, and the
    simulation run's standings matrix. Rows carry the run id of the simulation they were priced from, so a rerun of
    this step alone leaves the week's run as it was."""
    settings = ctx.settings
    conn = ctx.db("odds")
    conn.executescript(FUTURES_DDL)
    add_no_side(conn)
    stamp = {
        "run_id": simulation_run_id,
        "week": settings.week,
        "season": settings.season,
        "created_at": timestamp(utc_now()),
    }
    for table, rows in tables.items():
        conn.execute(f"DELETE FROM {table} WHERE season = ? AND week = ?", (settings.season, settings.week))
        insert_rows(conn, table, [stamp | row for row in rows])
    conn.execute("DELETE FROM simulation_standings WHERE run_id = ?", (simulation_run_id,))
    insert_rows(conn, "simulation_standings", [stamp | standings_row])
    conn.commit()


def add_no_side(conn: sqlite3.Connection) -> None:
    """Rebuild a betting_odds_make_playoffs made before the NO side, its rows keeping their YES side. A rebuild,
    because SQLite cannot drop the NOT NULL that table put on american_odds."""
    columns = [name for (name,) in conn.execute("SELECT name FROM pragma_table_info('betting_odds_make_playoffs')")]
    if "no_probability" in columns:
        return
    listed = ", ".join(columns)
    with conn:
        conn.execute("BEGIN")
        conn.execute("ALTER TABLE betting_odds_make_playoffs RENAME TO make_playoffs_before_no_side")
        conn.execute(MAKE_PLAYOFFS_DDL)
        conn.execute(
            f"INSERT INTO betting_odds_make_playoffs ({listed}) SELECT {listed} FROM make_playoffs_before_no_side"
        )
        conn.execute("DROP TABLE make_playoffs_before_no_side")


def print_futures(teams: list[lineups.Team], chances: dict[str, np.ndarray]) -> None:
    make_playoffs, champion = chances["make_playoffs"], chances["champion"]
    order = sorted(range(len(teams)), key=lambda column: (-make_playoffs[column], -champion[column]))
    rows = [[teams[column].owner, *(f"{chance[column]:.1%}" for chance in chances.values())] for column in order]
    print_table(["owner", *(market.replace("_", " ") for market in chances)], rows)


def leaders(probabilities: pd.Series) -> list[dict]:
    ranked = probabilities.sort_values(ascending=False, kind="stable").head(TOP_N)
    return [{"owner": owner, "probability": round(float(probability), 3)} for owner, probability in ranked.items()]
