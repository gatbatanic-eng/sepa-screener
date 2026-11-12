import unittest
import pandas as pd
from leader_backtest.episodes import episode_metrics


class EpisodeTests(unittest.TestCase):
    def test_first_signal_and_cooldown(self):
        rows = []
        for i in range(45):
            rows.append({"date": f"2026-01-{i+1:02d}", "code": "AAA",
                         "good": i in (0, 21, 42), "return20": .2 if i in (0, 21, 42) else -.1,
                         "v1": False, "opportunity": True, "entry": False})
        r = episode_metrics(pd.DataFrame(rows), horizon=20)
        self.assertEqual(r["groups"]["opportunity"]["episodes"], 3)
        self.assertEqual(r["groups"]["opportunity"]["good_episodes"], 3)
        self.assertEqual(r["groups"]["opportunity"]["precision_pct"], 100.0)
        self.assertEqual(r["groups"]["opportunity"]["median_return20_pct"], 20.0)
        self.assertIsNone(r["groups"]["opportunity"]["mean_excess20_pct"])
        self.assertEqual(r["groups"]["opportunity"]["loss_rate_pct"], 0.0)
        self.assertEqual(r["groups"]["opportunity"]["net_mean_50bp_cost_pct"], 19.5)
        self.assertEqual(r["groups"]["entry"]["episodes"], 0)

    def test_benchmark_excess(self):
        rows = [{"date": "2026-01-01", "code": "AAA", "good": True,
                 "return20": .20, "benchmark20": .08,
                 "v1": True, "opportunity": True, "entry": True}]
        r = episode_metrics(pd.DataFrame(rows))
        self.assertEqual(r["groups"]["opportunity"]["mean_excess20_pct"], 12.0)
        self.assertEqual(r["groups"]["opportunity"]["net_excess_50bp_pct"], 11.5)

    def test_reject_duplicates(self):
        row = {"date": "2026-01-01", "code": "A", "good": True,
               "return20": .2, "v1": True, "opportunity": True, "entry": True}
        with self.assertRaises(ValueError):
            episode_metrics(pd.DataFrame([row, row]))


if __name__ == "__main__":
    unittest.main()
