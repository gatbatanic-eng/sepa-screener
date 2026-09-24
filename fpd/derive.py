from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

from .config import load_research_definition, research_definition_hash
from .features import closest_observation, coverage_change, dispersion, pct_revision
from .storage import read_gzip_json, write_replaceable_json

ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "research" / "fpd" / "raw"
DERIVED_ROOT = ROOT / "research" / "fpd" / "derived"
PUBLIC_ROOT = ROOT / "docs" / "data" / "fpd"


def raw_snapshot_paths(market: str = "us") -> list[Path]:
    return sorted((RAW_ROOT / market.lower()).glob("*/*/*.json.gz"))


def load_prior_snapshots(current_date: date, market: str = "us") -> list[dict]:
    out = []
    for path in raw_snapshot_paths(market):
        snapshot = read_gzip_json(path)
        try:
            observed = date.fromisoformat(snapshot["snapshotDate"])
        except (KeyError, ValueError):
            continue
        if observed < current_date:
            out.append(snapshot)
    return out


def _estimate_history(prior_snapshots: Iterable[dict], ticker: str, period_end: str) -> list[dict]:
    history = []
    for snapshot in prior_snapshots:
        for row in snapshot.get("observations", {}).get(ticker, []):
            if row.get("periodEnd") != period_end:
                continue
            history.append({
                "snapshotDate": snapshot.get("snapshotDate"),
                "periodEnd": period_end,
                "eps": row.get("eps", {}),
                "revenue": row.get("revenue", {}),
            })
    return history


def _feature_for_horizon(current: dict, history: list[dict], current_date: date, spec: dict) -> dict:
    target = int(spec["targetCalendarDays"])
    tolerance = int(spec["toleranceDays"])
    prior = closest_observation(history, current_date, current["periodEnd"], target, tolerance)
    base = {
        "requestedDays": target,
        "toleranceDays": tolerance,
        "previousSnapshotDate": prior.get("snapshotDate") if prior else None,
        "actualLagDays": None,
        "epsRevisionRaw": None,
        "revenueRevisionRaw": None,
        "epsCoverageChange": None,
        "revenueCoverageChange": None,
    }
    if prior is None:
        return base
    previous_date = date.fromisoformat(prior["snapshotDate"])
    base["actualLagDays"] = (current_date - previous_date).days

    eps_now, eps_prev = current.get("eps", {}), prior.get("eps", {})
    rev_now, rev_prev = current.get("revenue", {}), prior.get("revenue", {})
    base["epsRevisionRaw"] = pct_revision(eps_now.get("avg"), eps_prev.get("avg"))
    base["revenueRevisionRaw"] = pct_revision(rev_now.get("avg"), rev_prev.get("avg"))
    base["epsCoverageChange"] = coverage_change(eps_now.get("analysts"), eps_prev.get("analysts"))
    base["revenueCoverageChange"] = coverage_change(rev_now.get("analysts"), rev_prev.get("analysts"))
    return base


def derive_snapshot(current_snapshot: dict, prior_snapshots: list[dict]) -> dict:
    definition = load_research_definition()
    current_date = date.fromisoformat(current_snapshot["snapshotDate"])
    lookbacks = definition["lookbacks"]
    symbols: dict[str, list[dict]] = {}

    for ticker, rows in current_snapshot.get("observations", {}).items():
        derived_rows = []
        for row in rows:
            period_end = row.get("periodEnd")
            history = _estimate_history(prior_snapshots, ticker, period_end)
            eps, revenue = row.get("eps", {}), row.get("revenue", {})
            item = {
                "ticker": ticker,
                "periodType": row.get("periodType"),
                "periodEnd": period_end,
                "epsCurrent": eps.get("avg"),
                "revenueCurrent": revenue.get("avg"),
                "epsDispersion": dispersion(eps.get("avg"), eps.get("low"), eps.get("high")),
                "revenueDispersion": dispersion(revenue.get("avg"), revenue.get("low"), revenue.get("high")),
                "epsAnalysts": eps.get("analysts"),
                "revenueAnalysts": revenue.get("analysts"),
                "lookbacks": {},
            }
            for label, spec in lookbacks.items():
                item["lookbacks"][label] = _feature_for_horizon(row, history, current_date, spec)
            derived_rows.append(item)
        symbols[ticker] = derived_rows

    return {
        "schemaVersion": 1,
        "researchId": current_snapshot["researchId"],
        "researchDefinitionHash": research_definition_hash(definition),
        "snapshotDate": current_snapshot["snapshotDate"],
        "market": current_snapshot["market"],
        "sourceRawHash": current_snapshot.get("providerNormalizedPayloadHash"),
        "symbols": symbols,
        "notes": {
            "crossSectionalNormalization": "NOT_YET_APPLIED",
            "fpdSignal": "NOT_YET_APPLIED",
            "priceVelocity": "NOT_YET_APPLIED"
        }
    }


def derive_latest_us() -> dict:
    paths = raw_snapshot_paths("us")
    if not paths:
        raise RuntimeError("No US FPD raw snapshots available")
    current = read_gzip_json(paths[-1])
    current_date = date.fromisoformat(current["snapshotDate"])
    prior = load_prior_snapshots(current_date, "us")
    result = derive_snapshot(current, prior)
    out = DERIVED_ROOT / "us" / f"{current_date.year:04d}" / f"{current_date.month:02d}" / f"{current_date.isoformat()}.json"
    write_replaceable_json(out, result)
    write_replaceable_json(PUBLIC_ROOT / "derived_latest_us.json", result)
    return result
