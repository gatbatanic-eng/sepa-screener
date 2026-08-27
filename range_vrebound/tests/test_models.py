from datetime import date

import pytest
from pydantic import ValidationError

from src.market.regime import MarketRegime, RegimeType
from src.models.market_data import OHLCVBar
from src.models.signal import QualityStatus, Signal, SignalState, StrategyName
from src.models.trade import Trade


def test_ohlcv_bar_valid():
    bar = OHLCVBar(date=date(2026, 1, 5), symbol="005930", open=100, high=110, low=95, close=105, volume=1000)
    assert bar.high >= bar.low


def test_ohlcv_bar_rejects_high_below_low():
    with pytest.raises(ValidationError):
        OHLCVBar(date=date(2026, 1, 5), symbol="005930", open=100, high=90, low=95, close=92, volume=1000)


def test_market_regime_accepts_all_four_states():
    for regime in RegimeType:
        MarketRegime(date=date(2026, 1, 5), regime=regime)


def test_market_regime_rejects_unknown_state():
    with pytest.raises(ValidationError):
        MarketRegime(date=date(2026, 1, 5), regime="BULL")


@pytest.mark.parametrize("score", [0, 69, 70, 100])
def test_signal_score_accepts_in_bounds(score):
    Signal(
        symbol="005930", strategy=StrategyName.RANGE_MR, date=date(2026, 1, 5),
        market_regime=RegimeType.RANGE, setup_score=score, total_score=score,
        signal=SignalState.SETUP,
    )


@pytest.mark.parametrize("score", [-1, 101])
def test_signal_score_rejects_out_of_bounds(score):
    with pytest.raises(ValidationError):
        Signal(
            symbol="005930", strategy=StrategyName.RANGE_MR, date=date(2026, 1, 5),
            market_regime=RegimeType.RANGE, setup_score=score, total_score=50,
            signal=SignalState.SETUP,
        )


def test_signal_defaults_quality_ok_and_empty_reasons():
    sig = Signal(
        symbol="005930", strategy=StrategyName.V_REBOUND, date=date(2026, 1, 5),
        market_regime=RegimeType.CRASH, setup_score=80, total_score=80,
        signal=SignalState.WATCH,
    )
    assert sig.quality_status == QualityStatus.OK
    assert sig.reasons == []
    assert sig.trigger_score is None  # V-REBOUND는 별도 trigger_score를 계산하지 않음


def test_trade_rejects_non_positive_holding_period():
    with pytest.raises(ValidationError):
        Trade(
            symbol="005930", strategy=StrategyName.RANGE_MR, entry_date=date(2026, 1, 5),
            entry_price=100, stop=95, target=110, max_holding_days=0,
        )


def test_trade_accepts_valid_values():
    trade = Trade(
        symbol="005930", strategy=StrategyName.RANGE_MR, entry_date=date(2026, 1, 5),
        entry_price=100, stop=95, target=110, max_holding_days=20,
    )
    assert trade.max_holding_days == 20
    assert trade.exit_reason is None
