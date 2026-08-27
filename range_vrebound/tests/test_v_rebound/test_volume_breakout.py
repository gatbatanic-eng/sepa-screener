import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.strategies.v_rebound import compute_breakout, compute_volume_structure

CONFIG = load_config().v_rebound


def test_rebound_volume_ratio_boundary():
    n = 25
    volume = pd.Series([1000.0] * (n - 1) + [1300.0])  # 정확히 1.3배 (경계값)
    candidate_low_idx = pd.Series([np.nan] * n)
    df = compute_volume_structure(volume, candidate_low_idx, CONFIG)
    assert df["rebound_volume_ratio"].iloc[-1] == pytest.approx(1.3)


def test_panic_volume_ratio_uses_candidate_low_day():
    n = 25
    volume = pd.Series([1000.0] * (n - 1) + [1300.0])
    # 저점(패닉)은 index20에서 거래량이 2000이었다고 가정
    volume.iloc[20] = 2000.0
    candidate_low_idx = pd.Series([float(20)] * n)
    df = compute_volume_structure(volume, candidate_low_idx, CONFIG)
    # index20 시점의 baseline_avg_volume(직전 20일 평균, 전부 1000) 대비 2000 -> 2.0배
    assert df["panic_volume_ratio"].iloc[-1] == pytest.approx(2.0)


def test_panic_volume_ratio_nan_when_no_candidate_low_yet():
    n = 5
    volume = pd.Series([1000.0] * n)
    candidate_low_idx = pd.Series([np.nan] * n)
    df = compute_volume_structure(volume, candidate_low_idx, CONFIG)
    assert df["panic_volume_ratio"].isna().all()


def test_breakout_true_when_all_three_conditions_met():
    close_today = 111.0
    first_rebound_high = 110.0
    rebound_volume_ratio = 1.5
    rs_5d = 0.02
    assert compute_breakout(close_today, first_rebound_high, rebound_volume_ratio, rs_5d, CONFIG) is True


def test_breakout_false_when_price_not_above_first_rebound_high():
    assert compute_breakout(110.0, 110.0, 1.5, 0.02, CONFIG) is False


def test_breakout_false_when_volume_insufficient():
    assert compute_breakout(111.0, 110.0, 1.1, 0.02, CONFIG) is False


def test_breakout_false_when_rs_not_positive():
    assert compute_breakout(111.0, 110.0, 1.5, -0.01, CONFIG) is False


def test_breakout_false_when_first_rebound_high_unavailable():
    assert compute_breakout(111.0, None, 1.5, 0.02, CONFIG) is False


def test_breakout_false_when_rebound_volume_ratio_is_nan():
    assert compute_breakout(111.0, 110.0, np.nan, 0.02, CONFIG) is False


def test_breakout_false_when_rs_5d_is_nan():
    assert compute_breakout(111.0, 110.0, 1.5, np.nan, CONFIG) is False


def test_panic_volume_ratio_nan_when_baseline_unavailable():
    # 저점(index2)이 거래량 평균 산출에 필요한 20일 워밍업보다 이르면
    # baseline_avg_volume 자체가 NaN이라 panic ratio도 계산 불가.
    n = 10
    volume = pd.Series([1000.0] * n)
    candidate_low_idx = pd.Series([float(2)] * n)
    df = compute_volume_structure(volume, candidate_low_idx, CONFIG)
    assert df["panic_volume_ratio"].isna().all()
