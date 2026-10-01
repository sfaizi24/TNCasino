import os

from flask import Blueprint, jsonify, redirect, request, session, url_for
from flask_dance.consumer import oauth_authorized
from flask_dance.contrib.google import make_google_blueprint
from flask_login import LoginManager, login_user, logout_user

from .database import db
from .models import User

login_manager = LoginManager()


@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, user_id)


@login_manager.unauthorized_handler
def unauthorized():
    if request.path.startswith("/api/"):
        return jsonify({"error": "Login required"}), 401
    session["next_url"] = _get_safe_next_url()
    return redirect(url_for("google.login"))


def _get_safe_next_url():
    """Only save the URL if it's a real browser navigation (GET for a document).
    Avoids redirecting back to POST-only endpoints like /account/update-profile
    when a session expires mid-form-submit."""
    is_navigation = (
        request.headers.get("Sec-Fetch-Mode") == "navigate" and request.headers.get("Sec-Fetch-Dest") == "document"
    )
    if is_navigation:
        return request.url
    return request.referrer or url_for("betting.betting")


def _safe_email_for_new_user(email):
    """Return email if unclaimed, else None. Avoids unique constraint
    collision with ghost Replit-era users who still hold the email."""
    existing = db.session.query(User).filter_by(email=email).first()
    return None if existing else email


def init_auth(app):
    login_manager.init_app(app)

    google_bp = make_google_blueprint(
        client_id=app.config.get("GOOGLE_OAUTH_CLIENT_ID", ""),
        client_secret=app.config.get("GOOGLE_OAUTH_CLIENT_SECRET", ""),
        scope=["openid", "email", "profile"],
    )

    auth_bp = Blueprint("auth", __name__, url_prefix="/auth")

    @oauth_authorized.connect_via(google_bp)
    def google_logged_in(blueprint, token):
        resp = blueprint.session.get("/oauth2/v2/userinfo")
        if not resp.ok:
            return False

        info = resp.json()
        google_id = str(info["id"])
        google_email = info.get("email", "")

        admin_emails = [e.strip() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]

        user = db.session.get(User, google_id)
        if user is None:
            user = User(
                id=google_id,
                username=info.get("name"),
                email=_safe_email_for_new_user(google_email),
                first_name=info.get("given_name"),
                last_name=info.get("family_name"),
                profile_image_url=info.get("picture"),
                is_admin=google_email in admin_emails,
                account_balance=5000.0,
            )
            db.session.add(user)
        else:
            user.first_name = info.get("given_name")
            user.last_name = info.get("family_name")
            user.profile_image_url = info.get("picture")
            user.is_admin = google_email in admin_emails

        db.session.commit()
        login_user(user)
        next_url = session.pop("next_url", url_for("betting.betting"))
        return redirect(next_url)

    @auth_bp.route("/logout")
    def logout():
        logout_user()
        if google_bp.token is not None:
            del google_bp.token
        return redirect(url_for("betting.betting"))

    app.register_blueprint(google_bp, url_prefix="/auth")
    app.register_blueprint(auth_bp)
