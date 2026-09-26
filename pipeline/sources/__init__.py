"""Projection source registry. Each source module exposes a SOURCE instance and is imported on demand."""

import importlib

from pipeline.sources.base import ProjectionSource

SOURCE_NAMES = ["sleeper", "espn", "fantasysharks", "fantasypros", "firstdown", "fanduel"]


def load_source(name: str) -> ProjectionSource:
    return importlib.import_module(f"pipeline.sources.{name}").SOURCE
