"""엔진 + 전략 접두 불변성: 데이터를 t일에서 잘라 돌린 결과와 전체 데이터 결과의 t일까지 확정 거래가 같아야 한다.

(전략이 생기면 같은 헬퍼로 각 전략에 이 검사를 건다. 여기서는 더미 규칙 전략으로 엔진 자체를 검증하고,
미래를 쓰는 전략을 일부러 넣어 검사 틀이 위반을 잡는지도 확인한다.)
"""
from __future__ import annotations

import unittest

import numpy as np

from golden_backtest.engine.intents import EntryIntent, ExitIntent, StopSpec
from golden_backtest.engine.simulator import simulate
from golden_backtest.tests.engine_helpers import COSTS, SPEC
from golden_backtest.tests.lookahead import find_prefix_violations, synthetic_ohlcv


class RuleStrategy:
    """20일선 상향 돌파(M1) / 5% 아래 장중 손절 / 10일선 종가 이탈 추적 손절. t일 종가까지만 쓴다."""
    code, version, entry_model = "R", "0", "M1"
    LEAK = False

    def prepare(self, df):
        out = df.copy()
        out["sma20"] = out["close"].rolling(20).mean()
        out["sma10"] = out["close"].rolling(10).mean()
        out["signal"] = (out["close"] > out["sma20"]) & (out["close"].shift(1) <= out["sma20"].shift(1))
        if self.LEAK:  # 내일 종가가 오늘보다 높을 때만 신호: 미래 정보
            out["signal"] = out["close"].shift(-1) > out["close"] * 1.01
        return out

    def entry_intent(self, row):
        return EntryIntent("next_open") if bool(row["signal"]) else None

    def initial_stop(self, row, fill_price):
        return StopSpec(fill_price * 0.95, "intraday")

    def manage(self, position, row):
        return [ExitIntent("close", "trailing", price=float(row["sma10"]))] if np.isfinite(row["sma10"]) else []


class LeakyStrategy(RuleStrategy):
    LEAK = True


def closed_by(res, cut_date):
    """cut_date까지 확정된 거래(청산일 ≤ cut_date, 미청산 제외)를 비교 가능한 튜플로."""
    return [(t.entry_date, t.exit_date, round(t.entry_price, 8), round(t.exit_price, 8), t.exit_reason, round(t.r_multiple, 10))
            for t in res.confirmed() if t.exit_date <= cut_date]


class TestEnginePrefixInvariance(unittest.TestCase):
    """두 단계로 검사한다.

    1. prepare() 열 접두 불변성(lookahead.find_prefix_violations): 신호가 미래 봉을 쓰는지 직접 잡는다.
    2. 엔진 거래 접두 불변성: 잘라 돌린 확정 거래가 전체 실행의 같은 구간과 같은지.
       거래 수준 검사만으로는 한 봉짜리 누수를 못 잡는다 — 누수가 영향을 주는 마지막 봉의 신호는 잘린 데이터 밖(t+1)에서 체결되기 때문이다.
       그래서 1번이 필요하다. 2번은 엔진의 체결·청산 로직이 미래 봉을 쓰지 않는지(상태가 새어 나가지 않는지) 확인한다.
    """

    def setUp(self):
        self.df = synthetic_ohlcv(900, seed=21)
        self.cuts = list(range(120, 900, 37))

    def _trade_violations(self, strategy_cls):
        full = simulate(self.df, strategy_cls(), "X", COSTS, SPEC)
        bad = []
        for cut in self.cuts:
            part = simulate(self.df.iloc[: cut + 1], strategy_cls(), "X", COSTS, SPEC)
            cut_date = self.df.index[cut]
            if closed_by(full, cut_date) != closed_by(part, cut_date):
                bad.append(cut)
        return full, bad

    def _signal_violations(self, strategy_cls):
        return find_prefix_violations(lambda d: strategy_cls().prepare(d)["signal"].astype(float), self.df, cuts=self.cuts)

    def test_rule_strategy_signal_is_prefix_invariant(self):
        self.assertEqual(self._signal_violations(RuleStrategy), [])

    def test_rule_strategy_trades_are_prefix_invariant(self):
        full, bad = self._trade_violations(RuleStrategy)
        self.assertGreater(len(full.confirmed()), 5, "검사가 의미 있으려면 거래가 충분해야 한다")
        self.assertEqual(bad, [])

    def test_leaky_strategy_signal_is_caught(self):
        self.assertTrue(self._signal_violations(LeakyStrategy), "미래를 쓰는 신호가 접두 불변성 검사를 통과했다 — 검사 틀이 무력하다")


if __name__ == "__main__":
    unittest.main()
