"""Episode-level, non-overlapping diagnostics for opportunity signals.

Select first signal for each stock, suppress subsequent signals for horizon
stock trading sessions. No future prices are used to choose episodes.
"""
from __future__ import annotations
import pandas as pd


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
        out[signal] = {
            "episodes": n,
            "good_episodes": sum(g for g, _ in selected),
            "precision_pct": round(100 * sum(g for g, _ in selected) / n, 2) if n else None,
            "mean_return20_pct": round(100 * sum(ret for _, ret in selected) / n, 2) if n else None,
        }
    return {"cooldown_sessions": horizon, "groups": out,
            "limitations": ["First-signal episodes are not an investable portfolio",
                            "Evaluation excludes immature dates, which can shift episode boundaries",
                            "No trading costs, market benchmark, or delisted constituents"]}
