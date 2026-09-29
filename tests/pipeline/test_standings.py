import numpy as np

from pipeline.standings import (
    bracket_order,
    finishing_positions,
    play_bracket,
    play_round,
    playoff_seeds,
    position_counts,
    season_totals,
    week_results,
)


def test_week_results_give_the_higher_score_the_win_and_a_level_score_a_tie():
    scores = np.array([[10.0, 20.0, 30.0, 30.0], [5.0, 1.0, 7.0, 8.0]])

    wins, ties = week_results(scores, [(0, 1), (2, 3)])

    assert wins.tolist() == [[0, 1, 0, 0], [1, 0, 0, 1]]
    assert ties.tolist() == [[0, 0, 1, 1], [0, 0, 0, 0]]


def test_week_results_leave_a_team_without_a_game_on_zero():
    scores = np.array([[10.0, 20.0, 99.0]])

    wins, ties = week_results(scores, [(0, 1)])

    assert wins.tolist() == [[0, 1, 0]]
    assert ties.tolist() == [[0, 0, 0]]


def test_season_totals_add_every_simulated_week_to_the_record_to_date():
    week_one = np.array([[10.0, 20.0], [30.0, 5.0]])
    week_two = np.array([[15.0, 15.0], [1.0, 2.0]])

    wins, ties, points = season_totals(
        np.array([2, 1]),
        np.array([0, 1]),
        np.array([100.5, 90.25]),
        [(week_one, [(0, 1)]), (week_two, [(0, 1)])],
    )

    assert wins.tolist() == [[2, 2], [3, 2]]
    assert ties.tolist() == [[1, 2], [0, 1]]
    assert points.tolist() == [[125.5, 125.25], [131.5, 97.25]]


def test_finishing_positions_rank_wins_then_ties_then_points_then_the_lower_column():
    wins = np.array([[5, 6, 5, 5, 5]])
    ties = np.array([[1, 0, 0, 1, 1]])
    points = np.array([[900.0, 800.0, 999.0, 950.0, 950.0]])

    positions = finishing_positions(wins, ties, points)

    assert positions.tolist() == [[4, 1, 5, 2, 3]]


def test_finishing_positions_rank_each_sim_on_its_own():
    wins = np.array([[3, 1, 2], [1, 2, 3]])
    ties = np.zeros((2, 3), dtype=int)
    points = np.zeros((2, 3))

    positions = finishing_positions(wins, ties, points)

    assert positions.tolist() == [[1, 3, 2], [3, 2, 1]]


def test_position_counts_tally_every_team_at_every_place():
    positions = np.array([[1, 2, 3], [2, 1, 3], [1, 3, 2], [1, 2, 3]])

    counts = position_counts(positions)

    assert counts.tolist() == [[3, 1, 0], [1, 2, 1], [0, 1, 3]]
    assert counts.sum(axis=0).tolist() == [4, 4, 4]
    assert counts.sum(axis=1).tolist() == [4, 4, 4]


def test_bracket_order_opens_seed_i_against_the_last_seed_and_keeps_the_top_two_apart():
    assert bracket_order(1) == [1]
    assert bracket_order(2) == [1, 2]
    assert bracket_order(4) == [1, 4, 2, 3]
    assert bracket_order(8) == [1, 8, 4, 5, 2, 7, 3, 6]


def test_playoff_seeds_list_the_columns_from_first_place_down():
    positions = np.array([[3, 1, 4, 2], [1, 2, 3, 4]])

    assert playoff_seeds(positions, 2).tolist() == [[1, 3], [0, 1]]


def test_a_round_advances_the_higher_score_and_on_a_tie_the_higher_seed():
    bracket = np.array([[0, 3, 1, 2], [0, 3, 1, 2]])
    seed_scores = np.array([[100.0, 90.0, 90.0, 100.0], [90.0, 80.0, 85.0, 95.0]])

    # Sim 0: both games tie, so seeds 1 and 2 go through; sim 1: seeds 4 and 3 win on score.
    assert play_round(bracket, seed_scores).tolist() == [[0, 1], [3, 2]]


def test_a_tie_advances_the_higher_seed_when_it_is_second_in_the_pair():
    bracket = np.array([[7, 3, 5, 1]])  # seed 8 against seed 4, seed 6 against seed 2, as round two can pair them

    assert play_round(bracket, np.zeros((1, 8))).tolist() == [[3, 1]]


def test_a_four_team_bracket_plays_the_semifinals_then_the_final():
    # Sim 0 seeds columns 2, 0, 3, 1; sim 1 seeds the columns in order.
    seeds = playoff_seeds(np.array([[2, 4, 1, 3], [1, 2, 3, 4]]), 4)
    semifinals = np.array(
        [
            [100.0, 120.0, 110.0, 90.0],  # seed 4 (column 1) upsets seed 1; seed 2 (column 0) beats seed 3
            [110.0, 100.0, 90.0, 105.0],  # seeds 1 and 2 win
        ]
    )
    final = np.array(
        [
            [100.0, 100.0, 0.0, 0.0],  # seed 2 against seed 4 tied: seed 2 is champion
            [80.0, 95.0, 0.0, 0.0],  # seed 2 beats seed 1
        ]
    )

    assert play_bracket(seeds, [semifinals, final]).tolist() == [0, 1]


def test_an_eight_team_bracket_meets_its_winners_in_fixed_order_without_reseeding():
    # Places by column, so seeds 1 to 8 are columns 1, 4, 3, 6, 0, 7, 5, 2.
    seeds = playoff_seeds(np.array([[5, 1, 8, 3, 2, 7, 4, 6]]), 8)
    # 1v8: 100 to 110, seed 8 wins. 4v5: 95 all, seed 4 wins. 2v7: 120 to 80, seed 2. 3v6: 90 to 105, seed 6.
    round_one = np.array([[95.0, 100.0, 110.0, 90.0, 120.0, 80.0, 95.0, 105.0]])
    # The 1v8 winner meets the 4v5 winner and ties, so seed 4 goes through; seed 6 beats seed 2, 130 to 90.
    # Teams already out score 200, which would win any game they were wrongly left in.
    round_two = np.array([[200.0, 200.0, 100.0, 200.0, 90.0, 200.0, 100.0, 130.0]])
    # Seed 4 (column 6) beats seed 6 (column 7) in the final. Reseeding would have made seed 8 champion instead.
    final = np.array([[200.0, 200.0, 200.0, 200.0, 200.0, 200.0, 120.0, 110.0]])

    assert seeds.tolist() == [[1, 4, 3, 6, 0, 7, 5, 2]]
    assert play_bracket(seeds, [round_one, round_two, final]).tolist() == [6]
