"""Lineups step: start each roster's best available players by projected points, fill the slots that injuries and
byes leave empty from the waiver wire, and write the week's lineups, team totals and roster statuses."""

import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass

from pipeline.runner import StepContext, StepResult, timestamp, utc_now
from pipeline.steps.league import insert_rows, load_league_settings
from pipeline.waivers import Assignment, Hole, ProjectedPlayer, allocate

NAME = "lineups"

# Sleeper's codes for players who will not play; it spells Suspended "Sus".
EXCLUDED_INJURIES = frozenset({"Out", "IR", "PUP", "Sus", "Doubtful"})
MIN_FREE_AGENT_SOURCES = 2
POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]

# An unresolved hole scores nothing; its row takes the slot's position.
EMPTY_SLOT = {
    "sleeper_player_id": None,
    "player_name": None,
    "nfl_team": None,
    "mu": 0.0,
    "sigma": 0.0,
    "var": 0.0,
    "n_sources": 0,
    "is_replacement": 0,
}

LINEUP_TABLES = """
CREATE TABLE IF NOT EXISTS team_lineups (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  roster_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  record TEXT NOT NULL,
  slot TEXT NOT NULL,                 -- QB, RB1, RB2, WR1, WR2, TE, FLEX, K, DEF
  sleeper_player_id TEXT,             -- NULL only for an unresolved hole
  player_name TEXT,
  position TEXT NOT NULL,             -- player's position; the slot's position for an unresolved hole
  nfl_team TEXT,
  mu REAL NOT NULL,
  sigma REAL NOT NULL,
  var REAL NOT NULL,
  n_sources INTEGER NOT NULL,
  is_replacement INTEGER NOT NULL DEFAULT 0,
  timestamp TEXT NOT NULL,
  PRIMARY KEY (season, week, roster_id, slot)
);

CREATE TABLE IF NOT EXISTS team_projections_summary (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  roster_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  owner TEXT NOT NULL,
  record TEXT NOT NULL,
  total_mu REAL NOT NULL,
  combined_sigma REAL NOT NULL,
  total_var REAL NOT NULL,
  waiver_pickups INTEGER NOT NULL,
  timestamp TEXT NOT NULL,
  PRIMARY KEY (season, week, roster_id)
);
"""

ROSTER_TABLE = """
CREATE TABLE IF NOT EXISTS projections_rosters (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  roster_id INTEGER NOT NULL,
  team_name TEXT NOT NULL,
  sleeper_player_id TEXT NOT NULL,
  first_name TEXT,
  last_name TEXT,
  position TEXT,
  nfl_team TEXT,
  mu REAL NOT NULL,                   -- 0 when unprojected
  var REAL NOT NULL,                  -- 0 when unprojected
  starting_status INTEGER NOT NULL,   -- 1 in the optimal lineup, else 0 (Flask treats truthy as starter)
  roster_status TEXT NOT NULL,        -- starter | bench | out | bye | unprojected
  timestamp TEXT NOT NULL,
  PRIMARY KEY (season, week, sleeper_player_id)
);
"""

ROSTERS_WITH_OWNERS = """
SELECT rosters.roster_id, rosters.team_name, rosters.wins, rosters.losses, rosters.players, rosters.reserve,
       rosters.taxi, rosters.waiver_position, rosters.waiver_budget_used, users.display_name, users.username
FROM rosters
LEFT JOIN users ON users.user_id = rosters.owner_id
WHERE rosters.league_id = ?
ORDER BY rosters.roster_id
"""


@dataclass(frozen=True)
class Team:
    roster_id: int
    team_name: str
    owner: str
    record: str
    players: list[str]
    inactive: frozenset[str]  # reserve (IR) and taxi players, who cannot start
    faab_remaining: int
    waiver_position: int

    @property
    def columns(self) -> dict:
        return {"roster_id": self.roster_id, "team_name": self.team_name, "owner": self.owner, "record": self.record}


