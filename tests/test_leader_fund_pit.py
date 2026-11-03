import unittest

import numpy as np
import pandas as pd

from leader_backtest import fund_pit as F


def rows(fs_div="CFS", rev=100, op=10, net=5, add=None, liab=50, eq=100, rcept="20240515000001"):
    mk = lambda nm, sj, amt, add_=None: {"fs_div": fs_div, "account_nm": nm, "sj_div": sj, "thstrm_amount": str(amt), "thstrm_add_amount": None if add_ is None else str(add_), "rcept_no": rcept}
    return [mk("매출액", "IS", rev, add), mk("영업이익", "IS", op, None if add is None else add // 10), mk("당기순이익", "IS", net, None if add is None else add // 20),
            mk("부채총계", "BS", liab), mk("자본총계", "BS", eq)]


class KrTest(unittest.TestCase):
    def test_cfs_preferred_and_ofs_fallback(self):
        both = rows("CFS", rev=200) + rows("OFS", rev=100)
        self.assertEqual(F.kr_rows_to_cum(both)["revenue"]["amount"], 200.0)
        self.assertEqual(F.kr_rows_to_cum(rows("OFS", rev=100))["basis"], "OFS")
        self.assertEqual(F.kr_rows_to_cum([]), {})

    def test_quarters_from_cumulative_and_q4_from_annual(self):
        rep = {(2023, 1): F.kr_rows_to_cum(rows(rev=100, op=10, net=5, rcept="20230515000001")),
               (2023, 2): F.kr_rows_to_cum(rows(rev=120, add=220, rcept="20230814000001")),
               (2023, 3): F.kr_rows_to_cum(rows(rev=130, add=350, rcept="20231114000001")),
               (2023, 4): F.kr_rows_to_cum(rows(rev=500, rcept="20240320000001"))}
        qs = {r["q"]: r for r in F.kr_quarters(rep)}
        self.assertEqual([qs[i]["revenue"] for i in (1, 2, 3, 4)], [100.0, 120.0, 130.0, 150.0])     # Q2=220-100, Q3=350-220, Q4=500-350
        self.assertEqual(qs[4]["filed"], "2024-03-20")                                               # Q4 는 사업보고서 접수일
        self.assertEqual(str(qs[2]["qend"]), "2023-06-30")

    def test_basis_switch_blocks_difference(self):
        rep = {(2023, 1): F.kr_rows_to_cum(rows("OFS", rev=100)), (2023, 2): F.kr_rows_to_cum(rows("CFS", rev=120, add=220))}
        self.assertIsNone({r["q"]: r for r in F.kr_quarters(rep)}[2]["revenue"])


class FeatureTest(unittest.TestCase):
    def quarters(self):
        d = []
        for y, base in ((2022, 100), (2023, 150)):
            for q in (1, 2, 3, 4):
                d.append({"code": "A", "qend": f"{y}-{q * 3:02d}-28", "filed": f"{y}-{q * 3 + 1:02d}-15" if q < 4 else f"{y + 1}-03-20", "revenue": base, "op": base * 0.1 if y == 2023 else -5.0,
                          "net": 1.0, "liabilities": 50.0, "equity": 100.0})
        return pd.DataFrame(d)

    def test_yoy_turnaround_and_margin(self):
        f = F.quarter_features(self.quarters())
        r = f[f["qend"] == pd.Timestamp("2023-03-28")].iloc[0]
        self.assertAlmostEqual(r["revYoY"], 50.0)
        self.assertEqual(r["turn"], 1.0)                      # 전년 영업적자 -> 흑자
        self.assertTrue(np.isnan(r["opYoY"]))                 # 기준이 0 이하면 증가율 결측
        self.assertAlmostEqual(r["opMargin"], 10.0)
        self.assertAlmostEqual(r["de"], 50.0)

    def test_attach_uses_only_filings_before_the_day(self):
        f = F.quarter_features(self.quarters())
        dates = pd.to_datetime(["2023-04-14", "2023-04-15", "2023-04-16", "2024-02-01"])
        panel = pd.DataFrame({"date": dates, "code": "A"})
        m = F.attach(panel, f).sort_values("date").reset_index(drop=True)
        self.assertTrue(np.isnan(m.loc[0, "revYoY"]) or m.loc[0, "daysSince"] > 0)       # 접수일 전날: 아직 Q1'23 값은 없다
        self.assertNotEqual(m.loc[1, "daysSince"], 0)                                     # 접수 당일은 쓰지 않는다(이전 공시 또는 결측)
        self.assertEqual(m.loc[2, "daysSince"], 1.0)                                      # 접수 다음 날부터 사용
        self.assertAlmostEqual(m.loc[2, "revYoY"], 50.0)
        self.assertTrue(np.isnan(m.loc[3, "revYoY"]) or m.loc[3, "daysSince"] <= 200)    # 200일 넘은 낡은 값은 결측 처리


class UsTest(unittest.TestCase):
    def test_earliest_first_beats_tag_priority_and_late_balance_sheet_is_dropped(self):
        import publish_us_fundamentals as us
        rows = [{"rank": 0, "filed": "2012-05-01", "val": 111.0, "accn": "b"}, {"rank": 3, "filed": "2010-05-01", "val": 100.0, "accn": "a"}]
        self.assertEqual(F.earliest_first(rows)["val"], 100.0)             # 태그 우선순위가 높아도 나중에 공시된 값은 쓰지 않는다
        self.assertIsNone(F.earliest_first([]))
        rec = {"periodEnd": "2010-03-31", "filedAt": "2011-01-01", "revenue": 1.0, "operatingProfit": 0.1, "netIncome": 0.05, "liabilities": 5.0, "equity": 9.0,
               "provenance": {"revenue": {"filedAt": "2010-05-01"}, "netIncome": {"filedAt": "2010-05-01"}, "liabilities": {"filedAt": "2010-05-01"}, "equity": {"filedAt": "2011-01-01"}}}
        r = F.us_record(rec, "X")
        self.assertEqual(r["filed"], "2010-05-01")                          # 손익 항목 공시일 기준
        self.assertEqual(r["liabilities"], 5.0)
        self.assertIsNone(r["equity"])                                       # 늦게 공시된 자본은 그 시점에 알 수 없다


if __name__ == "__main__":
    unittest.main()
