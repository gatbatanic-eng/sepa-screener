import pandas as pd
import pytest

from src.indicators.volume import avg_volume, volume_ratio


def test_avg_volume_basic():
    volume = pd.Series([100, 200, 300, 400], dtype=float)
    result = avg_volume(volume, period=2)
    assert pd.isna(result.iloc[0])
    assert result.iloc[1] == 150.0
    assert result.iloc[3] == 350.0


def test_volume_ratio_boundary_1_2x():
    # 스펙 7.14조 기본 거래량 조건 경계값: current >= 20일 평균 * 1.2
    current = pd.Series([120.0])
    avg = pd.Series([100.0])
    result = volume_ratio(current, avg)
    assert result.iloc[0] == pytest.approx(1.2)


def test_volume_ratio_equal_is_1_0():
    result = volume_ratio(pd.Series([100.0]), pd.Series([100.0]))
    assert result.iloc[0] == pytest.approx(1.0)


def test_volume_ratio_nan_when_avg_is_zero():
    result = volume_ratio(pd.Series([100.0]), pd.Series([0.0]))
    assert pd.isna(result.iloc[0])
