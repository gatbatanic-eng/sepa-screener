from __future__ import annotations

import math
from datetime import date
from typing import Any


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_estimate(observation: dict) -> list[str]:
    errors: list[str] = []
    ticker = observation.get("ticker")
    if not isinstance(ticker, str) or not ticker.strip():
        errors.append("MISSING_TICKER")
    if observation.get("periodType") != "FY":
        errors.append("INVALID_PERIOD_TYPE")
    try:
        date.fromisoformat(str(observation.get("periodEnd")))
    except (TypeError, ValueError):
        errors.append("INVALID_PERIOD_END")

    usable = False
    for key in ("eps", "revenue"):
        block = observation.get(key)
        if not isinstance(block, dict):
            errors.append(f"MISSING_{key.upper()}_BLOCK")
            continue
        avg, low, high, analysts = (
            block.get("avg"),
            block.get("low"),
            block.get("high"),
            block.get("analysts"),
        )
        if avg is not None:
            if not _finite(avg):
                errors.append(f"INVALID_{key.upper()}_AVG")
            else:
                usable = True
        for label, value in (("LOW", low), ("HIGH", high)):
            if value is not None and not _finite(value):
                errors.append(f"INVALID_{key.upper()}_{label}")
        if analysts is not None and (not isinstance(analysts, int) or analysts < 0):
            errors.append(f"INVALID_{key.upper()}_ANALYST_COUNT")
        if all(_finite(x) for x in (low, avg, high)) and not (low <= avg <= high):
            errors.append(f"INCONSISTENT_{key.upper()}_RANGE")

    if not usable:
        errors.append("NO_USABLE_ESTIMATE")
    return errors


def validate_symbol_rows(rows: list[dict]) -> list[str]:
    errors: list[str] = []
    periods: set[tuple[str, str]] = set()
    for row in rows:
        errors.extend(validate_estimate(row))
        key = (str(row.get("periodType")), str(row.get("periodEnd")))
        if key in periods:
            errors.append("DUPLICATE_FISCAL_PERIOD")
        periods.add(key)
    return sorted(set(errors))
