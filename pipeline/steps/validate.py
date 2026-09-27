"""Validate step: notebook 08's cross-table checks over the week's lineups, simulation and odds, so a broken week
stops before publish. It writes nothing, and reads every odds table at the run publish would upload."""

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from itertools import permutations, product
from pathlib import Path

import pyarrow.parquet as pq

from pipeline.runner import StepContext, StepFailed, StepResult
from pipeline.steps.league import LeagueSettings, load_league_settings

NAME = "validate"

SUM_TOLERANCE = 1e-6
MAX_LISTED = 5

# Written together by the odds step, one row set per simulation run.
WEEKLY_ODDS_TABLES = [
    "betting_odds_team_ou",
    "betting_odds_matchup_ou",
    "betting_odds_matchup_ml",
    "betting_odds_highest_scorer",
    "betting_odds_lowest_scorer",
    "team_distribution_curves",
    "team_matchup_margin_curves",
]
# The playoffs step's futures, which end when the playoffs start. A market is empty when every team is priced
# outside its 1-99% band, so the standings matrix, written on every run, is what shows the step has run.
FUTURES_TABLES = ["betting_odds_first_place", "betting_odds_make_playoffs"]
STANDINGS_TABLE = "standings_probability_matrix"
RUN_TABLES = [*WEEKLY_ODDS_TABLES, *FUTURES_TABLES, STANDINGS_TABLE]
SCORER_TABLES = ["betting_odds_highest_scorer", "betting_odds_lowest_scorer"]

# The tables Flask reads for the week, which must have its rows.
REQUIRED_TABLES = [
    "betting_odds_matchup_ml",
    "betting_odds_team_ou",
    "betting_odds_highest_scorer",
    "betting_odds_lowest_scorer",
    "team_distribution_curves",
    "team_matchup_margin_curves",
    "team_lineups",
    "projections_rosters",
    "matchups",
]
PROBABILITY_COLUMNS = {
    "betting_odds_team_ou": ["over_prob", "under_prob"],
    "betting_odds_matchup_ou": ["over_prob", "under_prob"],
    "betting_odds_matchup_ml": ["team1_win_prob", "team2_win_prob"],
    "betting_odds_highest_scorer": ["probability"],
    "betting_odds_lowest_scorer": ["probability"],
    "betting_odds_first_place": ["probability"],
    "betting_odds_make_playoffs": ["probability"],
    "standings_probability_matrix": ["probability"],
    "team_matchup_margin_curves": ["team_win_prob", "opponent_win_prob", "tie_prob"],
}
# Flask maps these back to a Sleeper user by display name or username.
OWNER_COLUMNS = {
    "team_lineups": ["owner"],
    "betting_odds_team_ou": ["owner"],
    "betting_odds_highest_scorer": ["owner"],
    "betting_odds_lowest_scorer": ["owner"],
    "betting_odds_first_place": ["owner"],
    "betting_odds_make_playoffs": ["owner"],
    "team_distribution_curves": ["owner"],
    "team_matchup_margin_curves": ["team_owner", "opponent_owner"],
}

ROSTER_OWNERS = """
SELECT rosters.roster_id, users.display_name, users.username
FROM rosters
LEFT JOIN users ON users.user_id = rosters.owner_id
WHERE rosters.league_id = :league_id
"""
WEEK_ROWS = "SELECT * FROM {table} WHERE season = :season AND week = :week"
LEAGUE_WEEK_ROWS = "SELECT * FROM {table} WHERE league_id = :league_id AND week = :week"
LATEST_FIRST = "SELECT * FROM {table} WHERE season = :season AND week = :week ORDER BY created_at DESC, run_id DESC"
# publish.keep_latest_run's choice: the newest created_at, then the highest run_id; a NULL created_at is oldest.
LATEST_RUN_ROWS = """
SELECT * FROM {table} WHERE season = :season AND week = :week AND run_id = (
  SELECT run_id FROM {table} WHERE season = :season AND week = :week
  GROUP BY run_id ORDER BY MAX(created_at) DESC, run_id DESC LIMIT 1
)
"""


