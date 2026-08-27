from datetime import date, timedelta

import pandas as pd
import pytest

from src.models.signal import StrategyName
from src.models.trade import ExitReason
from src.backtest.engine import simulate_trade


def _dates(n: int, start=date(2024, 1, 1)):
    return pd.date_range(start, periods=n, freq="D")


def test_stop_hit_exits_at_stop_price():
    idx = _dates(5)
    high = pd.Series([101, 102, 103, 104, 105], index=idx, dtype=float)
    low = pd.Series([99, 98, 85, 90, 91], index=idx, dtype=float)  # index2에서 stop(90) 하회
    close = pd.Series([100, 100, 88, 95, 96], index=idx, dtype=float)

    trade = simulate_trade(
        symbol="TEST", strategy=StrategyName.RANGE_MR, entry_date=idx[0].date(), entry_price=100.0,
        stop=90.0, target=130.0, max_holding_days=10, high=high, low=low, close=close,
    )
    assert trade.exit_reason == ExitReason.STOP
    assert trade.exit_price == pytest.approx(90.0)
    assert trade.exit_date == idx[2].date()
    assert trade.return_pct == pytest.approx(-0.10)


def test_target_hit_exits_at_target_price():
    idx = _dates(5)
    high = pd.Series([101, 102, 135, 104, 105], index=idx, dtype=float)  # index2에서 target(130) 상회
    low = pd.Series([99, 98, 100, 90, 91], index=idx, dtype=float)
    close = pd.Series([100, 100, 132, 95, 96], index=idx, dtype=float)

    trade = simulate_trade(
        symbol="TEST", strategy=StrategyName.RANGE_MR, entry_date=idx[0].date(), entry_price=100.0,
        stop=90.0, target=130.0, max_holding_days=10, high=high, low=low, close=close,
    )
    assert trade.exit_reason == ExitReason.TARGET
    assert trade.exit_price == pytest.approx(130.0)
    assert trade.return_pct == pytest.approx(0.30)


def test_time_exit_when_neither_hit_within_max_holding_days():
    idx = _dates(5)
    high = pd.Series([101, 102, 103, 104, 105], index=idx, dtype=float)
    low = pd.Series([99, 98, 97, 96, 95], index=idx, dtype=float)
    close = pd.Series([100, 101, 102, 103, 104], index=idx, dtype=float)

    trade = simulate_trade(
        symbol="TEST", strategy=StrategyName.RANGE_MR, entry_date=idx[0].date(), entry_price=100.0,
        stop=90.0, target=130.0, max_holding_days=4, high=high, low=low, close=close,
    )
    assert trade.exit_reason == ExitReason.TIME_EXIT
    assert trade.exit_date == idx[4].date()
    assert trade.exit_price == pytest.approx(104.0)
    assert trade.holding_days == 4


def test_stop_takes_priority_when_both_hit_same_day():
    idx = _dates(3)
    high = pd.Series([101, 140, 105], index=idx, dtype=float)  # target(130) 상회
    low = pd.Series([99, 85, 91], index=idx, dtype=float)  # stop(90) 하회 (같은 날)
    close = pd.Series([100, 100, 96], index=idx, dtype=float)

    trade = simulate_trade(
        symbol="TEST", strategy=StrategyName.RANGE_MR, entry_date=idx[0].date(), entry_price=100.0,
        stop=90.0, target=130.0, max_holding_days=10, high=high, low=low, close=close,
    )
    assert trade.exit_reason == ExitReason.STOP


def test_mfe_and_mae_computed_over_holding_window():
    idx = _dates(3)
    high = pd.Series([105, 110, 108], index=idx, dtype=float)
    low = pd.Series([98, 95, 99], index=idx, dtype=float)
    close = pd.Series([102, 105, 103], index=idx, dtype=float)

    trade = simulate_trade(
        symbol="TEST", strategy=StrategyName.RANGE_MR, entry_date=idx[0].date(), entry_price=100.0,
        stop=80.0, target=200.0, max_holding_days=3, high=high, low=low, close=close,
    )
    # MFE = (110-100)/100 = 0.10, MAE = (95-100)/100 = -0.05
    assert trade.mfe == pytest.approx(0.10)
    assert trade.mae == pytest.approx(-0.05)


def test_invalidated_exit_when_flagged_before_stop_or_target():
    idx = _dates(4)
    high = pd.Series([101, 102, 103, 104], index=idx, dtype=float)
    low = pd.Series([99, 98, 97, 96], index=idx, dtype=float)
    close = pd.Series([100, 100, 100, 100], index=idx, dtype=float)
    invalidated = pd.Series([False, True, False, False], index=idx)

    trade = simulate_trade(
        symbol="TEST", strategy=StrategyName.RANGE_MR, entry_date=idx[0].date(), entry_price=100.0,
        stop=90.0, target=130.0, max_holding_days=10, high=high, low=low, close=close,
        invalidated=invalidated,
    )
    assert trade.exit_reason == ExitReason.INVALIDATED
    assert trade.exit_date == idx[1].date()


def test_max_holding_days_boundary_exact_day():
    idx = _dates(6)
    high = pd.Series([101] * 6, index=idx, dtype=float)
    low = pd.Series([99] * 6, index=idx, dtype=float)
    close = pd.Series([100] * 6, index=idx, dtype=float)

    trade = simulate_trade(
        symbol="TEST", strategy=StrategyName.RANGE_MR, entry_date=idx[0].date(), entry_price=100.0,
        stop=50.0, target=200.0, max_holding_days=5, high=high, low=low, close=close,
    )
    assert trade.holding_days == 5
    assert trade.exit_date == idx[5].date()
