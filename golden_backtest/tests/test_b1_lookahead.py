"""B1 접두 불변성: 신호 열 직접 검사 + 엔진 거래 검사(두 가지 모두 필수), 누수 변형이 적발되는지까지."""
from __future__ import annotations

import unittest

from golden_backtest.strategies.b1_turtle import B1Turtle
from golden_backtest.tests.lookahead import synthetic_ohlcv
from golden_backtest.tests.strategy_checks import signal_violations, trade_violations

COLS = ["n", "entry_level", "exit_level"]


def make():
    return B1Turtle(entry_n=20, exit_n=10, atr_n=14, trade_start=None)   # 합성 데이터에서 거래가 충분히 나오도록 작은 값


class LeakyB1(B1Turtle):
    """일부러 미래를 쓰는 변형: 수준을 중앙 정렬 rolling으로 계산한다."""

    def prepare(self, df):
        out = super().prepare(df)
        out["entry_level"] = out["high"].rolling(self.entry_n, center=True, min_periods=self.entry_n).max()
        return out


class TestB1PrefixInvariance(unittest.TestCase):
    def setUp(self):
        self.df = synthetic_ohlcv(900, seed=31)
        self.cuts = list(range(120, 900, 37))

    def test_signal_columns_are_prefix_invariant(self):
        res = signal_violations(make, self.df, COLS, self.cuts)
        self.assertEqual(res, {c: [] for c in COLS})

    def test_engine_trades_are_prefix_invariant(self):
        n, bad = trade_violations(make, self.df, self.cuts)
        self.assertGreater(n, 10, "검사가 의미 있으려면 거래가 충분해야 한다")
        self.assertEqual(bad, [])

    def test_leaky_variant_is_caught_by_signal_check(self):
        res = signal_violations(lambda: LeakyB1(entry_n=20, exit_n=10, atr_n=14, trade_start=None), self.df, COLS, self.cuts)
        self.assertTrue(res["entry_level"], "미래를 쓰는 신호가 신호 열 검사를 통과했다 — 검사가 무력하다")

    def test_default_spec_parameters_are_prefix_invariant_too(self):
        res = signal_violations(lambda: B1Turtle(trade_start=None), self.df, COLS, self.cuts)
        self.assertEqual(res, {c: [] for c in COLS})


if __name__ == "__main__":
    unittest.main()
