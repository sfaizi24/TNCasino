"""League step: mirror the Sleeper league, its players and weekly stats into league.db, and build the season's NFL
schedule, byes included, from ESPN's scoreboard."""

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

import requests

from pipeline.runner import StepContext, StepResult, timestamp, utc_now
from pipeline.settings import sleeper_get
from pipeline.sources.base import POSITIONS
from pipeline.sources.teams import CANONICAL_TEAMS, normalize_team

NAME = "league"

ESPN_SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
REGULAR_SEASON_WEEKS = 18

# Notebook 01's DDL, kept so the 2025 rows already in league.db stay readable.
MIRROR_TABLES = """
CREATE TABLE IF NOT EXISTS leagues (
  league_id TEXT PRIMARY KEY, name TEXT NOT NULL, season TEXT NOT NULL, season_type TEXT, sport TEXT, status TEXT,
  total_rosters INTEGER, roster_positions TEXT, scoring_settings TEXT, settings TEXT, previous_league_id TEXT,
  bracket_id TEXT, draft_id TEXT, avatar TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY, username TEXT, display_name TEXT, avatar TEXT, metadata TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS rosters (
  roster_id INTEGER, league_id TEXT NOT NULL, owner_id TEXT, co_owners TEXT, team_name TEXT, starters TEXT,
  players TEXT, reserve TEXT, taxi TEXT, settings TEXT, metadata TEXT,
  wins INTEGER DEFAULT 0, losses INTEGER DEFAULT 0, ties INTEGER DEFAULT 0, fpts REAL DEFAULT 0,
  fpts_against REAL DEFAULT 0, fpts_decimal REAL DEFAULT 0, fpts_against_decimal REAL DEFAULT 0,
  total_moves INTEGER DEFAULT 0, waiver_position INTEGER, waiver_budget_used INTEGER DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (roster_id, league_id),
  FOREIGN KEY (league_id) REFERENCES leagues(league_id),
  FOREIGN KEY (owner_id) REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_rosters_league ON rosters(league_id);
CREATE INDEX IF NOT EXISTS idx_rosters_owner ON rosters(owner_id);
CREATE TABLE IF NOT EXISTS matchups (
  matchup_id TEXT PRIMARY KEY, league_id TEXT NOT NULL, week INTEGER NOT NULL, roster_id INTEGER NOT NULL,
  matchup_id_number INTEGER, starters TEXT, players TEXT, points REAL DEFAULT 0, custom_points REAL,
  players_points TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (league_id) REFERENCES leagues(league_id),
  FOREIGN KEY (roster_id, league_id) REFERENCES rosters(roster_id, league_id)
);
CREATE INDEX IF NOT EXISTS idx_matchups_league_week ON matchups(league_id, week);
CREATE INDEX IF NOT EXISTS idx_matchups_roster ON matchups(roster_id);
CREATE TABLE IF NOT EXISTS nfl_players (
  player_id TEXT PRIMARY KEY, full_name TEXT, first_name TEXT, last_name TEXT, position TEXT, team TEXT,
  number INTEGER, age INTEGER, height TEXT, weight TEXT, college TEXT, years_exp INTEGER, birth_date TEXT,
  birth_city TEXT, birth_state TEXT, birth_country TEXT, high_school TEXT, status TEXT, active BOOLEAN,
  injury_status TEXT, injury_body_part TEXT, injury_notes TEXT, injury_start_date TEXT,
  practice_participation TEXT, depth_chart_position TEXT, depth_chart_order INTEGER, search_rank INTEGER,
  fantasy_positions TEXT, metadata TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_players_team ON nfl_players(team);
CREATE INDEX IF NOT EXISTS idx_players_position ON nfl_players(position);
CREATE INDEX IF NOT EXISTS idx_players_status ON nfl_players(status);
CREATE TABLE IF NOT EXISTS player_stats (
  stat_id TEXT PRIMARY KEY, player_id TEXT NOT NULL, season TEXT NOT NULL, week INTEGER NOT NULL, team TEXT,
  opponent TEXT,
  pass_att INTEGER DEFAULT 0, pass_cmp INTEGER DEFAULT 0, pass_yd REAL DEFAULT 0, pass_td INTEGER DEFAULT 0,
  pass_int INTEGER DEFAULT 0, pass_2pt INTEGER DEFAULT 0, pass_int_td INTEGER DEFAULT 0, pass_fd INTEGER DEFAULT 0,
  pass_sack INTEGER DEFAULT 0, pass_sack_yd REAL DEFAULT 0,
  rush_att INTEGER DEFAULT 0, rush_yd REAL DEFAULT 0, rush_td INTEGER DEFAULT 0, rush_2pt INTEGER DEFAULT 0,
  rush_fd INTEGER DEFAULT 0, rush_fumble INTEGER DEFAULT 0, rush_fumble_lost INTEGER DEFAULT 0,
  rec_tgt INTEGER DEFAULT 0, rec INTEGER DEFAULT 0, rec_yd REAL DEFAULT 0, rec_td INTEGER DEFAULT 0,
  rec_2pt INTEGER DEFAULT 0, rec_fd INTEGER DEFAULT 0, rec_fumble INTEGER DEFAULT 0,
  rec_fumble_lost INTEGER DEFAULT 0,
  pts_std REAL DEFAULT 0, pts_half_ppr REAL DEFAULT 0, pts_ppr REAL DEFAULT 0,
  st_td INTEGER DEFAULT 0, st_ff INTEGER DEFAULT 0, st_fum_rec INTEGER DEFAULT 0,
  fgm_0_19 INTEGER DEFAULT 0, fgm_20_29 INTEGER DEFAULT 0, fgm_30_39 INTEGER DEFAULT 0,
  fgm_40_49 INTEGER DEFAULT 0, fgm_50p INTEGER DEFAULT 0, fgm INTEGER DEFAULT 0, fga INTEGER DEFAULT 0,
  xpm INTEGER DEFAULT 0, xpa INTEGER DEFAULT 0,
  metadata TEXT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (player_id) REFERENCES nfl_players(player_id),
  UNIQUE(player_id, season, week)
);
CREATE INDEX IF NOT EXISTS idx_stats_player_week ON player_stats(player_id, week);
CREATE INDEX IF NOT EXISTS idx_stats_season_week ON player_stats(season, week);
CREATE TABLE IF NOT EXISTS transactions (
  transaction_id TEXT PRIMARY KEY, league_id TEXT NOT NULL, type TEXT NOT NULL, status TEXT, roster_ids TEXT,
  settings TEXT, metadata TEXT, adds TEXT, drops TEXT, draft_picks TEXT, waiver_budget TEXT, creator TEXT,
  created BIGINT, consenter_ids TEXT, status_updated BIGINT,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (league_id) REFERENCES leagues(league_id)
);
CREATE INDEX IF NOT EXISTS idx_transactions_league ON transactions(league_id);
"""

