from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

import pandas as pd
import yfinance as yf


class YahooActionsError(RuntimeError):
    pass


def _as_date(value) -> str | None:
    try:
        if hasattr(value, "date"):
            return value.date().isoformat()
        return date.fromisoformat(str(value)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def parse_split_download(data: pd.DataFrame, symbols: Iterable[str]) -> dict:
    symbols = list(symbols)
    events: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
    covered: dict[str, bool] = {symbol: False for symbol in symbols}

    if data is None or data.empty:
        return {"events": events, "covered": covered}

    if isinstance(data.columns, pd.MultiIndex):
        level0 = set(map(str, data.columns.get_level_values(0)))
        for symbol in symbols:
            if symbol not in level0:
                continue
            frame = data[symbol]
            covered[symbol] = True
            if "Stock Splits" not in frame.columns:
                continue
            series = frame["Stock Splits"].dropna()
            for idx, raw in series.items():
                try:
                    ratio = float(raw)
                except (TypeError, ValueError):
                    continue
                if ratio == 0:
                    continue
                observed = _as_date(idx)
                if observed:
                    events[symbol].append({
                        "date": observed,
                        "ratio": ratio,
                        "source": "YAHOO_FINANCE",
                    })
        return {"events": events, "covered": covered}

    if len(symbols) == 1:
        symbol = symbols[0]
        covered[symbol] = True
        if "Stock Splits" in data.columns:
            for idx, raw in data["Stock Splits"].dropna().items():
                try:
                    ratio = float(raw)
                except (TypeError, ValueError):
                    continue
                if ratio == 0:
                    continue
                observed = _as_date(idx)
                if observed:
                    events[symbol].append({
                        "date": observed,
                        "ratio": ratio,
                        "source": "YAHOO_FINANCE",
                    })
    return {"events": events, "covered": covered}


def fetch_recent_splits_yahoo(
    symbols: Iterable[str],
    session_date: date,
    lookback_days: int = 100,
) -> dict:
    symbols = sorted(set(symbols))
    if not symbols:
        return {"events": {}, "covered": {}}
    start = session_date - timedelta(days=lookback_days)
    end = session_date + timedelta(days=1)
    try:
        data = yf.download(
            symbols,
            start=start.isoformat(),
            end=end.isoformat(),
            interval="1d",
            actions=True,
            auto_adjust=False,
            repair=False,
            threads=True,
            group_by="ticker",
            progress=False,
            timeout=20,
        )
    except Exception as exc:
        raise YahooActionsError(f"Yahoo split download failed: {exc}") from exc
    return parse_split_download(data, symbols)
