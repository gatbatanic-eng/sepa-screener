from __future__ import annotations

import math
from datetime import date
from statistics import median
from typing import Iterable


def pct_revision(current: float | None, previous: float | None, *, require_positive_current: bool = True) -> float | None:
    """Return raw percentage revision as a ratio (0.05 == +5%).

    EPS uses the default positive-current rule. Revenue may use the same rule;
    no epsilon or denominator floor is introduced.
    """
    if current is None or previous is None:
        return None
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (current, previous)):
        return None
    if previous <= 0:
        return None
    if require_positive_current and current <= 0:
        return None
    return current / previous - 1.0


def closest_observation(
    history: list[dict],
    current_date: date,
    period_end: str,
    target_days: int,
    tolerance_days: int,
) -> dict | None:
    """Match only the same fiscal period to the closest allowed calendar lag."""
    target = current_date.toordinal() - target_days
    candidates: list[tuple[int, int, dict]] = []
    for item in history:
        if item.get("periodEnd") != period_end:
            continue
        try:
            observed = date.fromisoformat(str(item["snapshotDate"]))
        except (KeyError, ValueError):
            continue
        lag = current_date.toordinal() - observed.toordinal()
        if lag <= 0:
            continue
        distance = abs(observed.toordinal() - target)
        if distance <= tolerance_days:
            candidates.append((distance, -observed.toordinal(), item))
    return min(candidates, default=(0, 0, None))[2]


def dispersion(avg: float | None, low: float | None, high: float | None) -> float | None:
    if avg is None or low is None or high is None:
        return None
    if not all(isinstance(v, (int, float)) and math.isfinite(v) for v in (avg, low, high)):
        return None
    if avg == 0:
        return None
    return (high - low) / abs(avg)


def coverage_change(now: int | None, previous: int | None) -> float | None:
    if now is None or previous is None or now < 0 or previous < 0:
        return None
    return (now - previous) / max(previous, 1)


def robust_z(values: Iterable[float | None]) -> list[float | None]:
    values = list(values)
    valid = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
    if not valid:
        return [None] * len(values)
    center = median(valid)
    mad = median(abs(v - center) for v in valid)
    scale = 1.4826 * mad
    if scale == 0:
        return [0.0 if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None for v in values]
    return [
        (float(v) - center) / scale
        if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        else None
        for v in values
    ]


def percentile_rank(values: Iterable[float | None]) -> list[float | None]:
    """Average-tie ranks mapped to (0, 1) with (rank - .5) / N."""
    values = list(values)
    indexed = [(i, float(v)) for i, v in enumerate(values)
               if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)]
    n = len(indexed)
    out: list[float | None] = [None] * len(values)
    if not n:
        return out
    ordered = sorted(indexed, key=lambda pair: pair[1])
    pos = 0
    while pos < n:
        end = pos + 1
        while end < n and ordered[end][1] == ordered[pos][1]:
            end += 1
        average_rank = ((pos + 1) + end) / 2.0
        pct = (average_rank - 0.5) / n
        for j in range(pos, end):
            out[ordered[j][0]] = pct
        pos = end
    return out
