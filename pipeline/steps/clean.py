"""Clean the week's scraped projections in place so the match step compares like with like.

Names lose trailing injury tags and suffixes but keep their case and punctuation, because they are
also display names. Legacy positions and team codes become canonical, and every defense takes
Sleeper's (city, nickname) form. Cleaning can make two rows from one source identical; of those,
the row with the highest projected_points is kept.
"""

import sqlite3

from pipeline.names import strip_suffix
from pipeline.runner import StepContext, StepResult
from pipeline.sources.teams import DEF_NAMES, normalize_team, team_from_def_name

NAME = "clean"

INJURY_TAGS = frozenset({"Q", "O", "D", "IR", "SUS", "PUP"})

PROJECTIONS_DDL = """
CREATE TABLE IF NOT EXISTS projections (
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
  created_at TEXT NOT NULL,
  UNIQUE (source_website, season, week, player_first_name, player_last_name, position)
);
"""

INSERT_PROJECTION = """
INSERT INTO projections (id, source_website, season, week, player_first_name, player_last_name, position, team,
                         projected_points, external_id, created_at)
VALUES (:id, :source_website, :season, :week, :player_first_name, :player_last_name, :position, :team,
        :projected_points, :external_id, :created_at)
"""


def run(ctx: StepContext) -> StepResult:
    season, week = ctx.settings.season, ctx.settings.week
    conn = ctx.db("projections")
    conn.executescript(PROJECTIONS_DDL)
    rows = conn.execute(
        "SELECT * FROM projections WHERE season = ? AND week = ? ORDER BY id", (season, week)
    ).fetchall()
    if not rows:
        raise RuntimeError(f"no projections for season {season} week {week}; run the scrape step first")

    cleaned = [clean_row(row) for row in rows]
    kept = keep_highest(cleaned)
    with conn:
        conn.execute("DELETE FROM projections WHERE season = ? AND week = ?", (season, week))
        conn.executemany(INSERT_PROJECTION, kept)

    n_renamed = 0
    unknown_teams = set()
    for row, clean in zip(rows, cleaned, strict=True):
        old_name = (row["player_first_name"], row["player_last_name"])
        new_name = (clean["player_first_name"], clean["player_last_name"])
        if new_name != old_name:
            n_renamed += 1
        if row["team"] and clean["team"] is None:
            unknown_teams.add(row["team"])

    summary = {
        "n_in": len(rows),
        "n_out": len(kept),
        "n_renamed": n_renamed,
        "n_dropped_duplicates": len(rows) - len(kept),
        "unknown_teams": sorted(unknown_teams),
    }
    ctx.log(
        f"{summary['n_in']} rows in, {summary['n_out']} out: {summary['n_renamed']} renamed, "
        f"{summary['n_dropped_duplicates']} duplicates dropped"
    )
    return StepResult(summary)


def clean_row(row: sqlite3.Row) -> dict:
    first = drop_injury_tags(row["player_first_name"])
    last = strip_suffix(drop_injury_tags(row["player_last_name"]))
    position = "DEF" if row["position"] == "DST" else row["position"]
    team = normalize_team(row["team"])
    if position == "DEF":
        code = team_from_def_name(f"{first} {last}") or team
        if code is not None:
            first, last = DEF_NAMES[code]
            team = code
    return {**dict(row), "player_first_name": first, "player_last_name": last, "position": position, "team": team}


def drop_injury_tags(name: str) -> str:
    parts = name.split()
    while len(parts) > 1 and parts[-1] in INJURY_TAGS:
        parts.pop()
    return " ".join(parts)


def keep_highest(rows: list[dict]) -> list[dict]:
    best = {}
    for row in rows:
        key = (row["source_website"], row["player_first_name"], row["player_last_name"], row["position"])
        if key not in best or row["projected_points"] > best[key]["projected_points"]:
            best[key] = row
    return sorted(best.values(), key=lambda row: row["id"])
