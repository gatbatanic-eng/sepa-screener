from datetime import date

import pytest

from src.market.regime import RegimeType
from src.models.signal import QualityStatus, Signal, SignalState, StrategyName
from src.models.trade import ExitReason, Trade
from src.storage import save_signal, save_trade


def _signal(symbol="005930", strategy=StrategyName.RANGE_MR, signal_state=SignalState.BUY_CANDIDATE):
    return Signal(
        symbol=symbol,
        strategy=strategy,
        date=date(2024, 1, 5),
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
        reasons=["Box position = 13%"],
    )


def _trade(symbol="005930", strategy=StrategyName.RANGE_MR, return_pct=0.15):
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
        return_pct=return_pct,
        mfe=return_pct,
        mae=-0.02,
        holding_days=4,
    )


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_get_config_returns_strategy_sections(client):
    resp = client.get("/config")
    assert resp.status_code == 200
    body = resp.json()
    assert "range_mr" in body
    assert "v_rebound" in body
    assert body["range_mr"]["thresholds"]["buy_rr_min"] == 2.0


def test_list_signals_empty(client):
    resp = client.get("/signals")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_signals_after_seeding(client, db_session):
    save_signal(db_session, _signal())
    db_session.commit()

    resp = client.get("/signals")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "005930"
    assert body[0]["rr_1"] == pytest.approx(3.0)


def test_list_signals_filter_by_strategy(client, db_session):
    save_signal(db_session, _signal(strategy=StrategyName.RANGE_MR))
    save_signal(db_session, _signal(symbol="000660", strategy=StrategyName.V_REBOUND))
    db_session.commit()

    resp = client.get("/signals", params={"strategy": "V_REBOUND"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "000660"


def test_list_signals_filter_by_signal_state(client, db_session):
    save_signal(db_session, _signal(signal_state=SignalState.WATCH))
    save_signal(db_session, _signal(symbol="000660", signal_state=SignalState.BUY_CANDIDATE))
    db_session.commit()

    resp = client.get("/signals", params={"signal": "BUY_CANDIDATE"})
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "000660"


def test_get_symbol_signals_404_when_none(client):
    resp = client.get("/signals/999999")
    assert resp.status_code == 404


def test_get_symbol_signals_found(client, db_session):
    save_signal(db_session, _signal(symbol="005930"))
    db_session.commit()

    resp = client.get("/signals/005930")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["symbol"] == "005930"


def test_list_trades(client, db_session):
    save_trade(db_session, _trade())
    db_session.commit()

    resp = client.get("/trades")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["exit_reason"] == "TARGET"


def test_backtest_metrics_empty(client):
    resp = client.get("/backtest/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_trades"] == 0
    assert body["win_rate"] is None


def test_backtest_metrics_with_trades(client, db_session):
    save_trade(db_session, _trade(return_pct=0.10))
    save_trade(db_session, _trade(return_pct=-0.05))
    db_session.commit()

    resp = client.get("/backtest/metrics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_trades"] == 2
    assert body["win_rate"] == pytest.approx(0.5)
