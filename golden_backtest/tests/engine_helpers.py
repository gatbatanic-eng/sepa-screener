"""엔진 테스트용 도구: 손으로 만든 일봉과, 의도를 대본대로 내는 전략."""
from __future__ import annotations

import pandas as pd

from golden_backtest.engine.costs import CostRate, Costs
from golden_backtest.engine.intents import EntryIntent, ExitIntent, StopSpec

# 편도 0.1% (수수료 0.05% + 슬리피지 0.05%). 손계산이 쉽도록 모든 테스트가 이 값을 쓴다
COSTS = Costs(CostRate(0.0005, 0.0005))
SPEC = "1.3"


def make_df(rows, start="2024-01-02") -> pd.DataFrame:
    """rows: (시가, 고가, 저가, 종가) 목록. 봉 번호는 0부터이고 날짜는 영업일 순서."""
    idx = pd.bdate_range(start, periods=len(rows))
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"], dtype=float)


class ScriptedStrategy:
    """entries: {신호 봉 번호: EntryIntent}, stop: StopSpec(또는 체결가를 받는 함수),
    manage: {봉 번호: [ExitIntent, ...]} — 그 봉 마감 후 manage()가 낼 의도."""

    code = "T"
    version = "0.1"
    entry_model = "M1"

    def __init__(self, entries=None, stop=None, manage=None):
        self.entries = entries or {}
        self.stop = stop
        self.manage_script = manage or {}
        self._pos: dict = {}

    def prepare(self, df):
        self._pos = {ts: i for i, ts in enumerate(df.index)}
        return df

    def entry_intent(self, row):
        return self.entries.get(self._pos[row.name])

    def initial_stop(self, row, fill_price):
        return self.stop(fill_price) if callable(self.stop) else self.stop

    def manage(self, position, row):
        return list(self.manage_script.get(self._pos[row.name], []))


NEXT_OPEN = EntryIntent("next_open")


def buy_stop(level, tranches=None):
    return EntryIntent("buy_stop", level, tranches or {"ALL": 1.0})


def intraday_stop(price):
    return StopSpec(price, "intraday")


def close_stop(price):
    return StopSpec(price, "close")


def exit_next_open(reason="rule", tranche=None):
    """종가에서 조건을 확인한 규칙 청산 → 다음 봉 시가."""
    return ExitIntent("close", reason, tranche=tranche)
