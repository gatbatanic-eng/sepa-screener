"""
sepa/universe.py — 유니버스 선정
================================

한국 유니버스를 두 모드로 뽑을 수 있다 (스펙 4조).

- ``legacy_market_cap`` : 기존 방식 — 코스피+코스닥 시가총액 상위 N (기본 200)
- ``liquidity`` (기본) : 최근 20거래일 평균 거래대금 기준 상위 N (기본 600)

**liquidity 모드 구현 메모 / 한계**
- FDR ``StockListing('KRX')`` 는 단일일 스냅샷(거래대금 ``Amount``)만 준다.
  20거래일 평균 거래대금을 유니버스 선정 *전에* 알 수는 없다.
- 그래서 2단계로 한다:
  1. 단일일 거래대금(없으면 시총) 상위 ``candidate_n`` 개를 후보로 조회.
  2. 조회 후 종목별 20거래일 평균 거래대금을 계산해 상위 ``top_n`` 을 최종
     유니버스(``in_universe=True``)로 확정. 나머지는 결과에 남되 RS percentile
     모수·통과 집계에서 제외.

**제외**
- 우선주 / 스팩 : 종목명 기반으로 제외 (아래 휴리스틱).
- 거래정지 / 관리종목 / 위험종목 : FDR 리스팅에서 신뢰성 있게 판별할 수 없어
  구현하지 않는다. (로그로 한계를 남긴다.) 극단적 저유동성은 20일 평균
  거래대금 floor 로 걸러진다.

미국은 S&P500 구성종목을 그대로 쓴다.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from sepa.config import UniverseConfig

log = logging.getLogger("sepa.universe")

# 종목명 접미/키워드 기반 휴리스틱 (FDR 이름 컬럼 기준)
_PREFERRED_SUFFIXES = ("우", "우B", "우C", "우(전환)", "(전환)", "3우B", "2우B", "1우")
_SPAC_KEYWORDS = ("스팩", "기업인수목적")


def is_preferred_kr(name: str | None) -> bool:
    if not name:
        return False
    nm = str(name).strip()
    return nm.endswith(_PREFERRED_SUFFIXES)


def is_spac_kr(name: str | None) -> bool:
    if not name:
        return False
    nm = str(name)
    return any(kw in nm for kw in _SPAC_KEYWORDS)


def _liquidity_proxy_column(listing: pd.DataFrame) -> str | None:
    """단일일 거래대금 컬럼을 찾는다 (FDR 버전에 따라 'Amount' / 'TradingValue' 등)."""
    for col in ("Amount", "TradingValue", "AccTradeValue", "거래대금"):
        if col in listing.columns:
            return col
    return None


def select_kr_candidates(listing: pd.DataFrame, cfg: UniverseConfig) -> pd.DataFrame:
    """
    한국 후보 유니버스를 반환한다. 컬럼: Code, Name, Market, Marcap.

    liquidity 모드면 candidate_n 개, legacy 모드면 market_cap_top_n 개.
    우선주/스팩은 제외한다.
    """
    df = listing.copy()
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ", "KOSDAQ GLOBAL"])]
    df["Market"] = df["Market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})

    n0 = len(df)
    if cfg.exclude_preferred:
        df = df[~df["Name"].map(is_preferred_kr)]
    if cfg.exclude_spac:
        df = df[~df["Name"].map(is_spac_kr)]
    log.info("KR 유니버스: 우선주/스팩 제외 %d → %d", n0, len(df))
    log.info("KR 유니버스 한계: 거래정지·관리·위험종목은 FDR 리스팅으로 신뢰성 있게 "
             "판별 불가 — 제외하지 않음 (20일 평균 거래대금 floor 로 저유동성만 완화).")

    if cfg.kr_mode == "legacy_market_cap":
        df = df.dropna(subset=["Marcap"]).sort_values("Marcap", ascending=False)
        df = df.head(cfg.kr_market_cap_top_n)
        log.info("KR 유니버스 모드=legacy_market_cap, 시총 상위 %d", len(df))
    else:
        proxy = _liquidity_proxy_column(df)
        if proxy:
            df = df.dropna(subset=[proxy]).sort_values(proxy, ascending=False)
            log.info("KR 유니버스 모드=liquidity, 후보 선별 기준=%s(단일일)", proxy)
        else:
            df = df.dropna(subset=["Marcap"]).sort_values("Marcap", ascending=False)
            log.warning("KR 유니버스 모드=liquidity 이나 거래대금 컬럼이 없어 시총으로 후보 선별 "
                        "(단일일 거래대금 근사 불가)")
        df = df.head(cfg.kr_liquidity_candidate_n)

    out = df[["Code", "Name", "Market"]].copy()
    out["Marcap"] = df["Marcap"] if "Marcap" in df.columns else np.nan
    return out.reset_index(drop=True)


def finalize_liquidity_universe(avg_trading_value_by_code: dict[str, float | None],
                                cfg: UniverseConfig) -> set[str]:
    """
    {code: 20일 평균 거래대금(원)} → 최종 유니버스에 들 code 집합.

    - floor 미만(또는 None) 은 제외.
    - 나머지 중 상위 ``kr_liquidity_top_n`` 개.
    legacy 모드면 floor·top_n 을 적용하지 않고 전부 포함(호출부에서 이 함수 자체를
    건너뛰면 됨).
    """
    valid = {c: v for c, v in avg_trading_value_by_code.items()
             if v is not None and v >= cfg.kr_min_avg_trading_value_krw}
    ranked = sorted(valid, key=lambda c: valid[c], reverse=True)
    keep = set(ranked[: cfg.kr_liquidity_top_n])
    log.info("KR 유니버스 확정: 20일 평균 거래대금 floor(%.0f원) 통과 %d개 중 상위 %d개",
             cfg.kr_min_avg_trading_value_krw, len(valid), len(keep))
    return keep


def avg_trading_value_20(close: pd.Series, volume: pd.Series, window: int = 20) -> float | None:
    """최근 window 거래일 평균 거래대금 = mean(close * volume). 데이터 부족 시 None."""
    if close is None or volume is None or len(close) < window:
        return None
    tv = (close.astype(float) * volume.astype(float)).tail(window)
    if tv.isna().any():
        tv = tv.dropna()
    if len(tv) < max(5, window // 2):
        return None
    return float(tv.mean())
