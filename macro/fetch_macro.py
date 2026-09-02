"""
fetch_macro.py — 매크로 지표 수집 및 파생값 계산

수집 결과:
  data/macro_history.csv   지표별 일별 시계열 (넓은 형식, 최근 ~400 거래일)
  반환값 snapshot(dict)     지표별 현재값·변화율·백분위 등 (interpret.py 입력)

소스:
  FRED   — API 키 불필요 (fredgraph.csv)
  yfinance — 지수·환율·원자재
  ECOS   — 한국은행, ECOS_API_KEY 환경변수 필요 (없으면 KR3Y 건너뜀)
  pykrx  — 코스피 외국인 순매수 (KRX 접근 실패 시 건너뜀)

  --mock [calm|stress] : 네트워크 없이 합성 데이터로 실행 (규칙·대시보드 테스트용)
"""
from __future__ import annotations

import io
import os
import sys
import logging
from datetime import date, timedelta

import numpy as np
import pandas as pd
import requests

log = logging.getLogger("macro.fetch")

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
HISTORY_CSV = os.path.join(DATA_DIR, "macro_history.csv")

LOOKBACK_DAYS = 420  # 달력일 기준, 1년 백분위 + 여유

# ------------------------------------------------------------
# 지표 정의
#   kind: rate  → 변화를 bp로 (value*100 차이)
#         price → 변화를 %로
#         flow  → 누적합 (억원), 변화율 미계산
# ------------------------------------------------------------
INDICATORS = {
    "US10Y":  dict(name="미 국채 10년", unit="%",  kind="rate",  src="fred", code="DGS10"),
    "US2Y":   dict(name="미 국채 2년",  unit="%",  kind="rate",  src="fred", code="DGS2"),
    "HY_OAS": dict(name="하이일드 스프레드", unit="bp", kind="rate", src="fred", code="BAMLH0A0HYM2", scale=100),
    "WTI":    dict(name="WTI 유가", unit="$", kind="price", src="fred", code="DCOILWTICO", yf_fallback="CL=F"),
    "KR3Y":   dict(name="한국 국고채 3년", unit="%", kind="rate", src="ecos", code="817Y002/010200000"),
    "DXY":    dict(name="달러인덱스", unit="", kind="price", src="yf", code="DX-Y.NYB"),
    "USDKRW": dict(name="원/달러", unit="원", kind="price", src="yf", code="KRW=X"),
    "USDJPY": dict(name="엔/달러", unit="엔", kind="price", src="yf", code="JPY=X"),
    "COPPER": dict(name="구리", unit="$/lb", kind="price", src="yf", code="HG=F"),
    "GOLD":   dict(name="금", unit="$/oz", kind="price", src="yf", code="GC=F"),
    "VIX":    dict(name="VIX", unit="", kind="price", src="yf", code="^VIX"),
    "SOX":    dict(name="필라델피아 반도체", unit="", kind="price", src="yf", code="^SOX"),
    "KOSPI":  dict(name="코스피", unit="", kind="price", src="yf", code="^KS11"),
    "KOSDAQ": dict(name="코스닥", unit="", kind="price", src="yf", code="^KQ11"),
    "KOSPI_FOREIGN": dict(name="코스피 외국인 순매수", unit="억원", kind="flow", src="pykrx", code="KOSPI"),
}

# 파생 지표 (수집 후 계산)
DERIVED = {
    "SPREAD_2S10S": dict(name="2s10s 스프레드", unit="bp", kind="rate_bp"),   # 이미 bp 단위 → 차이 그대로
    "COPPER_GOLD":  dict(name="구리/금 비율", unit="", kind="price"),
}

GROUPS = {
    "금리": ["US10Y", "US2Y", "SPREAD_2S10S", "KR3Y"],
    "환율": ["DXY", "USDKRW", "USDJPY"],
    "원자재": ["WTI", "COPPER", "GOLD", "COPPER_GOLD"],
    "리스크심리": ["VIX", "HY_OAS"],
    "섹터수급": ["SOX", "KOSPI", "KOSDAQ", "KOSPI_FOREIGN"],
}


# ------------------------------------------------------------
# 소스별 수집 함수 — 모두 pd.Series(index=date, values=float) 반환
# ------------------------------------------------------------
def fetch_fred(code: str, start: date) -> pd.Series:
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={code}"
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text))
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"])
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    s = df.dropna().set_index("date")["value"]
    return s[s.index >= pd.Timestamp(start)]


