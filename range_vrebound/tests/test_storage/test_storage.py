from datetime import date

import pytest

from src.market.regime import RegimeType
from src.models.signal import QualityStatus, Signal, SignalState, StrategyName
from src.models.trade import ExitReason, Trade
from src.storage import (
    get_engine,
    get_session_factory,
    query_signals,
    query_trades,
    save_signal,
    save_trade,
)


@pytest.fixture()
def session(tmp_path):
    engine = get_engine(tmp_path / "test.db")
    Session = get_session_factory(engine)
    session = Session()
    yield session
    session.close()


def _signal(symbol="005930", strategy=StrategyName.RANGE_MR, signal_state=SignalState.BUY_CANDIDATE, d=date(2024, 1, 5)):
    return Signal(
        symbol=symbol,
        strategy=strategy,
        date=d,
        market_regime=RegimeType.RANGE,
        setup_score=82,
        trigger_score=78,
        total_score=82,
        signal=signal_state,
        quality_status=QualityStatus.UNKNOWN,
        entry=10000,
        stop=9500,
        target_1=11500,
        target_2=13000,
        rr_1=3.0,
        rr_2=6.0,
        reasons=["Box position = 13%", "R/R = 3.0"],
    )


def _trade(symbol="005930", strategy=StrategyName.RANGE_MR):
    return Trade(
        symbol=symbol,
        strategy=strategy,
        entry_date=date(2024, 1, 6),
        entry_price=10000,
        stop=9500,
        target=11500,
        max_holding_days=20,
        exit_date=date(2024, 1, 10),
        exit_reason=ExitReason.TARGET,
        exit_price=11500,
        return_pct=0.15,
        mfe=0.15,
        mae=-0.02,
        holding_days=4,
    )


def test_save_and_query_signal_roundtrip(session):
    save_signal(session, _signal())
    session.commit()

    results = query_signals(session)
    assert len(results) == 1
    assert isinstance(results[0], Signal)
    assert results[0].symbol == "005930"
    assert results[0].reasons == ["Box position = 13%", "R/R = 3.0"]
    assert results[0].rr_1 == pytest.approx(3.0)


def test_query_signals_filter_by_strategy(session):
    save_signal(session, _signal(strategy=StrategyName.RANGE_MR))
    save_signal(session, _signal(symbol="000660", strategy=StrategyName.V_REBOUND))
    session.commit()

    results = query_signals(session, strategy=StrategyName.V_REBOUND)
    assert len(results) == 1
    assert results[0].symbol == "000660"


def test_query_signals_filter_by_signal_state(session):
    save_signal(session, _signal(signal_state=SignalState.BUY_CANDIDATE))
    save_signal(session, _signal(symbol="000660", signal_state=SignalState.WATCH))
    session.commit()

    results = query_signals(session, signal_state=SignalState.WATCH)
    assert len(results) == 1
    assert results[0].symbol == "000660"


def test_query_signals_filter_by_symbol(session):
    save_signal(session, _signal(symbol="005930"))
    save_signal(session, _signal(symbol="000660"))
    session.commit()

    results = query_signals(session, symbol="005930")
    assert len(results) == 1


def test_save_and_query_trade_roundtrip(session):
    save_trade(session, _trade())
    session.commit()

    results = query_trades(session)
    assert len(results) == 1
    assert isinstance(results[0], Trade)
    assert results[0].return_pct == pytest.approx(0.15)
    assert results[0].exit_reason == ExitReason.TARGET


def test_query_trades_filter_by_strategy(session):
    save_trade(session, _trade(strategy=StrategyName.RANGE_MR))
    save_trade(session, _trade(symbol="000660", strategy=StrategyName.V_REBOUND))
    session.commit()

    results = query_trades(session, strategy=StrategyName.V_REBOUND)
    assert len(results) == 1
    assert results[0].symbol == "000660"


def test_query_trades_filter_by_symbol(session):
    save_trade(session, _trade(symbol="005930"))
    save_trade(session, _trade(symbol="000660"))
    session.commit()

    results = query_trades(session, symbol="000660")
    assert len(results) == 1
    assert results[0].symbol == "000660"
