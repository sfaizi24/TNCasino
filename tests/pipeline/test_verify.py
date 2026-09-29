import json
from dataclasses import replace
from pathlib import Path

import pytest

from pipeline.sources import sleeper
from pipeline.sources.base import POSITIONS, Projection
from pipeline.sources.verify import (
    Check,
    SourceReport,
    duplicate_positions,
    position_agreement,
    position_counts,
    value_agreement,
    verify_source,
    week_stamp,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sleeper" / "projections_2026_w4.json"
CHECK_NAMES = [
    "position_agreement",
    "duplicate_positions",
    "position_counts",
    "value_agreement",
    "team_codes",
    "week_stamp",
    "freshness",
    "top_players",
]


@pytest.fixture(scope="module")
def payload():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def sleeper_players(payload):
    """nfl_players rows for everyone in the fixture, projected or not."""
    return [
        {
            "player_id": row["player_id"],
            "first_name": row["player"]["first_name"],
            "last_name": row["player"]["last_name"],
            "position": row["player"]["position"],
            "team": row["player"]["team"],
        }
        for row in payload
    ]


@pytest.fixture(scope="module")
def sleeper_rows(payload):
    return sleeper.parse(payload, 2026, 4)


@pytest.fixture(scope="module")
def clean_rows(sleeper_rows):
    """A second source that agrees with Sleeper: the same 188 players, 10% more points each."""
    return [replace(row, source="example.com", points=round(row.points * 1.1, 2)) for row in sleeper_rows]


def verify(rows, sleeper_players, sleeper_rows, previous_rows=(), positions=POSITIONS) -> SourceReport:
    return verify_source(rows, 4, sleeper_players, sleeper_rows, list(previous_rows), positions)


def checks_by_name(report: SourceReport) -> dict[str, Check]:
    return {check.name: check for check in report.checks}


def edit_player(rows: list[Projection], full_name: str, **changes) -> list[Projection]:
    edited = []
    for row in rows:
        if f"{row.first_name} {row.last_name}" == full_name:
            row = replace(row, **changes)
        edited.append(row)
    return edited


def reverse_points(rows: list[Projection], position: str) -> list[Projection]:
    """Give the best player at `position` the worst player's points, the second best the second worst, and so on."""
    ranked = sorted([row for row in rows if row.position == position], key=lambda row: row.points)
    points = [row.points for row in reversed(ranked)]
    reversed_rows = [replace(row, points=new_points) for row, new_points in zip(ranked, points, strict=True)]
    return [row for row in rows if row.position != position] + reversed_rows


def alternate_points(rows: list[Projection], position: str, offset: float) -> list[Projection]:
    """Add `offset` to every other player at `position` and take it from the rest: same order, lower r."""
    edited = []
    at_position = 0
    for row in rows:
        if row.position == position:
            sign = 1 if at_position % 2 == 0 else -1
            row = replace(row, points=row.points + sign * offset)
            at_position += 1
        edited.append(row)
    return edited


def test_clean_source_passes_every_check(clean_rows, sleeper_players, sleeper_rows):
    previous_week = [replace(row, week=3, points=row.points + 1.5) for row in clean_rows]

    report = verify(clean_rows, sleeper_players, sleeper_rows, previous_week)

    assert (report.source, report.status, report.n_rows) == ("example.com", "ok", 188)
    assert [check.name for check in report.checks] == CHECK_NAMES
    for check in report.checks:
        assert check.status == "ok", check
        assert not check.detail.startswith("n/a"), check
        assert check.detail.isascii()


def test_clean_source_passes_every_check_for_a_future_week(clean_rows, sleeper_players, sleeper_rows):
    report = verify_source(clean_rows, 4, sleeper_players, sleeper_rows, [], POSITIONS, future_week=True)

    assert report.status == "ok"
    assert [check.name for check in report.checks] == CHECK_NAMES


def test_relabelled_players_fail_position_agreement(clean_rows, sleeper_players):
    backs = [row for row in clean_rows if row.position in {"QB", "RB"}]
    backs = edit_player(backs, "Jahmyr Gibbs", position="QB")
    backs = edit_player(backs, "Bijan Robinson", position="WR")

    check = position_agreement(backs, sleeper_players)

    assert check == Check(
        "position_agreement",
        "fail",
        "2 of 68 matched rows differ (2.9%): Jahmyr Gibbs RB->QB, Bijan Robinson RB->WR",
    )


def test_one_relabelled_player_in_a_full_list_is_a_warning(clean_rows, sleeper_players):
    rows = edit_player(clean_rows, "Jahmyr Gibbs", position="QB")

    check = position_agreement(rows, sleeper_players)

    assert check == Check("position_agreement", "warn", "1 of 164 matched rows differ (0.6%): Jahmyr Gibbs RB->QB")


def test_namesakes_are_told_apart_by_team(clean_rows, sleeper_players):
    receiver = {"player_id": "90001", "first_name": "Jahmyr", "last_name": "Gibbs", "position": "WR", "team": "NYJ"}
    players = [*sleeper_players, receiver]
    relabelled = edit_player(clean_rows, "Jahmyr Gibbs", position="QB")
    traded = edit_player(relabelled, "Jahmyr Gibbs", team="CHI")

    assert position_agreement(relabelled, players).detail.startswith("1 of 164 matched rows differ")
    assert position_agreement(traded, players).detail == "0 of 163 matched rows differ (0.0%)"


def test_last_weeks_numbers_fail_freshness(clean_rows, sleeper_players, sleeper_rows):
    previous_week = [replace(row, week=3) for row in clean_rows]

    report = verify(clean_rows, sleeper_players, sleeper_rows, previous_week)

    assert report.status == "fail"
    assert checks_by_name(report)["freshness"] == Check(
        "freshness", "fail", "188 of 188 players have the same points as the previous week (100.0%)"
    )


def test_truncated_receiver_list_fails_position_counts(clean_rows, sleeper_players, sleeper_rows):
    receivers = [row for row in clean_rows if row.position == "WR"][:20]
    rows = [row for row in clean_rows if row.position != "WR"] + receivers

    report = verify(rows, sleeper_players, sleeper_rows)

    assert report.status == "fail"
    assert checks_by_name(report)["position_counts"] == Check(
        "position_counts", "fail", "outside the expected range: WR 20 (expected 50-200)"
    )


def test_unrecognised_teams_fail_team_codes(clean_rows, sleeper_players, sleeper_rows):
    rows = [replace(row, team=None) if index % 10 == 0 else row for index, row in enumerate(clean_rows)]

    check = checks_by_name(verify(rows, sleeper_players, sleeper_rows))["team_codes"]

    assert check.status == "fail"
    assert check.detail.startswith("19 of 188 rows have no recognised team (10.1%): ")


def test_one_row_without_a_team_is_a_warning(clean_rows, sleeper_players, sleeper_rows):
    rows = edit_player(clean_rows, "Josh Allen", team=None)

    check = checks_by_name(verify(rows, sleeper_players, sleeper_rows))["team_codes"]

    assert check == Check("team_codes", "warn", "1 of 188 rows have no recognised team (0.5%): Josh Allen")


def test_top_player_missing_from_sleeper_fails_top_players(clean_rows, sleeper_players, sleeper_rows):
    rows = edit_player(clean_rows, "Josh Allen", first_name="Joshua")

    report = verify(rows, sleeper_players, sleeper_rows)

    assert report.status == "fail"
    assert checks_by_name(report)["top_players"] == Check(
        "top_players", "fail", "1 of 18 top-3 players are not in Sleeper's player database: Joshua Allen QB"
    )


def test_source_without_defenses_skips_the_defense_count(clean_rows, sleeper_players, sleeper_rows):
    rows = [row for row in clean_rows if row.position != "DEF"]

    report = verify(rows, sleeper_players, sleeper_rows, positions=POSITIONS - {"DEF"})

    assert report.status == "ok"
    assert checks_by_name(report)["position_counts"] == Check(
        "position_counts", "ok", "QB 24, RB 44, WR 54, TE 24, K 18"
    )


def test_missing_defenses_fail_a_source_that_provides_them(clean_rows):
    rows = [row for row in clean_rows if row.position != "DEF"]

    check = position_counts(rows, POSITIONS)

    assert check == Check("position_counts", "fail", "outside the expected range: DEF 0 (expected 20-36)")


def test_empty_source_fails(sleeper_players, sleeper_rows):
    report = verify([], sleeper_players, sleeper_rows)

    assert (report.status, report.n_rows) == ("fail", 0)


def test_rows_for_another_week_fail_week_stamp(clean_rows):
    rows = [replace(row, week=3) for row in clean_rows]

    assert week_stamp(rows, 4) == Check("week_stamp", "fail", "expected week 4, found week 3 (188 rows)")


def test_week_stamp_is_na_for_a_source_that_names_no_week(clean_rows):
    rows = [replace(row, week=3) for row in clean_rows]

    assert week_stamp(rows, 4, has_week_stamp=False) == Check(
        "week_stamp", "ok", "n/a: the source names no week, so rows carry the requested one"
    )


def test_value_disagreement_fails_at_quarterback(clean_rows, sleeper_rows):
    check = value_agreement(reverse_points(clean_rows, "QB"), sleeper_rows)

    assert check.status == "fail"
    assert check.detail.startswith("QB r=-")
    assert "n=24 (fail), RB r=1.00" in check.detail


def test_close_quarterbacks_with_a_low_r_fail_this_week_but_warn_for_a_future_week(clean_rows, sleeper_rows):
    rows = alternate_points(clean_rows, "QB", 2.5)

    this_week = value_agreement(rows, sleeper_rows)
    future_week = value_agreement(rows, sleeper_rows, future_week=True)

    assert this_week.status == "fail"
    assert this_week.detail.startswith("QB r=0.69 MAD=2.55 n=24 (fail), RB r=1.00")
    assert future_week.status == "warn"
    assert future_week.detail.startswith("QB r=0.69 MAD=2.55 n=24 (warn), RB r=1.00")
    assert future_week.detail.count("(warn)") == 1


def test_quarterbacks_far_apart_fail_a_future_week(clean_rows, sleeper_rows):
    check = value_agreement(alternate_points(clean_rows, "QB", 5.0), sleeper_rows, future_week=True)

    assert check.status == "fail"
    assert check.detail.startswith("QB r=")
    assert "MAD=5.05 n=24 (fail), RB r=1.00" in check.detail


def test_running_backs_with_a_low_r_fail_a_future_week(clean_rows, sleeper_rows):
    check = value_agreement(reverse_points(clean_rows, "RB"), sleeper_rows, future_week=True)

    assert check.status == "fail"
    assert "n=44 (fail)" in check.detail


def test_value_disagreement_only_warns_at_kicker(clean_rows, sleeper_players, sleeper_rows):
    report = verify(reverse_points(clean_rows, "K"), sleeper_players, sleeper_rows)

    check = checks_by_name(report)["value_agreement"]
    assert report.status == "warn"
    assert check.status == "warn"
    assert "n=18 (warn)" in check.detail


def test_value_agreement_is_na_without_sleeper_projections(clean_rows):
    assert value_agreement(clean_rows, []) == Check(
        "value_agreement", "ok", "n/a: no Sleeper projections to compare with"
    )


def test_player_listed_under_two_positions(clean_rows):
    gibbs = [row for row in clean_rows if row.last_name == "Gibbs"]
    one_duplicate = clean_rows + [replace(gibbs[0], position="WR")]
    three_duplicates = one_duplicate + [
        replace(row, position="TE") for row in clean_rows if row.last_name in {"Robinson", "Taylor"}
    ]

    assert duplicate_positions(one_duplicate) == Check(
        "duplicate_positions", "warn", "1 of 188 players listed under more than one position: Jahmyr Gibbs RB/WR"
    )
    assert duplicate_positions(three_duplicates).status == "fail"
