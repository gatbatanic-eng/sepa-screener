import unittest

import numpy as np
import pandas as pd

from leader_backtest import search as S
from leader_backtest import search_fund as SF
from tests.test_leader_backtest import synth


def fund_data(edge=0.0, n_stocks=40):
    rows = []
    for i in range(n_stocks):
        df = synth(n=1500, drift=0.0003, vol=0.014, seed=500 + i, start="2016-01-01")
        f = S.stock_frame(df, 0.002)
        f["code"] = f"S{i}"
        rows.append(f)
    p = pd.concat(rows)
    p.index.name = "date"
    p = p.reset_index()
    p["rs"] = p.groupby("date")["ret252"].rank(pct=True) * 100
    p = p[p["date"] >= "2017-01-03"].copy()
    rng = np.random.default_rng(3)
    good = {f"S{i}": rng.random() > 0.5 for i in range(n_stocks)}          # 종목 절반을 '고성장'으로 라벨
    p["revYoY"] = np.where(p["code"].map(good), 30.0, -5.0)
    for k in ("opYoY", "opMargin", "dMargin", "accel", "turn", "netPos", "de"):
        p[k] = np.where(p["code"].map(good), 12.0, 1.0)
    p["daysSince"] = 30.0
    if edge:
        for k in S.EXITS:
            sel = p["code"].map(good) & (p["revYoY"] >= 20)
            p.loc[sel, "x_" + k] = p.loc[sel, "x_" + k] + np.float32(edge)
    return SF.FundData(p, {}, extra=(*SF.FUND_COLS, "daysSince"))


class FundSearchTest(unittest.TestCase):
    def test_sampling_requires_a_fundamental_condition_and_is_reproducible(self):
        a, b = SF.sample_fund_configs(300), SF.sample_fund_configs(300)
        self.assertEqual(a, b)
        self.assertTrue(all(any(c[k] != SF.OFF[k] for k in SF.FUND_KEYS) for c in a))

    def test_planted_fundamental_edge_is_found_and_missing_fails_the_filter(self):
        edge, noise = fund_data(0.05), fund_data(0.0)
        cfg = {"rs": 0, "trend": 0, "regime": 0, "vol": 0, "breakout": 0, "highMin": -9, "atrMin": 0, "exit": "H20", "revYoY": 20, "opYoY": -9, "opMargin": -9,
               "dMargin": -9, "accel": 0, "turn": 0, "netPos": 0, "de": 999, "recent": 999}
        self.assertGreater(S.combined(edge.score(cfg)), 3.0)
        self.assertLess(abs(S.combined(noise.score(cfg))), 3.0)
        nan_data = fund_data(0.0)
        nan_data.fmasks[("revYoY", 20)] = nan_data.extra["revYoY"] * np.nan >= 20            # 결측만 있으면 신호 0 -> 유효하지 않음
        self.assertEqual(S.combined(nan_data.score(cfg)), -99.0)

    def test_turnaround_counts_for_op_growth(self):
        d = fund_data(0.0)
        base = {"rs": 0, "trend": 0, "regime": 0, "vol": 0, "breakout": 0, "highMin": -9, "atrMin": 0, "exit": "H20", "revYoY": -9, "opYoY": 50, "opMargin": -9,
                "dMargin": -9, "accel": 0, "turn": 0, "netPos": 0, "de": 999, "recent": 999}
        m_no = d.mask(base)
        d.extra["turn"][:] = 1.0
        d.fmasks[("opYoY", 50)] = (d.extra["opYoY"] >= 50) | (d.extra["turn"] == 1)
        self.assertGreater(int(d.mask(base).sum()), int(m_no.sum()))


class KeyTest(unittest.TestCase):
    def test_join_keys_match_price_and_fund_sides(self):
        self.assertEqual(list(SF.join_key(pd.Series(["009450.KS", "035720.KQ", "9450"]), "kr")), ["009450", "035720", "009450"])
        self.assertEqual(list(SF.join_key(pd.Series(["BRK-B", "brk.b", "AAPL"]), "us")), ["BRK-B", "BRK-B", "AAPL"])

    def test_hook_attaches_features_with_suffixed_codes_and_fails_loudly_when_nothing_matches(self):
        from leader_backtest import fund_pit as FP
        from unittest import mock
        q = pd.DataFrame({"code": ["009450"] * 8, "qend": pd.date_range("2022-03-31", periods=8, freq="QE"), "filed": pd.date_range("2022-05-15", periods=8, freq="QE") + pd.Timedelta(days=0),
                          "revenue": [100, 110, 120, 130, 150, 160, 170, 180.0], "op": [10.0] * 8, "net": [5.0] * 8, "liabilities": [50.0] * 8, "equity": [100.0] * 8})
        panel = pd.DataFrame({"date": pd.bdate_range("2024-01-02", periods=60), "code": "009450.KS", "close": 1.0})
        with mock.patch.object(SF, "load_quarters", return_value=q):
            out = SF.fund_hook("kr")(panel)
        self.assertEqual(set(out["code"]), {"009450.KS"})
        self.assertGreater(float(out["revYoY"].notna().mean()), 0.5)
        other = panel.assign(code="999999.KS")
        with mock.patch.object(SF, "load_quarters", return_value=q):
            with self.assertRaises(RuntimeError):
                SF.fund_hook("kr")(other)


if __name__ == "__main__":
    unittest.main()