def run(ctx: StepContext) -> StepResult:
    settings = ctx.settings
    league_conn = ctx.db("league")
    league = load_league_settings(league_conn, settings.league_id)
    slots = league.slots
    teams = load_teams(league_conn, settings.league_id, league.waiver_budget)
    players = load_players(league_conn)
    byes = load_byes(league_conn, settings.season, settings.week)
    projections = load_projections(ctx.db("projections"), settings.season, settings.week)

    statuses = {}
    lineups = {}
    for team in teams:
        statuses[team.roster_id] = roster_statuses(team, players, projections, byes)
        available = [player_id for player_id, status in statuses[team.roster_id].items() if status == "bench"]
        candidates = [projected_player(players[player_id], projections[player_id]) for player_id in available]
        lineups[team.roster_id] = pick_lineup(slots, candidates)

    holes = [
        Hole(team.roster_id, slot, position, team.faab_remaining, team.waiver_position)
        for team in teams
        for slot, position in slots.items()
        if slot not in lineups[team.roster_id]
    ]
    rostered = {player_id for team in teams for player_id in team.players}
    pool = free_agent_pool(players, projections, byes, rostered)
    caps = median_starter_mu(lineups, slots)
    assignments = allocate(holes, pool, caps)
    assignments_by_slot = {(assignment.hole.roster_id, assignment.hole.slot): assignment for assignment in assignments}

    lineup_rows = []
    total_rows = []
    roster_rows = []
    for team in teams:
        lineup = lineups[team.roster_id]
        team_lineup = team_lineup_rows(team, slots, lineup, assignments_by_slot)
        lineup_rows += team_lineup
        total_rows.append(team_total_row(team, team_lineup))
        roster_rows += team_roster_rows(team, statuses[team.roster_id], lineup, players, projections)
    write_week(ctx, lineup_rows, total_rows, roster_rows)

    ctx.log(f"{len(teams)} rosters, {len(holes)} empty slots after picking lineups, {len(pool)} free agents")
    for assignment in assignments:
        if assignment.free_agent is not None:
            hole = assignment.hole
            pickup_id = assignment.free_agent.sleeper_player_id
            ctx.log(f"roster {hole.roster_id} {hole.slot}: free agent {pickup_id} at {assignment.mu:.2f}")
    warnings = [
        f"roster {team.roster_id} lists player {player_id}, who is not in nfl_players"
        for team in teams
        for player_id in team.players
        if player_id not in players
    ]
    warnings += [
        f"no free agent left for {assignment.hole.slot} on roster {assignment.hole.roster_id}"
        for assignment in assignments
        if assignment.free_agent is None
    ]
    return StepResult(build_summary(teams, total_rows, assignments, pool, caps), warnings)


def load_teams(conn: sqlite3.Connection, league_id: str, waiver_budget: int) -> list[Team]:
    teams = []
    for row in conn.execute(ROSTERS_WITH_OWNERS, (league_id,)):
        reserve = json.loads(row["reserve"]) or []
        taxi = json.loads(row["taxi"]) or []
        teams.append(
            Team(
                roster_id=row["roster_id"],
                team_name=row["team_name"] or f"Team {row['roster_id']}",
                owner=row["display_name"] or row["username"] or "Unknown",
                record=f"{row['wins']}-{row['losses']}",
                players=json.loads(row["players"]),
                inactive=frozenset(reserve + taxi),
                faab_remaining=waiver_budget - row["waiver_budget_used"],
                waiver_position=row["waiver_position"],
            )
        )
    return teams


def load_players(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT player_id, first_name, last_name, position, team, injury_status, fantasy_positions FROM nfl_players"
    )
    return {row["player_id"]: dict(row) for row in rows}


def load_byes(conn: sqlite3.Connection, season: int, week: int) -> set[str]:
    rows = conn.execute("SELECT team FROM nfl_schedules WHERE season = ? AND week = ? AND is_bye = 1", (season, week))
    return {row["team"] for row in rows}


def load_projections(conn: sqlite3.Connection, season: int, week: int) -> dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT * FROM player_week_stats WHERE season = ? AND week = ?", (season, week)).fetchall()
    if not rows:
        raise LookupError(f"player_week_stats has no rows for {season} week {week}; run the stats step first")
    return {row["sleeper_player_id"]: row for row in rows}


