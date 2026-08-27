import pandas as pd

from src.config import SupportConfig
from src.strategies.range_mr import detect_support_touches, rolling_support_touch_count

CONFIG = SupportConfig(
    tolerance_pct=0.03, confirmation_window_days=3, rebound_threshold_pct=0.03, min_touches_high_quality=2
)


def test_single_touch_confirmed_within_window():
    box_low = pd.Series([100.0] * 5)
    low = pd.Series([100.0, 101.0, 105.0, 106.0, 107.0])
    close = pd.Series([100.0, 101.0, 106.0, 106.0, 106.0])
    events = detect_support_touches(low, close, box_low, CONFIG)
    assert events.sum() == 1
    assert events.iloc[2] == 1  # 반등 확인일


def test_touch_not_confirmed_expires_after_window():
    box_low = pd.Series([100.0] * 5)
    low = pd.Series([100.0, 105.0, 106.0, 107.0, 108.0])
    close = pd.Series([100.0, 100.0, 101.0, 101.0, 101.0])
    events = detect_support_touches(low, close, box_low, CONFIG)
    assert events.sum() == 0


def test_contiguous_zone_days_count_as_one_touch():
    box_low = pd.Series([100.0] * 6)
    low = pd.Series([100.0, 101.0, 100.5, 105.0, 106.0, 107.0])  # 0,1,2가 연속으로 zone 안
    close = pd.Series([100.0, 100.5, 101.0, 106.0, 106.0, 106.0])
    events = detect_support_touches(low, close, box_low, CONFIG)
    assert events.sum() == 1


def test_two_separate_touches_counted_separately():
    box_low = pd.Series([100.0] * 10)
    low = pd.Series([100.0, 105.0, 106.0, 100.0, 106.0, 107.0, 108.0, 109.0, 110.0, 111.0])
    close = pd.Series([100.0, 106.0, 106.0, 100.0, 107.0, 106.0, 106.0, 106.0, 106.0, 106.0])
    events = detect_support_touches(low, close, box_low, CONFIG)
    assert events.sum() == 2


def test_rolling_support_touch_count_within_period():
    events = pd.Series([0, 1, 0, 0, 1, 0])
    result = rolling_support_touch_count(events, period_days=3)
    assert result.iloc[1] == 1
    assert result.iloc[4] == 1
    assert result.iloc[5] == 1  # index3,4,5 -> touches at idx4만


def test_support_touches_are_causal():
    box_low = pd.Series([100.0] * 8)
    low = pd.Series([100.0, 101.0, 105.0, 106.0, 107.0, 108.0, 109.0, 110.0])
    close = pd.Series([100.0, 101.0, 106.0, 106.0, 106.0, 106.0, 106.0, 106.0])
    full = detect_support_touches(low, close, box_low, CONFIG)
    truncated = detect_support_touches(low.iloc[:4], close.iloc[:4], box_low.iloc[:4], CONFIG)
    assert full.iloc[2] == truncated.iloc[2]
