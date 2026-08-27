import pandas as pd
import pytest

from src.config import BoxConfig
from src.strategies.range_mr import compute_box_metrics

CONFIG = BoxConfig(period_days=5, width_min=0.15, width_max=0.40, position_max=0.25)


def test_box_metrics_known_values():
    high = pd.Series([100.0, 105.0, 110.0, 108.0, 106.0])
    low = pd.Series([95.0, 96.0, 90.0, 97.0, 96.0])
    close = pd.Series([98.0, 100.0, 95.0, 100.0, 91.0])
    df = compute_box_metrics(high, low, close, CONFIG)
    last = df.iloc[-1]
    # period=5, 전체 구간 사용
    assert last["box_high"] == 110.0
    assert last["box_low"] == 90.0
    assert last["box_width"] == pytest.approx((110.0 - 90.0) / 90.0)
    assert last["box_midpoint"] == pytest.approx(100.0)
    assert last["box_position"] == pytest.approx((91.0 - 90.0) / (110.0 - 90.0))


def test_box_filter_passes_within_thresholds():
    # width = (120-100)/100 = 0.20 (15~40% 이내), position = (105-100)/20 = 0.25 (경계, <=0.25 통과)
    high = pd.Series([120.0] * 5)
    low = pd.Series([100.0] * 5)
    close = pd.Series([100.0, 100.0, 100.0, 100.0, 105.0])
    df = compute_box_metrics(high, low, close, CONFIG)
    assert bool(df["passes_box_filter"].iloc[-1]) is True


def test_box_filter_fails_when_position_above_max():
    high = pd.Series([120.0] * 5)
    low = pd.Series([100.0] * 5)
    close = pd.Series([100.0, 100.0, 100.0, 100.0, 106.0])  # position = 0.30 > 0.25
    df = compute_box_metrics(high, low, close, CONFIG)
    assert bool(df["passes_box_filter"].iloc[-1]) is False


def test_box_filter_fails_when_width_out_of_range():
    high = pd.Series([200.0] * 5)  # width = 100% > 40%
    low = pd.Series([100.0] * 5)
    close = pd.Series([100.0] * 5)
    df = compute_box_metrics(high, low, close, CONFIG)
    assert bool(df["passes_box_filter"].iloc[-1]) is False


def test_box_metrics_nan_with_insufficient_history():
    high = pd.Series([100.0, 105.0])
    low = pd.Series([95.0, 96.0])
    close = pd.Series([98.0, 100.0])
    df = compute_box_metrics(high, low, close, CONFIG)
    assert df["box_high"].isna().all()
    assert (~df["passes_box_filter"]).all()
