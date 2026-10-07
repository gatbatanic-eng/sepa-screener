"""B1 터틀 System 2 (규격 v1.5). 주문 의도만 만든다 — 체결가·비용은 engine에서만 계산한다.

규칙과 출처 표기
- 진입 [원전, M2]: 직전 55일 최고가에 매수 스탑. 신호일 t의 종가 마감 후 수준 = 최근 55봉 최고 고가(t 포함)이고,
  이는 t+1 봉 입장에서 '직전 55일'이다(indicators.donchian_high(high, 55)의 t+1 값과 같다).
- 초기 손절 [원전]: 진입가 − 2N, 장중 스탑. N = 신호일 t의 20일 ATR(Wilder).
- 청산 [원전]: 직전 20일 최저가 이탈, 장중 스탑. 매 봉 마감 후 최근 20봉 최저 저가(t 포함)를 손절선으로 낸다.
  엔진이 손절선을 올리기만 하므로 "2N 손절과 20일 저가 이탈 중 먼저 닿는 쪽"과 같다.
- [단순화] 제외: ½N 피라미딩(v2), 숏.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from golden_backtest import config
from golden_backtest.engine.intents import EntryIntent, ExitIntent, StopSpec
from golden_backtest.indicators import atr
from golden_backtest.strategies.base import Strategy


class B1Turtle(Strategy):
    code = "B1"
    entry_model = "M2"

    def __init__(self, entry_n: int | None = None, exit_n: int | None = None, atr_n: int | None = None,
                 stop_mult: float | None = None, trade_start: str | None | bool = False):
        """인자를 주지 않으면 config/strategies/b1_turtle.yaml 값을 쓴다(테스트에서만 작은 값으로 바꾼다)."""
        cfg = config.load("strategies/b1_turtle")
        self.version = cfg["version"]
        self.entry_n = cfg["entry_n"] if entry_n is None else entry_n
        self.exit_n = cfg["exit_n"] if exit_n is None else exit_n
        self.atr_n = cfg["atr_n"] if atr_n is None else atr_n
        self.stop_mult = cfg["stop_mult"] if stop_mult is None else stop_mult
        start = cfg["trade_start"] if trade_start is False else trade_start
        self.trade_start = pd.Timestamp(start) if start else None

    def prepare(self, df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["n"] = atr(out["high"], out["low"], out["close"], self.atr_n)                               # N (신호일 t까지)
        out["entry_level"] = out["high"].rolling(self.entry_n, min_periods=self.entry_n).max()          # 다음 봉의 매수 스탑 수준
        out["exit_level"] = out["low"].rolling(self.exit_n, min_periods=self.exit_n).min()              # 다음 봉의 청산 스탑 수준
        return out

    def entry_intent(self, row: pd.Series) -> EntryIntent | None:
        if self.trade_start is not None and row.name < self.trade_start:
            return None
        level, n = row["entry_level"], row["n"]
        if not (np.isfinite(level) and np.isfinite(n) and n > 0):
            return None
        return EntryIntent("buy_stop", float(level))

    def initial_stop(self, row: pd.Series, fill_price: float) -> StopSpec:
        risk = self.stop_mult * float(row["n"])  # N은 신호일 값
        if fill_price - risk <= 0:
            # 2N이 가격보다 큰 극단 케이스: 손절가가 0 이하라 닿을 수 없다. 손절 없이 명목 리스크 2N으로 R을 계산하고 기록에 표시한다 [임의]
            return StopSpec(None, None, nominal_risk=risk, risk_basis="nominal_2n_stop_below_zero")
        return StopSpec(fill_price - risk, "intraday", reason="stop")

    def manage(self, position, row: pd.Series) -> list[ExitIntent]:
        level = row["exit_level"]
        if not np.isfinite(level):
            return []
        return [ExitIntent("intraday", "rule", price=float(level))]
