import sys
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import data  # noqa: E402


class TestKrListingFallback(unittest.TestCase):
    def test_uses_naver_listing_when_krx_is_down(self):
        naver = pd.DataFrame({"Code": ["005930", "654321", "005935"], "Name": ["삼성전자", "OO스팩2호", "삼성전자우"],
                              "Market": ["KOSPI", "KOSDAQ", "KOSPI"]})
        with mock.patch.object(data.fdr, "StockListing", side_effect=ValueError("KRX 점검")), \
                mock.patch.object(data, "naver_kr_listing", return_value=naver) as fallback:
            out = data.fetch_kr_candidate_universe()
        fallback.assert_called_once()
        self.assertEqual(list(out["Code"]), ["005930"])  # 스팩·우선주 제외는 대체 목록에도 적용
        self.assertEqual(list(out.columns), ["Code", "Name", "Market"])

    def test_krx_listing_is_preferred_when_available(self):
        krx = pd.DataFrame({"Code": ["005930"], "Name": ["삼성전자"], "Market": ["KOSPI"]})
        with mock.patch.object(data.fdr, "StockListing", return_value=krx), \
                mock.patch.object(data, "naver_kr_listing") as fallback:
            data.fetch_kr_candidate_universe()
        fallback.assert_not_called()


if __name__ == "__main__":
    unittest.main()
