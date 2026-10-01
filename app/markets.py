"""Market keys and the published prices behind them.

A key names one market in the published odds tables: `2026-w04-moneyline-1v4` is week 4's game
between rosters 1 and 4, `2026-champion` is the season's champion market. A bet is a key and a
selection, and its price always comes from the row the key finds, never from the browser. A spread
has no table: its price comes from the week's latest score matrix at the line the bettor picked.
"""

import re
from dataclasses import dataclass

from pipeline import markets as win_rules

from .matrices import MissingMatrix, leg_outcome, score_matrix
from .routes.helpers import query_analytics
from .windows import latest_run_id


class MarketError(ValueError):
    """A refusal whose message can be shown to the bettor as it is."""


# Whether each market is priced per week, and the columns holding the roster ids its key names.
SHAPES = {
    "moneyline": (True, ("team1_id", "team2_id")),
    "spread": (True, ("team1_id", "team2_id")),
    "team_total": (True, ("team_id",)),
    "highest_scorer": (True, ()),
    "lowest_scorer": (True, ()),
    "make_playoffs": (False, ("team_id",)),
    "last_place": (False, ()),
    "champion": (False, ()),
}

# A spread line is the selected roster's, a multiple of 0.5 no more than 50 points either way.
SPREAD_LIMIT = 50

# Roster ids stop at nine digits, so no key can overflow an integer column.
KEY_PATTERN = re.compile(
    r"(?P<season>[0-9]{4})"
    r"(?:-w(?P<week>[0-9]{2}))?"
    r"-(?P<name>[a-z_]+)"
    r"(?:-(?P<teams>[0-9]{1,9}(?:v[0-9]{1,9})?))?"
)

# One row per selection: run_id, selection, odds, probability and line. Only the latest published
# season is quoted, the same rows the odds endpoints list.
QUOTE_SQL = {
    "moneyline": """
        WITH game AS (
            SELECT * FROM betting_odds_matchup_ml
            WHERE season = :season AND week = :week AND team1_id = :team1 AND team2_id = :team2
              AND season = (SELECT MAX(season) FROM betting_odds_matchup_ml)
        )
        SELECT run_id, CAST(team1_id AS TEXT) AS selection, team1_ml AS odds, team1_win_prob AS probability,
               NULL AS line
        FROM game
        UNION ALL
        SELECT run_id, CAST(team2_id AS TEXT), team2_ml, team2_win_prob, NULL FROM game
    """,
    "team_total": """
        WITH team AS (
            SELECT * FROM betting_odds_team_ou
            WHERE season = :season AND week = :week AND team_id = :team1
              AND season = (SELECT MAX(season) FROM betting_odds_team_ou)
        )
        SELECT run_id, 'over' AS selection, over_odds AS odds, over_prob AS probability, line FROM team
        UNION ALL
        SELECT run_id, 'under', under_odds, under_prob, line FROM team
    """,
    "highest_scorer": """
        SELECT run_id, CAST(team_id AS TEXT) AS selection, odds, probability, NULL AS line
        FROM betting_odds_highest_scorer
        WHERE season = :season AND week = :week
          AND season = (SELECT MAX(season) FROM betting_odds_highest_scorer)
    """,
    "lowest_scorer": """
        SELECT run_id, CAST(team_id AS TEXT) AS selection, odds, probability, NULL AS line
        FROM betting_odds_lowest_scorer
        WHERE season = :season AND week = :week
          AND season = (SELECT MAX(season) FROM betting_odds_lowest_scorer)
    """,
    "make_playoffs": """
        WITH team AS (
            SELECT * FROM betting_odds_make_playoffs
            WHERE season = :season AND team_id = :team1
              AND season = (SELECT MAX(season) FROM betting_odds_make_playoffs)
              AND week = (SELECT MAX(week) FROM betting_odds_make_playoffs WHERE season = :season)
        )
        SELECT run_id, 'yes' AS selection, american_odds AS odds, probability, NULL AS line FROM team
        UNION ALL
        SELECT run_id, 'no', no_american_odds, no_probability, NULL FROM team
    """,
    "last_place": """
        SELECT run_id, CAST(team_id AS TEXT) AS selection, american_odds AS odds, probability, NULL AS line
        FROM betting_odds_last_place
        WHERE season = :season
          AND season = (SELECT MAX(season) FROM betting_odds_last_place)
          AND week = (SELECT MAX(week) FROM betting_odds_last_place WHERE season = :season)
    """,
    "champion": """
        SELECT run_id, CAST(team_id AS TEXT) AS selection, american_odds AS odds, probability, NULL AS line
        FROM betting_odds_champion
        WHERE season = :season
          AND season = (SELECT MAX(season) FROM betting_odds_champion)
          AND week = (SELECT MAX(week) FROM betting_odds_champion WHERE season = :season)
    """,
}


