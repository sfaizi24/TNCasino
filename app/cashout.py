"""What a pending bet is worth now, and the cash-out offered for it.

A bet's fair value is its payout times its chance of winning in the latest run, at the bet's own
line; the offer is 95% of that. A weekly bet's chance comes from the week's latest score matrix in
`simulation_totals`, through the win rule its market was priced with. A futures bet's chance is the
latest futures quote, because the standings simulation behind it is not stored as a matrix. There is
no offer while the bet's own run is still the latest, because removing the bet is the way out then.
Nothing here moves money.
"""

from dataclasses import dataclass
from functools import cache, partial

from pipeline import markets as win_rules

from .markets import MarketError, find_quote, parse_key
from .routes.helpers import get_current_week, query_analytics
from .windows import betting_window

OFFER_SHARE = 0.95

NO_OFFER = "No offer for this bet"
CANNOT_PRICE = "No offer: the latest run cannot price this bet"

MATRIX_SQL = "SELECT n_sims, roster_ids, totals FROM simulation_totals WHERE run_id = :run_id"
STANDINGS_SQL = "SELECT standings_through_week FROM simulation_runs WHERE run_id = :run_id"


@dataclass(frozen=True)
class Offer:
    bet_id: int
    amount: float
    fair_value: float
    probability: float
    run_id: str


class NoOffer(Exception):
    """Why this bet has no cash-out offer now; the message can be shown to the bettor."""


def offer_for(bet, now=None):
    return _price(bet, partial(betting_window, now=now), _score_matrix)


def offers_for(bets, now=None):
    """Offers for one page of bets, reading each week's window and each run's matrix once."""
    window_of = cache(partial(betting_window, now=now))
    matrix_of = cache(_score_matrix)
    offers = {}
    for bet in bets:
        try:
            offers[bet.id] = _price(bet, window_of, matrix_of)
        except NoOffer:
            continue
    return offers


def _price(bet, window_of, matrix_of):
    if bet.status != "pending" or len(bet.legs) != 1:
        raise NoOffer(NO_OFFER)
    [leg] = bet.legs
    try:
        market = parse_key(leg.market)
    except MarketError:
        raise NoOffer(NO_OFFER) from None

    if market.week is None:
        probability, run_id = _futures_probability(bet, market, leg, window_of)
    else:
        probability, run_id = _weekly_probability(bet, market, leg, window_of, matrix_of)

    fair_value = (bet.amount + bet.potential_win) * probability
    amount = round(OFFER_SHARE * fair_value, 2)
    # At 0 or 1 the sims cannot say what the bet is worth.
    if not 0 < probability < 1 or amount < 0.01:
        raise NoOffer(CANNOT_PRICE)
    return Offer(bet.id, amount, fair_value, probability, run_id)


def _weekly_probability(bet, market, leg, window_of, matrix_of):
    window = window_of(bet.week)
    _require_open(window)
    _require_newer_run(bet, window.run_id)

    stored = matrix_of(window.run_id)
    if stored is None:
        raise NoOffer(CANNOT_PRICE)
    scores, columns = stored
    return win_rules.probability(_outcome(market, leg, scores, columns)), window.run_id


def _futures_probability(bet, market, leg, window_of):
    current_week = get_current_week()
    _require_open(window_of(current_week))
    try:
        quote = find_quote(market, leg.selection)
    except MarketError:
        raise NoOffer(CANNOT_PRICE) from None
    _require_newer_run(bet, quote.run_id)

    runs = query_analytics(STANDINGS_SQL, {"run_id": quote.run_id})
    if not runs or runs[0]["standings_through_week"] < current_week - 1:
        raise NoOffer("No offer until the next run: the standings are behind")
    return quote.probability, quote.run_id


def _require_open(window):
    if window.state == "paused":
        raise NoOffer("Betting is paused until the odds update")
    if window.state == "closed":
        raise NoOffer(f"Betting is closed for week {window.week}")


def _require_newer_run(bet, latest_run_id):
    if latest_run_id == bet.run_id:
        raise NoOffer("Odds have not changed since this bet was placed; remove it instead")


def _score_matrix(run_id):
    """The run's scores and each roster's column in them; None when the run's matrix was never stored."""
    rows = query_analytics(MATRIX_SQL, {"run_id": run_id})
    if not rows:
        return None
    [row] = rows
    roster_ids = [int(roster_id) for roster_id in row["roster_ids"].split(",")]
    scores = win_rules.decode_totals(row["totals"], row["n_sims"], len(roster_ids))
    columns = {roster_id: column for column, roster_id in enumerate(roster_ids)}
    return scores, columns


def _outcome(market, leg, scores, columns):
    if market.name == "moneyline":
        first, second = market.teams
        picked_id = int(leg.selection)
        other_id = second if picked_id == first else first
        return win_rules.moneyline(scores, _column(columns, picked_id), _column(columns, other_id))
    if market.name == "team_total":
        [roster_id] = market.teams
        # The bet's own line, never the odds table's current one.
        return win_rules.team_total(scores, _column(columns, roster_id), leg.line, leg.selection)
    if market.name == "highest_scorer":
        return win_rules.highest_scorer(scores, _column(columns, int(leg.selection)))
    return win_rules.lowest_scorer(scores, _column(columns, int(leg.selection)))


def _column(columns, roster_id):
    if roster_id not in columns:
        raise NoOffer(CANNOT_PRICE)
    return columns[roster_id]
