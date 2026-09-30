import datetime as dt
import gzip
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from funnel import rules, sectorheat, validation
from funnel.data_kr import build_quarters, classify_disclosure, parse_multi
from funnel.prices import price_metrics


def quarters(revenues, ops, equity=100.0, liabilities=50.0, start=(2024, 1)):
    out = []
    y, q = start
    for rev, op in zip(revenues, ops):
        out.append({"year": y, "quarter": q, "revenue": rev, "operatingProfit": op,
                    "netIncome": op, "equity": equity, "liabilities": liabilities})
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


def stock(**changes):
    base = {
        "market": "kr", "symbol": "T", "name": "Test", "marcap": 1e11,
        # 매출 YoY: 10% → 30% → 60% (2분기 연속 가속), 영업이익 적자 → 흑자
        "quarters": quarters([100, 100, 100, 100, 100, 110, 130, 160], [-5, -5, -5, -5, -3, 1, 5, 20]),
        "sharesNow": 101.0, "sharesYearAgo": 100.0, "ocfToNi": 1.2, "ocfTTMPositive": True,
        "dilutionEvents12m": 0, "splitEvents12m": 0,
        "prices": {"price": 95, "high52Ratio": 0.95, "ret6m": 0.4, "ret36to6m": -0.3, "aboveMa40": True},
    }
    base.update(changes)
    return base


class RulesTest(unittest.TestCase):
    def test_accelerating_turnaround_scores_high_and_times_on(self):
        res = rules.evaluate(stock(), 0.1)
        self.assertFalse(res["gates"]["excluded"])
        self.assertEqual(res["scores"]["S1"], 100)
        self.assertIn("2분기 연속 가속", res["reasons"]["S1"])
        self.assertIn("영업 흑자전환", res["reasons"]["S1"])
        self.assertEqual(res["T1"], "ON")
        self.assertGreater(res["composite"], 80)

    def test_g1_loss_making_runup(self):
        s = stock(quarters=quarters([100] * 8, [-5] * 8), prices={"ret6m": 2.0, "high52Ratio": 1, "aboveMa40": True})
        res = rules.evaluate(s, 0.5)
        self.assertEqual([h["code"] for h in res["gates"]["hits"]], ["G1"])

    def test_g2_dilution_by_shares_or_events(self):
        self.assertIn("G2", [h["code"] for h in rules.evaluate(stock(sharesNow=120.0), 0.5)["gates"]["hits"]])
        self.assertIn("G2", [h["code"] for h in rules.evaluate(stock(dilutionEvents12m=2), 0.5)["gates"]["hits"]])

    def test_g3_debt_and_capital_impairment(self):
        s = stock(quarters=quarters([100] * 8, [5] * 8, equity=10.0, liabilities=50.0))
        self.assertIn("G3", [h["code"] for h in rules.evaluate(s, 0.5)["gates"]["hits"]])
        s = stock(quarters=quarters([100] * 8, [-5] * 8, equity=-1.0))
        self.assertEqual(rules.evaluate(s, 0.5)["gates"]["hits"][0]["reason"], "자본잠식")

    def test_g3_softened_for_buybacks_and_financials(self):
        # 자본 마이너스 + 영업흑자(자사주 매입형) → 제외 대신 확인 필요
        res = rules.evaluate(stock(quarters=quarters([100] * 8, [5] * 8, equity=-1.0)), 0.5)
        self.assertFalse(res["gates"]["excluded"])
        self.assertEqual(res["gates"]["flags"][0]["code"], "G3?")
        # 금융업은 부채비율 기준 미적용
        bank = stock(quarters=quarters([100] * 8, [5] * 8, equity=10.0, liabilities=100.0),
                     industry=rules.classify_industry("us", "6022"))
        self.assertFalse(rules.evaluate(bank, 0.5)["gates"]["excluded"])

    def test_s1_floor_and_recovery(self):
        # 역성장 회복: -50% → -28% → +20% 는 가속이 아니라 회복
        s = stock(quarters=quarters([200, 200, 200, 200, 100, 144, 240], [5] * 7))
        res = rules.evaluate(s, 0.5)
        self.assertIn("역성장 회복(가속 아님)", res["reasons"]["S1"])
        self.assertFalse(res["inflection"])
        # 매출 +2% 는 순위 최소 조건 미달
        flat = stock(quarters=quarters([100, 100, 100, 100, 102, 102, 102, 102], [5] * 8))
        self.assertFalse(rules.evaluate(flat, 0.5)["meetsS1Floor"])
        self.assertTrue(rules.evaluate(stock(), 0.5)["meetsS1Floor"])

    def test_industry_classification(self):
        self.assertEqual(rules.classify_industry("us", "2911")["cyclical"], "정유")
        self.assertTrue(rules.classify_industry("us", "6798")["financial"])
        self.assertEqual(rules.classify_industry("kr", "50112")["cyclical"], "해운")
        self.assertTrue(rules.classify_industry("kr", "64191")["financial"])
        self.assertEqual(rules.classify_industry("us", "3674", "MU")["cyclical"], "메모리 반도체")
        self.assertIsNone(rules.classify_industry("kr", "20423")["cyclical"])

    def test_g4_is_flag_not_exclusion(self):
        res = rules.evaluate(stock(splitEvents12m=1), 0.5)
        self.assertFalse(res["gates"]["excluded"])
        self.assertEqual(res["gates"]["flags"][0]["code"], "G4")

    def test_missing_data_stays_none(self):
        res = rules.evaluate(stock(quarters=[], sharesNow=None, dilutionEvents12m=None, ocfToNi=None, ocfTTMPositive=None), None)
        self.assertIsNone(res["scores"]["S1"])
        self.assertIsNone(res["scores"]["S6"])
        self.assertIsNone(res["scores"]["S4"])
        self.assertIsNone(res["composite"])

    def test_percentile_neglect(self):
        pct = rules.percentile_ranks({"a": -0.5, "b": 0.0, "c": 3.0, "d": None})
        self.assertEqual(rules.score_s4(pct["a"]), 100)
        self.assertEqual(rules.score_s4(pct["c"]), 0)
        self.assertIsNone(pct["d"])

    def test_p1_tenbagger_arithmetic(self):
        # 목표시장 10조 × 점유율 20% × 이익률 25% × 20배 = 10조 / 시총 1조 = 10배
        res = rules.p1_multiple(10e12, 0.2, 0.25, 20, 1e12)
        self.assertEqual(res["multipleX"], 10)
        self.assertEqual(res["verdict"], "텐배거 산수 통과")
        self.assertEqual(rules.p1_multiple(1e12, 0.1, 0.1, 10, 1e12)["verdict"], "제외(3배 미만)")
        self.assertEqual(rules.p1_multiple(None, 0.1, 0.1, 10, 1e12)["verdict"], "입력 부족")
        self.assertEqual(rules.p1_multiple(10e12, 0.2, 0.25, 20, 2e12, other=2e12)["multipleX"], 6)