@dataclass(frozen=True)
class Market:
    name: str
    season: int
    week: int | None = None
    teams: tuple[int, ...] = ()

    @property
    def key(self):
        parts = [str(self.season)]
        if self.week is not None:
            parts.append(f"w{self.week:02d}")
        parts.append(self.name)
        if self.teams:
            parts.append("v".join(str(team) for team in self.teams))
        return "-".join(parts)


@dataclass(frozen=True)
class Quote:
    run_id: str
    odds: str | None
    probability: float
    line: float | None

    @property
    def price(self):
        return price_from_odds(self.odds)


def parse_key(key):
    """The market a key names. Anything not spelled exactly as key_for_row writes it is an unknown market."""
    match = KEY_PATTERN.fullmatch(key) if isinstance(key, str) else None
    if match is None or match["name"] not in SHAPES:
        raise MarketError("Unknown market")

    weekly, team_columns = SHAPES[match["name"]]
    week = int(match["week"]) if match["week"] else None
    teams = tuple(int(team) for team in match["teams"].split("v")) if match["teams"] else ()
    market = Market(match["name"], int(match["season"]), week, teams)

    has_shape = weekly == (week is not None) and len(teams) == len(team_columns)
    # A matchup names the lower roster id first, as the odds tables store it.
    in_order = len(teams) < 2 or teams[0] < teams[1]
    if not (has_shape and in_order) or market.key != key:
        raise MarketError("Unknown market")
    return market


def key_for_row(name, row):
    """The key of the market a published odds row prices."""
    weekly, team_columns = SHAPES[name]
    week = row["week"] if weekly else None
    teams = tuple(row[column] for column in team_columns)
    return Market(name, row["season"], week, teams).key


def find_quote(market, selection, line=None):
    """The selection's quote. A spread is priced at the requested line; every other market ignores `line`."""
    if market.name == "spread":
        return _spread_quote(market, selection, line)
    side = _published_side(QUOTE_SQL[market.name], market, selection)
    return Quote(run_id=side["run_id"], odds=side["odds"], probability=side["probability"], line=side["line"])


def spread_quote(market, selection, line, run_id, matrix):
    """One side of a spread at a line, at the share of the run's sims in which the side covers."""
    probability = win_rules.probability(leg_outcome(market, selection, line, matrix))
    return Quote(run_id=run_id, odds=odds_from_probability(probability), probability=probability, line=line)


def _published_side(sql, market, selection):
    params = {"season": market.season, "week": market.week}
    for number, team in enumerate(market.teams, start=1):
        params[f"team{number}"] = team

    sides = query_analytics(sql, params)
    if not sides:
        raise MarketError("Unknown market")
    for side in sides:
        if side["selection"] == selection:
            return side
    raise MarketError("Unknown selection")


def _spread_quote(market, selection, line):
    line = _spread_line(line)
    # A spread is offered on each matchup with a published moneyline, between the same two rosters.
    _published_side(QUOTE_SQL["moneyline"], market, selection)

    run_id = latest_run_id(market.week)
    try:
        matrix = score_matrix(run_id)
    except MissingMatrix:
        raise MarketError("Not offered") from None
    return spread_quote(market, selection, line, run_id, matrix)


def _spread_line(line):
    try:
        line = float(line)
    except (TypeError, ValueError):
        raise MarketError("Unknown line") from None
    if abs(line) > SPREAD_LIMIT or line % 0.5 != 0:
        raise MarketError("Unknown line")
    return line


def price_from_odds(odds):
    """American odds text as an integer: "+150" is 150, "-150" is -150 and even money is 100."""
    if odds is None:
        return None
    if odds.upper() == "EVEN":
        return 100
    price = int(odds)
    return 100 if price == -100 else price


def odds_from_probability(probability):
    """Fair American odds text, rounded as the pipeline's odds step rounds; none at 0 or 1, which the sims cannot price."""
    if not 0 < probability < 1:
        return None
    if probability >= 0.5:
        return f"{round(-probability / (1 - probability) * 100)}"
    return f"+{round((1 - probability) / probability * 100)}"


def potential_win(amount, price):
    """What a winning bet pays on top of the stake."""
    if price > 0:
        return amount * price / 100
    return amount * 100 / -price
