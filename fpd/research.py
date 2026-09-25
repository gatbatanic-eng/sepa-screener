from __future__ import annotations

from datetime import date
from pathlib import Path

from . import DATASET_ID
from .backtest import confirmed_monthly_cohort_dates
from .cross_section import score_primary_f1
from .derive import derive_snapshot, raw_snapshot_paths
from .outcomes import attach_outcomes
from .prices import sepa_us_sessions
from .storage import read_gzip_json, write_replaceable_json

ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = ROOT / "docs" / "data" / "fpd"


def load_all_raw(
    market: str = "us",
    dataset_id: str | None = DATASET_ID,
) -> list[dict]:
    snapshots = [read_gzip_json(path) for path in raw_snapshot_paths(market)]
    if dataset_id is not None:
        snapshots = [
            snapshot for snapshot in snapshots
            if snapshot.get("datasetId") == dataset_id
        ]
    return sorted(snapshots, key=lambda x: x["snapshotDate"])


def rebuild_monthly_research(market: str = "us") -> dict:
    snapshots = load_all_raw(market)
    dates = [x["snapshotDate"] for x in snapshots]
    market_sessions = sepa_us_sessions() if market.lower() == "us" else dates
    confirmed = set(confirmed_monthly_cohort_dates(dates, market_sessions))
    cohorts = []

    for i, current in enumerate(snapshots):
        if current["snapshotDate"] not in confirmed:
            continue
        derived = derive_snapshot(current, snapshots[:i])
        signal = score_primary_f1(derived)
        cohort = attach_outcomes(
            signal,
            snapshots,
            i,
            market_sessions=market_sessions,
        )
        cohort["status"] = signal.get("status")
        cohort["manifest"] = signal.get("manifest")
        cohorts.append(cohort)

    latest = snapshots[-1] if snapshots else None
    status = {
        "schemaVersion": 1,
        "market": market.upper(),
        "datasetId": DATASET_ID,
        "rawSnapshots": len(snapshots),
        "firstSnapshotDate": snapshots[0]["snapshotDate"] if snapshots else None,
        "latestSnapshotDate": latest["snapshotDate"] if latest else None,
        "confirmedMonthlyCohorts": len(cohorts),
        "marketSessionsObserved": len(market_sessions),
        "cohorts": cohorts,
    }
    write_replaceable_json(PUBLIC_ROOT / f"monthly_research_{market.lower()}.json", status)
    return status


def research_readiness(market: str = "us") -> dict:
    snapshots = load_all_raw(market)
    if not snapshots:
        return {
            "status": "NO_DATA",
            "datasetId": DATASET_ID,
            "rawSnapshots": 0,
            "firstSnapshotDate": None,
            "latestSnapshotDate": None,
        }
    first = date.fromisoformat(snapshots[0]["snapshotDate"])
    latest = date.fromisoformat(snapshots[-1]["snapshotDate"])
    calendar_span = (latest - first).days
    return {
        "status": "COLLECTING",
        "datasetId": DATASET_ID,
        "rawSnapshots": len(snapshots),
        "firstSnapshotDate": first.isoformat(),
        "latestSnapshotDate": latest.isoformat(),
        "calendarSpanDays": calendar_span,
        "R7PotentiallyAvailable": calendar_span >= 5,
        "R30PotentiallyAvailable": calendar_span >= 25,
        "R60PotentiallyAvailable": calendar_span >= 53,
        "R90PotentiallyAvailable": calendar_span >= 80,
    }


def publish_research_status(market: str = "us") -> dict:
    result = research_readiness(market)
    write_replaceable_json(PUBLIC_ROOT / f"research_status_{market.lower()}.json", result)
    return result
