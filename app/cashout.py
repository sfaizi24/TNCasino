"""What a pending bet is worth now, and the cash-out offered for it.

A bet's fair value is its payout times its chance of winning in the latest run, at the bet's own
line; the offer is 95% of that. A weekly bet's chance is the share of the week's latest score matrix
in `simulation_totals` in which every leg wins, through the win rule each leg's market was priced
with, so a single and a parlay are priced alike. A futures single's chance is its latest quote, and a
futures parlay's is the share of the latest futures run's standings matrix in `simulation_standings`
in which every leg wins. There is no offer while the bet's own run is still the latest, because
removing the bet is the way out then. Nothing here moves money.
"""

from dataclasses import dataclass
from functools import cache, partial

from .markets import MarketError, find_quote, parse_key
from .matrices import MissingMatrix, futures_outcome, joint_probability, leg_outcome, score_matrix, standings_matrix
from .routes.helpers import get_current_week, query_analytics
from .windows import betting_window

OFFER_SHARE = 0.95

NO_OFFER = "No offer for this bet"
CANNOT_PRICE = "No offer: the latest run cannot price this bet"

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
    return _price(bet, partial(betting_window, now=now))


def offers_for(bets, now=None):
    """Offers for one page of bets, reading each week's window once; the matrices are cached per worker."""
    window_of = cache(partial(betting_window, now=now))
    offers = {}
    for bet in bets:
        try:
            offers[bet.id] = _price(bet, window_of)
        except NoOffer:
            continue
    return offers


def _price(bet, window_of):
    if bet.status != "pending" or not bet.legs:
        raise NoOffer(NO_OFFER)
    try:
        markets = [parse_key(leg.market) for leg in bet.legs]
    except MarketError:
        raise NoOffer(NO_OFFER) from None

    if all(market.week is None for market in markets):
        probability, run_id = _futures_probability(bet, markets, window_of)
    else:
        probability, run_id = _weekly_probability(bet, markets, window_of)

    fair_value = (bet.amount + bet.potential_win) * probability
    amount = round(OFFER_SHARE * fair_value, 2)
    # At 0 or 1 the sims cannot say what the bet is worth.
    if not 0 < probability < 1 or amount < 0.01:
        raise NoOffer(CANNOT_PRICE)
    return Offer(bet.id, amount, fair_value, probability, run_id)


def _weekly_probability(bet, markets, window_of):
    """The share of the latest run's sims in which every leg wins at its own line and selection."""
    window = window_of(bet.week)
    _require_open(window)
    _require_newer_run(bet, window.run_id)

    try:
        matrix = score_matrix(window.run_id)
        outcomes = [
            leg_outcome(market, leg.selection, leg.line, matrix) for market, leg in zip(markets, bet.legs, strict=True)
        ]
    except (MissingMatrix, KeyError):
        raise NoOffer(CANNOT_PRICE) from None
    return joint_probability(outcomes), window.run_id


def _futures_probability(bet, markets, window_of):
    """A single's latest quote, or the share of the latest futures run's simulated seasons every leg wins."""
    window = window_of(get_current_week())
    _require_open(window)
    try:
        quote = find_quote(markets[0], bet.legs[0].selection)
    except MarketError:
        raise NoOffer(CANNOT_PRICE) from None
    _require_newer_run(bet, quote.run_id)

    # The week's simulation run is what the playoffs step ran on, whether or not it ran alone.
    runs = query_analytics(STANDINGS_SQL, {"run_id": window.run_id})
    if not runs or runs[0]["standings_through_week"] < window.week - 1:
        raise NoOffer("No offer until the next run: the standings are behind")
    if len(markets) == 1:
        return quote.probability, quote.run_id

    try:
        standings = standings_matrix(quote.run_id)
        outcomes = [
            futures_outcome(market, leg.selection, standings) for market, leg in zip(markets, bet.legs, strict=True)
        ]
    except (MissingMatrix, KeyError):
        raise NoOffer(CANNOT_PRICE) from None
    return joint_probability(outcomes), quote.run_id


def _require_open(window):
    if window.state == "paused":
        raise NoOffer("Betting is paused until the odds update")
    if window.state == "closed":
        raise NoOffer(f"Betting is closed for week {window.week}")


def _require_newer_run(bet, latest_run_id):
    if latest_run_id == bet.run_id:
        raise NoOffer("Odds have not changed since this bet was placed; remove it instead")
