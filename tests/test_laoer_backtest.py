import unittest

import numpy as np
import pandas as pd

from laoer_backtest import data as D, engine, sim, strategies as S


def _synthetic(n=1500, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2015-01-01", periods=n)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0012, 0.04, n)))
    df = pd.DataFrame(dict(Open=c * (1 + rng.normal(0, .005, n)), Close=c), index=idx)
    df["High"] = df[["Open", "Close"]].max(axis=1) * (1 + abs(rng.normal(0, .015, n)))
    df["Low"] = df[["Open", "Close"]].min(axis=1) * (1 - abs(rng.normal(0, .015, n)))
    return df


class LaoerBacktestTest(unittest.TestCase):
    def test_v1_strategy_matches_original_engine(self):
        df = _synthetic()
        eq, cy = engine.run(df)
        st = S.LaoerV1()
        res, _ = sim.backtest(df, st)
        np.testing.assert_allclose(res["equity"].to_numpy(), eq.to_numpy(), rtol=1e-10)
        self.assertEqual(len(st.cycles()), len(cy))

    def test_lump_sum_tracks_price(self):
        df = _synthetic()
        res, _ = sim.backtest(df, S.LumpSum(), fee=0.0)
        np.testing.assert_allclose(res["equity"] / res["equity"].iloc[0],
                                   df["Close"] / df["Close"].iloc[0], rtol=1e-10)

    def test_dca_spends_all_cash(self):
        _, acct = sim.backtest(_synthetic(), S.DCA(5, 40))
        self.assertLess(acct.cash, 1e-6)

    def test_synth_tqqq_tracks_real(self):
        t = D.tracking(D.synth_tqqq("NDX", -0.0154))
        self.assertGreater(t["corr"], 0.99)
        self.assertLess(abs(t["cagr_synth"] - t["cagr_real"]), 0.005)


if __name__ == "__main__":
    unittest.main()
