import unittest

from fpd.analysis import analyze_monthly_research


def cohort(day, offset):
    rows = []
    for i in range(20):
        signal = float(i)
        excess = float(i + offset) / 100.0
        rows.append({
            "ticker": f"T{i:02d}",
            "fpdCRZ": signal,
            "outcomes": {
                "65": {
                    "status": "complete",
                    "excessReturnRaw": excess,
                }
            },
        })
    return {"snapshotDate": day, "rows": rows}


class FPDAnalysisTests(unittest.TestCase):
    def test_monotonic_synthetic_signal(self):
        monthly = {
            "market": "US",
            "rawSnapshots": 100,
            "confirmedMonthlyCohorts": 3,
            "cohorts": [
                cohort("2027-01-29", 0),
                cohort("2027-02-26", 1),
                cohort("2027-03-31", 2),
            ],
        }
        result = analyze_monthly_research(monthly)["horizons"]["65"]
        self.assertEqual(result["completedCohorts"], 3)
        self.assertAlmostEqual(result["meanIC"], 1.0)
        self.assertAlmostEqual(result["positiveICRate"], 1.0)
        self.assertGreater(result["meanD10_D1"], 0)
        self.assertAlmostEqual(result["decileMonotonicity"], 1.0)
        self.assertIsNotNone(result["D10_D1_BlockBootstrap"])

    def test_pending_outcomes_do_not_enter_validation(self):
        monthly = {
            "market": "US",
            "rawSnapshots": 5,
            "confirmedMonthlyCohorts": 1,
            "cohorts": [{
                "snapshotDate": "2027-01-29",
                "rows": [{
                    "ticker": "ABC",
                    "fpdCRZ": 1.0,
                    "outcomes": {"65": {"status": "pending"}},
                }],
            }],
        }
        result = analyze_monthly_research(monthly)["horizons"]["65"]
        self.assertEqual(result["completedCohorts"], 0)
        self.assertIsNone(result["meanIC"])


if __name__ == "__main__":
    unittest.main()