@dataclass(frozen=True)
class Week:
    number: int
    data_dir: Path
    league: LeagueSettings
    owners: dict[int, str]  # every roster of the league, named as the lineups step names them
    user_names: set[str]  # the names Flask can map back to a roster
    rows: dict[str, list[sqlite3.Row]]  # the week's rows by table; each odds table at its latest run

    @property
    def is_playoffs(self) -> bool:
        return self.number >= self.league.playoff_week_start

    @property
    def teams_in_play(self) -> set[int]:
        """The rosters the odds must price: the week's matchups (in the playoffs only the teams with a game), or
        every roster while league.db has no matchups for the week."""
        matchups = self.rows["matchups"]
        if not matchups:
            return set(self.owners)
        if self.is_playoffs:
            return {row["roster_id"] for row in matchups if row["matchup_id_number"] is not None}
        return {row["roster_id"] for row in matchups}

    @property
    def league_pairs(self) -> set[tuple[int, int]]:
        rosters_by_game = defaultdict(list)
        for row in self.rows["matchups"]:
            if row["matchup_id_number"] is not None:
                rosters_by_game[row["matchup_id_number"]].append(row["roster_id"])
        return {tuple(sorted(rosters)) for rosters in rosters_by_game.values() if len(rosters) == 2}


def run(ctx: StepContext) -> StepResult:
    week = load_week(ctx)
    checks = []
    for check in CHECKS:
        status, detail = check(week)
        ascii_detail = detail.encode("ascii", "backslashreplace").decode("ascii")
        checks.append({"name": check.__name__, "status": status, "detail": ascii_detail})
        ctx.log(f"{check.__name__}: {status} - {ascii_detail}")

    statuses = Counter(check["status"] for check in checks)
    summary = {"checks": checks, "n_ok": statuses["ok"], "n_warn": statuses["warn"], "n_fail": statuses["fail"]}
    failed = [check["name"] for check in checks if check["status"] == "fail"]
    if failed:
        raise StepFailed(f"{len(failed)} of {len(checks)} checks failed: {', '.join(failed)}", summary)
    warnings = [f"{check['name']}: {check['detail']}" for check in checks if check["status"] == "warn"]
    return StepResult(summary, warnings)


def load_week(ctx: StepContext) -> Week:
    settings = ctx.settings
    params = {"season": settings.season, "week": settings.week, "league_id": settings.league_id}
    league = ctx.db("league")
    odds = ctx.db("odds")
    league_settings = load_league_settings(league, settings.league_id)
    rosters = league.execute(ROSTER_OWNERS, params).fetchall()
    user_names = {row[column] for row in rosters for column in ("display_name", "username") if row[column]}

    rows = {
        "matchups": read_rows(league, "matchups", LEAGUE_WEEK_ROWS, params),
        "projections_rosters": read_rows(league, "projections_rosters", WEEK_ROWS, params),
        "team_lineups": read_rows(ctx.db("projections"), "team_lineups", WEEK_ROWS, params),
        "simulation_runs": read_rows(odds, "simulation_runs", LATEST_FIRST, params),
    }
    for table in RUN_TABLES:
        rows[table] = read_rows(odds, table, LATEST_RUN_ROWS, params)

    return Week(
        number=settings.week,
        data_dir=settings.data_dir,
        league=league_settings,
        owners={row["roster_id"]: row["display_name"] or row["username"] or "Unknown" for row in rosters},
        user_names=user_names | {"Unknown"},
        rows=rows,
    )


def read_rows(conn: sqlite3.Connection, table: str, query: str, params: dict) -> list[sqlite3.Row]:
    """The query's rows, or none while the step that creates the table has not run."""
    created = conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)).fetchone()
    if created is None:
        return []
    return conn.execute(query.format(table=table), params).fetchall()


def listing(items: list[str]) -> str:
    """The first few items and a count of the rest, so a detail stays one short line."""
    if len(items) <= MAX_LISTED:
        return ", ".join(items)
    return f"{', '.join(items[:MAX_LISTED])} and {len(items) - MAX_LISTED} more"


def lineup_slots(week: Week) -> tuple[str, str]:
    filled = {(row["roster_id"], row["slot"]) for row in week.rows["team_lineups"]}
    missing = [
        f"({roster_id}, {slot})"
        for roster_id in sorted(week.owners)
        for slot in week.league.slots
        if (roster_id, slot) not in filled
    ]
    if missing:
        return "fail", f"no lineup row for (roster_id, slot) {listing(missing)}"
    return "ok", f"{len(week.owners)} rosters fill all {len(week.league.slots)} slots"


