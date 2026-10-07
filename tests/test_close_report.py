import unittest

from nhplug import close_report as cr

TODAY = "20261007"


def bars(close, prev, turn, avg_turn, news=0, hi=None):
    b = [{"bsop_date": TODAY, "stck_prpr": close, "tr_pbmn": turn, "news_cnt": news, "stck_hgpr": close}]
    b += [{"bsop_date": f"202609{k:02d}", "stck_prpr": prev, "tr_pbmn": avg_turn, "stck_hgpr": hi or prev} for k in range(1, 21)]
    return b


def day(code, sector, chg, surge=1.0, news=0, frgn=0, inst=0):
    prev = 100.0
    d = cr.stock_day(bars(prev * (1 + chg / 100), prev, 1e9 * surge, 1e9, news), {"bstp_kor_isnm": "코스피 " + sector, "iem_nm": "*" + code}, TODAY)
    d["flow"] = {"frgn": frgn, "inst": inst, "indiv": -frgn - inst, "program": 0}
    return d


class CloseReportTest(unittest.TestCase):
    def test_stock_day_needs_todays_bar(self):
        self.assertIsNone(cr.stock_day(bars(100, 100, 1, 1)[1:], {}, TODAY))
        d = cr.stock_day(bars(110, 100, 3e9, 1e9, news=7), {"bstp_kor_isnm": "코스닥 반도체", "iem_nm": "#ABC"}, TODAY)
        self.assertAlmostEqual(d["chg"], 10.0)
        self.assertAlmostEqual(d["surge"], 3.0)
        self.assertEqual((d["sector"], d["name"], d["news"], d["newHigh20"]), ("반도체", "ABC", 7, True))

    def test_flow_today_picks_todays_row(self):
        inv = [{"bsop_date1": "20261006", "invest": 9}, {"bsop_date1": TODAY, "invest": 5, "gigwan": -2, "person": -3, "program": 1}]
        self.assertEqual(cr.flow_today(inv, TODAY), {"frgn": 5.0, "inst": -2.0, "indiv": -3.0, "program": 1.0})
        self.assertIsNone(cr.flow_today(inv[:1], TODAY))

    def test_sectors_flows_issues_and_themes(self):
        data = {}
        for i in range(6):
            data[f"S{i}"] = day(f"S{i}", "반도체", 6 + i, surge=3, news=10, frgn=100000, inst=50000)
            data[f"B{i}"] = day(f"B{i}", "은행", -1, frgn=-100000)
            data[f"X{i}"] = day(f"X{i}", "소형", 1)
        market = {c: "KOSPI" for c in data}
        rep = cr.build(TODAY, data, market, {"추천": ["S0", "ZZZ"]})
        self.assertEqual(rep["strongSectors"][0]["sector"], "반도체")
        self.assertEqual(rep["flowInSectors"][0]["sector"], "반도체")
        self.assertEqual(rep["flowOutSectors"][0]["sector"], "은행")
        self.assertEqual(len(rep["issues"]), 6)                    # 반도체 6종목만 ±5%·2배 조건을 넘는다
        self.assertEqual(rep["themes"][0]["sector"], "반도체")
        self.assertEqual([w["code"] for w in rep["watch"]], ["S0"])
        self.assertEqual(rep["market"]["KOSPI"]["up"], 12)
        md = cr.to_markdown(rep)
        self.assertIn("강한 섹터", md)
        self.assertIn("테마: 반도체 강세", md)

    def test_small_sectors_are_left_out(self):
        data = {f"A{i}": day(f"A{i}", "작은업종", 3) for i in range(cr.MIN_SECTOR - 1)}
        self.assertEqual(cr.build(TODAY, data, {}, {})["strongSectors"], [])

    def test_stop_alerts_within_three_percent(self):
        data = {"111111": day("111111", "x", 0)}
        data["111111"]["close"] = 102.0
        self.assertEqual(len(cr.stop_alerts(data, [{"code": "111111", "stopPrice": 100.0}])), 1)
        self.assertEqual(cr.stop_alerts(data, [{"code": "111111", "stopPrice": 90.0}]), [])


if __name__ == "__main__":
    unittest.main()
