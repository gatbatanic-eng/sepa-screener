import os
import unittest
from unittest import mock

import numpy as np
import pandas as pd

from leader_backtest import sp500_members as M
from leader_backtest import search as S
from tests.test_leader_search import tiny_data


def html(n=120):
    rows = "".join(f"<tr><td>T{i}</td><td>Co{i}</td><td>{'' if i % 7 == 0 else f'20{10 + i % 15}-0{1 + i % 9}-15'}</td></tr>" for i in range(n))
    rows += "<tr><td>BRK.B</td><td>Berkshire</td><td>2010-02-16</td></tr>"
    return f"<table><thead><tr><th>Symbol</th><th>Security</th><th>Date added</th></tr></thead><tbody>{rows}</tbody></table>"


class MembersTest(unittest.TestCase):
    def test_parse_normalizes_symbols_and_dates(self):
        df = M.parse_members(html())
        self.assertIn("BRK-B", set(df["symbol"]))
        self.assertTrue(df["date_added"].notna().sum() > 50)
        self.assertTrue(df.loc[df.symbol == "T7", "date_added"].isna().all())          # 편입일 없는 종목은 결측으로 남는다
        with self.assertRaises(RuntimeError):
            M.parse_members("<table><tr><th>A</th></tr><tr><td>1</td></tr></table>")

    def test_eligibility_uses_added_date_and_unknown_means_always(self):
        dates = np.array(pd.to_datetime(["2020-01-01", "2021-01-01", "2022-01-01"]), dtype="datetime64[ns]")
        didx = np.array([0, 1, 2, 0, 1, 2]); cidx = np.array([0, 0, 0, 1, 1, 1])
        members = pd.DataFrame({"symbol": ["A", "B"], "date_added": pd.to_datetime(["2021-06-01", None])})
        e = M.eligibility(dates, didx, cidx, np.array(["A", "B"]), members)
        self.assertEqual(list(e), [False, False, True, True, True, True])
        e2 = M.eligibility(dates, didx, cidx, np.array(["A", "ZZZ"]), members)          # 표에 없는 종목은 전 구간 제외
        self.assertEqual(list(e2), [False, False, True, False, False, False])

    def test_apply_restricts_signals_and_rebuilds_baseline(self):
        d = tiny_data(0.0, n_stocks=10)
        cfg = {"rs": 0, "trend": 0, "atrMin": 0, "atrMax": 9, "emaLo": -9, "emaHi": 9, "highMin": -9, "rsiLo": 0, "rsiHi": 101, "vol": 0, "breakout": 0,
               "ret20Min": -9, "riskMax": 999, "regime": 0, "exit": "H20"}
        before = int(d.mask(cfg).sum())
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False) as f:
            pd.DataFrame({"symbol": list(d.codes), "date_added": ["2022-06-01"] * len(d.codes)}).to_csv(f.name, index=False)
            path = f.name
        with mock.patch.dict(os.environ, {"MEMBERS_CSV": path}):
            M.apply_to(d, "us")
            self.assertLess(int(d.mask(cfg).sum()), before)
            self.assertFalse(bool(d.elig[d.dates[d.didx] < np.datetime64("2022-06-01")].any()))
            M.apply_to(d, "kr")                                                          # 한국에는 적용하지 않는다(이미 적용된 값 유지)
        self.assertIsNotNone(d.elig)


if __name__ == "__main__":
    unittest.main()
