import json
import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import Integer, case, cast, desc, distinct, func

from .. import cashout, ledger, markets, parlays
from ..database import db
from ..models import Bet, BetLeg, ParlayRefusal, User, WeeklyStats
from ..windows import betting_window
from .helpers import friendly_description, get_current_week, get_team_mapping

betting_bp = Blueprint("betting", __name__)

# How a description names each market whose selection is a team.
MARKET_LABELS = {
    "highest_scorer": "Highest Scorer",
    "lowest_scorer": "Lowest Scorer",
    "first_place": "First Place",
    "make_playoffs": "Make Playoffs",
}

# The parlay refusals the owner counts to decide whether scorer legs stay (design §1.4).
LOGGED_RULES = ("same_market", "impossible", "redundant")


@betting_bp.route("/betting")
def betting():
    return render_template(
        "betting.html",
        user=current_user if current_user.is_authenticated else None,
        current_week=get_current_week(),
    )


@betting_bp.route("/api/betting_window")
def get_betting_window():
    week = request.args.get("week", get_current_week(), type=int)
    window = betting_window(week)
    return jsonify(
        {
            "success": True,
            "week": window.week,
            "state": window.state,
            "closes_at": _iso(window.closes_at),
            "run_created_at": _iso(window.run_created_at),
        }
    )


def _iso(timestamp):
    return timestamp.isoformat() if timestamp else None