def fetch_yf(code: str, start: date) -> pd.Series:
    import yfinance as yf
    df = yf.download(code, start=start.isoformat(), progress=False, auto_adjust=False)
    if df.empty:
        raise RuntimeError(f"yfinance empty: {code}")
    close = df["Close"]
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    s = close.dropna()
    s.index = pd.to_datetime(s.index).tz_localize(None)
    return s.astype(float)


def fetch_ecos(code: str, start: date) -> pd.Series:
    key = os.environ.get("ECOS_API_KEY")
    if not key:
        raise RuntimeError("ECOS_API_KEY 없음")
    stat, item = code.split("/")
    url = (f"https://ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/2000/"
           f"{stat}/D/{start:%Y%m%d}/{date.today():%Y%m%d}/{item}")
    r = requests.get(url, timeout=30)
    r.raise_for_status()
    rows = r.json().get("StatisticSearch", {}).get("row", [])
    if not rows:
        raise RuntimeError("ECOS 응답 비어있음")
    df = pd.DataFrame(rows)
    s = pd.Series(pd.to_numeric(df["DATA_VALUE"]).values,
                  index=pd.to_datetime(df["TIME"], format="%Y%m%d"))
    return s.dropna()


def fetch_pykrx_foreign(market: str, start: date) -> pd.Series:
    from pykrx import stock
    df = stock.get_market_trading_value_by_date(f"{start:%Y%m%d}", f"{date.today():%Y%m%d}", market)
    col = [c for c in df.columns if "외국인" in str(c)]
    if not col:
        raise RuntimeError(f"외국인 컬럼 없음: {list(df.columns)}")
    s = df[col[0]].astype(float) / 1e8  # 원 → 억원
    s.index = pd.to_datetime(s.index)
    return s


FETCHERS = {"fred": fetch_fred, "yf": fetch_yf, "ecos": fetch_ecos}


def fetch_all(start: date) -> pd.DataFrame:
    series = {}
    for iid, spec in INDICATORS.items():
        try:
            if spec["src"] == "pykrx":
                s = fetch_pykrx_foreign(spec["code"], start)
            else:
                s = FETCHERS[spec["src"]](spec["code"], start)
            if spec.get("scale"):
                s = s * spec["scale"]
            series[iid] = s
            log.info("%-14s %5d rows  last=%s %.4g", iid, len(s), s.index[-1].date(), s.iloc[-1])
        except Exception as e:  # noqa: BLE001
            fb = spec.get("yf_fallback")
            if fb:
                try:
                    s = fetch_yf(fb, start)
                    series[iid] = s
                    log.warning("%s 기본 소스 실패(%s) → yfinance %s 대체", iid, e, fb)
                    continue
                except Exception as e2:  # noqa: BLE001
                    log.error("%s 대체 소스도 실패: %s", iid, e2)
            log.error("%s 수집 실패: %s", iid, e)
    if not series:
        raise RuntimeError("수집된 지표가 하나도 없음")
    df = pd.DataFrame(series).sort_index()
    return df


