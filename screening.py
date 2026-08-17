"""
SEPA(미너비니) 추세 템플릿 1차 스크리너
=====================================
한국(코스피/코스닥 시가총액 상위 N종목, 기본 200)과 미국(S&P500 전체
구성종목)을 대상으로 마크 미너비니의 "추세 템플릿(Trend Template)"
8개 조건을 매일 점검한다. 두 시장은 서로 다른 CSV/구글시트 탭으로
완전히 분리되어 저장된다.

중요한 설계 원칙 (임의로 완화하지 말 것)
----------------------------------------
1. 8개 조건은 "전부 동시 충족(AND)"이 원칙이다. 점수화하거나 부분 충족을
   통과로 처리하지 않는다.
2. 이동평균은 전부 단순이동평균(SMA)만 사용한다. EMA는 사용하지 않는다.
3. 개별 종목의 데이터가 부족하거나 조회에 실패하면 추정치로 채우지 않고
   "확인 불가"로 표시해 제외한다.
4. 8번 조건(상대강도)은 IBD RS가 없어 각 시장 지수(한국: 코스피/코스닥,
   미국: S&P500) 대비 3·6·12개월 초과수익률로 계산한 "대체 지표"이며,
   반드시 그렇게 표기한다.
5. 이 스크리너는 1차 필터(8개 조건 AND)가 본체다. 스테이지(와인스타인
   4단계) 확정, 베이스 단계 카운트, 펀더멘털, 촉매 판단, 매매 신호/
   자동매매는 여전히 다루지 않는다.
6. 거래량 기반 지표(Dry-up/돌파거래량), VCP 수축, 피벗 근접도, 컨빅션
   스코어는 "진입 타이밍 참고용" 부가 지표이며 8개 조건 판정에는 전혀
   관여하지 않는다. VCP는 실제 미너비니 방법론(스윙 고점/저점 기반 다중
   파동 탐지)이 아닌 고정 4주 구간 비교 근사치이므로 반드시 그렇게
   표기한다. 이 지표들은 매수 신호가 아니다.

실행 방법
---------
    python screening.py                    # 기본: 한국(KR) 시장만 실행
    python screening.py --market US        # 미국(S&P500) 시장만 실행
    python screening.py --market ALL       # 한국 + 미국 순차 실행
    python screening.py --limit 20         # 개발/테스트용: 유니버스 앞에서 20종목만
    python screening.py --skip-sheets      # 구글시트 업로드 생략, CSV만 저장
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

try:
    import FinanceDataReader as fdr
except ImportError as exc:  # pragma: no cover
    print("FinanceDataReader가 설치되어 있지 않습니다. `pip install -r requirements.txt`")
    raise

# Windows 콘솔(cp949 등)에서도 한글 로그가 깨지지 않도록 UTF-8 강제
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

# ----------------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------------
TOP_N_DEFAULT = 200

# 넉넉하게 조회할 달력일 수. 200일 SMA + 1개월 추세 확인 + 52주 고저가 +
# 12개월 상대수익률까지 모두 커버하려면 최소 380일 이상의 캘린더 범위가
# 필요하다. 휴장일/공휴일을 감안해 900일(약 2.5년)을 조회한다.
HISTORY_CALENDAR_DAYS = 900

# 최소 필요 거래일 수 (이보다 적으면 데이터 부족으로 "확인 불가" 처리)
#   - SMA200 계산: 200일
#   - SMA200 1개월(20거래일) 추세 확인: +20일
#   - 52주 고저가 / 12개월 상대수익률: 약 252거래일
MIN_TRADING_DAYS = 260

# 200일선이 "최근 20거래일 동안 상승 중"인지 확인할 때 쓰는 lookback
MA_TREND_LOOKBACK = 20

# RS(대체 지표) 계산에 쓰는 기간 (달력일 기준, asof 방식으로 조회)
RS_PERIODS_DAYS = {"3m": 91, "6m": 182, "12m": 365}

# RS 백분위 통과 기준
RS_RANK_THRESHOLD = 70

# 12개월 상대수익률 계산이 가능하려면 상장/데이터 이력이 최소 이만큼은
# 있어야 한다 (그렇지 않으면 asof가 과거 데이터를 못 찾아 NaN이 됨)
MIN_HISTORY_SPAN_DAYS = 380

MAX_WORKERS_DEFAULT = 8
REQUEST_DELAY_RANGE = (0.15, 0.4)  # 요청 사이 랜덤 딜레이(초) - 레이트리밋 방지
MAX_RETRIES = 3
RETRY_BACKOFF_BASE = 1.6

KOSPI_INDEX_CODE = "KS11"
KOSDAQ_INDEX_CODE = "KQ11"
US_INDEX_CODE = "US500"  # S&P500 지수

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

# ----------------------------------------------------------------------------
# 진입 타이밍 참고 지표 (거래량 / VCP 근사치 / 피벗) 설정
# ----------------------------------------------------------------------------
# 아래 지표들은 8개 조건(추세 템플릿) 판정과는 완전히 별개인 "참고용 부가 정보"다.
# "전체통과(8개AND)" 판정에는 어떤 영향도 주지 않는다.
# VCP(변동성 수축)는 실제 미너비니 방법론(스윙 고점/저점 기반 다중 파동 탐지)의
# 근사치일 뿐이며, 반드시 그렇게 표기한다 (RS를 "대체 지표"로 표기한 것과 동일한 원칙).

VOL_SMA_WINDOW = 50            # 평균 거래량 기준 일수
DRYUP_SHORT_WINDOW = 10        # Dry-up 비율 계산용 단기 평균거래량 일수

VCP_WINDOW_DAYS = 20           # VCP 비교 구간 길이 (4주 = 20거래일)
VCP_CONTRACTION_THRESHOLD = 0.90  # 직전 구간 대비 이 비율 이하로 줄어야 "수축"으로 인정

PIVOT_LOOKBACK_DAYS = 50       # 피벗(베이스 내부 저항선) 탐색 기간 (10주, 당일 제외)
PIVOT_NEAR_LOW = -0.05         # "피벗임박"으로 볼 하한 (피벗 대비 -5%)
PIVOT_NEAR_HIGH = 0.0          # "피벗임박"으로 볼 상한 (피벗 대비 0%, 즉 아직 안 뚫음)

# 컨빅션 스코어 가중치 (초기값 - 임의 설정, 추후 성과 데이터로 튜닝 예정)
CONVICTION_WEIGHTS = {
    "vcp": 0.25,
    "pivot": 0.25,
    "breakout_vol": 0.20,
    "dryup": 0.15,
    "rs": 0.15,
}


@dataclass(frozen=True)
class MarketConfig:
    key: str                 # "KR" | "US"
    label: str                # 로그용 표시 이름
    file_prefix: str          # CSV 파일명 접두사
    sheet_tab_prefix: str     # 구글시트 일자별 탭 이름 접두사 ("" = 기존 한국 탭과 동일한 이름 유지)
    summary_sheet_name: str   # 구글시트 누적 요약 탭 이름
    rs_note: str               # 구글시트 안내문구에 들어갈 RS 설명


MARKET_CONFIGS: dict[str, MarketConfig] = {
    "KR": MarketConfig(
        key="KR", label="한국(코스피/코스닥 시총 상위)",
        file_prefix="kr", sheet_tab_prefix="", summary_sheet_name="일별요약",
        rs_note="코스피/코스닥 지수 대비 3·6·12개월 초과수익률의 유니버스 내 백분위",
    ),
    "US": MarketConfig(
        key="US", label="미국(S&P500 전체 구성종목)",
        file_prefix="us", sheet_tab_prefix="US_", summary_sheet_name="US_일별요약",
        rs_note="S&P500 지수 대비 3·6·12개월 초과수익률의 유니버스 내 백분위",
    ),
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("sepa_screener")


# ----------------------------------------------------------------------------
# 데이터 구조
# ----------------------------------------------------------------------------
@dataclass
class StockResult:
    code: str
    name: str
    market: str
    marcap: Optional[float] = None
    status: str = "OK"  # OK | 확인불가
    exclude_reason: str = ""

    close: Optional[float] = None
    sma50: Optional[float] = None
    sma150: Optional[float] = None
    sma200: Optional[float] = None
    high_52w: Optional[float] = None
    low_52w: Optional[float] = None

    cond1_above_150_200: Optional[bool] = None
    cond2_150_above_200: Optional[bool] = None
    cond3_200_rising: Optional[bool] = None
    cond4_50_above_150_200: Optional[bool] = None
    cond5_above_50: Optional[bool] = None
    cond6_30pct_above_low: Optional[bool] = None
    cond7_within_25pct_high: Optional[bool] = None

    rs_3m: Optional[float] = None
    rs_6m: Optional[float] = None
    rs_12m: Optional[float] = None
    rs_raw: Optional[float] = None
    rs_percentile: Optional[float] = None
    cond8_rs_rank: Optional[bool] = None

    met_count: Optional[int] = None  # 참고용: 8개 중 몇 개를 충족했는지 (통과 판정은 여전히 8개 AND)
    pass_all: Optional[bool] = None

    # --- 진입 타이밍 참고 지표 (8개 조건 판정과 무관, 참고용) ---
    volume: Optional[float] = None
    vol_sma50: Optional[float] = None
    dryup_ratio: Optional[float] = None
    breakout_vol_ratio: Optional[float] = None
    vcp_ratio: Optional[float] = None          # 근사치 (스윙 파동 탐지가 아닌 고정 4주 구간 비교)
    vcp_forming: Optional[bool] = None
    pivot: Optional[float] = None
    pivot_position: Optional[float] = None
    pivot_near: Optional[bool] = None
    conviction_score: Optional[float] = None   # 참고용 진입 타이밍 점수(0~10), 매수 신호 아님


# ----------------------------------------------------------------------------
# 유틸: 재시도 포함 데이터 조회
# ----------------------------------------------------------------------------
def fetch_price_history(code: str, start: str) -> pd.DataFrame:
    """FinanceDataReader로 가격 이력을 조회한다. 실패 시 재시도한다."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            time.sleep(random.uniform(*REQUEST_DELAY_RANGE))
            df = fdr.DataReader(code, start)
            if df is None or df.empty:
                raise ValueError("빈 데이터프레임 반환")
            return df
        except Exception as exc:  # noqa: BLE001 - 개별 종목 실패는 전체를 죽이면 안 됨
            last_exc = exc
            wait = (RETRY_BACKOFF_BASE ** attempt) + random.uniform(0, 0.5)
            logger.warning(
                "[%s] 조회 실패 (attempt %d/%d): %s -> %.1fs 후 재시도",
                code, attempt, MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"{code} 조회 최종 실패: {last_exc}")


