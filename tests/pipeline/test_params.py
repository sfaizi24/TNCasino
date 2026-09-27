import json
import math

import pytest

from pipeline.model.params import PARAMS_DIR, load_params
from pipeline.model.sigma import sigma


def test_v1_matches_the_notebook_05_constants():
    params = load_params("v1")

    assert params["version"] == "v1"
    assert params["sources"]["sleeper.com"] == {"weight": 1.0, "bias": 0.0}
    assert params["sigma"]["pos_sigma"] == {"QB": 7, "RB": 9, "WR": 10, "TE": 8, "K": 4, "DEF": 7}
    assert (params["sigma"]["alpha"], params["sigma"]["beta"]) == (2.0, 1.0)


def test_every_params_file_names_its_own_version():
    for path in PARAMS_DIR.glob("*.json"):
        assert json.loads(path.read_text(encoding="utf-8"))["version"] == path.stem


def test_unknown_version_lists_the_available_ones():
    with pytest.raises(ValueError, match="available: v1"):
        load_params("v99")


def test_v1_sigma_is_the_position_baseline_for_a_single_source():
    assert sigma(14.2, "WR", 0.0, load_params("v1")) == 10.0


def test_v1_sigma_widens_with_source_disagreement():
    # sqrt((2 * 3)^2 + (1 * 9)^2)
    assert sigma(14.2, "RB", 3.0, load_params("v1")) == math.sqrt(117)


def test_v1_sigma_falls_back_to_the_default_position_sigma():
    assert sigma(5.0, "FB", 0.0, load_params("v1")) == 8.0


def test_linear_sigma_grows_with_mu_and_never_drops_below_one():
    params = {"sigma": {"formula": "linear", "by_position": {"WR": {"a": 2.0, "b": 0.5}, "K": {"a": -3.0, "b": 0.1}}}}

    assert sigma(10.0, "WR", 4.0, params) == 7.0
    assert sigma(8.0, "K", 0.0, params) == 1.0


def test_unknown_sigma_formula_raises():
    with pytest.raises(ValueError, match="unknown sigma formula 'cubic'"):
        sigma(10.0, "WR", 0.0, {"sigma": {"formula": "cubic"}})
