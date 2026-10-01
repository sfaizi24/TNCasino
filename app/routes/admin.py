import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime, timedelta

from flask import Blueprint, jsonify, render_template, request
from flask_login import current_user
from sqlalchemy import inspect, or_

from .. import ledger, settlement
from ..database import db
from ..models import Bet, BetLeg
from .helpers import admin_required, friendly_description, get_current_week, query_analytics
from .pipeline_summary import summary_sections

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
    week = request.args.get("week", get_current_week(), type=int)

    try:
        # A futures bet stays listed after its week, for its Win and Loss buttons once its market is decided.
        listed = or_(Bet.week == week, Bet.legs.any(BetLeg.week.is_(None)))
        bets = db.session.query(Bet).filter(Bet.status == "pending", listed).order_by(Bet.id).all()

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
                    "by_hand": _settles_by_hand(bet),
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
    data = request.get_json()
    bet_id = data.get("bet_id")
    won = data.get("won", False)

    if not bet_id:
        return jsonify({"success": False, "error": "Bet ID required"})

    try:
        bet = db.session.query(Bet).filter_by(id=bet_id).first()

        if not bet:
            return jsonify({"success": False, "error": "Bet not found"})

        if not _settles_by_hand(bet):
            return jsonify({"success": False, "error": "Parlays settle from the Settle Week card"})

        week = _open_result_week(bet, get_current_week())
        if not ledger.settle(bet, won, week=week):
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


def _settles_by_hand(bet):
    """A single, or a futures parlay, which may hold a champion leg decided only after the final. A weekly parlay
    settles from the preview, which judges it leg by leg, dropping pushed legs and re-pricing the rest."""
    return len(bet.legs) < 2 or all(leg.week is None for leg in bet.legs)


@admin_bp.route("/api/admin/settlement_preview", methods=["GET"])
@admin_required
def settlement_preview():
    week = request.args.get("week", get_current_week(), type=int)

    try:
        scores = settlement.team_scores(week)
        outcomes = settlement.outcomes_for_week(week, scores)
        rows = [_preview_row(result) for result in outcomes]
    except settlement.SettlementError as error:
        return jsonify({"success": False, "error": str(error)})
    except Exception as error:
        db.session.rollback()
        logging.exception(f"Could not preview the settlement of week {week}")
        return jsonify({"success": False, "error": str(error)})

    decided = sum(1 for result in outcomes if result.outcome != settlement.UNDECIDED)
    return jsonify(
        {
            "success": True,
            "week": week,
            "scores": [asdict(score) for score in scores.values()],
            "bets": rows,
            "decided": decided,
            "undecided": len(outcomes) - decided,
        }
    )


def _preview_row(result):
    bet = result.bet
    name = " ".join(part for part in (bet.user.first_name, bet.user.last_name) if part)
    row = {
        "id": bet.id,
        "user": name or f"User #{bet.user_id[:8]}",
        "description": friendly_description(bet.description),
        "amount": bet.amount,
        "odds": bet.odds,
        "potential_win": bet.potential_win,
        "market": None,
        "selection": None,
        "line": None,
        "legs": [{"market": leg.market, "selection": leg.selection, "line": leg.line} for leg in bet.legs],
        "outcome": result.outcome,
        "reason": result.reason,
    }
    # A parlay's picks are its legs; only a single bet is one market and selection.
    if len(bet.legs) == 1:
        row.update(row["legs"][0])
    return row


@admin_bp.route("/api/admin/settle_outcomes", methods=["POST"])
@admin_required
def settle_outcomes():
    data = request.get_json()
    week = data.get("week")
    if not week:
        return jsonify({"success": False, "error": "Week required"})

    settled = []
    skipped = []
    try:
        scores = settlement.team_scores(week)
        current = {result.bet.id: result for result in settlement.outcomes_for_week(week, scores)}
        for shown in data.get("bets", []):
            reason = _settle_as_shown(current.get(shown["id"]), shown, week)
            if reason is None:
                settled.append(shown["id"])
            else:
                skipped.append({"id": shown["id"], "reason": reason})
    except settlement.SettlementError as error:
        return jsonify({"success": False, "error": str(error)})
    except Exception as error:
        # Bets settled before the failure were committed one by one and stand.
        db.session.rollback()
        logging.exception(f"Settling week {week} stopped after settling bets {settled}")
        return jsonify({"success": False, "error": str(error)})

    logging.info(f"Settled week {week} bets {settled}, skipped {skipped}")
    return jsonify({"success": True, "settled": settled, "skipped": skipped})


def _settle_as_shown(result, shown, week):
    """Settle one bet in its own transaction if its outcome is still the one shown; return why not, or None."""
    if result is None:
        bet = db.session.get(Bet, shown["id"])
        return "already settled" if bet is not None and bet.status != "pending" else "not found"
    if result.outcome == settlement.UNDECIDED:
        return "undecided"
    if result.outcome != shown["outcome"]:
        return f"scores changed: now {result.outcome}"

    result_week = _open_result_week(result.bet, week)
    if result.outcome == settlement.PUSH:
        closed = ledger.push(result.bet, leg_statuses=result.leg_statuses, week=result_week)
    else:
        closed = ledger.settle(
            result.bet,
            won=result.outcome == settlement.WON,
            potential_win=result.potential_win,
            leg_statuses=result.leg_statuses,
            week=result_week,
        )
    if not closed:
        db.session.rollback()
        return "already settled"
    db.session.commit()
    return None


def _open_result_week(bet, settling_week):
    """The week a bet's result posts to: a futures bet's is the week it settles in, opened first; any other its own."""
    is_futures = bool(bet.legs) and all(leg.week is None for leg in bet.legs)
    if not is_futures:
        return bet.week
    ledger.open_week(bet.user_id, settling_week)
    return settling_week


@admin_bp.route("/api/admin/void_bet", methods=["POST"])
@admin_required
def void_bet():
    bet_id = request.get_json().get("bet_id")
    if not bet_id:
        return jsonify({"success": False, "error": "Bet ID required"})

    try:
        bet = db.session.get(Bet, bet_id)
        if bet is None:
            return jsonify({"success": False, "error": "Bet not found"})
        if not ledger.void(bet):
            db.session.rollback()
            return jsonify({"success": False, "error": "Bet already settled"})
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        logging.exception(f"Could not void bet {bet_id}")
        return jsonify({"success": False, "error": str(error)})

    logging.info(f"Voided bet {bet_id}")
    return jsonify({"success": True})


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
    if not week:
        return jsonify({"success": False, "error": "Week required"})

    lock_time = datetime.now(UTC) + timedelta(days=7)
    if data.get("lock_time") is not None:
        try:
            lock_time = datetime.strptime(data["lock_time"], "%Y-%m-%dT%H:%M").replace(tzinfo=UTC)
        except ValueError:
            return jsonify({"success": False, "error": "Invalid lock time"})

    try:
        period = db.session.query(BettingPeriod).filter_by(week=week).first()
        if period is None:
            return jsonify({"success": False, "error": "Betting period not found"})
        period.is_locked = False
        period.lock_time = lock_time
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        logging.exception(f"Could not unlock week {week}")
        return jsonify({"success": False, "error": str(error)})

    logging.info(f"{current_user.email} unlocked week {week} until {lock_time:%Y-%m-%d %H:%M} UTC")
    return jsonify({"success": True})


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
        sections={name: summary_sections(step["summary"]) for name, step in latest_steps.items()},
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
