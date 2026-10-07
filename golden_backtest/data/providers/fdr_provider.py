"""FinanceDataReader 어댑터(P1의 유일한 가격 소스). 네트워크 호출은 이 파일에만 둔다.

분할 이벤트는 Yahoo의 split 기록(yfinance, 이 저장소의 기존 의존성)을 쓴다. FDR의 미국 가격이 Yahoo 기반이라 같은 출처다.
"""
from __future__ import annotations

import logging
import time

import pandas as pd

logger = logging.getLogger("golden_backtest.fdr")


def _fdr():
    import FinanceDataReader as fdr
    return fdr


def fetch_sp500_symbols() -> list[str]:
    listing = _fdr().StockListing("S&P500")
    col = "Symbol" if "Symbol" in listing.columns else "Code"
    return [str(s) for s in listing[col].dropna().tolist()]


def dual_class_variant(symbol: str) -> str | None:
    """FDR 목록이 점을 지운 티커(BRKB) → BRK-B. 마지막 글자 앞에 하이픈을 넣는다."""
    if "." in symbol:
        return symbol.replace(".", "-")
    if len(symbol) >= 3:
        return f"{symbol[:-1]}-{symbol[-1]}"
    return None


def _read(symbol: str, start: str, retries: int = 3) -> pd.DataFrame:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            df = _fdr().DataReader(symbol, start)
            if df is not None and not df.empty:
                return df
            last = ValueError("빈 데이터프레임")
        except Exception as exc:  # noqa: BLE001
            last = exc
            if "404" in str(exc):
                break  # 없는 심볼은 재시도해도 같다
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{symbol}: {last}")


def fetch_ohlcv(symbol: str, start: str, dual_class_retry: bool = True) -> tuple[pd.DataFrame, str]:
    """(일봉, 실제로 성공한 심볼). 실패하면 변형 심볼로 한 번 더 시도하고, 그래도 안 되면 예외."""
    try:
        return _read(symbol, start), symbol
    except RuntimeError as first:
        alt = dual_class_variant(symbol) if dual_class_retry else None
        if alt is None:
            raise
        try:
            return _read(alt, start), alt
        except RuntimeError:
            raise first


def fetch_splits(symbol: str, retries: int = 3) -> pd.Series:
    """Yahoo split 이벤트: index=ex-date(tz 없음), value=비율. 분할이 없으면 빈 Series. 조회 실패는 예외."""
    import yfinance as yf

    last: Exception | None = None
    for attempt in range(retries):
        try:
            s = yf.Ticker(symbol).splits
            if s is None:
                s = pd.Series(dtype=float)
            s = s.astype(float)
            if len(s):
                idx = pd.to_datetime(s.index)
                if getattr(idx, "tz", None) is not None:
                    idx = idx.tz_localize(None)
                s.index = idx.normalize()
            return s[s > 0]
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{symbol} 분할 조회 실패: {last}")
