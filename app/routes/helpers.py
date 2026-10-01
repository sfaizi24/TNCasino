from datetime import UTC, datetime
from functools import wraps

from flask import flash, redirect, url_for
from flask_login import current_user, login_required
from sqlalchemy import text

from ..database import db

# Maps a Sleeper username (or display_name as it appears in the DB) to the
# friendly first name we want shown across the site. Lookups elsewhere should
# go through display_name_for / friendly_description so this stays the only
# place the mapping is defined.
OWNER_DISPLAY_NAMES = {
    "xavierking4": "Dany",
    "umarrahman30": "Umar",
    "sfaizi24": "Samer",
    "sahirsyed30": "Sahir",
    "monkeyman966699696": "Hasan",
    "mehdidrissi": "Mehdi",
    "asadrafique": "Asad",
    "amir812": "Arman",
    "TBK41": "Tabarak",
    "Jibraan": "Jibraan",
    "Bilal879": "Bilal",
    "Ammady": "Ammad",
    "fajandfoujee": "Faraj",
}

_REVERSE_OWNER_LOOKUP = {friendly: raw for raw, friendly in OWNER_DISPLAY_NAMES.items()}


def display_name_for(owner):
    if not owner:
        return owner
    return OWNER_DISPLAY_NAMES.get(owner, owner)


def resolve_owner(name):
    """Translate a friendly display name back to its raw Sleeper handle."""
    if not name:
        return name
    return _REVERSE_OWNER_LOOKUP.get(name, name)


def friendly_description(description):
    """Replace any raw Sleeper handles inside a stored bet description.

    Replaces longest keys first so that 'Bilal' never shadows 'Bilal879'.
    """
    if not description:
        return description
    for raw in sorted(OWNER_DISPLAY_NAMES, key=len, reverse=True):
        description = description.replace(raw, OWNER_DISPLAY_NAMES[raw])
    return description


def get_current_week():
    from ..models import BettingPeriod

    period = db.session.query(BettingPeriod).filter_by(is_settled=False).order_by(BettingPeriod.week.desc()).first()

    if period:
        return period.week

    print(
        "[WARNING] No active (unsettled) betting period found in database. Defaulting to week 10. Please create a new betting period via /admin."
    )
    return 10


def check_betting_period_lock(period):
    """The period's lock time once the admin's lock has closed it, locking it for good when the time has passed."""
    lock_time = period.lock_time
    if lock_time.tzinfo is None:
        lock_time = lock_time.replace(tzinfo=UTC)

    if not period.is_locked and datetime.now(UTC) >= lock_time:
        period.is_locked = True
        db.session.commit()
    return period.lock_time if period.is_locked else None


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("pages.index"))
        if not getattr(current_user, "is_admin", False):
            flash("You do not have permission to access this page.", "error")
            return redirect(url_for("betting.betting"))
        return f(*args, **kwargs)

    return decorated_function


def query_analytics(sql, params=None):
    """Execute a read-only SQL query and return results as a list of dicts."""
    result = db.session.execute(text(sql), params or {})
    return [dict(row._mapping) for row in result]


def get_league_id_for_week(week):
    league_rows = query_analytics(
        "SELECT DISTINCT league_id FROM sleeper_matchups WHERE week = :week",
        {"week": week},
    )
    return league_rows[0]["league_id"] if league_rows else None


def get_team_mapping(week):
    """Get roster_id to owner name mapping from league database for the given week."""
    current_league_id = get_league_id_for_week(week)

    roster_rows = query_analytics(
        """
        SELECT r.roster_id, u.display_name, u.username
        FROM sleeper_rosters r
        LEFT JOIN sleeper_users u ON r.owner_id = u.user_id
        WHERE r.league_id = :league_id
        """,
        {"league_id": current_league_id},
    )

    team_mapping = {}
    for row in roster_rows:
        owner_name = row["display_name"] or row["username"] or f"Team {row['roster_id']}"
        team_mapping[row["roster_id"]] = display_name_for(owner_name)

    return team_mapping
