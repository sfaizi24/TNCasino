import logging
from datetime import UTC, datetime

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user, login_required
from sqlalchemy import Integer, case, cast, desc, distinct, func

from .. import ledger
from ..database import db
from .helpers import (
    check_betting_period_lock,
    friendly_description,
    get_current_week,
    get_team_mapping,
    query_analytics,
)

betting_bp = Blueprint("betting", __name__)


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
    from ..models import BettingPeriod

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
    from ..models import Bet, User, WeeklyStats

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
            )
            .filter(Bet.bet_type == bet_type)
            .group_by(Bet.description)
            .order_by(desc("count"))
            .first()
        )
        return result

    popular_moneyline = get_popular_bet_with_stats("moneyline")
    popular_over_under = get_popular_bet_with_stats("team_ou")
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
    from ..models import Bet

    data = request.get_json()
    bet_type = data.get("bet_type", "moneyline")
    amount = float(data.get("amount", 0))
    week = get_current_week()

    print(
        f"[BET REQUEST] User: {current_user.id} ({current_user.username}), Type: {bet_type}, Amount: {amount}, Week: {week}, Balance: {current_user.account_balance}"
    )
    print(f"[BET REQUEST] Full data: {data}")

    lock_time = check_betting_period_lock(week)
    if lock_time:
        print(f"[BET REJECTED] User {current_user.id} - Betting locked at {lock_time}")
        return jsonify(
            {"success": False, "error": f"Bets are locked as of {lock_time.strftime('%Y-%m-%d %I:%M %p UTC')}"}
        )

    if amount <= 0:
        print(f"[BET REJECTED] User {current_user.id} - Invalid amount: {amount}")
        return jsonify({"success": False, "error": "Invalid bet amount"})

    if current_user.account_balance < amount:
        print(
            f"[BET REJECTED] User {current_user.id} - Insufficient balance: {current_user.account_balance} < {amount}"
        )
        return jsonify({"success": False, "error": "Insufficient balance"})

    try:
        if bet_type == "highest_scorer":
            owner = data.get("owner")
            odds = data.get("odds")

            if not owner or not odds:
                print(
                    f"[BET REJECTED] User {current_user.id} - Highest scorer missing data: owner={owner}, odds={odds}"
                )
                return jsonify({"success": False, "error": "Missing required data"})

            odds_num = int(odds.replace("+", ""))
            if odds.startswith("+"):
                potential_win = amount * (odds_num / 100)
            else:
                potential_win = amount * (100 / abs(odds_num))

            description = f"{owner}: Highest Scorer {odds}"

            bet = Bet(
                user_id=current_user.id,
                bet_type="highest_scorer",
                description=description,
                week=week,
                amount=amount,
                odds=odds,
                potential_win=potential_win,
                status="pending",
                created_at=datetime.now(UTC),
            )
            return _record_bet(bet)

        if bet_type == "lowest_scorer":
            owner = data.get("owner")
            odds = data.get("odds")

            if not owner or not odds:
                print(f"[BET REJECTED] User {current_user.id} - Lowest scorer missing data: owner={owner}, odds={odds}")
                return jsonify({"success": False, "error": "Missing required data"})

            odds_num = int(odds.replace("+", ""))
            if odds.startswith("+"):
                potential_win = amount * (odds_num / 100)
            else:
                potential_win = amount * (100 / abs(odds_num))

            description = f"{owner}: Lowest Scorer {odds}"

            bet = Bet(
                user_id=current_user.id,
                bet_type="lowest_scorer",
                description=description,
                week=week,
                amount=amount,
                odds=odds,
                potential_win=potential_win,
                status="pending",
                created_at=datetime.now(UTC),
            )
            return _record_bet(bet)

        if bet_type == "first_seed":
            owner = data.get("owner")
            odds = data.get("odds")

            if not owner or not odds:
                print(f"[BET REJECTED] User {current_user.id} - First seed missing data: owner={owner}, odds={odds}")
                return jsonify({"success": False, "error": "Missing required data"})

            odds_num = int(odds.replace("+", ""))
            if odds.startswith("+"):
                potential_win = amount * (odds_num / 100)
            else:
                potential_win = amount * (100 / abs(odds_num))

            description = f"{owner}: #1 Seed {odds}"

            bet = Bet(
                user_id=current_user.id,
                bet_type="first_seed",
                description=description,
                week=week,
                amount=amount,
                odds=odds,
                potential_win=potential_win,
                status="pending",
                created_at=datetime.now(UTC),
            )
            return _record_bet(bet)

        if bet_type == "ammad_playoff":
            owner = data.get("owner")
            odds = data.get("odds")

            if not owner or not odds:
                print(f"[BET REJECTED] User {current_user.id} - Ammad playoff missing data: owner={owner}, odds={odds}")
                return jsonify({"success": False, "error": "Missing required data"})

            odds_num = int(odds.replace("+", ""))
            if odds.startswith("+"):
                potential_win = amount * (odds_num / 100)
            else:
                potential_win = amount * (100 / abs(odds_num))

            description = f"{owner}: Ammad Playoff {odds}"

            bet = Bet(
                user_id=current_user.id,
                bet_type="ammad_playoff",
                description=description,
                week=week,
                amount=amount,
                odds=odds,
                potential_win=potential_win,
                status="pending",
                created_at=datetime.now(UTC),
            )
            return _record_bet(bet)

        if bet_type == "team_ou":
            team_idx = data.get("team_idx")
            choice = data.get("choice")

            teams = query_analytics(
                "SELECT * FROM betting_odds_team_ou WHERE week = :week ORDER BY owner",
                {"week": week},
            )

            if team_idx >= len(teams):
                print(f"[BET REJECTED] User {current_user.id} - Invalid team_idx: {team_idx} (max: {len(teams) - 1})")
                return jsonify({"success": False, "error": "Invalid team"})

            team_data = teams[team_idx]
            owner = team_data["owner"]
            line = team_data["line"]

            potential_win = amount
            description = friendly_description(f"{owner} O/U {line:.1f}: {choice.capitalize()}")

            bet = Bet(
                user_id=current_user.id,
                bet_type="team_ou",
                description=description,
                week=week,
                amount=amount,
                odds="EVEN",
                potential_win=potential_win,
                status="pending",
                created_at=datetime.now(UTC),
            )
            return _record_bet(bet)

        # Moneyline bets
        matchup_idx = data.get("matchup_idx")
        team = data.get("team")
        team_mapping = get_team_mapping(week)

        matchups = query_analytics(
            "SELECT * FROM betting_odds_matchup_ml WHERE week = :week ORDER BY matchup",
            {"week": week},
        )

        if matchup_idx >= len(matchups):
            print(
                f"[BET REJECTED] User {current_user.id} - Invalid matchup_idx: {matchup_idx} (max: {len(matchups) - 1})"
            )
            return jsonify({"success": False, "error": "Invalid matchup"})

        matchup = matchups[matchup_idx]

        team1_owner = team_mapping.get(matchup["team1_id"], f"Team {matchup['team1_id']}")
        team2_owner = team_mapping.get(matchup["team2_id"], f"Team {matchup['team2_id']}")
        matchup_display = f"{team1_owner} vs {team2_owner}"

        if team == "team1":
            team_name = team1_owner
            odds = matchup["team1_ml"]
        elif team == "team2":
            team_name = team2_owner
            odds = matchup["team2_ml"]
        else:
            print(f"[BET REJECTED] User {current_user.id} - Invalid team selection: {team}")
            return jsonify({"success": False, "error": "Invalid team"})

        odds_num = int(odds)
        if odds_num > 0:
            potential_win = amount * (odds_num / 100)
        else:
            potential_win = amount * (100 / abs(odds_num))

        description = f"{matchup_display}: {team_name} {odds}"

        bet = Bet(
            user_id=current_user.id,
            bet_type="moneyline",
            description=description,
            week=week,
            amount=amount,
            odds=odds,
            potential_win=potential_win,
            status="pending",
            created_at=datetime.now(UTC),
        )
        return _record_bet(bet)

    except Exception as e:
        # Roll back before the prints below: a failed transaction cannot reload current_user.
        db.session.rollback()
        print(f"[BET ERROR] User {current_user.id} - Exception occurred: {type(e).__name__}: {str(e)}")
        print(f"[BET ERROR] Request data was: {data}")
        print(f"[BET ERROR] User balance: {current_user.account_balance}, Bet amount: {amount}")
        import traceback

        traceback.print_exc()
        print(f"[BET ERROR] Database rolled back for user {current_user.id}")
        return jsonify({"success": False, "error": str(e)})


