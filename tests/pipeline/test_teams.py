import pytest

from pipeline.sources.teams import CANONICAL_TEAMS, DEF_NAMES, normalize_team, team_from_def_name


def test_thirty_two_canonical_teams():
    assert len(CANONICAL_TEAMS) == 32
    assert set(DEF_NAMES) == CANONICAL_TEAMS


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("SEA", "SEA"),
        ("sea", "SEA"),
        (" KC ", "KC"),
        ("JAC", "JAX"),
        ("WSH", "WAS"),
        ("GBP", "GB"),
        ("KCC", "KC"),
        ("NEP", "NE"),
        ("NOS", "NO"),
        ("SFO", "SF"),
        ("TBB", "TB"),
        ("LVR", "LV"),
        ("OAK", "LV"),
        ("LA", "LAR"),
        ("FA", None),
        ("", None),
        (None, None),
    ],
)
def test_normalize_team(code, expected):
    assert normalize_team(code) == expected


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Seahawks D/ST", "SEA"),
        ("Seattle Seahawks", "SEA"),
        ("Seahawks, Seattle", "SEA"),
        ("Seahawks", "SEA"),
        ("Seattle", "SEA"),
        ("SEA", "SEA"),
        ("49ers D/ST", "SF"),
        ("Kansas City Chiefs", "KC"),
        ("New York Giants", "NYG"),
        ("Jets DEF", "NYJ"),
        ("Los Angeles", None),
        ("New York", None),
        ("Josh Allen", None),
    ],
)
def test_team_from_def_name(text, expected):
    assert team_from_def_name(text) == expected
