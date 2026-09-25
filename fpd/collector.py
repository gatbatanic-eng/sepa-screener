from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from . import COLLECTOR_VERSION, DATASET_ID, RESEARCH_COHORT, RESEARCH_ID, SCHEMA_VERSION
from .config import load_research_definition, research_definition_hash
from .prices import benchmark_close_for_session, latest_sepa_us_session
from .provider_fmp import FMPError, fetch_annual_estimates, fetch_recent_earnings, fetch_recent_splits
from .quality import validate_symbol_rows
from .storage import write_immutable_gzip_json, write_replaceable_json
from .universe import load_us_pit_universe

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "research" / "fpd" / "raw"
PUBLIC_ROOT = ROOT / "docs" / "data" / "fpd"


def _payload_hash(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _raw_path(session_date: date) -> Path:
    return (
        RAW_ROOT / "us" / f"{session_date.year:04d}" / f"{session_date.month:02d}"
        / f"{session_date.isoformat()}.json.gz"
    )


def collect_us(session_date: date, api_key: str | None = None) -> dict:
    latest_session = latest_sepa_us_session()
    if session_date != latest_session:
        raise RuntimeError(
            f"FPD can only collect the latest closed SEPA US session: "
            f"requested={session_date.isoformat()} latest={latest_session.isoformat()}"
        )

    raw_path = _raw_path(session_date)
    if raw_path.exists():
        return {
            "schemaVersion": SCHEMA_VERSION,
            "researchId": RESEARCH_ID,
            "snapshotDate": session_date.isoformat(),
            "market": "US",
            "status": "ALREADY_COLLECTED",
        }

    definition = load_research_definition()
    universe = load_us_pit_universe()
    benchmark = benchmark_close_for_session(session_date)
    symbols = {item["ticker"] for item in universe}

    # Split coverage is integrity-critical: a missed split can create false EPS
    # revision and price-velocity jumps. Earnings events are diagnostic only.
    split_rows = fetch_recent_splits(session_date, api_key=api_key)
    split_events: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
    for event in split_rows:
        symbol = str(event.get("symbol") or "").strip()
        if symbol in split_events and event.get("date"):
            split_events[symbol].append({
                "date": str(event.get("date"))[:10],
                "numerator": event.get("numerator"),
                "denominator": event.get("denominator"),
                "splitType": event.get("splitType"),
            })

    earnings_status = "OK"
    earnings_events: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
    try:
        earnings_rows = fetch_recent_earnings(session_date, api_key=api_key)
        for event in earnings_rows:
            symbol = str(event.get("symbol") or "").strip()
            if symbol in earnings_events and event.get("date"):
                earnings_events[symbol].append({
                    "date": str(event.get("date"))[:10],
                    "epsActual": event.get("epsActual"),
                    "epsEstimated": event.get("epsEstimated"),
                    "revenueActual": event.get("revenueActual"),
                    "revenueEstimated": event.get("revenueEstimated"),
                    "lastUpdated": event.get("lastUpdated"),
                })
    except FMPError as exc:
        earnings_status = f"UNAVAILABLE:{exc}"

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
            "priceAsOf": session_date.isoformat(),
            "inUniverse": True,
        }
        try:
            rows = fetch_annual_estimates(symbol, api_key=api_key)
            rows = [
                row for row in rows
                if row.get("periodEnd") and date.fromisoformat(row["periodEnd"]) > session_date
            ]
            if not rows:
                failures[symbol] = {"status": "NO_FORWARD_ESTIMATES"}
                continue
            errors = validate_symbol_rows(rows)
            if errors:
                failures[symbol] = {"status": "INVALID_PROVIDER_DATA", "errors": errors}
                continue
            observations[symbol] = rows
        except FMPError as exc:
            failures[symbol] = {"status": "PROVIDER_ERROR", "reason": str(exc)}

    expected = len(universe)
    observed = len(observations)
    if observed == 0:
        raise RuntimeError("FPD collection produced zero valid estimate observations; raw snapshot not written")

    payload = {
        "schemaVersion": SCHEMA_VERSION,
        "researchId": RESEARCH_ID,
        "datasetId": DATASET_ID,
        "researchCohort": RESEARCH_COHORT,
        "researchDefinitionHash": research_definition_hash(definition),
        "collectorVersion": COLLECTOR_VERSION,
        "snapshotDate": session_date.isoformat(),
        "recordedAt": recorded_at,
        "market": "US",
        "provider": "FMP",
        "benchmark": benchmark,
        "events": {
            "splitCalendarStatus": "OK",
            "earningsCalendarStatus": earnings_status,
            "splits": split_events,
            "earnings": earnings_events,
        },
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
    write_immutable_gzip_json(raw_path, payload)

    latest = {
        "schemaVersion": SCHEMA_VERSION,
        "researchId": RESEARCH_ID,
        "datasetId": DATASET_ID,
        "researchCohort": RESEARCH_COHORT,
        "snapshotDate": session_date.isoformat(),
        "recordedAt": recorded_at,
        "market": "US",
        "status": "COLLECTED" if not failures else "PARTIAL",
        "benchmark": benchmark,
        "manifest": payload["manifest"],
        "failures": failures,
        "researchDefinitionHash": payload["researchDefinitionHash"],
        "collectorVersion": COLLECTOR_VERSION,
    }
    write_replaceable_json(PUBLIC_ROOT / "latest_us.json", latest)
    return latest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", help="Latest closed US trading-session date YYYY-MM-DD; defaults to SEPA latestSession")
    args = parser.parse_args()
    session_date = date.fromisoformat(args.date) if args.date else latest_sepa_us_session()
    result = collect_us(session_date)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
