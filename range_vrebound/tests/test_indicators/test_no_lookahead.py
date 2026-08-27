"""데이터 누수 금지 (스펙 2.3조) 회귀 테스트.

각 지표는 인덱스 i까지만 잘라서 계산하든, 전체 시계열로 계산하든 인덱스 i의
값이 동일해야 한다. 미래 데이터가 과거 시점의 계산값에 영향을 주면 안 된다.
"""
import numpy as np
import pandas as pd
import pytest

from src.indicators.momentum import rsi
from src.indicators.trend import sma
from src.indicators.volatility import atr, rolling_high, rolling_low
from src.indicators.volume import avg_volume

np.random.seed(42)
_CLOSE = pd.Series(100 + np.cumsum(np.random.randn(40)))
_HIGH = _CLOSE + np.random.rand(40) * 2
_LOW = _CLOSE - np.random.rand(40) * 2
_VOLUME = pd.Series(1000 + np.random.rand(40) * 500)

CUT_INDEX = 25


def _assert_causal(full_series: pd.Series, truncated_series: pd.Series, at: int):
    full_value = full_series.iloc[at]
    truncated_value = truncated_series.iloc[at]
    if pd.isna(full_value):
        assert pd.isna(truncated_value)
    else:
        assert truncated_value == pytest.approx(full_value)


def test_sma_is_causal():
    full = sma(_CLOSE, period=5)
    truncated = sma(_CLOSE.iloc[: CUT_INDEX + 1], period=5)
    _assert_causal(full, truncated, CUT_INDEX)


def test_rolling_high_low_are_causal():
    full_high = rolling_high(_CLOSE, period=10)
    trunc_high = rolling_high(_CLOSE.iloc[: CUT_INDEX + 1], period=10)
    _assert_causal(full_high, trunc_high, CUT_INDEX)

    full_low = rolling_low(_CLOSE, period=10)
    trunc_low = rolling_low(_CLOSE.iloc[: CUT_INDEX + 1], period=10)
    _assert_causal(full_low, trunc_low, CUT_INDEX)


def test_avg_volume_is_causal():
    full = avg_volume(_VOLUME, period=20)
    truncated = avg_volume(_VOLUME.iloc[: CUT_INDEX + 1], period=20)
    _assert_causal(full, truncated, CUT_INDEX)


def test_rsi_is_causal():
    full = rsi(_CLOSE, period=14)
    truncated = rsi(_CLOSE.iloc[: CUT_INDEX + 1], period=14)
    _assert_causal(full, truncated, CUT_INDEX)


def test_atr_is_causal():
    close_shifted = _CLOSE.shift(1)
    full = atr(_HIGH, _LOW, _CLOSE, period=14)
    trunc = atr(_HIGH.iloc[: CUT_INDEX + 1], _LOW.iloc[: CUT_INDEX + 1], _CLOSE.iloc[: CUT_INDEX + 1], period=14)
    _assert_causal(full, trunc, CUT_INDEX)
