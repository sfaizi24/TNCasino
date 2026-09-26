import pytest

from pipeline.names import normalize_name, split_full_name, strip_suffix


@pytest.mark.parametrize(
    ("full_name", "expected"),
    [
        ("Josh Allen", ("Josh", "Allen")),
        ("Amon-Ra St. Brown", ("Amon-Ra", "St. Brown")),
        ("Marvin Harrison Jr.", ("Marvin", "Harrison Jr.")),
        ("  Ja'Marr   Chase ", ("Ja'Marr", "Chase")),
        ("Seahawks", ("", "Seahawks")),
    ],
)
def test_split_full_name(full_name, expected):
    assert split_full_name(full_name) == expected


@pytest.mark.parametrize(
    ("last_name", "expected"),
    [
        ("Harrison Jr.", "Harrison"),
        ("Cook III", "Cook"),
        ("Pitts Sr.", "Pitts"),
        ("Gordon II", "Gordon"),
        ("St. Brown", "St. Brown"),
        ("Allen", "Allen"),
        ("V", "V"),
    ],
)
def test_strip_suffix(last_name, expected):
    assert strip_suffix(last_name) == expected


@pytest.mark.parametrize(
    ("first", "last", "expected"),
    [
        ("Marvin", "Harrison Jr.", ("marvin", "harrison")),
        ("D'Andre", "Swift", ("dandre", "swift")),
        ("Amon-Ra", "St. Brown", ("amonra", "st brown")),
        ("T.J.", "Hockenson", ("tj", "hockenson")),
        ("JOSH", "  Allen ", ("josh", "allen")),
    ],
)
def test_normalize_name(first, last, expected):
    assert normalize_name(first, last) == expected


def test_normalized_names_agree_across_source_spellings():
    assert normalize_name("Marvin", "Harrison Jr.") == normalize_name("Marvin", "Harrison")
    assert normalize_name("James", "Cook III") == normalize_name("James", "Cook")
