import struct
import zlib

import numpy as np
import pytest

from pipeline import markets

# Five sims (rows) of three teams (columns), small enough to count by eye. Team 0 beats team 1 in sims 0 and 3,
# ties it in sims 1 and 4 and loses sim 2; all three teams share the top and the bottom score in sim 4.
SCORES = np.array(
    [
        [110.0, 100.0, 90.0],
        [100.0, 100.0, 120.0],
        [95.0, 105.0, 80.0],
        [120.0, 101.5, 101.5],
        [100.0, 100.0, 100.0],
    ]
)


def sims(outcome: markets.Outcome) -> tuple[list[int], list[int]]:
    """The sims the selection won and the sims it pushed."""
    return np.flatnonzero(outcome.won).tolist(), np.flatnonzero(outcome.pushed).tolist()


def test_a_moneyline_wins_on_the_higher_score_and_pushes_on_a_tie():
    assert sims(markets.moneyline(SCORES, 0, 1)) == ([0, 3], [1, 4])
    assert sims(markets.moneyline(SCORES, 1, 0)) == ([2], [1, 4])


def test_a_team_total_wins_beyond_the_line_and_pushes_on_it():
    assert sims(markets.team_total(SCORES, 0, 100.0, "over")) == ([0, 3], [1, 4])
    assert sims(markets.team_total(SCORES, 0, 100.0, "under")) == ([2], [1, 4])


def test_a_matchup_total_prices_the_two_teams_combined_score():
    # Teams 1 and 2 combine for 190, 220, 185, 203 and 200.
    assert sims(markets.matchup_total(SCORES, 1, 2, 200.0, "over")) == ([1, 3], [4])
    assert sims(markets.matchup_total(SCORES, 1, 2, 200.0, "under")) == ([0, 2], [4])


def test_a_spread_adds_the_line_to_the_team_and_both_sides_push_together():
    # Team 1 getting 10 points covers sims 1, 2 and 4, team 0 giving 10 covers sim 3, and sim 0 lands on the line.
    assert sims(markets.spread(SCORES, 1, 0, 10.0)) == ([1, 2, 4], [0])
    assert sims(markets.spread(SCORES, 0, 1, -10.0)) == ([3], [0])


def test_every_team_sharing_the_top_score_wins_the_sim():
    assert sims(markets.highest_scorer(SCORES, 0)) == ([0, 3, 4], [])
    assert sims(markets.highest_scorer(SCORES, 1)) == ([2, 4], [])
    assert sims(markets.highest_scorer(SCORES, 2)) == ([1, 4], [])


def test_every_team_sharing_the_bottom_score_wins_the_sim():
    assert sims(markets.lowest_scorer(SCORES, 0)) == ([1, 4], [])
    assert sims(markets.lowest_scorer(SCORES, 1)) == ([1, 3, 4], [])
    assert sims(markets.lowest_scorer(SCORES, 2)) == ([0, 2, 3, 4], [])


def test_a_probability_is_the_share_of_all_sims_so_pushes_count_against_it():
    over = markets.team_total(SCORES, 0, 100.0, "over")
    under = markets.team_total(SCORES, 0, 100.0, "under")
    assert (markets.probability(over), markets.probability(under)) == (0.4, 0.2)
    # The three-way tie for the top score in sim 4 counts for every team, so these chances sum past one.
    highest = [markets.probability(markets.highest_scorer(SCORES, team)) for team in range(3)]
    assert highest == [0.6, 0.4, 0.4]


def test_a_side_other_than_over_or_under_is_refused():
    with pytest.raises(ValueError, match="side is 'over' or 'under', not 'Over'"):
        markets.team_total(SCORES, 0, 100.0, "Over")


def test_the_encoding_is_zlib_compressed_little_endian_float32_one_sim_after_another():
    scores = np.array([[1.5, 2.5, 3.5], [4.5, 5.5, 6.5]])

    raw = zlib.decompress(markets.encode_totals(scores))

    assert struct.unpack("<6f", raw) == (1.5, 2.5, 3.5, 4.5, 5.5, 6.5)
    # The bytes follow the sims whatever the array's memory order.
    assert markets.encode_totals(np.asfortranarray(scores)) == markets.encode_totals(scores)


def test_decoding_gives_back_the_matrix_at_float32_precision():
    rng = np.random.default_rng(3)
    scores = rng.normal(100, 20, size=(1000, 12))

    decoded = markets.decode_totals(markets.encode_totals(scores), 1000, 12)

    assert decoded.dtype == np.float64
    assert np.array_equal(decoded, scores.astype(np.float32).astype(np.float64))
    # Simulated draws are float32 already, so theirs come back exactly.
    assert np.array_equal(markets.decode_totals(markets.encode_totals(SCORES), 5, 3), SCORES)


# Four simulated seasons of three teams: each row holds every team's finishing place, and the playoffs take the top 2.
POSITIONS = np.array([[1, 2, 3], [3, 1, 2], [2, 3, 1], [1, 3, 2]])
CHAMPIONS = np.array([1, 1, 2, 0])


def test_make_playoffs_yes_wins_inside_the_playoff_line_and_no_outside_it():
    assert sims(markets.make_playoffs(POSITIONS, 0, 2, "yes")) == ([0, 2, 3], [])
    assert sims(markets.make_playoffs(POSITIONS, 0, 2, "no")) == ([1], [])
    assert sims(markets.make_playoffs(POSITIONS, 1, 2, "no")) == ([2, 3], [])


def test_a_side_other_than_yes_or_no_is_refused():
    with pytest.raises(ValueError, match="side is 'yes' or 'no', not 'over'"):
        markets.make_playoffs(POSITIONS, 0, 2, "over")


def test_last_place_wins_in_the_seasons_the_team_finishes_bottom():
    assert sims(markets.last_place(POSITIONS, 0)) == ([1], [])
    assert sims(markets.last_place(POSITIONS, 1)) == ([2, 3], [])
    assert sims(markets.last_place(POSITIONS, 2)) == ([0], [])


def test_champion_wins_in_the_seasons_the_team_takes_the_bracket():
    assert sims(markets.champion(CHAMPIONS, 1)) == ([0, 1], [])
    assert markets.probability(markets.champion(CHAMPIONS, 2)) == 0.25


def test_the_standings_encoding_is_zlib_compressed_uint8_places_then_the_champion():
    raw = zlib.decompress(markets.encode_standings(POSITIONS, CHAMPIONS))

    assert list(raw) == [1, 2, 3, 1, 3, 1, 2, 1, 2, 3, 1, 2, 1, 3, 2, 0]


def test_decoding_gives_back_the_places_and_the_champions():
    positions, champions = markets.decode_standings(markets.encode_standings(POSITIONS, CHAMPIONS), 4, 3)

    assert positions.shape == (4, 3)
    assert champions.shape == (4,)
    assert np.array_equal(positions, POSITIONS)
    assert np.array_equal(champions, CHAMPIONS)


def test_repetitive_standings_compress_below_their_raw_size():
    positions = np.tile(np.random.default_rng(5).permutation(12) + 1, (2000, 1))
    champions = np.zeros(2000, dtype=np.int64)

    raw_bytes = 2000 * (12 + 1)
    assert len(markets.encode_standings(positions, champions)) < raw_bytes