SCHEDULE_TABLE = """
CREATE TABLE IF NOT EXISTS nfl_schedules (
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  team TEXT NOT NULL,
  opponent TEXT,
  is_home INTEGER NOT NULL,
  is_bye INTEGER NOT NULL,
  game_date TEXT,
  status TEXT,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (season, week, team)
);
"""

# Sleeper fields copied as sent, and the list/dict fields stored as JSON.
LEAGUE_COLUMNS = [
    "league_id",
    "name",
    "season",
    "season_type",
    "sport",
    "status",
    "total_rosters",
    "previous_league_id",
    "bracket_id",
    "draft_id",
    "avatar",
]
LEAGUE_JSON_COLUMNS = ["roster_positions", "scoring_settings", "settings"]
USER_COLUMNS = ["user_id", "username", "display_name", "avatar"]
USER_JSON_COLUMNS = ["metadata"]
ROSTER_JSON_COLUMNS = ["co_owners", "starters", "players", "reserve", "taxi", "settings", "metadata"]
ROSTER_COUNTERS = [
    "wins",
    "losses",
    "ties",
    "fpts",
    "fpts_against",
    "fpts_decimal",
    "fpts_against_decimal",
    "total_moves",
    "waiver_budget_used",
]
MATCHUP_JSON_COLUMNS = ["starters", "players", "players_points"]
TRANSACTION_COLUMNS = ["transaction_id", "type", "status", "creator", "created", "status_updated"]
TRANSACTION_JSON_COLUMNS = [
    "roster_ids",
    "settings",
    "metadata",
    "adds",
    "drops",
    "draft_picks",
    "waiver_budget",
    "consenter_ids",
]
PLAYER_COLUMNS = [
    "player_id",
    "full_name",
    "first_name",
    "last_name",
    "position",
    "team",
    "number",
    "age",
    "height",
    "weight",
    "college",
    "years_exp",
    "birth_date",
    "birth_city",
    "birth_state",
    "birth_country",
    "high_school",
    "status",
    "active",
    "injury_status",
    "injury_body_part",
    "injury_notes",
    "injury_start_date",
    "practice_participation",
    "depth_chart_position",
    "depth_chart_order",
    "search_rank",
]
PLAYER_JSON_COLUMNS = ["fantasy_positions", "metadata"]
STAT_COLUMNS = [
    "pass_att",
    "pass_cmp",
    "pass_yd",
    "pass_td",
    "pass_int",
    "pass_2pt",
    "pass_int_td",
    "pass_fd",
    "pass_sack",
    "pass_sack_yd",
    "rush_att",
    "rush_yd",
    "rush_td",
    "rush_2pt",
    "rush_fd",
    "rush_fumble",
    "rush_fumble_lost",
    "rec_tgt",
    "rec",
    "rec_yd",
    "rec_td",
    "rec_2pt",
    "rec_fd",
    "rec_fumble",
    "rec_fumble_lost",
    "pts_std",
    "pts_half_ppr",
    "pts_ppr",
    "st_td",
    "st_ff",
    "st_fum_rec",
    "fgm_0_19",
    "fgm_20_29",
    "fgm_30_39",
    "fgm_40_49",
    "fgm_50p",
    "fgm",
    "fga",
    "xpm",
    "xpa",
]

