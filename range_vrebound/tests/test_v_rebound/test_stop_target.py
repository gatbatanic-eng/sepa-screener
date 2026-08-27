import pandas as pd
import pytest

from src.config import load_config
from src.strategies.v_rebound import compute_stop_target

CONFIG = load_config().v_rebound


def test_stop_target_known_values():
    confirmed_low_price = pd.Series([80.0])
    first_rebound_high = pd.Series([95.0])
    period_high = pd.Series([120.0])  # 급락 전 고점
    close = pd.Series([90.0])
    atr = pd.Series([2.0])

    df = compute_stop_target(confirmed_low_price, first_rebound_high, period_high, close, atr, CONFIG)

    # stop = 80 - 0.5*2.0 = 79.0
    assert df["stop"].iloc[0] == pytest.approx(79.0)
    assert df["target_1"].iloc[0] == pytest.approx(95.0)
    assert df["target_2"].iloc[0] == pytest.approx(120.0)
    # risk = 90-79=11, reward1 = 95-90=5 -> rr_1 = 5/11
    assert df["rr_1"].iloc[0] == pytest.approx(5.0 / 11.0)
    # reward2 = 120-90=30 -> rr_2 = 30/11
    assert df["rr_2"].iloc[0] == pytest.approx(30.0 / 11.0)


def test_stop_target_nan_when_not_yet_confirmed():
    confirmed_low_price = pd.Series([float("nan")])
    first_rebound_high = pd.Series([float("nan")])
    period_high = pd.Series([120.0])
    close = pd.Series([90.0])
    atr = pd.Series([2.0])

    df = compute_stop_target(confirmed_low_price, first_rebound_high, period_high, close, atr, CONFIG)
    assert pd.isna(df["stop"].iloc[0])
    assert pd.isna(df["rr_1"].iloc[0])
