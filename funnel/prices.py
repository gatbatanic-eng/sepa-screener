"""주간 종가로 가격 지표를 계산한다 (Yahoo 일괄 다운로드)."""
from __future__ import annotations

import logging
import time

import pandas as pd

log = logging.getLogger("funnel.prices")

CHUNK = 200


def price_metrics(close: pd.Series) -> dict | None:
    """주간 종가 → 현재가, 52주 고점 대비, 6개월 수익률, 36→6개월 전 수익률, 40주선 위 여부."""
    close = pd.to_numeric(close, errors="coerce").dropna()
    close = close[close > 0]
    if len(close) < 30:
        return None
    price = float(close.iloc[-1])
    high52 = float(close.iloc[-52:].max())
    ret6m = price / float(close.iloc[-27]) - 1 if len(close) >= 27 else None
    ret36to6m = None
    if len(close) >= 80:  # 최소 1.5년 이력은 있어야 '과거 소외'를 판단
        start = close.iloc[-157] if len(close) >= 157 else close.iloc[0]
        ret36to6m = float(close.iloc[-27]) / float(start) - 1
    ma40 = float(close.iloc[-40:].mean()) if len(close) >= 40 else None
    return {
        "price": price,
        "priceDate": pd.Timestamp(close.index[-1]).date().isoformat(),
        "high52Ratio": price / high52 if high52 > 0 else None,
        "ret6m": ret6m,
        "ret36to6m": ret36to6m,
        "aboveMa40": None if ma40 is None else price > ma40,
    }


def download_weekly(symbols: list[str], period: str = "4y") -> dict[str, pd.Series]:
    """Yahoo 주간 종가. 실패한 종목은 결과에서 빠진다(0으로 채우지 않음)."""
    import yfinance as yf

    out: dict[str, pd.Series] = {}
    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        for attempt in range(3):
            try:
                frame = yf.download(chunk, period=period, interval="1wk", auto_adjust=True,
                                    progress=False, threads=True, group_by="column")
                break
            except Exception as exc:  # noqa: BLE001
                log.warning("Yahoo 다운로드 실패 (%d/%d): %s", attempt + 1, 3, exc)
                time.sleep(5 * (attempt + 1))
        else:
            continue
        if frame is None or frame.empty:
            continue
        closes = frame["Close"] if isinstance(frame.columns, pd.MultiIndex) else frame[["Close"]].rename(columns={"Close": chunk[0]})
        for sym in chunk:
            if sym in closes.columns:
                series = closes[sym].dropna()
                if not series.empty:
                    out[sym] = series
        log.info("가격 %d/%d 종목 수집", len(out), min(i + CHUNK, len(symbols)))
        time.sleep(1)
    return out
