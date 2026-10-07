"""sepa/indicators.py와의 교차검증. sepa는 이 파일(테스트)에서만 import한다. 입력이 같으면 출력이 같아야 한다."""
from __future__ import annotations

import unittest

import numpy as np

from golden_backtest import indicators as ind
from golden_backtest.tests.lookahead import synthetic_ohlcv
from sepa import indicators as sepa_ind


class CrossCheck(unittest.TestCase):
    def setUp(self):
        self.df = synthetic_ohlcv(900, seed=11)

    def assertSame(self, a, b):
        np.testing.assert_allclose(a.values, b.values, rtol=1e-12, atol=1e-12, equal_nan=True)

    def test_sma(self):
        for n in (5, 20, 50, 200):
            self.assertSame(ind.sma(self.df["close"], n), sepa_ind.sma(self.df["close"], n))

    def test_ema(self):
        for n in (10, 20, 50):
            self.assertSame(ind.ema(self.df["close"], n), sepa_ind.ema(self.df["close"], n))

    def test_atr(self):
        d = self.df
        for n in (14, 20, 42):
            self.assertSame(ind.atr(d["high"], d["low"], d["close"], n), sepa_ind.atr(d["high"], d["low"], d["close"], n))

    def test_true_range(self):
        d = self.df
        self.assertSame(ind.true_range(d["high"], d["low"], d["close"]), sepa_ind.true_range(d["high"], d["low"], d["close"]))


if __name__ == "__main__":
    unittest.main()