@betting_bp.route("/leaderboard")
def leaderboard():
    current_week = get_current_week()
    selected_week = request.args.get("week", current_week, type=int)

    available_weeks = db.session.query(distinct(WeeklyStats.week)).order_by(desc(WeeklyStats.week)).all()
    available_weeks = [w[0] for w in available_weeks]

    users_with_bets = db.session.query(Bet.user_id).group_by(Bet.user_id).subquery()

    alltime_top = (
        db.session.query(User.id, User.first_name, User.last_name, User.total_pnl)
        .join(users_with_bets, User.id == users_with_bets.c.user_id)
        .order_by(desc(User.total_pnl))
        .limit(3)
        .all()
    )

    alltime_bottom = (
        db.session.query(User.id, User.first_name, User.last_name, User.total_pnl)
        .join(users_with_bets, User.id == users_with_bets.c.user_id)
        .order_by(User.total_pnl.asc())
        .limit(2)
        .all()
    )

    weekly_top = (
        db.session.query(User.id, User.first_name, User.last_name, WeeklyStats.settled_pnl)
        .join(WeeklyStats, User.id == WeeklyStats.user_id)
        .filter(WeeklyStats.week == selected_week, WeeklyStats.bets_placed > 0)
        .order_by(desc(WeeklyStats.settled_pnl))
        .limit(3)
        .all()
    )

    weekly_bottom = (
        db.session.query(User.id, User.first_name, User.last_name, WeeklyStats.settled_pnl)
        .join(WeeklyStats, User.id == WeeklyStats.user_id)
        .filter(WeeklyStats.week == selected_week, WeeklyStats.bets_placed > 0)
        .order_by(WeeklyStats.settled_pnl.asc())
        .limit(2)
        .all()
    )

    best_odds_bet = (
        db.session.query(
            Bet.description,
            Bet.odds,
            func.sum(Bet.amount).label("amount"),
            func.sum(Bet.result).label("result"),
            User.first_name,
            User.last_name,
            Bet.week,
        )
        .join(User, Bet.user_id == User.id)
        .filter(Bet.status == "won")
        .group_by(Bet.user_id, Bet.description, Bet.odds, Bet.week, User.first_name, User.last_name)
        .order_by(desc(cast(func.replace(func.replace(Bet.odds, "+", ""), "EVEN", "0"), Integer)))
        .first()
    )

    most_money_won = (
        db.session.query(
            Bet.description,
            Bet.odds,
            func.sum(Bet.amount).label("amount"),
            func.sum(Bet.result).label("result"),
            User.first_name,
            User.last_name,
            Bet.week,
        )
        .join(User, Bet.user_id == User.id)
        .filter(Bet.status == "won")
        .group_by(Bet.user_id, Bet.description, Bet.odds, Bet.week, User.first_name, User.last_name)
        .order_by(desc(func.sum(Bet.result)))
        .first()
    )

    worst_odds_bet = (
        db.session.query(
            Bet.description,
            Bet.odds,
            func.sum(Bet.amount).label("amount"),
            Bet.result,
            User.first_name,
            User.last_name,
            Bet.week,
        )
        .join(User, Bet.user_id == User.id)
        .filter(Bet.status == "lost")
        .group_by(Bet.user_id, Bet.description, Bet.odds, Bet.week, Bet.result, User.first_name, User.last_name)
        .order_by(cast(func.replace(func.replace(Bet.odds, "+", ""), "EVEN", "0"), Integer).asc())
        .first()
    )

    biggest_loss = (
        db.session.query(
            Bet.description,
            Bet.odds,
            func.sum(Bet.amount).label("amount"),
            Bet.result,
            User.first_name,
            User.last_name,
            Bet.week,
        )
        .join(User, Bet.user_id == User.id)
        .filter(Bet.status == "lost")
        .group_by(Bet.user_id, Bet.description, Bet.odds, Bet.week, Bet.result, User.first_name, User.last_name)
        .order_by(desc(func.sum(Bet.amount)))
        .first()
    )

    def get_popular_bet_with_stats(bet_type):
        result = (
            db.session.query(
                Bet.description,
                func.count(Bet.id).label("count"),
                func.sum(case((Bet.status == "won", 1), else_=0)).label("wins"),
                func.sum(case((Bet.status == "lost", 1), else_=0)).label("losses"),
                func.sum(case((Bet.status == "pending", 1), else_=0)).label("pending"),
                func.sum(Bet.amount).label("total_wagered"),
                func.max(Bet.week).label("week"),
                func.sum(case((Bet.status == "push", 1), else_=0)).label("pushes"),
            )
            .filter(Bet.bet_type == bet_type, Bet.status.notin_(("removed", "void")))
            .group_by(Bet.description)
            .order_by(desc("count"))
            .first()
        )
        return result

    popular_moneyline = get_popular_bet_with_stats("moneyline")
    popular_over_under = get_popular_bet_with_stats("team_total")
    popular_highest = get_popular_bet_with_stats("highest_scorer")
    popular_lowest = get_popular_bet_with_stats("lowest_scorer")

    return render_template(
        "leaderboard.html",
        user=current_user if current_user.is_authenticated else None,
        current_week=current_week,
        selected_week=selected_week,
        available_weeks=available_weeks,
        weekly_top=weekly_top,
        weekly_bottom=weekly_bottom,
        alltime_top=alltime_top,
        alltime_bottom=alltime_bottom,
        best_odds_bet=best_odds_bet,
        most_money_won=most_money_won,
        worst_odds_bet=worst_odds_bet,
        biggest_loss=biggest_loss,
        popular_moneyline=popular_moneyline,
        popular_over_under=popular_over_under,
        popular_highest=popular_highest,
        popular_lowest=popular_lowest,
    )


@betting_bp.route("/api/place_bet", methods=["POST"])
@login_required
def place_bet():
    data = request.get_json()
    amount = float(data.get("amount", 0))
    week = get_current_week()

    window = betting_window(week)
    if window.state != "open":
        return _refuse_window(window)
    if amount <= 0:
        return _refuse("Invalid bet amount")
    if current_user.account_balance < amount:
        return _refuse("Insufficient balance")

    try:
        legs = data.get("legs") if isinstance(data.get("legs"), list) else []
        if len(legs) > 1:
            parlay = _quote_parlay(legs, data.get("run_id"), week, window)
            return _record_bet(_new_parlay(parlay, amount, week))
        # The page sends a lone pick as a one-leg list; other clients send it at the top level.
        pick = legs[0] if legs and isinstance(legs[0], dict) else data
        return _place_single(pick, data.get("run_id"), amount, week)
    except markets.MarketError as error:
        return _refuse(str(error))
    except parlays.ParlayRefusal as refusal:
        return _refuse_parlay(refusal, window)
    except Exception as error:
        # Roll back before logging: a failed transaction cannot reload current_user.
        db.session.rollback()
        logging.exception(f"Bet by user {current_user.id} failed: {data}")
        return _refuse(str(error))


