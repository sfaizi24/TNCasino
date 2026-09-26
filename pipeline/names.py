"""Player-name helpers shared by the sources, the clean step and the match step.

Sources spell the same player differently: Sleeper has no suffixes ("Marvin Harrison"), ESPN and
FantasySharks keep them ("Harrison Jr."), and punctuation varies ("D'Andre", "St. Brown"). Rows are
stored as the source spells them; matching compares the normalised form.
"""

import re

SUFFIXES = frozenset({"jr", "sr", "ii", "iii", "iv", "v"})


def split_full_name(full_name: str) -> tuple[str, str]:
    """ "Amon-Ra St. Brown" -> ("Amon-Ra", "St. Brown"). A single token is a last name."""
    parts = full_name.split()
    if len(parts) < 2:
        return "", full_name.strip()
    return parts[0], " ".join(parts[1:])


def strip_suffix(last_name: str) -> str:
    parts = last_name.split()
    while len(parts) > 1 and parts[-1].rstrip(".").lower() in SUFFIXES:
        parts.pop()
    return " ".join(parts)


def normalize_name(first: str, last: str) -> tuple[str, str]:
    """Matching key: lower case, suffix and punctuation removed, spaces collapsed."""
    return _normalize(first), _normalize(strip_suffix(last))


def _normalize(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", "", text.lower()).split())
