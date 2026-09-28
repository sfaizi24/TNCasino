from datetime import UTC, datetime

from flask_login import UserMixin
from sqlalchemy import UniqueConstraint

from .database import db


def utc_now():
    return datetime.now(UTC)


# User Model - ID is String (Google user ID or legacy Replit ID)
class User(UserMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.String, primary_key=True)
    username = db.Column(db.String, unique=True, nullable=True)
    email = db.Column(db.String, unique=True, nullable=True)
    first_name = db.Column(db.String, nullable=True)
    last_name = db.Column(db.String, nullable=True)
    profile_image_url = db.Column(db.String, nullable=True)

    account_balance = db.Column(db.Float, default=1000.0)
    total_pnl = db.Column(db.Float, default=0.0)
    is_admin = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime(timezone=True), default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now)


# Betting tables
class Bet(db.Model):
    __tablename__ = "bets"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String, db.ForeignKey("users.id"), nullable=False)
    bet_type = db.Column(db.String, nullable=False)
    description = db.Column(db.Text, nullable=False)
    amount = db.Column(db.Float, nullable=False)
    odds = db.Column(db.String, nullable=False)
    # The published quote the bet was priced at; null on bets placed before markets existed.
    price = db.Column(db.Integer, nullable=True)
    probability = db.Column(db.Float, nullable=True)
    run_id = db.Column(db.String, nullable=True)
    potential_win = db.Column(db.Float, nullable=False)
    status = db.Column(db.String, default="pending")
    result = db.Column(db.Float, default=0.0)
    week = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now)
    settled_at = db.Column(db.DateTime(timezone=True), nullable=True)

    user = db.relationship(User, backref="bets")


class BetLeg(db.Model):
    """One pick inside a bet: a market key, a selection and the quote it was priced at."""

    __tablename__ = "bet_legs"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    bet_id = db.Column(db.Integer, db.ForeignKey("bets.id"), nullable=False, index=True)
    season = db.Column(db.Integer, nullable=False)
    week = db.Column(db.Integer, nullable=True)
    market = db.Column(db.String, nullable=False)
    selection = db.Column(db.String, nullable=False)
    line = db.Column(db.Numeric(7, 2, asdecimal=False), nullable=True)
    price = db.Column(db.Integer, nullable=False)
    probability = db.Column(db.Float, nullable=False)
    # pending until the bet settles, then won or lost; void when the bet is removed.
    status = db.Column(db.String, default="pending", nullable=False)
    settled_at = db.Column(db.DateTime(timezone=True), nullable=True)

    bet = db.relationship(Bet, backref="legs")


class WeeklyStats(db.Model):
    __tablename__ = "weekly_stats"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String, db.ForeignKey("users.id"), nullable=False)
    week = db.Column(db.Integer, nullable=False)
    starting_balance = db.Column(db.Float, nullable=False)
    ending_balance = db.Column(db.Float, nullable=False)
    pnl = db.Column(db.Float, nullable=False)
    active_bets_amount = db.Column(db.Float, default=0.0)
    settled_pnl = db.Column(db.Float, default=0.0)
    bets_placed = db.Column(db.Integer, default=0)
    bets_won = db.Column(db.Integer, default=0)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now)

    user = db.relationship(User, backref="weekly_stats")

    __table_args__ = (UniqueConstraint("user_id", "week", name="uq_user_week"),)


class BettingPeriod(db.Model):
    __tablename__ = "betting_periods"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    week = db.Column(db.Integer, unique=True, nullable=False)
    lock_time = db.Column(db.DateTime(timezone=True), nullable=False)
    is_locked = db.Column(db.Boolean, default=False)
    is_settled = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime(timezone=True), default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), default=utc_now, onupdate=utc_now)
