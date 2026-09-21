import unittest

from recovery_mode import (
    ENTRY_MAX_KRW,
    ENTRY_MIN_KRW,
    entry_ok,
    gap_chase_metrics,
    market_metrics,
    strength_score,
    suggested_amount,
    track_record_stats,
)


class RecoveryModeTest(unittest.TestCase):
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
