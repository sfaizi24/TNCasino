import pytest
from sqlalchemy import text

from app import markets
from app.markets import Market, MarketError, Quote
from tests.conftest import RUN_ID

KEYS = [
    ("2026-w04-moneyline-1v4", Market("moneyline", 2026, 4, (1, 4))),
    ("2026-w04-team_total-4", Market("team_total", 2026, 4, (4,))),
    ("2026-w04-highest_scorer", Market("highest_scorer", 2026, 4)),
    ("2026-w04-lowest_scorer", Market("lowest_scorer", 2026, 4)),
    ("2026-first_place", Market("first_place", 2026)),
    ("2026-make_playoffs-4", Market("make_playoffs", 2026, None, (4,))),
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
        ("team_total", {"season": 2026, "week": 4, "team_id": 4}, "2026-w04-team_total-4"),
        ("highest_scorer", {"season": 2026, "week": 4, "team_id": 4}, "2026-w04-highest_scorer"),
        ("lowest_scorer", {"season": 2026, "week": 12, "team_id": 4}, "2026-w12-lowest_scorer"),
        ("first_place", {"season": 2026, "week": 4, "team_id": 4}, "2026-first_place"),
        ("make_playoffs", {"season": 2026, "week": 4, "team_id": 4}, "2026-make_playoffs-4"),
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
        "2026-w04-spread-1v4",
        "2026-w04-Moneyline-1v4",
        "2026-w04-moneyline-1v4 ",
    ],
)
def test_parse_key_refuses_any_other_spelling(key):
    with pytest.raises(MarketError, match="Unknown market"):
        markets.parse_key(key)


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


def test_futures_are_quoted_from_the_highest_published_week(seeded_analytics, db_session):
    db_session.session.execute(
        text(
            "INSERT INTO betting_odds_first_place (run_id, week, season, team_id, owner, probability, american_odds) "
            "VALUES ('2026w12-20261124T140000', 12, 2026, 1, 'Alice A', 0.55, '-122')"
        )
    )
    first_place = markets.parse_key("2026-first_place")

    assert markets.find_quote(first_place, "1").run_id == "2026w12-20261124T140000"
    with pytest.raises(MarketError, match="Unknown selection"):
        markets.find_quote(first_place, "2")


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
