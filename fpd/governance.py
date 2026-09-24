from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .config import canonical_json

ALLOWED_STATUS = {
    "PROPOSED", "FROZEN", "RUNNING", "FAILED", "INCONCLUSIVE",
    "VALIDATED", "OOS_PENDING", "OOS_PASSED", "OOS_FAILED", "RETIRED",
}


def definition_hash(definition: dict) -> str:
    return hashlib.sha256(canonical_json(definition).encode("utf-8")).hexdigest()


def experiment_identity(experiment: dict) -> dict:
    """Fields that define the hypothesis and may not change after FROZEN."""
    return {
        key: copy.deepcopy(experiment.get(key))
        for key in (
            "researchId", "family", "parentResearchId", "hypothesis",
            "variables", "formula", "universe", "testPeriod",
            "primaryHorizons", "primaryMetrics", "classification",
        )
    }


def experiment_hash(experiment: dict) -> str:
    return definition_hash(experiment_identity(experiment))


def validate_registry(registry: dict) -> list[str]:
    errors: list[str] = []
    experiments = registry.get("experiments")
    if not isinstance(experiments, list):
        return ["EXPERIMENTS_NOT_LIST"]
    seen: set[str] = set()
    for item in experiments:
        rid = item.get("researchId")
        if not isinstance(rid, str) or not rid:
            errors.append("MISSING_RESEARCH_ID")
            continue
        if rid in seen:
            errors.append(f"DUPLICATE_RESEARCH_ID:{rid}")
        seen.add(rid)
        if item.get("status") not in ALLOWED_STATUS:
            errors.append(f"INVALID_STATUS:{rid}")
        if item.get("status") == "FROZEN":
            stored = item.get("definitionHash")
            actual = experiment_hash(item)
            if stored != actual:
                errors.append(f"FROZEN_DEFINITION_CHANGED:{rid}")
    return sorted(set(errors))


def freeze_experiment(experiment: dict) -> dict:
    if experiment.get("status") not in (None, "PROPOSED", "FROZEN"):
        raise RuntimeError("Only proposed experiments can be frozen")
    frozen = copy.deepcopy(experiment)
    frozen["status"] = "FROZEN"
    frozen["definitionHash"] = experiment_hash(frozen)
    return frozen


def register_experiment(registry: dict, experiment: dict) -> dict:
    output = copy.deepcopy(registry)
    output.setdefault("experiments", [])
    rid = experiment.get("researchId")
    if any(x.get("researchId") == rid for x in output["experiments"]):
        raise RuntimeError(f"Research ID already registered: {rid}")
    output["experiments"].append(copy.deepcopy(experiment))
    errors = validate_registry(output)
    if errors:
        raise RuntimeError("Invalid research registry: " + ", ".join(errors))
    return output


def load_registry(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))
