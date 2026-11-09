import unittest

import numpy as np
import pandas as pd

from leader_backtest import sepa_go as G


def _frame(n=420, seed=0, drift=0.0015):
    r = np.random.default_rng(seed)
    idx = pd.bdate_range("2021-01-04", periods=n)
    c = 100 * np.exp(np.cumsum(r.normal(drift, 0.02, n)))
    return pd.DataFrame({"open": c, "high": c * (1 + abs(r.normal(0, .01, n))), "low": c * (1 - abs(r.normal(0, .01, n))),
                         "close": c, "volume": r.lognormal(10, .5, n)}, index=idx)


class SepaGoFeatureTests(unittest.TestCase):
    def test_features_are_causal(self):
        df = _frame()
        bench = df["close"] * 0 + 100.0
        full = G.stock_features(df, bench)
        cut = G.stock_features(df.iloc[:350], bench.iloc[:350])
        cols = ["c1", "c2", "c3", "c4", "c5", "c6", "c7", "c17", "bo_day", "bo_recent", "simple_bo"]
        pd.testing.assert_frame_equal(full[cols].iloc[:350], cut[cols])

    def test_trend_conditions_match_screening_definition(self):
        df = _frame(seed=3)
        f = G.stock_features(df, df["close"] * 0 + 100.0)
        for i in range(300, len(df), 29):
            sub = df.iloc[:i + 1]
            c, h, l = sub["close"], sub["high"], sub["low"]
            s50, s150, s200 = c.rolling(50).mean().iloc[-1], c.rolling(150).mean().iloc[-1], c.rolling(200).mean()
            hi, lo, x = h.iloc[-252:].max(), l.iloc[-252:].min(), c.iloc[-1]
            ref = [x > s150 and x > s200.iloc[-1], s150 > s200.iloc[-1], s200.iloc[-1] > s200.iloc[-21],
                   s50 > s150 and s50 > s200.iloc[-1], x > s50, x >= lo * 1.3, x >= hi * .75]
            self.assertEqual([bool(f[f"c{k}"].iloc[i]) for k in range(1, 8)], ref, i)

    def test_old_and_new_configs_differ_only_in_atr_rule(self):
        self.assertFalse(G.CFG_OLD.setup.atr_normalize)
        self.assertEqual(G.CFG_OLD.setup.atr_contraction_max, 0.75)
        self.assertTrue(G.CFG_NEW.setup.atr_normalize)
        self.assertEqual(G.CFG_OLD.entry, G.CFG_NEW.entry)


if __name__ == "__main__":
    unittest.main()