def _place_single(pick, run_id, amount, week):
    market = markets.parse_key(pick.get("market"))
    if market.week is not None and market.week != week:
        return _refuse("Not this week's market")

    selection = str(pick.get("selection"))
    quote = markets.find_quote(market, selection, pick.get("line"))
    if _quote_moved(market, quote, run_id, pick.get("line")):
        return _refuse("Odds have changed", run_id=quote.run_id, price=quote.price, odds=quote.odds, line=quote.line)
    if quote.price is None:
        return _refuse("Not offered")

    return _record_bet(_new_bet(market, selection, quote, amount, week))


def _quote_moved(market, quote, run_id, line):
    """Whether the bettor was shown another run's price, or a team total at another line."""
    if run_id != quote.run_id:
        return True
    if market.name != "team_total":
        return False
    try:
        return round(float(line), 2) != round(quote.line, 2)
    except (TypeError, ValueError):
        return True


@betting_bp.route("/api/parlay_quote", methods=["POST"])
@login_required
def parlay_quote():
    data = request.get_json()
    legs = data.get("legs")
    week = get_current_week()

    window = betting_window(week)
    if window.state != "open":
        return _refuse_window(window)
    if not isinstance(legs, list):
        return _refuse("A parlay has 2 to 4 legs", rule="size", legs=[])

    try:
        parlay = _quote_parlay(legs, data.get("run_id"), week, window)
    except parlays.ParlayRefusal as refusal:
        if refusal.rule in LOGGED_RULES:
            _log_refusal(legs, week, window.run_id, refusal.rule)
        return _refuse_parlay(refusal, window)
    except Exception as error:
        db.session.rollback()
        logging.exception(f"Parlay quote for user {current_user.id} failed: {data}")
        return _refuse(str(error))

    return jsonify(
        {
            "success": True,
            "run_id": parlay.run_id,
            "probability": parlay.probability,
            "odds": parlay.odds,
            "price": parlay.price,
            "legs": [
                {
                    "market": leg.market.key,
                    "selection": leg.selection,
                    "line": leg.line,
                    "odds": leg.quote.odds,
                    "price": leg.quote.price,
                    "probability": leg.quote.probability,
                }
                for leg in parlay.legs
            ],
        }
    )


def _quote_parlay(legs, run_id, week, window):
    """The parlay at the window's run; a page showing another run's prices has seen every leg change."""
    if run_id != window.run_id:
        keys = [leg.get("market") if isinstance(leg, dict) else None for leg in legs]
        raise parlays.ParlayRefusal("Odds have changed", "odds_changed", keys)
    return parlays.quote(legs, week, window.run_id)


def _log_refusal(legs, week, run_id, rule):
    db.session.add(ParlayRefusal(user_id=current_user.id, week=week, run_id=run_id, legs=json.dumps(legs), rule=rule))
    db.session.commit()


def _refuse_parlay(refusal, window):
    details = {"rule": refusal.rule, "legs": list(refusal.legs)}
    if refusal.rule == "odds_changed":
        details["run_id"] = window.run_id
    return _refuse(str(refusal), **details)


def _new_bet(market, selection, quote, amount, week):
    return Bet(
        user_id=current_user.id,
        bet_type=market.name,
        description=_describe(market, selection, quote, week),
        week=week,
        amount=amount,
        odds=quote.odds,
        price=quote.price,
        probability=quote.probability,
        run_id=quote.run_id,
        potential_win=markets.potential_win(amount, quote.price),
        legs=[_new_leg(market, selection, quote)],
    )


