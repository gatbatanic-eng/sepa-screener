import unittest

import numpy as np
import pandas as pd

from leader_backtest.index_filter import evaluate, metrics, run_filter


class FilterTest(unittest.TestCase):
    def test_no_lookahead_and_cash_when_below(self):
        idx = pd.bdate_range("2020-01-01", periods=8)
        c = pd.Series([10, 10, 10, 11, 12, 6, 6, 6.0], index=idx)
        d = run_filter(c, 3, 0.0)
        self.assertAlmostEqual(d.iloc[3], 0.0)                 # t=3 종가에서 신호가 켜져도 t=3 수익(+10%)은 못 얻는다(미래 정보 금지)
        self.assertAlmostEqual(d.iloc[4], 12 / 11 - 1)         # 다음 날(t=4)부터 보유
        self.assertAlmostEqual(d.iloc[5], 6 / 12 - 1)          # t=4 종가에서도 신호가 켜져 있었으므로 급락일을 맞는다
        self.assertAlmostEqual(d.iloc[6], 0.0)                 # t=5 종가에서 꺼졌으므로 이후 현금

    def test_crash_avoided_when_signal_flips_before_drop_day(self):
        idx = pd.bdate_range("2020-01-01", periods=12)
        c = pd.Series([10, 11, 12, 13, 14, 15, 14, 12, 8, 7, 7, 7.0], index=idx)
        f = (1 + run_filter(c, 3, 0.0)).prod()
        bh = c.iloc[-1] / c.iloc[0]
        self.assertGreater(f, bh)                               # 하락 대부분을 현금으로 비껴감

    def test_cost_applies_on_switch_only(self):
        idx = pd.bdate_range("2020-01-01", periods=10)
        c = pd.Series(np.linspace(10, 20, 10), index=idx)
        a, b = run_filter(c, 3, 0.0), run_filter(c, 3, 0.01)
        self.assertAlmostEqual(float((a - b).sum()), 0.01)     # 계속 상승이면 진입 전환 1회만

    def test_evaluate_shapes_and_verdict(self):
        rng = np.random.default_rng(1)
        idx = pd.bdate_range("2015-01-01", periods=2000)
        c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, 2000))), index=idx)
        r = evaluate(c, 0.001)
        self.assertEqual(set(r["periods"]), {"FULL", "SINCE2022"})
        self.assertIn(r["periods"]["FULL"]["verdict"]["label"], {"유효", "부분", "무효"})
        self.assertLessEqual(r["periods"]["FULL"]["SMA200"]["exposurePct"], 100.0)
        m = metrics(pd.Series([0.1, -0.1], index=pd.bdate_range("2020-01-01", periods=2)))
        self.assertLess(m["mddPct"], 0)


if __name__ == "__main__":
    unittest.main()
