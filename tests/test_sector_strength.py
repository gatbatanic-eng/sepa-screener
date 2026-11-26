"""sector_strength.py — 네트워크 없이 집계·분류·기록 규칙을 확인한다."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sector_strength as ss


def row(code, rs, change=None, passed=False):
    return {"code": code, "name": f"종목{code}", "rsScore": rs, "rsChange20d": change, "passAll": passed, "changePct": 1.0}


class NamesTest(unittest.TestCase):
    def test_krx_sector_name_strips_market(self):
        self.assertEqual(ss.krx_sector_name("코스피 전기·전자"), "전기·전자")
        self.assertEqual(ss.krx_sector_name("코스닥 전기·전자"), "전기·전자")
        for bad in ("코스닥", "", None, "코스피 제조"):
            self.assertIsNone(ss.krx_sector_name(bad))

    def test_ksic_rules(self):
        self.assertEqual(ss.ksic_to_krx("반도체 제조업"), "전기·전자")
        self.assertEqual(ss.ksic_to_krx("기초 의약물질 및 생물학적 제제 제조업"), "제약")
        self.assertEqual(ss.ksic_to_krx("자동차 신품 부품 제조업"), "운송장비·부품")
        self.assertEqual(ss.ksic_to_krx("소프트웨어 개발 및 공급업"), "IT 서비스")
        self.assertEqual(ss.ksic_to_krx("전기업"), "전기·가스")
        self.assertIsNone(ss.ksic_to_krx("nan"))
        self.assertIsNone(ss.ksic_to_krx("알 수 없는 업종"))


class AggregateTest(unittest.TestCase):
    def setUp(self):
        self.rows = [row("A", 90, 10, True), row("B", 80, 0), row("C", 70, -10),   # 반도체: 중앙값 80
                     row("D", 40, 5), row("E", 30, 5), row("F", 20, 5),             # 화학: 중앙값 30
                     row("G", 99), row("H", 50), {"code": "X", "rsScore": None}]
        self.map = {c: {"sector": "반도체"} for c in "ABC"} | {c: {"sector": "화학"} for c in "DEF"} | {"G": {"sector": "통신"}}

    def test_metrics_and_ranking(self):
        out = ss.aggregate(self.rows, self.map)
        semi, chem = out["sectors"][0], out["sectors"][1]
        self.assertEqual((semi["sector"], semi["rank"], semi["n"], semi["rs"]), ("반도체", 1, 3, 80))
        self.assertEqual(semi["rsDelta"], 0)              # 20일 전 중앙값 80 → 변화 0
        self.assertEqual(semi["strongPct"], 66.7)         # 90, 80
        self.assertEqual(semi["trendPct"], 33.3)
        self.assertEqual([x["code"] for x in semi["leaders"]], ["A", "B", "C"])
        self.assertEqual((chem["rank"], chem["rsDelta"]), (2, 5))

    def test_small_and_unclassified_are_not_ranked(self):
        out = ss.aggregate(self.rows, self.map)
        tail = out["sectors"][2:]
        self.assertEqual([s["sector"] for s in tail], ["통신", ss.UNCLASSIFIED])   # 미분류는 맨 끝
        self.assertTrue(all(s["small"] and "rank" not in s for s in tail))
        self.assertEqual((out["stocks"], out["unclassified"], out["coveragePct"]), (8, 1, 87.5))   # rsScore 없는 X는 셈에서 제외


class HistoryTest(unittest.TestCase):
    def test_same_date_replaced_and_trimmed(self):
        sectors = [{"sector": "반도체", "rs": 80, "small": False}, {"sector": "통신", "rs": 99, "small": True}]
        hist = [{"date": "2026-11-24", "rs": {"반도체": 70}}, {"date": "2026-11-25", "rs": {"반도체": 1}}]
        out = ss.update_history(hist, "2026-11-25", sectors)
        self.assertEqual(out[-1], {"date": "2026-11-25", "rs": {"반도체": 80}})   # 표본 적은 섹터는 기록하지 않음
        self.assertEqual(len(out), 2)
        many = [{"date": f"2026-01-{i:02d}", "rs": {}} for i in range(1, 32)] * 5
        self.assertEqual(len(ss.update_history(many, "2026-12-01", sectors)), ss.HISTORY_DAYS)

    def test_rank_change(self):
        sectors = [{"sector": "화학", "rank": 1}, {"sector": "반도체", "rank": 2}, {"sector": "새섹터", "rank": 3}]
        hist = [{"date": "2026-11-24", "rs": {"반도체": 80, "화학": 30}}, {"date": "2026-11-25", "rs": {}}]
        ss.attach_rank_change(sectors, hist, "2026-11-25")
        self.assertEqual([s.get("rankChange") for s in sectors], [1, -1, None])


class SectorMapTest(unittest.TestCase):
    def test_kr_uses_nh_first_then_ksic_cache(self):
        with tempfile.TemporaryDirectory() as d:
            nh = Path(d) / "nh.json"
            nh.write_text(json.dumps({"stocks": {"005930": {"sector": "코스피 전기·전자"}, "000001": {"sector": "코스닥"}}}), encoding="utf-8")
            cache = Path(d) / "map.json"
            cache.write_text(json.dumps({"updatedAt": "2099-01-01T00:00:00+00:00", "industry": {"000001": "의약품 제조업"}}), encoding="utf-8")
            with mock.patch.object(ss, "NH_FILES", [nh]):
                out = ss.kr_sector_map({"005930", "000001", "999999"}, fetch=False, cache_path=cache)
        self.assertEqual(out["005930"], {"sector": "전기·전자", "source": "krx"})
        self.assertEqual(out["000001"], {"sector": "제약", "source": "ksic"})
        self.assertNotIn("999999", out)

    def test_us_fetch_failure_falls_back_to_cache(self):
        with tempfile.TemporaryDirectory() as d:
            cache = Path(d) / "map.json"
            cache.write_text(json.dumps({"updatedAt": "2000-01-01T00:00:00+00:00", "sector": {"AAPL": "Information Technology"}}), encoding="utf-8")
            with mock.patch.object(ss, "_listing", side_effect=OSError("down")):
                out = ss.us_sector_map({"AAPL", "BRK-B"}, fetch=True, cache_path=cache)
        self.assertEqual(out, {"AAPL": {"sector": "정보기술", "source": "gics"}})

    def test_us_ticker_override(self):
        import pandas as pd

        listing = pd.DataFrame({"Symbol": ["BRKB", "AAPL"], "Sector": ["Financials", "Information Technology"]})
        with tempfile.TemporaryDirectory() as d, mock.patch.object(ss, "_listing", return_value=listing):
            out = ss.us_sector_map({"BRK-B", "AAPL"}, fetch=True, cache_path=Path(d) / "map.json")
        self.assertEqual(out["BRK-B"]["sector"], "금융")


if __name__ == "__main__":
    unittest.main()
