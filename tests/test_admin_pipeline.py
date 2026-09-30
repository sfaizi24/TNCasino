import json

import pytest
from sqlalchemy import text

from app.routes.admin import PIPELINE_STEP_ORDER
from app.routes.pipeline_summary import summary_sections
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


LATE_RUN = "2026w04-20260929T180000"

STEP_SHAPES = {
    "league": {
        "n_users": 12,
        "byes_this_week": ["DET", "LV"],
        "owner_changes": [{"roster_id": 3, "previous_owner": "amir812", "owner": "sfaizi24"}],
    },
    "scrape": {
        "sources": [
            {
                "source": "espn.com",
                "status": "warn",
                "n_rows": 417,
                "elapsed_s": 0.73,
                "checks": [{"name": "value_agreement", "status": "warn", "detail": "K r=0.81 MAD=0.71 n=32 (warn)"}],
            },
            {"source": "sleeper.com", "status": "kept", "n_rows": 457, "elapsed_s": 0, "checks": []},
        ],
        "n_ok_sources": 2,
        "dropped": [],
    },
    "clean": {"n_in": 1406, "n_out": 1406, "unknown_teams": []},
    "match": {
        "match_rate": 0.988,
        "by_method": {"external_id": 457, "exact_team": 865},
        "unmatched_top": [{"name": "Kyle Juszczyk", "position": "RB", "source": "espn.com", "points": 2.57}],
    },
    "stats": {
        "n_players": 528,
        "by_position": [{"position": "QB", "n": 65, "mean_mu": 8.4, "mean_sigma": 6.39}],
        "top": [{"name": "Jahmyr Gibbs", "position": "RB", "mu": 25.15, "sigma": 11.18}],
    },
    "accuracy": {
        "week_evaluated": 3,
        "consensus": {"ALL": {"mae": 4.12, "bias": -0.31, "corr": 0.612}},
        "best_source_by_position": {"QB": "fantasypros.com", "RB": "espn.com"},
        "coverage_80": None,
    },
    "calibrate": {
        "model_version": "v2.3",
        "weeks_evaluated": [1, 2, 3],
        "player_coverage_80": {"QB": 0.8125, "RB": 0.7932},
    },
    "lineups": {
        "teams": [
            {
                "roster_id": 5,
                "owner": "TBK41",
                "total_mu": 131.88,
                "holes": [{"slot": "RB", "replacement": "Jaylen Warren", "mu": 8.4}],
            },
            {"roster_id": 7, "owner": "mehdidrissi", "total_mu": 132.5, "holes": []},
        ],
        "pool_sizes": {"QB": 19, "RB": 47},
        "cap_by_position": {"QB": 18.41, "FLEX": 11.16},
        "unresolved": [{"roster_id": 9, "owner": "fajandfoujee", "slot": "K"}],
    },
    "simulate": {"n_sims": 50000, "teams": [{"owner": "TBK41", "mean": 131.92, "p10": 102.06, "p90": 163.83}]},
    "odds": {
        "favourites": [{"matchup": "Team 2 vs Team 5", "favourite": "TBK41", "prob": 0.719}],
        "ou_lines": [{"owner": "TBK41", "line": 130.24}],
        "highest": [{"owner": "TBK41", "prob": 0.199}],
    },
    "playoffs": {
        "future_weeks": [5, 6],
        "playoff_weeks": [15, 16, 17],
        "projections": [{"week": 5, "sources": ["sleeper.com", "espn.com"], "n_players": 505, "empty_slots": 2}],
        "first_place": [{"owner": "TBK41", "probability": 0.254}],
        "make_playoffs": [{"owner": "TBK41", "probability": 0.84}],
        "last_place": [{"owner": "fajandfoujee", "probability": 0.231}],
        "champion": [{"owner": "TBK41", "probability": 0.17}],
        "n_sims": 20000,
        "elapsed_s": 41.87,
    },
    "validate": {
        "checks": [{"name": "frozen_tables", "status": "warn", "detail": "no standings for week 4 yet"}],
        "n_ok": 11,
        "n_warn": 1,
    },
    "publish": {
        "tables": [{"name": "moneylines", "rows": 12}],
        "skipped": ["calibration_metrics"],
        "charts_uploaded": 7,
        "totals_stored": ["2026w04-20260930T225920"],
        "elapsed_s": 12.4,
        "target_host": "localhost",
    },
}