class SectorHeatTest(unittest.TestCase):
    def member(self, sym, tier, ret, op=1.0, sg=0.0, ev=None):
        return {"symbol": sym, "tier": tier, "ret6m": ret, "opTTM": op, "shareGrowth": sg, "dilutionEvents12m": ev}

    def test_froth_2000_style_is_red(self):
        members = [self.member("C1", "core", 0.3), self.member("C2", "core", 0.2),
                   self.member("F1", "fringe", 2.5, op=-1, sg=0.2), self.member("F2", "fringe", 1.5, op=-1, sg=0.1)]
        heat = sectorheat.compute(members)
        self.assertEqual(heat["flags"], {"A": True, "B": True, "C": True, "D": True})
        self.assertEqual(heat["level"], "r")
        self.assertEqual(heat["topMovers"][0]["symbol"], "F1")

    def test_quiet_market_is_green_and_missing_is_none(self):
        members = [self.member("C1", "core", 0.1), self.member("F1", "fringe", 0.05, sg=None)]
        heat = sectorheat.compute(members)
        self.assertEqual(heat["level"], "g")
        self.assertIsNone(sectorheat.compute([])["flags"]["A"])
        self.assertEqual(sectorheat.compute([])["level"], "")

    def test_korean_dilution_events_count(self):
        members = [self.member(f"K{i}", "fringe", 0.1, sg=None, ev=1 if i < 2 else 0) for i in range(5)]
        self.assertTrue(sectorheat.compute(members)["flags"]["D"])


