import numpy as np
import pytest
from sqlalchemy import text

from app.database import db
from app.markets import odds_from_probability, parse_key
from app.matrices import MissingMatrix, joint_probability, leg_outcome, score_matrix
from pipeline import markets as win_rules
from tests.conftest import RUN_ID, SEEDED_TOTALS


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


@pytest.mark.parametrize(
    ("probability", "odds"),
    [(0.5, "-100"), (0.6, "-150"), (0.4, "+150"), (0.3819, "+162"), (0.01, "+9900"), (0.99, "-9900"), (0.45, "+122")],
)
def test_fair_odds_round_as_the_pipeline_rounds(probability, odds):
    assert odds_from_probability(probability) == odds
