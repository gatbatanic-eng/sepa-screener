import unittest

from fpd.backtest import (
    assign_deciles,
    block_bootstrap_mean_ci,
    confirmed_monthly_cohort_dates,
    decile_monotonicity,
    decile_summary,
    spearman_ic,
)


class FPDBacktestTests(unittest.TestCase):
    def test_spearman_ic(self):
        self.assertAlmostEqual(spearman_ic([1, 2, 3, 4], [10, 20, 30, 40]), 1.0)
        self.assertAlmostEqual(spearman_ic([1, 2, 3, 4], [40, 30, 20, 10]), -1.0)

    def test_deciles_are_rank_based(self):
        values = list(range(1, 11))
        self.assertEqual(assign_deciles(values), list(range(1, 11)))

    def test_decile_monotonicity(self):
        values = list(range(1, 101))
        returns = list(range(1, 101))
        summary = decile_summary(values, returns)
        self.assertAlmostEqual(decile_monotonicity(summary), 1.0)

    def test_block_bootstrap_constant_series(self):
        result = block_bootstrap_mean_ci([2.0, 2.0, 2.0, 2.0], block_length=2, n_resamples=100, seed=7)
        self.assertEqual(result["mean"], 2.0)
        self.assertEqual(result["lower"], 2.0)
        self.assertEqual(result["upper"], 2.0)

    def test_monthly_cohort_waits_for_next_month(self):
        dates = [
            "2027-01-05",
            "2027-01-29",
            "2027-02-10",
            "2027-02-26",
            "2027-03-01",
        ]
        self.assertEqual(
            confirmed_monthly_cohort_dates(dates),
            ["2027-01-29", "2027-02-26"],
        )

    def test_missing_true_month_end_is_not_backfilled(self):
        snapshots = [
            "2027-01-28",
            "2027-02-01",
            "2027-02-26",
            "2027-03-01",
        ]
        sessions = [
            "2027-01-28",
            "2027-01-29",
            "2027-02-01",
            "2027-02-26",
            "2027-03-01",
        ]
        self.assertEqual(
            confirmed_monthly_cohort_dates(snapshots, sessions),
            ["2027-02-26"],
        )


if __name__ == "__main__":
    unittest.main()
