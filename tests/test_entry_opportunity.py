import unittest
from entry_opportunity import match_current_aggressive


class DualTrackTests(unittest.TestCase):
    def setUp(self):
        self.sepa = {"latestSession": "2026-09-22"}
        self.rows = [{"code": "MRNA", "status": "OK", "close": 182.56, "entryVerdict": "NO-GO"}]
        self.aggressive = {"latestSession": "2026-09-22", "latestRows": [{
            "code": "MRNA", "status": "OK", "inUniverse": True,
            "priceAsOf": "2026-09-22", "benchmarkAsOf": "2026-09-22",
            "dataFreshness": "CURRENT", "close": 182.56, "marketOk": True,
            "aggressiveGo": True, "aggressiveWatch": False, "breakout": True,
            "riskOk": True, "initialRiskPct": 4.27, "referenceStop": 175.09,
            "breakoutLevel": 176.86,
        }]}

    def match(self):
        return match_current_aggressive(self.rows, self.sepa, self.aggressive)

    def test_mrna_no_go_stays_no_go_and_aggressive_is_visible(self):
        self.assertEqual(self.match()["MRNA"]["aggressiveOverlay"], "GO")
        self.assertTrue(self.match()["MRNA"]["aggressiveSepaConflict"])
        self.assertEqual(self.rows[0]["entryVerdict"], "NO-GO")

    def test_stale_or_future_sessions_suppressed(self):
        self.aggressive["latestSession"] = "2026-09-21"
        self.assertEqual(self.match(), {})
        self.aggressive["latestSession"] = "2026-09-22"
        self.aggressive["latestRows"][0]["priceAsOf"] = "2026-09-23"
        self.assertEqual(self.match(), {})

    def test_price_mismatch_and_duplicates_suppressed(self):
        a = self.aggressive["latestRows"][0]
        a["close"] = 185
        self.assertEqual(self.match(), {})
        a["close"] = 182.56
        self.aggressive["latestRows"].append(a.copy())
        self.assertEqual(self.match(), {})

    def test_invalid_stops_and_market_gate_suppressed(self):
        a = self.aggressive["latestRows"][0]
        a["referenceStop"] = 185
        self.assertEqual(self.match(), {})
        a["referenceStop"] = 175.09
        a["marketOk"] = False
        self.assertEqual(self.match(), {})

    def test_watch_does_not_invent_zero_risk(self):
        a = self.aggressive["latestRows"][0]
        a.update(aggressiveGo=False, aggressiveWatch=True, breakout=False,
                 initialRiskPct=0, referenceStop=185)
        result = self.match()["MRNA"]
        self.assertEqual(result["aggressiveOverlay"], "WATCH")
        self.assertIsNone(result["aggressiveRiskPct"])
        self.assertIsNone(result["aggressiveStop"])

    def test_missing_input_hidden_not_rejected(self):
        self.assertEqual(match_current_aggressive(self.rows, {}, self.aggressive), {})
        self.assertEqual(match_current_aggressive(self.rows, self.sepa, {}), {})


if __name__ == "__main__":
    unittest.main()
