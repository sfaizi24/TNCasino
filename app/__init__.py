import logging
import os
import sys

from dotenv import load_dotenv
from flask import Flask, session
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()
logging.basicConfig(level=logging.DEBUG)


def create_app(config=None):
    app = Flask(
        __name__,
        template_folder="../frontend/templates",
        static_folder="../frontend/static",
    )

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["WTF_CSRF_CHECK_DEFAULT"] = False
    app.config["GOOGLE_OAUTH_CLIENT_ID"] = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "")
    app.config["GOOGLE_OAUTH_CLIENT_SECRET"] = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "")

    if config:
        app.config.update(config)

    if not app.config.get("TESTING"):
        app.config.setdefault(
            "SQLALCHEMY_ENGINE_OPTIONS",
            {
                "pool_pre_ping": True,
                "pool_recycle": 300,
            },
        )

    app.secret_key = app.config["SECRET_KEY"]
    if not app.secret_key and not app.config.get("TESTING"):
        raise RuntimeError("SECRET_KEY environment variable is required")

    if os.environ.get("FLASK_ENV") == "production":
        app.config["SESSION_COOKIE_SECURE"] = True
        app.config["SESSION_COOKIE_HTTPONLY"] = True
        app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

    # Extensions
    from .extensions import csrf

    csrf.init_app(app)

    from .database import db

    db.init_app(app)

    # Database setup
    with app.app_context():
        from . import models  # noqa: F401

        db.create_all()
        logging.info("Database tables created")

        if not app.config.get("TESTING"):
            from .migrations import run_schema_migrations

            run_schema_migrations()
            logging.info("Schema migrations completed")

    # Auth
    from .auth import init_auth

    init_auth(app)

    # Route blueprints
    from .routes.account import account_bp
    from .routes.admin import admin_bp
    from .routes.analytics import analytics_bp
    from .routes.betting import betting_bp
    from .routes.odds import odds_bp
    from .routes.pages import pages_bp

    app.register_blueprint(pages_bp)
    app.register_blueprint(account_bp)
    app.register_blueprint(odds_bp)
    app.register_blueprint(analytics_bp)
    app.register_blueprint(betting_bp)
    app.register_blueprint(admin_bp)

    from .routes.helpers import friendly_description

    app.jinja_env.filters["friendly_names"] = friendly_description

    @app.before_request
    def make_session_permanent():
        session.permanent = True

    return app


if "pytest" not in sys.modules:
    app = create_app()
