import unittest
from recovery_mode import strength_score, entry_ok, suggested_amount

class RecoveryModeTest(unittest.TestCase):
    def base(self):
        return {
            "status":"OK","inUniverse":True,"marketOk":True,"trendOkAggressive":True,
            "nearHigh":True,"rsOk":True,"liquidityOk":True,"aggressiveGo":True,
            "breakout":True,"volumeOk":True,"clvOk":True,"rsiOk":True,"notExtended":True,
            "sma50Slope10Pct":4.0,"highProximityPct":-2.0,"pivotDistancePct":1.0,
            "volumeRatio":2.0,"clv":0.85,"rsi14":64,"signalCount":6,"initialRiskPct":4.0,
        }

    def test_good_candidate_passes(self):
        r = self.base()
        s = strength_score(r)
        self.assertGreaterEqual(s, 78)
        self.assertTrue(entry_ok(r, s))

    def test_high_risk_rejected(self):
        r = self.base()
        r["initialRiskPct"] = 6.0
        self.assertFalse(entry_ok(r, strength_score(r)))

    def test_sizing(self):
        self.assertEqual(suggested_amount(92, 4.0), 4_000_000)
        self.assertEqual(suggested_amount(86, 4.8), 3_500_000)
        self.assertEqual(suggested_amount(80, 5.2), 3_000_000)

if __name__ == "__main__":
    unittest.main()
