import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import naver_listing as nl

ROOT = Path(__file__).resolve().parents[1]
COPIES = [ROOT / "technical_signals" / "naver_listing.py", ROOT / "momentum_signals" / "naver_listing.py",
          ROOT / "range_vrebound" / "src" / "data" / "naver_listing.py"]


def payload(prefix: str, start: int, n: int, name_key="stockName"):
    return {"stocks": [{"itemCode": f"{start + i:06d}", name_key: f"{prefix}{start + i}", "marketValue": "1,234억",
                        "accumulatedTradingValue": "5,000,000"} for i in range(n)], "totalCount": 99999}


class FakeResponse:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def fake_get_factory(kospi=1100, kosdaq=1100, name_key="stockName"):
    def get(url, params=None, **kw):
        market = url.rsplit("/", 1)[1]
        total = kospi if market == "KOSPI" else kosdaq
        base = 0 if market == "KOSPI" else 500000
        page = params["page"]
        n = max(0, min(100, total - (page - 1) * 100))
        return FakeResponse(payload(market, base + (page - 1) * 100, n, name_key))
    return get


class NaverListingTest(unittest.TestCase):
    def test_copies_are_identical(self):
        source = (ROOT / "naver_listing.py").read_text(encoding="utf-8")
        for copy in COPIES:
            self.assertEqual(copy.read_text(encoding="utf-8"), source, f"{copy} 가 루트 naver_listing.py와 다르다")

    def test_parse_number_korean_units(self):
        self.assertAlmostEqual(nl.parse_number("1조 2,345억"), 1.2345e12)
        self.assertAlmostEqual(nl.parse_number("3,000억"), 3e11)
        self.assertEqual(nl.parse_number("1,234"), 1234.0)
        self.assertTrue(pd.isna(nl.parse_number("-")))
        self.assertTrue(pd.isna(nl.parse_number(None)))

    def test_parse_page_requires_code_and_name(self):
        rows = nl.parse_page({"stocks": [{"itemCode": "5930", "stockName": "삼성전자", "marketValue": "400조"},
                                         {"itemCode": "000660"},               # 이름 없음
                                         {"itemCode": "ABC123", "stockName": "x"}]}, "KOSPI")  # 코드 형식 아님
        self.assertEqual([(r["Code"], r["Name"], r["Market"]) for r in rows], [("005930", "삼성전자", "KOSPI")])
        self.assertAlmostEqual(rows[0]["Marcap"], 4e14)

    def test_listing_pages_through_both_markets(self):
        frame = nl.naver_kr_listing(get=fake_get_factory())
        self.assertEqual(len(frame), 2200)
        self.assertEqual(set(frame["Market"]), {"KOSPI", "KOSDAQ"})
        self.assertEqual(frame.attrs["source"], "naver-fallback")
        self.assertTrue((frame["Marcap"] > 0).all())

    def test_too_small_or_unnamed_response_fails(self):
        with self.assertRaises(RuntimeError):
            nl.naver_kr_listing(get=fake_get_factory(kospi=300, kosdaq=200))
        with self.assertRaises(RuntimeError):  # 이름 키가 바뀌면 이름 없는 행으로 걸러져 0종목
            nl.naver_kr_listing(get=fake_get_factory(name_key="unknownKey"))


class FallbackWiringTest(unittest.TestCase):
    def test_screening_falls_back_when_krx_is_down(self):
        import screening
        fallback = pd.DataFrame({"Code": ["005930"], "Name": ["삼성전자"], "Market": ["KOSPI"], "Marcap": [4e14], "Amount": [1e9]})
        with mock.patch.object(screening.fdr, "StockListing", side_effect=ValueError("KRX 점검")), \
                mock.patch.object(screening.time, "sleep"), \
                mock.patch("naver_listing.naver_kr_listing", return_value=fallback):
            self.assertIs(screening.fetch_stock_listing("KRX"), fallback)
        # KRX 이외 시장(S&P500 등)은 대체하지 않고 그대로 실패한다
        with mock.patch.object(screening.fdr, "StockListing", side_effect=ValueError("down")), \
                mock.patch.object(screening.time, "sleep"):
            with self.assertRaises(RuntimeError):
                screening.fetch_stock_listing("S&P500")

    def test_funnel_refuses_fallback_listing(self):
        import screening
        from funnel import data_kr
        frame = pd.DataFrame({"Code": ["005930"], "Name": ["삼성전자"], "Market": ["KOSPI"], "Marcap": [4e14]})
        frame.attrs["source"] = "naver-fallback"
        with mock.patch.object(screening, "fetch_stock_listing", return_value=frame):
            with self.assertRaisesRegex(RuntimeError, "대체 목록"):
                data_kr.load_universe()


if __name__ == "__main__":
    unittest.main()
