"""ATR 기반 손절 (스펙 24조 "손절은 가격 구조와 ATR을 우선한다").

RANGE-MR의 STOP(support zone 하단 - 0.5*ATR, 13조)과 V-REBOUND의 STOP
(확정 저점 - 0.5*ATR, Phase 5 제안)이 공유하는 공식이다. "가격 구조 기준점"
(reference_price)만 전략마다 다르다.
"""
from __future__ import annotations

import pandas as pd


def compute_atr_stop(reference_price: pd.Series, atr: pd.Series, atr_multiplier: float) -> pd.Series:
    return reference_price - atr_multiplier * atr
