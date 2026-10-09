import datetime as dt
import tempfile
import unittest
from pathlib import Path

import publish_fundamentals as pf


def rows(revenue="1,000", op="100", rcept="20260814000001"):
    base = {"rcept_no": rcept, "currency": "KRW", "thstrm_add_amount": revenue}
    return [
        {**base, "sj_div": "IS", "account_id": "ifrs-full_Revenue", "account_nm": "매출액", "thstrm_amount": revenue},
        {**base, "sj_div": "IS", "account_id": "dart_OperatingIncomeLoss", "account_nm": "영업이익", "thstrm_amount": op},
        {**base, "sj_div": "BS", "account_id": "ifrs-full_Liabilities", "account_nm": "부채총계", "thstrm_amount": "500"},
        {**base, "sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계", "thstrm_amount": "1,500"},
        {**base, "sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계", "thstrm_amount": "2,000", "extra": "x" * 50},
        {**base, "sj_div": "IS", "account_id": "dart_Other", "account_nm": "기타손익", "thstrm_amount": "7"},
    ]


class FakeDart:
    def __init__(self, empty_years=()):
        self.calls = []
        self.empty_years = empty_years

    def request(self, endpoint, **p):
        if endpoint == "company.json":
            return {"induty_code": "26110"}
        self.calls.append((p["bsns_year"], p["reprt_code"], p["fs_div"]))
        return [] if p["bsns_year"] in self.empty_years else rows()


class SlimTest(unittest.TestCase):
    def test_slim_keeps_normalize_result_identical(self):
        full = {(2025, q): {"rows": rows(str(1000 * q)), "basis": "CFS"} for q in (1, 2, 3, 4)}
        small = {k: {"rows": pf.slim(v["rows"]), "basis": v["basis"]} for k, v in full.items()}
        self.assertEqual(pf.normalize(full), pf.normalize(small))
        self.assertLess(len(small[(2025, 1)]["rows"]), len(full[(2025, 1)]["rows"]))
        self.assertTrue(all("extra" not in r for r in small[(2025, 1)]["rows"]))


class SettleTest(unittest.TestCase):
    def test_period_end_and_settled(self):
        self.assertEqual(pf.period_end(2025, 4), dt.date(2025, 12, 31))
        self.assertEqual(pf.period_end(2026, 2), dt.date(2026, 6, 30))
        today = dt.date(2026, 10, 1)
        self.assertTrue(pf.is_settled(2026, 1, today))    # 3/31 + 150일 = 8/28 이전
        self.assertFalse(pf.is_settled(2026, 2, today))   # 6/30 + 150일 = 11/27 이후에야 확정
        self.assertTrue(pf.is_settled(2025, 4, today))


class CorpListTest(unittest.TestCase):
    def zipped(self):
        import io, zipfile
        xml = ("<result><list><corp_code>00000001</corp_code><stock_code>005930</stock_code></list>"
               "<list><corp_code>00000002</corp_code><stock_code> </stock_code></list></result>")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("CORPCODE.xml", xml)
        return buf.getvalue()

    def api(self, responses):
        class A(pf.Dart):
            def request(self, endpoint, **p):
                return responses.pop(0)
        return A("key")

    def test_error_message_has_dart_status_but_no_secret(self):
        for raw, expect in ((b'{"status":"020","message":"limit exceeded"}', "status=020"),
                            (b"<result><status>010</status><message>unregistered key</message></result>", "status=010"),
                            (b"<html>maintenance</html>", "non-zip response")):
            with tempfile.TemporaryDirectory() as d, self.assertRaises(RuntimeError) as ctx:
                self.api([raw]).corporations(Path(d))
            self.assertIn(expect, str(ctx.exception))
            self.assertNotIn("key", str(ctx.exception).replace("unregistered key", ""))

    def test_failed_download_falls_back_to_last_good_list(self):
        with tempfile.TemporaryDirectory() as d:
            api = self.api([self.zipped(), b'{"status":"020","message":"x"}'])
            self.assertEqual(api.corporations(Path(d)), {"005930": "00000001"})
            self.assertEqual(api.corps_source, "live")
            self.assertEqual(api.corporations(Path(d)), {"005930": "00000001"})
            self.assertEqual(api.corps_source, "cache")

    def test_stale_days(self):
        now = dt.datetime(2026, 10, 9, 12, tzinfo=dt.timezone.utc)
        self.assertEqual(pf.stale_days("2026-10-08T11:51:00+00:00", now), 1)
        self.assertEqual(pf.stale_days("2026-10-06T11:00:00+00:00", now), 3)
        self.assertIsNone(pf.stale_days(None, now))


class CacheTest(unittest.TestCase):
    now = dt.datetime(2026, 10, 1, 3, tzinfo=dt.timezone.utc)

    def test_second_run_requests_only_unsettled_periods(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            first = FakeDart()
            r1 = pf.fetch_reports(first, "C1", "000001", self.now, cache)
            second = FakeDart()
            r2 = pf.fetch_reports(second, "C1", "000001", self.now, cache)
            self.assertEqual(r1, r2)
            self.assertGreater(len(first.calls), 10)
            # 확정되지 않은 2026Q2(11/27까지)와 아직 공시 전인 2026Q3만 다시 묻는다
            self.assertEqual({(y, c) for y, c, _ in second.calls}, {(2026, "11012"), (2026, "11014")})

    def test_missing_settled_report_is_cached_not_asked_again(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d)
            pf.fetch_reports(FakeDart(empty_years=(2023,)), "C1", "000001", self.now, cache)
            again = FakeDart(empty_years=(2023,))
            pf.fetch_reports(again, "C1", "000001", self.now, cache)
            self.assertFalse([c for c in again.calls if c[0] == 2023])

    def test_collect_stock_returns_normalized_quarters(self):
        with tempfile.TemporaryDirectory() as d:
            code, company, quarters = pf.collect_stock(FakeDart(), {"000001": "C1"}, {"code": "1", "name": "x"}, self.now, Path(d))
            self.assertEqual(code, "000001")
            self.assertEqual(company["induty_code"], "26110")
            self.assertTrue(quarters and quarters[-1]["year"] == 2026)
            with self.assertRaises(RuntimeError):
                pf.collect_stock(FakeDart(), {}, {"code": "9", "name": "y"}, self.now, Path(d))


if __name__ == "__main__":
    unittest.main()
