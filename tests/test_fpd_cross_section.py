import unittest

from fpd.cross_section import score_primary_f1, select_forward_rows


def row(period_end, eps, rev, pv, rs):
    return {
        "ticker": None,
        "periodType": "FY",
        "periodEnd": period_end,
        "lookbacks": {
            "30D": {
                "epsRevisionRaw": eps,
                "revenueRevisionRaw": rev,
                "priceReturnRaw": pv,
                "relativeStrengthRaw": rs,
            }
        },
    }


class FPDCrossSectionTests(unittest.TestCase):
    def derived(self):
        return {
            "schemaVersion": 1,
            "researchId": "FPD-v0.2.1",
            "researchDefinitionHash": "hash",
            "snapshotDate": "2026-09-25",
            "market": "US",
            "symbols": {
                "LOW": [
                    row("2028-12-31", 0.3, 0.3, 0.3, 0.3),
                    row("2027-12-31", 0.0, 0.0, 0.2, 0.2),
                ],
                "MID": [row("2027-12-31", 0.1, 0.1, 0.1, 0.1)],
                "HIGH": [row("2027-12-31", 0.2, 0.2, 0.0, 0.0)],
            },
        }

    def test_f1_is_nearest_future_period_not_input_order(self):
        labelled = select_forward_rows(self.derived())
        self.assertEqual(labelled["LOW"][0]["periodEnd"], "2027-12-31")
        self.assertEqual(labelled["LOW"][0]["forwardOrdinal"], "F1")
        self.assertEqual(labelled["LOW"][1]["forwardOrdinal"], "F2")

    def test_primary_fpd_uses_robust_cross_section(self):
        result = score_primary_f1(self.derived())
        rows = {x["ticker"]: x for x in result["rows"]}
        self.assertLess(rows["LOW"]["fpdCRZ"], 0)
        self.assertAlmostEqual(rows["MID"]["fpdCRZ"], 0.0)
        self.assertGreater(rows["HIGH"]["fpdCRZ"], 0)
        self.assertLess(rows["LOW"]["fpdCRZRank"], rows["HIGH"]["fpdCRZRank"])

    def test_missing_revenue_keeps_composite_missing(self):
        data = self.derived()
        data["symbols"]["MID"][0]["lookbacks"]["30D"]["revenueRevisionRaw"] = None
        result = score_primary_f1(data)
        mid = next(x for x in result["rows"] if x["ticker"] == "MID")
        self.assertIsNone(mid["rvCompositeRZ"])
        self.assertIsNone(mid["fpdCRZ"])


if __name__ == "__main__":
    unittest.main()
