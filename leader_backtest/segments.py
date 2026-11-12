"""Out-of-sample descriptive diagnostics by year and contemporaneous index path."""
from __future__ import annotations
import numpy as np
import pandas as pd


def segment_episodes(observations: pd.DataFrame, horizon: int = 20) -> dict:
    required = {"date", "code", "benchmark20", "return20", "v1", "opportunity", "entry"}
    if missing := required - set(observations.columns):
        raise ValueError(f"missing: {sorted(missing)}")
    df = observations.sort_values(["code", "date"])
    result = {}
    for signal in ("v1", "opportunity", "entry"):
        chosen = []
        for _, group in df.groupby("code", sort=False):
            last = -horizon - 1
            for i, row in enumerate(group.itertuples(index=False)):
                if bool(getattr(row, signal)) and i - last > horizon:
                    if pd.notna(row.benchmark20):
                        chosen.append({"date": pd.Timestamp(row.date),
                                       "excess": float(row.return20) - float(row.benchmark20),
                                       "benchmark": float(row.benchmark20)})
                    last = i
        if not chosen:
            result[signal] = {"episodes": 0}
            continue
        p = pd.DataFrame(chosen)
        p["year"] = p["date"].dt.year.astype(str)
        p["index_regime"] = np.where(p["benchmark"] > 0, "index_up", "index_flat_or_down")
        def stats(frame):
            return {"episodes": len(frame),
                    "mean_excess_pct": round(100 * float(frame["excess"].mean()), 2),
                    "net_excess_50bp_pct": round(100 * (float(frame["excess"].mean()) - .005), 2),
                    "positive_excess_rate_pct": round(100 * float((frame["excess"] > 0).mean()), 2)}
        daily = p.groupby("date", sort=True)["excess"].mean()
        result[signal] = {"episodes": len(p),
                          "by_year": {str(k): stats(g) for k, g in p.groupby("year")},
                          "by_forward_index_regime": {str(k): stats(g) for k, g in p.groupby("index_regime")},
                          "signal_days": len(daily),
                          "daily_equal_date_mean_excess_pct": round(100 * float(daily.mean()), 2),
                          "daily_positive_excess_rate_pct": round(100 * float((daily > 0).mean()), 2)}
    return {"groups": result, "limitations": [
        "Index regime uses future returns for retrospective diagnosis, not for signal selection",
        "Same-day averaging does not remove serial dependence or establish significance",
        "No portfolio simulation or point-in-time universe"]}
