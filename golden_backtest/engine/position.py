"""포지션 상태. 손절선 갱신 규칙(올리기만 가능)과 MFE/MAE 추적을 한 곳에서 관리한다."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class TrancheState:
    name: str
    weight: float
    open: bool = True
    stop_intraday: float | None = None
    stop_intraday_reason: str = "stop"
    stop_close: float | None = None
    stop_close_reason: str = "stop"


@dataclass
class Position:
    ticker: str
    strategy: str
    signal_date: pd.Timestamp
    entry_index: int
    entry_date: pd.Timestamp
    entry_price: float
    initial_stop: float | None          # 손절이 없으면 None
    stop_kind: str | None
    risk: float                         # 1R (가격 단위). 손절이 있으면 진입가 − 초기 손절가, 없으면 명목 리스크
    risk_basis: str
    trade_id: int
    tranches: dict[str, TrancheState] = field(default_factory=dict)
    highest_high: float = float("-inf")  # 진입 봉부터의 최고 고가 (MFE)
    lowest_low: float = float("inf")     # 진입 봉부터의 최저 저가 (MAE)
    bars_since_entry: int = 0            # 진입 봉 = 0. 전략이 "진입 후 N거래일째"를 셀 때 쓴다

    # --- 손절선: 올리기만 한다 (롱 온리). 내리는 제안은 조용히 무시한다 ---
    def raise_intraday_stop(self, price: float, reason: str, tranche: str | None = None) -> None:
        for t in self._targets(tranche):
            if t.stop_intraday is None or price > t.stop_intraday:
                t.stop_intraday, t.stop_intraday_reason = price, reason

    def raise_close_stop(self, price: float, reason: str, tranche: str | None = None) -> None:
        for t in self._targets(tranche):
            if t.stop_close is None or price > t.stop_close:
                t.stop_close, t.stop_close_reason = price, reason

    def _targets(self, tranche: str | None) -> list[TrancheState]:
        if tranche is None:
            return self.open_tranches()
        t = self.tranches.get(tranche)
        return [t] if t is not None and t.open else []

    def open_tranches(self) -> list[TrancheState]:
        return [t for t in self.tranches.values() if t.open]

    def update_excursions(self, high: float, low: float) -> None:
        self.highest_high = max(self.highest_high, high)
        self.lowest_low = min(self.lowest_low, low)

    @property
    def mfe_r(self) -> float:
        return (self.highest_high - self.entry_price) / self.risk

    @property
    def mae_r(self) -> float:
        return (self.lowest_low - self.entry_price) / self.risk