def fetch_stock_listing(market: str) -> pd.DataFrame:
    """fdr.StockListing을 재시도와 함께 호출한다 (유니버스 조회는 실패 시 전체가 죽으므로 특히 중요)."""
    last_exc: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            listing = fdr.StockListing(market)
            if listing is None or listing.empty:
                raise ValueError("빈 목록 반환")
            return listing
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            wait = (RETRY_BACKOFF_BASE ** attempt) + random.uniform(0, 1.0)
            logger.warning(
                "종목 목록(%s) 조회 실패 (attempt %d/%d): %s -> %.1fs 후 재시도",
                market, attempt, MAX_RETRIES, exc, wait,
            )
            time.sleep(wait)
    raise RuntimeError(f"종목 목록({market}) 조회 최종 실패: {last_exc}")


def get_universe_kr(top_n: int) -> pd.DataFrame:
    """코스피/코스닥 시가총액 상위 top_n 종목 목록을 가져온다."""
    logger.info("KRX 전체 종목 목록 조회 중...")
    listing = fetch_stock_listing("KRX")

    # KOSDAQ GLOBAL은 코스닥 내 세그먼트이므로 코스닥에 포함, KONEX는 제외
    listing = listing[listing["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])].copy()
    listing["Market"] = listing["Market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})

    # 우선주/스팩/리츠 등은 시가총액 랭킹에서 원칙적으로 제외 대상이 아니므로
    # (사용자가 별도 요청하지 않았음) 그대로 둔다. 다만 시총 결측치는 제외.
    listing = listing.dropna(subset=["Marcap"])
    listing = listing.sort_values("Marcap", ascending=False)
    listing = listing.head(top_n).reset_index(drop=True)
    logger.info("유니버스 확정: %d종목 (코스피+코스닥 시총 상위)", len(listing))
    return listing[["Code", "Name", "Market", "Marcap"]]


# fdr.StockListing('S&P500')이 복수클래스 주식의 점(.)을 제거해서 내려주는데,
# 정작 시세 조회(야후 파이낸스 백엔드)는 하이픈 표기를 요구해서 그대로 두면
# "확인불가"로 빠진다. S&P500 내 해당 종목은 아래 두 개뿐이라 명시적으로 보정한다.
US_TICKER_OVERRIDES = {
    "BRKB": "BRK-B",  # Berkshire Hathaway
    "BFB": "BF-B",    # Brown-Forman
}


def get_universe_us() -> pd.DataFrame:
    """S&P500 전체 구성종목 목록을 가져온다 (시가총액 데이터는 이 소스에서 제공하지 않음)."""
    logger.info("S&P500 구성종목 목록 조회 중...")
    listing = fetch_stock_listing("S&P500")
    listing = listing.rename(columns={"Symbol": "Code"})
    listing["Code"] = listing["Code"].replace(US_TICKER_OVERRIDES)
    listing["Market"] = "US"
    listing["Marcap"] = np.nan  # 무료 소스 한계로 시총 데이터 없음 (전체 구성종목을 그대로 스크리닝)
    listing = listing.dropna(subset=["Code", "Name"]).reset_index(drop=True)
    logger.info("유니버스 확정: %d종목 (S&P500 전체 구성종목)", len(listing))
    return listing[["Code", "Name", "Market", "Marcap"]]


def asof_price(series: pd.Series, target_date: pd.Timestamp) -> Optional[float]:
    """target_date 시점 또는 그 이전 가장 가까운 값을 반환. 없으면 None."""
    try:
        val = series.asof(target_date)
    except Exception:  # noqa: BLE001
        return None
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    return float(val)


# ----------------------------------------------------------------------------
# 종목 1개 처리
# ----------------------------------------------------------------------------
def evaluate_stock(code: str, name: str, market: str, marcap: float, start_date: str) -> tuple[StockResult, Optional[pd.Series]]:
    """
    한 종목에 대해 8개 조건 중 1~7번을 판정한다.
    (8번 RS는 전체 유니버스가 모여야 백분위를 매길 수 있으므로 이 함수에서는
    3/6/12개월 초과수익률의 '원재료'인 종가 시계열만 함께 반환한다.)
    """
    result = StockResult(code=code, name=name, market=market, marcap=marcap)

    try:
        df = fetch_price_history(code, start_date)
    except Exception as exc:  # noqa: BLE001
        result.status = "확인불가"
        result.exclude_reason = f"데이터 조회 실패: {exc}"
        return result, None

    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    # 데이터 소스가 드물게 특정일 OHLC를 통째로 NaN으로 반환하는 경우가 있음
    # (예: 당일 장중 미확정 데이터, 소스 자체의 결측일). 그 하루만 건너뛴다.
    df = df.dropna(subset=["Close", "High", "Low"])

    if len(df) < MIN_TRADING_DAYS:
        result.status = "확인불가"
        result.exclude_reason = f"거래일 데이터 부족 ({len(df)}일 < 최소 {MIN_TRADING_DAYS}일, 상장 이력 짧음 등)"
        return result, None

    span_days = (df.index[-1] - df.index[0]).days
    if span_days < MIN_HISTORY_SPAN_DAYS:
        result.status = "확인불가"
        result.exclude_reason = f"데이터 이력 기간 부족 ({span_days}일 < 최소 {MIN_HISTORY_SPAN_DAYS}일)"
        return result, None

    close = df["Close"].astype(float)
    high = df["High"].astype(float)
    low = df["Low"].astype(float)

    sma50 = close.rolling(50).mean()
    sma150 = close.rolling(150).mean()
    sma200 = close.rolling(200).mean()

    if pd.isna(sma200.iloc[-1]) or pd.isna(sma150.iloc[-1]) or pd.isna(sma50.iloc[-1]):
        result.status = "확인불가"
        result.exclude_reason = "이동평균 계산 불가 (데이터 결측)"
        return result, None

    if len(sma200) <= MA_TREND_LOOKBACK or pd.isna(sma200.iloc[-1 - MA_TREND_LOOKBACK]):
        result.status = "확인불가"
        result.exclude_reason = "200일선 추세 판정 불가 (데이터 부족)"
        return result, None

    last_close = float(close.iloc[-1])
    last_sma50 = float(sma50.iloc[-1])
    last_sma150 = float(sma150.iloc[-1])
    last_sma200 = float(sma200.iloc[-1])
    sma200_1m_ago = float(sma200.iloc[-1 - MA_TREND_LOOKBACK])

    # 52주 고저가: 최근 252거래일(대략 52주) 기준, 일중 고가/저가 사용
    window = min(252, len(df))
    high_52w = float(high.iloc[-window:].max())
    low_52w = float(low.iloc[-window:].min())

    result.close = last_close
    result.sma50 = last_sma50
    result.sma150 = last_sma150
    result.sma200 = last_sma200
    result.high_52w = high_52w
    result.low_52w = low_52w

    # --- 조건 1~7 ---
    result.cond1_above_150_200 = (last_close > last_sma150) and (last_close > last_sma200)
    result.cond2_150_above_200 = last_sma150 > last_sma200
    result.cond3_200_rising = last_sma200 > sma200_1m_ago
    result.cond4_50_above_150_200 = (last_sma50 > last_sma150) and (last_sma50 > last_sma200)
    result.cond5_above_50 = last_close > last_sma50
    result.cond6_30pct_above_low = last_close >= low_52w * 1.30
    result.cond7_within_25pct_high = last_close <= high_52w * 1.25

    # --- 진입 타이밍 참고 지표 (8개 조건과 무관, 참고용) ---
    if "Volume" in df.columns:
        volume = df["Volume"].astype(float)
        compute_timing_metrics(result, high, low, close, volume)

    return result, close


def compute_timing_metrics(result: StockResult, high: pd.Series, low: pd.Series,
                            close: pd.Series, volume: pd.Series) -> None:
    """거래량/VCP(근사치)/피벗 등 진입 타이밍 참고 지표를 계산해 result에 채운다.
    여기서 계산되는 값들은 8개 조건 판정에 전혀 관여하지 않는 부가 정보다."""
    n = len(close)

    # 거래량이 0이거나 결측인 구간이 있으면(일부 종목의 저유동성 구간 등) 계산을 건너뛴다.
    if volume.iloc[-VOL_SMA_WINDOW:].isna().any() or (volume.iloc[-VOL_SMA_WINDOW:] <= 0).all():
        return

    last_volume = float(volume.iloc[-1])
    vol_sma50 = float(volume.rolling(VOL_SMA_WINDOW).mean().iloc[-1])
    result.volume = last_volume
    result.vol_sma50 = vol_sma50

    if vol_sma50 > 0:
        vol_sma_short = float(volume.rolling(DRYUP_SHORT_WINDOW).mean().iloc[-1])
        result.dryup_ratio = vol_sma_short / vol_sma50
        result.breakout_vol_ratio = last_volume / vol_sma50

    # --- VCP 수축 근사치: 4주(20거래일) 구간 3개의 고저폭(%)을 순차 비교 ---
    if n >= VCP_WINDOW_DAYS * 3:
        def range_pct(lo: int, hi: int) -> Optional[float]:
            seg_high = float(high.iloc[lo:hi].max())
            seg_low = float(low.iloc[lo:hi].min())
            if seg_high <= 0:
                return None
            return (seg_high - seg_low) / seg_high

        range0 = range_pct(n - VCP_WINDOW_DAYS, n)                        # 최근 4주
        range1 = range_pct(n - VCP_WINDOW_DAYS * 2, n - VCP_WINDOW_DAYS)  # 직전 4주
        range2 = range_pct(n - VCP_WINDOW_DAYS * 3, n - VCP_WINDOW_DAYS * 2)  # 그 직전 4주

        if range0 is not None and range1 is not None and range2 is not None and range1 > 0 and range2 > 0:
            result.vcp_ratio = range0 / range1
            result.vcp_forming = (range0 <= range1 * VCP_CONTRACTION_THRESHOLD) and \
                                  (range1 <= range2 * VCP_CONTRACTION_THRESHOLD)

    # --- 피벗(베이스 내부 저항선) 근사치: 당일 제외 최근 10주 내 최고가 ---
    if n >= PIVOT_LOOKBACK_DAYS + 1:
        pivot = float(high.iloc[-(PIVOT_LOOKBACK_DAYS + 1):-1].max())
        if pivot > 0:
            result.pivot = pivot
            result.pivot_position = float(close.iloc[-1]) / pivot - 1.0
            result.pivot_near = PIVOT_NEAR_LOW <= result.pivot_position <= PIVOT_NEAR_HIGH


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _score_dryup(ratio: float) -> float:
    """낮을수록(거래량이 마를수록) 높은 점수. 0.5 이하=10점, 1.2 이상=0점."""
    lo, hi = 0.5, 1.2
    return 10.0 * _clamp01((hi - ratio) / (hi - lo))


def _score_breakout(ratio: float) -> float:
    """높을수록(거래량이 실린 돌파일수록) 높은 점수. 1.0 이하=0점, 2.0 이상=10점."""
    lo, hi = 1.0, 2.0
    return 10.0 * _clamp01((ratio - lo) / (hi - lo))


def _score_vcp(ratio: float) -> float:
    """VCP수축비율이 낮을수록(더 수축했을수록) 높은 점수. 0.3 이하=10점, 1.0 이상=0점."""
    lo, hi = 0.3, 1.0
    return 10.0 * _clamp01((hi - ratio) / (hi - lo))


def _score_pivot(position: float) -> float:
    """피벗 대비 -5%~0% 구간이 10점(가장 좋음). 그 구간을 벗어날수록 점수가 줄어든다."""
    if PIVOT_NEAR_LOW <= position <= PIVOT_NEAR_HIGH:
        return 10.0
    if position > PIVOT_NEAR_HIGH:
        return 10.0 * _clamp01(1.0 - position / 0.10)  # +10% 이상이면 0점
    return 10.0 * _clamp01(1.0 - (PIVOT_NEAR_LOW - position) / 0.15)  # -20% 이하면 0점


def compute_conviction_score(r: "StockResult") -> Optional[float]:
    """
    참고용 진입 타이밍 점수(0~10). 8개 조건 판정과 무관하며 매수 신호가 아니다.
    구성 지표 중 하나라도 계산이 안 된 종목은 (추정으로 채우지 않고) None으로 둔다.
    가중치는 초기값이며, 실거래 성과가 쌓이면 재조정할 예정이다.
    """
    if (r.dryup_ratio is None or r.breakout_vol_ratio is None or
            r.vcp_ratio is None or r.pivot_position is None or r.rs_percentile is None):
        return None

    sub_scores = {
        "dryup": _score_dryup(r.dryup_ratio),
        "breakout_vol": _score_breakout(r.breakout_vol_ratio),
        "vcp": _score_vcp(r.vcp_ratio),
        "pivot": _score_pivot(r.pivot_position),
        "rs": r.rs_percentile / 10.0,
    }
    score = sum(sub_scores[k] * w for k, w in CONVICTION_WEIGHTS.items())
    return round(score, 2)


# ----------------------------------------------------------------------------
# RS(대체 지표) 계산
# ----------------------------------------------------------------------------
def compute_excess_returns(stock_close: pd.Series, index_close: pd.Series) -> dict[str, Optional[float]]:
    """종가 시계열로부터 3/6/12개월 초과수익률(종목 - 지수)을 계산."""
    last_date = stock_close.index[-1]
    last_price = float(stock_close.iloc[-1])
    out: dict[str, Optional[float]] = {}

    for label, days in RS_PERIODS_DAYS.items():
        target_date = last_date - pd.Timedelta(days=days)
        past_stock_price = asof_price(stock_close, target_date)
        past_index_price = asof_price(index_close, target_date)

        if not past_stock_price or not past_index_price:
            out[label] = None
            continue

        stock_ret = (last_price / past_stock_price) - 1.0
        index_ret = (float(index_close.asof(last_date)) / past_index_price) - 1.0
        out[label] = stock_ret - index_ret

    return out


# ----------------------------------------------------------------------------
# 메인 스크리닝 로직
# ----------------------------------------------------------------------------
def run_screening(market_key: str, top_n: int, max_workers: int, limit: Optional[int] = None) -> pd.DataFrame:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    if market_key == "KR":
        universe = get_universe_kr(top_n)
        benchmark_codes = (("KOSPI", KOSPI_INDEX_CODE), ("KOSDAQ", KOSDAQ_INDEX_CODE))
    elif market_key == "US":
        universe = get_universe_us()
        benchmark_codes = (("US", US_INDEX_CODE),)
    else:
        raise ValueError(f"알 수 없는 시장 키: {market_key}")

    if limit:
        universe = universe.head(limit)
        logger.info("개발/테스트 모드: 상위 %d종목으로 제한", limit)

    start_date = (pd.Timestamp.today() - pd.Timedelta(days=HISTORY_CALENDAR_DAYS)).strftime("%Y-%m-%d")

    logger.info("벤치마크 지수(%s) 조회 중...", ", ".join(label for label, _ in benchmark_codes))
    index_close: dict[str, pd.Series] = {}
    for label, code in benchmark_codes:
        idx_df = fetch_price_history(code, start_date)
        idx_df = idx_df.sort_index()
        idx_df = idx_df[~idx_df.index.duplicated(keep="last")]
        index_close[label] = idx_df["Close"].astype(float)

    results: list[StockResult] = []
    close_series_map: dict[str, pd.Series] = {}
    market_map: dict[str, str] = {}

    logger.info("종목별 가격 데이터 조회 및 조건 1~7 판정 시작 (동시성 %d)...", max_workers)
    total = len(universe)
    done_count = 0
    failed_codes: list[str] = []

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(
                evaluate_stock, row.Code, row.Name, row.Market, row.Marcap, start_date
            ): row
            for row in universe.itertuples(index=False)
        }
        for future in as_completed(futures):
            row = futures[future]
            done_count += 1
            try:
                stock_result, close_series = future.result()
            except Exception as exc:  # noqa: BLE001 - 개별 종목 실패가 전체를 죽이지 않도록
                logger.error("[%s %s] 예기치 못한 오류: %s", row.Code, row.Name, exc)
                stock_result = StockResult(
                    code=row.Code, name=row.Name, market=row.Market, marcap=row.Marcap,
                    status="확인불가", exclude_reason=f"예외 발생: {exc}",
                )
                close_series = None

            if stock_result.status == "확인불가":
                failed_codes.append(f"{stock_result.code} {stock_result.name}: {stock_result.exclude_reason}")
            else:
                close_series_map[stock_result.code] = close_series
                market_map[stock_result.code] = stock_result.market

            results.append(stock_result)
            if done_count % 25 == 0 or done_count == total:
                logger.info("진행률: %d/%d", done_count, total)

    if failed_codes:
        logger.warning("데이터 조회/계산 실패 또는 확인불가 종목 %d건:", len(failed_codes))
        for line in failed_codes:
            logger.warning("  - %s", line)

    # --- RS(대체 지표) 계산: OK 상태인 종목만 대상 ---
    logger.info("상대강도(RS, 대체 지표) 계산 중...")
    for r in results:
        if r.status != "OK":
            continue
        stock_close = close_series_map.get(r.code)
        if stock_close is None:
            continue
        bench = index_close.get(r.market)
        if bench is None:
            r.status = "확인불가"
            r.exclude_reason = f"벤치마크 지수 없음 (market={r.market})"
            continue

        excess = compute_excess_returns(stock_close, bench)
        if any(v is None for v in excess.values()):
            r.status = "확인불가"
            r.exclude_reason = "RS(3/6/12개월 초과수익률) 계산 불가 - 데이터 이력 부족"
            continue

        r.rs_3m, r.rs_6m, r.rs_12m = excess["3m"], excess["6m"], excess["12m"]
        # 동일가중 평균. (IBD RS처럼 최근 구간에 가중치를 더 주는 방식이 아니라
        # 3/6/12개월을 단순 평균한 값이며, 이는 설계상 임의 선택임을 명시)
        r.rs_raw = float(np.mean([r.rs_3m, r.rs_6m, r.rs_12m]))

    # --- RS 백분위 랭킹 (유니버스 내에서, OK 상태 + rs_raw 유효한 종목 대상) ---
    valid_rs = [r for r in results if r.status == "OK" and r.rs_raw is not None]
    if valid_rs:
        rs_values = pd.Series([r.rs_raw for r in valid_rs])
        pct_ranks = rs_values.rank(pct=True) * 100.0
        for r, pct in zip(valid_rs, pct_ranks):
            r.rs_percentile = float(pct)
            r.cond8_rs_rank = r.rs_percentile >= RS_RANK_THRESHOLD

    # --- 최종 판정: 8개 조건 전부 충족 ---
    for r in results:
        if r.status != "OK":
            r.pass_all = False
            continue
        conds = [
            r.cond1_above_150_200, r.cond2_150_above_200, r.cond3_200_rising,
            r.cond4_50_above_150_200, r.cond5_above_50, r.cond6_30pct_above_low,
            r.cond7_within_25pct_high, r.cond8_rs_rank,
        ]
        if any(c is None for c in conds):
            r.status = "확인불가"
            r.exclude_reason = r.exclude_reason or "조건 판정 불완전"
            r.pass_all = False
        else:
            r.met_count = sum(conds)
            r.pass_all = all(conds)

    # --- 컨빅션 스코어 (참고용 진입 타이밍 점수, 전체 스크리닝 대상 종목에 계산) ---
    for r in results:
        if r.status == "OK":
            r.conviction_score = compute_conviction_score(r)

    return results_to_dataframe(results)


def results_to_dataframe(results: list[StockResult]) -> pd.DataFrame:
    rows = []
    for r in results:
        rows.append({
            "종목코드": r.code,
            "종목명": r.name,
            "시장": r.market,
            "시가총액": r.marcap,
            "상태": r.status,
            "제외사유": r.exclude_reason,
            "종가": r.close,
            "SMA50": r.sma50,
            "SMA150": r.sma150,
            "SMA200": r.sma200,
            "52주최고가": r.high_52w,
            "52주최저가": r.low_52w,
            "조건1_150200위": r.cond1_above_150_200,
            "조건2_150위200": r.cond2_150_above_200,
            "조건3_200상승중": r.cond3_200_rising,
            "조건4_50위150200": r.cond4_50_above_150_200,
            "조건5_종가위50": r.cond5_above_50,
            "조건6_저가대비30pct이상": r.cond6_30pct_above_low,
            "조건7_고가대비25pct이내": r.cond7_within_25pct_high,
            "RS_3개월초과수익률": r.rs_3m,
            "RS_6개월초과수익률": r.rs_6m,
            "RS_12개월초과수익률": r.rs_12m,
            "RS_원점수": r.rs_raw,
            "RS_백분위랭킹": r.rs_percentile,
            "조건8_RS랭킹70이상_대체지표": r.cond8_rs_rank,
            "충족조건수(8개중, 참고용)": r.met_count,
            "전체통과(8개AND)": r.pass_all,
            "거래량": r.volume,
            "SMA50거래량": r.vol_sma50,
            "Dryup비율_참고용": r.dryup_ratio,
            "돌파거래량배율_참고용": r.breakout_vol_ratio,
            "VCP수축비율_근사치": r.vcp_ratio,
            "VCP형성중_근사치": r.vcp_forming,
            "피벗": r.pivot,
            "피벗대비위치_참고용": r.pivot_position,
            "피벗임박_참고용": r.pivot_near,
            "컨빅션스코어_참고용_매수신호아님": r.conviction_score,
        })
    df = pd.DataFrame(rows)
    df = df.sort_values(
        ["전체통과(8개AND)", "컨빅션스코어_참고용_매수신호아님", "충족조건수(8개중, 참고용)", "RS_백분위랭킹"],
        ascending=[False, False, False, False], na_position="last",
    )
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------
# 구글시트 업로드 (연동 정보 없으면 조용히 건너뜀)
# ----------------------------------------------------------------------------
def _apply_sheet_formatting(ws, df: pd.DataFrame) -> None:
    """가독성을 위한 서식: 헤더 고정/굵게, 기본 필터, 전체통과 행 하이라이트."""
    import gspread.utils as gutils

    n_rows, n_cols = len(df), len(df.columns)
    last_col = gutils.rowcol_to_a1(1, n_cols).rstrip("1")  # 예: 3 -> "C"
    header_row = 2  # 1행: 안내문구, 2행: 실제 헤더
    first_data_row = header_row + 1
    last_data_row = header_row + n_rows

    pass_col_idx = list(df.columns).index("전체통과(8개AND)") + 1
    pass_col_letter = gutils.rowcol_to_a1(1, pass_col_idx).rstrip("1")

    ws.freeze(rows=header_row)
    ws.format(f"A{header_row}:{last_col}{header_row}", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.90, "green": 0.90, "blue": 0.90},
    })
    ws.set_basic_filter(f"A{header_row}:{last_col}{last_data_row}")

    if n_rows > 0:
        rule = {
            "requests": [{
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [{
                            "sheetId": ws.id,
                            "startRowIndex": first_data_row - 1,
                            "endRowIndex": last_data_row,
                            "startColumnIndex": 0,
                            "endColumnIndex": n_cols,
                        }],
                        "booleanRule": {
                            "condition": {
                                "type": "CUSTOM_FORMULA",
                                "values": [{"userEnteredValue": f"=${pass_col_letter}{first_data_row}=TRUE"}],
                            },
                            "format": {"backgroundColor": {"red": 0.80, "green": 0.94, "blue": 0.80}},
                        },
                    },
                    "index": 0,
                },
            }],
        }
        ws.spreadsheet.batch_update(rule)


