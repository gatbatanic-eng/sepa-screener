from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import date, datetime, timezone
from pathlib import Path

from . import COLLECTOR_VERSION, DATASET_ID, PANEL_ID, RESEARCH_COHORT, RESEARCH_ID, SCHEMA_VERSION
from .config import load_research_definition, research_definition_hash
from .prices import benchmark_close_for_session, latest_sepa_us_session
from .provider_fmp import FMPError, fetch_annual_estimates
from .provider_yahoo import YahooActionsError, fetch_recent_splits_yahoo
from .quality import validate_symbol_rows
from .storage import write_immutable_gzip_json, write_replaceable_json
from .universe import load_us_pit_universe, select_frozen_free_panel

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
            "datasetId": DATASET_ID,
            "panelId": PANEL_ID,
            "snapshotDate": session_date.isoformat(),
            "market": "US",
            "status": "ALREADY_COLLECTED",
        }

    definition = load_research_definition()
    full_universe = load_us_pit_universe()
    universe, panel_meta = select_frozen_free_panel(full_universe)
    if len(universe) > int(definition["freeMode"]["panelEstimateCallsMax"]):
        raise RuntimeError("Active free panel exceeds frozen FMP estimate-call budget")

    benchmark = benchmark_close_for_session(session_date)
    symbols = [item["ticker"] for item in universe]

    # Free-tier split integrity source. This uses one Yahoo multi-ticker download
    # and consumes no FMP request quota.
    split_status = "OK"
    try:
        split_payload = fetch_recent_splits_yahoo(symbols, session_date)
        split_events = split_payload["events"]
        split_coverage = split_payload["covered"]
        if not all(split_coverage.get(symbol, False) for symbol in symbols):
            split_status = "PARTIAL"
    except YahooActionsError as exc:
        split_status = f"UNAVAILABLE:{exc}"
        split_events = {symbol: [] for symbol in symbols}
        split_coverage = {symbol: False for symbol in symbols}

    recorded_at = datetime.now(timezone.utc).isoformat()
    observations: dict[str, list[dict]] = {}
    failures: dict[str, dict] = {}
    universe_snapshot: dict[str, dict] = {}
    estimate_calls_attempted = 0

    for item in universe:
        symbol = item["ticker"]
        universe_snapshot[symbol] = {
            "name": item.get("name"),
            "market": "US",
            "close": item.get("close"),
            "priceAsOf": session_date.isoformat(),
            "inUniverse": True,
            "inResearchPanel": True,
        }
        try:
            estimate_calls_attempted += 1
            rows = fetch_annual_estimates(
                symbol,
                api_key=api_key,
                retries=1,
            )
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
        "panelId": PANEL_ID,
        "researchDefinitionHash": research_definition_hash(definition),
        "collectorVersion": COLLECTOR_VERSION,
        "snapshotDate": session_date.isoformat(),
        "recordedAt": recorded_at,
        "market": "US",
        "provider": "FMP",
        "providerMode": "FREE_TIER",
        "benchmark": benchmark,
        "panel": panel_meta,
        "events": {
            "splitCalendarStatus": split_status,
            "splitSource": "YAHOO_FINANCE_ACTIONS",
            "splitCoverage": split_coverage,
            "splits": split_events,
            "earningsCalendarStatus": "UNAVAILABLE_FREE_MODE",
            "earnings": {symbol: [] for symbol in symbols},
        },
        "sourceCommit": os.environ.get("GITHUB_SHA"),
        "githubRunId": os.environ.get("GITHUB_RUN_ID"),
        "universe": universe_snapshot,
        "observations": observations,
        "failures": failures,
        "manifest": {
            "sourceUniverseSize": len(full_universe),
            "frozenPanelSize": panel_meta["frozenSymbols"],
            "activePanelSize": expected,
            "symbolsExpected": expected,
            "symbolsObserved": observed,
            "symbolsFailed": len(failures),
            "coveragePct": round(observed / expected * 100, 4) if expected else 0.0,
            "fmpEstimateCallsAttempted": estimate_calls_attempted,
            "fmpContractCheckCallsOutsideCollector": 1,
            "fmpDailyCallBudget": int(definition["freeMode"]["fmpDailyCallBudget"]),
            "fmpReservedCalls": int(definition["freeMode"]["reservedCalls"]),
        },
    }
    payload["providerNormalizedPayloadHash"] = _payload_hash(observations)
    write_immutable_gzip_json(raw_path, payload)

    latest = {
        "schemaVersion": SCHEMA_VERSION,
        "researchId": RESEARCH_ID,
        "datasetId": DATASET_ID,
        "researchCohort": RESEARCH_COHORT,
        "panelId": PANEL_ID,
        "snapshotDate": session_date.isoformat(),
        "recordedAt": recorded_at,
        "market": "US",
        "providerMode": "FREE_TIER",
        "status": "COLLECTED" if not failures else "PARTIAL",
        "benchmark": benchmark,
        "panel": panel_meta,
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
