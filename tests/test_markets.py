from datetime import UTC, datetime

import numpy as np
import pytest
from sqlalchemy import text

from app import markets
from app.database import db
from app.markets import Market, MarketError, Quote
from app.routes.helpers import query_analytics
from pipeline.markets import encode_totals
from tests.conftest import RUN_ID, SEEDED_TOTALS

KEYS = [
    ("2026-w04-moneyline-1v4", Market("moneyline", 2026, 4, (1, 4))),
    ("2026-w04-spread-1v4", Market("spread", 2026, 4, (1, 4))),
    ("2026-w04-team_total-4", Market("team_total", 2026, 4, (4,))),
    ("2026-w04-highest_scorer", Market("highest_scorer", 2026, 4)),
    ("2026-w04-lowest_scorer", Market("lowest_scorer", 2026, 4)),
    ("2026-first_place", Market("first_place", 2026)),
    ("2026-make_playoffs-4", Market("make_playoffs", 2026, None, (4,))),
    ("2026-last_place", Market("last_place", 2026)),
    ("2026-champion", Market("champion", 2026)),
]


@pytest.mark.parametrize(("key", "market"), KEYS)
def test_parse_key_reads_each_market(key, market):
    assert markets.parse_key(key) == market


@pytest.mark.parametrize(("key", "market"), KEYS)
def test_a_market_spells_its_key(key, market):
    assert market.key == key


@pytest.mark.parametrize(
    ("name", "row", "key"),
    [
        ("moneyline", {"season": 2026, "week": 4, "team1_id": 1, "team2_id": 4}, "2026-w04-moneyline-1v4"),
        ("spread", {"season": 2026, "week": 4, "team1_id": 1, "team2_id": 4}, "2026-w04-spread-1v4"),
        ("team_total", {"season": 2026, "week": 4, "team_id": 4}, "2026-w04-team_total-4"),
        ("highest_scorer", {"season": 2026, "week": 4, "team_id": 4}, "2026-w04-highest_scorer"),
        ("lowest_scorer", {"season": 2026, "week": 12, "team_id": 4}, "2026-w12-lowest_scorer"),
        ("first_place", {"season": 2026, "week": 4, "team_id": 4}, "2026-first_place"),
        ("make_playoffs", {"season": 2026, "week": 4, "team_id": 4}, "2026-make_playoffs-4"),
        ("last_place", {"season": 2026, "week": 4, "team_id": 4}, "2026-last_place"),
        ("champion", {"season": 2026, "week": 4, "team_id": 4}, "2026-champion"),
    ],
)
def test_key_for_row_names_the_market_the_row_prices(name, row, key):
    assert markets.key_for_row(name, row) == key


@pytest.mark.parametrize(
    "key",
    [
        None,
        12,
        "",
        "2026",
        "2026-w4-moneyline-1v4",
        "2026-w04-moneyline-4v1",
        "2026-w04-moneyline-1v1",
        "2026-w04-moneyline-1",
        "2026-w04-moneyline--1v4",
        "2026-w04-moneyline-av4",
        "2026-w04-team_total",
        "2026-w04-team_total-01",
        "2026-w04-team_total--4",
        "2026-w04-team_total-1234567890",
        "2026-w04-highest_scorer-4",
        "2026-w04-first_place",
        "2026-first_place-4",
        "2026-make_playoffs",
        "2026-w04-make_playoffs-4",
        "2026-w04-last_place",
        "2026-last_place-4",
        "2026-w14-champion",
        "2026-champion-4",
        "2026-w04-spread-4v1",
        "2026-w04-spread-4",
        "2026-w04-matchup_total-1v4",
        "2026-w04-Moneyline-1v4",
        "2026-w04-moneyline-1v4 ",
    ],
)
def test_parse_key_refuses_any_other_spelling(key):
    with pytest.raises(MarketError, match="Unknown market"):
        markets.parse_key(key)


def test_a_spread_is_keyed_by_its_matchups_moneyline_row(seeded_analytics):
    [row] = query_analytics("SELECT * FROM betting_odds_matchup_ml")

    assert markets.key_for_row("spread", row) == "2026-w10-spread-1v2"


@pytest.mark.parametrize(
    ("key", "selection", "odds", "probability", "line"),
    [
        ("2026-w10-moneyline-1v2", "1", "-150", 0.6, None),
        ("2026-w10-moneyline-1v2", "2", "+130", 0.4, None),
        ("2026-w10-team_total-1", "over", "-120", 0.55, 110.5),
        ("2026-w10-team_total-2", "under", "-125", 0.52, 95.0),
        ("2026-w10-highest_scorer", "1", "+185", 0.35, None),
        ("2026-w10-lowest_scorer", "2", "+230", 0.30, None),
        ("2026-first_place", "2", "+150", 0.30, None),
        ("2026-make_playoffs-1", "yes", "-400", 0.80, None),
        ("2026-last_place", "2", "+230", 0.30, None),
        ("2026-champion", "1", "+233", 0.30, None),
    ],
)
def test_find_quote_reads_each_market_from_its_table(seeded_analytics, key, selection, odds, probability, line):
    quote = markets.find_quote(markets.parse_key(key), selection)

    assert quote == Quote(run_id=RUN_ID, odds=odds, probability=probability, line=line)
    assert quote.price == int(odds)


