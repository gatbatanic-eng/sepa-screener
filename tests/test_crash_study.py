import unittest

import numpy as np
import pandas as pd

from crash_study import combo_study as C
from crash_study.study import dd_events


class CrashStudyTest(unittest.TestCase):
    def test_sim_buy_and_hold_matches_cumprod(self):
        rng = np.random.default_rng(1)
        r = rng.normal(0.001, 0.02, 500)
        eq, _ = C.sim(r, np.zeros(500), np.ones(500), lag=0, fee=0.0)
        np.testing.assert_allclose(eq[-1], np.prod(1 + r[1:]), rtol=1e-9)  # 0일 종가에 매수 → 1일부터 수익

    def test_stop_exits_and_blocks_reentry(self):
        # 고점 100 → 85(-15%, -10% 신호) → 계속 하락 → 70 에서 진입가 대비 -20% 이하
        c = pd.Series([100, 100, 85, 84, 80, 75, 70, 65, 90, 100.0])
        ev = dd_events(c, -0.10)
        self.assertEqual(ev, [2])
        T, tr = C.overlay(c, c, ev, None, 0.20, lag=1)
        self.assertTrue(tr[0]["stopped"])
        # 진입 idx3(84) → 손절 결정은 67.2 이하인 idx7(65)이므로 idx7 부터 0
        self.assertEqual(list(T[:8]), [0, 0, 1, 1, 1, 1, 1, 0])
        T2, tr2 = C.overlay(c, c, ev, None, None, lag=1)
        self.assertFalse(tr2[0]["stopped"])
        self.assertEqual(T2[2:8].sum(), 6)

    def test_sim_band_blocks_small_changes(self):
        n = 50
        T = np.full(n, 0.5)
        T[20:] = 0.55
        eq, w = C.sim(np.zeros(n), np.zeros(n), T, lag=0, band=0.10, fee=0.0)
        self.assertAlmostEqual(w[-1], 0.5)


if __name__ == "__main__":
    unittest.main()
