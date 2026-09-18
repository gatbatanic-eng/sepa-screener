"""
technical_signals/data.py — 네트워크 호출(유니버스·OHLCV 조회)
==================================================================

다른 서브시스템(`sepa/`, `screener/`, `range_vrebound/`)의 코드를 import하지
않는다. 유니버스를 고르는 방식(코스피+코스닥 시가총액 상위 N, S&P500 전체
구성종목)은 관례상 동일하게 fdr.StockListing 기반으로 독립 재구현한다
(`range_vrebound/src/data/loader.py`와 같은 원칙).
"""
from __future__ import annotations

import datetime as dt
import logging

import pandas as pd

try:
    import FinanceDataReader as fdr
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "FinanceDataReader가 설치되어 있지 않습니다. "
        "`pip install -r technical_signals/requirements.txt`"
    ) from exc

import config as cfg

logger = logging.getLogger(__name__)

_KR_REQUIRED_COLUMNS = {"Code", "Name", "Marcap"}


def fetch_kr_universe(top_n: int = cfg.KR_TOP_N_DEFAULT) -> pd.DataFrame:
    """코스피+코스닥 시가총액 상위 top_n. 컬럼: Code, Name, Market."""
    kospi = fdr.StockListing("KOSPI")
    kosdaq = fdr.StockListing("KOSDAQ")
    combined = pd.concat([kospi, kosdaq], ignore_index=True)
    if "Market" in combined.columns:
        combined["Market"] = combined["Market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
    missing = _KR_REQUIRED_COLUMNS - set(combined.columns)
    if missing:
        raise ValueError(f"KR listing에 필요한 컬럼이 없습니다: {sorted(missing)}")
    cleaned = combined.dropna(subset=["Code", "Name", "Marcap"])
    cleaned = cleaned.sort_values("Marcap", ascending=False)
    return cleaned.head(top_n).reset_index(drop=True)[["Code", "Name", "Market"]]


def fetch_us_universe() -> pd.DataFrame:
    """S&P500 전체 구성종목. 컬럼: Code, Name, Market(=US 고정)."""
    listing = fdr.StockListing("S&P500")
    code_col = "Symbol" if "Symbol" in listing.columns else "Code"
    name_col = "Name" if "Name" in listing.columns else "name"
    out = listing[[code_col, name_col]].rename(columns={code_col: "Code", name_col: "Name"})
    out = out.dropna(subset=["Code", "Name"])
    out["Market"] = "US"
    return out.reset_index(drop=True)


def fetch_ohlcv(code: str, start: dt.date) -> pd.DataFrame:
    """단일 종목의 일봉 OHLCV DataFrame(Open/High/Low/Close/Volume, DatetimeIndex 오름차순)."""
    return fdr.DataReader(code, start)


def history_start_date(today: dt.date | None = None) -> dt.date:
    today = today or dt.date.today()
    return today - dt.timedelta(days=cfg.HISTORY_CALENDAR_DAYS)
