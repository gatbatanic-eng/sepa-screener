import unittest
import numpy as np
import pandas as pd

from aggressive_screen import CONFIG, evaluate, indicators


class AggressiveScreenTests(unittest.TestCase):
    def frame(self):
        dates = pd.bdate_range("2025-01-01", periods=260)
        close = np.linspace(70, 99, len(dates))
        high = close + 0.6
        low = close - 0.6
        volume = np.full(len(dates), 20_000_000.0)
        high[-21:-1] = np.minimum(high[-21:-1], 99.4)
        close[-1], high[-1], low[-1], volume[-1] = 100.0, 100.4, 99.0, 40_000_000.0
        return pd.DataFrame({"Open": close - .2, "High": high, "Low": low, "Close": close, "Volume": volume}, index=dates)

    def row(self):
        return {"code": "X", "name": "X", "market": "US", "status": "OK", "inUniverse": True, "rsScore": 90, "regime": "GREEN"}

    def test_breakout_signal_and_risk_fields(self):
        result = evaluate(self.row(), self.frame(), "us")
        self.assertEqual(result["status"], "OK")
        self.assertTrue(result["breakout"])
        self.assertTrue(result["aggressiveGo"])
        self.assertLessEqual(result["initialRiskPct"], CONFIG["maxInitialRiskPct"])
        self.assertGreater(result["referenceStop"], 0)

    def test_red_market_blocks_entry(self):
        row = self.row(); row["regime"] = "RED"
        result = evaluate(row, self.frame(), "us")
        self.assertFalse(result["marketOk"])
        self.assertFalse(result["aggressiveGo"])

    def test_missing_data_is_unknown_not_false_signal(self):
        frame = self.frame(); frame.loc[frame.index[-1], "Close"] = np.nan
        result = evaluate(self.row(), frame, "us")
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["aggressiveGo"])

    def test_indicators_use_prior_high_and_volume(self):
        frame = self.frame(); data = indicators(frame)
        self.assertLess(data.breakoutLevel.iloc[-1], data.Close.iloc[-1])
        self.assertAlmostEqual(data.volumeRatio.iloc[-1], 2.0)


if __name__ == "__main__":
    unittest.main()
