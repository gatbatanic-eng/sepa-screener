import unittest
import pandas as pd
from leader_backtest.robustness import robustness


class RobustnessTests(unittest.TestCase):
    def test_known_excess_and_deterministic_bootstrap(self):
        rows = []
        for i in range(50):
            rows.append({"date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=i),
                         "code": f"X{i}", "return20": .12, "benchmark20": .02,
                         "v1": True, "opportunity": True, "entry": True})
        out = robustness(pd.DataFrame(rows))
        x = out["groups"]["opportunity"]
        self.assertEqual(x["episodes"], 50)
        self.assertEqual(x["median_excess_pct"], 10.0)
        self.assertEqual(x["winsor_5pct_mean_excess_pct"], 10.0)
        self.assertEqual(x["trim_5pct_mean_excess_pct"], 10.0)
        self.assertEqual(x["equal_day_block_bootstrap_ci95_pct"], [10.0, 10.0])

    def test_duplicate_rejected(self):
        row = {"date": "2024-01-01", "code": "X", "return20": .1,
               "benchmark20": .02, "v1": True, "opportunity": True, "entry": True}
        with self.assertRaises(ValueError):
            robustness(pd.DataFrame([row, row]))


if __name__ == "__main__":
    unittest.main()
