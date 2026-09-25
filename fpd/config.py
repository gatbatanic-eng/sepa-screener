from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DEFINITION_PATH = ROOT / "research" / "fpd" / "registry" / "fpd_v0.2.2_free.json"


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def load_research_definition(path: Path = RESEARCH_DEFINITION_PATH) -> dict:
    definition = json.loads(path.read_text(encoding="utf-8"))
    if definition.get("status") != "FROZEN":
        raise RuntimeError("FPD research definition must be FROZEN before collection")
    return definition


def research_definition_hash(definition: dict | None = None) -> str:
    definition = definition or load_research_definition()
    return hashlib.sha256(canonical_json(definition).encode("utf-8")).hexdigest()
