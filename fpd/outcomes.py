from __future__ import annotations

from datetime import date
from typing import Iterable


DEFAULT_HORIZONS = (65, 130, 252, 504)


def _positive(value) -> float | None:
    try:
        out = float(value)
        return out if out > 0 else None
    except (TypeError, ValueError):
        return None


def _close(snapshot: dict, ticker: str) -> float | None:
    return _positive(snapshot.get("universe", {}).get(ticker, {}).get("close"))


def _benchmark(snapshot: dict) -> float | None:
    return _positive(snapshot.get("benchmark", {}).get("close"))


def _session_dates(values: Iterable[str]) -> list[str]:
    return sorted({date.fromisoformat(str(value)).isoformat() for value in values})


def forward_outcome(
    snapshots: list[dict],
    start_index: int,
    ticker: str,
    horizon: int,
    market_sessions: Iterable[str] | None = None,
) -> dict:
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    if not (0 <= start_index < len(snapshots)):
        raise IndexError("start_index outside snapshot range")

    start = snapshots[start_index]

    # Preferred path: count actual market sessions, not collected FPD files.
    if market_sessions is not None:
        sessions = _session_dates(market_sessions)
        start_date = str(start.get("snapshotDate"))
        if start_date not in sessions:
            return {
                "status": "unavailable",
                "horizonSessions": horizon,
                "observedSessions": 0,
                "reason": "START_SESSION_NOT_IN_MARKET_CALENDAR",
            }

        start_pos = sessions.index(start_date)
        available_sessions = max(0, len(sessions) - start_pos - 1)
        base = {
            "status": "pending",
            "horizonSessions": horizon,
            "observedSessions": min(available_sessions, horizon),
        }
        if available_sessions < horizon:
            return base

        target_date = sessions[start_pos + horizon]
        base.update(status="unavailable", targetDate=target_date)
        by_date = {str(item.get("snapshotDate")): item for item in snapshots}
        target = by_date.get(target_date)
        if target is None:
            base["reason"] = "RAW_SNAPSHOT_MISSING_AT_TARGET"
            return base

        p0, p1 = _close(start, ticker), _close(target, ticker)
        b0, b1 = _benchmark(start), _benchmark(target)
        if any(v is None for v in (p0, p1, b0, b1)):
            base["reason"] = "PRICE_OR_BENCHMARK_MISSING"
            return base

        path_dates = sessions[start_pos + 1: start_pos + horizon + 1]
        path_prices = []
        for session in path_dates:
            snap = by_date.get(session)
            path_prices.append(_close(snap, ticker) if snap is not None else None)
        complete_path = all(value is not None for value in path_prices)
        excursions = [(value / p0 - 1.0) for value in path_prices] if complete_path else []

        ret = p1 / p0 - 1.0
        benchmark_ret = b1 / b0 - 1.0
        base.update(
            status="complete",
            returnRaw=ret,
            benchmarkReturnRaw=benchmark_ret,
            excessReturnRaw=ret - benchmark_ret,
            maxUpRaw=max([0.0, *excursions]) if complete_path else None,
            maxDownRaw=min([0.0, *excursions]) if complete_path else None,
            pathStatus="complete" if complete_path else "missing",
            baselineClose=p0,
            targetClose=p1,
            baselineBenchmark=b0,
            targetBenchmark=b1,
        )
        return base

    # Compatibility/testing fallback: every snapshot is assumed to be one session.
    available = len(snapshots) - start_index - 1
    base = {
        "status": "pending",
        "horizonSessions": horizon,
        "observedSessions": min(max(available, 0), horizon),
    }
    if available < horizon:
        return base

    target_index = start_index + horizon
    target = snapshots[target_index]
    p0, p1 = _close(start, ticker), _close(target, ticker)
    b0, b1 = _benchmark(start), _benchmark(target)
    base.update(status="unavailable", targetDate=target.get("snapshotDate"))
    if any(v is None for v in (p0, p1, b0, b1)):
        base["reason"] = "PRICE_OR_BENCHMARK_MISSING"
        return base

    window = snapshots[start_index + 1: target_index + 1]
    prices = [_close(snapshot, ticker) for snapshot in window]
    complete_path = all(value is not None for value in prices)
    excursions = [(value / p0 - 1.0) for value in prices] if complete_path else []
    ret = p1 / p0 - 1.0
    benchmark_ret = b1 / b0 - 1.0
    base.update(
        status="complete",
        returnRaw=ret,
        benchmarkReturnRaw=benchmark_ret,
        excessReturnRaw=ret - benchmark_ret,
        maxUpRaw=max([0.0, *excursions]) if complete_path else None,
        maxDownRaw=min([0.0, *excursions]) if complete_path else None,
        pathStatus="complete" if complete_path else "missing",
        baselineClose=p0,
        targetClose=p1,
        baselineBenchmark=b0,
        targetBenchmark=b1,
    )
    return base


def attach_outcomes(
    signal: dict,
    snapshots: list[dict],
    start_index: int,
    horizons: Iterable[int] = DEFAULT_HORIZONS,
    market_sessions: Iterable[str] | None = None,
) -> dict:
    rows = []
    for source in signal.get("rows", []):
        row = dict(source)
        ticker = row.get("ticker")
        row["outcomes"] = {
            str(h): forward_outcome(
                snapshots,
                start_index,
                ticker,
                int(h),
                market_sessions=market_sessions,
            )
            for h in horizons
        }
        rows.append(row)
    return {
        "schemaVersion": 1,
        "researchId": signal.get("researchId"),
        "datasetId": signal.get("datasetId"),
        "researchCohort": signal.get("researchCohort"),
        "researchDefinitionHash": signal.get("researchDefinitionHash"),
        "snapshotDate": signal.get("snapshotDate"),
        "market": signal.get("market"),
        "signalFiscalSelector": signal.get("signalFiscalSelector"),
        "rows": rows,
    }
