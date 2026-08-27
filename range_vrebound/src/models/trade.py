"""백테스트 체결 기록 스키마 (스펙 27조).

entry_date/entry_price는 Phase 0 계획 제안 3에 따라 신호일(T) 종가로 계산된
스탑/타깃을 T+1일 시가로 체결한 결과를 담는다.
"""
from __future__ import annotations

from datetime import date as date_
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from src.models.signal import StrategyName


class ExitReason(str, Enum):
    STOP = "STOP"
    TARGET = "TARGET"
    TIME_EXIT = "TIME_EXIT"
    INVALIDATED = "INVALIDATED"


class Trade(BaseModel):
    symbol: str
    strategy: StrategyName

    entry_date: date_
    entry_price: float
    stop: float
    target: float
    max_holding_days: int = Field(gt=0)

    exit_date: Optional[date_] = None
    exit_reason: Optional[ExitReason] = None
    exit_price: Optional[float] = None

    return_pct: Optional[float] = None
    mfe: Optional[float] = None  # Maximum Favorable Excursion
    mae: Optional[float] = None  # Maximum Adverse Excursion
    holding_days: Optional[int] = None
