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

    def test_tax_lump_sum_only_on_liquidation(self):
        df = _synthetic()
        tax = dict(rate=0.22, deduction=0.0, fx=1.0)
        res, acct = sim.backtest(df, S.LumpSum(), fee=0.0, tax=tax)
        gain = res["equity"].iloc[-1] - 10000.0
        self.assertAlmostEqual(res.attrs["liq_tax"], max(gain, 0) * 0.22, places=6)
        self.assertEqual(acct.realized_krw, {})

    def test_tax_paid_in_may_next_year(self):
        idx = pd.bdate_range("2020-12-01", "2021-06-30")
        c = np.where(idx < pd.Timestamp("2020-12-15"), 100.0, 200.0)
        df = pd.DataFrame(dict(Open=c, High=c, Low=c, Close=c), index=idx)

        class Flip(sim.Strategy):
            def on_bar(self, acct, bar):
                if bar.i == 0:
                    acct.buy(acct.cash, bar.c)
                elif bar.date == pd.Timestamp("2020-12-16"):
                    acct.sell(acct.shares, bar.c)

        res, acct = sim.backtest(df, Flip(), fee=0.0, tax=dict(rate=0.22, deduction=1000.0, fx=1.0))
        self.assertAlmostEqual(acct.realized_krw[2020], 10000.0)
        self.assertAlmostEqual(res["equity"].loc["2021-04-30"], 20000.0)
        self.assertAlmostEqual(res["equity"].loc["2021-05-03"], 20000.0 - 9000.0 * 0.22)


if __name__ == "__main__":
    unittest.main()
