"""config/*.yaml 로더. 값은 yaml 한 곳에서만 온다."""
from __future__ import annotations

from pathlib import Path

import yaml

_DIR = Path(__file__).resolve().parent


def load(name: str) -> dict:
    with open(_DIR / f"{name}.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)