SUMMARY_HEADER = [
    "날짜", "스크리닝종목수", "정상판정", "확인불가", "8개조건전부통과",
    "평균RS백분위_정상판정종목", "평균충족조건수_정상판정종목",
]


def _upsert_daily_summary(sh, df: pd.DataFrame, run_date: str, cfg: MarketConfig) -> None:
    """누적 요약 탭에 오늘 자 요약 한 줄을 추가/갱신하고, 추세 차트를 붙여둔다."""
    import gspread

    summary_sheet_name = cfg.summary_sheet_name
    ok_df = df[df["상태"] == "OK"]
    row = [
        run_date,
        int(len(df)),
        int(len(ok_df)),
        int((df["상태"] == "확인불가").sum()),
        int((df["전체통과(8개AND)"] == True).sum()),  # noqa: E712
        round(float(ok_df["RS_백분위랭킹"].mean()), 2) if len(ok_df) else "",
        round(float(ok_df["충족조건수(8개중, 참고용)"].mean()), 2) if len(ok_df) else "",
    ]

    try:
        ws = sh.worksheet(summary_sheet_name)
        is_new = False
    except gspread.exceptions.WorksheetNotFound:
        ws = sh.add_worksheet(title=summary_sheet_name, rows="3000", cols=str(len(SUMMARY_HEADER) + 2))
        ws.update([SUMMARY_HEADER], "A1")
        ws.freeze(rows=1)
        ws.format(f"A1:{chr(ord('A') + len(SUMMARY_HEADER) - 1)}1", {"textFormat": {"bold": True}})
        is_new = True

    existing_dates = ws.col_values(1)  # 1행은 헤더
    if run_date in existing_dates:
        row_idx = existing_dates.index(run_date) + 1
        ws.update([row], f"A{row_idx}")
        logger.info("'%s' 탭의 %s 요약 행을 갱신했습니다.", summary_sheet_name, run_date)
    else:
        ws.append_row(row, value_input_option="USER_ENTERED")
        logger.info("'%s' 탭에 %s 요약 행을 추가했습니다.", summary_sheet_name, run_date)

    if is_new:
        _add_summary_chart(sh, ws, cfg)


