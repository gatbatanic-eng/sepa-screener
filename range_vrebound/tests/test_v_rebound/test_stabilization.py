import pandas as pd
import pytest

from src.config import load_config
from src.strategies.v_rebound import track_stabilization

CONFIG = load_config().v_rebound  # confirm_window_days=3, rebound_from_low_pct=0.03,
# first_rebound_high min=3/max=10


def _build_series():
    passes_filter = pd.Series([False] * 5 + [True] * 15)
    low_tail = [90, 85, 80, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93]
    low = pd.Series([100.0] * 5 + low_tail)
    close = low + 1
    high = low + 3
    return low, high, close, passes_filter


def test_candidate_low_tracks_running_minimum():
    low, high, close, passes_filter = _build_series()
    df = track_stabilization(low, high, close, passes_filter, CONFIG)
    assert df["candidate_low"].iloc[7] == 80.0
    assert df["candidate_low"].iloc[10] == 80.0  # 이후로도 새 저점 없음


def test_is_stabilized_after_confirm_window():
    low, high, close, passes_filter = _build_series()
    df = track_stabilization(low, high, close, passes_filter, CONFIG)
    # 저점(index7) 이후 3거래일 동안 새 저점 없음 -> index10부터 안정화
    assert bool(df["is_stabilized"].iloc[9]) is False
    assert bool(df["is_stabilized"].iloc[10]) is True


def test_recovered_3pct_flag():
    low, high, close, passes_filter = _build_series()
    df = track_stabilization(low, high, close, passes_filter, CONFIG)
    # 저점 80 대비 +3% = 82.4. close[7]=81(미달), close[8]=83(충족)
    assert bool(df["recovered_3pct"].iloc[7]) is False
    assert bool(df["recovered_3pct"].iloc[8]) is True


def test_first_rebound_high_not_available_before_min_days():
    low, high, close, passes_filter = _build_series()
    df = track_stabilization(low, high, close, passes_filter, CONFIG)
    # 저점(index7) + min_days_after_low(3) = index10 이전에는 아직 없음
    assert pd.isna(df["first_rebound_high"].iloc[9])


def test_first_rebound_high_running_max_within_window():
    low, high, close, passes_filter = _build_series()
    df = track_stabilization(low, high, close, passes_filter, CONFIG)
    assert df["first_rebound_high"].iloc[10] == pytest.approx(high.iloc[10])
    assert df["first_rebound_high"].iloc[11] == pytest.approx(max(high.iloc[10], high.iloc[11]))


def test_first_rebound_high_freezes_after_max_days():
    low, high, close, passes_filter = _build_series()
    df = track_stabilization(low, high, close, passes_filter, CONFIG)
    frozen_value = high.iloc[10:18].max()  # 저점(7) + [3..10] = index 10..17
    assert df["first_rebound_high"].iloc[17] == pytest.approx(frozen_value)
    assert df["first_rebound_high"].iloc[19] == pytest.approx(frozen_value)  # 이후 갱신 안 됨


def test_new_lower_low_resets_stabilization():
    low, high, close, passes_filter = _build_series()
    # index20에 기존 저점(80)보다 더 낮은 새 저점 발생 -> 안정화 리셋
    low = pd.concat([low, pd.Series([70.0])], ignore_index=True)
    high = pd.concat([high, pd.Series([73.0])], ignore_index=True)
    close = pd.concat([close, pd.Series([71.0])], ignore_index=True)
    passes_filter = pd.concat([passes_filter, pd.Series([True])], ignore_index=True)

    df = track_stabilization(low, high, close, passes_filter, CONFIG)
    assert df["candidate_low"].iloc[20] == 70.0
    assert bool(df["is_stabilized"].iloc[20]) is False
    assert pd.isna(df["first_rebound_high"].iloc[20])


def test_no_tracking_before_filter_first_passes():
    low, high, close, passes_filter = _build_series()
    df = track_stabilization(low, high, close, passes_filter, CONFIG)
    assert pd.isna(df["candidate_low"].iloc[0])
    assert bool(df["is_stabilized"].iloc[0]) is False


def test_stabilization_is_causal():
    low, high, close, passes_filter = _build_series()
    full = track_stabilization(low, high, close, passes_filter, CONFIG)
    cut = 12
    truncated = track_stabilization(
        low.iloc[: cut + 1], high.iloc[: cut + 1], close.iloc[: cut + 1], passes_filter.iloc[: cut + 1], CONFIG
    )
    assert full["is_stabilized"].iloc[cut] == truncated["is_stabilized"].iloc[cut]
    assert full["first_rebound_high"].iloc[cut] == pytest.approx(truncated["first_rebound_high"].iloc[cut])