def _new_parlay(parlay, amount, week):
    return Bet(
        user_id=current_user.id,
        bet_type="parlay",
        description=" + ".join(_describe(leg.market, leg.selection, leg.quote, week) for leg in parlay.legs),
        week=week,
        amount=amount,
        odds=parlay.odds,
        price=parlay.price,
        probability=parlay.probability,
        run_id=parlay.run_id,
        potential_win=markets.potential_win(amount, parlay.price),
        legs=[_new_leg(leg.market, leg.selection, leg.quote) for leg in parlay.legs],
    )


def _new_leg(market, selection, quote):
    return BetLeg(
        season=market.season,
        week=market.week,
        market=market.key,
        selection=selection,
        line=quote.line,
        price=quote.price,
        probability=quote.probability,
    )


def _describe(market, selection, quote, week):
    names = get_team_mapping(week)

    def name(roster_id):
        return names.get(roster_id, f"Team {roster_id}")

    if market.name == "moneyline":
        team1, team2 = market.teams
        return f"{name(team1)} vs {name(team2)}: {name(int(selection))} {quote.odds}"
    if market.name == "spread":
        team1, team2 = market.teams
        return f"{name(team1)} vs {name(team2)}: {name(int(selection))} {quote.line:+.1f} {quote.odds}"
    if market.name == "team_total":
        return f"{name(market.teams[0])} O/U {quote.line:.2f}: {selection.capitalize()}"
    team = market.teams[0] if market.teams else int(selection)
    return f"{name(team)}: {MARKET_LABELS[market.name]} {quote.odds}"


def _record_bet(bet):
    ledger.open_week(bet.user_id, bet.week)
    if not ledger.place(bet):
        db.session.rollback()
        logging.info(f"Refused bet for user {bet.user_id}: balance below the {bet.amount} stake")
        return _refuse("Insufficient balance")

    db.session.commit()
    db.session.refresh(current_user)
    new_balance = current_user.account_balance
    logging.info(f"User {bet.user_id} placed {bet.bet_type} bet {bet.description}, new balance {new_balance}")
    reply = {
        "success": True,
        "new_balance": new_balance,
        "bet_id": bet.id,
        "market": None,
        "selection": None,
        "price": bet.price,
        "legs": [leg.market for leg in bet.legs],
    }
    # A parlay's picks are its legs; only a single is one market and selection.
    if len(bet.legs) == 1:
        reply.update(market=bet.legs[0].market, selection=bet.legs[0].selection)
    return jsonify(reply)


@betting_bp.route("/api/my_bets")
@login_required
def get_my_bets():
    try:
        bets = (
            db.session.query(Bet)
            .filter_by(user_id=current_user.id, status="pending")
            .order_by(Bet.created_at.desc())
            .all()
        )
        windows = {week: betting_window(week) for week in {bet.week for bet in bets}}
        offers = cashout.offers_for(bets)
        return jsonify([_bet_summary(bet, windows[bet.week], offers.get(bet.id)) for bet in bets])
    except Exception:
        logging.exception(f"Could not list the bets of user {current_user.id}")
        return jsonify([])


def _bet_summary(bet, window, offer):
    summary = {
        "id": bet.id,
        "description": friendly_description(bet.description),
        "amount": bet.amount,
        "odds": bet.odds,
        "potential_win": bet.potential_win,
        "status": bet.status,
        "week": bet.week,
        "bet_type": bet.bet_type,
        "market": None,
        "selection": None,
        "line": None,
        "price": bet.price,
        "probability": bet.probability,
        "run_id": bet.run_id,
        # Removal needs the bet's own run to be the latest and an offer needs a newer one, so a bet never has both.
        "removable": window.state == "open" and _run_is_latest(bet, window),
        "cash_out_offer": offer.amount if offer else None,
        "legs": [
            {
                "market": leg.market,
                "selection": leg.selection,
                "line": leg.line,
                "price": leg.price,
                "odds": f"{leg.price:+d}",
            }
            for leg in bet.legs
        ],
    }
    # A parlay marks no card side, so only a single names its market and selection.
    if len(bet.legs) == 1:
        leg = bet.legs[0]
        summary.update(market=leg.market, selection=leg.selection, line=leg.line)
    return summary


