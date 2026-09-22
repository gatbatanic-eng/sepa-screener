"""momentum_signals/scoring.py 단위 테스트 — 하드 게이트, 강도점수, 상위5 병합."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as cfg  # noqa: E402
from scoring import hard_gate, score, select_top5_balanced  # noqa: E402
from signals import StockMetrics  # noqa: E402


def _mk(**kw) -> StockMetrics:
    return StockMetrics(code="X", name="X", market="KR", **kw)


class TestHardGate(unittest.TestCase):
    def test_passes_when_both_conditions_met(self):
        g = hard_gate(_mk(volume_ratio=1.3, risk_pct=5.0))
        self.assertTrue(g.passed)

    def test_fails_when_volume_below_threshold(self):
        g = hard_gate(_mk(volume_ratio=1.1, risk_pct=5.0))
        self.assertFalse(g.passed)

    def test_fails_when_risk_above_threshold(self):
        g = hard_gate(_mk(volume_ratio=1.3, risk_pct=6.0))
        self.assertFalse(g.passed)

    def test_unknown_when_missing_data(self):
        g = hard_gate(_mk(volume_ratio=None, risk_pct=5.0))
        self.assertIsNone(g.passed)


class TestScore(unittest.TestCase):
    def test_full_marks_when_everything_optimal(self):
        m = _mk(rs_score=100.0, volume_ratio=1.5, pivot_distance_pct=0.0, clv_value=0.6,
                rsi_value=65.0, disparity20=10.0, risk_pct=0.0)
        s = score(m)
        self.assertAlmostEqual(s.total, 100.0, places=1)

    def test_zero_when_all_missing(self):
        s = score(_mk())
        self.assertEqual(s.total, 0.0)

    def test_pivot_score_decays_outside_optimal_band(self):
        near = score(_mk(pivot_distance_pct=0.0)).pivot
        far = score(_mk(pivot_distance_pct=10.0)).pivot
        self.assertGreater(near, far)

    def test_rsi_score_decays_outside_band(self):
        inside = score(_mk(rsi_value=60.0)).rsi
        outside = score(_mk(rsi_value=30.0)).rsi
        self.assertGreater(inside, outside)

    def test_disparity_score_full_below_cap_decays_above(self):
        low = score(_mk(disparity20=5.0)).disparity
        high = score(_mk(disparity20=40.0)).disparity
        self.assertEqual(low, cfg.SCORE_WEIGHTS["disparity"])
        self.assertLess(high, low)

    def test_risk_efficiency_higher_when_risk_smaller(self):
        tight = score(_mk(risk_pct=1.0)).risk_efficiency
        loose = score(_mk(risk_pct=5.0)).risk_efficiency
        self.assertGreater(tight, loose)


class TestSelectTop5Balanced(unittest.TestCase):
    def test_balances_across_markets_even_if_one_market_dominates_by_rs(self):
        rs_by_code = {}
        kr_codes, us_codes = [], []
        for i in range(10):
            code = f"KR{i}"
            kr_codes.append(code)
            rs_by_code[code] = (None, 100.0 - i)  # 국내가 RS 상위를 싹쓸이
        for i in range(3):
            code = f"US{i}"
            us_codes.append(code)
            rs_by_code[code] = (None, 50.0 - i)
        top5 = select_top5_balanced(rs_by_code, kr_codes, us_codes, n=5)
        self.assertEqual(len(top5), 5)
        self.assertGreaterEqual(sum(1 for c in top5 if c.startswith("US")), 2)

    def test_excludes_codes_with_no_rs_score(self):
        rs_by_code = {"KR0": (None, 90.0), "KR1": (None, None)}
        top5 = select_top5_balanced(rs_by_code, ["KR0", "KR1"], [], n=5)
        self.assertEqual(top5, ["KR0"])


if __name__ == "__main__":
    unittest.main()
