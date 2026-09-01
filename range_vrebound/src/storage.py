"""SQLite 저장소 (Phase 0 계획, 사용자 확정: SQLite).

계산된 Signal/Trade를 저장하고 조회한다. pydantic 모델(스펙 25/27조)과
1:1로 대응하는 SQLAlchemy 테이블을 쓴다 — API/대시보드가 다시 계산하지
않고 저장된 결과를 조회할 수 있게 한다.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from sqlalchemy import Column, Date, Float, Integer, String, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from src.market.regime import RegimeType
from src.models.signal import QualityStatus, Signal, SignalState, StrategyName
from src.models.trade import ExitReason, Trade

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "screener.db"


class Base(DeclarativeBase):
    pass


class SignalRecord(Base):
    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    name = Column(String, nullable=True)
    strategy = Column(SAEnum(StrategyName), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    market_regime = Column(SAEnum(RegimeType), nullable=False)
    setup_score = Column(Float, nullable=False)
    trigger_score = Column(Float, nullable=True)
    total_score = Column(Float, nullable=False)
    signal = Column(SAEnum(SignalState), nullable=False, index=True)
    quality_status = Column(SAEnum(QualityStatus), nullable=False)
    entry = Column(Float, nullable=True)
    stop = Column(Float, nullable=True)
    target_1 = Column(Float, nullable=True)
    target_2 = Column(Float, nullable=True)
    rr_1 = Column(Float, nullable=True)
    rr_2 = Column(Float, nullable=True)
    reasons_json = Column(String, nullable=False, default="[]")


class TradeRecord(Base):
    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String, nullable=False, index=True)
    strategy = Column(SAEnum(StrategyName), nullable=False, index=True)
    entry_date = Column(Date, nullable=False)
    entry_price = Column(Float, nullable=False)
    stop = Column(Float, nullable=False)
    target = Column(Float, nullable=False)
    max_holding_days = Column(Integer, nullable=False)
    exit_date = Column(Date, nullable=True)
    exit_reason = Column(SAEnum(ExitReason), nullable=True)
    exit_price = Column(Float, nullable=True)
    return_pct = Column(Float, nullable=True)
    mfe = Column(Float, nullable=True)
    mae = Column(Float, nullable=True)
    holding_days = Column(Integer, nullable=True)


# 컬럼이 나중에 추가된 스키마 변경 이력. (table, column, SQL 타입) — 이미
# 실제 운영 DB가 쌓인 뒤 Signal에 `name`을 추가하면서 필요해졌다.
# `Base.metadata.create_all`은 이미 있는 테이블에는 새 컬럼을 만들어주지
# 않으므로, 매번 가벼운 마이그레이션으로 누락된 컬럼을 보충한다.
_COLUMN_MIGRATIONS: list[tuple[str, str, str]] = [
    ("signals", "name", "TEXT"),
]


def _ensure_columns(engine) -> None:
    with engine.connect() as conn:
        for table, column, coltype in _COLUMN_MIGRATIONS:
            existing = {row[1] for row in conn.execute(text(f"PRAGMA table_info({table})"))}
            if column not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {coltype}"))
                conn.commit()


def get_engine(db_path: Path | str = DEFAULT_DB_PATH):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    _ensure_columns(engine)
    return engine


def get_session_factory(engine) -> sessionmaker:
    return sessionmaker(bind=engine)


def save_signal(session: Session, signal: Signal) -> SignalRecord:
    record = SignalRecord(
        symbol=signal.symbol,
        name=signal.name,
        strategy=signal.strategy,
        date=signal.date,
        market_regime=signal.market_regime,
        setup_score=signal.setup_score,
        trigger_score=signal.trigger_score,
        total_score=signal.total_score,
        signal=signal.signal,
        quality_status=signal.quality_status,
        entry=signal.entry,
        stop=signal.stop,
        target_1=signal.target_1,
        target_2=signal.target_2,
        rr_1=signal.rr_1,
        rr_2=signal.rr_2,
        reasons_json=json.dumps(signal.reasons, ensure_ascii=False),
    )
    session.add(record)
    session.flush()
    return record


def save_trade(session: Session, trade: Trade) -> TradeRecord:
    record = TradeRecord(
        symbol=trade.symbol,
        strategy=trade.strategy,
        entry_date=trade.entry_date,
        entry_price=trade.entry_price,
        stop=trade.stop,
        target=trade.target,
        max_holding_days=trade.max_holding_days,
        exit_date=trade.exit_date,
        exit_reason=trade.exit_reason,
        exit_price=trade.exit_price,
        return_pct=trade.return_pct,
        mfe=trade.mfe,
        mae=trade.mae,
        holding_days=trade.holding_days,
    )
    session.add(record)
    session.flush()
    return record


def _record_to_signal(record: SignalRecord) -> Signal:
    return Signal(
        symbol=record.symbol,
        name=record.name,
        strategy=record.strategy,
        date=record.date,
        market_regime=record.market_regime,
        setup_score=record.setup_score,
        trigger_score=record.trigger_score,
        total_score=record.total_score,
        signal=record.signal,
        quality_status=record.quality_status,
        entry=record.entry,
        stop=record.stop,
        target_1=record.target_1,
        target_2=record.target_2,
        rr_1=record.rr_1,
        rr_2=record.rr_2,
        reasons=json.loads(record.reasons_json),
    )


def _record_to_trade(record: TradeRecord) -> Trade:
    return Trade(
        symbol=record.symbol,
        strategy=record.strategy,
        entry_date=record.entry_date,
        entry_price=record.entry_price,
        stop=record.stop,
        target=record.target,
        max_holding_days=record.max_holding_days,
        exit_date=record.exit_date,
        exit_reason=record.exit_reason,
        exit_price=record.exit_price,
        return_pct=record.return_pct,
        mfe=record.mfe,
        mae=record.mae,
        holding_days=record.holding_days,
    )


def query_signals(
    session: Session,
    strategy: Optional[StrategyName] = None,
    signal_state: Optional[SignalState] = None,
    symbol: Optional[str] = None,
) -> list[Signal]:
    query = session.query(SignalRecord)
    if strategy is not None:
        query = query.filter(SignalRecord.strategy == strategy)
    if signal_state is not None:
        query = query.filter(SignalRecord.signal == signal_state)
    if symbol is not None:
        query = query.filter(SignalRecord.symbol == symbol)
    return [_record_to_signal(r) for r in query.all()]


def query_trades(
    session: Session,
    strategy: Optional[StrategyName] = None,
    symbol: Optional[str] = None,
) -> list[Trade]:
    query = session.query(TradeRecord)
    if strategy is not None:
        query = query.filter(TradeRecord.strategy == strategy)
    if symbol is not None:
        query = query.filter(TradeRecord.symbol == symbol)
    return [_record_to_trade(r) for r in query.all()]
