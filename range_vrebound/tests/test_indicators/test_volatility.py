import numpy as np
import pandas as pd
import pytest

from src.indicators.volatility import (
    atr,
    box_position,
    drawdown_from_high,
    range_width,
    rolling_high,
    rolling_low,
    true_range,
)


def test_true_range_picks_max_of_three_components():
    high = pd.Series([105.0, 108.0, 100.0])
    low = pd.Series([100.0, 103.0, 90.0])
    prev_close = pd.Series([np.nan, 105.0, 103.0], dtype="float64")
    result = true_range(high, low, prev_close)
    # idx0: prev_close NaN -> high-low = 5
    assert result.iloc[0] == 5.0
    # idx1: high-low=5, |high-prevclose|=3, |low-prevclose|=2 -> max=5
    assert result.iloc[1] == 5.0
    # idx2: high-low=10, |high-prevclose|=|100-103|=3, |low-prevclose|=|90-103|=13 -> max=13
    assert result.iloc[2] == 13.0


def test_atr_matches_hand_calculation():
    # TR 시퀀스: 1,2,3,4,5,6 / period=3
    # 첫 ATR(idx2) = 단순평균(1,2,3) = 2.0
    # 이후 Wilder 평활: atr[i] = (atr[i-1]*(period-1) + tr[i]) / period
    high = pd.Series([10, 12, 15, 19, 24, 30], dtype=float)
    low = pd.Series([9, 10, 12, 15, 19, 24], dtype=float)
    close = pd.Series([9.5, 11.0, 14.0, 18.0, 23.0, 29.0], dtype=float)
    result = atr(high, low, close, period=3)
    tr = true_range(high, low, close.shift(1))
    expected_seed = tr.iloc[0:3].mean()
    assert result.iloc[2] == pytest.approx(expected_seed)
    expected_next = (expected_seed * 2 + tr.iloc[3]) / 3
    assert result.iloc[3] == pytest.approx(expected_next)


def test_rolling_high_low_basic():
    series = pd.Series([1, 5, 3, 8, 2], dtype=float)
    high = rolling_high(series, period=3)
    low = rolling_low(series, period=3)
    assert high.iloc[2] == 5.0
    assert high.iloc[4] == 8.0
    assert low.iloc[2] == 1.0
    assert low.iloc[4] == 2.0


def test_range_width_known_value():
    # (high-low)/low = (115-100)/100 = 0.15 -> 스펙 7.2조 하한 경계값
    assert range_width(pd.Series([115.0]), pd.Series([100.0])).iloc[0] == pytest.approx(0.15)


def test_range_width_nan_when_low_is_zero():
    result = range_width(pd.Series([100.0]), pd.Series([0.0]))
    assert pd.isna(result.iloc[0])


def test_box_position_at_low_and_high_boundaries():
    box_high = pd.Series([120.0])
    box_low = pd.Series([100.0])
    assert box_position(pd.Series([100.0]), box_high, box_low).iloc[0] == 0.0
    assert box_position(pd.Series([120.0]), box_high, box_low).iloc[0] == 1.0
    # 스펙 7.2조 경계값: position_max=0.25 -> 105는 통과 경계선
    assert box_position(pd.Series([105.0]), box_high, box_low).iloc[0] == pytest.approx(0.25)


def test_box_position_nan_when_box_has_zero_width():
    result = box_position(pd.Series([100.0]), pd.Series([100.0]), pd.Series([100.0]))
    assert pd.isna(result.iloc[0])


def test_drawdown_from_high_zero_at_high():
    price = pd.Series([80.0, 100.0])
    period_high = pd.Series([100.0, 100.0])
    result = drawdown_from_high(price, period_high)
    assert result.iloc[1] == 0.0
    assert result.iloc[0] == pytest.approx(-0.20)


def test_drawdown_from_high_exact_boundary_value():
    price = pd.Series([75.0])
    period_high = pd.Series([100.0])
    result = drawdown_from_high(price, period_high)
    assert result.iloc[0] == pytest.approx(-0.25)


def test_atr_insufficient_history_is_all_nan():
    high = pd.Series([10.0, 11.0])
    low = pd.Series([9.0, 9.5])
    close = pd.Series([9.5, 10.5])
    result = atr(high, low, close, period=14)
    assert result.isna().all()


def test_atr_propagates_nan_when_true_range_is_undefined():
    # index3: high/low가 모두 NaN이면 True Range의 세 성분(hl/hc/lc)이
    # 전부 NaN이 되어 그날 TR 자체가 NaN이다.
    high = pd.Series([10.0, 12.0, 15.0, np.nan, 24.0, 30.0], dtype=float)
    low = pd.Series([9.0, 10.0, 12.0, np.nan, 19.0, 24.0], dtype=float)
    close = pd.Series([9.5, 11.0, 14.0, 18.0, 23.0, 29.0], dtype=float)
    result = atr(high, low, close, period=3)
    # Wilder 평활은 재귀식이라 한 번 TR이 끊기면 그 이후 값도 회복되지
    # 못하고 NaN으로 전파된다 (이어붙일 기준값이 없음).
    assert pd.isna(result.iloc[-1])
