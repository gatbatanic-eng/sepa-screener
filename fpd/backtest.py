from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Iterable

import numpy as np
import pandas as pd

from .features import percentile_rank


def spearman_ic(signal: Iterable[float | None], forward_return: Iterable[float | None]) -> float | None:
    frame = pd.DataFrame({"signal": list(signal), "ret": list(forward_return)}).dropna()
    if len(frame) < 2 or frame["signal"].nunique() < 2 or frame["ret"].nunique() < 2:
        return None
    return float(frame["signal"].rank(method="average").corr(frame["ret"].rank(method="average")))


def assign_deciles(values: Iterable[float | None]) -> list[int | None]:
    ranks = percentile_rank(values)
    out: list[int | None] = []
    for rank in ranks:
        if rank is None:
            out.append(None)
        else:
            out.append(min(10, int(rank * 10) + 1))
    return out


def decile_summary(signal: Iterable[float | None], forward_return: Iterable[float | None]) -> dict[int, dict]:
    signal, forward_return = list(signal), list(forward_return)
    if len(signal) != len(forward_return):
        raise ValueError("signal and forward_return lengths differ")
    deciles = assign_deciles(signal)
    grouped: dict[int, list[float]] = defaultdict(list)
    for decile, ret in zip(deciles, forward_return):
        if decile is None or ret is None:
            continue
        try:
            value = float(ret)
        except (TypeError, ValueError):
            continue
        if np.isfinite(value):
            grouped[decile].append(value)
    return {
        d: {
            "count": len(grouped.get(d, [])),
            "mean": float(np.mean(grouped[d])) if grouped.get(d) else None,
            "median": float(np.median(grouped[d])) if grouped.get(d) else None,
        }
        for d in range(1, 11)
    }


def decile_monotonicity(summary: dict[int, dict], statistic: str = "median") -> float | None:
    x, y = [], []
    for decile in range(1, 11):
        value = summary.get(decile, {}).get(statistic)
        if value is not None:
            x.append(decile)
            y.append(value)
    return spearman_ic(x, y)


def block_bootstrap_mean_ci(
    values: Iterable[float],
    block_length: int,
    n_resamples: int = 10_000,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict:
    data = np.asarray([float(v) for v in values if v is not None and np.isfinite(float(v))], dtype=float)
    if len(data) == 0:
        return {"mean": None, "lower": None, "upper": None, "n": 0}
    if block_length < 1:
        raise ValueError("block_length must be >= 1")
    rng = np.random.default_rng(seed)
    n = len(data)
    blocks_needed = int(np.ceil(n / block_length))
    samples = np.empty(n_resamples, dtype=float)
    for i in range(n_resamples):
        pieces = []
        for _ in range(blocks_needed):
            start = int(rng.integers(0, n))
            idx = (np.arange(start, start + block_length) % n).astype(int)
            pieces.append(data[idx])
        sample = np.concatenate(pieces)[:n]
        samples[i] = sample.mean()
    return {
        "mean": float(data.mean()),
        "lower": float(np.quantile(samples, alpha / 2)),
        "upper": float(np.quantile(samples, 1 - alpha / 2)),
        "n": int(n),
        "blockLength": int(block_length),
        "resamples": int(n_resamples),
        "seed": int(seed),
    }


def confirmed_monthly_cohort_dates(snapshot_dates: Iterable[str]) -> list[str]:
    """Return month-end observations only after a later month proves the month closed.

    The latest observed month remains unconfirmed, preventing a mid-month run from
    being mislabeled as the primary monthly cohort.
    """
    parsed = sorted({date.fromisoformat(str(value)) for value in snapshot_dates})
    if not parsed:
        return []
    months: dict[tuple[int, int], list[date]] = defaultdict(list)
    for day in parsed:
        months[(day.year, day.month)].append(day)
    ordered = sorted(months)
    if len(ordered) < 2:
        return []
    confirmed = ordered[:-1]
    return [max(months[key]).isoformat() for key in confirmed]
