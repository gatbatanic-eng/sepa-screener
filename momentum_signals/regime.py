"""momentum_signals/regime.py — 시장 레짐 게이트(스펙 8번).

코스피/S&P500이 각각 20일 이평 아래면 해당 시장 신규진입을 보류한다.
다른 서브시스템의 regime.py(GREEN/YELLOW/RED 3단계)와 달리 스펙이 2단계
(허용/보류)만 요구해서 그대로 2단계로 구현한다."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

import config as cfg
from indicators import sma


@dataclass
class RegimeResult:
    index_close: float | None = None
    index_sma: float | None = None
    above_ma: bool | None = None   # True=신규진입 허용, False=보류, None=판정불가

    @property
    def allow_new_entry(self) -> bool | None:
        return self.above_ma


def evaluate_regime(index_ohlcv: pd.DataFrame) -> RegimeResult:
    r = RegimeResult()
    if index_ohlcv is None or len(index_ohlcv) < cfg.REGIME_MA_PERIOD:
        return r
    close = index_ohlcv["Close"].astype(float)
    ma = sma(close, cfg.REGIME_MA_PERIOD)
    if pd.isna(ma.iloc[-1]):
        return r
    r.index_close = round(float(close.iloc[-1]), 4)
    r.index_sma = round(float(ma.iloc[-1]), 4)
    r.above_ma = r.index_close >= r.index_sma
    return r
