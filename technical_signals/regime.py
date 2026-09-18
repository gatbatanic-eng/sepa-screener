"""
technical_signals/regime.py — 시장 국면 게이트
==================================================

개별 종목 신호와는 별개로, 시장 지수·breadth(개별 종목 신호 무시 위험 —
2026-09-18 리뷰 5번)를 본다. 펀더멘털을 섞는 게 아니라 시장 자체의 기술적
상태(지수 이평선 배열 + breadth)만 보므로 technical_signals의 "순수 기술적
지표" 원칙과 충돌하지 않는다. SEPA `sepa/regime.py`와 같은 철학(3조건 카운트
→ GREEN/YELLOW/RED)이지만 독립 재구현이다.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import config as cfg
from indicators import sma

GREEN = "GREEN"
YELLOW = "YELLOW"
RED = "RED"


@dataclass
class RegimeResult:
    index_close: float | None = None
    index_sma50: float | None = None
    index_sma200: float | None = None
    above_sma200: bool | None = None
    sma50_above_sma200: bool | None = None
    breadth: float | None = None
    breadth_ok: bool | None = None
    regime: str | None = None  # GREEN/YELLOW/RED, 판정 불가면 None(억지 값 아님)


def evaluate_regime(index_ohlcv: pd.DataFrame | None, breadth: float | None) -> RegimeResult:
    """index_ohlcv: 지수 OHLCV(Close 컬럼 필요). breadth: 유니버스 중 SMA50 위 종목 비율(0~1)."""
    r = RegimeResult()

    if index_ohlcv is not None and len(index_ohlcv) >= cfg.MA_SLOW:
        close = index_ohlcv["Close"].astype(float)
        sma50 = sma(close, cfg.MA_FAST)
        sma200 = sma(close, cfg.MA_SLOW)
        if not pd.isna(sma50.iloc[-1]) and not pd.isna(sma200.iloc[-1]):
            r.index_close = float(close.iloc[-1])
            r.index_sma50 = round(float(sma50.iloc[-1]), 4)
            r.index_sma200 = round(float(sma200.iloc[-1]), 4)
            r.above_sma200 = r.index_close > r.index_sma200
            r.sma50_above_sma200 = r.index_sma50 > r.index_sma200

    if breadth is not None:
        r.breadth = round(breadth, 4)
        r.breadth_ok = r.breadth >= cfg.BREADTH_GREEN_MIN

    conds = [r.above_sma200, r.sma50_above_sma200, r.breadth_ok]
    if any(c is None for c in conds):
        r.regime = None
    else:
        count = sum(1 for c in conds if c)
        r.regime = GREEN if count == 3 else (RED if count == 0 else YELLOW)

    return r


def compute_breadth(above_sma50_flags: list[bool]) -> float | None:
    """OK 판정 종목 중 SMA50 위에 있는 비율(0~1). 대상이 없으면 None."""
    if not above_sma50_flags:
        return None
    return sum(1 for v in above_sma50_flags if v) / len(above_sma50_flags)
