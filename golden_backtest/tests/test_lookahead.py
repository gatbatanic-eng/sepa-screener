"""누수 테스트 틀: (1) 틀 자체가 누수를 잡아내는지 (2) 모든 지표가 접두 불변인지. 전략이 생기면 3번째 클래스를 더한다."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from golden_backtest import indicators as ind
from golden_backtest.tests.lookahead import find_prefix_violations, synthetic_ohlcv


class HarnessDetectsLeaks(unittest.TestCase):
    """일부러 미래를 쓰는 함수를 넣어, 검사 틀이 실제로 위반을 잡는지 확인한다(틀이 항상 통과하면 의미가 없다)."""

    def setUp(self):
        self.df = synthetic_ohlcv()

    def test_shift_negative_is_caught(self):
        self.assertTrue(find_prefix_violations(lambda d: d["close"].shift(-1), self.df))

    def test_centered_rolling_is_caught(self):
        self.assertTrue(find_prefix_violations(lambda d: d["close"].rolling(5, center=True).mean(), self.df))

    def test_full_period_normalization_is_caught(self):
        self.assertTrue(find_prefix_violations(lambda d: (d["close"] - d["close"].mean()) / d["close"].std(), self.df))

    def test_causal_function_passes(self):
        self.assertEqual(find_prefix_violations(lambda d: d["close"].rolling(5).mean(), self.df), [])


class IndicatorsArePrefixInvariant(unittest.TestCase):
    def setUp(self):
        self.df = synthetic_ohlcv()

    def check(self, fn):
        self.assertEqual(find_prefix_violations(fn, self.df), [])

    def test_sma(self):
        self.check(lambda d: ind.sma(d["close"], 20))

    def test_ema(self):
        self.check(lambda d: ind.ema(d["close"], 20))

    def test_atr(self):
        self.check(lambda d: ind.atr(d["high"], d["low"], d["close"], 14))

    def test_rsi(self):
        self.check(lambda d: ind.rsi(d["close"], 2))
        self.check(lambda d: ind.rsi(d["close"], 14))

    def test_donchian(self):
        self.check(lambda d: ind.donchian_high(d["high"], 55))
        self.check(lambda d: ind.donchian_low(d["low"], 20))

    def test_adr(self):
        self.check(lambda d: ind.adr_pct(d["high"], d["low"], 20))

    def test_rs_pct(self):
        # 열 3개(상장 시점이 다른 종목 포함) + 지수. 종목 열 집합은 고정, 접두 절단은 행(날짜)만.
        base = synthetic_ohlcv(seed=1)["close"]
        panel = pd.DataFrame({"A": base, "B": synthetic_ohlcv(seed=2)["close"], "C": synthetic_ohlcv(seed=3)["close"]})
        panel.loc[panel.index[:200], "C"] = np.nan  # 늦게 상장한 종목
        bench = synthetic_ohlcv(seed=4)["close"]

        def fn(d):
            return ind.rs_pct(ind.relative_return(d, bench.reindex(d.index), 252))

        self.assertEqual(find_prefix_violations(fn, panel, cuts=[260, 300, 400, 599]), [])


if __name__ == "__main__":
    unittest.main()
