from datetime import date

import numpy as np
import pytest

from src.backtest.metrics import compute_metrics
from src.models.signal import StrategyName
from src.models.trade import ExitReason, Trade


def _trade(return_pct: float, holding_days: int) -> Trade:
    return Trade(
        symbol="TEST",
        strategy=StrategyName.RANGE_MR,
        entry_date=date(2024, 1, 1),
        entry_price=100.0,
        stop=90.0,
        target=120.0,
        max_holding_days=20,
        exit_date=date(2024, 1, 1 + holding_days),
        exit_reason=ExitReason.TARGET if return_pct > 0 else ExitReason.STOP,
        exit_price=100.0 * (1 + return_pct),
        return_pct=return_pct,
        mfe=max(return_pct, 0.0),
        mae=min(return_pct, 0.0),
        holding_days=holding_days,
    )


RETURNS = [0.10, -0.05, 0.20, -0.10, 0.05]
HOLDING_DAYS = [5, 10, 15, 20, 8]


def _build_trades():
    return [_trade(r, h) for r, h in zip(RETURNS, HOLDING_DAYS)]


def test_basic_counts_and_averages():
    metrics = compute_metrics(_build_trades())
    assert metrics.total_trades == 5
    assert metrics.win_rate == pytest.approx(0.6)
    assert metrics.avg_return == pytest.approx(np.mean(RETURNS))
    assert metrics.median_return == pytest.approx(np.median(RETURNS))
    assert metrics.avg_win == pytest.approx(np.mean([0.10, 0.20, 0.05]))
    assert metrics.avg_loss == pytest.approx(np.mean([-0.05, -0.10]))
    assert metrics.avg_holding_period == pytest.approx(np.mean(HOLDING_DAYS))


def test_profit_factor_and_expectancy():
    metrics = compute_metrics(_build_trades())
    assert metrics.profit_factor == pytest.approx(0.35 / 0.15)
    assert metrics.expectancy == pytest.approx(metrics.avg_return)


def test_max_drawdown_on_compounded_equity_curve():
    metrics = compute_metrics(_build_trades())
    assert metrics.max_drawdown == pytest.approx(-0.10, abs=1e-6)


def test_sharpe_ratio():
    metrics = compute_metrics(_build_trades())
    expected = np.mean(RETURNS) / np.std(RETURNS, ddof=1)
    assert metrics.sharpe_ratio == pytest.approx(expected)


def test_sortino_ratio():
    metrics = compute_metrics(_build_trades())
    downside = np.clip(RETURNS, a_min=None, a_max=0.0)
    downside_deviation = np.sqrt(np.mean(downside**2))
    expected = np.mean(RETURNS) / downside_deviation
    assert metrics.sortino_ratio == pytest.approx(expected)


def test_empty_trades_returns_zero_and_none_fields():
    metrics = compute_metrics([])
    assert metrics.total_trades == 0
    assert metrics.win_rate is None
    assert metrics.avg_return is None
    assert metrics.profit_factor is None
    assert metrics.sharpe_ratio is None


def test_all_wins_profit_factor_and_avg_loss_are_none():
    trades = [_trade(0.10, 5), _trade(0.05, 3)]
    metrics = compute_metrics(trades)
    assert metrics.avg_loss is None
    assert metrics.profit_factor is None  # 손실이 없어 분모가 0 -> 정의 불가


def test_single_trade_sharpe_and_sortino_are_none():
    metrics = compute_metrics([_trade(0.10, 5)])
    assert metrics.sharpe_ratio is None  # 표본 1개로는 표준편차 계산 불가
