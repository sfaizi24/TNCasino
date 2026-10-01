import numpy as np
import pytest
from sqlalchemy import text

from app.database import db
from app.markets import odds_from_probability, parse_key
from app.matrices import (
    MissingMatrix,
    futures_outcome,
    joint_probability,
    leg_outcome,
    score_matrix,
    standings_matrix,
)
from pipeline import markets as win_rules
from tests.conftest import RUN_ID, SEEDED_CHAMPIONS, SEEDED_POSITIONS, SEEDED_TOTALS


def test_the_seeded_run_decodes_to_its_matrix(seeded_analytics):
    matrix = score_matrix(RUN_ID)

    assert matrix.columns == {1: 0, 2: 1}
    np.testing.assert_array_equal(matrix.scores, SEEDED_TOTALS)


def test_a_run_is_read_once_per_worker(seeded_analytics, monkeypatch):
    decodes = []
    decode_totals = win_rules.decode_totals
    monkeypatch.setattr(win_rules, "decode_totals", lambda *args: decodes.append(args) or decode_totals(*args))

    first = score_matrix(RUN_ID)
    second = score_matrix(RUN_ID)

    assert first is second
    assert len(decodes) == 1


def test_a_run_without_a_stored_matrix_is_missing_and_not_cached(seeded_analytics):
    with pytest.raises(MissingMatrix):
        score_matrix("never-stored")

    db.session.execute(
        text("""
        INSERT INTO simulation_totals (run_id, season, week, created_at, n_sims, roster_ids, totals)
        VALUES ('never-stored', 2026, 10, '2026-11-11T14:00:00+00:00', 20, '1,2', :totals)
    """),
        {"totals": win_rules.encode_totals(SEEDED_TOTALS)},
    )
    db.session.commit()

    assert score_matrix("never-stored").columns == {1: 0, 2: 1}


@pytest.mark.parametrize(
    ("market", "selection", "line", "wins"),
    [
        ("2026-w10-moneyline-1v2", "1", None, 11),
        ("2026-w10-moneyline-1v2", "2", None, 8),
        ("2026-w10-team_total-1", "over", 110.5, 9),
        ("2026-w10-team_total-1", "under", 110.5, 10),
        ("2026-w10-highest_scorer", "1", None, 12),
        ("2026-w10-lowest_scorer", "2", None, 12),
    ],
)
def test_each_market_wins_the_sims_its_rule_says(seeded_analytics, market, selection, line, wins):
    outcome = leg_outcome(parse_key(market), selection, line, score_matrix(RUN_ID))

    assert int(outcome.won.sum()) == wins


# Roster 1's margins over roster 2 run -29 to 30 with a median of 4; two sims land on 4 and one on 9.5.
@pytest.mark.parametrize(
    ("selection", "line", "wins", "pushes"),
    [("1", -4.0, 9, 2), ("2", 4.0, 9, 2), ("1", -9.5, 5, 1), ("2", 9.5, 14, 1), ("1", 0.5, 12, 0), ("2", -0.5, 8, 0)],
)
def test_a_spread_covers_when_the_picked_score_plus_its_line_beats_the_other(
    seeded_analytics, selection, line, wins, pushes
):
    outcome = leg_outcome(parse_key("2026-w10-spread-1v2"), selection, line, score_matrix(RUN_ID))

    assert (int(outcome.won.sum()), int(outcome.pushed.sum())) == (wins, pushes)


def test_a_roster_the_run_did_not_simulate_is_a_key_error(seeded_analytics):
    with pytest.raises(KeyError):
        leg_outcome(parse_key("2026-w10-highest_scorer"), "7", None, score_matrix(RUN_ID))


def test_the_joint_chance_counts_the_sims_every_leg_wins(seeded_analytics):
    matrix = score_matrix(RUN_ID)
    wins = leg_outcome(parse_key("2026-w10-moneyline-1v2"), "1", None, matrix)
    over = leg_outcome(parse_key("2026-w10-team_total-1"), "over", 110.5, matrix)

    # Roster 1 wins in 11 sims and is over 110.5 in 9; it does both in the 7 sims where it tops 110.5 and roster 2.
    assert joint_probability([wins, over]) == pytest.approx(7 / 20)
    assert joint_probability([wins]) == pytest.approx(11 / 20)


def test_the_seeded_standings_agree_with_the_seeded_futures_quotes():
    alice, bob = SEEDED_POSITIONS[:, 0], SEEDED_POSITIONS[:, 1]
    champions_place = SEEDED_POSITIONS[np.arange(20), SEEDED_CHAMPIONS]

    assert all(sorted(places) == [1, 2, 3, 4] for places in SEEDED_POSITIONS)
    assert set(champions_place) == {1, 2}
    assert (int((alice <= 2).sum()), int((bob <= 2).sum())) == (16, 12)
    assert (int((alice == 4).sum()), int((bob == 4).sum())) == (4, 6)
    assert (int((SEEDED_CHAMPIONS == 0).sum()), int((SEEDED_CHAMPIONS == 1).sum())) == (6, 3)
    assert int(((alice <= 2) & (bob <= 2)).sum()) == 8
    assert int(((alice <= 2) & (bob > 2)).sum()) == 8


def test_the_seeded_run_decodes_to_its_standings(seeded_analytics):
    standings = standings_matrix(RUN_ID)

    assert (standings.columns, standings.playoff_teams) == ({1: 0, 2: 1, 3: 2, 4: 3}, 2)
    np.testing.assert_array_equal(standings.positions, SEEDED_POSITIONS)
    np.testing.assert_array_equal(standings.champions, SEEDED_CHAMPIONS)
    assert standings_matrix(RUN_ID) is standings


def test_a_run_without_stored_standings_is_missing(seeded_analytics):
    with pytest.raises(MissingMatrix):
        standings_matrix("never-stored")


@pytest.mark.parametrize(
    ("market", "selection", "wins"),
    [
        ("2026-make_playoffs-1", "yes", 16),
        ("2026-make_playoffs-1", "no", 4),
        ("2026-make_playoffs-2", "yes", 12),
        ("2026-make_playoffs-2", "no", 8),
        ("2026-make_playoffs-3", "yes", 7),
        ("2026-last_place", "1", 4),
        ("2026-last_place", "2", 6),
        ("2026-champion", "1", 6),
        ("2026-champion", "2", 3),
        ("2026-champion", "4", 5),
    ],
)
def test_each_futures_market_wins_the_seasons_its_rule_says(seeded_analytics, market, selection, wins):
    outcome = futures_outcome(parse_key(market), selection, standings_matrix(RUN_ID))

    assert (int(outcome.won.sum()), int(outcome.pushed.sum())) == (wins, 0)


def test_a_futures_roster_the_run_did_not_simulate_is_a_key_error(seeded_analytics):
    with pytest.raises(KeyError):
        futures_outcome(parse_key("2026-champion"), "7", standings_matrix(RUN_ID))


@pytest.mark.parametrize(
    ("probability", "odds"),
    [(0.5, "-100"), (0.6, "-150"), (0.4, "+150"), (0.3819, "+162"), (0.01, "+9900"), (0.99, "-9900"), (0.45, "+122")],
)
def test_fair_odds_round_as_the_pipeline_rounds(probability, odds):
    assert odds_from_probability(probability) == odds


@pytest.mark.parametrize("probability", [0.0, 1.0])
def test_a_chance_of_zero_or_one_has_no_fair_odds(probability):
    assert odds_from_probability(probability) is None
