import pandas as pd
import pytest

from src.config import load_config
from src.strategies.range_mr import compute_stop_target

CONFIG = load_config().range_mr


def test_stop_target_known_values():
    box_high = pd.Series([120.0])
    box_low = pd.Series([100.0])
    box_midpoint = pd.Series([110.0])
    close = pd.Series([102.0])
    atr = pd.Series([2.0])

    df = compute_stop_target(box_high, box_low, box_midpoint, close, atr, CONFIG)

    # support zone 하단 = 100*(1-0.03) = 97.0, stop = 97.0 - 0.5*2.0 = 96.0
    assert df["stop"].iloc[0] == pytest.approx(96.0)
    assert df["target_1"].iloc[0] == pytest.approx(110.0)
    assert df["target_2"].iloc[0] == pytest.approx(120.0)
    # risk = 102-96=6, reward1 = 110-102=8 -> rr_1 = 8/6
    assert df["rr_1"].iloc[0] == pytest.approx(8.0 / 6.0)
    # reward2 = 120-102=18 -> rr_2 = 18/6=3.0
    assert df["rr_2"].iloc[0] == pytest.approx(3.0)


def test_stop_target_rr_none_when_close_already_below_stop():
    box_high = pd.Series([120.0])
    box_low = pd.Series([100.0])
    box_midpoint = pd.Series([110.0])
    close = pd.Series([90.0])  # 이미 stop 아래
    atr = pd.Series([2.0])

    df = compute_stop_target(box_high, box_low, box_midpoint, close, atr, CONFIG)
    assert pd.isna(df["rr_1"].iloc[0])
    assert pd.isna(df["rr_2"].iloc[0])
