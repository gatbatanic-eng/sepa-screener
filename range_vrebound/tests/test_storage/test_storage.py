from datetime import date

import pytest
from sqlalchemy import create_engine, text

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


def _signal(symbol="005930", strategy=StrategyName.RANGE_MR, signal_state=SignalState.BUY_CANDIDATE, d=date(2024, 1, 5), name=None):
    return Signal(
        symbol=symbol,
        name=name,
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


def test_get_engine_migrates_pre_existing_db_missing_name_column(tmp_path):
    """실제로 겪은 상황을 재현한다: name 컬럼이 생기기 전에 이미 운영
    중이던 DB에 새 코드로 접근하면 마이그레이션 없이는
    'no such column: signals.name'으로 깨진다.
    """
    db_path = tmp_path / "legacy.db"
    legacy_engine = create_engine(f"sqlite:///{db_path}")
    with legacy_engine.connect() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol VARCHAR NOT NULL,
                    strategy VARCHAR NOT NULL,
                    date DATE NOT NULL,
                    market_regime VARCHAR NOT NULL,
                    setup_score FLOAT NOT NULL,
                    trigger_score FLOAT,
                    total_score FLOAT NOT NULL,
                    signal VARCHAR NOT NULL,
                    quality_status VARCHAR NOT NULL,
                    entry FLOAT,
                    stop FLOAT,
                    target_1 FLOAT,
                    target_2 FLOAT,
                    rr_1 FLOAT,
                    rr_2 FLOAT,
                    reasons_json VARCHAR NOT NULL DEFAULT '[]'
                )
                """
            )
        )
        conn.execute(
            text(
                "INSERT INTO signals (symbol, strategy, date, market_regime, setup_score, "
                "total_score, signal, quality_status, reasons_json) VALUES "
                "('005930', 'RANGE_MR', '2024-01-05', 'RANGE', 80, 80, 'WATCH', 'UNKNOWN', '[]')"
            )
        )
        conn.commit()
    legacy_engine.dispose()

    engine = get_engine(db_path)  # 마이그레이션이 여기서 일어나야 한다
    session = get_session_factory(engine)()
    try:
        results = query_signals(session)  # name 컬럼이 없으면 여기서 OperationalError
        assert len(results) == 1
        assert results[0].name is None  # 기존 행은 값이 없어 NULL

        save_signal(session, _signal(symbol="000660", name="SK하이닉스"))
        session.commit()
        results = query_signals(session, symbol="000660")
        assert results[0].name == "SK하이닉스"
    finally:
        session.close()
