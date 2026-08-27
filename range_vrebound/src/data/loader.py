"""KR 유니버스 및 OHLCV 데이터 로더.

screening.py를 import하거나 수정하지 않는다 — 운영 중인 SEPA 파이프라인은
건드리지 않는다 (Phase 0 계획 제안 9). 대신 코스피+코스닥 시가총액 상위
N종목을 고르는 동일한 방식(fdr.StockListing 기반)을 이 모듈에서 독립적으로
재구현한다.

네트워크 호출이 필요한 함수(fetch_*)와, 그 결과를 가공하는 순수 함수를
분리해서 순수 함수만 네트워크 없이 단위 테스트할 수 있게 한다.
"""
from __future__ import annotations

import logging
from datetime import date as date_

import pandas as pd

try:
    import FinanceDataReader as fdr
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "FinanceDataReader가 설치되어 있지 않습니다. "
        "`pip install -r range_vrebound/requirements.txt`"
    ) from exc

from src.models.market_data import MarketIndexBar, OHLCVBar

logger = logging.getLogger(__name__)

KOSPI_INDEX_CODE = "KS11"
KOSDAQ_INDEX_CODE = "KQ11"

_REQUIRED_LISTING_COLUMNS = {"Code", "Name", "Marcap"}


def select_top_n_by_market_cap(listing: pd.DataFrame, top_n: int) -> pd.DataFrame:
    """코스피+코스닥 통합 종목 목록에서 시가총액(Marcap) 상위 top_n을 고른다."""
    missing = _REQUIRED_LISTING_COLUMNS - set(listing.columns)
    if missing:
        raise ValueError(f"listing에 필요한 컬럼이 없습니다: {sorted(missing)}")
    cleaned = listing.dropna(subset=["Code", "Name", "Marcap"])
    cleaned = cleaned.sort_values("Marcap", ascending=False)
    return cleaned.head(top_n).reset_index(drop=True)


def fetch_kr_universe(top_n: int) -> pd.DataFrame:
    """코스피+코스닥 상장목록을 조회해 시가총액 상위 top_n을 반환한다 (네트워크 호출).

    결과에는 "Market" 컬럼(KOSPI/KOSDAQ)이 그대로 남아있다 — 종목별로
    어느 지수(KS11/KQ11)를 벤치마크로 써야 하는지 정할 때 쓴다. SEPA
    스크리너와 동일하게 "KOSDAQ GLOBAL"은 "KOSDAQ"으로 합친다
    ([screening.py:305](../screening.py)와 동일한 관례, 코드는 독립).
    """
    kospi = fdr.StockListing("KOSPI")
    kosdaq = fdr.StockListing("KOSDAQ")
    combined = pd.concat([kospi, kosdaq], ignore_index=True)
    if "Market" in combined.columns:
        combined["Market"] = combined["Market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
    return select_top_n_by_market_cap(combined, top_n)


def fetch_ohlcv(symbol: str, start: date_, end: date_) -> list[OHLCVBar]:
    """단일 종목의 일봉 OHLCV를 조회한다 (네트워크 호출)."""
    df = fdr.DataReader(symbol, start, end)
    return dataframe_to_ohlcv_bars(df, symbol)


def fetch_index_ohlcv(index_code: str, start: date_, end: date_) -> list[MarketIndexBar]:
    """KS11/KQ11 등 시장 지수의 종가 시계열을 조회한다 (네트워크 호출)."""
    df = fdr.DataReader(index_code, start, end)
    return [
        MarketIndexBar(date=idx.date(), index_code=index_code, close=float(row["Close"]))
        for idx, row in df.iterrows()
    ]


def dataframe_to_ohlcv_bars(df: pd.DataFrame, symbol: str) -> list[OHLCVBar]:
    """FinanceDataReader OHLCV DataFrame을 OHLCVBar 리스트로 변환한다."""
    return [
        OHLCVBar(
            date=idx.date(),
            symbol=symbol,
            open=float(row["Open"]),
            high=float(row["High"]),
            low=float(row["Low"]),
            close=float(row["Close"]),
            volume=float(row["Volume"]),
        )
        for idx, row in df.iterrows()
    ]
