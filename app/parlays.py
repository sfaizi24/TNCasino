"""Parlays: two to four of the week's picks priced together at the joint chance of the latest run.

A parlay wins only if every leg wins, so its chance is the share of the run's sims in which every
leg wins, at fair odds with no cap and no house edge. Two combinations are refused: two legs from
one market, which can only win together on a tie, and a leg that adds nothing to the others.
Nothing here moves money.
"""

from collections import Counter
from dataclasses import dataclass

import numpy as np

from .markets import Market, MarketError, Quote, find_quote, odds_from_probability, parse_key, price_from_odds
from .matrices import MissingMatrix, joint_probability, leg_outcome, score_matrix

CANNOT_PRICE = "Not offered: the simulations cannot price this parlay"


@dataclass(frozen=True)
class Leg:
    market: Market
    selection: str
    line: float | None
    quote: Quote  # the leg's own single quote at the run


@dataclass(frozen=True)
class Parlay:
    run_id: str
    legs: tuple[Leg, ...]
    probability: float
    odds: str

    @property
    def price(self):
        return price_from_odds(self.odds)


class ParlayRefusal(Exception):
    """Why these legs are not offered together; the message can be shown to the bettor."""

    def __init__(self, message, rule, legs=()):
        super().__init__(message)
        self.rule = rule
        self.legs = tuple(legs)


def quote(requests, week, run_id):
    """The parlay of the legs the page sent, priced at the week's latest run, or the first rule it breaks."""
    if not 2 <= len(requests) <= 4:
        raise ParlayRefusal("A parlay has 2 to 4 legs", "size")
    legs = _legs(requests, week)
    _require_distinct_markets(legs)
    _require_current_quotes(legs, requests, run_id)

    try:
        matrix = score_matrix(run_id)
    except MissingMatrix:
        raise ParlayRefusal("Not offered", "no_price") from None
    outcomes = [leg_outcome(leg.market, leg.selection, leg.line, matrix) for leg in legs]
    probability, odds = joint_price(outcomes)

    _require_every_leg_counts(legs, outcomes)
    return Parlay(run_id, legs, probability, odds)


def joint_price(outcomes):
    """The joint chance and fair odds of legs judged on one matrix; at 0 or 1 the sims cannot price them."""
    probability = joint_probability(outcomes)
    if not 0 < probability < 1:
        raise ParlayRefusal(CANNOT_PRICE, "impossible")
    return probability, odds_from_probability(probability)


def _legs(requests, week):
    legs, faulty_keys, messages = [], [], []
    for request in requests:
        key = request.get("market")
        try:
            legs.append(_leg(key, str(request.get("selection")), week))
        except MarketError as error:
            faulty_keys.append(key)
            messages.append(str(error))
    if faulty_keys:
        raise ParlayRefusal(messages[0], "leg", faulty_keys)
    return tuple(legs)


def _leg(key, selection, week):
    market = parse_key(key)
    if market.week is None:
        raise MarketError("Futures cannot be parlayed")
    if market.week != week:
        raise MarketError("Not this week's market")
    single = find_quote(market, selection)
    return Leg(market, selection, single.line, single)


def _require_distinct_markets(legs):
    counts = Counter(leg.market.key for leg in legs)
    shared = [key for key, count in counts.items() if count > 1]
    if shared:
        raise ParlayRefusal("Two legs from one market", "same_market", shared)


def _require_current_quotes(legs, requests, run_id):
    moved = [leg.market.key for leg, request in zip(legs, requests, strict=True) if _moved(leg, request, run_id)]
    if moved:
        raise ParlayRefusal("Odds have changed", "odds_changed", moved)
    unpriced = [leg.market.key for leg in legs if leg.quote.price is None]
    if unpriced:
        raise ParlayRefusal("Not offered", "no_price", unpriced)


def _moved(leg, request, run_id):
    """Whether the leg is quoted at another run, or is a team total at another line than the page showed."""
    if leg.quote.run_id != run_id:
        return True
    if leg.market.name != "team_total":
        return False
    try:
        return round(float(request.get("line")), 2) != round(leg.quote.line, 2)
    except (TypeError, ValueError):
        return True


def _require_every_leg_counts(legs, outcomes):
    """A leg adds nothing when the other legs alone win in exactly as many sims as all of them together."""
    together = _winning_sims(outcomes)
    idle = []
    for index, leg in enumerate(legs):
        others = outcomes[:index] + outcomes[index + 1 :]
        if _winning_sims(others) == together:
            idle.append(leg.market.key)
    if idle:
        raise ParlayRefusal("A leg adds nothing to this parlay", "redundant", idle)


def _winning_sims(outcomes):
    return int(np.logical_and.reduce([outcome.won for outcome in outcomes]).sum())
