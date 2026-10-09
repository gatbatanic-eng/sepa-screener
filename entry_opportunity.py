"""Read-only overlay for independent aggressive screener signals.

Never modify SEPA verdicts. Reject stale, mismatched, incomplete and duplicate
research snapshots, and never use forward outcomes to decide what to display.
"""
import json
import math
from pathlib import Path


def read_json(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _positive(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value > 0 else None
    except (TypeError, ValueError):
        return None


def match_current_aggressive(sepa_rows, sepa_feed, aggressive_feed):
    """Map code to a vetted experimental GO/WATCH display, not a BUY."""
    if not isinstance(sepa_feed, dict) or not isinstance(aggressive_feed, dict):
        return {}
    session = sepa_feed.get("latestSession")
    if not isinstance(session, str) or len(session) != 10 or session != aggressive_feed.get("latestSession"):
        return {}
    latest = aggressive_feed.get("latestRows")
    if not isinstance(latest, list):
        return {}
    index = {}
    for r in latest:
        if isinstance(r, dict):
            index.setdefault(str(r.get("code")), []).append(r)
    result = {}
    for row in sepa_rows:
        code = str(row.get("code"))
        candidates = index.get(code, [])
        if row.get("status") != "OK" or len(candidates) != 1:
            continue
        a = candidates[0]
        if (a.get("status") != "OK" or a.get("inUniverse") is not True
                or a.get("priceAsOf") != session
                or a.get("benchmarkAsOf") not in (None, session)
                or a.get("dataFreshness") not in (None, "CURRENT")
                or a.get("marketOk") is not True):
            continue
        close, other = _positive(row.get("close")), _positive(a.get("close"))
        if close is None or other is None or abs(close - other) > max(0.01, close * 0.0001):
            continue
        kind = "GO" if a.get("aggressiveGo") is True else "WATCH" if a.get("aggressiveWatch") is True else None
        pivot = _positive(a.get("breakoutLevel"))
        if kind is None or pivot is None:
            continue
        risk = _positive(a.get("initialRiskPct")) if kind == "GO" else None
        stop = _positive(a.get("referenceStop")) if kind == "GO" else None
        if kind == "GO" and (risk is None or risk > 7 or stop is None or stop >= close
                             or a.get("breakout") is not True or a.get("riskOk") is not True):
            continue
        result[code] = {
            "aggressiveOverlay": kind,
            "aggressiveAsOf": session,
            "aggressivePivot": round(pivot, 4),
            "aggressiveStop": round(stop, 4) if stop is not None else None,
            "aggressiveRiskPct": round(risk, 2) if risk is not None else None,
            "aggressiveSepaConflict": kind == "GO" and row.get("entryVerdict") != "GO",
        }
    return result
