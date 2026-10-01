import unittest
from datetime import datetime
from unittest.mock import patch

import pandas as pd

import screening


def _frame(last_day: str) -> pd.DataFrame:
    idx = pd.to_datetime(["2026-09-16", "2026-09-17", last_day]).drop_duplicates()
    values = range(1, len(idx) + 1)
    return pd.DataFrame(
        {"Open": values, "High": values, "Low": values, "Close": values, "Volume": values},
        index=idx,
    )


class KoreanPriceFallbackTest(unittest.TestCase):
    @patch("screening._latest_closed_kr_session", return_value=pd.Timestamp("2026-09-18"))
    @patch("screening._download_yahoo_history")
    @patch("screening.fdr.DataReader")
    @patch("screening.time.sleep", return_value=None)
    def test_stale_primary_uses_newer_yahoo_close(self, _sleep, data_reader, yahoo, _session):
        data_reader.return_value = _frame("2026-09-17")
        yahoo.return_value = _frame("2026-09-18")

        result = screening.fetch_price_history("005930", "2025-01-01", market="KOSPI")

        self.assertEqual(result.index.max(), pd.Timestamp("2026-09-18"))
        yahoo.assert_called_once_with("005930.KS", "2025-01-01")

    @patch("screening._latest_closed_kr_session", return_value=pd.Timestamp("2026-09-18"))
    @patch("screening._download_yahoo_history")
    @patch("screening.fdr.DataReader")
    @patch("screening.time.sleep", return_value=None)
    def test_fresh_primary_does_not_call_fallback(self, _sleep, data_reader, yahoo, _session):
        data_reader.return_value = _frame("2026-09-18")

        result = screening.fetch_price_history("005930", "2025-01-01", market="KOSPI")

        self.assertEqual(result.index.max(), pd.Timestamp("2026-09-18"))
        yahoo.assert_not_called()

    @patch("screening._latest_closed_kr_session", return_value=pd.Timestamp("2026-09-21"))
    @patch("screening._download_pykrx_latest")
    @patch("screening._download_naver_history")
    @patch("screening._download_yahoo_history")
    @patch("screening.fdr.DataReader")
    @patch("screening.time.sleep", return_value=None)
    def test_stale_primary_and_yahoo_use_latest_krx_close(self, _sleep, data_reader, yahoo, naver, pykrx, _session):
        data_reader.return_value = _frame("2026-09-17")
        yahoo.return_value = _frame("2026-09-18")
        naver.return_value = _frame("2026-09-18")
        pykrx.return_value = _frame("2026-09-21").tail(1)

        result = screening.fetch_price_history("005930", "2025-01-01", market="KOSPI")

        self.assertEqual(result.index.max(), pd.Timestamp("2026-09-21"))
        self.assertIn(pd.Timestamp("2026-09-18"), result.index)

    @patch("screening._latest_closed_kr_session", return_value=pd.Timestamp("2026-09-21"))
    @patch("screening._download_pykrx_latest")
    @patch("screening._download_naver_history")
    @patch("screening._download_yahoo_history")
    @patch("screening.fdr.DataReader")
    @patch("screening.time.sleep", return_value=None)
    def test_krx_confirmed_holiday_keeps_last_actual_session(self, _sleep, data_reader, yahoo, naver, pykrx, _session):
        data_reader.return_value = _frame("2026-09-18")
        yahoo.return_value = _frame("2026-09-18")
        naver.return_value = _frame("2026-09-18")
        pykrx.return_value = _frame("2026-09-18").tail(1)

        result = screening.fetch_price_history("005930", "2025-01-01", market="KOSPI")

        self.assertEqual(result.index.max(), pd.Timestamp("2026-09-18"))

    @patch("screening._latest_closed_kr_session", return_value=pd.Timestamp("2026-09-21"))
    @patch("screening._download_pykrx_latest")
    @patch("screening._download_naver_history")
    @patch("screening._download_yahoo_history")
    @patch("screening.fdr.DataReader")
    @patch("screening.time.sleep", return_value=None)
    def test_stale_vendor_data_uses_newer_naver_close(self, _sleep, data_reader, yahoo, naver, pykrx, _session):
        data_reader.return_value = _frame("2026-09-17")
        yahoo.return_value = _frame("2026-09-18")
        naver.return_value = _frame("2026-09-21")

        result = screening.fetch_price_history("005930", "2025-01-01", market="KOSPI")

        self.assertEqual(result.index.max(), pd.Timestamp("2026-09-21"))
        pykrx.assert_not_called()

    def test_korean_yahoo_symbol_mapping(self):
        self.assertEqual(screening._kr_yahoo_symbol("KS11", "KOSPI"), "^KS11")
        self.assertEqual(screening._kr_yahoo_symbol("KQ11", "KOSDAQ"), "^KQ11")
        self.assertEqual(screening._kr_yahoo_symbol("005930", "KOSPI"), "005930.KS")
        self.assertEqual(screening._kr_yahoo_symbol("035720", "KOSDAQ"), "035720.KQ")
        self.assertIsNone(screening._kr_yahoo_symbol("AAPL", "US"))
        self.assertEqual(
            screening._kr_yahoo_symbols("035720", "KR"),
            ["035720.KS", "035720.KQ"],
        )


