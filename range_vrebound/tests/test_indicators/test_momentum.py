import pandas as pd
import pytest

from src.indicators.momentum import rsi


def test_rsi_all_gains_is_100():
    close = pd.Series([100, 101, 102, 103, 104, 105], dtype=float)
    result = rsi(close, period=3)
    assert result.iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_0():
    close = pd.Series([105, 104, 103, 102, 101, 100], dtype=float)
    result = rsi(close, period=3)
    assert result.iloc[-1] == pytest.approx(0.0)


def test_rsi_flat_series_is_50():
    close = pd.Series([100.0] * 6)
    result = rsi(close, period=3)
    assert result.iloc[-1] == pytest.approx(50.0)


def test_rsi_bounded_between_0_and_100():
    close = pd.Series([100, 98, 103, 95, 110, 90, 120], dtype=float)
    result = rsi(close, period=3).dropna()
    assert (result >= 0).all()
    assert (result <= 100).all()


def test_rsi_insufficient_history_is_nan():
    close = pd.Series([100, 101], dtype=float)
    result = rsi(close, period=14)
    assert result.isna().all()
