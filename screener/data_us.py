"""미국 유니버스(S&P 500)를 yfinance 로 가져와 표준 스키마로 변환.

yfinance 한계 (STRATEGY.md 6절):
  - `.info` 는 종목별 개별 API 호출이라 느리고 간헐적으로 429(rate-limit) → 재시도
  - 기본 종목 수 제한(max_tickers). 진행 상황 주기 출력.
  - 주주수익률은 배당수익률만 반영(자사주매입률은 무료 소스로 신뢰도 낮아 제외)
"""
from __future__ import annotations

import time

import numpy as np
import pandas as pd

from . import schema

MIN_MARCAP_USD = 2_000_000_000  # 20억 달러
_TRADING_6M = 126
_TRADING_12M = 252

# fdr.StockListing('S&P500') 은 복수클래스 종목의 점(.)을 제거해 내려주는데
# yfinance 는 하이픈 표기를 요구한다.
_TICKER_OVERRIDES = {"BRKB": "BRK-B", "BFB": "BF-B"}


def _sp500_universe() -> pd.DataFrame:
    import FinanceDataReader as fdr

    listing = fdr.StockListing("S&P500")
    listing = listing.rename(columns={"Symbol": "symbol", "Name": "name"})
    listing["symbol"] = listing["symbol"].replace(_TICKER_OVERRIDES)
    listing = listing.dropna(subset=["symbol", "name"])
    return listing[["symbol", "name"]].reset_index(drop=True)


def load(
    max_tickers: int | None = 120,
    min_marcap: float = MIN_MARCAP_USD,
    verbose: bool = True,
) -> pd.DataFrame:
    import yfinance as yf

    uni = _sp500_universe()
    if max_tickers:
        uni = uni.head(max_tickers)
    total = len(uni)
    if verbose:
        print(f"[US] S&P500 상위 {total}종목, 시총 하한 ${min_marcap/1e9:,.1f}B, 조회 시작...")

    rows: list[dict] = []
    for i, rec in enumerate(uni.itertuples(index=False), 1):
        sym = rec.symbol
        info, hist = _fetch_one(yf, sym)
        if info is None or hist is None or hist.empty:
            if verbose:
                print(f"  [{sym}] 데이터 없음, 건너뜀")
            continue

        close = hist["Close"].dropna().astype(float)
        if len(close) < 200:
            continue

        marcap = _num(info.get("marketCap"))
        if marcap is not None and marcap < min_marcap:
            if verbose and i % 20 == 0:
                print(f"  ...{i}/{total}")
            continue

        price = float(close.iloc[-1])
        sma200 = float(close.iloc[-200:].mean())

        roe = _num(info.get("returnOnEquity"))
        roe = roe * 100.0 if roe is not None else np.nan

        d2e = _num(info.get("debtToEquity"))
        if d2e is not None and d2e < 5:   # 비율로 내려온 경우(예: 1.5) → 퍼센트로
            d2e *= 100.0

        eps = _num(info.get("trailingEps"))
        nic = _num(info.get("netIncomeToCommon"))
        net_pos = bool((eps is not None and eps > 0) or (nic is not None and nic > 0))

        rows.append(dict(
            symbol=sym,
            name=info.get("shortName") or rec.name,
            market="US",
            price=price,
            sma200=sma200,
            ret_6m=_ret(close, _TRADING_6M),
            ret_12m=_ret(close, _TRADING_12M),
            per=_pos(info.get("trailingPE")),
            pbr=_pos(info.get("priceToBook")),
            ev_ebitda=_pos(info.get("enterpriseToEbitda")),
            shareholder_yield=_div_yield(info),
            roe=roe,
            debt_to_equity=d2e if d2e is not None else np.nan,
            net_income_positive=net_pos,
        ))
        if verbose and i % 20 == 0:
            print(f"  ...{i}/{total} 처리 (수집 {len(rows)})")

    out = pd.DataFrame(rows)
    if verbose:
        print(f"[US] 완료: {len(out)}종목")
    return schema.validate(out if not out.empty else schema.empty_frame())


def _fetch_one(yf, sym: str, retries: int = 3):
    for attempt in range(1, retries + 1):
        try:
            t = yf.Ticker(sym)
            hist = t.history(period="2y", auto_adjust=True)
            info = t.info or {}
            return info, hist
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            wait = 2.0 * attempt + (5.0 if "429" in msg or "too many" in msg else 0)
            time.sleep(wait)
    return None, None


def _div_yield(info: dict) -> float:
    y = _num(info.get("dividendYield"))
    if y is None:
        y = _num(info.get("trailingAnnualDividendYield"))
    if y is None:
        return np.nan
    return y / 100.0 if y > 1 else y   # yfinance 버전별로 % 또는 소수


def _ret(close: pd.Series, n: int) -> float:
    if len(close) <= n:
        return np.nan
    return float(close.iloc[-1] / close.iloc[-1 - n] - 1.0)


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f


def _pos(v) -> float:
    f = _num(v)
    return f if (f is not None and f > 0) else np.nan
