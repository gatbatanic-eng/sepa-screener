from __future__ import annotations

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


def forward_outcome(
    snapshots: list[dict],
    start_index: int,
    ticker: str,
    horizon: int,
) -> dict:
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    if not (0 <= start_index < len(snapshots)):
        raise IndexError("start_index outside snapshot range")

    available = len(snapshots) - start_index - 1
    base = {
        "status": "pending",
        "horizonSessions": horizon,
        "observedSessions": min(max(available, 0), horizon),
    }
    if available < horizon:
        return base

    start = snapshots[start_index]
    target_index = start_index + horizon
    target = snapshots[target_index]
    p0, p1 = _close(start, ticker), _close(target, ticker)
    b0, b1 = _benchmark(start), _benchmark(target)
    base.update(
        status="unavailable",
        targetDate=target.get("snapshotDate"),
    )
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
) -> dict:
    rows = []
    for source in signal.get("rows", []):
        row = dict(source)
        ticker = row.get("ticker")
        row["outcomes"] = {
            str(h): forward_outcome(snapshots, start_index, ticker, int(h))
            for h in horizons
        }
        rows.append(row)
    return {
        "schemaVersion": 1,
        "researchId": signal.get("researchId"),
        "researchDefinitionHash": signal.get("researchDefinitionHash"),
        "snapshotDate": signal.get("snapshotDate"),
        "market": signal.get("market"),
        "signalFiscalSelector": signal.get("signalFiscalSelector"),
        "rows": rows,
    }