class DartParsingTest(unittest.TestCase):
    def test_parse_multi_prefers_consolidated(self):
        rows = [
            {"stock_code": "000001", "fs_div": "OFS", "account_nm": "매출액", "thstrm_amount": "90", "thstrm_add_amount": ""},
            {"stock_code": "000001", "fs_div": "CFS", "account_nm": "매출액", "thstrm_amount": "1,000", "thstrm_add_amount": "3,000"},
            {"stock_code": "000001", "fs_div": "CFS", "account_nm": "영업 이익", "thstrm_amount": "(50)", "thstrm_add_amount": "-10"},
        ]
        out = parse_multi(rows)["000001"]
        self.assertEqual(out["basis"], "CFS")
        self.assertEqual(out["revenue"], (1000.0, 3000.0))
        self.assertEqual(out["operatingProfit"], (-50.0, -10.0))

    def test_q4_is_annual_minus_q3_cumulative(self):
        raw = {(2025, 3): {"basis": "CFS", "revenue": (100.0, 300.0)},
               (2025, 4): {"basis": "CFS", "revenue": (420.0, None)}}
        q = {(r["year"], r["quarter"]): r for r in build_quarters(raw)}
        self.assertEqual(q[(2025, 4)]["revenue"], 120.0)
        raw[(2025, 3)]["basis"] = "OFS"
        self.assertIsNone({(r["year"], r["quarter"]): r for r in build_quarters(raw)}[(2025, 4)]["revenue"])

    def test_marcap_units_normalized(self):
        import pandas as pd
        from funnel.data_kr import normalize_marcap
        # 네이버 보완값(억 원 단위 숫자) → 원 단위
        eok = normalize_marcap(pd.Series([3337100.0, 22282.0, 450.0]))
        self.assertEqual(eok.iloc[0], 3337100.0 * 1e8)
        won = pd.Series([3.3e14, 2.2e12])
        self.assertTrue(normalize_marcap(won).equals(won))

    def test_missing_q1_derived_from_q2_cumulative(self):
        raw = {(2025, 1): {"basis": "CFS"},
               (2025, 2): {"basis": "CFS", "revenue": (161.7, 280.1)}}
        q = {(r["year"], r["quarter"]): r for r in build_quarters(raw)}
        self.assertAlmostEqual(q[(2025, 1)]["revenue"], 118.4)

    def test_quarter_cache_skips_settled_periods(self):
        from funnel import data_kr

        class FakeDart:
            calls = []

            def request(self, endpoint, **params):
                self.calls.append((params["bsns_year"], params["reprt_code"]))
                return [{"corp_code": "C1", "stock_code": "000001", "fs_div": "CFS", "account_nm": "매출액",
                         "thstrm_amount": "100", "thstrm_add_amount": "100"}]

        with tempfile.TemporaryDirectory() as d:
            api = FakeDart()
            today = dt.date(2026, 9, 29)
            first = data_kr.fetch_quarters(api, {"000001": "C1"}, ["000001"], today, Path(d))
            n_first = len(api.calls)
            second = data_kr.fetch_quarters(api, {"000001": "C1"}, ["000001"], today, Path(d))
            self.assertEqual(first, second)
            self.assertEqual(len(api.calls) - n_first, 2)  # 최근 2개 분기만 재조회

    def test_disclosure_classification(self):
        self.assertEqual(classify_disclosure("주요사항보고서(유상증자결정)"), "dilution")
        self.assertEqual(classify_disclosure("주요사항보고서(전환사채권발행결정)"), "dilution")
        self.assertIsNone(classify_disclosure("[기재정정]주요사항보고서(유상증자결정)"))
        self.assertEqual(classify_disclosure("주요사항보고서(회사분할결정)"), "split")
        self.assertIsNone(classify_disclosure("주요사항보고서(회사분할합병결정)"))


class PriceAndValidationTest(unittest.TestCase):
    def test_price_metrics_weekly(self):
        idx = pd.date_range("2023-01-06", periods=160, freq="W-FRI")
        close = pd.Series([10.0] * 100 + [10.0 + i for i in range(60)], index=idx)
        m = price_metrics(close)
        self.assertEqual(m["price"], 69.0)
        self.assertAlmostEqual(m["high52Ratio"], 1.0)
        self.assertTrue(m["aboveMa40"])
        self.assertAlmostEqual(m["ret6m"], 69.0 / 43.0 - 1)

    def test_validation_freezes_checkpoints_once(self):
        with tempfile.TemporaryDirectory() as d:
            snaps = Path(d) / "snapshots"
            snaps.mkdir()
            snap = {"recordedAt": "2026-01-01T00:00:00+00:00", "benchmark": {"price": 100},
                    "rows": [{"symbol": "A", "price": 10, "rank": 1, "excluded": False},
                             {"symbol": "B", "price": 10, "rank": 2, "excluded": False},
                             {"symbol": "C", "price": 10, "rank": None, "excluded": True}]}
            with gzip.open(snaps / "2026-01-01.json.gz", "wt", encoding="utf-8") as f:
                json.dump(snap, f)
            path = Path(d) / "validation.json"
            state = validation.update(snaps, path, {"A": 20, "B": 10, "C": 5}, 110, dt.date(2026, 7, 5), top_k=1)
            entry = state["snapshots"]["2026-01-01"]
            self.assertEqual(entry["current"]["top"]["avgReturnPct"], 100.0)
            self.assertEqual(entry["current"]["passed"]["avgReturnPct"], 50.0)
            self.assertEqual(entry["current"]["benchmarkReturnPct"], 10.0)
            self.assertEqual(set(entry["frozen"]), {"1m", "3m", "6m"})
            later = validation.update(snaps, path, {"A": 40, "B": 10, "C": 5}, 120, dt.date(2026, 8, 1), top_k=1)
            self.assertEqual(later["snapshots"]["2026-01-01"]["frozen"]["6m"]["top"]["avgReturnPct"], 100.0)


if __name__ == "__main__":
    unittest.main()
