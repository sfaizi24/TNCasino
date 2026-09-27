import pytest
from sqlalchemy import text

from app.routes.admin import PIPELINE_STEP_ORDER
from pipeline.steps import STEP_ORDER

FIRST_RUN = "2026w04-20260929T140000"
RERUN = "2026w04-20260929T160000"
STEP_FIELDS = {
    "run_id",
    "step",
    "started_at",
    "finished_at",
    "duration_s",
    "status",
    "summary",
    "warnings",
    "charts",
    "error",
}


def test_step_order_mirrors_the_pipeline_package():
    assert PIPELINE_STEP_ORDER == STEP_ORDER


def test_rerun_steps_come_from_the_newest_run(admin_client, pipeline_tables):
    steps = admin_client.get("/api/admin/pipeline?week=4").get_json()["steps"]

    run_by_step = {step["step"]: step["run_id"] for step in steps}
    assert run_by_step == {
        "league": FIRST_RUN,
        "scrape": FIRST_RUN,
        "calibrate": FIRST_RUN,
        "simulate": RERUN,
        "odds": RERUN,
        "playoffs": FIRST_RUN,
    }


def test_api_lists_steps_in_pipeline_order(admin_client, pipeline_tables, db_session):
    # A step that first ran after everything else still belongs between scrape and calibrate.
    db_session.session.execute(
        text("""
        INSERT INTO pipeline_runs (run_id, season, week, started_at, status, steps)
        VALUES ('2026w04-20260929T170000', 2026, 4, '2026-09-29T17:00:00+00:00', 'ok', '["match"]')
    """)
    )
    db_session.session.execute(
        text("""
        INSERT INTO pipeline_steps (run_id, step, started_at, status)
        VALUES ('2026w04-20260929T170000', 'match', '2026-09-29T17:00:00+00:00', 'ok')
    """)
    )
    db_session.session.commit()

    steps = admin_client.get("/api/admin/pipeline?week=4").get_json()["steps"]

    expected = ["league", "scrape", "match", "calibrate", "simulate", "odds", "playoffs"]
    assert [step["step"] for step in steps] == expected


def test_page_lists_every_step_and_marks_the_ones_not_run(admin_client, pipeline_tables, captured_templates):
    html = admin_client.get("/admin/pipeline?week=4").get_data(as_text=True)

    steps = captured_templates[0][1]["steps"]
    assert [name for name, _ in steps] == PIPELINE_STEP_ORDER
    not_run = [name for name, step in steps if step is None]
    assert not_run == ["clean", "match", "stats", "accuracy", "lineups", "validate", "publish"]
    assert "not run" in html


def test_api_returns_parsed_step_records(admin_client, pipeline_tables):
    payload = admin_client.get("/api/admin/pipeline?week=4").get_json()

    assert set(payload) == {"week", "steps", "sources", "runs"}
    assert payload["week"] == 4

    steps = {step["step"]: step for step in payload["steps"]}
    assert all(set(step) == STEP_FIELDS for step in steps.values())
    assert steps["scrape"]["status"] == "warn"
    assert steps["scrape"]["warnings"] == ["espn.com failed verification and was dropped"]
    assert steps["scrape"]["summary"]["dropped"] == ["espn.com"]
    assert steps["calibrate"]["charts"] == ["calibration_week_4.png"]
    assert steps["simulate"]["summary"] == {"n_sims": 50000, "seed": 1739}
    assert steps["simulate"]["duration_s"] == 30.0

    playoffs = steps["playoffs"]
    assert playoffs["status"] == "failed"
    assert playoffs["summary"] == {}
    assert playoffs["warnings"] == []
    assert playoffs["charts"] == []
    assert playoffs["error"].endswith("KeyError: 'playoff_week_start'")


def test_sources_carry_checks_and_agent_verdicts(admin_client, pipeline_tables):
    sources = admin_client.get("/api/admin/pipeline?week=4").get_json()["sources"]

    assert [source["source"] for source in sources] == ["sleeper.com", "espn.com"]
    sleeper, espn = sources
    assert sleeper["verdict"] == "ok"
    assert sleeper["note"] == "top 15 look right"
    assert espn["status"] == "fail"
    assert espn["verdict"] is None
    assert espn["checks"][0]["name"] == "position_agreement"


def test_runs_are_listed_newest_first(admin_client, pipeline_tables):
    runs = admin_client.get("/api/admin/pipeline?week=4").get_json()["runs"]

    assert [run["run_id"] for run in runs] == [RERUN, FIRST_RUN]
    assert runs[0]["steps"] == ["simulate", "odds"]
    assert runs[1]["status"] == "failed"


def test_page_shows_warnings_errors_charts_and_verdicts(admin_client, pipeline_tables):
    html = admin_client.get("/admin/pipeline?week=4").get_data(as_text=True)

    assert "espn.com failed verification and was dropped" in html
    assert "playoff_week_start" in html
    assert 'href="/analytics-images/calibration_week_4.png"' in html
    assert "top 15 look right" in html


def test_defaults_to_latest_week_with_recorded_steps(admin_client, pipeline_tables, db_session, captured_templates):
    # Week 5 has a run that never recorded a step, so week 4 stays the default.
    db_session.session.execute(
        text("""
        INSERT INTO pipeline_runs (run_id, season, week, started_at, status, steps)
        VALUES ('2026w03-20260922T140000', 2026, 3, '2026-09-22T14:00:00+00:00', 'ok', '["league"]'),
               ('2026w05-20261006T140000', 2026, 5, '2026-10-06T14:00:00+00:00', 'running', '["league"]')
    """)
    )
    db_session.session.execute(
        text("""
        INSERT INTO pipeline_steps (run_id, step, started_at, status)
        VALUES ('2026w03-20260922T140000', 'league', '2026-09-22T14:00:00+00:00', 'ok')
    """)
    )
    db_session.session.commit()

    assert admin_client.get("/api/admin/pipeline").get_json()["week"] == 4

    admin_client.get("/admin/pipeline")
    context = captured_templates[0][1]
    assert context["week"] == 4
    assert context["weeks"] == [4, 3]


def test_week_without_runs_is_empty(admin_client, pipeline_tables, captured_templates):
    payload = admin_client.get("/api/admin/pipeline?week=9").get_json()
    assert payload == {"week": 9, "steps": [], "sources": [], "runs": []}

    page = admin_client.get("/admin/pipeline?week=9")
    assert page.status_code == 200
    context = captured_templates[0][1]
    assert all(step is None for _, step in context["steps"])
    assert context["sources"] == []
    assert context["runs"] == []


def test_missing_tables_show_the_empty_state(admin_client):
    page = admin_client.get("/admin/pipeline")
    assert page.status_code == 200
    assert "No pipeline runs published yet" in page.get_data(as_text=True)

    payload = admin_client.get("/api/admin/pipeline").get_json()
    assert payload == {"week": None, "steps": [], "sources": [], "runs": []}


@pytest.mark.parametrize("url", ["/admin/pipeline", "/api/admin/pipeline"])
def test_non_admins_are_sent_back_to_betting(logged_in_client, url):
    resp = logged_in_client.get(url)

    assert resp.status_code == 302
    assert resp.location.endswith("/betting")


@pytest.mark.parametrize(("url", "status_code"), [("/admin/pipeline", 302), ("/api/admin/pipeline", 401)])
def test_anonymous_visitors_are_rejected(client, url, status_code):
    assert client.get(url).status_code == status_code
