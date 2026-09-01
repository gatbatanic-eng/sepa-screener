"""한국 유니버스(KOSPI+KOSDAQ)를 표준 스키마로 변환.

데이터 소스 (모두 무료 공개):
  - 유니버스·시가총액·가격이력:  FinanceDataReader (KRX/네이버 백엔드)
  - 재무지표(PER/PBR/ROE/부채비율/배당수익률/순이익):  네이버 금융 모바일 API

주: 당초 명세는 pykrx 를 지정했으나, KRX 데이터포털이 비로그인 JSON 요청을
    차단(HTTP 400 "LOGOUT")하도록 바뀌어 pykrx 1.2.7 이 동작하지 않는다.
    동일하게 무료·공개이면서 이 저장소가 이미 쓰고 있는 FinanceDataReader +
    네이버 금융으로 대체했다. 표준 스키마 출력은 명세 그대로다.
    (pykrx 가 복구되면 data_kr.load 내부만 교체하면 된다.)
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import requests

from . import schema

MIN_MARCAP_KRW = 1_000 * 100_000_000  # 1,000억 원
_TRADING_6M = 126
_TRADING_12M = 252
_HISTORY_CAL_DAYS = 430

_NAVER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
                  "AppleWebKit/605.1.15 (KHTML, like Gecko) Mobile/15E148 Safari/604.1",
    "Referer": "https://m.stock.naver.com/",
}
_EXCLUDE_NAME_TOKENS = ("스팩", "리츠")


def load(
    max_tickers: int | None = 300,
    min_marcap: float = MIN_MARCAP_KRW,
    verbose: bool = True,
) -> pd.DataFrame:
    import FinanceDataReader as fdr

    listing = fdr.StockListing("KRX")
    listing = listing[listing["Market"].isin(["KOSPI", "KOSDAQ"])].copy()
    listing = listing.dropna(subset=["Marcap", "Code", "Name"])
    listing = listing[listing["Marcap"] >= min_marcap]
    listing = listing[listing["Code"].astype(str).str.endswith("0")]  # 보통주
    listing = listing[~listing["Name"].str.contains("|".join(_EXCLUDE_NAME_TOKENS))]
    listing = listing.sort_values("Marcap", ascending=False).reset_index(drop=True)
    if max_tickers:
        listing = listing.head(max_tickers)

    if verbose:
        print(f"[KR] 유니버스 {len(listing)}종목 "
              f"(시총 ≥ {min_marcap/1e8:,.0f}억), 데이터 수집 시작...")

    session = requests.Session()
    session.headers.update(_NAVER_HEADERS)
    start = (datetime.now() - timedelta(days=_HISTORY_CAL_DAYS)).strftime("%Y-%m-%d")

    rows: list[dict] = []
    for i, rec in enumerate(listing.itertuples(index=False), 1):
        code = str(rec.Code)
        try:
            hist = fdr.DataReader(code, start)
            close = hist["Close"].dropna().astype(float)
            close = close[close > 0]
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  [{code}] 가격 조회 실패: {e}")
            continue
        if len(close) < 200:
            continue

        fund = _naver_fundamentals(session, code)
        price = float(close.iloc[-1])
        rows.append(dict(
            symbol=code,
            name=rec.Name,
            market=rec.Market,
            price=price,
            sma200=float(close.iloc[-200:].mean()),
            ret_6m=_ret(close, _TRADING_6M),
            ret_12m=_ret(close, _TRADING_12M),
            per=fund["per"],
            pbr=fund["pbr"],
            ev_ebitda=np.nan,  # 무료 소스로 신뢰 가능한 값 없음
            shareholder_yield=fund["shareholder_yield"],
            roe=fund["roe"],
            debt_to_equity=fund["debt_to_equity"],
            net_income_positive=fund["net_income_positive"],
        ))
        if verbose and i % 25 == 0:
            print(f"  ...{i}/{len(listing)} 처리 (수집 {len(rows)})")
        time.sleep(0.1)

    out = pd.DataFrame(rows) if rows else schema.empty_frame()
    if verbose:
        print(f"[KR] 완료: {len(out)}종목")
    return schema.validate(out)


# ---------------------------------------------------------------------------
def _naver_fundamentals(session: requests.Session, code: str) -> dict:
    blank = dict(per=np.nan, pbr=np.nan, roe=np.nan, debt_to_equity=np.nan,
                 shareholder_yield=np.nan, net_income_positive=False)
    try:
        integ = session.get(
            f"https://m.stock.naver.com/api/stock/{code}/integration", timeout=12
        ).json()
    except Exception:  # noqa: BLE001
        integ = {}
    info = {t.get("code"): t.get("value") for t in integ.get("totalInfos", [])}
    per = _num(info.get("per"))
    pbr = _num(info.get("pbr"))
    div_yield = _num(info.get("dividendYieldRatio"))

    roe = d2e = np.nan
    net_pos = False
    try:
        fin = session.get(
            f"https://m.stock.naver.com/api/stock/{code}/finance/annual", timeout=12
        ).json()["financeInfo"]
        # 최신 '실적'(컨센서스 아님) 컬럼 키
        actual_keys = [c["key"] for c in fin["trTitleList"] if c.get("isConsensus") != "Y"]
        latest = actual_keys[-1] if actual_keys else None
        rowmap = {r["title"]: r["columns"] for r in fin["rowList"]}
        if latest:
            roe = _pick(rowmap, "ROE", latest)
            d2e = _pick(rowmap, "부채비율", latest)
            ni = _pick(rowmap, "당기순이익", latest)
            if not np.isnan(ni):
                net_pos = ni > 0
    except Exception:  # noqa: BLE001
        pass

    if np.isnan(roe):
        return {**blank, "per": per, "pbr": pbr,
                "shareholder_yield": div_yield / 100.0 if not np.isnan(div_yield) else np.nan}

    return dict(
        per=per, pbr=pbr, roe=roe, debt_to_equity=d2e,
        shareholder_yield=div_yield / 100.0 if not np.isnan(div_yield) else np.nan,
        net_income_positive=bool(net_pos),
    )


def _pick(rowmap: dict, title: str, key: str) -> float:
    col = rowmap.get(title, {})
    cell = col.get(key)
    if isinstance(cell, dict):
        return _num(cell.get("value"))
    return _num(cell)


def _num(v) -> float:
    if v is None:
        return np.nan
    s = str(v).strip().replace(",", "").replace("배", "").replace("원", "").replace("%", "")
    if s in ("", "-", "N/A"):
        return np.nan
    try:
        return float(s)
    except ValueError:
        return np.nan


def _ret(close: pd.Series, n: int) -> float:
    if len(close) <= n:
        return np.nan
    return float(close.iloc[-1] / close.iloc[-1 - n] - 1.0)
