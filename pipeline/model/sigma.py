"""Standard deviation of a player's weekly score under the parameters' sigma formula."""

import math


def sigma(mu: float, position: str, spread: float, params: dict) -> float:
    """v1 widens a position baseline by source disagreement; linear grows with the projection itself."""
    config = params["sigma"]
    formula = config["formula"]
    if formula == "v1":
        pos_sigma = config["pos_sigma"].get(position, config["default_pos_sigma"])
        return math.sqrt((config["alpha"] * spread) ** 2 + (config["beta"] * pos_sigma) ** 2)
    if formula == "linear":
        coefficients = config["by_position"][position]
        return max(1.0, coefficients["a"] + coefficients["b"] * mu)
    raise ValueError(f"unknown sigma formula {formula!r}")
