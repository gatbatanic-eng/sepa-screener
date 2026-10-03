"""계좌 곡선·거래에서 성과 요약. 표본이 MIN_CLOSED 미만이면 '표본 부족'으로 표시하고 순위·도태 판단에 쓰지 않는다."""
from __future__ import annotations

import pandas as pd

from . import config


def max_drawdown(equity: list[float]) -> float:
    peak, worst = float("-inf"), 0.0
    for e in equity:
        peak = max(peak, e)
        worst = min(worst, e / peak - 1)
    return round(worst * 100, 3)


def bench_return(series: pd.Series | None, start: str, end: str) -> float | None:
    if series is None or series.empty:
        return None
    s = series[(series.index >= start) & (series.index <= end)]
    return round((float(s.iloc[-1]) / float(s.iloc[0]) - 1) * 100, 3) if len(s) >= 2 else None


def summarize(result: dict, bench: dict[str, pd.Series]) -> dict:
    curve, trades = result["curve"], result["trades"]
    if not curve:
        return {"status": "NO_DATA"}
    eq = [c["equity"] for c in curve]
    start, end = curve[0]["date"], curve[-1]["date"]
    ret = round((eq[-1] / config.INITIAL_CAPITAL - 1) * 100, 3)
    rets = [t["returnPct"] for t in trades]
    benches = {name: bench_return(s, start, end) for name, s in bench.items()}
    vals = [v for v in benches.values() if v is not None]
    ref = round(sum(vals) / len(vals), 3) if vals else None
    return {"status": "OK" if len(trades) >= config.MIN_CLOSED else "INSUFFICIENT_SAMPLE",
            "from": start, "to": end, "returnPct": ret, "maxDrawdownPct": max_drawdown(eq),
            "benchmarkPct": benches, "referencePct": ref, "excessPct": None if ref is None or not (trades or result["open"]) else round(ret - ref, 3),
            "closed": len(trades), "open": len(result["open"]),
            "winRatePct": round(100 * sum(r > 0 for r in rets) / len(rets), 1) if rets else None,
            "avgTradePct": round(sum(rets) / len(rets), 3) if rets else None,
            "avgSessions": round(sum(t["sessions"] for t in trades) / len(trades), 1) if trades else None,
            "stopShare": round(100 * sum(t["reason"] == "STOP" for t in trades) / len(trades), 1) if trades else None,
            "avgExposurePct": round(100 * sum(c["positions"] for c in curve) / len(curve) / config.MAX_POSITIONS, 1),
            "skipped": len(result["skipped"])}
