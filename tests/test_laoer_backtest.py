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

    def test_signal_ramp_buys_one_fifth_per_week(self):
        from laoer_backtest.signal import compute
        closes = 100 * np.exp(np.cumsum(np.tile([0.03, -0.03], 15)))  # 일변동 3% → 연 약 48%
        pos = dict(shares=0, cash_usd=10000, target_vol=0.35, lookback=20, band=0.10,
                   ramp_start="2026-01-05", ramp_weeks=5, ramp_start_weight=0.0, ramp_min_gap=0.02)
        full = compute(pos, closes)
        s = compute(pos, closes, asof=pd.Timestamp("2026-01-06"))
        self.assertAlmostEqual(s["eff"], full["w"] / 5)
        self.assertEqual(s["qty"], int(full["w"] / 5 * 10000 // closes[-1]))
        after = compute(pos, closes, asof=pd.Timestamp("2026-03-01"))  # 기간 종료 → 평소 규칙
        self.assertIsNone(after["ramp"])
        self.assertAlmostEqual(after["eff"], full["w"])

    def test_signal_trend_rule_halves_weight_in_downtrend(self):
        from laoer_backtest.signal import compute
        closes = 100 * np.exp(np.cumsum(np.tile([0.03, -0.03], 15)))
        pos = dict(shares=0, cash_usd=10000, target_vol=0.40, lookback=20, band=0.10,
                   trend_rule="cross50_200", trend_k=0.5)
        up = compute(pos, closes, index_closes=np.linspace(100, 200, 250))
        down = compute(pos, closes, index_closes=np.linspace(200, 100, 250))
        self.assertTrue(up["trend"]["up"])
        self.assertFalse(down["trend"]["up"])
        self.assertAlmostEqual(down["w"], up["w"] * 0.5)
        self.assertAlmostEqual(compute(pos, closes)["w"], up["w"])  # 지수 데이터 없으면 규칙 미적용

    def test_signal_ramp_does_not_sell_shares_bought_early(self):
        from laoer_backtest.signal import compute
        closes = 100 * np.exp(np.cumsum(np.tile([0.03, -0.03], 15)))  # 목표 약 83%
        pos = dict(shares=50, cash_usd=5000, target_vol=0.40, lookback=20, band=0.10,
                   ramp_start="2026-01-05", ramp_weeks=5, ramp_start_weight=0.0, ramp_min_gap=0.02)
        s = compute(pos, closes, asof=pd.Timestamp("2026-01-06"))  # 1주차 분할 목표 약 17%, 현재 약 50%
        self.assertGreater(s["cur"] - s["eff"], 0.10)
        self.assertEqual(s["qty"], 0)
        hi = compute(dict(pos, target_vol=0.10), closes, asof=pd.Timestamp("2026-01-06"))  # 최종 목표보다 10%p 넘게 많으면 매도
        self.assertLess(hi["qty"], 0)


if __name__ == "__main__":
    unittest.main()
