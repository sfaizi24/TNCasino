"""Pipeline settings: season, week and league discovered from Sleeper, overridable through the environment."""

import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import requests
from dotenv import load_dotenv

from pipeline.db import DB_NAMES

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "backend" / "data"
SLEEPER_API = "https://api.sleeper.app/v1"
DEFAULT_MODEL_VERSION = "v1"


class SettingsError(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    season: int
    week: int
    league_id: str
    seed: int = 1738
    n_sims: int = 50_000
    model_version: str = DEFAULT_MODEL_VERSION
    data_dir: Path = DATA_DIR
    sleeper_username: str = ""

    @property
    def db_paths(self) -> dict[str, Path]:
        return {name: self.data_dir / "databases" / f"{name}.db" for name in DB_NAMES}

    @property
    def sims_dir(self) -> Path:
        return self.data_dir / "sims"

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"


def load_settings(week: int | None = None, season: int | None = None) -> Settings:
    load_dotenv(PROJECT_ROOT / ".env")
    if season is None:
        season = int(os.environ.get("PIPELINE_SEASON") or fetch_nfl_state()["season"])
    if week is None:
        week = int(fetch_nfl_state()["week"])
    return Settings(
        season=season,
        week=week,
        league_id=os.environ.get("PIPELINE_LEAGUE_ID") or discover_league_id(season),
        model_version=os.environ.get("PIPELINE_MODEL_VERSION") or DEFAULT_MODEL_VERSION,
        sleeper_username=os.environ.get("SLEEPER_USERNAME", ""),
    )


def discover_league_id(season: int) -> str:
    """Find the user's league for `season` whose previous_league_id chain leads back to LEAGUE_ID."""
    ancestor = fetch_league(required_env("LEAGUE_ID"))
    leagues = fetch_leagues(fetch_user_id(required_env("SLEEPER_USERNAME")), season)
    matches = [league for league in leagues if descends_from(league, ancestor)]
    if len(matches) != 1:
        candidates = ", ".join(f"{league['league_id']} ({league['name']})" for league in leagues) or "none"
        raise SettingsError(
            f"expected one {season} league descending from LEAGUE_ID {ancestor['league_id']}, "
            f"found {len(matches)}. Candidates: {candidates}. Set PIPELINE_LEAGUE_ID to choose one."
        )
    return matches[0]["league_id"]


def descends_from(league: dict, ancestor: dict) -> bool:
    """Walk back one season at a time until reaching the ancestor's season, then compare ids."""
    while int(league["season"]) > int(ancestor["season"]):
        previous_id = league.get("previous_league_id")
        if not previous_id:
            return False
        league = fetch_league(previous_id)
    return league["league_id"] == ancestor["league_id"]


def required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise SettingsError(f"{name} is not set; add it to .env or set PIPELINE_LEAGUE_ID")
    return value


def fetch_nfl_state() -> dict:
    return sleeper_get("/state/nfl")


def fetch_user_id(username: str) -> str:
    user = sleeper_get(f"/user/{username}")
    if user is None:
        raise SettingsError(f"Sleeper user {username!r} not found")
    return user["user_id"]


def fetch_leagues(user_id: str, season: int) -> list[dict]:
    return sleeper_get(f"/user/{user_id}/leagues/nfl/{season}")


def fetch_league(league_id: str) -> dict:
    return sleeper_get(f"/league/{league_id}")


@cache
def sleeper_get(path: str):
    response = requests.get(SLEEPER_API + path, timeout=30)
    response.raise_for_status()
    return response.json()
