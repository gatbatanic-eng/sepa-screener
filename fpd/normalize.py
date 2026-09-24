from __future__ import annotations

import math
from datetime import date
from typing import Any


def finite(value: Any) -> float | None:
    try:
        if value is None or isinstance(value, bool):
            return None
        out = float(value)
        return out if math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def nonnegative_int(value: Any) -> int | None:
    out = finite(value)
    if out is None or out < 0:
        return None
    return int(round(out))


def first(row: dict, *names: str):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    return None


def iso_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return None


def normalize_fmp_estimate(symbol: str, row: dict) -> dict:
    """Map FMP stable analyst-estimates payloads into the PIT schema.

    Aliases are intentional.  FMP has used slightly different field labels
    across API generations; unknown schemas must be rejected by quality checks,
    not silently coerced.
    """
    period_end = iso_date(first(row, "date", "periodEnd", "fiscalDateEnding"))
    eps_avg = finite(first(row, "estimatedEpsAvg", "estimatedEPSAvg", "epsAvg"))
    eps_low = finite(first(row, "estimatedEpsLow", "estimatedEPSLow", "epsLow"))
    eps_high = finite(first(row, "estimatedEpsHigh", "estimatedEPSHigh", "epsHigh"))
    eps_analysts = nonnegative_int(first(
        row,
        "numberAnalystEstimatedEps",
        "numberAnalystsEstimatedEps",
        "numberOfAnalystsEstimatedEps",
        "epsAnalystCount",
    ))
    revenue_avg = finite(first(row, "estimatedRevenueAvg", "revenueAvg"))
    revenue_low = finite(first(row, "estimatedRevenueLow", "revenueLow"))
    revenue_high = finite(first(row, "estimatedRevenueHigh", "revenueHigh"))
    revenue_analysts = nonnegative_int(first(
        row,
        "numberAnalystEstimatedRevenue",
        "numberAnalystsEstimatedRevenue",
        "numberOfAnalystsEstimatedRevenue",
        "revenueAnalystCount",
    ))
    return {
        "ticker": symbol,
        "periodType": "FY",
        "periodEnd": period_end,
        "eps": {
            "avg": eps_avg,
            "low": eps_low,
            "high": eps_high,
            "analysts": eps_analysts,
        },
        "revenue": {
            "avg": revenue_avg,
            "low": revenue_low,
            "high": revenue_high,
            "analysts": revenue_analysts,
        },
    }
