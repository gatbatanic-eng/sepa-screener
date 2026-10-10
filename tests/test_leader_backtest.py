import unittest

import numpy as np
import pandas as pd

from leader_backtest import main as lb
from leader_backtest.rules import features, first_of_cluster, forward_returns, select


def synth(n=700, drift=0.0015, vol=0.012, seed=1, start="2023-06-01"):
    rng = np.random.default_rng(seed)
    close = 50 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    idx = pd.bdate_range(start, periods=n)
    return pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close, "volume": rng.integers(1_000, 5_000, n).astype(float)}, index=idx)


class ForwardTest(unittest.TestCase):
    def test_hold_and_stop_use_next_close_entry(self):
        c = pd.Series([100, 100, 110, 120, 90, 130, 140.0])
        hold = forward_returns(c, 3, 0.0)                              # t=0: 진입 c[1]=100, 청산 c[4]=90
        self.assertAlmostEqual(hold.iloc[0], -0.10)
        self.assertAlmostEqual(hold.iloc[1], 130 / 110 - 1)               # t=1: 진입 c[2]=110, 청산 c[5]=130
        stop = forward_returns(c, 3, 0.0, 0.08)                         # 진입 100, c[4]=90이 -8% 이하 → 90에서 청산(여기서는 어차피 같은 값)
        self.assertAlmostEqual(stop.iloc[0], -0.10)
        c2 = pd.Series([100, 100, 90, 120, 130, 140, 150.0])            # 중간에 -8% 이하(90) 뒤 반등: HOLD는 +20%대, STOP8은 -10%
        self.assertGreater(forward_returns(c2, 4, 0.0).iloc[0], 0.2)
        self.assertAlmostEqual(forward_returns(c2, 4, 0.0, 0.08).iloc[0], -0.10)
        self.assertTrue(np.isnan(forward_returns(c2, 4, 0.0).iloc[-1]))   # 마지막 h+1일은 값이 없다
        self.assertAlmostEqual(forward_returns(c, 3, 0.005).iloc[0], -0.105)  # 비용 차감

    def test_no_future_leak_in_features(self):
        df = synth()
        full = features(df)
        cut = features(df.iloc[:500])
        pd.testing.assert_frame_equal(full.iloc[:500].drop(columns=[]), cut, check_dtype=False)   # 앞부분 값은 뒤 데이터가 있어도 같다


class SelectTest(unittest.TestCase):
    def panel(self):
        data = {f"S{i}": synth(seed=i, drift=0.002 if i % 2 else -0.0005) for i in range(12)}
        idx = pd.Series(np.linspace(100, 150, 700), index=data["S0"].index)
        return lb.build_panel(data, "us", idx)

    def test_groups_are_nested_and_cooldown_applies(self):
        panel = self.panel()
        m = select(panel)
        self.assertEqual(set(m), {"ALL", "TREND", "V1", "LEADER", "PULLBACK", "BREAKOUT"})
        base = panel["ok"] & panel["trend"] & (panel["rs"] >= 70)
        self.assertFalse((m["TREND"] & ~base).any())
        self.assertFalse((m["V1"] & ~base).any())
        self.assertFalse((m["LEADER"] & (panel["rs"] < 90)).any())
        # 같은 종목의 신호는 10거래일 안에 다시 나오지 않는다
        for code, g in panel[m["TREND"]].groupby("code"):
            d = pd.DatetimeIndex(sorted(g["date"]))
            pos = {x: i for i, x in enumerate(sorted(panel[panel["code"] == code]["date"]))}
            gaps = np.diff([pos[x] for x in d])
            self.assertTrue((gaps > 10).all() if len(gaps) else True)

    def test_evaluate_runs_and_reports_all_groups(self):
        panel = self.panel()
        res = lb.evaluate(panel, "us", 12)
        r = res["results"]["HOLD20"]["all"]
        self.assertEqual(set(r), set(lb.GROUPS))
        self.assertIsNone(r["ALL"]["vsAllPp"])
        self.assertEqual(set(res["results"]), {"HOLD20", "STOP820", "HOLD40", "STOP840"})
        md = lb.to_markdown({"ranAt": "x", "markets": {"us": res}})
        self.assertIn("### HOLD20", md)

    def test_bootstrap_needs_enough_observations(self):
        lo, hi = lb.block_bootstrap_ci(np.arange(5.0), 20)
        self.assertTrue(np.isnan(lo))
        lo, hi = lb.block_bootstrap_ci(np.random.default_rng(0).normal(0, 1, 400), 20)
        self.assertLess(lo, hi)


if __name__ == "__main__":
    unittest.main()


class WinnersTest(unittest.TestCase):
    def panel(self):
        from leader_backtest import winners
        data = {f"S{i}": synth(seed=i, drift=0.003 if i % 2 else -0.0003, vol=0.02) for i in range(16)}
        return winners, winners.build_panel(data, "us")

    def test_labels_use_next_close_entry_and_adverse_excursion(self):
        from leader_backtest import winners
        c = pd.Series([100, 100, 90, 130, 140, 150.0])
        lab = winners.labels(c, 0.0, h=3)
        self.assertAlmostEqual(lab["ret"].iloc[0], 140 / 100 - 1)                     # 진입 c[1]=100, 청산 c[4]=140
        self.assertAlmostEqual(lab["mdd"].iloc[0], -0.10)                                                # 중간에 90까지 내려감
        self.assertTrue(np.isnan(lab["ret"].iloc[-1]))

    def test_panel_analysis_runs_and_validation_uses_discovery_thresholds(self):
        winners, panel = self.panel()
        res = winners.analyze(panel, "us")
        self.assertIn("recall", res)
        self.assertEqual(set(res["recall"]), {"ALL", "TREND", "V1", "LEADER", "PULLBACK", "BREAKOUT"})
        self.assertEqual(res["recall"]["ALL"]["first"]["recallPct"], 100.0)
        self.assertTrue(res["univariate"])
        first, second, mid = winners.split_halves(panel)
        rule = winners.search_rule(panel, first)
        for c in rule:                                                    # 임계값은 전반기 분포에서만 나온다
            x = panel.loc[first, c["feature"]].dropna()
            self.assertGreaterEqual(c["threshold"], x.min())
            self.assertLessEqual(c["threshold"], x.max())
        md = winners.to_markdown({"ranAt": "x", "markets": {"us": res}})
        self.assertIn("공통점", md)
