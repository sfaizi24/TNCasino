"""Load model parameters by version from the JSON files committed under pipeline/model/params/."""

import json
from pathlib import Path

PARAMS_DIR = Path(__file__).parent / "params"


def load_params(version: str) -> dict:
    path = PARAMS_DIR / f"{version}.json"
    if not path.exists():
        available = ", ".join(sorted(file.stem for file in PARAMS_DIR.glob("*.json")))
        raise ValueError(f"unknown model version {version!r}; available: {available}")
    return json.loads(path.read_text(encoding="utf-8"))
