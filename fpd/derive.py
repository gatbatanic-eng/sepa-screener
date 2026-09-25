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


def _snapshot_by_date(prior_snapshots: Iterable[dict]) -> dict[str, dict]:
    return {
        str(snapshot.get("snapshotDate")): snapshot
        for snapshot in prior_snapshots
        if snapshot.get("snapshotDate")
    }


def _positive(value) -> float | None:
    try:
        out = float(value)
        return out if out > 0 else None
    except (TypeError, ValueError):
        return None


def _simple_return(current, previous) -> float | None:
    current, previous = _positive(current), _positive(previous)
    if current is None or previous is None:
        return None
    return current / previous - 1.0


def _events_inside(events: list[dict], previous_date: date, current_date: date) -> list[dict]:
    out = []
    for event in events:
        try:
            event_date = date.fromisoformat(str(event.get("date"))[:10])
        except ValueError:
            continue
        if previous_date < event_date <= current_date:
            out.append(event)
    return out


def _feature_for_horizon(
    ticker: str,
    current: dict,
    history: list[dict],
    current_snapshot: dict,
    prior_by_date: dict[str, dict],
    current_date: date,
    spec: dict,
) -> dict:
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
        "epsPrevious": None,
        "revenuePrevious": None,
        "pricePreviousClose": None,
        "benchmarkPreviousClose": None,
        "priceReturnRaw": None,
        "benchmarkReturnRaw": None,
        "relativeStrengthRaw": None,
    }
    if prior is None:
        return base

    previous_date = date.fromisoformat(prior["snapshotDate"])
    base["actualLagDays"] = (current_date - previous_date).days

    eps_now, eps_prev = current.get("eps", {}), prior.get("eps", {})
    rev_now, rev_prev = current.get("revenue", {}), prior.get("revenue", {})
    base["epsPrevious"] = eps_prev.get("avg")
    base["revenuePrevious"] = rev_prev.get("avg")
    base["epsRevisionRaw"] = pct_revision(eps_now.get("avg"), eps_prev.get("avg"))
    base["revenueRevisionRaw"] = pct_revision(rev_now.get("avg"), rev_prev.get("avg"))

    split_events = _events_inside(
        current_snapshot.get("events", {}).get("splits", {}).get(ticker, []),
        previous_date,
        current_date,
    )
    earnings_events = _events_inside(
        current_snapshot.get("events", {}).get("earnings", {}).get(ticker, []),
        previous_date,
        current_date,
    )
    base["splitInsideWindow"] = bool(split_events)
    base["splitEvents"] = split_events
    base["earningsInsideWindow"] = bool(earnings_events)
    base["earningsEvents"] = earnings_events
    base["epsCoverageChange"] = coverage_change(eps_now.get("analysts"), eps_prev.get("analysts"))
    base["revenueCoverageChange"] = coverage_change(rev_now.get("analysts"), rev_prev.get("analysts"))

    prior_snapshot = prior_by_date.get(prior["snapshotDate"], {})
    current_close = current_snapshot.get("universe", {}).get(ticker, {}).get("close")
    prior_close = prior_snapshot.get("universe", {}).get(ticker, {}).get("close")
    current_benchmark = current_snapshot.get("benchmark", {}).get("close")
    prior_benchmark = prior_snapshot.get("benchmark", {}).get("close")
    price_return = _simple_return(current_close, prior_close)
    benchmark_return = _simple_return(current_benchmark, prior_benchmark)
    base["pricePreviousClose"] = prior_close
    base["benchmarkPreviousClose"] = prior_benchmark
    base["priceReturnRaw"] = price_return
    base["benchmarkReturnRaw"] = benchmark_return
    if price_return is not None and benchmark_return is not None:
        base["relativeStrengthRaw"] = price_return - benchmark_return

    if base.get("splitInsideWindow"):
        # Do not infer a split adjustment for estimate or stored point-in-time
        # closes. Exclude contaminated EPS/PV/RS while preserving revenue revision.
        base["epsRevisionRaw"] = None
        base["priceReturnRaw"] = None
        base["relativeStrengthRaw"] = None
    return base


def derive_snapshot(current_snapshot: dict, prior_snapshots: list[dict]) -> dict:
    definition = load_research_definition()
    current_date = date.fromisoformat(current_snapshot["snapshotDate"])
    lookbacks = definition["lookbacks"]
    prior_by_date = _snapshot_by_date(prior_snapshots)
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
                item["lookbacks"][label] = _feature_for_horizon(
                    ticker,
                    row,
                    history,
                    current_snapshot,
                    prior_by_date,
                    current_date,
                    spec,
                )

            l30 = item["lookbacks"].get("30D", {})
            l60 = item["lookbacks"].get("60D", {})
            eps30, eps60 = l30.get("epsRevisionRaw"), l60.get("epsRevisionRaw")
            rev30, rev60 = l30.get("revenueRevisionRaw"), l60.get("revenueRevisionRaw")
            eps_previous_leg = pct_revision(l30.get("epsPrevious"), l60.get("epsPrevious"))
            rev_previous_leg = pct_revision(l30.get("revenuePrevious"), l60.get("revenuePrevious"))
            item["acceleration"] = {
                "epsRA_A": None if eps30 is None or eps60 is None else eps30 - eps60 / 2.0,
                "revenueRA_A": None if rev30 is None or rev60 is None else rev30 - rev60 / 2.0,
                "epsPrevious30Leg": eps_previous_leg,
                "revenuePrevious30Leg": rev_previous_leg,
                "epsRA_B": None if eps30 is None or eps_previous_leg is None else eps30 - eps_previous_leg,
                "revenueRA_B": None if rev30 is None or rev_previous_leg is None else rev30 - rev_previous_leg,
            }
            derived_rows.append(item)
        symbols[ticker] = derived_rows

    return {
        "schemaVersion": 1,
        "researchId": current_snapshot["researchId"],
        "datasetId": current_snapshot.get("datasetId"),
        "researchCohort": current_snapshot.get("researchCohort"),
        "researchDefinitionHash": research_definition_hash(definition),
        "snapshotDate": current_snapshot["snapshotDate"],
        "market": current_snapshot["market"],
        "sourceRawHash": current_snapshot.get("providerNormalizedPayloadHash"),
        "benchmark": current_snapshot.get("benchmark"),
        "symbols": symbols,
        "notes": {
            "priceWindowAlignment": "SAME_PRIOR_SNAPSHOT_AS_REVISION",
            "crossSectionalNormalization": "SEPARATE_STEP",
            "fpdSignal": "SEPARATE_STEP"
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
