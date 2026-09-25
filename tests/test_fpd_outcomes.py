import unittest

from fpd.outcomes import attach_outcomes, forward_outcome
from fpd.research import research_readiness


def snapshot(day, price, benchmark):
    return {
        "snapshotDate": day,
        "market": "US",
        "benchmark": {"close": benchmark},
        "universe": {"ABC": {"close": price, "inUniverse": True}},
    }


class FPDOutcomeTests(unittest.TestCase):
    def test_forward_outcome_complete(self):
        snaps = [
            snapshot("2027-01-04", 100, 100),
            snapshot("2027-01-05", 105, 101),
            snapshot("2027-01-06", 110, 102),
        ]
        result = forward_outcome(snaps, 0, "ABC", 2)
        self.assertEqual(result["status"], "complete")
        self.assertAlmostEqual(result["returnRaw"], 0.10)
        self.assertAlmostEqual(result["benchmarkReturnRaw"], 0.02)
        self.assertAlmostEqual(result["excessReturnRaw"], 0.08)
        self.assertAlmostEqual(result["maxUpRaw"], 0.10)
        self.assertAlmostEqual(result["maxDownRaw"], 0.0)

    def test_forward_outcome_pending(self):
        snaps = [snapshot("2027-01-04", 100, 100), snapshot("2027-01-05", 101, 101)]
        result = forward_outcome(snaps, 0, "ABC", 2)
        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["observedSessions"], 1)

    def test_missing_terminal_price_not_silently_dropped(self):
        snaps = [
            snapshot("2027-01-04", 100, 100),
            {"snapshotDate": "2027-01-05", "benchmark": {"close": 101}, "universe": {}},
        ]
        result = forward_outcome(snaps, 0, "ABC", 1)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["reason"], "PRICE_OR_BENCHMARK_MISSING")

    def test_market_session_horizon_ignores_missing_intermediate_raw_snapshot(self):
        snaps = [
            snapshot("2027-01-04", 100, 100),
            snapshot("2027-01-06", 110, 102),
        ]
        sessions = ["2027-01-04", "2027-01-05", "2027-01-06"]
        result = forward_outcome(snaps, 0, "ABC", 2, market_sessions=sessions)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["targetDate"], "2027-01-06")
        self.assertAlmostEqual(result["returnRaw"], 0.10)
        self.assertEqual(result["pathStatus"], "missing")
        self.assertIsNone(result["maxUpRaw"])

    def test_missing_exact_target_snapshot_is_unavailable_not_shifted(self):
        snaps = [
            snapshot("2027-01-04", 100, 100),
            snapshot("2027-01-07", 120, 103),
        ]
        sessions = ["2027-01-04", "2027-01-05", "2027-01-06", "2027-01-07"]
        result = forward_outcome(snaps, 0, "ABC", 2, market_sessions=sessions)
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["targetDate"], "2027-01-06")
        self.assertEqual(result["reason"], "RAW_SNAPSHOT_MISSING_AT_TARGET")

    def test_attach_outcomes_preserves_signal(self):
        signal = {
            "researchId": "FPD-v0.2.1",
            "researchDefinitionHash": "x",
            "snapshotDate": "2027-01-04",
            "market": "US",
            "signalFiscalSelector": "F1",
            "rows": [{"ticker": "ABC", "fpdCRZ": 1.2}],
        }
        snaps = [snapshot("2027-01-04", 100, 100), snapshot("2027-01-05", 110, 105)]
        result = attach_outcomes(signal, snaps, 0, horizons=(1,))
        self.assertEqual(result["rows"][0]["fpdCRZ"], 1.2)
        self.assertEqual(result["rows"][0]["outcomes"]["1"]["status"], "complete")


if __name__ == "__main__":
    unittest.main()