def _add_summary_chart(sh, ws, cfg: MarketConfig) -> None:
    """누적 요약 탭에 '8개조건전부통과' 추이를 보여주는 꺾은선 차트를 한 번만 추가한다."""
    max_rows = int(ws.row_count)
    request = {
        "requests": [{
            "addChart": {
                "chart": {
                    "spec": {
                        "title": f"[{cfg.label}] 일별 8개 조건 전부 통과 종목 수 추이",
                        "basicChart": {
                            "chartType": "LINE",
                            "legendPosition": "BOTTOM_LEGEND",
                            "axis": [
                                {"position": "BOTTOM_AXIS", "title": "날짜"},
                                {"position": "LEFT_AXIS", "title": "통과 종목 수"},
                            ],
                            "domains": [{
                                "domain": {"sourceRange": {"sources": [{
                                    "sheetId": ws.id, "startRowIndex": 0, "endRowIndex": max_rows,
                                    "startColumnIndex": 0, "endColumnIndex": 1,
                                }]}},
                            }],
                            "series": [{
                                "series": {"sourceRange": {"sources": [{
                                    "sheetId": ws.id, "startRowIndex": 0, "endRowIndex": max_rows,
                                    "startColumnIndex": 4, "endColumnIndex": 5,
                                }]}},
                                "targetAxis": "LEFT_AXIS",
                            }],
                            "headerCount": 1,
                        },
                    },
                    "position": {
                        "overlayPosition": {
                            "anchorCell": {"sheetId": ws.id, "rowIndex": 0, "columnIndex": len(SUMMARY_HEADER) + 1},
                        },
                    },
                },
            },
        }],
    }
    sh.batch_update(request)


