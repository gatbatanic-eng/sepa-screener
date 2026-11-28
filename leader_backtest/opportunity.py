"""Opportunity detection V1. Research-only; does not modify the live BUY engine.

Input: panel with one row per (date, code), chronological OHLCV features from
leader_backtest.rules.features plus cross-sectional rs percentile.
No forward return is consulted when classifying an opportunity.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

RULE_VERSION = "opportunity_v1_2026-11-08"
REQUIRED = ("date", "code", "ok", "trend", "rs", "risk", "rsi", "pivDist", "volRatio", "pullback")


def classify(panel: pd.DataFrame) -> pd.DataFrame:
    """Return independent discovery/entry decisions, without lookahead.

    OPPORTUNITY is a broad watchlist, not a buy recommendation.
    ENTRY is deliberately conservative and requires a valid opportunity.
    WAIT covers discovered opportunities without a qualifying entry.
    REJECT covers everything else, including insufficient history.
    """
    missing = sorted(set(REQUIRED) - set(panel.columns))
    if missing:
        raise ValueError(f"missing required columns: {missing}")
    if panel.duplicated(["date", "code"]).any():
        raise ValueError("duplicate date/code rows")
    p = panel.copy()
    ok = p["ok"].fillna(False).astype(bool)
    rs = pd.to_numeric(p["rs"], errors="coerce")
    risk = pd.to_numeric(p["risk"], errors="coerce")
    rsi = pd.to_numeric(p["rsi"], errors="coerce")
    dist = pd.to_numeric(p["pivDist"], errors="coerce")
    vol = pd.to_numeric(p["volRatio"], errors="coerce")
    trend = p["trend"].fillna(False).astype(bool)
    pb = p["pullback"].fillna(False).astype(bool)

    # Predeclared, intentionally broad discovery: trend leaders, relative
    # strength leaders, or volume-backed pivot attempts.
    opportunity = ok & (
        (trend & (rs >= 70))
        | (rs >= 90)
        | ((rs >= 70) & (dist > 0) & (dist <= 5) & (vol >= 1.4))
    )
    # Entry remains distinct. Reuses pre-existing V1 risk/chase thresholds;
    # this is a baseline, not a claim that these thresholds are optimal.
    entry = opportunity & trend & (risk > 0) & (risk <= 8) & (rsi < 75) & (dist < 5)
    p["opportunity"] = opportunity
    p["entry"] = entry
    p["decision"] = np.select(
        [entry, opportunity], ["ENTRY", "WAIT"], default="REJECT"
    )
    p["rule_version"] = RULE_VERSION
    return p


def evaluate(signals: pd.DataFrame, close: pd.DataFrame, horizon: int = 20,
             threshold: float = 0.15, max_drawdown: float = -0.08) -> dict:
    """Score historical discovery without using outcomes to select signals.

    close: date-indexed, code-columned close matrix. Signal at t enters at
    next available common trading date t+1 close, exits t+1+h close.
    GOOD means final gain >= threshold and no intermediate close-to-entry
    drawdown below max_drawdown. Unmatured observations are excluded.
    Overlapping signal-days are observations, not independent trades.
    """
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    if close.index.has_duplicates or close.columns.has_duplicates:
        raise ValueError("duplicate close dates or codes")
    if not close.index.is_monotonic_increasing:
        raise ValueError("close dates must be sorted")
    rows = []
    loc = {d: i for i, d in enumerate(close.index)}
    for s in signals.itertuples(index=False):
        i = loc.get(s.date)
        if i is None or i + horizon + 1 >= len(close) or s.code not in close.columns:
            continue
        path = close[s.code].iloc[i + 1:i + horizon + 2].to_numpy(dtype=float)
        if len(path) != horizon + 1 or not np.isfinite(path).all() or path[0] <= 0:
            continue
        gain = path[-1] / path[0] - 1
        dd = path[1:].min() / path[0] - 1
        rows.append({"date": s.date, "code": s.code, "opportunity": bool(s.opportunity),
                     "entry": bool(s.entry), "decision": s.decision,
                     "return": float(gain), "good": bool(gain >= threshold and dd > max_drawdown)})
    if not rows:
        return {"matured": 0, "good": 0, "opportunity_recall": None,
                "opportunity_precision": None, "entry_precision": None}
    out = pd.DataFrame(rows)
    good = out["good"]
    opp = out["opportunity"]
    ent = out["entry"]
    return {"matured": len(out), "good": int(good.sum()),
            "opportunity_recall": float((good & opp).sum() / good.sum()) if good.any() else None,
            "opportunity_precision": float((good & opp).sum() / opp.sum()) if opp.any() else None,
            "entry_precision": float((good & ent).sum() / ent.sum()) if ent.any() else None}
