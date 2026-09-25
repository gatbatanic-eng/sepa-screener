import unittest
from datetime import date
from unittest.mock import patch

from fpd.provider_fmp import fetch_calendar_range


class FPDProviderTests(unittest.TestCase):
    def test_calendar_range_splits_over_90_days(self):
        calls = []

        def fake_chunk(endpoint, start, end, api_key=None, max_pages=10):
            calls.append((endpoint, start, end))
            return [{"date": end.isoformat()}]

        with patch("fpd.provider_fmp._calendar_chunk", side_effect=fake_chunk):
            rows = fetch_calendar_range(
                "splits-calendar",
                date(2026, 6, 1),
                date(2026, 9, 25),
                api_key="x",
            )

        self.assertEqual(len(calls), 2)
        self.assertLessEqual((calls[0][2] - calls[0][1]).days, 89)
        self.assertLessEqual((calls[1][2] - calls[1][1]).days, 89)
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
