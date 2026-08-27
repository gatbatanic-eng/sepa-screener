"""스펙 29조 TRAIN -> PARAMETER TEST -> OUT-OF-SAMPLE 구조를 실제 파이프라인
(합성 데이터)으로 한 번 전체 배선해본다. 실제 값 자체보다 "그리드 스윕과
train/test 분리가 실제로 동작하는가"를 확인하는 용도다.
"""
import numpy as np
import pandas as pd

from src.backtest.engine import generate_trades
from src.backtest.metrics import compute_metrics
from src.backtest.robustness import detect_overfitting_risk, run_parameter_sensitivity, train_test_split_by_date
from src.config import load_config
from src.models.signal import StrategyName
from src.pipeline import evaluate_range_mr

BASE_CONFIG = load_config()


def _synthetic_ohlcv(n: int, seed: int):
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1.0, n)))
    close = close.clip(lower=10.0)
    high = close + rng.random(n) * 2
    low = close - rng.random(n) * 2
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1000 + rng.random(n) * 500)
    idx = pd.date_range("2022-01-03", periods=n, freq="B")
    for s in (close, high, low, open_, volume):
        s.index = idx
    return open_, high, low, close, volume


def _run_range_mr_backtest(config, open_, high, low, close, volume, benchmark) -> "BacktestMetrics":
    signals_df = evaluate_range_mr(open_, high, low, close, volume, benchmark, config.range_mr)
    trades = generate_trades(
        "TEST", StrategyName.RANGE_MR, signals_df, open_, high, low, close, config.backtest.holding_periods_days
    )
    return compute_metrics(trades)


def test_box_period_sensitivity_sweep_runs_on_real_pipeline():
    n = 300
    open_, high, low, close, volume = _synthetic_ohlcv(n, seed=42)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(43).normal(0, 0.5, n)), index=close.index)

    def run_fn(cfg):
        return _run_range_mr_backtest(cfg, open_, high, low, close, volume, benchmark)

    grid = BASE_CONFIG.backtest.walk_forward.box_period_grid  # [40, 60, 80, 120]
    df = run_parameter_sensitivity(run_fn, BASE_CONFIG, "range_mr.box.period_days", grid, "avg_return")

    assert list(df["value"]) == grid
    assert "total_trades" in df.columns
    # 거래가 전혀 없으면 avg_return이 None일 수 있다 — 크래시 없이 도는 것 자체가 목적
    result = detect_overfitting_risk(df, "avg_return")
    assert "risk" in result


def test_train_test_split_then_backtest_both_halves():
    n = 260
    open_, high, low, close, volume = _synthetic_ohlcv(n, seed=100)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(101).normal(0, 0.5, n)), index=close.index)

    split_date = close.index[len(close) // 2]
    train_mask, test_mask = train_test_split_by_date(close.index, split_date)
    assert train_mask.sum() + test_mask.sum() == n

    train_metrics = _run_range_mr_backtest(
        BASE_CONFIG,
        open_[train_mask], high[train_mask], low[train_mask], close[train_mask], volume[train_mask],
        benchmark[train_mask],
    )
    test_metrics = _run_range_mr_backtest(
        BASE_CONFIG,
        open_[test_mask], high[test_mask], low[test_mask], close[test_mask], volume[test_mask],
        benchmark[test_mask],
    )
    # 크래시 없이 두 구간 모두 지표가 계산되면 배선 검증은 충분하다
    assert train_metrics.total_trades >= 0
    assert test_metrics.total_trades >= 0
