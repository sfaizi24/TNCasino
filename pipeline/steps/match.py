"""Link each projection row to a Sleeper player id and record which rule made the link.

Rules run in order and the first hit wins: Sleeper's own ids, defenses by team code, a few
hand-checked names, then name matches that loosen one step at a time. A name rule links a row only
when it narrows to a single player, so an ambiguous name stays unmatched rather than linking the
wrong player; the loosest, last name and first initial, also needs the team to agree. A player
counts at every position in his Sleeper fantasy_positions, which covers fullbacks projected as
running backs and two-way players.
"""

import json
import sqlite3
from collections import defaultdict

from pipeline.names import normalize_name
from pipeline.runner import StepContext, StepResult, timestamp, utc_now

NAME = "match"

SLEEPER_WEBSITE = "sleeper.com"
METHODS = ["external_id", "def_team", "hardcoded", "exact_team", "exact", "last_initial"]

# (first, last) as the sources spell the player after cleaning -> Sleeper player id.
HARDCODED_MATCHES = {
    ("Bam", "Knight"): "8122",  # Zonovan Knight; notebook 04 pointed at 6945, which is Antonio Gibson.
}

PROJECTIONS_WITH_SLEEPER_DDL = """
CREATE TABLE IF NOT EXISTS projections_with_sleeper (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source_website TEXT NOT NULL,
  season INTEGER NOT NULL,
  week INTEGER NOT NULL,
  player_first_name TEXT NOT NULL,
  player_last_name TEXT NOT NULL,
  position TEXT NOT NULL,
  team TEXT,
  projected_points REAL NOT NULL,
  external_id TEXT,
  sleeper_player_id TEXT,
  match_method TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (source_website, season, week, player_first_name, player_last_name, position)
);
"""

INSERT_MATCH = """
INSERT INTO projections_with_sleeper (source_website, season, week, player_first_name, player_last_name, position,
                                      team, projected_points, external_id, sleeper_player_id, match_method, created_at)
VALUES (:source_website, :season, :week, :player_first_name, :player_last_name, :position,
        :team, :projected_points, :external_id, :sleeper_player_id, :match_method, :created_at)
"""


class SleeperPlayers:
    """nfl_players indexed for the match rules, each player filed under every position he can play."""

    def __init__(self, rows: list[sqlite3.Row]):
        self.ids = set()
        self.defenses = {}
        self.by_name = defaultdict(list)
        self.by_initial = defaultdict(list)
        for row in rows:
            self.ids.add(row["player_id"])
            first, last = normalize_name(row["first_name"] or "", row["last_name"] or "")
            for position in playable_positions(row):
                if position == "DEF":
                    self.defenses[row["team"]] = row["player_id"]
                else:
                    self.by_name[(first, last, position)].append(row)
                    self.by_initial[(last, first[:1], position)].append(row)


def run(ctx: StepContext) -> StepResult:
    season, week = ctx.settings.season, ctx.settings.week
    player_rows = (
        ctx.db("league")
        .execute("SELECT player_id, first_name, last_name, position, team, fantasy_positions FROM nfl_players")
        .fetchall()
    )
    players = SleeperPlayers(player_rows)
    warnings = [
        f"hardcoded match {first} {last} -> {player_id} is not in nfl_players; skipped"
        for (first, last), player_id in HARDCODED_MATCHES.items()
        if player_id not in players.ids
    ]

    conn = ctx.db("projections")
    conn.executescript(PROJECTIONS_WITH_SLEEPER_DDL)
    rows = conn.execute(
        "SELECT * FROM projections WHERE season = ? AND week = ? ORDER BY id", (season, week)
    ).fetchall()
    if not rows:
        raise RuntimeError(f"no projections for season {season} week {week}; run the scrape step first")

    created_at = timestamp(utc_now())
    records = []
    for row in rows:
        sleeper_player_id, match_method = match_row(row, players)
        record = dict(row)
        record.update(sleeper_player_id=sleeper_player_id, match_method=match_method, created_at=created_at)
        records.append(record)
    with conn:
        conn.execute("DELETE FROM projections_with_sleeper WHERE season = ? AND week = ?", (season, week))
        conn.executemany(INSERT_MATCH, records)

    summary = summarize(records)
    ctx.log(f"matched {summary['n_matched']} of {summary['n_rows']} rows ({summary['match_rate']:.1%})")
    return StepResult(summary, warnings)


def playable_positions(row: sqlite3.Row) -> set[str]:
    positions = {row["position"]}
    if row["fantasy_positions"]:
        positions.update(json.loads(row["fantasy_positions"]))
    return positions


def match_row(row: sqlite3.Row, players: SleeperPlayers) -> tuple[str | None, str | None]:
    if row["source_website"] == SLEEPER_WEBSITE and row["external_id"] in players.ids:
        return row["external_id"], "external_id"

    if row["position"] == "DEF":
        player_id = players.defenses.get(row["team"])
        if player_id is None:
            return None, None
        return player_id, "def_team"

    hardcoded_id = HARDCODED_MATCHES.get((row["player_first_name"], row["player_last_name"]))
    if hardcoded_id in players.ids:
        return hardcoded_id, "hardcoded"

    first, last = normalize_name(row["player_first_name"], row["player_last_name"])
    named = players.by_name.get((first, last, row["position"]), [])
    on_team = [player for player in named if player["team"] == row["team"]]
    if row["team"] is not None and len(on_team) == 1:
        return on_team[0]["player_id"], "exact_team"
    if len(named) == 1:
        return named[0]["player_id"], "exact"

    # An initial reaches retired namesakes (ESPN's "Josh Allen, RB" found Javorius Allen), so it
    # needs the source's team to agree whenever the source gives one.
    initialled = players.by_initial.get((last, first[:1], row["position"]), [])
    if row["team"] is not None:
        initialled = [player for player in initialled if player["team"] == row["team"]]
    if len(initialled) == 1:
        return initialled[0]["player_id"], "last_initial"
    return None, None


def summarize(records: list[dict]) -> dict:
    by_method = dict.fromkeys(METHODS, 0)
    unmatched = []
    for record in records:
        if record["match_method"] is None:
            unmatched.append(record)
        else:
            by_method[record["match_method"]] += 1

    n_matched = len(records) - len(unmatched)
    unmatched.sort(key=lambda record: record["projected_points"], reverse=True)
    unmatched_top = [
        {
            "name": f"{record['player_first_name']} {record['player_last_name']}",
            "position": record["position"],
            "source": record["source_website"],
            "points": round(record["projected_points"], 2),
        }
        for record in unmatched[:10]
    ]
    return {
        "n_rows": len(records),
        "n_matched": n_matched,
        "match_rate": round(n_matched / len(records), 3),
        "by_method": by_method,
        "unmatched_top": unmatched_top,
    }
