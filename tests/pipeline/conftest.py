"""Fakes for the network side of `pipeline.sources.base`, shared by the source tests."""

import json
from types import SimpleNamespace

import pytest

from pipeline.sources import base


class Clock:
    """A fake for base.time's clock: time passes only when something sleeps, and each sleep is recorded."""

    def __init__(self):
        self.now = 1000.0
        self.sleeps = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float):
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(base.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(base.time, "sleep", clock.sleep)
    monkeypatch.setattr(base, "_last_request_at", {})
    return clock


@pytest.fixture
def serve(monkeypatch):
    """serve(body_for_url) answers base.get's requests with body_for_url(url) and returns the list each request is recorded in."""

    def install(body_for_url) -> list[dict]:
        sent = []

        def fake_get(url, headers, timeout):
            sent.append({"url": url, "headers": headers, "timeout": timeout})
            body = body_for_url(url)
            return SimpleNamespace(text=body, json=lambda: json.loads(body), raise_for_status=lambda: None)

        monkeypatch.setattr(base.requests, "get", fake_get)
        return sent

    return install
