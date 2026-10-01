"""
technical_signals/data.py — 네트워크 호출(유니버스·OHLCV 조회)
==================================================================

다른 서브시스템(`sepa/`, `screener/`, `range_vrebound/`)의 코드를 import하지
않는다.

한국 유니버스는 애초에 `fdr.StockListing("KOSPI"/"KOSDAQ")`의 Marcap(시가총액)
컬럼으로 상위 N종목만 추리려 했으나(`range_vrebound/src/data/loader.py`와
같은 관례), 실제로 돌려보니(로컬 및 GitHub Actions 둘 다) 이 컬럼이 —
Marcap뿐 아니라 Close/Volume/Amount 등 시세 관련 컬럼 전부 — 현재 이
엔드포인트에서 항상 NaN으로 내려와 사실상 못 쓴다(업스트림 데이터 문제로
추정, 이 리포의 다른 곳에도 잠재적으로 영향을 줄 수 있음). Code/Name/Market
은 정상 채워지므로, 시가총액 랭킹 없이 코스피+코스닥 전체를 스크리닝한다
(개별 종목 OHLCV는 `fdr.DataReader`로 별도 조회하며 이건 정상 동작 확인됨).
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
from naver_listing import naver_kr_listing

logger = logging.getLogger(__name__)

_KR_REQUIRED_COLUMNS = {"Code", "Name"}


def _kr_listing() -> pd.DataFrame:
    """코스피+코스닥 상장 목록. KRX(data.krx.co.kr) 점검·장애로 막히면 네이버 금융 대체 목록을 쓴다."""
    try:
        return pd.concat([fdr.StockListing("KOSPI"), fdr.StockListing("KOSDAQ")], ignore_index=True)
    except Exception as exc:  # noqa: BLE001
        logger.error("KRX 종목 목록 조회 실패(%s) — 네이버 대체 목록 사용", exc)
        return naver_kr_listing()


def fetch_kr_universe() -> pd.DataFrame:
    """코스피+코스닥 전체 상장종목. 컬럼: Code, Name, Market.
    시가총액 랭킹은 쓰지 않는다(모듈 docstring 참고 — Marcap이 항상 NaN).
    스팩(기업인수목적회사)은 가격이 거의 안 움직여 지표가 좋아 보이므로 제외한다."""
    combined = _kr_listing()
    if "Market" in combined.columns:
        combined["Market"] = combined["Market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
    missing = _KR_REQUIRED_COLUMNS - set(combined.columns)
    if missing:
        raise ValueError(f"KR listing에 필요한 컬럼이 없습니다: {sorted(missing)}")
    cleaned = combined.dropna(subset=["Code", "Name"])
    cleaned = cleaned[~cleaned["Name"].astype(str).str.contains("스팩", regex=False)]
    return cleaned.reset_index(drop=True)[["Code", "Name", "Market"]]


def fetch_us_universe() -> pd.DataFrame:
    """S&P500 전체 구성종목. 컬럼: Code, Name, Market(=US 고정)."""
    listing = fdr.StockListing("S&P500")
    code_col = "Symbol" if "Symbol" in listing.columns else "Code"
    name_col = "Name" if "Name" in listing.columns else "name"
    out = listing[[code_col, name_col]].rename(columns={code_col: "Code", name_col: "Name"})
    out = out.dropna(subset=["Code", "Name"])
    out["Market"] = "US"
    return out.reset_index(drop=True)


def clean_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """종가가 비어 있는 봉(장 마감 직후 미확정 봉 등)을 버린다. 남겨두면 마지막 종가가
    NaN이 되어 모든 이동평균/신호가 통째로 NaN이 된다."""
    if df is None or df.empty or "Close" not in df.columns:
        return df
    return df.dropna(subset=["Close"])


def fetch_ohlcv(code: str, start: dt.date) -> pd.DataFrame:
    """단일 종목의 일봉 OHLCV DataFrame(Open/High/Low/Close/Volume, DatetimeIndex 오름차순)."""
    return clean_ohlcv(fdr.DataReader(code, start))


def fetch_index_ohlcv(index_code: str, start: dt.date) -> pd.DataFrame:
    """시장 지수(KS11/KQ11/US500 등) 일봉. 시장 국면 게이트 계산용."""
    return clean_ohlcv(fdr.DataReader(index_code, start))


def history_start_date(today: dt.date | None = None) -> dt.date:
    today = today or dt.date.today()
    return today - dt.timedelta(days=cfg.HISTORY_CALENDAR_DAYS)
