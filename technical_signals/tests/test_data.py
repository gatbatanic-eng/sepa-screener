import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import data  # noqa: E402
from data import clean_ohlcv  # noqa: E402
from unittest import mock  # noqa: E402


class TestCleanOhlcv(unittest.TestCase):
    def test_drops_trailing_nan_close_bar(self):
        idx = pd.date_range("2026-09-15", periods=4, freq="D")
        df = pd.DataFrame(
            {"Open": [1, 2, 3, np.nan], "High": [1, 2, 3, np.nan], "Low": [1, 2, 3, np.nan],
             "Close": [1.0, 2.0, 3.0, np.nan], "Volume": [10, 10, 10, 0]},
            index=idx,
        )
        out = clean_ohlcv(df)
        self.assertEqual(len(out), 3)
        self.assertEqual(out["Close"].iloc[-1], 3.0)

    def test_keeps_complete_frame_and_handles_empty(self):
        idx = pd.date_range("2026-09-15", periods=2, freq="D")
        df = pd.DataFrame({"Close": [1.0, 2.0]}, index=idx)
        self.assertEqual(len(clean_ohlcv(df)), 2)
        self.assertTrue(clean_ohlcv(pd.DataFrame()).empty)


if __name__ == "__main__":
    unittest.main()


class TestKrListingFallback(unittest.TestCase):
    def test_uses_naver_listing_when_krx_is_down(self):
        naver = pd.DataFrame({"Code": ["005930", "123456"], "Name": ["삼성전자", "OO스팩1호"], "Market": ["KOSPI", "KOSDAQ GLOBAL"]})
        with mock.patch.object(data.fdr, "StockListing", side_effect=ValueError("KRX 점검")), \
                mock.patch.object(data, "naver_kr_listing", return_value=naver) as fallback:
            out = data.fetch_kr_universe()
        fallback.assert_called_once()
        self.assertEqual(list(out["Code"]), ["005930"])  # 스팩 제외는 대체 목록에도 그대로 적용
        self.assertEqual(list(out.columns), ["Code", "Name", "Market"])

    def test_krx_listing_is_preferred_when_available(self):
        krx = pd.DataFrame({"Code": ["005930"], "Name": ["삼성전자"], "Market": ["KOSPI"]})
        with mock.patch.object(data.fdr, "StockListing", return_value=krx), \
                mock.patch.object(data, "naver_kr_listing") as fallback:
            data.fetch_kr_universe()
        fallback.assert_not_called()
