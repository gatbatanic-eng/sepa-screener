import unittest
from unittest.mock import patch

import recovery_mode as recovery

from recovery_mode import (
    ENTRY_MAX_KRW,
    ENTRY_MIN_KRW,
    entry_ok,
    gap_chase_metrics,
    market_metrics,
    rejection_reason,
    strength_score,
    suggested_amount,
    track_record_stats,
)


class RecoveryModeTest(unittest.TestCase):
    def snapshot(self, rows):
        with patch.object(recovery, "load_market", side_effect=[(rows, "2026-09-23", {}), ([], None, {})]), \
             patch.object(recovery, "load_valuation_us", return_value={}), \
             patch.object(recovery, "estimate_history", return_value=[]), \
             patch.object(recovery, "enrich_row", side_effect=lambda row, *args: dict(row)), \
             patch.object(recovery, "apply_sector_strength"):
            return recovery.build_snapshot()

    def candidate(self, code="A", **changes):
        row = self.base()
        row.update(code=code, name=code, marketBucket="US", strengthScore=90,
                   close=101, breakoutLevel=100, referenceStop=98, priceAsOf="2026-09-23")
        row.update(changes)
        return row

    def test_agilent_score_rejection_is_consistent_in_snapshot_and_html(self):
        snapshot = self.snapshot([self.candidate(name="Agilent", strengthScore=66.1)])
        row = snapshot["strongest"][0]
        self.assertFalse(row["entryPass"])
        self.assertIn("66.1 < 78.0", row["entryReason"])
        self.assertEqual(snapshot["entries"], [])
        html = recovery.render_html(snapshot)
        self.assertNotIn("진입 조건 통과", html)
        self.assertIn("오늘 조건 통과 종목 없음", html)
        self.assertNotIn('<article class="execution">', html)

    def test_all_entry_gates_have_rejection_reasons(self):
        mutations = [
            {key: False} for key in ("inUniverse", "marketOk", "trendOkAggressive", "nearHigh",
                "rsOk", "liquidityOk", "aggressiveGo", "breakout", "volumeOk", "clvOk", "rsiOk", "notExtended")
        ] + [
            {"status": "ERROR"}, {"strengthScore": 77.9}, {"strengthScore": None},
            {"initialRiskPct": None}, {"initialRiskPct": 6}, {"breadth": None},
            {"breadth": .2}, {"gapPct": None}, {"gapPct": 7},
            {"eventRisk": {"status": "BLOCK"}}, {"fundamental": {"risk": "BLOCK"}},
            {"estimate": {"samples": 10, "deltaPct": -6}},
        ]
        for changes in mutations:
            with self.subTest(changes=changes):
                row = self.candidate(**changes)
                self.assertFalse(entry_ok(row, row["strengthScore"]))
                self.assertNotEqual(rejection_reason(row), "진입 조건 통과")
        row = self.candidate(strengthScore=78)
        self.assertTrue(entry_ok(row, 78))
        self.assertEqual(rejection_reason(row), "진입 조건 통과")

    def test_candidate_limit_and_sector_rejections_never_claim_pass(self):
        rows = [self.candidate(str(i), sectorKey=str(i)) for i in range(5)]
        snapshot = self.snapshot(rows)
        self.assertEqual(len(snapshot["entries"]), 3)
        self.assertEqual([e["executionPriority"] for e in snapshot["entries"]], [1, 2, None])
        for r in snapshot["strongest"][3:]:
            self.assertFalse(r["entryPass"])
            self.assertIn("한도", r["entryReason"])
        snapshot = self.snapshot([self.candidate("A", sectorKey="Tech"), self.candidate("B", sectorKey="Tech")])
        self.assertFalse(snapshot["strongest"][1]["entryPass"])
        self.assertIn("동일 섹터", snapshot["strongest"][1]["entryReason"])

    def test_execution_prices_and_loss_use_upper_entry_price(self):
        snapshot = self.snapshot([self.candidate()])
        e = snapshot["entries"][0]
        p = e["executionPlan"]
        self.assertEqual((p["entryPriceMin"], p["entryPriceMax"]), (101, 103))
        self.assertEqual((p["referenceStop"], p["target1R"], p["target2R"]), (98, 108, 113))
        self.assertEqual(e["suggestedAmountKRW"], 3_000_000)
        self.assertEqual(p["estimatedMaxLossKRW"], 145632)
        self.assertAlmostEqual(p["plannedLossPct"], 5 / 103 * 100)
        self.assertTrue(e["entryPass"])
        self.assertEqual(recovery.compact_row(e)["executionPlan"], p)

    def test_price_bounds_round_inward_and_obey_stop_risk_limit(self):
        p = recovery.execution_plan(self.candidate(close=100.001, breakoutLevel=99, referenceStop=95))
        self.assertEqual(p["entryPriceMin"], 100.01)
        self.assertEqual(p["entryPriceMax"], 100.22)
        self.assertLessEqual(p["sizingRiskPct"], recovery.STRICT_RISK_PCT)

    def test_invalid_or_empty_price_ranges_cannot_be_execution_candidates(self):
        mutations = [{key: v} for key in ("close", "breakoutLevel", "referenceStop")
                     for v in (None, 0, -1, float("nan"), float("inf"))]
        mutations += [{"referenceStop": 101}, {"breakoutLevel": 101}, {"close": 104},
                      {"referenceStop": 90}]
        for changes in mutations:
            with self.subTest(changes=changes):
                snapshot = self.snapshot([self.candidate(**changes)])
                self.assertEqual(snapshot["entries"], [])
                self.assertFalse(snapshot["strongest"][0]["entryPass"])
                self.assertIn("실행 가격", snapshot["strongest"][0]["entryReason"])

    def test_cards_show_two_priorities_with_currencies_and_all_values(self):
        snapshot = self.snapshot([self.candidate("US1"), self.candidate("KR1", marketBucket="KR"),
                                  self.candidate("RESERVE")])
        html = recovery.render_html(snapshot)
        self.assertEqual(html.count('<article class="execution">'), 2)
        self.assertIn("1순위 · US1", html)
        self.assertIn("2순위 · KR1", html)
        self.assertNotIn("3순위", html)
        for text in ("101.00 USD ~ 103.00 USD", "101.00 KRW ~ 103.00 KRW", "98.00 USD",
                     "108.00 USD", "113.00 USD", "3,000,000원", "145,632원", "2026-09-23",
                     "환율 불변", "실제 손실은 더 클 수"):
            self.assertIn(text, html)
        self.assertEqual(recovery.render_html(self.snapshot([self.candidate()])).count(
            '<article class="execution">'), 1)

    def base(self):
        return {
            "status": "OK", "inUniverse": True, "marketOk": True,
            "trendOkAggressive": True, "nearHigh": True, "rsOk": True,
            "liquidityOk": True, "aggressiveGo": True, "breakout": True,
            "volumeOk": True, "clvOk": True, "rsiOk": True, "notExtended": True,
            "sma50Slope10Pct": 6.0, "highProximityPct": -1.0, "pivotDistancePct": 1.0,
            "volumeRatio": 2.5, "clv": 0.9, "rsi14": 64, "signalCount": 6,
            "initialRiskPct": 4.0, "regime": "GREEN", "breadth": 0.65,
            "sizeFactor": 1.0, "gapPct": 1.0, "changePct1d": 4.0,
        }

    def test_good_candidate_passes(self):
        row = self.base()
        score = strength_score(row)
        self.assertGreaterEqual(score, 78)
        self.assertTrue(entry_ok(row, score))

    def test_high_risk_rejected(self):
        row = self.base()
        row["initialRiskPct"] = 6.0
        self.assertFalse(entry_ok(row, strength_score(row)))

    def test_zero_risk_is_not_treated_as_missing_or_high(self):
        row = self.base()
        row["initialRiskPct"] = 0.0
        row["marketRisk"] = market_metrics(row)
        row["gapRisk"] = gap_chase_metrics(row)
        self.assertTrue(entry_ok(row, 90))
        self.assertNotIn("초기리스크", rejection_reason(row))

    def test_missing_market_or_gap_data_fails_closed(self):
        row = self.base()
        row.pop("breadth")
        row["marketRisk"] = market_metrics(row)
        row["gapRisk"] = gap_chase_metrics(row)
        self.assertFalse(row["marketRisk"]["available"])
        self.assertFalse(entry_ok(row, 90))

        row = self.base()
        row.pop("gapPct")
        row["marketRisk"] = market_metrics(row)
        row["gapRisk"] = gap_chase_metrics(row)
        self.assertFalse(row["gapRisk"]["available"])
        self.assertFalse(entry_ok(row, 90))

    def test_gap_chase_is_blocked(self):
        row = self.base()
        row.update(gapPct=4.0, pivotDistancePct=4.0)
        row["gapRisk"] = gap_chase_metrics(row)
        self.assertTrue(row["gapRisk"]["chase"])
        self.assertFalse(entry_ok(row, 90))

    def test_weak_breadth_is_blocked(self):
        row = self.base()
        row["breadth"] = 0.25
        row["marketRisk"] = market_metrics(row)
        self.assertTrue(row["marketRisk"]["blocked"])
        self.assertFalse(entry_ok(row, 90))

    def test_event_and_estimate_can_block(self):
        row = self.base()
        row["eventRisk"] = {"status": "BLOCK"}
        self.assertFalse(entry_ok(row, 90))

        row = self.base()
        row["estimate"] = {"samples": 12, "deltaPct": -6.0, "trend": "DOWN"}
        self.assertFalse(entry_ok(row, 90))

    def test_track_record_starts_conservative(self):
        state = {
            "signals": [{
                "group": "AGGR_GO", "date": "2026-01-01",
                "outcomes": {"5": {"status": "complete", "returnPct": 5.0, "excessPct": 2.0, "maxDownPct": -2.0}},
            }]
        }
        stats = track_record_stats([state])
        self.assertEqual(stats["mode"], "EARLY")
        self.assertEqual(stats["amountCapKRW"], ENTRY_MIN_KRW)

    def test_strong_track_record_can_unlock_4m(self):
        signals = []
        for i in range(40):
            signals.append({
                "group": "AGGR_GO", "date": f"2026-01-{(i % 28) + 1:02d}",
                "outcomes": {"5": {
                    "status": "complete", "returnPct": 3.0 if i % 4 else -1.0,
                    "excessPct": 2.0 if i % 4 else 0.5, "maxDownPct": -2.0,
                }},
            })
        stats = track_record_stats([{"signals": signals}])
        self.assertEqual(stats["mode"], "STRONG")
        self.assertEqual(stats["amountCapKRW"], ENTRY_MAX_KRW)

    def test_sizing_obeys_track_cap_and_caution(self):
        self.assertEqual(suggested_amount(92, 4.0, track_cap=ENTRY_MIN_KRW), ENTRY_MIN_KRW)
        self.assertEqual(suggested_amount(92, 4.0, market_cap=0.75), ENTRY_MIN_KRW)
        self.assertEqual(suggested_amount(92, 4.0, event_status="WARN"), ENTRY_MIN_KRW)


if __name__ == "__main__":
    unittest.main()
