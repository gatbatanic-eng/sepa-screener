"""generate_trades의 '해피 패스'(실제 BUY_CANDIDATE로부터 거래 생성)를
전략 파이프라인 없이 직접 구성한 signals_df로 검증한다. 현실적인 가격
시나리오로 특정 전략이 실제로 BUY_CANDIDATE를 내게 만드는 것은
test_end_to_end.py가 담당하고, 여기서는 generate_trades 자체의 배선을
확실히 점검한다.
"""
import numpy as np
import pandas as pd
import pytest

from src.backtest.engine import generate_trades
from src.models.signal import SignalState, StrategyName


def _price_series(n: int, start=100.0):
    idx = pd.date_range("2024-01-02", periods=n, freq="B")
    close = pd.Series([start] * n, index=idx)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    return open_, high, low, close, idx


def test_generate_trades_creates_one_trade_per_holding_period():
    n = 30
    open_, high, low, close, idx = _price_series(n)
    signals_df = pd.DataFrame(
        {"signal": [None] * n, "stop": [np.nan] * n, "target_1": [np.nan] * n},
        index=idx,
    )
    signal_col = list(signals_df["signal"])
    signal_col[10] = SignalState.BUY_CANDIDATE
    signals_df["signal"] = signal_col
    signals_df.loc[idx[10], "stop"] = 90.0
    signals_df.loc[idx[10], "target_1"] = 130.0

    holding_periods = [5, 10, 20]
    trades = generate_trades("TEST", StrategyName.RANGE_MR, signals_df, open_, high, low, close, holding_periods)

    assert len(trades) == len(holding_periods)
    assert {t.max_holding_days for t in trades} == set(holding_periods)
    for t in trades:
        assert t.entry_date == idx[11].date()  # 신호일 다음 거래일
        assert t.entry_price == pytest.approx(float(open_.iloc[11]))
        assert t.stop == pytest.approx(90.0)
        assert t.target == pytest.approx(130.0)
        assert t.exit_reason is not None


def test_generate_trades_skips_when_stop_or_target_is_nan():
    n = 20
    open_, high, low, close, idx = _price_series(n)
    signals_df = pd.DataFrame(
        {"signal": [None] * n, "stop": [np.nan] * n, "target_1": [np.nan] * n},
        index=idx,
    )
    signal_col = list(signals_df["signal"])
    signal_col[5] = SignalState.BUY_CANDIDATE  # stop/target은 NaN인 채로 둠
    signals_df["signal"] = signal_col

    trades = generate_trades("TEST", StrategyName.RANGE_MR, signals_df, open_, high, low, close, [10])
    assert trades == []


def test_generate_trades_multiple_buy_candidates_produce_multiple_trade_groups():
    n = 40
    open_, high, low, close, idx = _price_series(n)
    signals_df = pd.DataFrame(
        {"signal": [None] * n, "stop": [np.nan] * n, "target_1": [np.nan] * n},
        index=idx,
    )
    signal_col = list(signals_df["signal"])
    signal_col[5] = SignalState.BUY_CANDIDATE
    signal_col[20] = SignalState.BUY_CANDIDATE
    signals_df["signal"] = signal_col
    signals_df.loc[idx[5], ["stop", "target_1"]] = [90.0, 120.0]
    signals_df.loc[idx[20], ["stop", "target_1"]] = [95.0, 115.0]

    trades = generate_trades("TEST", StrategyName.RANGE_MR, signals_df, open_, high, low, close, [10])
    assert len(trades) == 2
    entry_dates = {t.entry_date for t in trades}
    assert idx[6].date() in entry_dates
    assert idx[21].date() in entry_dates
