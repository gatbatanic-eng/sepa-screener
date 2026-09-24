from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from . import COLLECTOR_VERSION, RESEARCH_ID, SCHEMA_VERSION
from .config import load_research_definition, research_definition_hash
from .provider_fmp import FMPError, fetch_annual_estimates
from .quality import validate_symbol_rows
from .storage import write_immutable_gzip_json, write_replaceable_json
from .universe import load_us_pit_universe

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "research" / "fpd" / "raw"
PUBLIC_ROOT = ROOT / "docs" / "data" / "fpd"


def _payload_hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def collect_us(session_date: date, api_key: str | None = None) -> dict:
    definition = load_research_definition()
    universe = load_us_pit_universe()
    recorded_at = datetime.now(timezone.utc).isoformat()
    observations: dict[str, list[dict]] = {}
    failures: dict[str, dict] = {}
    universe_snapshot: dict[str, dict] = {}

    for item in universe:
        symbol = item["ticker"]
        universe_snapshot[symbol] = {
            "name": item.get("name"),
            "market": "US",
            "close": item.get("close"),
            "inUniverse": True,
        }
        try:
            rows = fetch_annual_estimates(symbol, api_key=api_key)
            errors = validate_symbol_rows(rows)
            if errors:
                failures[symbol] = {"status": "INVALID_PROVIDER_DATA", "errors": errors}
                continue
            observations[symbol] = rows
        except FMPError as exc:
            failures[symbol] = {"status": "PROVIDER_ERROR", "reason": str(exc)}

    expected = len(universe)
    observed = len(observations)
    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "researchId": RESEARCH_ID,
        "researchDefinitionHash": research_definition_hash(definition),
        "collectorVersion": COLLECTOR_VERSION,
        "snapshotDate": session_date.isoformat(),
        "recordedAt": recorded_at,
        "market": "US",
        "provider": "FMP",
        "sourceCommit": os.environ.get("GITHUB_SHA"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "universe": universe_snapshot,
        "observations": observations,
        "failures": failures,
        "manifest": {
            "symbolsExpected": expected,
            "symbolsObserved": observed,
            "symbolsFailed": len(failures),
            "coveragePct": round(observed / expected * 100, 4) if expected else 0.0,
        },
    }
    payload["providerNormalizedPayloadHash"] = _payload_hash(observations)

    raw_path = (
        RAW_ROOT / "us" / f"{session_date.year:04d}" / f"{session_date.month:02d}"
        / f"{session_date.isoformat()}.json.gz"
    )
    write_immutable_gzip_json(raw_path, payload)

    latest = {
        "schemaVersion": SCHEMA_VERSION,
        "researchId": RESEARCH_ID,
        "snapshotDate": session_date.isoformat(),
        "recordedAt": recorded_at,
        "market": "US",
        "status": "COLLECTED" if not failures else "PARTIAL",
        "manifest": payload["manifest"],
        "failures": failures,
        "researchDefinitionHash": payload["researchDefinitionHash"],
        "collectorVersion": COLLECTOR_VERSION,
    }
    write_replaceable_json(PUBLIC_ROOT / "latest_us.json", latest)
    return latest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Trading-session date YYYY-MM-DD; defaults to UTC date")
    args = parser.parse_args()
    session_date = date.fromisoformat(args.date) if args.date else datetime.now(timezone.utc).date()
    result = collect_us(session_date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
