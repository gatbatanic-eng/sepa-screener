"""같은 (종목, 전략, 날짜) 신호가 중복 저장되던 버그 회귀 테스트."""
from datetime import date

import pytest

from src.models.signal import SignalState, StrategyName
from src.storage import dedupe_signals, get_engine, get_session_factory, query_signals, save_signal
from tests.test_storage.test_storage import _signal


@pytest.fixture()
def session(tmp_path):
    engine = get_engine(tmp_path / "dedupe.db")
    s = get_session_factory(engine)()
    yield s
    s.close()


def test_saving_same_symbol_strategy_date_twice_keeps_one_row_with_latest_values(session):
    save_signal(session, _signal(signal_state=SignalState.WATCH))
    save_signal(session, _signal(signal_state=SignalState.BUY_CANDIDATE))
    session.commit()

    rows = query_signals(session)
    assert len(rows) == 1
    assert rows[0].signal == SignalState.BUY_CANDIDATE


def test_different_date_strategy_or_symbol_are_kept(session):
    save_signal(session, _signal())
    save_signal(session, _signal(d=date(2024, 1, 8)))
    save_signal(session, _signal(strategy=StrategyName.V_REBOUND))
    save_signal(session, _signal(symbol="000660"))
    session.commit()

    assert len(query_signals(session)) == 4


def test_dedupe_removes_existing_duplicates_and_keeps_newest(session):
    from src.storage import SignalRecord

    # 예전 버그로 이미 쌓인 상태를 재현: 같은 키 3행을 직접 삽입
    for state in (SignalState.INVALIDATED, SignalState.WATCH, SignalState.BUY_CANDIDATE):
        sig = _signal(signal_state=state)
        session.add(SignalRecord(
            symbol=sig.symbol, name=sig.name, strategy=sig.strategy, date=sig.date,
            market_regime=sig.market_regime, setup_score=sig.setup_score, trigger_score=sig.trigger_score,
            total_score=sig.total_score, signal=sig.signal, quality_status=sig.quality_status,
            entry=sig.entry, stop=sig.stop, target_1=sig.target_1, target_2=sig.target_2,
            rr_1=sig.rr_1, rr_2=sig.rr_2, reasons_json="[]"))
    save_signal(session, _signal(symbol="000660"))
    session.commit()
    assert session.query(SignalRecord).count() == 4

    removed = dedupe_signals(session)
    session.commit()

    assert removed == 2
    rows = query_signals(session, symbol="005930")
    assert len(rows) == 1
    assert rows[0].signal == SignalState.BUY_CANDIDATE
    assert len(query_signals(session, symbol="000660")) == 1


def test_dedupe_is_noop_when_no_duplicates(session):
    save_signal(session, _signal())
    session.commit()
    assert dedupe_signals(session) == 0
    assert len(query_signals(session)) == 1
