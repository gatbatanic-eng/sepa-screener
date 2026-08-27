"""모멘텀 보조지표: RSI (스펙 9조 — RSI는 보조지표로만 사용, 매수조건 핵심 아님).

Wilder's RSI 표준 정의를 사용한다 (평균 상승폭/하락폭을 Wilder 평활로 계산).
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)

    avg_gain = _wilder_smoothing(gain, period)
    avg_loss = _wilder_smoothing(loss, period)

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        result = 100.0 - (100.0 / (1.0 + rs))

    # avg_loss == 0: 상승만 있었으면 100, 둘 다 0(횡보)이면 관례상 50.
    both_zero = (avg_gain == 0) & (avg_loss == 0)
    only_loss_zero = (avg_loss == 0) & (avg_gain > 0)
    result = result.mask(only_loss_zero, 100.0)
    result = result.mask(both_zero, 50.0)
    return result


def _wilder_smoothing(series: pd.Series, period: int) -> pd.Series:
    """시드값(첫 유효값)은 첫 period개의 단순평균, 이후는 Wilder 재귀 평활."""
    result = pd.Series(np.nan, index=series.index, dtype="float64")
    if len(series) <= period:
        return result

    seed_pos = period
    seed = series.iloc[1 : period + 1].mean()  # 첫 값(diff의 NaN)은 제외
    result.iloc[seed_pos] = seed

    prev = seed
    for i in range(period + 1, len(series)):
        current = series.iloc[i]
        prev = (prev * (period - 1) + current) / period
        result.iloc[i] = prev
    return result