OWNER_CHANGES = """
SELECT roster.roster_id,
       COALESCE(previous_user.display_name, previous_user.username, 'Unknown') AS previous_owner,
       COALESCE(owner_user.display_name, owner_user.username, 'Unknown') AS owner
FROM rosters AS roster
JOIN rosters AS previous ON previous.roster_id = roster.roster_id AND previous.league_id = ?
LEFT JOIN users AS previous_user ON previous_user.user_id = previous.owner_id
LEFT JOIN users AS owner_user ON owner_user.user_id = roster.owner_id
WHERE roster.league_id = ? AND roster.owner_id IS NOT previous.owner_id
ORDER BY roster.roster_id
"""


@dataclass(frozen=True)
class LeagueSettings:
    roster_positions: list[str]  # starting slots in Sleeper's order, bench removed
    playoff_teams: int
    playoff_week_start: int
    waiver_type: int  # 2 = FAAB
    waiver_budget: int
    num_teams: int
    previous_league_id: str | None

    @property
    def slots(self) -> dict[str, str]:
        """Slot name -> position, numbering positions that repeat: QB, RB1, RB2, WR1, WR2, TE, FLEX, K, DEF."""
        totals = Counter(self.roster_positions)
        seen = Counter()
        slots = {}
        for position in self.roster_positions:
            seen[position] += 1
            name = position if totals[position] == 1 else f"{position}{seen[position]}"
            slots[name] = position
        return slots


