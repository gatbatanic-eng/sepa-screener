import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from fpd.features import closest_observation, coverage_change, dispersion, pct_revision, percentile_rank, robust_z
from fpd.normalize import normalize_fmp_estimate
from fpd.quality import validate_estimate, validate_symbol_rows
from fpd.storage import read_gzip_json, write_immutable_gzip_json


class FPDCoreTests(unittest.TestCase):
    def test_normalize_aliases(self):
        row = {
            "date": "2027-09-30",
            "estimatedEpsAvg": 5.0,
            "estimatedEpsLow": 4.5,
            "estimatedEpsHigh": 5.5,
            "numberAnalystEstimatedEps": 12,
            "estimatedRevenueAvg": 100.0,
            "estimatedRevenueLow": 95.0,
            "estimatedRevenueHigh": 110.0,
            "numberAnalystEstimatedRevenue": 10,
        }
        out = normalize_fmp_estimate("ABC", row)
        self.assertEqual(out["periodEnd"], "2027-09-30")
        self.assertEqual(out["eps"]["analysts"], 12)
        self.assertEqual(out["revenue"]["avg"], 100.0)
        self.assertEqual(validate_estimate(out), [])

    def test_normalize_current_fmp_stable_fields(self):
        row = {
            "symbol": "AAPL",
            "date": "2030-09-27",
            "revenueLow": 648.0,
            "revenueHigh": 735.0,
            "revenueAvg": 679.0,
            "epsAvg": 13.565,
            "epsHigh": 15.01999,
            "epsLow": 12.76582,
            "numAnalystsRevenue": 16,
            "numAnalystsEps": 7,
        }
        out = normalize_fmp_estimate("AAPL", row)
        self.assertEqual(out["eps"]["analysts"], 7)
        self.assertEqual(out["revenue"]["analysts"], 16)
        self.assertEqual(validate_estimate(out), [])

    def test_same_fiscal_period_only(self):
        history = [
            {"snapshotDate": "2026-08-26", "periodEnd": "2027-12-31", "value": 10},
            {"snapshotDate": "2026-08-25", "periodEnd": "2026-12-31", "value": 99},
        ]
        matched = closest_observation(history, date(2026, 9, 25), "2027-12-31", 30, 5)
        self.assertEqual(matched["value"], 10)

    def test_rollover_rejected(self):
        history = [{"snapshotDate": "2026-08-26", "periodEnd": "2026-12-31", "value": 99}]
        self.assertIsNone(closest_observation(history, date(2026, 9, 25), "2027-12-31", 30, 5))

    def test_revision_no_epsilon(self):
        self.assertAlmostEqual(pct_revision(11, 10), 0.1)
        self.assertIsNone(pct_revision(1, 0))
        self.assertIsNone(pct_revision(-1, 1))

    def test_dispersion_and_coverage(self):
        self.assertAlmostEqual(dispersion(10, 8, 12), 0.4)
        self.assertIsNone(dispersion(0, -1, 1))
        self.assertEqual(coverage_change(3, 0), 3.0)

    def test_robust_z_mad_zero(self):
        self.assertEqual(robust_z([1, 1, 1, None]), [0.0, 0.0, 0.0, None])

    def test_percentile_rank_average_ties(self):
        ranked = percentile_rank([1, 2, 2, 4])
        self.assertAlmostEqual(ranked[0], 0.125)
        self.assertAlmostEqual(ranked[1], 0.5)
        self.assertAlmostEqual(ranked[2], 0.5)
        self.assertAlmostEqual(ranked[3], 0.875)

    def test_duplicate_period_rejected(self):
        row = normalize_fmp_estimate("ABC", {"date": "2027-09-30", "estimatedEpsAvg": 1})
        self.assertIn("DUPLICATE_FISCAL_PERIOD", validate_symbol_rows([row, row]))

    def test_immutable_snapshot(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "x.json.gz"
            payload = {"a": 1, "b": [2, 3]}
            write_immutable_gzip_json(path, payload)
            self.assertEqual(read_gzip_json(path), payload)
            with self.assertRaises(RuntimeError):
                write_immutable_gzip_json(path, payload)


if __name__ == "__main__":
    unittest.main()
