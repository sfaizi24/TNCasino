import logging

from .database import db

# The bet types that took their market's name; rows stored under the old names are renamed in place.
RENAMED_BET_TYPES = {"team_ou": "team_total", "first_seed": "first_place", "ammad_playoff": "make_playoffs"}


def run_schema_migrations():
    try:
        from sqlalchemy import inspect, text

        def column_exists(inspector, table_name, column_name):
            if table_name not in inspector.get_table_names():
                return False
            columns = [col["name"] for col in inspector.get_columns(table_name)]
            return column_name in columns

        dialect = db.engine.dialect.name
        logging.info(f"Running schema migrations for dialect: {dialect}")

        with db.engine.begin() as conn:
            inspector = inspect(db.engine)

            if dialect == "postgresql":
                logging.info("PostgreSQL detected: migrating datetime columns to timezone-aware")
                datetime_migrations = [
                    ("users", "created_at"),
                    ("users", "updated_at"),
                    ("bets", "created_at"),
                    ("bets", "settled_at"),
                    ("weekly_stats", "created_at"),
                    ("betting_periods", "lock_time"),
                    ("betting_periods", "created_at"),
                    ("betting_periods", "updated_at"),
                ]

                for table, column in datetime_migrations:
                    if column_exists(inspector, table, column):
                        try:
                            result = conn.execute(
                                text(f"""
                                SELECT data_type
                                FROM information_schema.columns
                                WHERE table_name = '{table}'
                                AND column_name = '{column}'
                            """)
                            ).first()

                            if result and result[0] == "timestamp without time zone":
                                conn.execute(
                                    text(f"""
                                    ALTER TABLE {table}
                                    ALTER COLUMN {column} TYPE TIMESTAMP WITH TIME ZONE
                                    USING {column} AT TIME ZONE 'UTC'
                                """)
                                )
                                logging.info(f"Converted {table}.{column} to TIMESTAMPTZ")
                        except Exception as col_error:
                            logging.error(f"Error converting {table}.{column}: {col_error}")
            else:
                logging.info(f"{dialect} detected: skipping timezone migration (not needed)")

            inspector = inspect(db.engine)

            if not column_exists(inspector, "users", "is_admin"):
                logging.info("Adding is_admin column to users")
                if dialect == "postgresql":
                    conn.execute(text("ALTER TABLE users ADD COLUMN is_admin BOOLEAN DEFAULT FALSE"))
                else:
                    conn.execute(text("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0"))
                logging.info("is_admin column added")

            if not column_exists(inspector, "weekly_stats", "active_bets_amount"):
                logging.info("Adding active_bets_amount column to weekly_stats")
                if dialect == "postgresql":
                    conn.execute(
                        text("ALTER TABLE weekly_stats ADD COLUMN active_bets_amount DOUBLE PRECISION DEFAULT 0.0")
                    )
                else:
                    conn.execute(text("ALTER TABLE weekly_stats ADD COLUMN active_bets_amount REAL DEFAULT 0.0"))

                conn.execute(
                    text("""
                    UPDATE weekly_stats
                    SET active_bets_amount = COALESCE((
                        SELECT SUM(b.amount)
                        FROM bets b
                        WHERE b.user_id = weekly_stats.user_id
                          AND b.week = weekly_stats.week
                          AND b.status = 'pending'
                    ), 0.0)
                """)
                )
                logging.info("active_bets_amount column added and backfilled")

            if not column_exists(inspector, "weekly_stats", "settled_pnl"):
                logging.info("Adding settled_pnl column to weekly_stats")
                if dialect == "postgresql":
                    conn.execute(text("ALTER TABLE weekly_stats ADD COLUMN settled_pnl DOUBLE PRECISION DEFAULT 0.0"))
                else:
                    conn.execute(text("ALTER TABLE weekly_stats ADD COLUMN settled_pnl REAL DEFAULT 0.0"))

                conn.execute(
                    text("""
                    UPDATE weekly_stats
                    SET settled_pnl = COALESCE((
                        SELECT SUM(b.result)
                        FROM bets b
                        WHERE b.user_id = weekly_stats.user_id
                          AND b.week = weekly_stats.week
                          AND b.status IN ('won', 'lost')
                    ), 0.0)
                """)
                )
                logging.info("settled_pnl column added and backfilled")

            float_type = "DOUBLE PRECISION" if dialect == "postgresql" else "REAL"
            timestamp_type = "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "TIMESTAMP"
            bet_columns = (
                ("run_id", "VARCHAR"),
                ("price", "INTEGER"),
                ("probability", float_type),
                ("cash_out_amount", float_type),
                ("cash_out_run_id", "VARCHAR"),
                ("cashed_out_at", timestamp_type),
            )
            for column, column_type in bet_columns:
                if not column_exists(inspector, "bets", column):
                    conn.execute(text(f"ALTER TABLE bets ADD COLUMN {column} {column_type}"))
                    logging.info(f"Added bets.{column}")

            for old_type, new_type in RENAMED_BET_TYPES.items():
                conn.execute(
                    text("UPDATE bets SET bet_type = :new_type WHERE bet_type = :old_type"),
                    {"new_type": new_type, "old_type": old_type},
                )

            if "flask_dance_oauth" in inspector.get_table_names():
                conn.execute(text("DROP TABLE flask_dance_oauth"))
                logging.info("Dropped legacy flask_dance_oauth table")

            logging.info("Schema migrations completed successfully")

    except Exception as e:
        logging.error(f"Migration error: {e}")
        import traceback

        traceback.print_exc()
