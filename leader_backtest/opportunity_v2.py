"""Predeclared Opportunity V2 candidate filters. Research-only, no forward data."""
from __future__ import annotations
import pandas as pd


def candidate_filters(panel: pd.DataFrame) -> pd.DataFrame:
    required = {"opportunity", "trend", "rs", "rsi", "risk", "pivDist", "volRatio", "pullback"}
    if missing := required - set(panel.columns):
        raise ValueError(f"missing columns: {sorted(missing)}")
    p = panel
    opp = p["opportunity"].fillna(False).astype(bool)
    rs = pd.to_numeric(p["rs"], errors="coerce")
    rsi = pd.to_numeric(p["rsi"], errors="coerce")
    risk = pd.to_numeric(p["risk"], errors="coerce")
    dist = pd.to_numeric(p["pivDist"], errors="coerce")
    vol = pd.to_numeric(p["volRatio"], errors="coerce")
    trend = p["trend"].fillna(False).astype(bool)
    pullback = p["pullback"].fillna(False).astype(bool)
    result = pd.DataFrame(index=p.index)
    # Independent, interpretable hypotheses; no thresholds tuned on outcomes.
    result["v2_leader"] = opp & trend & (rs >= 90) & (rsi < 75)
    result["v2_breakout"] = opp & trend & (rs >= 80) & dist.between(0, 5, inclusive="both") & (vol >= 1.4)
    result["v2_pullback"] = opp & trend & (rs >= 80) & pullback & (rsi < 65)
    result["v2_controlled_risk"] = opp & trend & (rs >= 80) & risk.between(0, 8, inclusive="right") & (rsi < 70)
    return result
