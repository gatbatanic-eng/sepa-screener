import datetime as dt
import unittest

from tenbagger_screener import classify_row, update_history


def base(**changes):
    row = {
        "ticker": "TEST", "marketCap": 8_000_000_000,
        "revenueGrowthCurrentFY": 50, "epsGrowthCurrentFY": 100,
        "epsGrowthNextFY": 45, "epsGrowthState": "NORMAL",
        "epsRevision3M": 18, "revenueRevision3M": 8,
        "grossMarginCurrent": 65, "gmTrend": "GM_UP",
        "sharesGrowthYoY": 3, "aiBottleneck": True,
        "valuationBurden": 3, "forwardPE": 35, "priceToSales": 12,
        "revenueVisibility": 4, "customerConcentration": 3,
        "epsEstimateCurrent": 1.5,
    }
    row.update(changes)
    return row


class TenbaggerRulesTest(unittest.TestCase):
    def test_crdo_like_candidate(self):
        result = classify_row(base(ticker="CRDO"))
        self.assertEqual(result["status"], "텐배거 후보")
        self.assertNotIn("DILUTION", result["warnings"])

    def test_alab_like_compounder(self):
        result = classify_row(base(ticker="ALAB", marketCap=40_000_000_000, valuationBurden=5))
        self.assertEqual(result["status"], "고성장 Compounder")
        self.assertIn("HIGH_VALUATION", result["warnings"])

    def test_aaoi_like_dilution_overrides_growth(self):
        result = classify_row(base(ticker="AAOI", sharesGrowthYoY=25, grossMarginCurrent=28))
        self.assertEqual(result["status"], "희석 위험")
        self.assertIn("DILUTION", result["warnings"])

    def test_missing_revision_is_not_zero(self):
        result = classify_row(base(epsRevision3M=None, revenueRevision3M=None))
        self.assertIsNone(result["epsRevision3M"])
        self.assertIsNone(result["revenueRevision3M"])
        self.assertIn("DATA_INCOMPLETE", result["warnings"])
        self.assertEqual(result["status"], "초기 탐색")

    def test_history_keeps_first_observation_and_replaces_same_day(self):
        state = {"history": {}, "firstObservedAt": {}}
        first = base(ticker="CRDO", status="실적 가속", warnings=[])
        update_history(state, [first], dt.datetime(2026, 9, 21, tzinfo=dt.timezone.utc))
        revised = base(ticker="CRDO", status="텐배거 후보", warnings=[])
        update_history(state, [revised], dt.datetime(2026, 9, 21, 20, tzinfo=dt.timezone.utc))
        self.assertEqual(state["firstObservedAt"]["CRDO"], "2026-09-21")
        self.assertEqual(len(state["history"]["CRDO"]), 1)
        self.assertEqual(revised["observationCount"], 1)


if __name__ == "__main__":
    unittest.main()
