import numpy as np

from pipeline.standings import finishing_positions, position_counts, season_totals, week_results


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
