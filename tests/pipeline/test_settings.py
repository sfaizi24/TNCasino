import pytest

from pipeline import settings as settings_module
from pipeline.settings import Settings, SettingsError, load_local_settings, load_settings

REAL_SLEEPER_GET = settings_module.sleeper_get

LEAGUES = {
    "L2024": {"league_id": "L2024", "season": "2024", "name": "TN League 2024", "previous_league_id": None},
    "L2025": {"league_id": "L2025", "season": "2025", "name": "TN League 2025", "previous_league_id": "L2024"},
    "L2026": {"league_id": "L2026", "season": "2026", "name": "TN League 2026", "previous_league_id": "L2025"},
    "L2027": {"league_id": "L2027", "season": "2027", "name": "TN League 2027", "previous_league_id": "L2026"},
    "D2026": {"league_id": "D2026", "season": "2026", "name": "TN League rerun", "previous_league_id": "L2025"},
    "X2025": {"league_id": "X2025", "season": "2025", "name": "Work league 2025", "previous_league_id": None},
    "X2026": {"league_id": "X2026", "season": "2026", "name": "Work league 2026", "previous_league_id": "X2025"},
    "X2027": {"league_id": "X2027", "season": "2027", "name": "Work league 2027", "previous_league_id": "X2026"},
    "N2026": {"league_id": "N2026", "season": "2026", "name": "Brand new league", "previous_league_id": None},
}


def unexpected_request(path):
    raise AssertionError(f"unexpected Sleeper request {path}")


@pytest.fixture(autouse=True)
def offline_environment(monkeypatch):
    for name in [
        "PIPELINE_SEASON",
        "PIPELINE_LEAGUE_ID",
        "PIPELINE_MODEL_VERSION",
        "PIPELINE_DATA_DIR",
        "LEAGUE_ID",
        "SLEEPER_USERNAME",
    ]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(settings_module, "load_dotenv", lambda path: None)
    monkeypatch.setattr(settings_module, "sleeper_get", unexpected_request)


@pytest.fixture
def league_env(monkeypatch):
    monkeypatch.setenv("LEAGUE_ID", "L2025")
    monkeypatch.setenv("SLEEPER_USERNAME", "tnfan")


def fake_sleeper(monkeypatch, user_leagues: dict[int, list[str]]) -> dict:
    """Answer Sleeper paths from fixed data; any other path raises KeyError and fails the test."""
    responses = {
        "/state/nfl": {"season": "2026", "week": 3},
        "/user/tnfan": {"user_id": "U1"},
    }
    for league_id, league in LEAGUES.items():
        responses[f"/league/{league_id}"] = league
    for season, league_ids in user_leagues.items():
        responses[f"/user/U1/leagues/nfl/{season}"] = [LEAGUES[league_id] for league_id in league_ids]
    monkeypatch.setattr(settings_module, "sleeper_get", responses.__getitem__)
    return responses


def test_env_overrides_skip_discovery(monkeypatch):
    monkeypatch.setenv("PIPELINE_SEASON", "2026")
    monkeypatch.setenv("PIPELINE_LEAGUE_ID", "L2026")
    monkeypatch.setenv("PIPELINE_MODEL_VERSION", "v2")

    settings = load_settings(week=4)

    assert settings.season == 2026
    assert settings.week == 4
    assert settings.league_id == "L2026"
    assert settings.model_version == "v2"


def test_data_dir_env_moves_every_path(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_LEAGUE_ID", "L2026")
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))

    settings = load_settings(week=4, season=2026)

    assert settings.data_dir == tmp_path
    assert settings.db_paths["odds"] == tmp_path / "databases" / "odds.db"


def test_season_flag_takes_precedence_over_env(monkeypatch):
    monkeypatch.setenv("PIPELINE_SEASON", "2026")
    monkeypatch.setenv("PIPELINE_LEAGUE_ID", "L2025")

    settings = load_settings(week=12, season=2025)

    assert (settings.season, settings.week) == (2025, 12)


def test_season_and_week_default_to_the_nfl_state(monkeypatch):
    monkeypatch.setenv("PIPELINE_LEAGUE_ID", "L2026")
    fake_sleeper(monkeypatch, {})

    settings = load_settings()

    assert (settings.season, settings.week) == (2026, 3)


