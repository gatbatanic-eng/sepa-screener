import unittest

import numpy as np
import pandas as pd

from laoer_backtest import leverage_study as L


class LeverageStudyTest(unittest.TestCase):
    def frame(self, n=60):
        idx = pd.bdate_range("2020-01-01", periods=n)
        rng = np.random.default_rng(1)
        q = pd.Series(rng.normal(0.0005, 0.01, n), index=idx)
        return pd.DataFrame({"q": q, "t": 3 * q, "ndx": 100 * (1 + q).cumprod(), "rate": 0.02}, index=idx)

    def test_const_leverage_is_linear_mix_of_qqq_and_tqqq(self):
        r = self.frame()
        self.assertTrue(np.allclose(L.const_returns(r, 1.0), r["q"]))
        self.assertTrue(np.allclose(L.const_returns(r, 3.0), r["t"]))
        self.assertTrue(np.allclose(L.const_returns(r, 2.0), 0.5 * r["q"] + 0.5 * r["t"]))

    def test_vol_target_uses_no_future_data(self):
        n = 400
        rng = np.random.default_rng(2)
        t = rng.normal(0.001, 0.02, n)
        ndx = 100 * np.cumprod(1 + t / 3)
        rate = np.full(n, 0.03)
        base = L.vol_target_returns(t, ndx, rate)
        t2, ndx2 = t.copy(), ndx.copy()
        t2[300:] = -0.05                       # 300일 이후를 바꿔도 300일 이전 수익은 같아야 한다
        ndx2[300:] = ndx[299] * np.cumprod(1 + t2[300:] / 3)
        alt = L.vol_target_returns(t2, ndx2, rate)
        self.assertTrue(np.allclose(base[:300], alt[:300]))
        self.assertFalse(np.allclose(base[300:], alt[300:]))

    def test_stats_and_rolling(self):
        idx = pd.bdate_range("2000-01-03", periods=2600)
        ret = pd.Series(0.0004, index=idx)
        s = L.stats(ret)
        self.assertAlmostEqual(s["mdd"], 0.0)
        self.assertGreater(s["cagr"], 0.05)
        roll = L.rolling_cagr(ret, 5).dropna()
        self.assertTrue((roll > 0).all())


if __name__ == "__main__":
    unittest.main()
