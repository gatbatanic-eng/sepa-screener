"""Research-only real OHLCV comparison of V1 vs Opportunity Layer V1.

Run: python -m leader_backtest.opportunity_run
Downloads price data; writes a report, never modifies live recommendations.
"""
from __future__ import annotations

import datetime as dt
import json
import os

import numpy as np
import pandas as pd

from .main import ROOT, fetch_ohlcv
from .rules import features, select
from .opportunity import classify
from .episodes import episode_metrics


def _universe(market: str) -> dict[str, str]:
    src = json.loads((ROOT / "docs" / "data" / f"latest_{market}.json").read_text(encoding="utf-8"))
    mapping = {}
    for row in src:
        if row.get("status") != "OK" or not row.get("code"):
            continue
        code = str(row["code"])
        ticker = (code.replace(".", "-") if market == "us"
                  else code.zfill(6) + (".KQ" if row.get("market") == "KOSDAQ" else ".KS"))
        mapping[ticker] = code
    return mapping


def run(market: str, start: str = "2023-01-01", evaluation: str = "2024-01-01",
        horizon: int = 20) -> dict:
    tickers = _universe(market)
    data = fetch_ohlcv(list(tickers), start=start)
    import yfinance as yf
    symbols = {"kr": {"ks": "^KS11", "kq": "^KQ11"}, "us": {"us": "^GSPC"}}[market]
    benchmarks = {}
    for key, symbol in symbols.items():
        raw = yf.download(symbol, start=start, interval="1d", auto_adjust=True, progress=False)
        if raw.empty:
            raise RuntimeError(f"{market}: missing benchmark {symbol}")
        close = raw["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        close.index = pd.DatetimeIndex(close.index).tz_localize(None)
        benchmarks[key] = close.dropna()

    frames = []
    for ticker, bars in data.items():
        f = features(bars)
        f["date"] = f.index
        f["code"] = ticker
        f["close"] = bars["close"]
        frames.append(f.reset_index(drop=True))
    if not frames:
        raise RuntimeError(f"{market}: no OHLCV data downloaded")
    panel = pd.concat(frames, ignore_index=True)
    panel["rs"] = panel.groupby("date")["ret252"].rank(pct=True) * 100
    panel = panel.sort_values(["code", "date"]).reset_index(drop=True)
    old_v1 = select(panel)["V1"]
    classified = classify(panel)
    classified["v1"] = old_v1.to_numpy(bool)

    # Evaluate on each stock's actual daily trading calendar, not a padded
    # calendar; no forward-fill of delisted/missing observations.
    result = []
    for ticker, group in classified.groupby("code", sort=False):
        bars = data[ticker]["close"]
        loc = {d: i for i, d in enumerate(bars.index)}
        for row in group.itertuples(index=False):
            if row.date < pd.Timestamp(evaluation) or not row.ok:
                continue
            i = loc.get(row.date)
            if i is None or i + horizon + 1 >= len(bars):
                continue
            path = bars.iloc[i + 1:i + horizon + 2].to_numpy(float)
            if len(path) != horizon + 1 or not np.isfinite(path).all() or path[0] <= 0:
                continue
            ret = path[-1] / path[0] - 1
            bkey = "us" if market == "us" else ("kq" if ticker.endswith(".KQ") else "ks")
            b = benchmarks[bkey]
            entry_day, exit_day = bars.index[i + 1], bars.index[i + horizon + 1]
            if entry_day not in b.index or exit_day not in b.index:
                continue
            benchmark20 = float(b.loc[exit_day] / b.loc[entry_day] - 1)
            good = ret >= 0.15 and path[1:].min() / path[0] - 1 > -0.08
            result.append({"date": str(row.date.date()), "code": ticker, "good": bool(good),
                           "v1": bool(row.v1), "opportunity": bool(row.opportunity),
                           "entry": bool(row.entry), "return20": float(ret),
                           "benchmark20": benchmark20})
    if not result:
        raise RuntimeError(f"{market}: no matured observations")
    out = pd.DataFrame(result)
    n_good = int(out.good.sum())
    groups = {}
    for name in ("v1", "opportunity", "entry"):
        selected = out[name]
        tp = int((selected & out.good).sum())
        groups[name] = {"signals": int(selected.sum()), "true_positives": tp,
                        "recall_pct": round(100 * tp / n_good, 2) if n_good else None,
                        "precision_pct": round(100 * tp / selected.sum(), 2) if selected.any() else None,
                        "mean_return20_pct": round(float(out.loc[selected, "return20"].mean()) * 100, 2)
                        if selected.any() else None}
    episodes = episode_metrics(out, horizon=horizon)
    return {"market": market, "data_tickers": len(data), "matured_rows": len(out),
            "good_rows": n_good, "period_start": evaluation, "groups": groups, "episodes": episodes,
            "limitations": ["Current-constituent survivorship bias",
                            "Overlapping signal-day observations, not independent trades",
                            "No trading cost, slippage, portfolio simulation, or liquidity constraints",
                            "This is detection diagnostic, not a profitability backtest"]}


def main() -> None:
    start = os.getenv("OPP_FETCH_START", "2023-01-01")
    evaluation = os.getenv("OPP_EVAL_START", "2024-01-01")
    results = {"version": "opportunity_v1_2026-11-08",
               "ran_at": dt.datetime.now(dt.timezone.utc).isoformat(), "markets": {}}
    for market in ("kr", "us"):
        results["markets"][market] = run(market, start, evaluation)
    dest = ROOT / "research" / "leader_backtest" / "opportunity_v1_results.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
