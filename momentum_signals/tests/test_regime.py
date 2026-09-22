"""momentum_signals/regime.py 단위 테스트."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from regime import evaluate_regime  # noqa: E402


def _idx_df(closes):
    idx = pd.date_range("2026-01-01", periods=len(closes), freq="B")
    return pd.DataFrame({"Close": closes}, index=idx)


class TestEvaluateRegime(unittest.TestCase):
    def test_allow_entry_when_above_sma20(self):
        closes = [100.0] * 19 + [120.0]
        r = evaluate_regime(_idx_df(closes))
        self.assertTrue(r.above_ma)
        self.assertTrue(r.allow_new_entry)

    def test_block_entry_when_below_sma20(self):
        closes = [100.0] * 19 + [80.0]
        r = evaluate_regime(_idx_df(closes))
        self.assertFalse(r.above_ma)
        self.assertFalse(r.allow_new_entry)

    def test_none_when_insufficient_data(self):
        r = evaluate_regime(_idx_df([100.0] * 5))
        self.assertIsNone(r.above_ma)
        self.assertIsNone(r.allow_new_entry)


if __name__ == "__main__":
    unittest.main()
