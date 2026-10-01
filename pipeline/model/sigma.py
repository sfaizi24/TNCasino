"""Standard deviation of a player's weekly score under the parameters' sigma formula."""

import math


def sigma(mu: float, position: str, spread: float, n_sources: int, params: dict) -> float:
    """v1 widens a position baseline by source disagreement; linear grows with the projection itself; hybrid
    grows with the projection and widens where the sources disagree."""
    config = params["sigma"]
    formula = config["formula"]
    if formula == "v1":
        pos_sigma = config["pos_sigma"].get(position, config["default_pos_sigma"])
        return math.sqrt((config["alpha"] * spread) ** 2 + (config["beta"] * pos_sigma) ** 2)
    if formula == "linear":
        return fitted_line(mu, position, config)
    if formula == "hybrid":
        disagreement = shrunk_spread(spread, n_sources, config["typical_spread"][position], config["prior_sources"])
        return math.sqrt(fitted_line(mu, position, config) ** 2 + (config["alpha"] * disagreement) ** 2)
    raise ValueError(f"unknown sigma formula {formula!r}")


def fitted_line(mu: float, position: str, config: dict) -> float:
    coefficients = config["by_position"][position]
    return max(1.0, coefficients["a"] + coefficients["b"] * mu)


def shrunk_spread(spread: float, n_sources: int, typical: float, prior_sources: int) -> float:
    """The sources' spread pulled toward the position's typical spread as if `prior_sources` more sources had
    reported it: a spread measured on two sources is mostly noise, one on seven is mostly signal."""
    observed = (n_sources - 1) * spread**2
    prior = prior_sources * typical**2
    return math.sqrt((observed + prior) / (n_sources - 1 + prior_sources))