def _run_is_latest(bet, window):
    """Whether the bet was priced from its week's latest run, the same run a cash-out offer compares against."""
    if any(leg.week is not None for leg in bet.legs):
        return bet.run_id == window.run_id
    # A futures bet has no window run, so its odds row decides; a legacy bet has no legs to check.
    return all(_quotes_run(leg, bet.run_id) for leg in bet.legs)


def _quotes_run(leg, run_id):
    try:
        quote = markets.find_quote(markets.parse_key(leg.market), leg.selection)
    except markets.MarketError:
        return False
    return quote.run_id == run_id


@betting_bp.route("/api/remove_bet/<int:bet_id>", methods=["DELETE"])
@login_required
def remove_bet(bet_id):
    try:
        bet = db.session.query(Bet).filter_by(id=bet_id, user_id=current_user.id, status="pending").first()
        if not bet:
            return _refuse("Bet not found")

        window = betting_window(bet.week)
        if window.state != "open":
            return _refuse_window(window)
        if not _run_is_latest(bet, window):
            return _refuse("Odds have changed since this bet was placed")

        if not ledger.remove(bet):
            # Settled or removed by another request since the read above.
            db.session.rollback()
            return _refuse("Bet not found")

        db.session.commit()
        db.session.refresh(current_user)
        return jsonify({"success": True, "new_balance": current_user.account_balance})
    except Exception as error:
        db.session.rollback()
        logging.exception(f"Could not remove bet {bet_id} for user {current_user.id}")
        return _refuse(str(error))


@betting_bp.route("/api/cash_out/<int:bet_id>", methods=["POST"])
@login_required
def cash_out_bet(bet_id):
    try:
        bet = db.session.query(Bet).filter_by(id=bet_id, user_id=current_user.id, status="pending").first()
        if not bet:
            return _refuse("Bet not found")

        offer = cashout.offer_for(bet)
        if round(float(request.get_json()["offer"]), 2) != offer.amount:
            return _refuse("Offer has changed", offer=offer.amount)

        week = get_current_week()
        ledger.open_week(current_user.id, week)
        if not ledger.cash_out(bet, offer.amount, offer.run_id, week):
            # Settled, removed or cashed out by another request since the read above.
            db.session.rollback()
            return _refuse("Bet not found")

        db.session.commit()
        db.session.refresh(current_user)
        new_balance = current_user.account_balance
        logging.info(
            f"User {current_user.id} cashed out bet {bet_id} for {offer.amount} at run {offer.run_id}, "
            f"new balance {new_balance}"
        )
        return jsonify({"success": True, "new_balance": new_balance, "cash_out_amount": offer.amount})
    except cashout.NoOffer as no_offer:
        return _refuse(str(no_offer))
    except Exception as error:
        db.session.rollback()
        logging.exception(f"Could not cash out bet {bet_id} for user {current_user.id}")
        return _refuse(str(error))


def _refuse(error, **details):
    return jsonify({"success": False, "error": error, **details})


def _refuse_window(window):
    if window.lock_time:
        return _refuse(f"Bets are locked as of {window.lock_time.strftime('%Y-%m-%d %I:%M %p UTC')}")
    if window.state == "paused":
        return _refuse("Betting is paused until the odds update")
    return _refuse(f"Betting is closed for week {window.week}")


@betting_bp.route("/api/session-check")
def check_session():
    if current_user.is_authenticated:
        return jsonify({"authenticated": True, "username": current_user.username})
    return jsonify({"authenticated": False}), 401
