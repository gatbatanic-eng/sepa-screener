import pandas as pd
import pytest

from src.config import load_config
from src.scoring.range_score import compute_trigger_score
from src.strategies.range_mr import compute_trigger_flags

CONFIG = load_config().range_mr


def _flat_benchmark(n: int) -> pd.Series:
    return pd.Series([100.0] * n)


def test_breakout_flag_true_when_close_exceeds_prior_high():
    n = 10
    open_ = pd.Series([100.0] * n)
    high = pd.Series([100.0] * (n - 1) + [100.0])
    high.iloc[-2] = 105.0  # 직전 3일 중 최고가
    low = pd.Series([95.0] * n)
    close = pd.Series([100.0] * (n - 1) + [110.0])  # 오늘 종가가 직전 고가 돌파
    volume = pd.Series([1000.0] * n)
    benchmark = _flat_benchmark(n)
    flags = compute_trigger_flags(open_, high, low, close, volume, benchmark, CONFIG)
    assert bool(flags["breakout"].iloc[-1]) is True


def test_volume_ok_flag_boundary():
    n = 25
    open_ = pd.Series([100.0] * n)
    high = pd.Series([101.0] * n)
    low = pd.Series([99.0] * n)
    close = pd.Series([100.0] * n)
    volume = pd.Series([1000.0] * (n - 1) + [1200.0])  # 정확히 1.2배 (경계값)
    benchmark = _flat_benchmark(n)
    flags = compute_trigger_flags(open_, high, low, close, volume, benchmark, CONFIG)
    assert bool(flags["volume_ok"].iloc[-1]) is True


def test_bullish_candle_flag():
    open_ = pd.Series([100.0, 100.0])
    high = pd.Series([105.0, 105.0])
    low = pd.Series([95.0, 95.0])
    close = pd.Series([98.0, 102.0])  # 첫날 음봉, 둘째날 양봉
    volume = pd.Series([1000.0, 1000.0])
    benchmark = _flat_benchmark(2)
    flags = compute_trigger_flags(open_, high, low, close, volume, benchmark, CONFIG)
    assert bool(flags["bullish_candle"].iloc[0]) is False
    assert bool(flags["bullish_candle"].iloc[1]) is True


def test_rs_positive_flag():
    n = 10
    open_ = pd.Series([100.0] * n)
    high = pd.Series([101.0] * n)
    low = pd.Series([99.0] * n)
    close = pd.Series([100.0] * (n - 1) + [110.0])  # 종목 급등
    volume = pd.Series([1000.0] * n)
    benchmark = pd.Series([100.0] * n)  # 시장은 그대로
    flags = compute_trigger_flags(open_, high, low, close, volume, benchmark, CONFIG)
    assert bool(flags["rs_positive"].iloc[-1]) is True


def test_trigger_score_all_true_is_100():
    flags = pd.DataFrame(
        {
            "breakout": [True],
            "volume_ok": [True],
            "bullish_candle": [True],
            "ma_recovery": [True],
            "rs_positive": [True],
        }
    )
    score = compute_trigger_score(flags, CONFIG)
    assert score.iloc[0] == pytest.approx(100.0)


def test_trigger_score_all_false_is_0():
    flags = pd.DataFrame(
        {
            "breakout": [False],
            "volume_ok": [False],
            "bullish_candle": [False],
            "ma_recovery": [False],
            "rs_positive": [False],
        }
    )
    score = compute_trigger_score(flags, CONFIG)
    assert score.iloc[0] == pytest.approx(0.0)


def test_trigger_score_partial():
    # breakout, volume_ok만 True -> 20+20=40
    flags = pd.DataFrame(
        {
            "breakout": [True],
            "volume_ok": [True],
            "bullish_candle": [False],
            "ma_recovery": [False],
            "rs_positive": [False],
        }
    )
    score = compute_trigger_score(flags, CONFIG)
    assert score.iloc[0] == pytest.approx(40.0)
