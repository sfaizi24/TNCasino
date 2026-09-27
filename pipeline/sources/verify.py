"""Silent-failure checks for one source's week of projections, run before its rows are stored.

A source can return a well-formed page that is still wrong: positions shifted, last week's numbers,
a truncated list. Each check compares the rows with Sleeper's player database, Sleeper's projections
for the week, or the source's own previous week.
"""

import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np

from pipeline.names import normalize_name
from pipeline.sources.base import POSITIONS, Projection
from pipeline.sources.teams import CANONICAL_TEAMS

POSITION_ORDER = ["QB", "RB", "WR", "TE", "K", "DEF"]
STATUS_ORDER = ["ok", "warn", "fail"]

POSITION_MISMATCH_FAIL = 0.02
POSITION_MISMATCH_WARN = 0.005
DUPLICATE_POSITIONS_FAIL = 2
# Sleeper lists over 100 tight ends and FantasySharks over 60 quarterbacks in an ordinary week, so the
# upper bounds sit above the design doc's QB 50, RB 130, WR 170, TE 90.
COUNT_RANGES = {"QB": (20, 80), "RB": (40, 150), "WR": (50, 200), "TE": (20, 130), "K": (15, 40), "DEF": (20, 36)}
MIN_CORRELATION = 0.85
MAX_MEDIAN_DIFFERENCE = 4.0
MIN_PAIRS = 5  # fewer matched players than this and a correlation means nothing
WARN_ONLY_POSITIONS = {"K", "DEF"}  # sources genuinely disagree on kickers and defenses
UNKNOWN_TEAMS_FAIL = 0.05
IDENTICAL_POINTS_FAIL = 0.90
TOP_N = 3
MAX_EXAMPLES = 5


@dataclass(frozen=True)
class Check:
    name: str
    status: str  # ok | warn | fail
    detail: str  # one ASCII line; starts with "n/a:" when the check had nothing to compare


@dataclass(frozen=True)
class SourceReport:
    source: str
    status: str  # the worst check status
    checks: list[Check]
    n_rows: int


def verify_source(
    rows: list[Projection],
    week: int,
    sleeper_players: list[dict],
    sleeper_rows: list[Projection],
    previous_rows: list[Projection],
    positions: frozenset[str],
) -> SourceReport:
    """`sleeper_players` are nfl_players rows; `sleeper_rows` Sleeper's projections for the week, empty if unknown."""
    checks = [
        position_agreement(rows, sleeper_players),
        duplicate_positions(rows),
        position_counts(rows, positions),
        value_agreement(rows, sleeper_rows),
        team_codes(rows),
        week_stamp(rows, week),
        freshness(rows, previous_rows),
        top_players(rows, sleeper_players),
    ]
    return SourceReport(
        source=rows[0].source if rows else "",
        status=worst([check.status for check in checks]),
        checks=checks,
        n_rows=len(rows),
    )


def position_agreement(rows: list[Projection], sleeper_players: list[dict]) -> Check:
    """Rows whose position differs from Sleeper's for the same player, such as a running back listed at QB."""
    players_by_name = defaultdict(list)
    for player in sleeper_players:
        if player["position"] in POSITIONS:
            players_by_name[normalize_name(player["first_name"], player["last_name"])].append(player)

    matched = 0
    differences = []
    for row in rows:
        if row.position == "DEF":
            continue  # a defense is its team, so its position cannot disagree
        candidates = players_by_name[normalize_name(row.first_name, row.last_name)]
        sleeper_position = known_position(row, candidates)
        if sleeper_position is None:
            continue
        matched += 1
        if sleeper_position != row.position:
            differences.append(f"{display_name(row)} {sleeper_position}->{row.position}")

    if matched == 0:
        return Check("position_agreement", "ok", "n/a: no rows matched Sleeper's player database")
    share = len(differences) / matched
    status = "ok"
    if share > POSITION_MISMATCH_FAIL:
        status = "fail"
    elif share > POSITION_MISMATCH_WARN:
        status = "warn"
    detail = f"{len(differences)} of {matched} matched rows differ ({share:.1%})"
    return Check("position_agreement", status, with_examples(detail, differences))


def known_position(row: Projection, candidates: list[dict]) -> str | None:
    """Sleeper's position for the row's player; None when the name is unknown or namesakes play different positions."""
    same_team = [player for player in candidates if player["team"] == row.team]
    if same_team:
        candidates = same_team
    positions = {player["position"] for player in candidates}
    if len(positions) != 1:
        return None
    return positions.pop()


def duplicate_positions(rows: list[Projection]) -> Check:
    rows_by_name = defaultdict(list)
    for row in rows:
        rows_by_name[normalize_name(row.first_name, row.last_name)].append(row)

    duplicates = []
    for same_name in rows_by_name.values():
        positions = sorted({row.position for row in same_name})
        if len(positions) > 1:
            duplicates.append(f"{display_name(same_name[0])} {'/'.join(positions)}")

    status = "ok"
    if len(duplicates) > DUPLICATE_POSITIONS_FAIL:
        status = "fail"
    elif duplicates:
        status = "warn"
    detail = f"{len(duplicates)} of {len(rows_by_name)} players listed under more than one position"
    return Check("duplicate_positions", status, with_examples(detail, duplicates))


def position_counts(rows: list[Projection], positions: frozenset[str]) -> Check:
    """Rows per position inside a plausible range; positions the source never provides are not counted."""
    counts = Counter(row.position for row in rows)
    counted = [position for position in POSITION_ORDER if position in positions]
    breaches = []
    for position in counted:
        low, high = COUNT_RANGES[position]
        if not low <= counts[position] <= high:
            breaches.append(f"{position} {counts[position]} (expected {low}-{high})")

    if breaches:
        return Check("position_counts", "fail", "outside the expected range: " + ", ".join(breaches))
    return Check("position_counts", "ok", ", ".join(f"{position} {counts[position]}" for position in counted))


