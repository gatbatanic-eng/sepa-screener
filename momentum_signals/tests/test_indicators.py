"""momentum_signals/indicators.py 단위 테스트 — look-ahead 회귀 포함."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indicators import (atr, avg_trading_value, clv, disparity, period_return_pct, rolling_high,
                         rsi, sma, swing_low, volume_sma)  # noqa: E402


def _series(vals, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(vals), freq="B")
    return pd.Series(vals, index=idx, dtype=float)


class TestBasics(unittest.TestCase):
    def test_sma(self):
        s = _series([1, 2, 3, 4, 5])
        out = sma(s, 3)
        self.assertTrue(pd.isna(out.iloc[1]))
        self.assertEqual(out.iloc[2], 2.0)
        self.assertEqual(out.iloc[-1], 4.0)

    def test_rsi_all_gains_is_100(self):
        s = _series([100 + i for i in range(30)])
        out = rsi(s, 14)
        self.assertAlmostEqual(out.iloc[-1], 100.0, places=1)

    def test_disparity_positive_when_above_ma(self):
        s = _series([100] * 19 + [110])
        d = disparity(s, 20)
        self.assertGreater(d.iloc[-1], 0)

    def test_clv_top_of_range_is_one(self):
        high = _series([110])
        low = _series([90])
        close = _series([110])
        self.assertAlmostEqual(clv(high, low, close).iloc[-1], 1.0)

    def test_clv_zero_range_does_not_crash(self):
        high = _series([100])
        low = _series([100])
        close = _series([100])
        self.assertEqual(clv(high, low, close).iloc[-1], 0.0)

    def test_period_return_pct(self):
        s = _series([100, 100, 100, 100, 100, 110])
        r = period_return_pct(s, 5)
        self.assertAlmostEqual(r.iloc[-1], 10.0)

    def test_avg_trading_value(self):
        close = _series([10.0] * 25)
        vol = _series([1000.0] * 25)
        v = avg_trading_value(close, vol, 20)
        self.assertAlmostEqual(v.iloc[-1], 10000.0)


class TestNoLookahead(unittest.TestCase):
    """미래 봉을 추가해도 과거 시점 값이 안 바뀌어야 causal(look-ahead 없음)."""

    def _check(self, fn, series_kwargs):
        full = {k: _series(v) for k, v in series_kwargs.items()}
        n = len(next(iter(full.values())))
        truncated = {k: v.iloc[: n - 5] for k, v in full.items()}
        full_out = fn(**full)
        trunc_out = fn(**truncated)
        pd.testing.assert_series_equal(full_out.iloc[: n - 5], trunc_out, check_names=False)

    def test_rolling_high_no_lookahead(self):
        vals = [100 + (i % 7) for i in range(60)]
        self._check(lambda high: rolling_high(high, 20), {"high": vals})

    def test_swing_low_no_lookahead(self):
        vals = [100 - (i % 7) for i in range(60)]
        self._check(lambda low: swing_low(low, 20), {"low": vals})

    def test_atr_no_lookahead(self):
        close = [100 + (i % 5) for i in range(40)]
        high = [c + 2 for c in close]
        low = [c - 2 for c in close]
        self._check(lambda high, low, close: atr(high, low, close, 14),
                    {"high": high, "low": low, "close": close})

    def test_rsi_no_lookahead(self):
        vals = [100 + (i % 9) - (i % 4) for i in range(40)]
        self._check(lambda close: rsi(close, 14), {"close": vals})


if __name__ == "__main__":
    unittest.main()
