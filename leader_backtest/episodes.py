"""Episode-level, non-overlapping diagnostics for opportunity signals.

Select first signal for each stock, suppress subsequent signals for horizon
stock trading sessions. No future prices are used to choose episodes.
"""
from __future__ import annotations
import pandas as pd
import numpy as np


def episode_metrics(observations: pd.DataFrame, horizon: int = 20) -> dict:
    required = {"date", "code", "good", "return20", "v1", "opportunity", "entry"}
    if missing := required - set(observations.columns):
        raise ValueError(f"missing columns: {sorted(missing)}")
    if horizon < 1:
        raise ValueError("horizon must be positive")
    df = observations.sort_values(["code", "date"]).copy()
    if df.duplicated(["code", "date"]).any():
        raise ValueError("duplicate code/date observations")
    out = {}
    for signal in ("v1", "opportunity", "entry"):
        selected = []
        for _, group in df.groupby("code", sort=False):
            last = -horizon - 1
            for i, row in enumerate(group.itertuples(index=False)):
                if getattr(row, signal) and i - last > horizon:
                    selected.append((bool(row.good), float(row.return20)))
                    last = i
        n = len(selected)
        returns = np.asarray([ret for _, ret in selected], dtype=float)
        out[signal] = {
            "episodes": n,
            "good_episodes": sum(g for g, _ in selected),
            "precision_pct": round(100 * sum(g for g, _ in selected) / n, 2) if n else None,
            "mean_return20_pct": round(100 * float(returns.mean()), 2) if n else None,
            "median_return20_pct": round(100 * float(np.median(returns)), 2) if n else None,
            "p10_return20_pct": round(100 * float(np.percentile(returns, 10)), 2) if n else None,
            "loss_rate_pct": round(100 * float(np.mean(returns < 0)), 2) if n else None,
            "gain_10pct_rate_pct": round(100 * float(np.mean(returns >= .10)), 2) if n else None,
            "net_mean_20bp_cost_pct": round(100 * float(returns.mean() - .002), 2) if n else None,
            "net_mean_50bp_cost_pct": round(100 * float(returns.mean() - .005), 2) if n else None,
        }
    return {"cooldown_sessions": horizon, "groups": out,
            "limitations": ["First-signal episodes are not an investable portfolio",
                            "Evaluation excludes immature dates, which can shift episode boundaries",
                            "20bp/50bp are flat round-trip sensitivity assumptions, not realized execution costs",
                            "No market benchmark, portfolio overlap controls, or delisted constituents"]}
