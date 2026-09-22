"""momentum_signals/data.py — 유니버스·시세 조회.

다른 서브시스템과 동일하게 FinanceDataReader를 독립적으로 다시 부른다
(공유 코드 import 없음 — 이 저장소 관례).

국내 유니버스 한계: `fdr.StockListing("KOSPI"/"KOSDAQ")`의 Marcap 컬럼은
현재 항상 NaN이다(technical_signals 작업 때 발견, sepa/universe.py도 동일
문제로 liquidity 모드를 기본으로 씀). 그래서 스펙의 "시총 상위 200" 대신
20일 평균 거래대금 상위 200을 쓴다.
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
        "`pip install -r momentum_signals/requirements.txt`"
    ) from exc

import config as cfg

logger = logging.getLogger(__name__)

_SPAC_KEYWORDS = ("스팩", "기업인수목적")
_PREFERRED_SUFFIXES = ("우", "우B", "우C", "3우B", "2우B", "1우")


def _is_spac(name: str) -> bool:
    return any(kw in str(name) for kw in _SPAC_KEYWORDS)


def _is_preferred(name: str) -> bool:
    return str(name).strip().endswith(_PREFERRED_SUFFIXES)


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """종가가 비어 있는 미확정 봉을 버린다(technical_signals에서 발견된
    실제 버그 — 남겨두면 마지막 종가가 NaN이 되어 모든 지표가 깨진다)."""
    if df is None or df.empty or "Close" not in df.columns:
        return df
    return df.dropna(subset=["Close"])


def fetch_kr_candidate_universe() -> pd.DataFrame:
    """코스피+코스닥 전체 상장종목에서 우선주/스팩을 제외한 후보 리스트.
    컬럼: Code, Name, Market. 유동성 상위 선별은 pipeline.py가 20일 평균
    거래대금을 계산한 뒤 한다(단일일 스냅샷만으로는 20일 평균을 알 수 없다)."""
    kospi = fdr.StockListing("KOSPI")
    kosdaq = fdr.StockListing("KOSDAQ")
    combined = pd.concat([kospi, kosdaq], ignore_index=True)
    if "Market" in combined.columns:
        combined["Market"] = combined["Market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
    combined = combined.dropna(subset=["Code", "Name"])
    combined = combined[~combined["Name"].map(_is_spac)]
    combined = combined[~combined["Name"].map(_is_preferred)]
    return combined.reset_index(drop=True)[["Code", "Name", "Market"]]


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
    return clean_ohlcv(fdr.DataReader(code, start))


def fetch_index_ohlcv(index_code: str, start: dt.date) -> pd.DataFrame:
    return clean_ohlcv(fdr.DataReader(index_code, start))


def history_start_date(today: dt.date | None = None) -> dt.date:
    today = today or dt.date.today()
    return today - dt.timedelta(days=cfg.HISTORY_CALENDAR_DAYS)
