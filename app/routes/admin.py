import json
from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import inspect

from .. import ledger
from ..database import db
from .helpers import admin_required, friendly_description, get_current_week, query_analytics

admin_bp = Blueprint("admin", __name__)

# Mirrors STEP_ORDER in the pipeline package, which the web app deliberately does not import.
PIPELINE_STEP_ORDER = [
    "league",
    "scrape",
    "clean",
    "match",
    "stats",
    "accuracy",
    "calibrate",
    "lineups",
    "simulate",
    "odds",
    "playoffs",
    "validate",
    "publish",
]


@admin_bp.route("/admin")
@admin_required
def admin():
    return render_template("admin.html", user=current_user)


@admin_bp.route("/api/admin/betting_periods", methods=["GET"])
@admin_required
def get_betting_periods():
    from ..models import BettingPeriod

    try:
        periods = db.session.query(BettingPeriod).order_by(BettingPeriod.week.desc()).all()

        periods_data = []
        for period in periods:
            periods_data.append(
                {
                    "id": period.id,
                    "week": period.week,
                    "lock_time": period.lock_time.strftime("%Y-%m-%d %I:%M %p UTC"),
                    "is_locked": period.is_locked,
                    "is_settled": period.is_settled,
                }
            )

        return jsonify(periods_data)
    except Exception as e:
        print(f"Error getting betting periods: {e}")
        import traceback

        traceback.print_exc()
        return jsonify([])


@admin_bp.route("/api/admin/set_betting_period", methods=["POST"])
@admin_required
def set_betting_period():
    from ..models import BettingPeriod

    data = request.get_json()
    week = data.get("week")
    lock_time_str = data.get("lock_time")

    if not week or not lock_time_str:
        return jsonify({"success": False, "error": "Week and lock time required"})

    try:
        lock_time = datetime.strptime(lock_time_str, "%Y-%m-%dT%H:%M").replace(tzinfo=UTC)

        period = db.session.query(BettingPeriod).filter_by(week=week).first()

        if period:
            period.lock_time = lock_time
            period.is_locked = False
        else:
            period = BettingPeriod(week=week, lock_time=lock_time, is_locked=False, is_settled=False)
            db.session.add(period)

        db.session.commit()

        return jsonify({"success": True})
    except Exception as e:
        print(f"Error setting betting period: {e}")
        import traceback

        traceback.print_exc()
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


@admin_bp.route("/api/admin/pending_bets", methods=["GET"])
@admin_required
def get_pending_bets():
    from ..models import Bet

    week = request.args.get("week", get_current_week(), type=int)

    try:
        bets = db.session.query(Bet).filter_by(week=week, status="pending").all()

        bets_data = []
        for bet in bets:
            bets_data.append(
                {
                    "id": bet.id,
                    "user_id": bet.user_id,
                    "description": friendly_description(bet.description),
                    "amount": bet.amount,
                    "odds": bet.odds,
                    "potential_win": bet.potential_win,
                    "bet_type": bet.bet_type,
                }
            )

        return jsonify(bets_data)
    except Exception as e:
        print(f"Error getting pending bets: {e}")
        import traceback

        traceback.print_exc()
        return jsonify([])


@admin_bp.route("/api/admin/settle_bet", methods=["POST"])
@admin_required
def settle_bet():
    from ..models import Bet

    data = request.get_json()
    bet_id = data.get("bet_id")
    won = data.get("won", False)

    if not bet_id:
        return jsonify({"success": False, "error": "Bet ID required"})

    try:
        bet = db.session.query(Bet).filter_by(id=bet_id).first()

        if not bet:
            return jsonify({"success": False, "error": "Bet not found"})

        if not ledger.settle(bet, won):
            db.session.rollback()
            return jsonify({"success": False, "error": "Bet already settled"})

        db.session.commit()
        return jsonify({"success": True})
    except Exception as e:
        print(f"Error settling bet: {e}")
        import traceback

        traceback.print_exc()
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


@admin_bp.route("/api/admin/settle_week", methods=["POST"])
@admin_required
def settle_week():
    from ..models import BettingPeriod

    data = request.get_json()
    week = data.get("week")

    if not week:
        return jsonify({"success": False, "error": "Week required"})

    try:
        period = db.session.query(BettingPeriod).filter_by(week=week).first()

        if not period:
            return jsonify({"success": False, "error": "Betting period not found"})

        period.is_settled = True

        db.session.commit()

        return jsonify({"success": True})
    except Exception as e:
        print(f"Error settling week: {e}")
        import traceback

        traceback.print_exc()
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


