"""Robustness of benchmark-relative opportunity episodes, research only."""
from __future__ import annotations
import numpy as np
import pandas as pd


def robustness(observations: pd.DataFrame, horizon: int = 20, seed: int = 20261109) -> dict:
    required = {"date", "code", "return20", "benchmark20", "v1", "opportunity", "entry"}
    if missing := required - set(observations.columns):
        raise ValueError(f"missing columns: {sorted(missing)}")
    if horizon < 1:
        raise ValueError("horizon must be positive")
    df = observations.sort_values(["code", "date"])
    if df.duplicated(["code", "date"]).any():
        raise ValueError("duplicate code/date")
    output = {}
    for signal in ("v1", "opportunity", "entry"):
        selected = []
        for _, group in df.groupby("code", sort=False):
            last = -horizon - 1
            for i, row in enumerate(group.itertuples(index=False)):
                if bool(getattr(row, signal)) and i - last > horizon:
                    if pd.notna(row.benchmark20):
                        selected.append((pd.Timestamp(row.date), float(row.return20) - float(row.benchmark20)))
                    last = i
        if not selected:
            output[signal] = {"episodes": 0}
            continue
        p = pd.DataFrame(selected, columns=["date", "excess"])
        x = p.excess.to_numpy(float)
        low, high = np.quantile(x, [.05, .95])
        winsor = np.clip(x, low, high)
        trimmed = x[(x >= low) & (x <= high)]
        daily = p.groupby("date", sort=True).excess.mean()
        # Circular moving-block bootstrap on the chronological daily series.
        # A 20-session block is a sensitivity estimate, not a proof of independence.
        a = daily.to_numpy(float)
        ci = None
        if len(a) >= 40:
            rng = np.random.default_rng(seed)
            block = min(horizon, len(a))
            k = int(np.ceil(len(a) / block))
            means = np.empty(1000)
            for b in range(1000):
                starts = rng.integers(0, len(a), size=k)
                idx = (starts[:, None] + np.arange(block)[None, :]) % len(a)
                means[b] = a[idx.ravel()[:len(a)]].mean()
            ci = [round(100 * float(v), 2) for v in np.quantile(means, [.025, .975])]
        output[signal] = {
            "episodes": len(x),
            "median_excess_pct": round(100 * float(np.median(x)), 2),
            "winsor_5pct_mean_excess_pct": round(100 * float(winsor.mean()), 2),
            "trim_5pct_mean_excess_pct": round(100 * float(trimmed.mean()), 2),
            "positive_excess_rate_pct": round(100 * float(np.mean(x > 0)), 2),
            "signal_days": len(a),
            "equal_day_mean_excess_pct": round(100 * float(a.mean()), 2),
            "equal_day_block_bootstrap_ci95_pct": ci,
        }
    return {"groups": output, "limitations": [
        "Winsorization and trimming use full-sample distribution for retrospective sensitivity only",
        "Block bootstrap uses equal-day means; cross-sectional and serial dependence remain",
        "Not an executable portfolio; no survivorship-bias correction or next-open fills"]}
