import json
import math

import pytest

from pipeline.model.params import PARAMS_DIR, load_params
from pipeline.model.sigma import shrunk_spread, sigma


def test_v1_matches_the_notebook_05_constants():
    params = load_params("v1")

    assert params["version"] == "v1"
    assert params["sources"]["sleeper.com"] == {"weight": 1.0, "bias": {}}
    assert params["sigma"]["pos_sigma"] == {"QB": 7, "RB": 9, "WR": 10, "TE": 8, "K": 4, "DEF": 7}
    assert (params["sigma"]["alpha"], params["sigma"]["beta"]) == (2.0, 1.0)


def test_every_params_file_names_its_own_version():
    for path in PARAMS_DIR.glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == path.stem


def test_unknown_version_lists_the_available_ones():
    with pytest.raises(ValueError, match="available: v1"):
        load_params("v99")


def test_v1_sigma_is_the_position_baseline_for_a_single_source():
    assert sigma(14.2, "WR", 0.0, 1, load_params("v1")) == 10.0


def test_v1_sigma_widens_with_source_disagreement():
    # sqrt((2 * 3)^2 + (1 * 9)^2)
    assert sigma(14.2, "RB", 3.0, 4, load_params("v1")) == math.sqrt(117)


def test_v1_sigma_falls_back_to_the_default_position_sigma():
    assert sigma(5.0, "FB", 0.0, 1, load_params("v1")) == 8.0


def test_linear_sigma_grows_with_mu_and_never_drops_below_one():
    params = {"sigma": {"formula": "linear", "by_position": {"WR": {"a": 2.0, "b": 0.5}, "K": {"a": -3.0, "b": 0.1}}}}

    assert sigma(10.0, "WR", 4.0, 5, params) == 7.0
    assert sigma(8.0, "K", 0.0, 1, params) == 1.0


HYBRID = {
    "sigma": {
        "formula": "hybrid",
        "alpha": 4.0,
        "prior_sources": 2,
        "typical_spread": {"WR": 1.3},
        "by_position": {"WR": {"a": 2.0, "b": 0.5}},
    }
}


def test_a_spread_from_one_source_is_the_typical_spread():
    assert shrunk_spread(0.0, 1, typical=1.3, prior_sources=2) == 1.3


def test_a_spread_from_two_sources_is_mostly_the_typical_spread():
    # ((2 - 1) * 5.3^2 + 2 * 1.3^2) / (2 - 1 + 2)
    assert shrunk_spread(5.3, 2, typical=1.3, prior_sources=2) == pytest.approx(math.sqrt(31.47 / 3))


def test_a_spread_from_many_sources_is_mostly_its_own():
    assert shrunk_spread(5.3, 8, typical=1.3, prior_sources=2) == pytest.approx(
        math.sqrt((7 * 5.3**2 + 2 * 1.3**2) / 9)
    )


def test_hybrid_sigma_adds_the_shrunk_spread_to_the_fitted_line():
    # line 7.0 at mu 10; the spread 2.0 on 5 sources shrinks to sqrt((4 * 4 + 2 * 1.69) / 6)
    disagreement = math.sqrt((4 * 4.0 + 2 * 1.69) / 6)

    assert sigma(10.0, "WR", 2.0, 5, HYBRID) == pytest.approx(math.sqrt(49.0 + (4.0 * disagreement) ** 2))


def test_hybrid_sigma_is_never_narrower_than_the_line():
    assert sigma(10.0, "WR", 0.0, 1, HYBRID) > 7.0


def test_v3_is_v2_3_with_disagreement_and_a_horizon_discount():
    v3, v2_3 = load_params("v3"), load_params("v2.3")

    assert v3["amended"]["from_version"] == "v2.3"
    assert v3["sigma"]["formula"] == "hybrid"
    assert v3["sigma"]["by_position"] == v2_3["sigma"]["by_position"]
    assert (v3["sigma"]["alpha"], v3["sigma"]["prior_sources"]) == (4.0, 2)
    assert set(v3["sigma"]["typical_spread"]) == {"QB", "RB", "WR", "TE", "K", "DEF"}
    assert v3["horizon"] == {"discount_per_week": 0.05}
    for block in ("sources", "dud", "floor", "correlation", "fitted_on", "gate"):
        assert v3[block] == v2_3[block]


def test_unknown_sigma_formula_raises():
    with pytest.raises(ValueError, match="unknown sigma formula 'cubic'"):
        sigma(10.0, "WR", 0.0, 3, {"sigma": {"formula": "cubic"}})
