"""momentum_signals/rs.py 단위 테스트."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rs import rs_scores_for_market  # noqa: E402

LOOKBACKS = (21, 63, 126, 252)
WEIGHTS = (0.10, 0.40, 0.30, 0.20)


class TestRsScoresForMarket(unittest.TestCase):
    def test_best_performer_gets_highest_score(self):
        returns = {
            "A": {21: 20.0, 63: 20.0, 126: 20.0, 252: 20.0},
            "B": {21: 10.0, 63: 10.0, 126: 10.0, 252: 10.0},
            "C": {21: 0.0, 63: 0.0, 126: 0.0, 252: 0.0},
        }
        scores = rs_scores_for_market(returns, LOOKBACKS, WEIGHTS)
        self.assertGreater(scores["A"], scores["B"])
        self.assertGreater(scores["B"], scores["C"])

    def test_none_when_any_period_missing(self):
        returns = {
            "A": {21: 5.0, 63: 5.0, 126: 5.0, 252: None},
            "B": {21: 5.0, 63: 5.0, 126: 5.0, 252: 5.0},
        }
        scores = rs_scores_for_market(returns, LOOKBACKS, WEIGHTS)
        self.assertIsNone(scores["A"])
        self.assertIsNotNone(scores["B"])

    def test_scores_are_within_0_100(self):
        returns = {f"S{i}": {21: float(i), 63: float(i), 126: float(i), 252: float(i)}
                   for i in range(10)}
        scores = rs_scores_for_market(returns, LOOKBACKS, WEIGHTS)
        for v in scores.values():
            self.assertGreaterEqual(v, 0.0)
            self.assertLessEqual(v, 100.0)


if __name__ == "__main__":
    unittest.main()
