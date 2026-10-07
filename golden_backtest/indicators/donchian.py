"""돈치안 채널. 반드시 '직전 n봉'(오늘 제외)이다 — 오늘 봉을 포함하면 돌파 조건이 자기 자신과 비교된다."""
from __future__ import annotations

import pandas as pd


def donchian_high(high: pd.Series, n: int) -> pd.Series:
    """[원전] t일 값 = t-n ~ t-1일 최고가 (터틀 55일 돌파 등). 오늘 봉 제외."""
    return high.shift(1).rolling(n, min_periods=n).max()


def donchian_low(low: pd.Series, n: int) -> pd.Series:
    """[원전] t일 값 = t-n ~ t-1일 최저가 (터틀 20일 이탈 등). 오늘 봉 제외."""
    return low.shift(1).rolling(n, min_periods=n).min()
