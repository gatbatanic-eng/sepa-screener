"""변동성/가격범위 지표: True Range, ATR, 롤링 고저, 박스폭·포지션, 드로다운.

박스(7.2조)와 시장 레짐의 밴드폭(market_regime.range)은 동일한 "구간 고저폭"
계산을 공유하므로 range_width/rolling_high/rolling_low로 통일한다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def true_range(high: pd.Series, low: pd.Series, prev_close: pd.Series) -> pd.Series:
    """True Range = max(high-low, |high-prev_close|, |low-prev_close|).

    prev_close가 NaN인 첫 구간은 high-low만 사용한다.
    """
    hl = high - low
    hc = (high - prev_close).abs()
    lc = (low - prev_close).abs()
    tr = pd.concat([hl, hc, lc], axis=1).max(axis=1, skipna=True)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """Wilder's ATR. 표준 정의(단순 SMA가 아닌 Wilder 평활)를 사용한다.

    시드값(첫 유효값)은 첫 period개 True Range의 단순평균이고, 그 이후는
    atr[i] = (atr[i-1]*(period-1) + tr[i]) / period 로 재귀 평활한다.
    """
    tr = true_range(high, low, close.shift(1))
    result = pd.Series(np.nan, index=tr.index, dtype="float64")
    if len(tr) < period:
        return result

    seed_pos = period - 1
    seed = tr.iloc[0:period].mean()
    result.iloc[seed_pos] = seed

    prev = seed
    for i in range(period, len(tr)):
        current_tr = tr.iloc[i]
        if pd.isna(current_tr) or pd.isna(prev):
            prev = np.nan
            continue
        prev = (prev * (period - 1) + current_tr) / period
        result.iloc[i] = prev
    return result


def rolling_high(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).max()


def rolling_low(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).min()


def range_width(period_high: pd.Series, period_low: pd.Series) -> pd.Series:
    """(고가-저가)/저가. 저가가 0이면 계산 불가하므로 NaN (스펙 7.2조 BOX_WIDTH)."""
    with np.errstate(divide="ignore", invalid="ignore"):
        width = (period_high - period_low) / period_low
    return width.mask(period_low <= 0)


def box_position(price: pd.Series, box_high: pd.Series, box_low: pd.Series) -> pd.Series:
    """(price-box_low)/(box_high-box_low). 박스폭이 0이면 계산 불가하므로 NaN."""
    span = box_high - box_low
    with np.errstate(divide="ignore", invalid="ignore"):
        position = (price - box_low) / span
    return position.mask(span == 0)


def drawdown_from_high(price: pd.Series, period_high: pd.Series) -> pd.Series:
    """price/period_high - 1. 항상 <= 0 (period_high가 price를 포함하는 구간 최고가라는 전제).

    period_high가 0이면 계산 불가하므로 NaN (스펙 15조 60일 고점 대비 하락률).
    """
    with np.errstate(divide="ignore", invalid="ignore"):
        drawdown = price / period_high - 1.0
    return drawdown.mask(period_high == 0)
