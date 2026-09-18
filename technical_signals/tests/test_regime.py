"""technical_signals/regime.py 단위 테스트."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from regime import GREEN, RED, YELLOW, compute_breadth, evaluate_regime  # noqa: E402


def _index_df(close_values) -> pd.DataFrame:
    n = len(close_values)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    close = pd.Series(close_values, index=idx, dtype=float)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                          "Close": close, "Volume": pd.Series([1_000_000.0] * n, index=idx)})


class TestComputeBreadth(unittest.TestCase):
    def test_empty_list_is_none(self):
        self.assertIsNone(compute_breadth([]))

    def test_fraction_true(self):
        self.assertAlmostEqual(compute_breadth([True, True, False, False]), 0.5)
        self.assertAlmostEqual(compute_breadth([True, True, True]), 1.0)


class TestEvaluateRegime(unittest.TestCase):
    def test_none_index_and_none_breadth_gives_none_regime(self):
        r = evaluate_regime(None, None)
        self.assertIsNone(r.regime)

    def test_strong_uptrend_with_good_breadth_is_green(self):
        close = np.linspace(100, 300, 300)
        r = evaluate_regime(_index_df(close), breadth=0.7)
        self.assertTrue(r.above_sma200)
        self.assertTrue(r.sma50_above_sma200)
        self.assertTrue(r.breadth_ok)
        self.assertEqual(r.regime, GREEN)

    def test_strong_downtrend_with_bad_breadth_is_red(self):
        close = np.linspace(300, 100, 300)
        r = evaluate_regime(_index_df(close), breadth=0.1)
        self.assertFalse(r.above_sma200)
        self.assertFalse(r.sma50_above_sma200)
        self.assertFalse(r.breadth_ok)
        self.assertEqual(r.regime, RED)

    def test_mixed_conditions_is_yellow(self):
        # 완만한 상승이라 지수는 SMA200 위인데 breadth는 약함 -> 3조건 중 일부만 충족
        close = np.linspace(100, 130, 300)
        r = evaluate_regime(_index_df(close), breadth=0.2)
        self.assertEqual(r.regime, YELLOW)

    def test_short_index_history_leaves_trend_fields_none(self):
        close = np.linspace(100, 110, 50)  # SMA200 계산 불가
        r = evaluate_regime(_index_df(close), breadth=0.6)
        self.assertIsNone(r.above_sma200)
        self.assertIsNone(r.regime)


if __name__ == "__main__":
    unittest.main()