def lineup_mu(week: Week) -> tuple[str, str]:
    lineups = week.rows["team_lineups"]
    blanks = [
        f"({row['roster_id']}, {row['slot']})" for row in lineups if None in (row["mu"], row["sigma"], row["var"])
    ]
    if blanks:
        return "fail", f"NULL mu, sigma or var at (roster_id, slot) {listing(blanks)}"
    return "ok", f"{len(lineups)} lineup rows have mu, sigma and var"


def probabilities(week: Week) -> tuple[str, str]:
    invalid = []
    n_checked = 0
    for table, columns in PROBABILITY_COLUMNS.items():
        for row, column in product(week.rows[table], columns):
            n_checked += 1
            value = row[column]
            if value is None or not 0 <= value <= 1:
                invalid.append(f"{table}.{column} = {value}")
    if invalid:
        return "fail", f"outside [0, 1]: {listing(invalid)}"
    return "ok", f"{n_checked} probabilities in [0, 1]"


def moneyline_sums(week: Week) -> tuple[str, str]:
    n_sims = {row["run_id"]: row["n_sims"] for row in week.rows["simulation_runs"]}
    moneylines = week.rows["betting_odds_matchup_ml"]
    unbalanced = []
    for row in moneylines:
        if row["run_id"] not in n_sims:
            unbalanced.append(f"{row['matchup']} (no simulation run {row['run_id']})")
            continue
        total = row["team1_win_prob"] + row["team2_win_prob"] + row["ties"] / n_sims[row["run_id"]]
        if abs(total - 1) > SUM_TOLERANCE:
            unbalanced.append(f"{row['matchup']} sums to {total:.6f}")
    if unbalanced:
        return "fail", f"win, loss and tie do not sum to 1: {listing(unbalanced)}"
    return "ok", f"{len(moneylines)} moneylines sum to 1"


def matchup_consistency(week: Week) -> tuple[str, str]:
    """Notebook 08's betting API check: the priced games are league.db's, and each team in play has one of each line."""
    moneylines = week.rows["betting_odds_matchup_ml"]
    odds_pairs = {tuple(sorted((row["team1_id"], row["team2_id"]))) for row in moneylines}
    games = Counter([row["team1_id"] for row in moneylines] + [row["team2_id"] for row in moneylines])
    over_unders = {row["team_id"] for row in week.rows["betting_odds_team_ou"]}
    in_play = week.teams_in_play

    problems = [f"{pair} only in league.db" for pair in sorted(week.league_pairs - odds_pairs)]
    problems += [f"{pair} only in odds.db" for pair in sorted(odds_pairs - week.league_pairs)]
    problems += [f"team {team} has {games[team]} moneylines" for team in sorted(in_play) if games[team] != 1]
    problems += [f"team {team} has no team O/U" for team in sorted(in_play - over_unders)]
    problems += [f"team {team} has a team O/U but no game" for team in sorted(over_unders - in_play)]
    if problems:
        return "fail", listing(problems)
    return "ok", f"{len(odds_pairs)} games and {len(over_unders)} team O/U lines match league.db"


def scorer_markets(week: Week) -> tuple[str, str]:
    in_play = week.teams_in_play
    problems = []
    for table in SCORER_TABLES:
        priced = {row["team_id"] for row in week.rows[table]}
        problems += [f"{table} misses team {team}" for team in sorted(in_play - priced)]
        problems += [f"{table} prices team {team}, which has no game" for team in sorted(priced - in_play)]
    if problems:
        return "fail", listing(problems)
    return "ok", f"highest and lowest scorer price the {len(in_play)} teams in play"


def curves(week: Week) -> tuple[str, str]:
    """Flask compares any two teams, and the odds step writes a margin curve for every ordered pair."""
    owners_in_play = {week.owners[team] for team in week.teams_in_play}
    curve_owners = {row["owner"] for row in week.rows["team_distribution_curves"]}
    margins = {(row["team_owner"], row["opponent_owner"]) for row in week.rows["team_matchup_margin_curves"]}
    missing_margins = set(permutations(owners_in_play, 2)) - margins

    problems = [f"no distribution curve for {owner}" for owner in sorted(owners_in_play - curve_owners)]
    problems += [f"distribution curve for {owner}, who has no game" for owner in sorted(curve_owners - owners_in_play)]
    problems += [f"no margin curve for {team} vs {opponent}" for team, opponent in sorted(missing_margins)]
    if problems:
        return "fail", listing(problems)
    return "ok", f"{len(curve_owners)} distribution curves and {len(margins)} margin curves"


