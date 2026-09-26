"""NFL team codes as Sleeper spells them, plus the aliases other projection sources use."""

import re

DEF_NAMES: dict[str, tuple[str, str]] = {
    "ARI": ("Arizona", "Cardinals"),
    "ATL": ("Atlanta", "Falcons"),
    "BAL": ("Baltimore", "Ravens"),
    "BUF": ("Buffalo", "Bills"),
    "CAR": ("Carolina", "Panthers"),
    "CHI": ("Chicago", "Bears"),
    "CIN": ("Cincinnati", "Bengals"),
    "CLE": ("Cleveland", "Browns"),
    "DAL": ("Dallas", "Cowboys"),
    "DEN": ("Denver", "Broncos"),
    "DET": ("Detroit", "Lions"),
    "GB": ("Green Bay", "Packers"),
    "HOU": ("Houston", "Texans"),
    "IND": ("Indianapolis", "Colts"),
    "JAX": ("Jacksonville", "Jaguars"),
    "KC": ("Kansas City", "Chiefs"),
    "LAC": ("Los Angeles", "Chargers"),
    "LAR": ("Los Angeles", "Rams"),
    "LV": ("Las Vegas", "Raiders"),
    "MIA": ("Miami", "Dolphins"),
    "MIN": ("Minnesota", "Vikings"),
    "NE": ("New England", "Patriots"),
    "NO": ("New Orleans", "Saints"),
    "NYG": ("New York", "Giants"),
    "NYJ": ("New York", "Jets"),
    "PHI": ("Philadelphia", "Eagles"),
    "PIT": ("Pittsburgh", "Steelers"),
    "SEA": ("Seattle", "Seahawks"),
    "SF": ("San Francisco", "49ers"),
    "TB": ("Tampa Bay", "Buccaneers"),
    "TEN": ("Tennessee", "Titans"),
    "WAS": ("Washington", "Commanders"),
}

CANONICAL_TEAMS = frozenset(DEF_NAMES)

# FantasySharks (GBP, KCC, ...), FantasyPros (JAC), ESPN (WSH) and older data (OAK, SD, STL).
ALIASES = {
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "GBP": "GB",
    "HST": "HOU",
    "JAC": "JAX",
    "KCC": "KC",
    "LA": "LAR",
    "LVR": "LV",
    "NEP": "NE",
    "NOS": "NO",
    "OAK": "LV",
    "SD": "LAC",
    "SFO": "SF",
    "STL": "LAR",
    "TBB": "TB",
    "WSH": "WAS",
}

_NICKNAMES = {nickname.lower(): code for code, (_, nickname) in DEF_NAMES.items()}
_CITIES = {city.lower(): code for code, (city, _) in DEF_NAMES.items()}
_SHARED_CITIES = {"los angeles", "new york"}
_DEFENSE_WORDS = re.compile(r"\b(d/st|dst|def|defense)\b")


def normalize_team(code: str | None) -> str | None:
    if not code:
        return None
    code = code.strip().upper()
    code = ALIASES.get(code, code)
    return code if code in CANONICAL_TEAMS else None


def team_from_def_name(text: str) -> str | None:
    """Resolve a defense label however a source writes it.

    "Seahawks D/ST", "Seattle Seahawks", "Seahawks, Seattle", "Seahawks", "Seattle" and "SEA"
    all give "SEA". A city shared by two teams ("Los Angeles") is not enough on its own.
    """
    words = re.findall(r"[a-z0-9]+", _DEFENSE_WORDS.sub(" ", text.lower()))
    for word in words:
        if word in _NICKNAMES:
            return _NICKNAMES[word]
    city = " ".join(words)
    if city in _CITIES and city not in _SHARED_CITIES:
        return _CITIES[city]
    return normalize_team(text)