def run(ctx: StepContext) -> StepResult:
    settings = ctx.settings
    league_id = settings.league_id
    weeks = range(1, settings.week + 1)
    played_weeks = range(1, settings.week)

    league = sleeper_get(f"/league/{league_id}")
    users = sleeper_get(f"/league/{league_id}/users")
    rosters = sleeper_get(f"/league/{league_id}/rosters")
    matchups = {week: sleeper_get(f"/league/{league_id}/matchups/{week}") for week in weeks}
    transactions = [row for week in weeks for row in sleeper_get(f"/league/{league_id}/transactions/{week}")]
    stats = {week: sleeper_get(f"/stats/nfl/regular/{settings.season}/{week}") for week in played_weeks}
    players = player_rows(sleeper_get("/players/nfl"))
    schedule = fetch_schedule(settings.season, timestamp(utc_now()))

    conn = ctx.db("league")
    conn.executescript(MIRROR_TABLES + SCHEDULE_TABLE)
    write_league(conn, league, users, rosters)
    write_matchups(conn, league_id, matchups)
    insert_rows(conn, "transactions", [transaction_row(league_id, row) for row in transactions], replace=True)
    write_player_stats(conn, settings.season, stats)
    conn.execute("DELETE FROM nfl_players")
    insert_rows(conn, "nfl_players", players)
    conn.execute("DELETE FROM nfl_schedules WHERE season = ?", (settings.season,))
    insert_rows(conn, "nfl_schedules", schedule)
    conn.commit()

    byes = sorted(row["team"] for row in schedule if row["week"] == settings.week and row["is_bye"])
    owner_changes = find_owner_changes(conn, league_id, league["previous_league_id"])
    warnings = [f"Sleeper returned no player stats for week {week}" for week, rows in stats.items() if not rows]
    ctx.log(f"league {league_id}: {len(rosters)} rosters, {len(users)} users, {len(transactions)} transactions")
    ctx.log(f"{len(players)} players, {len(schedule)} schedule rows")
    ctx.log(f"byes in week {settings.week}: {', '.join(byes) or 'none'}")
    for change in owner_changes:
        ctx.log(f"roster {change['roster_id']} has a new owner since last season")

    summary = {
        "n_users": len(users),
        "n_rosters": len(rosters),
        "n_matchup_rows": sum(len(rows) for rows in matchups.values()),
        "n_transactions": len(transactions),
        "n_players": len(players),
        "n_stat_rows": sum(len(rows) for rows in stats.values()),
        "n_schedule_rows": len(schedule),
        "byes_this_week": byes,
        "owner_changes": owner_changes,
    }
    return StepResult(summary, warnings)


def load_league_settings(conn: sqlite3.Connection, league_id: str) -> LeagueSettings:
    row = conn.execute(
        "SELECT roster_positions, settings, previous_league_id FROM leagues WHERE league_id = ?", (league_id,)
    ).fetchone()
    if row is None:
        raise LookupError(f"league {league_id} is not in league.db; run the league step first")
    sleeper_settings = json.loads(row["settings"])
    return LeagueSettings(
        roster_positions=[position for position in json.loads(row["roster_positions"]) if position != "BN"],
        playoff_teams=sleeper_settings["playoff_teams"],
        playoff_week_start=sleeper_settings["playoff_week_start"],
        waiver_type=sleeper_settings["waiver_type"],
        waiver_budget=sleeper_settings["waiver_budget"],
        num_teams=sleeper_settings["num_teams"],
        previous_league_id=row["previous_league_id"],
    )


def find_owner_changes(conn: sqlite3.Connection, league_id: str, previous_league_id: str | None) -> list[dict]:
    """Rosters whose owner differs from the same roster_id last season; empty when last season is not stored."""
    return [dict(row) for row in conn.execute(OWNER_CHANGES, (previous_league_id, league_id))]


def fetch_schedule(season: int, updated_at: str) -> list[dict]:
    rows = []
    for week in range(1, REGULAR_SEASON_WEEKS + 1):
        rows.extend(parse_scoreboard(fetch_scoreboard(season, week), season, updated_at))
    return rows


def fetch_scoreboard(season: int, week: int) -> dict:
    # ESPN answers 403 to a browser-like User-Agent, so keep requests' default.
    params = {"seasontype": 2, "week": week, "dates": season}
    response = requests.get(ESPN_SCOREBOARD, params=params, timeout=30)
    response.raise_for_status()
    return response.json()


def parse_scoreboard(payload: dict, season: int, updated_at: str) -> list[dict]:
    """Two rows per game, one per team, then a bye row for every team without a game that week."""
    week = payload["week"]["number"]
    rows = []
    for event in payload["events"]:
        competitors = event["competitions"][0]["competitors"]
        teams = {
            competitor["homeAway"]: normalize_team(competitor["team"]["abbreviation"]) for competitor in competitors
        }
        kickoff = datetime.fromisoformat(event["date"]).isoformat(timespec="seconds")
        for side, other_side in (("home", "away"), ("away", "home")):
            rows.append(
                {
                    "season": season,
                    "week": week,
                    "team": teams[side],
                    "opponent": teams[other_side],
                    "is_home": int(side == "home"),
                    "is_bye": 0,
                    "game_date": kickoff,
                    "status": event["status"]["type"]["name"],
                    "updated_at": updated_at,
                }
            )

    playing = {row["team"] for row in rows}
    for team in sorted(CANONICAL_TEAMS - playing):
        rows.append(
            {
                "season": season,
                "week": week,
                "team": team,
                "opponent": None,
                "is_home": 0,
                "is_bye": 1,
                "game_date": None,
                "status": None,
                "updated_at": updated_at,
            }
        )
    return rows