def test_defaults_match_the_current_model(monkeypatch):
    monkeypatch.setenv("PIPELINE_LEAGUE_ID", "L2026")

    settings = load_settings(week=4, season=2026)

    assert settings.model_version == "v2.3"
    assert settings.seed == 1738
    assert settings.n_sims == 50_000


def test_local_settings_take_the_environment_and_ask_sleeper_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("PIPELINE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("PIPELINE_MODEL_VERSION", "v2")

    settings = load_local_settings()

    assert (settings.season, settings.week, settings.league_id) == (0, 0, "")
    assert settings.data_dir == tmp_path
    assert settings.model_version == "v2"


def test_discovery_picks_the_successor_of_league_id(monkeypatch, league_env):
    fake_sleeper(monkeypatch, {2026: ["X2026", "L2026", "N2026"]})

    assert load_settings(week=4, season=2026).league_id == "L2026"


def test_discovery_follows_the_chain_across_several_seasons(monkeypatch, league_env):
    fake_sleeper(monkeypatch, {2027: ["X2027", "L2027"]})

    assert load_settings(week=1, season=2027).league_id == "L2027"


def test_discovery_accepts_league_id_itself_in_its_own_season(monkeypatch, league_env):
    fake_sleeper(monkeypatch, {2025: ["X2025", "L2025"]})

    assert load_settings(week=16, season=2025).league_id == "L2025"


def test_discovery_raises_when_several_leagues_match(monkeypatch, league_env):
    fake_sleeper(monkeypatch, {2026: ["L2026", "D2026"]})

    with pytest.raises(SettingsError, match="found 2") as raised:
        load_settings(week=4, season=2026)

    assert "L2026 (TN League 2026)" in str(raised.value)
    assert "D2026 (TN League rerun)" in str(raised.value)


def test_discovery_raises_with_the_candidates_when_none_match(monkeypatch, league_env):
    fake_sleeper(monkeypatch, {2026: ["X2026", "N2026"]})

    with pytest.raises(SettingsError, match="found 0") as raised:
        load_settings(week=4, season=2026)

    assert "X2026 (Work league 2026), N2026 (Brand new league)" in str(raised.value)
    assert "PIPELINE_LEAGUE_ID" in str(raised.value)


def test_discovery_raises_for_an_unknown_user(monkeypatch, league_env):
    responses = fake_sleeper(monkeypatch, {})
    responses["/user/tnfan"] = None

    with pytest.raises(SettingsError, match="'tnfan' not found"):
        load_settings(week=4, season=2026)


def test_discovery_needs_league_id(monkeypatch):
    monkeypatch.setenv("SLEEPER_USERNAME", "tnfan")

    with pytest.raises(SettingsError, match="LEAGUE_ID is not set"):
        load_settings(week=4, season=2026)


def test_paths_live_under_the_data_dir(tmp_path):
    settings = Settings(season=2026, week=4, league_id="L2026", data_dir=tmp_path)

    assert settings.db_paths == {
        "league": tmp_path / "databases" / "league.db",
        "projections": tmp_path / "databases" / "projections.db",
        "odds": tmp_path / "databases" / "odds.db",
        "pipeline": tmp_path / "databases" / "pipeline.db",
    }
    assert settings.sims_dir == tmp_path / "sims"
    assert settings.images_dir == tmp_path / "images"


class FakeResponse:
    def raise_for_status(self):
        pass

    def json(self):
        return {"season": "2026", "week": 3}


def test_sleeper_get_requests_each_path_once_per_process(monkeypatch):
    requested = []

    def fake_get(url, timeout):
        requested.append(url)
        return FakeResponse()

    monkeypatch.setattr(settings_module.requests, "get", fake_get)
    REAL_SLEEPER_GET.cache_clear()
    try:
        assert REAL_SLEEPER_GET("/state/nfl") == {"season": "2026", "week": 3}
        REAL_SLEEPER_GET("/state/nfl")
    finally:
        REAL_SLEEPER_GET.cache_clear()

    assert requested == ["https://api.sleeper.app/v1/state/nfl"]
