import datetime as dt
import unittest
import unittest.mock

from goldencode import rankings as r

KST = dt.timezone(dt.timedelta(hours=9))


def row(code, name, value="7,202", hangeul="72억원", at="2026-10-30T14:45:18+09:00", end="stock", pct="29.24"):
    return {"itemCode": code, "stockName": name, "stockEndType": end, "closePrice": "1,830", "fluctuationsRatio": pct,
            "accumulatedTradingVolume": "4,142,041", "accumulatedTradingValue": value,
            "accumulatedTradingValueKrwHangeul": hangeul, "localTradedAt": at, "marketStatus": "OPEN"}


class Resp:
    def __init__(self, rows):
        self.rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return {"result": {"stocks": self.rows}}


class RankingsTest(unittest.TestCase):
    def test_won_parses_korean_units(self):
        self.assertEqual(r.won("4조 22억원"), 4.0022e12)
        self.assertEqual(r.won("3.7억원"), 3.7e8)
        self.assertIsNone(r.won("N/A"))

    def test_turnover_unit_and_age(self):
        fetched = dt.datetime(2026, 10, 30, 14, 46, 18, tzinfo=KST)
        x = r.normalize(row("093240", "형지엘리트"), "KOSPI", fetched)
        self.assertEqual(x["turnoverKRW"], 7.202e9)
        self.assertEqual(x["ageSeconds"], 60.0)
        self.assertEqual(x["warnings"], [])

    def test_unit_mismatch_falls_back_to_hangeul(self):
        fetched = dt.datetime(2026, 10, 30, 14, 46, tzinfo=KST)
        x = r.normalize(row("1", "a", value="7,202,000", hangeul="72억원"), "KOSPI", fetched)
        self.assertEqual(x["turnoverKRW"], 7.2e9)
        self.assertIn("turnover_unit_mismatch", x["warnings"])

    def test_stale_row_is_flagged(self):
        fetched = dt.datetime(2026, 10, 30, 14, 52, tzinfo=KST)
        x = r.normalize(row("1", "a"), "KOSPI", fetched)
        self.assertIn("row_older_than_300s", x["warnings"])

    def test_kinds(self):
        for name, end, want in [("삼성전자우", "stock", "preferred"), ("한화플러스제6호스팩", "stock", "spac"),
                                ("KODEX 200", "etf", "etf"), ("SK하이닉스", "stock", "common")]:
            self.assertEqual(r.kind_of({"stockName": name, "stockEndType": end}), want)

    def test_collect_merges_and_counts_sources(self):
        now = dt.datetime.now(KST).isoformat()

        def get(url, params=None, **kw):
            if params["page"] > 1:
                return Resp([])
            return Resp([row("005930", "삼성전자", at=now), row("000660", "SK하이닉스", at=now)])
        with unittest.mock.patch("time.sleep"):
            snap = r.collect(get=get)
        top = snap["candidates"][0]
        self.assertEqual(snap["status"], "degraded")  # 목록이 목표 수에 못 미치면 정상으로 보고하지 않는다
        self.assertEqual(snap["errors"], [])
        self.assertEqual(top["sourceCount"], len(r.LISTS) * 2)
        self.assertEqual(snap["quality"]["candidateCount"], 2)


if __name__ == "__main__":
    unittest.main()