def write_league(conn: sqlite3.Connection, league: dict, users: list[dict], rosters: list[dict]) -> None:
    league_id = league["league_id"]
    insert_rows(conn, "leagues", [mirror_row(league, LEAGUE_COLUMNS, LEAGUE_JSON_COLUMNS)], replace=True)
    # users has no league column: upsert this season's members and keep earlier owners resolvable.
    insert_rows(conn, "users", [mirror_row(user, USER_COLUMNS, USER_JSON_COLUMNS) for user in users], replace=True)
    conn.execute("DELETE FROM rosters WHERE league_id = ?", (league_id,))
    insert_rows(conn, "rosters", [roster_row(league_id, roster) for roster in rosters])


def write_matchups(conn: sqlite3.Connection, league_id: str, matchups_by_week: dict[int, list[dict]]) -> None:
    for week, matchups in matchups_by_week.items():
        conn.execute("DELETE FROM matchups WHERE league_id = ? AND week = ?", (league_id, week))
        insert_rows(conn, "matchups", [matchup_row(league_id, week, matchup) for matchup in matchups])


def write_player_stats(conn: sqlite3.Connection, season: int, stats_by_week: dict[int, dict]) -> None:
    for week, stats in stats_by_week.items():
        conn.execute("DELETE FROM player_stats WHERE season = ? AND week = ?", (season, week))
        rows = [stat_row(season, week, player_id, player_stats) for player_id, player_stats in stats.items()]
        insert_rows(conn, "player_stats", rows)


def insert_rows(conn: sqlite3.Connection, table: str, rows: list[dict], replace: bool = False) -> None:
    if not rows:
        return
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    columns = ", ".join(rows[0])
    placeholders = ", ".join(f":{column}" for column in rows[0])
    conn.executemany(f"{verb} INTO {table} ({columns}) VALUES ({placeholders})", rows)


def mirror_row(record: dict, columns: list[str], json_columns: list[str]) -> dict:
    row = {column: record.get(column) for column in columns}
    for column in json_columns:
        row[column] = json.dumps(record.get(column))
    return row


def roster_row(league_id: str, roster: dict) -> dict:
    row = mirror_row(roster, ["roster_id", "owner_id"], ROSTER_JSON_COLUMNS)
    row["league_id"] = league_id
    row["team_name"] = (roster["metadata"] or {}).get("team_name")
    for column in ROSTER_COUNTERS:
        row[column] = roster["settings"].get(column, 0)
    row["waiver_position"] = roster["settings"].get("waiver_position")
    return row


def matchup_row(league_id: str, week: int, matchup: dict) -> dict:
    row = mirror_row(matchup, ["roster_id", "points", "custom_points"], MATCHUP_JSON_COLUMNS)
    row["matchup_id"] = f"{league_id}_{week}_{matchup['roster_id']}"
    row["league_id"] = league_id
    row["week"] = week
    row["matchup_id_number"] = matchup["matchup_id"]
    return row


def transaction_row(league_id: str, transaction: dict) -> dict:
    row = mirror_row(transaction, TRANSACTION_COLUMNS, TRANSACTION_JSON_COLUMNS)
    row["league_id"] = league_id
    return row


def stat_row(season: int, week: int, player_id: str, stats: dict) -> dict:
    row = {"stat_id": f"{player_id}_{season}_{week}", "player_id": player_id, "season": season, "week": week}
    for column in STAT_COLUMNS:
        row[column] = stats.get(column, 0)
    return row


def player_rows(players: dict[str, dict]) -> list[dict]:
    """Fantasy positions only, as notebook 01 kept them; DEF entries stay as Sleeper sends them."""
    return [
        mirror_row(player, PLAYER_COLUMNS, PLAYER_JSON_COLUMNS)
        for player in players.values()
        if player.get("position") in POSITIONS
    ]
