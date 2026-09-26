"""SQLite paths and connections for the pipeline databases, and the run-record tables in pipeline.db."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pipeline.settings import Settings

DB_NAMES = ["league", "projections", "odds", "pipeline"]

RUN_TABLES = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
  run_id TEXT PRIMARY KEY,          -- "{season}w{week:02d}-{YYYYmmddTHHMMSS}"
  season INTEGER NOT NULL, week INTEGER NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,   -- running|ok|warn|failed
  steps TEXT NOT NULL,              -- JSON list of requested step names
  git_sha TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS pipeline_steps (
  run_id TEXT NOT NULL, step TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, duration_s REAL,
  status TEXT NOT NULL,             -- running|ok|warn|failed
  summary TEXT, warnings TEXT, charts TEXT, error TEXT,   -- JSON
  PRIMARY KEY (run_id, step)
);
CREATE TABLE IF NOT EXISTS source_reviews (
  season INTEGER NOT NULL, week INTEGER NOT NULL, source TEXT NOT NULL,
  verdict TEXT NOT NULL,            -- ok|reject
  note TEXT, reviewed_at TEXT NOT NULL,
  PRIMARY KEY (season, week, source)
);
"""


def db_path(settings: Settings, name: str) -> Path:
    return settings.db_paths[name]


def connect(settings: Settings, name: str) -> sqlite3.Connection:
    path = db_path(settings, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_run_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(RUN_TABLES)
