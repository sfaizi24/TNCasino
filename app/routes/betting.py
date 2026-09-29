import logging

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import Integer, case, cast, desc, distinct, func

from .. import ledger, markets
from ..database import db
from ..models import Bet, BetLeg, BettingPeriod, User, WeeklyStats
from .helpers import check_betting_period_lock, friendly_description, get_current_week, get_team_mapping

betting_bp = Blueprint("betting", __name__)

# How a description names each market whose selection is a team.
MARKET_LABELS = {
    "highest_scorer": "Highest Scorer",
    "lowest_scorer": "Lowest Scorer",
    "first_place": "First Place",
    "make_playoffs": "Make Playoffs",
}


def _format_lock_time(lock_time):
    if not lock_time:
        return "Thursday Night Kickoff"

    from zoneinfo import ZoneInfo

    eastern = lock_time.astimezone(ZoneInfo("America/New_York"))
    day = eastern.strftime("%b ") + str(eastern.day)
    hour = eastern.hour % 12 or 12
    suffix = "AM" if eastern.hour < 12 else "PM"
    minute_part = f":{eastern.minute:02d}" if eastern.minute else ""
    return f"{day}, {hour}{minute_part}{suffix} ET"


@betting_bp.route("/betting")
def betting():
    week = get_current_week()
    period = db.session.query(BettingPeriod).filter_by(week=week).first()

    return render_template(
        "betting.html",
        user=current_user if current_user.is_authenticated else None,
        current_week=week,
        bets_open_until=_format_lock_time(period.lock_time if period else None),
    )


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

    lock_time = check_betting_period_lock(week)
    if lock_time:
        return _refuse_locked(lock_time)
    if amount <= 0:
        return _refuse("Invalid bet amount")
    if current_user.account_balance < amount:
        return _refuse("Insufficient balance")

    try:
        market = markets.parse_key(data.get("market"))
        if market.week is not None and market.week != week:
            return _refuse("Not this week's market")

        selection = str(data.get("selection"))
        quote = markets.find_quote(market, selection)
        if _quote_moved(market, quote, data):
            return _refuse(
                "Odds have changed", run_id=quote.run_id, price=quote.price, odds=quote.odds, line=quote.line
            )
        if quote.price is None:
            return _refuse("Not offered")

        return _record_bet(_new_bet(market, selection, quote, amount, week))
    except markets.MarketError as error:
        return _refuse(str(error))
    except Exception as error:
        # Roll back before logging: a failed transaction cannot reload current_user.
        db.session.rollback()
        logging.exception(f"Bet by user {current_user.id} failed: {data}")
        return _refuse(str(error))


def _quote_moved(market, quote, data):
    """Whether the bettor was shown another run's price, or a team total at another line."""
    if data.get("run_id") != quote.run_id:
        return True
    if market.name != "team_total":
        return False
    try:
        return round(float(data.get("line")), 2) != round(quote.line, 2)
    except (TypeError, ValueError):
        return True


def _new_bet(market, selection, quote, amount, week):
    leg = BetLeg(
        season=market.season,
        week=market.week,
        market=market.key,
        selection=selection,
        line=quote.line,
        price=quote.price,
        probability=quote.probability,
    )
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
        legs=[leg],
    )


def _describe(market, selection, quote, week):
    names = get_team_mapping(week)

    def name(roster_id):
        return names.get(roster_id, f"Team {roster_id}")

    if market.name == "moneyline":
        team1, team2 = market.teams
        return f"{name(team1)} vs {name(team2)}: {name(int(selection))} {quote.odds}"
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
    leg = bet.legs[0]
    return jsonify(
        {
            "success": True,
            "new_balance": new_balance,
            "bet_id": bet.id,
            "market": leg.market,
            "selection": leg.selection,
            "price": bet.price,
        }
    )


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
        return jsonify([_bet_summary(bet) for bet in bets])
    except Exception:
        logging.exception(f"Could not list the bets of user {current_user.id}")
        return jsonify([])


def _bet_summary(bet):
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
        "removable": check_betting_period_lock(bet.week) is None and _run_is_latest(bet),
    }
    if bet.legs:
        leg = bet.legs[0]
        summary.update(market=leg.market, selection=leg.selection, line=leg.line)
    return summary


def _run_is_latest(bet):
    """Whether the bet's market still shows the run it was priced from; a legacy bet has no market to check."""
    if not bet.legs:
        return True
    leg = bet.legs[0]
    try:
        quote = markets.find_quote(markets.parse_key(leg.market), leg.selection)
    except markets.MarketError:
        return False
    return quote.run_id == bet.run_id


@betting_bp.route("/api/remove_bet/<int:bet_id>", methods=["DELETE"])
@login_required
def remove_bet(bet_id):
    try:
        bet = db.session.query(Bet).filter_by(id=bet_id, user_id=current_user.id, status="pending").first()
        if not bet:
            return _refuse("Bet not found")

        lock_time = check_betting_period_lock(bet.week)
        if lock_time:
            return _refuse_locked(lock_time)
        if not _run_is_latest(bet):
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


def _refuse(error, **details):
    return jsonify({"success": False, "error": error, **details})


def _refuse_locked(lock_time):
    return _refuse(f"Bets are locked as of {lock_time.strftime('%Y-%m-%d %I:%M %p UTC')}")


@betting_bp.route("/api/session-check")
def check_session():
    if current_user.is_authenticated:
        return jsonify({"authenticated": True, "username": current_user.username})
    return jsonify({"authenticated": False}), 401
