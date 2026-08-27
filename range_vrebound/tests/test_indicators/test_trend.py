import pandas as pd

from src.indicators.trend import distance_from_ma, is_ma_rising, sma


def test_sma_basic_values():
    close = pd.Series([1, 2, 3, 4, 5], dtype=float)
    result = sma(close, period=3)
    assert pd.isna(result.iloc[0])
    assert pd.isna(result.iloc[1])
    assert result.iloc[2] == 2.0
    assert result.iloc[3] == 3.0
    assert result.iloc[4] == 4.0


def test_sma_insufficient_history_is_nan():
    close = pd.Series([1, 2], dtype=float)
    result = sma(close, period=5)
    assert result.isna().all()


def test_is_ma_rising_true_when_strictly_increasing():
    ma = pd.Series([10, 11, 12, 13, 14], dtype=float)
    result = is_ma_rising(ma, lookback=2)
    assert result.iloc[4] == True  # noqa: E712 (14 > 12)


def test_is_ma_rising_false_on_flat_boundary():
    # 정확히 같은 값이면 "상승 중"이 아니다 (엄격 부등호 기준).
    ma = pd.Series([10, 10, 10, 10], dtype=float)
    result = is_ma_rising(ma, lookback=2)
    assert result.iloc[3] == False  # noqa: E712


def test_is_ma_rising_false_when_decreasing():
    ma = pd.Series([14, 13, 12, 11, 10], dtype=float)
    result = is_ma_rising(ma, lookback=2)
    assert result.iloc[4] == False  # noqa: E712


def test_is_ma_rising_na_when_insufficient_history():
    ma = pd.Series([10, 11], dtype=float)
    result = is_ma_rising(ma, lookback=5)
    assert pd.isna(result.iloc[1])


def test_distance_from_ma_positive_and_negative():
    price = pd.Series([110.0, 90.0])
    ma = pd.Series([100.0, 100.0])
    result = distance_from_ma(price, ma)
    assert result.iloc[0] == 0.10
    assert result.iloc[1] == -0.10


def test_distance_from_ma_nan_when_ma_is_zero():
    price = pd.Series([100.0])
    ma = pd.Series([0.0])
    result = distance_from_ma(price, ma)
    assert pd.isna(result.iloc[0])