@pytest.mark.parametrize(
    ("key", "selection"),
    [
        ("2026-w10-moneyline-1v2", "3"),
        ("2026-w10-moneyline-1v2", "01"),
        ("2026-w10-moneyline-1v2", "yes"),
        ("2026-w10-team_total-1", "1"),
        ("2026-w10-team_total-1", "Over"),
        ("2026-w10-highest_scorer", "over"),
        ("2026-w10-highest_scorer", "7"),
        ("2026-w10-lowest_scorer", "7"),
        ("2026-first_place", "yes"),
        ("2026-first_place", "7"),
        ("2026-make_playoffs-1", "1"),
        ("2026-make_playoffs-1", "no"),
        ("2026-last_place", "yes"),
        ("2026-champion", "7"),
    ],
)
def test_find_quote_refuses_a_selection_the_market_does_not_offer(seeded_analytics, key, selection):
    with pytest.raises(MarketError, match="Unknown selection"):
        markets.find_quote(markets.parse_key(key), selection)


@pytest.mark.parametrize(
    "key",
    [
        "2026-w11-moneyline-1v2",
        "2026-w10-moneyline-1v3",
        "2026-w10-team_total-7",
        "2026-w11-highest_scorer",
        "2026-w11-lowest_scorer",
        "2025-first_place",
        "2026-make_playoffs-7",
        "2025-last_place",
        "2025-champion",
    ],
)
def test_find_quote_refuses_a_market_with_no_published_row(seeded_analytics, key):
    with pytest.raises(MarketError, match="Unknown market"):
        markets.find_quote(markets.parse_key(key), "1")


def test_find_quote_reads_only_the_latest_published_season(seeded_analytics, db_session):
    db_session.session.execute(
        text(
            "INSERT INTO betting_odds_highest_scorer (run_id, week, season, team_id, owner, probability, odds) "
            "VALUES ('2027w10-20271109T140000', 10, 2027, 1, 'Alice A', 0.40, '+150')"
        )
    )

    with pytest.raises(MarketError, match="Unknown market"):
        markets.find_quote(markets.parse_key("2026-w10-highest_scorer"), "1")


@pytest.mark.parametrize("name", ["first_place", "last_place", "champion"])
def test_futures_are_quoted_from_the_highest_published_week(seeded_analytics, db_session, name):
    db_session.session.execute(
        text(
            f"INSERT INTO betting_odds_{name} (run_id, week, season, team_id, owner, probability, american_odds) "
            "VALUES ('2026w12-20261124T140000', 12, 2026, 1, 'Alice A', 0.55, '-122')"
        )
    )
    market = markets.parse_key(f"2026-{name}")

    assert markets.find_quote(market, "1").run_id == "2026w12-20261124T140000"
    with pytest.raises(MarketError, match="Unknown selection"):
        markets.find_quote(market, "2")


def test_a_side_without_a_price_is_quoted_without_one(seeded_analytics, db_session):
    db_session.session.execute(text("UPDATE betting_odds_lowest_scorer SET odds = NULL WHERE team_id = 1"))

    quote = markets.find_quote(markets.parse_key("2026-w10-lowest_scorer"), "1")

    assert quote.odds is None
    assert quote.price is None


@pytest.mark.parametrize(
    ("odds", "price"),
    [("+150", 150), ("-150", -150), ("+100", 100), ("-100", 100), ("100", 100), ("EVEN", 100), ("even", 100)],
)
def test_price_from_odds(odds, price):
    assert markets.price_from_odds(odds) == price


@pytest.mark.parametrize(("price", "win"), [(150, 150.0), (-150, 66.67), (100, 100.0), (-400, 25.0)])
def test_potential_win_on_a_100_stake(price, win):
    assert markets.potential_win(100, price) == pytest.approx(win, abs=0.01)


# Spreads on the seeded run. Roster 1's margins over roster 2 in its 20 sims, in order: -29, -28, -26, -23, -18,
# -13, -11, -5, 0, 4, 4, 5, 9, 9, 9.5, 15, 23, 27, 28 and 30. The median is 4, so roster 1's main line is -4.0 and
# roster 2's +4.0: each side covers in 9 sims and 2 land on the line. At roster 1 -9.5, roster 1 covers in 5 sims,
# roster 2 at +9.5 in 14, and one lands on it.
SPREAD = "2026-w10-spread-1v2"


