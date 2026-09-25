from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
US_RESEARCH_STATE = ROOT / "research" / "us.json"


def latest_sepa_us_session(path: Path = US_RESEARCH_STATE) -> date:
    state = json.loads(path.read_text(encoding="utf-8"))
    value = state.get("latestSession")
    if not value:
        raise RuntimeError("SEPA US research state has no latestSession")
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise RuntimeError(f"Invalid SEPA latestSession: {value}") from exc


def benchmark_close_for_session(session_date: date) -> dict:
    """Fetch the exact S&P500 close using the same provider path as SEPA."""
    from screening import US_INDEX_CODE, fetch_price_history

    start = (session_date - timedelta(days=14)).isoformat()
    frame = fetch_price_history(US_INDEX_CODE, start)
    if frame is None or frame.empty or "Close" not in frame:
        raise RuntimeError("S&P500 benchmark history unavailable")

    matches = []
    for idx, value in frame["Close"].items():
        try:
            observed = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
        except (TypeError, ValueError):
            continue
        if observed == session_date:
            try:
                close = float(value)
            except (TypeError, ValueError):
                continue
            if close > 0:
                matches.append(close)

    if not matches:
        raise RuntimeError(f"No exact S&P500 benchmark close for {session_date.isoformat()}")

    return {
        "symbol": US_INDEX_CODE,
        "priceAsOf": session_date.isoformat(),
        "close": matches[-1],
    }