# ------------------------------------------------------------
# 합성 데이터 (네트워크 없이 테스트)
# ------------------------------------------------------------
def mock_history(scenario: str = "calm", n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(42)
    idx = pd.bdate_range(end=date.today(), periods=n)

    def walk(start, vol, drift=0.0):
        steps = rng.normal(drift, vol, n)
        return start * np.exp(np.cumsum(steps))

    df = pd.DataFrame(index=idx)
    df["US10Y"] = 4.2 + np.cumsum(rng.normal(0, 0.03, n))
    df["US2Y"] = df["US10Y"] - 0.3 + np.cumsum(rng.normal(0, 0.02, n))
    df["HY_OAS"] = 320 + np.cumsum(rng.normal(0, 3, n))
    df["KR3Y"] = 2.9 + np.cumsum(rng.normal(0, 0.02, n))
    df["WTI"] = walk(75, 0.015)
    df["DXY"] = walk(103, 0.004)
    df["USDKRW"] = walk(1380, 0.004)
    df["USDJPY"] = walk(150, 0.005)
    df["COPPER"] = walk(4.3, 0.012)
    df["GOLD"] = walk(2600, 0.008)
    df["VIX"] = np.clip(15 + np.cumsum(rng.normal(0, 0.6, n)) * 0.3, 10, 40)
    df["SOX"] = walk(5000, 0.018, 0.0008)
    df["KOSPI"] = walk(2700, 0.012, 0.0004)
    df["KOSDAQ"] = walk(800, 0.014)
    df["KOSPI_FOREIGN"] = rng.normal(0, 3000, n)

    if scenario == "stress":
        k = 6  # 최근 6거래일에 충격
        df.iloc[-k:, df.columns.get_loc("US10Y")] += np.linspace(0, 0.35, k)
        df.iloc[-k:, df.columns.get_loc("VIX")] = np.linspace(18, 33, k)
        df.iloc[-k:, df.columns.get_loc("USDJPY")] *= np.linspace(1, 0.955, k)
        df.iloc[-k:, df.columns.get_loc("HY_OAS")] += np.linspace(0, 80, k)
        df.iloc[-k:, df.columns.get_loc("SOX")] *= np.linspace(1, 0.90, k)
        df.iloc[-k:, df.columns.get_loc("KOSPI_FOREIGN")] = -6000
        df.iloc[-25:, df.columns.get_loc("WTI")] *= np.linspace(1, 1.2, 25)
    return df


# ------------------------------------------------------------
# 파생 지표 & 스냅샷
# ------------------------------------------------------------
def add_derived(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if {"US10Y", "US2Y"} <= set(df.columns):
        df["SPREAD_2S10S"] = (df["US10Y"] - df["US2Y"]) * 100
    if {"COPPER", "GOLD"} <= set(df.columns):
        df["COPPER_GOLD"] = df["COPPER"] / df["GOLD"] * 1000
    return df


def _last_valid(s: pd.Series, n_back: int = 0) -> float | None:
    s = s.dropna()
    if len(s) <= n_back:
        return None
    return float(s.iloc[-1 - n_back])


def build_snapshot(df: pd.DataFrame) -> dict:
    """지표별 파생값 계산. 결측 지표는 값이 None."""
    specs = {**INDICATORS, **DERIVED}
    snap = {}
    for iid, spec in specs.items():
        if iid not in df.columns or df[iid].dropna().empty:
            continue
        s = df[iid].dropna()
        kind = spec["kind"]
        cur = float(s.iloc[-1])
        last_date = s.index[-1].date().isoformat()
        one_y = s[s.index >= s.index[-1] - pd.Timedelta(days=365)]

        def chg(n):
            prev = _last_valid(s, n)
            if prev is None:
                return None
            if kind == "rate":
                return round((cur - prev) * 100, 1)   # % → bp
            if kind == "rate_bp":
                return round(cur - prev, 1)
            if kind == "price":
                return round((cur / prev - 1) * 100, 2) if prev else None
            return None

        d = dict(
            id=iid, name=spec["name"], unit=spec["unit"], kind=kind,
            value=round(cur, 4), date=last_date,
            chg_1d=chg(1), chg_1w=chg(5), chg_1m=chg(21),
            prev_1m=_last_valid(s, 21),
            pct_1y=round(float((one_y < cur).mean() * 100), 1) if len(one_y) > 20 else None,
            max_1y=float(one_y.max()), min_1y=float(one_y.min()),
            above_ma50=bool(cur > s.tail(50).mean()) if len(s) >= 50 else None,
            is_high_1y=bool(cur >= one_y.max()) if len(one_y) > 20 else None,
            sum_5d=round(float(s.tail(5).sum()), 1) if kind == "flow" else None,
            sparkline=[round(float(x), 4) for x in s.tail(30).tolist()],
        )
        # HY_OAS 는 이미 bp 단위 → 변화도 bp 그대로
        if iid == "HY_OAS":
            d["chg_1d"], d["chg_1w"], d["chg_1m"] = (
                round(cur - _last_valid(s, n), 1) if _last_valid(s, n) is not None else None
                for n in (1, 5, 21))
        snap[iid] = d
    return snap


def load_history(mock: str | None = None) -> pd.DataFrame:
    os.makedirs(DATA_DIR, exist_ok=True)
    if mock:
        df = mock_history(mock)
    else:
        df = fetch_all(date.today() - timedelta(days=LOOKBACK_DAYS))
    df = add_derived(df)
    df.index.name = "date"
    df.to_csv(HISTORY_CSV, float_format="%.6g")
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    mock = None
    if "--mock" in sys.argv:
        i = sys.argv.index("--mock")
        mock = sys.argv[i + 1] if len(sys.argv) > i + 1 and not sys.argv[i + 1].startswith("-") else "calm"
    df = load_history(mock)
    snap = build_snapshot(df)
    for k, v in snap.items():
        print(f"{k:14s} {v['value']:>12.4g} 1d={v['chg_1d']} 1w={v['chg_1w']} 1m={v['chg_1m']} pct={v['pct_1y']}")