@admin_bp.route("/api/admin/unlock_period", methods=["POST"])
@admin_required
def unlock_period():
    from ..models import BettingPeriod

    data = request.get_json()
    week = data.get("week")

    print(f"[UNLOCK] Request received for week: {week}")
    print(f"[UNLOCK] Request data: {data}")
    print(f"[UNLOCK] Current user: {current_user.email if current_user.is_authenticated else 'Not authenticated'}")
    print(f"[UNLOCK] Is admin: {getattr(current_user, 'is_admin', False)}")

    if not week:
        print("[UNLOCK] Error: Week not provided")
        return jsonify({"success": False, "error": "Week required"})

    try:
        period = db.session.query(BettingPeriod).filter_by(week=week).first()

        if not period:
            print(f"[UNLOCK] Error: Betting period not found for week {week}")
            return jsonify({"success": False, "error": "Betting period not found"})

        print(
            f"[UNLOCK] Found period: week={period.week}, is_locked={period.is_locked}, is_settled={period.is_settled}, lock_time={period.lock_time}"
        )

        period.is_locked = False
        new_lock_time = datetime.now(UTC) + timedelta(days=7)
        period.lock_time = new_lock_time

        db.session.commit()

        print(f"[UNLOCK] Successfully unlocked week {week}, new lock_time set to {new_lock_time}")

        return jsonify({"success": True})
    except Exception as e:
        print(f"[UNLOCK] Error unlocking period: {e}")
        import traceback

        traceback.print_exc()
        db.session.rollback()
        return jsonify({"success": False, "error": str(e)})


@admin_bp.route("/admin/pipeline")
@admin_required
def admin_pipeline():
    weeks = _pipeline_weeks()
    if not weeks:
        return render_template("admin_pipeline.html", weeks=weeks)

    week = request.args.get("week", default=weeks[0], type=int)
    latest_steps = _latest_steps(week)
    return render_template(
        "admin_pipeline.html",
        weeks=weeks,
        week=week,
        steps=[(name, latest_steps.get(name)) for name in PIPELINE_STEP_ORDER],
        sources=_source_reports(week, latest_steps.get("scrape")),
        runs=_week_runs(week),
    )


@admin_bp.route("/api/admin/pipeline")
@admin_required
def get_pipeline_status():
    weeks = _pipeline_weeks()
    if not weeks:
        return jsonify({"week": request.args.get("week", type=int), "steps": [], "sources": [], "runs": []})

    week = request.args.get("week", default=weeks[0], type=int)
    latest_steps = _latest_steps(week)
    return jsonify(
        {
            "week": week,
            "steps": list(latest_steps.values()),
            "sources": _source_reports(week, latest_steps.get("scrape")),
            "runs": _week_runs(week),
        }
    )


def _pipeline_weeks():
    if not inspect(db.engine).has_table("pipeline_steps"):
        return []
    rows = query_analytics(
        "SELECT DISTINCT r.week FROM pipeline_runs r JOIN pipeline_steps s ON s.run_id = r.run_id ORDER BY r.week DESC"
    )
    return [row["week"] for row in rows]


def _latest_steps(week):
    rows = query_analytics(
        """
        SELECT s.run_id, s.step, s.started_at, s.finished_at, s.duration_s, s.status,
               s.summary, s.warnings, s.charts, s.error
        FROM pipeline_steps s
        JOIN pipeline_runs r ON r.run_id = s.run_id
        WHERE r.week = :week
        ORDER BY s.started_at, s.run_id
        """,
        {"week": week},
    )
    # Rows come oldest first, so a rerun's row replaces the earlier row for the same step.
    latest = {row["step"]: row for row in rows}
    for step in latest.values():
        step["summary"] = _parse_json(step["summary"], {})
        step["warnings"] = _parse_json(step["warnings"], [])
        step["charts"] = _parse_json(step["charts"], [])
    return {name: latest[name] for name in PIPELINE_STEP_ORDER if name in latest}


def _source_reports(week, scrape):
    if scrape is None:
        return []
    reviews = query_analytics(
        "SELECT source, verdict, note FROM source_reviews WHERE week = :week",
        {"week": week},
    )
    reviews_by_source = {review["source"]: review for review in reviews}

    reports = []
    for report in scrape["summary"].get("sources", []):
        review = reviews_by_source.get(report["source"], {})
        reports.append({**report, "verdict": review.get("verdict"), "note": review.get("note")})
    return reports


def _week_runs(week):
    runs = query_analytics(
        "SELECT run_id, status, steps, started_at, finished_at, git_sha, error "
        "FROM pipeline_runs WHERE week = :week ORDER BY started_at DESC",
        {"week": week},
    )
    for run in runs:
        run["steps"] = json.loads(run["steps"])
    return runs


def _parse_json(value, default):
    return json.loads(value) if value else default