def simulation_draws(week: Week) -> tuple[str, str]:
    simulations = week.rows["simulation_runs"]
    if not simulations:
        return "fail", f"simulation_runs has no run for week {week.number}"
    latest = simulations[0]
    path = week.data_dir / latest["draws_path"]
    if not path.exists():
        return "fail", f"{latest['draws_path']} of run {latest['run_id']} does not exist"
    n_rows = pq.read_metadata(path).num_rows
    n_sims, n_teams = latest["n_sims"], latest["n_teams"]
    if n_rows != n_sims * n_teams:
        return "fail", f"{latest['draws_path']} has {n_rows} rows, not {n_sims} sims x {n_teams} teams"
    return "ok", f"run {latest['run_id']} has {n_sims} sims x {n_teams} teams"


def odds_run(week: Week) -> tuple[str, str]:
    """The odds must come from the latest simulation: a rerun of simulate leaves them stale until odds reruns."""
    simulations = week.rows["simulation_runs"]
    if not simulations:
        return "fail", f"no simulation run for week {week.number} to price from"
    latest = simulations[0]["run_id"]
    stale = [table for table in WEEKLY_ODDS_TABLES if week.rows[table] and week.rows[table][0]["run_id"] != latest]
    if stale:
        runs = sorted({week.rows[table][0]["run_id"] for table in stale})
        return "fail", f"{len(stale)} odds tables are priced from {', '.join(runs)}, not the latest simulation {latest}"
    return "ok", f"priced from the latest simulation {latest}"


def frozen_tables(week: Week) -> tuple[str, str]:
    empty = [table for table in REQUIRED_TABLES if not week.rows[table]]
    if empty:
        return "fail", f"no rows for week {week.number} in {listing(empty)}"
    if week.is_playoffs:
        return "ok", f"{len(REQUIRED_TABLES)} tables have rows for week {week.number}; futures end at the playoffs"
    if not week.rows[STANDINGS_TABLE]:
        return "warn", f"no standings for week {week.number} in {STANDINGS_TABLE}; has the playoffs step run?"
    markets = sum(1 for table in FUTURES_TABLES if week.rows[table])
    return "ok", f"{len(REQUIRED_TABLES)} tables have rows for week {week.number}; {markets} futures markets offered"


def owners(week: Week) -> tuple[str, str]:
    strangers = set()
    n_checked = 0
    for table, columns in OWNER_COLUMNS.items():
        for row, column in product(week.rows[table], columns):
            n_checked += 1
            if row[column] not in week.user_names:
                strangers.add(f"{row[column]!r} in {table}.{column}")
    if strangers:
        return "fail", f"not a league user: {listing(sorted(strangers))}"
    return "ok", f"{n_checked} owner names match league users"


def unique_orderings(week: Week) -> tuple[str, str]:
    """Flask orders team O/U by owner and moneylines by matchup and pairs rows up by position."""
    owner_rows = Counter(row["owner"] for row in week.rows["betting_odds_team_ou"])
    matchup_rows = Counter(row["matchup"] for row in week.rows["betting_odds_matchup_ml"])
    repeats = [f"team O/U owner {owner!r} x{count}" for owner, count in owner_rows.items() if count > 1]
    repeats += [f"moneyline matchup {matchup!r} x{count}" for matchup, count in matchup_rows.items() if count > 1]
    if repeats:
        return "fail", f"repeated: {listing(repeats)}"
    return "ok", f"{len(owner_rows)} team O/U owners and {len(matchup_rows)} matchups are unique"


CHECKS = [
    lineup_slots,
    lineup_mu,
    probabilities,
    moneyline_sums,
    matchup_consistency,
    scorer_markets,
    curves,
    simulation_draws,
    odds_run,
    frozen_tables,
    owners,
    unique_orderings,
]