def add_steps(db_session, summaries, warnings=()):
    """Record each step's summary in one late run of week 4, which makes them the week's latest steps."""
    db_session.session.execute(
        text("""
        INSERT INTO pipeline_runs (run_id, season, week, started_at, status, steps)
        VALUES (:run_id, 2026, 4, '2026-09-29T18:00:00+00:00', 'ok', :steps)
    """),
        {"run_id": LATE_RUN, "steps": json.dumps(list(summaries))},
    )
    for step, summary in summaries.items():
        db_session.session.execute(
            text("""
            INSERT INTO pipeline_steps (run_id, step, started_at, status, summary, warnings, charts)
            VALUES (:run_id, :step, '2026-09-29T18:00:00+00:00', 'ok', :summary, :warnings, '[]')
        """),
            {"run_id": LATE_RUN, "step": step, "summary": json.dumps(summary), "warnings": json.dumps(warnings)},
        )
    db_session.session.commit()


def test_scalars_share_one_definition_list():
    summary = {"n_sims": 50000, "match_rate": 0.98765, "model_version": "v2", "coverage_80": None}

    assert summary_sections(summary) == [
        {
            "key": None,
            "kind": "scalars",
            "fields": {"n_sims": "50000", "match_rate": "0.9877", "model_version": "v2", "coverage_80": "—"},
        }
    ]


def test_lists_of_scalars_are_comma_lines_ahead_of_the_tables():
    summary = {"teams": [{"owner": "TBK41"}], "byes_this_week": ["DET", "LV"], "dropped": []}

    sections = summary_sections(summary)

    assert [section["kind"] for section in sections] == ["list", "list", "table"]
    assert sections[0] == {"key": "byes_this_week", "kind": "list", "text": "DET, LV"}
    assert sections[1]["text"] == "none"


def test_list_of_dicts_is_a_table_collapsed_past_twelve_rows():
    favourites = [{"matchup": "Team 2 vs Team 5", "favourite": "TBK41", "prob": 0.719}]
    tables = [{"name": f"table_{number}", "rows": number} for number in range(13)]

    favourites_section, tables_section = summary_sections({"favourites": favourites, "tables": tables})

    assert favourites_section == {
        "key": "favourites",
        "kind": "table",
        "columns": ["matchup", "favourite", "prob"],
        "rows": [["Team 2 vs Team 5", "TBK41", "0.719"]],
        "collapsed": False,
    }
    assert tables_section["collapsed"] is True
    assert len(tables_section["rows"]) == 13


def test_nested_lists_in_a_table_cell_are_compact():
    teams = STEP_SHAPES["lineups"]["teams"]
    projections = STEP_SHAPES["playoffs"]["projections"]

    (teams_section,) = summary_sections({"teams": teams})
    (projections_section,) = summary_sections({"projections": projections})

    assert teams_section["rows"] == [
        ["5", "TBK41", "131.88", ["RB: Jaylen Warren, 8.4"]],
        ["7", "mehdidrissi", "132.5", "—"],
    ]
    assert projections_section["rows"] == [["5", "sleeper.com, espn.com", "505", "2"]]


def test_dict_of_scalars_is_a_mapping():
    summary = {"pool_sizes": {"QB": 19, "RB": 47}, "best_source_by_position": {}}

    assert summary_sections(summary) == [
        {"key": "pool_sizes", "kind": "mapping", "rows": [("QB", "19"), ("RB", "47")]},
        {"key": "best_source_by_position", "kind": "mapping", "rows": []},
    ]


def test_dict_of_dicts_is_a_table_with_the_outer_keys_as_rows():
    summary = {"consensus": {"ALL": {"mae": 4.12, "bias": -0.31}, "QB": {"mae": 5.0, "corr": 0.61}}}

    assert summary_sections(summary) == [
        {
            "key": "consensus",
            "kind": "nested_mapping",
            "columns": ["", "mae", "bias", "corr"],
            "rows": [["ALL", "4.12", "-0.31", "—"], ["QB", "5.0", "—", "0.61"]],
            "collapsed": False,
        }
    ]


