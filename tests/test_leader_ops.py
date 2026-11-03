import unittest

import numpy as np
import pandas as pd

from leader_backtest.ops import P, metrics, simulate


def frame(cols):
    idx = pd.bdate_range("2024-01-01", periods=len(next(iter(cols.values()))))
    return pd.DataFrame(cols, index=idx)


class SimTest(unittest.TestCase):
    def setUp(self):
        self.reg = None

    def run_sim(self, close, sig_days, exit_rule="E1", cost=0.0, regime=True, sma=None, rs=None):
        c = frame(close)
        s = pd.DataFrame(False, index=c.index, columns=c.columns)
        for d, col in sig_days:
            s.iloc[d, s.columns.get_loc(col)] = True
        sm = frame(sma) if sma else pd.DataFrame(0.0, index=c.index, columns=c.columns)
        r = pd.DataFrame(50.0, index=c.index, columns=c.columns) if rs is None else frame(rs)
        reg = pd.Series(regime, index=c.index) if isinstance(regime, bool) else regime
        return simulate(c, sm, s, r, reg, exit_rule, cost)

    def test_enter_next_close_and_hard_stop(self):
        out = self.run_sim({"A": [100, 100, 100, 95, 91, 91, 91.0]}, [(1, "A")])      # 신호 t=1 -> 진입 t=2(100), t=4 91 <= 92 손절
        t = out["trades"]
        self.assertEqual(len(t), 1)
        self.assertAlmostEqual(t[0]["ret"], -0.09)
        self.assertAlmostEqual(out["equity"].iloc[-1], 1 - 0.125 * 0.09)

    def test_e1_exits_below_sma50_and_e2_trails_from_peak(self):
        close = {"A": [100, 100, 100, 120, 130, 125, 120, 118.0]}
        e1 = self.run_sim(close, [(1, "A")], "E1", sma={"A": [0, 0, 0, 0, 0, 0, 0, 119.0]})
        self.assertAlmostEqual(e1["trades"][0]["ret"], 118 / 100 - 1)
        e2 = self.run_sim(close, [(1, "A")], "E2")                                      # 최고 130, 15% 트레일 = 110.5 -> 끝까지 보유
        self.assertEqual(e2["trades"], [])
        self.assertAlmostEqual(e2["equity"].iloc[-1], 1 + 0.125 * 0.18)

    def test_regime_blocks_entry_and_slots_cap(self):
        off = self.run_sim({"A": [100.0] * 6}, [(1, "A")], regime=False)
        self.assertEqual(off["equity"].iloc[-1], 1.0)
        names = {f"S{i}": [100.0] * 6 for i in range(10)}
        out = self.run_sim(names, [(1, k) for k in names], "E2")
        self.assertAlmostEqual(out["equity"].iloc[-1], 1.0)                              # 가격이 안 변해 계좌 불변
        self.assertEqual(len(out["trades"]), 0)

    def test_picks_highest_rs_when_slots_short(self):
        p = dict(P, max_pos=1)
        close = {"A": [100, 100, 100, 110, 110.0], "B": [100, 100, 100, 90, 90.0]}
        rs = {"A": [90.0] * 5, "B": [95.0] * 5}
        c = frame(close)
        s = pd.DataFrame(False, index=c.index, columns=c.columns)
        s.iloc[1] = True
        out = simulate(c, pd.DataFrame(0.0, index=c.index, columns=c.columns), s, frame(rs), pd.Series(True, index=c.index), "E2", 0.0, p)
        self.assertLess(out["equity"].iloc[-1], 1.0)                                     # RS 95인 B를 골라 -10%

    def test_cost_and_metrics(self):
        out = self.run_sim({"A": [100, 100, 100, 110, 110.0]}, [(1, "A")], "E2", cost=0.01)
        m = metrics(out["equity"], out["trades"], pd.Series(100.0 + np.arange(5), index=out["equity"].index))
        self.assertEqual(m["trades"], 0)
        self.assertGreater(m["benchTotalPct"], 0)
        self.assertLess(out["equity"].iloc[-1], 1 + 0.125 * 0.10)                        # 진입 비용 반영

    def test_fixed_hold_with_stop_exit(self):
        close = {"A": [100, 100, 100, 110, 120, 130, 140, 150.0]}
        out = self.run_sim(close, [(1, "A")], "H3_S8")                     # 진입 t=2(100), 3일 뒤 t=5(130) 종가 청산
        self.assertEqual(len(out["trades"]), 1)
        self.assertAlmostEqual(out["trades"][0]["ret"], 0.30)
        self.assertEqual(out["trades"][0]["days"], 3)
        stopped = self.run_sim({"A": [100, 100, 100, 95, 91, 130, 140, 150.0]}, [(1, "A")], "H3_S8")
        self.assertAlmostEqual(stopped["trades"][0]["ret"], -0.09)         # t=4에 -8% 이하 손절이 보유 만료보다 먼저
        nostop = self.run_sim({"A": [100, 100, 100, 95, 91, 130, 140, 150.0]}, [(1, "A")], "H3")
        self.assertAlmostEqual(nostop["trades"][0]["ret"], 0.30)           # 손절 없는 고정 보유


if __name__ == "__main__":
    unittest.main()
