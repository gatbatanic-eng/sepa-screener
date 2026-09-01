"""모든 전략(RANGE-MR, V-REBOUND)이 공유하는 신호 스키마 (스펙 25조).

RANGE-MR과 V-REBOUND는 동일한 Signal 스키마를 사용한다. trigger_score는
RANGE-MR에서만 별도로 계산되므로(스펙 11/12조) V-REBOUND에서는 None으로
남겨둔다.
"""
from __future__ import annotations

from datetime import date as date_
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from src.market.regime import RegimeType


class StrategyName(str, Enum):
    RANGE_MR = "RANGE_MR"
    V_REBOUND = "V_REBOUND"


class SignalState(str, Enum):
    SETUP = "SETUP"
    WATCH = "WATCH"
    TRIGGER = "TRIGGER"
    BUY_CANDIDATE = "BUY_CANDIDATE"
    INVALIDATED = "INVALIDATED"


class QualityStatus(str, Enum):
    OK = "OK"
    UNKNOWN = "UNKNOWN"


class Signal(BaseModel):
    symbol: str
    name: Optional[str] = None  # 종목명 (표시용, 스펙 25조 스키마에는 없는 추가 필드)
    strategy: StrategyName
    date: date_
    market_regime: RegimeType

    setup_score: float = Field(ge=0, le=100)
    trigger_score: Optional[float] = Field(default=None, ge=0, le=100)
    total_score: float = Field(ge=0, le=100)

    signal: SignalState
    quality_status: QualityStatus = QualityStatus.OK

    entry: Optional[float] = None
    stop: Optional[float] = None
    target_1: Optional[float] = None
    target_2: Optional[float] = None
    rr_1: Optional[float] = None
    rr_2: Optional[float] = None

    reasons: list[str] = Field(default_factory=list)