def upload_to_google_sheets(df: pd.DataFrame, run_date: str, cfg: MarketConfig) -> bool:
    creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
    sheet_id = os.environ.get("GOOGLE_SHEET_ID")

    if not creds_json or not sheet_id:
        logger.info("GOOGLE_SERVICE_ACCOUNT_JSON 또는 GOOGLE_SHEET_ID 환경변수가 없어 "
                     "구글시트 업로드를 생략합니다. (CSV 저장만 수행)")
        return False

    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError:
        logger.warning("gspread/google-auth가 설치되어 있지 않아 구글시트 업로드를 건너뜁니다.")
        return False

    try:
        # Windows/PowerShell에서 Secret 값에 UTF-8 BOM이 섞여 들어오는 경우 방어
        creds_dict = json.loads(creds_json.lstrip("﻿"))
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(credentials)
        sh = gc.open_by_key(sheet_id)

        sheet_name = f"{cfg.sheet_tab_prefix}{run_date}"  # 예: 2026-08-16 / US_2026-08-16
        try:
            ws = sh.worksheet(sheet_name)
            sh.del_worksheet(ws)  # 같은 날 재실행 시 덮어쓰기
            logger.info("기존 '%s' 탭을 삭제하고 새로 생성합니다.", sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            pass

        ws = sh.add_worksheet(title=sheet_name, rows=str(len(df) + 10), cols=str(len(df.columns) + 2))

        header = [f"※ [{cfg.label}] 8번 RS는 IBD RS가 없어 {cfg.rs_note}로 계산한 대체 지표입니다. "
                   "'충족조건수'는 참고용이며, '전체통과'만 8개 조건 전부 충족(AND) 여부의 공식 판정입니다. "
                   "VCP/피벗/컨빅션스코어는 8개 조건 판정과 무관한 진입 타이밍 참고 지표이며, "
                   "VCP는 실제 미너비니 방법론(스윙 고점/저점 기반 다중 파동 탐지)이 아닌 "
                   "고정 4주 구간 비교 근사치입니다. 매수 신호가 아닙니다."]
        values = [header, list(df.columns)] + df.astype(object).where(pd.notnull(df), "").values.tolist()

        ws.update(values, "A1")
        _apply_sheet_formatting(ws, df)
        logger.info("구글시트 업로드 완료: 탭 '%s' (%d행)", sheet_name, len(df))

        try:
            _upsert_daily_summary(sh, df, run_date, cfg)
        except Exception as exc:  # noqa: BLE001 - 요약 탭 실패가 본 업로드 성공을 덮지 않도록
            logger.warning("'%s' 요약 탭 갱신 실패 (본 결과 업로드는 정상 완료됨): %s", cfg.summary_sheet_name, exc)

        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("구글시트 업로드 실패: %s", exc)
        return False


# ----------------------------------------------------------------------------
# 엔트리포인트
# ----------------------------------------------------------------------------
def run_market(market_key: str, run_date: str, args) -> None:
    cfg = MARKET_CONFIGS[market_key]
    t0 = time.time()
    logger.info("--- [%s] 스크리닝 시작 ---", cfg.label)

    df = run_screening(market_key=market_key, top_n=args.top_n, max_workers=args.workers, limit=args.limit)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    full_path = OUTPUT_DIR / f"sepa_screening_{cfg.file_prefix}_full_{run_date}.csv"
    pass_path = OUTPUT_DIR / f"sepa_screening_{cfg.file_prefix}_pass_{run_date}.csv"
    latest_full_path = OUTPUT_DIR / f"latest_{cfg.file_prefix}_full.csv"
    latest_pass_path = OUTPUT_DIR / f"latest_{cfg.file_prefix}_pass.csv"

    df.to_csv(full_path, index=False, encoding="utf-8-sig")
    df.to_csv(latest_full_path, index=False, encoding="utf-8-sig")

    pass_df = df[df["전체통과(8개AND)"] == True]  # noqa: E712
    pass_df.to_csv(pass_path, index=False, encoding="utf-8-sig")
    pass_df.to_csv(latest_pass_path, index=False, encoding="utf-8-sig")

    logger.info("CSV 저장 완료: %s (전체 %d행), %s (통과 %d행)", full_path, len(df), pass_path, len(pass_df))

    if not args.skip_sheets:
        upload_to_google_sheets(df, run_date, cfg)
    else:
        logger.info("--skip-sheets 지정됨: 구글시트 업로드 생략")

    elapsed = time.time() - t0
    ok_count = int((df["상태"] == "OK").sum())
    excluded_count = int((df["상태"] == "확인불가").sum())
    logger.info(
        "--- [%s] 완료 --- 총 %d종목 | 정상판정 %d | 확인불가/제외 %d | 8개조건전부통과 %d | 소요시간 %.1f초",
        cfg.label, len(df), ok_count, excluded_count, len(pass_df), elapsed,
    )


def main():
    parser = argparse.ArgumentParser(description="SEPA 추세 템플릿 1차 스크리너")
    parser.add_argument("--market", choices=["KR", "US", "ALL"], default="KR",
                         help="KR=한국(코스피/코스닥), US=미국(S&P500), ALL=둘 다 순차 실행 (기본: KR)")
    parser.add_argument("--top-n", type=int, default=TOP_N_DEFAULT, help="한국 시장 시가총액 상위 몇 종목을 대상으로 할지 (미국은 S&P500 전체 고정)")
    parser.add_argument("--limit", type=int, default=None, help="개발/테스트용: 유니버스를 앞에서부터 N종목으로 제한")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS_DEFAULT, help="동시 요청 스레드 수")
    parser.add_argument("--skip-sheets", action="store_true", help="구글시트 업로드를 강제로 건너뜀")
    args = parser.parse_args()

    # 실행 서버의 로컬 시간대(GitHub Actions는 UTC)와 무관하게 한국 날짜로 고정
    run_date = datetime.now(ZoneInfo("Asia/Seoul")).strftime("%Y-%m-%d")
    markets = ["KR", "US"] if args.market == "ALL" else [args.market]

    logger.info("=== SEPA 추세 템플릿 스크리닝 시작 (%s, 대상 시장: %s) ===", run_date, ", ".join(markets))
    for market_key in markets:
        run_market(market_key, run_date, args)
    logger.info("=== 전체 완료 ===")


if __name__ == "__main__":
    main()
