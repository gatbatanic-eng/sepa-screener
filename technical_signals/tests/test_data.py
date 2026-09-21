import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from data import clean_ohlcv  # noqa: E402


class TestCleanOhlcv(unittest.TestCase):
    def test_drops_trailing_nan_close_bar(self):
        idx = pd.date_range("2026-09-15", periods=4, freq="D")
        df = pd.DataFrame(
            {"Open": [1, 2, 3, np.nan], "High": [1, 2, 3, np.nan], "Low": [1, 2, 3, np.nan],
             "Close": [1.0, 2.0, 3.0, np.nan], "Volume": [10, 10, 10, 0]},
            index=idx,
        )
        out = clean_ohlcv(df)
        self.assertEqual(len(out), 3)
        self.assertEqual(out["Close"].iloc[-1], 3.0)

    def test_keeps_complete_frame_and_handles_empty(self):
        idx = pd.date_range("2026-09-15", periods=2, freq="D")
        df = pd.DataFrame({"Close": [1.0, 2.0]}, index=idx)
        self.assertEqual(len(clean_ohlcv(df)), 2)
        self.assertTrue(clean_ohlcv(pd.DataFrame()).empty)


if __name__ == "__main__":
    unittest.main()
