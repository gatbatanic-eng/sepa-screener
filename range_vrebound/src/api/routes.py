"""스크리닝 결과 조회 API (스펙 32조 Phase 9).

이 API는 스크리닝을 직접 실행하지 않는다 — 저장소(`src/storage.py`)에
이미 계산되어 저장된 Signal/Trade를 조회만 한다. 실제 계산은
`src/pipeline.py`(Phase 6)와 `src/backtest/`(Phase 7)가 하고, 그 결과를
저장하는 배치 스크립트는 별도로 만든다(예: 매일 실행되는 GitHub Actions).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.api.deps import get_db
from src.backtest.metrics import compute_metrics
from src.config import load_config
from src.models.signal import SignalState, StrategyName
from src.storage import query_signals, query_trades

router = APIRouter()


@router.get("/health")
def health() -> dict:
    return {"status": "ok"}


@router.get("/config")
def get_config() -> dict:
    """현재 적용 중인 CONFIG 전체를 반환한다 (투명성 — 스펙 2.2조)."""
    return load_config().model_dump()


@router.get("/signals")
def list_signals(
    strategy: Optional[StrategyName] = None,
    signal: Optional[SignalState] = None,
    symbol: Optional[str] = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    signals = query_signals(db, strategy=strategy, signal_state=signal, symbol=symbol)
    return [s.model_dump(mode="json") for s in signals]


@router.get("/signals/{symbol}")
def get_symbol_signals(symbol: str, db: Session = Depends(get_db)) -> list[dict]:
    signals = query_signals(db, symbol=symbol)
    if not signals:
        raise HTTPException(status_code=404, detail=f"'{symbol}'에 대해 저장된 신호가 없습니다.")
    return [s.model_dump(mode="json") for s in signals]


@router.get("/trades")
def list_trades(
    strategy: Optional[StrategyName] = None,
    symbol: Optional[str] = None,
    db: Session = Depends(get_db),
) -> list[dict]:
    trades = query_trades(db, strategy=strategy, symbol=symbol)
    return [t.model_dump(mode="json") for t in trades]


@router.get("/backtest/metrics")
def backtest_metrics(
    strategy: Optional[StrategyName] = None,
    symbol: Optional[str] = None,
    db: Session = Depends(get_db),
) -> dict:
    """저장된 거래로 스펙 28조 성과 지표를 계산해 반환한다.

    strategy/symbol로 필터링한 부분집합을 넘긴다 — 그룹핑 자체는
    `compute_metrics`의 책임이 아니다(Phase 7 설계 그대로).
    """
    trades = query_trades(db, strategy=strategy, symbol=symbol)
    return compute_metrics(trades).model_dump()
