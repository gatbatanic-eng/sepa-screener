import unittest

import numpy as np
import pandas as pd

from leader_backtest import search as S
from tests.test_leader_backtest import synth


def tiny_data(edge: float, seed=1, n_stocks=40, n=1500):
    rows = []
    for i in range(n_stocks):
        df = synth(n=n, drift=0.0003, vol=0.014, seed=seed * 100 + i, start="2016-01-01")
        f = S.stock_frame(df, 0.002)
        f["code"] = f"S{i}"
        rows.append(f)
    p = pd.concat(rows)
    p.index.name = "date"
    p = p.reset_index()
    p["rs"] = p.groupby("date")["ret252"].rank(pct=True) * 100
    p = p[p["date"] >= "2017-01-03"]
    if edge:                                   # rs>=80 인 행의 H20 결과에 +edge 를 심어 탐색이 찾는지 본다
        for k in S.EXITS:
            p.loc[p["rs"] >= 80, "x_" + k] = p.loc[p["rs"] >= 80, "x_" + k] + np.float32(edge)
    return S.Data(p, {})


class ExitTest(unittest.TestCase):
    def test_trailing_and_sma_exit(self):
        c = pd.Series([100, 100, 100, 120, 130, 125, 100, 90.0] + [90.0] * 100)
        out = S.exit_outcomes(c, pd.Series(0.0, index=c.index), 0.0)
        # t=1 진입가 c[2]=100; 최고 130, 15% 트레일 = 110.5, c[6]=100 에서 청산(손절 -8%는 92 이하라 c[6]=100은 트레일이 먼저)
        self.assertAlmostEqual(out["TR15"][1], 100 / 100 - 1)
        s50 = pd.Series([np.inf] * 5 + [0.0] * 103, index=c.index)       # 앞 5일은 종가가 무조건 50일선 아래
        out2 = S.exit_outcomes(c, s50, 0.0)
        self.assertAlmostEqual(out2["SMA50X"][1], 120 / 100 - 1)           # 50일선 아래 마감이면 다음 종가(c[3]=120)에서 청산

    def test_end_rows_are_nan_for_long_exits(self):
        c = pd.Series(np.linspace(100, 120, 200))
        out = S.exit_outcomes(c, c.rolling(50).mean(), 0.0)
        self.assertTrue(np.isnan(out["TR15"][-1]))
        self.assertTrue(np.isnan(out["H10"][-1]))


class SearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.noise = tiny_data(0.0)
        cls.edge = tiny_data(0.02, seed=2)
        cls.cfg = {"rs": 80, "trend": 0, "atrMin": 0, "atrMax": 9, "emaLo": -9, "emaHi": 9, "highMin": -9, "rsiLo": 0, "rsiHi": 101, "vol": 0, "breakout": 0,
                   "ret20Min": -9, "riskMax": 999, "regime": 0, "exit": "H20"}

    def test_planted_edge_is_found_and_noise_is_not_extreme(self):
        e = S.combined(self.edge.score(self.cfg))
        n = S.combined(self.noise.score(self.cfg))
        self.assertGreater(e, 3.0)
        self.assertLess(abs(n), 3.0)

    def test_min_signal_rule_returns_invalid(self):
        c = dict(self.cfg, rs=95, atrMin=0.04, vol=2.0, breakout=1, rsiLo=60)
        self.assertEqual(S.combined(self.noise.score(c)), -99.0)

    def test_null_permutation_keeps_date_marginals(self):
        d = self.noise
        real, nulls = S.run_all(d, [self.cfg] * 4, 1, 1)
        outs, base = _G_nulls()
        self.assertEqual(len(real), 4)
        k = "H20"
        a = d._date_mean(d.out[k], d.valid[k])
        b = base[k]
        m = np.isfinite(a) & np.isfinite(b)
        self.assertLess(float(np.nanmax(np.abs(a[m] - b[m]))), 0.02)       # 날짜별 평균은 거의 보존(NaN 0 대체로 꼬리만 미세 차이)

    def test_sampling_is_reproducible_and_spans_exits(self):
        a, b = S.sample_configs(500), S.sample_configs(500)
        self.assertEqual(a, b)
        self.assertGreater(len({c["exit"] for c in a}), 15)


def _G_nulls():
    return S._G["nulls"][0]


if __name__ == "__main__":
    unittest.main()


class PortfolioStageTest(unittest.TestCase):
    def test_signal_matrix_and_nonstrict_mask(self):
        from leader_backtest.search_portfolio import signal_matrix
        d = tiny_data(0.0, n_stocks=10)
        cfg = {"rs": 80, "trend": 0, "atrMin": 0, "atrMax": 9, "emaLo": -9, "emaHi": 9, "highMin": -9, "rsiLo": 0, "rsiHi": 101, "vol": 0, "breakout": 0,
               "ret20Min": -9, "riskMax": 999, "regime": 0, "exit": "SMA50X"}
        strict, loose = d.mask(cfg), d.mask(cfg, strict=False)
        self.assertGreaterEqual(int(loose.sum()), int(strict.sum()))
        sig = signal_matrix(d, loose)
        self.assertEqual(int(sig.values.sum()), int(loose.sum()))
        self.assertEqual(list(sig.columns), list(d.mats["close"].columns))
        i = int(np.flatnonzero(loose)[0])
        self.assertTrue(sig.iloc[d.didx[i], d.cidx[i]])
