import unittest
import pandas as pd

from leader_backtest.opportunity import classify, evaluate


class OpportunityTest(unittest.TestCase):
    def test_decision_separation_and_no_outcome_dependency(self):
        dates = pd.to_datetime(["2026-01-01"] * 3)
        p = pd.DataFrame({
            "date": dates, "code": ["A", "B", "C"], "ok": [True] * 3,
            "trend": [True, True, False], "rs": [92, 91, 20],
            "risk": [6, 15, 3], "rsi": [60, 80, 40],
            "pivDist": [2, 8, -3], "volRatio": [2, 1, 1],
            "pullback": [False] * 3,
        })
        out = classify(p)
        self.assertEqual(out["decision"].tolist(), ["ENTRY", "WAIT", "REJECT"])
        self.assertEqual(out["opportunity"].tolist(), [True, True, False])

    def test_no_future_leakage_and_unmatured_exclusion(self):
        dates = pd.date_range("2026-01-01", periods=25, freq="B")
        closes = pd.DataFrame({"A": [100.0] * 25}, index=dates)
        closes.iloc[21, 0] = 120
        s = pd.DataFrame({
            "date": [dates[0], dates[-1]], "code": ["A", "A"],
            "opportunity": [True, True], "entry": [False, False],
            "decision": ["WAIT", "WAIT"],
        })
        result = evaluate(s, closes, horizon=20)
        self.assertEqual(result["matured"], 1)
        self.assertEqual(result["good"], 1)
        self.assertEqual(result["opportunity_recall"], 1.0)

    def test_duplicate_signal_rejected(self):
        p = pd.DataFrame({"date": [1, 1], "code": ["A", "A"]})
        with self.assertRaises(ValueError):
            classify(p)


if __name__ == "__main__":
    unittest.main()
