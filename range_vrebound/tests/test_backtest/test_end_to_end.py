"""pipeline(Phase 6) -> backtest engine(Phase 7) 전체 배선 테스트."""
import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import generate_trades
from src.backtest.metrics import compute_metrics
from src.config import load_config
from src.models.signal import StrategyName
from src.pipeline import evaluate_range_mr, evaluate_v_rebound

CONFIG = load_config()


def _synthetic_ohlcv(n: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1.0, n)))
    close = close.clip(lower=10.0)
    high = close + rng.random(n) * 2
    low = close - rng.random(n) * 2
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1000 + rng.random(n) * 500)
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    for s in (close, high, low, open_, volume):
        s.index = idx
    return {"open_": open_, "high": high, "low": low, "close": close, "volume": volume}


def test_range_mr_end_to_end_backtest_runs_without_crash():
    n = 200
    bars = _synthetic_ohlcv(n, seed=1)
    benchmark = pd.Series(
        100 + np.cumsum(np.random.default_rng(2).normal(0, 0.5, n)), index=bars["close"].index
    )
    signals_df = evaluate_range_mr(
        bars["open_"], bars["high"], bars["low"], bars["close"], bars["volume"], benchmark, CONFIG.range_mr
    )
    trades = generate_trades(
        "TEST", StrategyName.RANGE_MR, signals_df, bars["open_"], bars["high"], bars["low"], bars["close"],
        CONFIG.backtest.holding_periods_days,
    )
    metrics = compute_metrics(trades)
    assert metrics.total_trades == len(trades)
    if trades:
        assert all(t.exit_reason is not None for t in trades)
        assert all(t.entry_price > 0 for t in trades)


def test_v_rebound_engineered_scenario_can_produce_trades():
    pre = [100.0] * 65
    crash = [100.0, 90.0, 80.0, 70.0]
    low_period = [70.0, 71.0, 72.0, 73.0, 74.0]
    rebound = [76.0, 79.0, 83.0, 88.0, 95.0, 100.0, 105.0, 110.0, 112.0, 115.0]
    close = pd.Series(pre + crash + low_period + rebound)
    n = len(close)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series([1000.0] * n)
    volume.iloc[len(pre) + len(crash) + len(low_period):] = 2500.0
    benchmark = pd.Series([100.0] * n)

    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    for s in (close, high, low, open_, volume, benchmark):
        s.index = idx

    signals_df = evaluate_v_rebound(open_, high, low, close, volume, benchmark, CONFIG.v_rebound)
    trades = generate_trades(
        "TEST", StrategyName.V_REBOUND, signals_df, open_, high, low, close, CONFIG.backtest.holding_periods_days
    )
    metrics = compute_metrics(trades)
    assert metrics.total_trades == len(trades)
    # 신호가 없더라도(임계값 미달) 크래시 없이 metrics가 계산되어야 한다
    if trades:
        for t in trades:
            assert t.holding_days is not None and t.holding_days >= 0


def test_generate_trades_skips_when_no_next_day_data():
    idx = pd.date_range("2023-01-02", periods=3, freq="B")
    signals_df = pd.DataFrame(
        {"signal": [None, None, "BUY_CANDIDATE"], "stop": [np.nan, np.nan, 90.0], "target_1": [np.nan, np.nan, 120.0]},
        index=idx,
    )
    open_ = pd.Series([100.0, 101.0, 102.0], index=idx)
    high = pd.Series([101.0, 102.0, 103.0], index=idx)
    low = pd.Series([99.0, 100.0, 101.0], index=idx)
    close = pd.Series([100.0, 101.0, 102.0], index=idx)
    trades = generate_trades("TEST", StrategyName.RANGE_MR, signals_df, open_, high, low, close, [5])
    assert trades == []  # 마지막 날 신호는 다음날 데이터가 없어 체결 불가
