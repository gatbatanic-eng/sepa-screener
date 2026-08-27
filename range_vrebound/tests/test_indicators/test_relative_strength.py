import pandas as pd
import pytest

from src.indicators.relative_strength import excess_return, period_return


def test_period_return_basic():
    price = pd.Series([100.0, 100.0, 100.0, 120.0])
    result = period_return(price, period=3)
    assert pd.isna(result.iloc[0])
    assert pd.isna(result.iloc[1])
    assert pd.isna(result.iloc[2])
    assert result.iloc[3] == pytest.approx(0.20)


def test_excess_return_positive_and_negative():
    stock = pd.Series([100.0, 100.0, 90.0])   # 5D 수익률 -10%
    market = pd.Series([100.0, 100.0, 95.0])  # 5D 수익률 -5%
    result = excess_return(stock, market, period=2)
    assert result.iloc[2] == pytest.approx(-0.05)


def test_excess_return_boundary_minus_10pp():
    # 스펙 15조 경계값: 종목-시장 60일 수익률 <= -10%p
    stock = pd.Series([100.0, 80.0])   # -20%
    market = pd.Series([100.0, 90.0])  # -10%
    result = excess_return(stock, market, period=1)
    assert result.iloc[1] == pytest.approx(-0.10)
