"""The contract every projection source implements: fetch one week and return Projection rows."""

import time
from dataclasses import dataclass
from urllib.parse import urlparse

import requests

POSITIONS = frozenset({"QB", "RB", "WR", "TE", "K", "DEF"})

# Every request says what it is. A source that refuses this header is reported, never disguised.
USER_AGENT = "TNCasino-pipeline/2026 (+https://tncasino.win)"
REQUEST_SPACING_S = 2.0  # the least time between two requests to one site; a robots.txt Crawl-delay extends it
TIMEOUT_S = 30

_last_request_at: dict[str, float] = {}  # host -> monotonic time of the pipeline's last request to it


@dataclass(frozen=True)
class Projection:
    source: str  # website key, e.g. "espn.com"
    season: int
    week: int
    first_name: str
    last_name: str
    position: str  # canonical QB/RB/WR/TE/K/DEF
    team: str | None  # canonical Sleeper code or None
    points: float  # PPR projected points
    external_id: str | None = None  # source's own player id when available


class ProjectionSource:
    name: str  # short key: sleeper, espn, fantasysharks, firstdown, fanduel
    website: str  # stored in projections.source_website
    supports_future_weeks: bool
    positions: frozenset[str] = POSITIONS  # narrowed by sources that serve fewer, e.g. no DEF page
    has_week_stamp: bool = True  # False when the payload names no week and rows only echo the requested one

    def fetch(self, season: int, week: int) -> list[Projection]:
        raise NotImplementedError


def get(url: str, headers: dict | None = None, spacing_s: float = REQUEST_SPACING_S) -> requests.Response:
    """A GET that identifies the pipeline and waits out the site's spacing since the last request to it."""
    host = urlparse(url).hostname
    wait_s = _last_request_at.get(host, float("-inf")) + spacing_s - time.monotonic()
    if wait_s > 0:
        print(f"  [fetch] {host}: waiting {wait_s:.0f}s before the next request")
        time.sleep(wait_s)
    response = requests.get(url, headers={"User-Agent": USER_AGENT, **(headers or {})}, timeout=TIMEOUT_S)
    _last_request_at[host] = time.monotonic()
    response.raise_for_status()
    return response