def value_agreement(rows: list[Projection], sleeper_rows: list[Projection]) -> Check:
    """Per position, Pearson r and median absolute difference against Sleeper on the players both project."""
    if not sleeper_rows:
        return Check("value_agreement", "ok", "n/a: no Sleeper projections to compare with")
    sleeper_points = {player_key(row): row.points for row in sleeper_rows}
    pairs = defaultdict(list)
    for row in rows:
        key = player_key(row)
        if key in sleeper_points:
            pairs[row.position].append((row.points, sleeper_points[key]))

    statuses = []
    parts = []
    for position in POSITION_ORDER:
        if len(pairs[position]) < MIN_PAIRS:
            continue
        points, baseline = np.array(pairs[position]).T
        r = correlation(points, baseline)
        mad = float(np.median(np.abs(points - baseline)))
        status = "ok"
        if r < MIN_CORRELATION or mad > MAX_MEDIAN_DIFFERENCE:
            status = "warn" if position in WARN_ONLY_POSITIONS else "fail"
        statuses.append(status)
        part = f"{position} r={r:.2f} MAD={mad:.2f} n={len(points)}"
        parts.append(part if status == "ok" else f"{part} ({status})")

    if not parts:
        return Check("value_agreement", "ok", f"n/a: fewer than {MIN_PAIRS} players per position matched Sleeper")
    return Check("value_agreement", worst(statuses), ", ".join(parts))


def correlation(points: np.ndarray, baseline: np.ndarray) -> float:
    """Pearson r, or 0 when one side is constant: identical projections for everyone mean a broken source."""
    if np.std(points) == 0 or np.std(baseline) == 0:
        return 0.0
    return float(np.corrcoef(points, baseline)[0, 1])


def team_codes(rows: list[Projection]) -> Check:
    if not rows:
        return Check("team_codes", "ok", "n/a: no rows")
    unknown = [display_name(row) for row in rows if row.team not in CANONICAL_TEAMS]
    share = len(unknown) / len(rows)
    status = "ok"
    if share > UNKNOWN_TEAMS_FAIL:
        status = "fail"
    elif unknown:
        status = "warn"
    detail = f"{len(unknown)} of {len(rows)} rows have no recognised team ({share:.1%})"
    return Check("team_codes", status, with_examples(detail, unknown))


def week_stamp(rows: list[Projection], week: int) -> Check:
    """Each row carries the week its payload says it is for; a source serving another week fails."""
    if not rows:
        return Check("week_stamp", "ok", "n/a: no rows")
    wrong_weeks = Counter(row.week for row in rows if row.week != week)
    if wrong_weeks:
        found = ", ".join(f"week {other} ({count} rows)" for other, count in sorted(wrong_weeks.items()))
        return Check("week_stamp", "fail", f"expected week {week}, found {found}")
    return Check("week_stamp", "ok", f"all {len(rows)} rows are for week {week}")


def freshness(rows: list[Projection], previous_rows: list[Projection]) -> Check:
    """A source that stopped updating serves last week's numbers under this week's label."""
    if not previous_rows:
        return Check("freshness", "ok", "n/a: no rows from the previous week")
    previous_points = {player_key(row): row.points for row in previous_rows}
    matched = [row for row in rows if player_key(row) in previous_points]
    if not matched:
        return Check("freshness", "ok", "n/a: no players in common with the previous week")
    identical = [row for row in matched if row.points == previous_points[player_key(row)]]
    share = len(identical) / len(matched)
    status = "fail" if share > IDENTICAL_POINTS_FAIL else "ok"
    detail = f"{len(identical)} of {len(matched)} players have the same points as the previous week ({share:.1%})"
    return Check("freshness", status, detail)


def top_players(rows: list[Projection], sleeper_players: list[dict]) -> Check:
    """The top players at each position must exist in Sleeper's player database at that position."""
    known = {sleeper_key(player) for player in sleeper_players}
    checked = 0
    missing = []
    for position in POSITION_ORDER:
        at_position = [row for row in rows if row.position == position]
        for row in sorted(at_position, key=lambda row: row.points, reverse=True)[:TOP_N]:
            checked += 1
            if player_key(row) not in known:
                missing.append(f"{display_name(row)} {row.position}")

    if missing:
        detail = f"{len(missing)} of {checked} top-{TOP_N} players are not in Sleeper's player database"
        return Check("top_players", "fail", with_examples(detail, missing))
    return Check("top_players", "ok", f"all {checked} top-{TOP_N} players are in Sleeper's player database")


def player_key(row: Projection) -> tuple:
    """How rows from different places are recognised as one player: a defense by its team, anyone else by
    normalised name and position."""
    if row.position == "DEF":
        return ("DEF", row.team)
    return (*normalize_name(row.first_name, row.last_name), row.position)


def sleeper_key(player: dict) -> tuple:
    """player_key for a row of nfl_players."""
    if player["position"] == "DEF":
        return ("DEF", player["team"])
    return (*normalize_name(player["first_name"], player["last_name"]), player["position"])


def display_name(row: Projection) -> str:
    """ "First Last" folded to ASCII, because the pipeline's output is read in a Windows console."""
    name = f"{row.first_name} {row.last_name}"
    return unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()


def with_examples(detail: str, examples: list[str]) -> str:
    if not examples:
        return detail
    return f"{detail}: {', '.join(examples[:MAX_EXAMPLES])}"


def worst(statuses: list[str]) -> str:
    return max(statuses, key=STATUS_ORDER.index)
