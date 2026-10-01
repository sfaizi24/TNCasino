import logging
from collections import defaultdict
from typing import NamedTuple

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from .. import markets
from ..database import db
from ..extensions import csrf

account_bp = Blueprint("account", __name__)


class BetGroup(NamedTuple):
    key: str
    label: str
    bets: list
    pnl: float


def _is_future(bet):
    is_weekly, _ = markets.SHAPES.get(bet.bet_type, (True, ()))
    return not is_weekly


def _group(key, label, bets):
    return BetGroup(key, label, bets, sum(bet.result for bet in bets))


def bet_history(bets):
    """A user's bets by week, newest first, then the season's futures. Removed bets were refunds and are left out."""
    by_week = defaultdict(list)
    futures = []
    for bet in bets:
        if bet.status == "removed":
            continue
        if _is_future(bet):
            futures.append(bet)
        else:
            by_week[bet.week].append(bet)

    weeks = sorted(by_week, key=lambda week: week or 0, reverse=True)
    groups = [_group(f"week-{week}", f"Week {week or '—'}", by_week[week]) for week in weeks]
    if futures:
        groups.append(_group("futures", "Futures", futures))
    return groups


@account_bp.route("/account")
@login_required
def account():
    from ..models import Bet

    bets = (
        db.session.query(Bet)
        .filter(Bet.user_id == current_user.id)
        .order_by(Bet.week.desc(), Bet.created_at.desc())
        .all()
    )

    return render_template(
        "account.html",
        user=current_user,
        history=bet_history(bets),
        in_play=sum(bet.amount for bet in bets if bet.status == "pending"),
        wins=sum(bet.status == "won" for bet in bets),
        losses=sum(bet.status == "lost" for bet in bets),
    )


@account_bp.route("/account/update-profile", methods=["POST"])
@login_required
def update_profile():
    csrf.protect()
    try:
        first_name = request.form.get("first_name", "").strip()
        last_name = request.form.get("last_name", "").strip()

        if len(first_name) > 100:
            flash("First name must be 100 characters or less.", "error")
            return redirect(url_for("account.account"))

        if len(last_name) > 100:
            flash("Last name must be 100 characters or less.", "error")
            return redirect(url_for("account.account"))

        current_user.first_name = first_name if first_name else None
        current_user.last_name = last_name if last_name else None

        db.session.commit()

        flash("Saved.", "success")

    except Exception as e:
        db.session.rollback()
        logging.error(f"Error updating profile: {e}")
        flash("An error occurred while updating your profile. Please try again.", "error")

    return redirect(url_for("account.account"))
