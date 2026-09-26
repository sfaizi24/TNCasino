"""Step registry: the canonical step order, CLI step selection, and on-demand import of step modules."""

import importlib
from types import ModuleType

STEP_ORDER = [
    "league",
    "scrape",
    "clean",
    "match",
    "stats",
    "calibrate",
    "lineups",
    "simulate",
    "odds",
    "playoffs",
    "accuracy",
    "validate",
    "publish",
]
DEFAULT_STEPS = [step for step in STEP_ORDER if step != "publish"]


def load_step(name: str) -> ModuleType:
    return importlib.import_module(f"pipeline.steps.{name}")


def resolve_steps(steps_arg: str | None, from_arg: str | None) -> list[str]:
    """Turn --steps or --from into step names in canonical order; publish only runs when named in --steps."""
    if steps_arg is not None:
        requested = [name.strip() for name in steps_arg.split(",")]
        unknown = [name for name in requested if name not in STEP_ORDER]
        if unknown:
            raise ValueError(f"unknown steps {unknown}; choose from {', '.join(STEP_ORDER)}")
        return [step for step in STEP_ORDER if step in requested]

    if from_arg is not None:
        if from_arg not in DEFAULT_STEPS:
            raise ValueError(f"--from must be one of {', '.join(DEFAULT_STEPS)}")
        return DEFAULT_STEPS[DEFAULT_STEPS.index(from_arg) :]

    return list(DEFAULT_STEPS)