def roster_statuses(team: Team, players: dict, projections: dict, byes: set[str]) -> dict[str, str]:
    """Each rostered player's status before the lineup is picked: bench means he can start."""
    statuses = {}
    for player_id in team.players:
        if player_id in team.inactive:
            statuses[player_id] = "out"
        elif player_id not in players:
            statuses[player_id] = "unprojected"
        else:
            statuses[player_id] = unavailable_reason(players[player_id], projections.get(player_id), byes) or "bench"
    return statuses


def unavailable_reason(player: dict, projection: sqlite3.Row | None, byes: set[str]) -> str | None:
    """Why a player cannot start this week (out, bye or unprojected), or None when he can."""
    if player["injury_status"] in EXCLUDED_INJURIES:
        return "out"
    if player["team"] in byes:
        return "bye"
    if projection is None:
        return "unprojected"
    return None


def projected_player(player: dict, projection: sqlite3.Row) -> ProjectedPlayer:
    fantasy_positions = json.loads(player["fantasy_positions"]) or [player["position"]]
    return ProjectedPlayer(
        sleeper_player_id=player["player_id"],
        player_name=projection["player_name"],
        position=projection["position"],
        positions=frozenset(fantasy_positions),
        nfl_team=projection["team"],
        mu=projection["mu"],
        sigma=projection["sigma"],
        var=projection["var"],
        n_sources=projection["n_sources"],
    )


def pick_lineup(slots: dict[str, str], candidates: list[ProjectedPlayer]) -> dict[str, ProjectedPlayer]:
    """Greedy by mu: each fixed slot in order takes the best candidate left who may start there, then FLEX does.

    Slots nobody can fill are left out of the result.
    """
    remaining = sorted(candidates, key=lambda player: player.mu, reverse=True)
    fixed_slots = [slot for slot, position in slots.items() if position != "FLEX"]
    flex_slots = [slot for slot, position in slots.items() if position == "FLEX"]
    lineup = {}
    for slot in fixed_slots + flex_slots:
        player = next((player for player in remaining if player.can_play(slots[slot])), None)
        if player is not None:
            lineup[slot] = player
            remaining.remove(player)
    return lineup


def free_agent_pool(players: dict, projections: dict, byes: set[str], rostered: set[str]) -> list[ProjectedPlayer]:
    """Unrostered players who can start this week and whom at least two sources project."""
    pool = []
    for player_id, projection in projections.items():
        player = players.get(player_id)
        if player is None or player_id in rostered or projection["n_sources"] < MIN_FREE_AGENT_SOURCES:
            continue
        if unavailable_reason(player, projection, byes) is None:
            pool.append(projected_player(player, projection))
    return pool


def median_starter_mu(lineups: dict[int, dict[str, ProjectedPlayer]], slots: dict[str, str]) -> dict[str, float]:
    """The waiver cap for each slot position: the median projection of the league's starters there."""
    starter_mus = defaultdict(list)
    for lineup in lineups.values():
        for slot, player in lineup.items():
            starter_mus[slots[slot]].append(player.mu)
    return {position: statistics.median(mus) for position, mus in starter_mus.items()}


def team_lineup_rows(
    team: Team,
    slots: dict[str, str],
    lineup: dict[str, ProjectedPlayer],
    assignments_by_slot: dict[tuple[int, str], Assignment],
) -> list[dict]:
    """One row per slot: the roster's own starter, else its waiver pickup, else an empty slot."""
    rows = []
    for slot, position in slots.items():
        assignment = assignments_by_slot.get((team.roster_id, slot))
        if slot in lineup:
            filled = slot_columns(lineup[slot], lineup[slot].mu, is_replacement=0)
        elif assignment.free_agent is not None:
            filled = slot_columns(assignment.free_agent, assignment.mu, is_replacement=1)
        else:
            filled = EMPTY_SLOT | {"position": position}
        rows.append(team.columns | {"slot": slot} | filled)
    return rows


