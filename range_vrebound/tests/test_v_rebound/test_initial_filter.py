import pandas as pd
import pytest

from src.config import load_config
from src.strategies.v_rebound import compute_initial_filter

CONFIG = load_config().v_rebound


def test_initial_filter_passes_on_exact_boundary():
    n = 61
    high = pd.Series([100.0] * n)  # 60일 고점 = 100
    close = pd.Series([100.0] * (n - 1) + [75.0])  # 드로다운 정확히 -25%
    benchmark = pd.Series([100.0] * (n - 1) + [90.0])  # 시장은 -10%, 종목-시장 = -15%p (<=-10%p 통과)
    df = compute_initial_filter(high, close, benchmark, CONFIG)
    last = df.iloc[-1]
    assert last["drawdown"] == pytest.approx(-0.25)
    assert bool(last["passes_filter"]) is True


def test_initial_filter_fails_when_drawdown_not_deep_enough():
    n = 61
    high = pd.Series([100.0] * n)
    close = pd.Series([100.0] * (n - 1) + [80.0])  # -20%, 기준 미달
    benchmark = pd.Series([100.0] * (n - 1) + [80.0])  # 초과하락은 0%p로 통과 조건이지만 드로다운 미달
    df = compute_initial_filter(high, close, benchmark, CONFIG)
    assert bool(df["passes_filter"].iloc[-1]) is False


def test_initial_filter_fails_when_excess_return_not_enough_even_if_drawdown_deep():
    n = 61
    high = pd.Series([100.0] * n)
    close = pd.Series([100.0] * (n - 1) + [70.0])  # -30%, 드로다운은 충분
    benchmark = pd.Series([100.0] * (n - 1) + [65.0])  # 시장도 -35% 하락, 종목 초과하락은 +5%p (기준 미달)
    df = compute_initial_filter(high, close, benchmark, CONFIG)
    assert bool(df["passes_filter"].iloc[-1]) is False
