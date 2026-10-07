"""전략 기본 틀. 전략은 주문 의도만 반환한다 — 체결가·비용 계산은 engine에서만 한다 (CLAUDE.md 절대 원칙 3).

의존 방향: strategies → engine 만 허용.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from golden_backtest.engine.intents import EntryIntent, ExitIntent, StopSpec


class Strategy(ABC):
    code: str          # "B3"
    version: str       # "1.0" (전략 구현 버전. 규격 버전과 다르다)
    entry_model: str   # "M1" | "M2"

    @abstractmethod
    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        """벡터화로 지표·셋업 조건 계산. t일 종가까지만 사용한다(누수 금지)."""

    @abstractmethod
    def entry_intent(self, row: pd.Series) -> EntryIntent | None:
        """t일(row) 기준으로 t+1에 쓸 주문. M1: next_open / M2: buy_stop(가격)."""

    @abstractmethod
    def initial_stop(self, row: pd.Series, fill_price: float) -> StopSpec:
        """체결가를 안 뒤의 초기 손절. row는 신호일 t의 행이다."""

    @abstractmethod
    def manage(self, position, row: pd.Series) -> list[ExitIntent]:
        """방금 마감한 봉(row) 기준 손절선 갱신, 트랜치 청산, 시간 청산. 상태 기반."""