def slot_columns(player: ProjectedPlayer, mu: float, is_replacement: int) -> dict:
    return {
        "sleeper_player_id": player.sleeper_player_id,
        "player_name": player.player_name,
        "position": player.position,
        "nfl_team": player.nfl_team,
        "mu": mu,
        "sigma": player.sigma,
        "var": player.var,
        "n_sources": player.n_sources,
        "is_replacement": is_replacement,
    }


def team_total_row(team: Team, lineup_rows: list[dict]) -> dict:
    total_var = sum(row["var"] for row in lineup_rows)
    return team.columns | {
        "total_mu": sum(row["mu"] for row in lineup_rows),
        "combined_sigma": math.sqrt(total_var),
        "total_var": total_var,
        "waiver_pickups": sum(row["is_replacement"] for row in lineup_rows),
    }


def team_roster_rows(
    team: Team, statuses: dict[str, str], lineup: dict[str, ProjectedPlayer], players: dict, projections: dict
) -> list[dict]:
    """Every rostered player with his projection, or 0 without one; the lineup's players become starters."""
    starters = {player.sleeper_player_id for player in lineup.values()}
    rows = []
    for player_id, status in statuses.items():
        player = players.get(player_id, {})
        projection = projections.get(player_id)
        rows.append(
            {
                "roster_id": team.roster_id,
                "team_name": team.team_name,
                "sleeper_player_id": player_id,
                "first_name": player.get("first_name"),
                "last_name": player.get("last_name"),
                "position": player.get("position"),
                "nfl_team": player.get("team"),
                "mu": projection["mu"] if projection is not None else 0.0,
                "var": projection["var"] if projection is not None else 0.0,
                "starting_status": int(player_id in starters),
                "roster_status": "starter" if player_id in starters else status,
            }
        )
    return rows


def write_week(ctx: StepContext, lineup_rows: list[dict], total_rows: list[dict], roster_rows: list[dict]) -> None:
    season = ctx.settings.season
    week = ctx.settings.week
    written_at = timestamp(utc_now())
    projections_conn = ctx.db("projections")
    projections_conn.executescript(LINEUP_TABLES)
    replace_week(projections_conn, "team_lineups", season, week, lineup_rows, written_at)
    replace_week(projections_conn, "team_projections_summary", season, week, total_rows, written_at)
    projections_conn.commit()
    league_conn = ctx.db("league")
    league_conn.executescript(ROSTER_TABLE)
    replace_week(league_conn, "projections_rosters", season, week, roster_rows, written_at)
    league_conn.commit()


def replace_week(
    conn: sqlite3.Connection, table: str, season: int, week: int, rows: list[dict], written_at: str
) -> None:
    conn.execute(f"DELETE FROM {table} WHERE season = ? AND week = ?", (season, week))
    insert_rows(conn, table, [{"season": season, "week": week, **row, "timestamp": written_at} for row in rows])


def build_summary(
    teams: list[Team],
    total_rows: list[dict],
    assignments: list[Assignment],
    pool: list[ProjectedPlayer],
    caps: dict[str, float],
) -> dict:
    owners = {team.roster_id: team.owner for team in teams}
    holes = defaultdict(list)
    unresolved = []
    for assignment in assignments:
        roster_id = assignment.hole.roster_id
        slot = assignment.hole.slot
        if assignment.free_agent is None:
            unresolved.append({"roster_id": roster_id, "owner": owners[roster_id], "slot": slot})
            replacement = None
        else:
            replacement = assignment.free_agent.player_name
        holes[roster_id].append({"slot": slot, "replacement": replacement, "mu": round(assignment.mu, 2)})
    pool_sizes = Counter(player.position for player in pool)
    teams_summary = [
        {
            "roster_id": row["roster_id"],
            "owner": row["owner"],
            "total_mu": round(row["total_mu"], 2),
            "holes": holes[row["roster_id"]],
        }
        for row in total_rows
    ]
    return {
        "teams": teams_summary,
        "n_replacements": sum(row["waiver_pickups"] for row in total_rows),
        "pool_sizes": {position: pool_sizes[position] for position in POSITION_ORDER},
        "cap_by_position": {position: round(cap, 2) for position, cap in caps.items()},
        "unresolved": unresolved,
    }
