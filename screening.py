"""
SEPA(미너비니) 추세 템플릿 1차 스크리너
=====================================
코스피/코스닥 시가총액 상위 N종목(기본 200)을 대상으로 마크 미너비니의
"추세 템플릿(Trend Template)" 8개 조건을 매일 점검한다.

중요한 설계 원칙 (임의로 완화하지 말 것)
----------------------------------------
1. 8개 조건은 "전부 동시 충족(AND)"이 원칙이다. 점수화하거나 부분 충족을
   통과로 처리하지 않는다.
2. 이동평균은 전부 단순이동평균(SMA)만 사용한다. EMA는 사용하지 않는다.
3. 개별 종목의 데이터가 부족하거나 조회에 실패하면 추정치로 채우지 않고
   "확인 불가"로 표시해 제외한다.
4. 8번 조건(상대강도)은 한국 시장에 IBD RS가 없어 코스피/코스닥 지수 대비
   3·6·12개월 초과수익률로 계산한 "대체 지표"이며, 반드시 그렇게 표기한다.
5. 이 스크리너는 1차 필터일 뿐이다. 스테이지(와인스타인 4단계) 확정, VCP
   패턴, 피벗 돌파, 펀더멘털, 촉매 판단, 매매 신호/자동매매는 다루지 않는다.

실행 방법
---------
    python screening.py                 # 기본 실행 (상위 200종목, 구글시트 연동 시도)
    python screening.py --limit 20      # 개발/테스트용: 상위 20종목만
    python screening.py --skip-sheets   # 구글시트 업로드 생략, CSV만 저장
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

OUTPUT_DIR = Path(__file__).resolve().parent / "output"

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

    pass_all: Optional[bool] = None


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


def get_universe(top_n: int) -> pd.DataFrame:
    """코스피/코스닥 시가총액 상위 top_n 종목 목록을 가져온다."""
    logger.info("KRX 전체 종목 목록 조회 중...")
    listing = fdr.StockListing("KRX")

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

    return result, close


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
def run_screening(top_n: int, max_workers: int, limit: Optional[int] = None) -> pd.DataFrame:
    from concurrent.futures import ThreadPoolExecutor, as_completed

    universe = get_universe(top_n)
    if limit:
        universe = universe.head(limit)
        logger.info("개발/테스트 모드: 상위 %d종목으로 제한", limit)

    start_date = (pd.Timestamp.today() - pd.Timedelta(days=HISTORY_CALENDAR_DAYS)).strftime("%Y-%m-%d")

    logger.info("벤치마크 지수(KOSPI/KOSDAQ) 조회 중...")
    index_close: dict[str, pd.Series] = {}
    for label, code in (("KOSPI", KOSPI_INDEX_CODE), ("KOSDAQ", KOSDAQ_INDEX_CODE)):
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
            r.pass_all = all(conds)

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
            "전체통과(8개AND)": r.pass_all,
        })
    df = pd.DataFrame(rows)
    df = df.sort_values(["전체통과(8개AND)", "RS_백분위랭킹"], ascending=[False, False], na_position="last")
    return df.reset_index(drop=True)


# ----------------------------------------------------------------------------
# 구글시트 업로드 (연동 정보 없으면 조용히 건너뜀)
# ----------------------------------------------------------------------------
def upload_to_google_sheets(df: pd.DataFrame, run_date: str) -> bool:
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
        creds_dict = json.loads(creds_json)
        scopes = ["https://www.googleapis.com/auth/spreadsheets"]
        credentials = Credentials.from_service_account_info(creds_dict, scopes=scopes)
        gc = gspread.authorize(credentials)
        sh = gc.open_by_key(sheet_id)

        sheet_name = run_date  # 예: 2026-08-16
        try:
            ws = sh.worksheet(sheet_name)
            sh.del_worksheet(ws)  # 같은 날 재실행 시 덮어쓰기
            logger.info("기존 '%s' 탭을 삭제하고 새로 생성합니다.", sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            pass

        ws = sh.add_worksheet(title=sheet_name, rows=str(len(df) + 10), cols=str(len(df.columns) + 2))

        header = ["※ 8번 RS는 IBD RS가 없는 한국 시장 대체 지표(코스피/코스닥 지수 대비 3·6·12개월 초과수익률의 유니버스 내 백분위)입니다."]
        values = [header, list(df.columns)] + df.astype(object).where(pd.notnull(df), "").values.tolist()

        ws.update(values, "A1")
        logger.info("구글시트 업로드 완료: 탭 '%s' (%d행)", sheet_name, len(df))
        return True
    except Exception as exc:  # noqa: BLE001
        logger.error("구글시트 업로드 실패: %s", exc)
        return False


# ----------------------------------------------------------------------------
# 엔트리포인트
# ----------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="SEPA 추세 템플릿 1차 스크리너")
    parser.add_argument("--top-n", type=int, default=TOP_N_DEFAULT, help="시가총액 상위 몇 종목을 대상으로 할지")
    parser.add_argument("--limit", type=int, default=None, help="개발/테스트용: 유니버스를 앞에서부터 N종목으로 제한")
    parser.add_argument("--workers", type=int, default=MAX_WORKERS_DEFAULT, help="동시 요청 스레드 수")
    parser.add_argument("--skip-sheets", action="store_true", help="구글시트 업로드를 강제로 건너뜀")
    args = parser.parse_args()

    t0 = time.time()
    run_date = datetime.now().strftime("%Y-%m-%d")
    logger.info("=== SEPA 추세 템플릿 스크리닝 시작 (%s) ===", run_date)

    df = run_screening(top_n=args.top_n, max_workers=args.workers, limit=args.limit)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    full_path = OUTPUT_DIR / f"sepa_screening_full_{run_date}.csv"
    pass_path = OUTPUT_DIR / f"sepa_screening_pass_{run_date}.csv"
    latest_full_path = OUTPUT_DIR / "latest_full.csv"
    latest_pass_path = OUTPUT_DIR / "latest_pass.csv"

    df.to_csv(full_path, index=False, encoding="utf-8-sig")
    df.to_csv(latest_full_path, index=False, encoding="utf-8-sig")

    pass_df = df[df["전체통과(8개AND)"] == True]  # noqa: E712
    pass_df.to_csv(pass_path, index=False, encoding="utf-8-sig")
    pass_df.to_csv(latest_pass_path, index=False, encoding="utf-8-sig")

    logger.info("CSV 저장 완료: %s (전체 %d행), %s (통과 %d행)", full_path, len(df), pass_path, len(pass_df))

    if not args.skip_sheets:
        upload_to_google_sheets(df, run_date)
    else:
        logger.info("--skip-sheets 지정됨: 구글시트 업로드 생략")

    elapsed = time.time() - t0
    ok_count = int((df["상태"] == "OK").sum())
    excluded_count = int((df["상태"] == "확인불가").sum())
    logger.info(
        "=== 완료 === 총 %d종목 | 정상판정 %d | 확인불가/제외 %d | 8개조건전부통과 %d | 소요시간 %.1f초",
        len(df), ok_count, excluded_count, len(pass_df), elapsed,
    )


if __name__ == "__main__":
    main()