def test_empty_summary_has_no_sections():
    assert summary_sections({}) == []


def test_unknown_shapes_fall_back_to_json():
    summary = {"pairs": [[1, 2], [3]], "mixed": {"n": 1, "weeks": [2, 3]}}

    assert summary_sections(summary) == [
        {"key": "pairs", "kind": "json", "text": json.dumps([[1, 2], [3]], indent=2)},
        {"key": "mixed", "kind": "json", "text": json.dumps({"n": 1, "weeks": [2, 3]}, indent=2)},
    ]


def test_every_real_step_shape_has_a_section_kind():
    for step, summary in STEP_SHAPES.items():
        kinds = [section["kind"] for section in summary_sections(summary)]
        assert "json" not in kinds, step


def test_page_shows_scalars_and_lists_as_definition_lists(admin_client, pipeline_tables):
    html = admin_client.get("/admin/pipeline?week=4").get_data(as_text=True)

    assert "<dt>n_users</dt><dd>12</dd>" in html
    assert "<dt>byes_this_week</dt><dd>DET, LV</dd>" in html


def test_page_titles_each_table_by_its_key(admin_client, pipeline_tables, db_session):
    add_steps(db_session, {"stats": STEP_SHAPES["stats"]})

    html = admin_client.get("/admin/pipeline?week=4").get_data(as_text=True)

    assert '<h5 class="pipeline-section-title">by_position</h5>' in html
    assert "<th>mean_sigma</th>" in html
    assert "Jahmyr Gibbs" in html


def test_page_collapses_long_tables_with_their_row_count(admin_client, pipeline_tables, db_session):
    tables = [{"name": f"table_{number}", "rows": number} for number in range(15)]
    add_steps(db_session, {"publish": {**STEP_SHAPES["publish"], "tables": tables}})

    html = admin_client.get("/admin/pipeline?week=4").get_data(as_text=True)

    assert "tables (15 rows)" in html
    assert "table_14" in html


def test_page_lists_nested_cells_line_by_line(admin_client, pipeline_tables, db_session):
    add_steps(db_session, {"lineups": STEP_SHAPES["lineups"]})

    html = admin_client.get("/admin/pipeline?week=4").get_data(as_text=True)

    assert "<div>RB: Jaylen Warren, 8.4</div>" in html
    assert '<th scope="row">FLEX</th><td>11.16</td>' in html


def test_page_shows_mappings_and_nested_mappings(admin_client, pipeline_tables, db_session):
    add_steps(db_session, {"accuracy": STEP_SHAPES["accuracy"]})

    html = admin_client.get("/admin/pipeline?week=4").get_data(as_text=True)

    assert '<th scope="row">QB</th><td>fantasypros.com</td>' in html
    assert "<td>ALL</td><td>4.12</td><td>-0.31</td><td>0.612</td>" in html


def test_page_shows_the_warnings_of_an_empty_summary(admin_client, pipeline_tables, db_session, captured_templates):
    add_steps(db_session, {"accuracy": {}}, warnings=["no projections for week 3"])

    html = admin_client.get("/admin/pipeline?week=4").get_data(as_text=True)

    assert "no projections for week 3" in html
    assert captured_templates[0][1]["sections"]["accuracy"] == []


def test_page_shows_unknown_shapes_as_json(admin_client, pipeline_tables, db_session):
    add_steps(db_session, {"odds": {"pairs": [[1, 2], [3]]}})

    html = admin_client.get("/admin/pipeline?week=4").get_data(as_text=True)

    assert '<pre class="pipeline-json">[\n  [\n    1,' in html


def test_page_renders_every_real_step_shape(admin_client, pipeline_tables, db_session):
    add_steps(db_session, STEP_SHAPES)

    page = admin_client.get("/admin/pipeline?week=4")

    assert page.status_code == 200
    html = page.get_data(as_text=True)
    for summary in STEP_SHAPES.values():
        for key in summary:
            assert key in html


def test_page_lists_each_source_check_on_one_line(admin_client, pipeline_tables):
    html = admin_client.get("/admin/pipeline?week=4").get_data(as_text=True)

    assert '<span class="pipeline-check-name">position_agreement</span>' in html
    assert '<span class="pipeline-check-detail">3.1% of matched rows disagree</span>' in html