@pytest.mark.parametrize(
    ("selection", "line", "odds", "probability"),
    [("1", -4.0, "+122", 0.45), ("2", 4.0, "+122", 0.45), ("1", -9.5, "+300", 0.25), ("2", 9.5, "-233", 0.70)],
    ids=["roster 1 main", "roster 2 main", "roster 1 alternate", "roster 2 alternate"],
)
def test_a_spread_is_priced_from_the_latest_score_matrix_at_the_requested_line(
    seeded_analytics, selection, line, odds, probability
):
    quote = markets.find_quote(markets.parse_key(SPREAD), selection, line)

    assert quote == Quote(run_id=RUN_ID, odds=odds, probability=probability, line=line)


def test_a_spread_line_may_come_as_text(seeded_analytics):
    assert markets.find_quote(markets.parse_key(SPREAD), "1", "-4.5").line == -4.5


@pytest.mark.parametrize(("selection", "line"), [("1", 30.0), ("2", -30.0)])
def test_a_spread_side_that_always_or_never_covers_has_no_price(seeded_analytics, selection, line):
    quote = markets.find_quote(markets.parse_key(SPREAD), selection, line)

    assert (quote.odds, quote.price) == (None, None)
    assert quote.probability in (0.0, 1.0)


@pytest.mark.parametrize("line", [None, 3.25, 41, -40.5, "x", float("nan")])
def test_a_spread_line_must_be_a_half_point_within_40(seeded_analytics, line):
    with pytest.raises(MarketError, match="Unknown line"):
        markets.find_quote(markets.parse_key(SPREAD), "1", line)


def test_a_spread_line_of_40_either_way_is_offered(seeded_analytics):
    assert markets.find_quote(markets.parse_key(SPREAD), "2", -40).probability == 0.0
    assert markets.find_quote(markets.parse_key(SPREAD), "2", 40).probability == 1.0


def test_the_line_is_checked_before_the_matchup(seeded_analytics):
    with pytest.raises(MarketError, match="Unknown line"):
        markets.find_quote(markets.parse_key("2026-w10-spread-1v3"), "3", 3.25)


@pytest.mark.parametrize("key", ["2026-w10-spread-1v3", "2026-w11-spread-1v2", "2025-w10-spread-1v2"])
def test_a_spread_needs_a_published_matchup(seeded_analytics, key):
    with pytest.raises(MarketError, match="Unknown market"):
        markets.find_quote(markets.parse_key(key), "1", -4.0)


@pytest.mark.parametrize("selection", ["3", "01", "over", "None"])
def test_a_spread_selection_is_one_of_the_matchups_rosters(seeded_analytics, selection):
    with pytest.raises(MarketError, match="Unknown selection"):
        markets.find_quote(markets.parse_key(SPREAD), selection, -4.0)


def test_a_spread_without_the_runs_matrix_is_not_offered(seeded_analytics, db_session):
    db_session.session.execute(text("DELETE FROM simulation_totals"))

    with pytest.raises(MarketError, match="Not offered"):
        markets.find_quote(markets.parse_key(SPREAD), "1", -4.0)


def test_a_spread_is_quoted_at_the_weeks_latest_run(seeded_analytics, db_session):
    # A newer run in which roster 1 wins every sim by 10; the moneyline row still carries the seeded run.
    rerun = "2026w10-20261110T143000"
    db_session.session.execute(
        text("""
        INSERT INTO simulation_runs (run_id, season, week, created_at, window_closes_at)
        VALUES (:run_id, 2026, 10, '2026-11-10T14:30:00+00:00', '2026-11-13T00:15:00+00:00')
    """),
        {"run_id": rerun},
    )
    db_session.session.execute(
        text("""
        INSERT INTO simulation_totals (run_id, season, week, created_at, n_sims, roster_ids, totals)
        VALUES (:run_id, 2026, 10, '2026-11-10T14:30:00+00:00', 20, '1,2', :totals)
    """),
        {"run_id": rerun, "totals": encode_totals(np.column_stack([SEEDED_TOTALS[:, 0], SEEDED_TOTALS[:, 0] - 10]))},
    )

    quote = markets.find_quote(markets.parse_key(SPREAD), "1", -9.5)

    assert (quote.run_id, quote.probability) == (rerun, 1.0)


def test_a_spread_quote_leaves_the_admins_lock_to_the_window(seeded_analytics, betting_period):
    betting_period.lock_time = datetime(2026, 11, 1, tzinfo=UTC)
    db.session.commit()

    markets.find_quote(markets.parse_key(SPREAD), "1", -4.0)

    db.session.refresh(betting_period)
    assert betting_period.is_locked is False