def _record_bet(bet):
    ledger.open_week(bet.user_id, bet.week)
    if not ledger.place(bet):
        db.session.rollback()
        logging.info(f"Refused bet for user {bet.user_id}: balance below the {bet.amount} stake")
        return jsonify({"success": False, "error": "Insufficient balance"})

    db.session.commit()
    db.session.refresh(current_user)
    new_balance = current_user.account_balance
    logging.info(f"User {bet.user_id} placed {bet.bet_type} bet {bet.description}, new balance {new_balance}")
    return jsonify({"success": True, "new_balance": new_balance})


@betting_bp.route("/api/my_bets")
@login_required
def get_my_bets():
    from ..models import Bet

    try:
        bets = (
            db.session.query(Bet)
            .filter_by(user_id=current_user.id, status="pending")
            .order_by(Bet.created_at.desc())
            .all()
        )

        bets_data = []
        for bet in bets:
            bets_data.append(
                {
                    "id": bet.id,
                    "description": friendly_description(bet.description),
                    "amount": bet.amount,
                    "odds": bet.odds,
                    "potential_win": bet.potential_win,
                    "status": bet.status,
                    "week": bet.week,
                }
            )

        return jsonify(bets_data)

    except Exception as e:
        print(f"Error getting bets: {e}")
        import traceback

        traceback.print_exc()
        return jsonify([])


@betting_bp.route("/api/remove_bet/<int:bet_id>", methods=["DELETE"])
@login_required
def remove_bet(bet_id):
    from ..models import Bet

    try:
        bet = db.session.query(Bet).filter_by(id=bet_id, user_id=current_user.id, status="pending").first()

        if not bet:
            return jsonify({"success": False, "error": "Bet not found"})

        lock_time = check_betting_period_lock(bet.week)
        if lock_time:
            return jsonify(
                {"success": False, "error": f"Bets are locked as of {lock_time.strftime('%Y-%m-%d %I:%M %p UTC')}"}
            )

        if not ledger.remove(bet):
            # Settled or removed by another request since the read above.
            db.session.rollback()
            return jsonify({"success": False, "error": "Bet not found"})

        db.session.commit()
        db.session.refresh(current_user)
        return jsonify({"success": True, "new_balance": current_user.account_balance})

    except Exception as e:
        print(f"Error removing bet: {e}")
        import traceback

        traceback.print_exc()
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


@betting_bp.route("/api/session-check")
def check_session():
    if current_user.is_authenticated:
        return jsonify({"authenticated": True, "username": current_user.username})
    return jsonify({"authenticated": False}), 401