class USPriceFallbackTest(unittest.TestCase):
    @patch("screening._latest_closed_us_session", return_value=pd.Timestamp("2026-09-18"))
    @patch("screening._download_yahoo_history")
    @patch("screening.fdr.DataReader")
    @patch("screening.time.sleep", return_value=None)
    def test_stale_us_uses_current_closed_quote(self, _sleep, reader, yahoo, _session):
        reader.return_value = _frame("2026-09-17")
        yahoo.return_value = _frame("2026-09-18")
        result = screening.fetch_price_history("AMD", "2025-01-01", market="US")
        self.assertEqual(result.index.max(), pd.Timestamp("2026-09-18"))
        yahoo.assert_called_once_with("AMD", "2025-01-01")

    @patch("screening._latest_closed_us_session", return_value=pd.Timestamp("2026-09-18"))
    @patch("screening._download_yahoo_history")
    def test_partial_daily_candle_is_removed(self, yahoo, _session):
        primary = pd.concat([_frame("2026-09-18"), _frame("2026-09-21").tail(1)])
        result = screening._closed_us_history("AMD", "2025-01-01", primary)
        self.assertEqual(result.index.max(), pd.Timestamp("2026-09-18"))
        yahoo.assert_not_called()

    @patch("screening._latest_closed_us_session", return_value=pd.Timestamp("2026-09-18"))
    @patch("screening._download_yahoo_history", side_effect=RuntimeError("unavailable"))
    def test_failed_refresh_keeps_true_old_date(self, yahoo, _session):
        result = screening._closed_us_history("AMD", "2025-01-01", _frame("2026-09-17"))
        self.assertEqual(result.index.max(), pd.Timestamp("2026-09-17"))

    @patch("screening._latest_closed_us_session", return_value=pd.Timestamp("2026-09-18"))
    @patch("screening._download_yahoo_history")
    def test_missing_primary_and_index_symbol(self, yahoo, _session):
        yahoo.return_value = _frame("2026-09-18")
        result = screening._closed_us_history(screening.US_INDEX_CODE, "2025-01-01", None)
        self.assertEqual(result.index.max(), pd.Timestamp("2026-09-18"))
        yahoo.assert_called_once_with("^GSPC", "2025-01-01")

    def test_us_close_cutoff_uses_dst_and_weekends(self):
        for instant, expected in [
            ("2026-10-01T00:00:00+00:00", "2026-09-30"),
            ("2026-09-30T19:00:00+00:00", "2026-09-29"),
            ("2026-10-04T12:00:00+00:00", "2026-10-02"),
            ("2026-01-05T21:05:00+00:00", "2026-01-02"),
            ("2026-01-05T21:10:00+00:00", "2026-01-05"),
        ]:
            with self.subTest(instant=instant):
                self.assertEqual(screening._latest_closed_us_session(datetime.fromisoformat(instant)), pd.Timestamp(expected))


if __name__ == "__main__":
    unittest.main()
