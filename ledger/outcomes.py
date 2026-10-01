"""신호 후 N거래일 수익률·지수 대비·최대 하락. 끝난(complete) 값은 고정해서 다시 계산하지 않는다."""
from __future__ import annotations

import bisect
import datetime as dt

import pandas as pd

from . import config


def _snap(sessions: list[str], date: str) -> int | None:
    """date 이하 마지막 거래일의 위치."""
    i = bisect.bisect_right(sessions, date) - 1
    return i if i >= 0 else None


def compute_one(signal: dict, series: pd.Series | None, bench: pd.Series | None, horizons=config.HORIZONS) -> dict[int, dict]:
    """한 신호의 호라이즌별 결과. 지수 달력(거래일)을 기준으로 N거래일 뒤를 잡는다."""
    if bench is None or bench.empty:
        return {h: {"status": "unavailable", "reason": "NO_BENCHMARK"} for h in horizons}
    sessions = list(bench.index)
    i0 = _snap(sessions, signal["date"])
    if i0 is None:
        return {h: {"status": "unavailable", "reason": "NO_BENCHMARK_AT_SIGNAL"} for h in horizons}
    out: dict[int, dict] = {}
    for h in horizons:
        if i0 + h >= len(sessions):
            out[h] = {"status": "pending", "observedSessions": len(sessions) - 1 - i0}
            continue
        if series is None or series.empty:
            out[h] = {"status": "unavailable", "reason": "NO_PRICE_DATA"}
            continue
        d0, dh = sessions[i0], sessions[i0 + h]
        if d0 not in series.index or dh not in series.index:
            out[h] = {"status": "unavailable", "reason": "NO_PRICE_AT_ENTRY" if d0 not in series.index else "NO_PRICE_AT_TARGET"}
            continue
        p0, ph = float(series[d0]), float(series[dh])
        path = series[(series.index > d0) & (series.index <= dh)]
        b0, bh = float(bench[d0]), float(bench[dh])
        ret, bret = (ph / p0 - 1) * 100, (bh / b0 - 1) * 100
        out[h] = {"status": "complete", "returnPct": round(ret, 4), "benchmarkPct": round(bret, 4),
                  "excessPct": round(ret - bret, 4), "maxDownPct": round(min(0.0, float(path.min()) / p0 * 100 - 100), 4),
                  "entryDate": d0, "targetDate": dh}
    return out


def needs_outcomes(frozen: dict | None, horizons=config.HORIZONS) -> bool:
    return not frozen or any(frozen.get(str(h), {}).get("status") != "complete" for h in horizons)


def merge(frozen: dict | None, fresh: dict[int, dict]) -> dict:
    """complete는 한 번 정해지면 유지. 나머지는 최신 값으로 갱신."""
    merged = dict(frozen or {})
    for h, o in fresh.items():
        if merged.get(str(h), {}).get("status") != "complete":
            merged[str(h)] = o
    return merged
