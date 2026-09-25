import unittest

from fpd.derive import derive_snapshot


class FPDDeriveTests(unittest.TestCase):
    def current(self):
        return {
            "researchId": "FPD-v0.2.1",
            "snapshotDate": "2026-09-25",
            "market": "US",
            "providerNormalizedPayloadHash": "abc",
            "benchmark": {"symbol": "US500", "priceAsOf": "2026-09-25", "close": 105.0},
            "universe": {
                "ABC": {"close": 110.0, "priceAsOf": "2026-09-25", "inUniverse": True}
            },
            "observations": {
                "ABC": [{
                    "ticker": "ABC",
                    "periodType": "FY",
                    "periodEnd": "2027-12-31",
                    "eps": {"avg": 11.0, "low": 10.0, "high": 12.0, "analysts": 12},
                    "revenue": {"avg": 110.0, "low": 105.0, "high": 115.0, "analysts": 10},
                }]
            },
        }

    def prior(self, snapshot_date, period_end="2027-12-31", eps=10.0, revenue=100.0):
        return {
            "snapshotDate": snapshot_date,
            "benchmark": {"symbol": "US500", "priceAsOf": snapshot_date, "close": 100.0},
            "universe": {
                "ABC": {"close": 100.0, "priceAsOf": snapshot_date, "inUniverse": True}
            },
            "observations": {
                "ABC": [{
                    "ticker": "ABC",
                    "periodType": "FY",
                    "periodEnd": period_end,
                    "eps": {"avg": eps, "low": 9.0, "high": 11.0, "analysts": 10},
                    "revenue": {"avg": revenue, "low": 95.0, "high": 105.0, "analysts": 8},
                }]
            },
        }

    def test_30d_revision_and_price_window_are_aligned(self):
        result = derive_snapshot(self.current(), [self.prior("2026-08-26")])
        row = result["symbols"]["ABC"][0]
        r30 = row["lookbacks"]["30D"]
        self.assertAlmostEqual(r30["epsRevisionRaw"], 0.1)
        self.assertAlmostEqual(r30["revenueRevisionRaw"], 0.1)
        self.assertEqual(r30["actualLagDays"], 30)
        self.assertAlmostEqual(r30["epsCoverageChange"], 0.2)
        self.assertAlmostEqual(r30["revenueCoverageChange"], 0.25)
        self.assertAlmostEqual(r30["priceReturnRaw"], 0.1)
        self.assertAlmostEqual(r30["benchmarkReturnRaw"], 0.05)
        self.assertAlmostEqual(r30["relativeStrengthRaw"], 0.05)

    def test_rollover_not_spliced(self):
        result = derive_snapshot(self.current(), [self.prior("2026-08-26", period_end="2026-12-31")])
        r30 = result["symbols"]["ABC"][0]["lookbacks"]["30D"]
        self.assertIsNone(r30["epsRevisionRaw"])
        self.assertIsNone(r30["previousSnapshotDate"])
        self.assertIsNone(r30["priceReturnRaw"])

    def test_outside_tolerance_is_missing(self):
        result = derive_snapshot(self.current(), [self.prior("2026-08-15")])
        r30 = result["symbols"]["ABC"][0]["lookbacks"]["30D"]
        self.assertIsNone(r30["epsRevisionRaw"])
        self.assertIsNone(r30["priceReturnRaw"])

    def test_near_zero_positive_eps_is_preserved_raw(self):
        current = self.current()
        current["observations"]["ABC"][0]["eps"]["avg"] = 0.06
        result = derive_snapshot(current, [self.prior("2026-08-26", eps=0.01)])
        self.assertAlmostEqual(result["symbols"]["ABC"][0]["lookbacks"]["30D"]["epsRevisionRaw"], 5.0)


if __name__ == "__main__":
    unittest.main()
