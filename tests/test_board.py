import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from ledger import adapters, board, config
from ledger.store import read_gz

U = dt.timezone.utc


def at(*a):
    return dt.datetime(*a, tzinfo=U)


class SessionRuleTest(unittest.TestCase):
    def test_expected_session_waits_for_the_due_time(self):
        self.assertEqual(board.expected_session("kr", at(2026, 10, 8, 12, 0)), dt.date(2026, 10, 7))   # 13:00 UTC 전
        self.assertEqual(board.expected_session("kr", at(2026, 10, 8, 13, 5)), dt.date(2026, 10, 8))
        self.assertEqual(board.expected_session("us", at(2026, 10, 10, 7, 30)), dt.date(2026, 10, 8))  # 금요일 미국장 기한은 토 12:00 UTC
        self.assertEqual(board.expected_session("us", at(2026, 10, 10, 13, 0)), dt.date(2026, 10, 9))

    def test_korean_holiday_is_skipped(self):
        self.assertEqual(board.expected_session("kr", at(2026, 10, 10, 7, 30), {"2026-10-09"}), dt.date(2026, 10, 8))
        self.assertEqual(board.sessions_behind("2026-10-08", dt.date(2026, 10, 8), {"2026-10-09"}), 0)

    def test_holidays_from_open_day_list(self):
        bars = ["2026-10-06", "2026-10-07", "2026-10-08"]
        # 목록 확인 시각이 10-09 장 마감 뒤인데 10-09가 없으면 휴장. 구간 안에서 빠진 평일도 휴장.
        self.assertEqual(board.kr_holidays(bars, at(2026, 10, 9, 15, 31)), {"2026-10-09"})
        self.assertEqual(board.kr_holidays(["2026-09-30", "2026-10-02"], None), {"2026-10-01"})
        # 확인 시각이 장 마감 전이면 그날은 아직 모른다
        self.assertEqual(board.kr_holidays(bars, at(2026, 10, 9, 6, 0)), set())

    def test_status_daily_and_weekly(self):
        now = at(2026, 10, 10, 13, 10)
        self.assertEqual(board.judge_status("2026-10-09", "daily", "us", now)[0], "ok")
        self.assertEqual(board.judge_status("2026-10-08", "daily", "us", now)[0], "lag")
        self.assertEqual(board.judge_status("2026-10-07", "daily", "us", now)[0], "down")
        self.assertEqual(board.judge_status(None, "daily", "us", now)[0], "wait")
        # 주간: 시세일이 아니라 실행한 날로 비교한다. 토요일 12:00 UTC 전에는 지연을 봐준다.
        self.assertEqual(board.judge_status("2026-10-09", "weekly", "us", now, run_date="2026-10-10")[0], "ok")
        self.assertEqual(board.judge_status("2026-10-02", "weekly", "kr", now, run_date="2026-10-03")[0], "down")
        self.assertEqual(board.judge_status("2026-10-02", "weekly", "kr", at(2026, 10, 10, 7, 0), run_date="2026-10-03")[0], "ok")


class ReadTest(unittest.TestCase):
    def test_tracker_selected_counts_and_coverage(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "research").mkdir()
            doc = {"latestSession": "2026-10-08", "updatedAt": "2026-10-08T19:00:00+00:00",
                   "days": {"s1:2026-10-08": {"date": "2026-10-08", "strategySeriesId": "s1", "rows": 10, "observedCodes": ["a"] * 9,
                                               "recordedAt": "2026-10-08T11:00:00+00:00"},
                            "s0:2026-10-08": {"date": "2026-10-08", "strategySeriesId": "s0", "rows": 10, "observedCodes": [],
                                               "recordedAt": "2026-10-08T10:00:00+00:00"}},
                   "membership": {"s1:000001:RANGE_GO": True, "s1:000002:RANGE_GO": True, "s1:000003:RANGE_WATCH": True,
                                  "s1:000004:RANGE_GO": False, "s0:000009:RANGE_GO": True}}
            (root / "research" / "range_kr.json").write_text(json.dumps(doc), encoding="utf-8")
            r = board.read_tracker("range", "kr", root)
            self.assertEqual(r["selected"], {"RANGE_GO": 2, "RANGE_WATCH": 1})   # 최신 계열만, 현재 소속만
            self.assertEqual(r["coverage"], {"observed": 9, "rows": 10, "ratio": 0.9})
            self.assertIsNone(board.read_tracker("range", "us", root))

    def test_perf_picks_first_group_with_completed_samples(self):
        ledger = {"strategies": {"multifactor": {"us": {
            "BUY": {"5": {"n": 0, "pending": 1, "status": "none"}, "20": {"n": 0, "status": "none"}},
            "WATCH": {"5": {"n": 43, "meanExcessPct": -0.77, "status": "concentrated", "independentWindows": 1}, "20": {"n": 0, "status": "none"}}}}}}
        spec = next(s for s in board.BOARD if s["key"] == "multifactor")
        p = board.pick_perf(ledger, spec, "us")
        self.assertEqual(p["group"], "WATCH")
        self.assertEqual(p["h5"]["judge"], "독립 구간 부족")
        self.assertIsNone(board.pick_perf(ledger, spec, "kr"))
        self.assertEqual(board.perf_note(spec, ledger, "kr"), "원장 집계 대기 (다음 갱신부터 표시)")
        self.assertEqual(board.perf_note(next(s for s in board.BOARD if s["key"] == "fpd"), ledger, "kr"), "성과 집계 없음(연구 단계)")

    def test_low_observed_ratio_marks_lag(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "research").mkdir()
            doc = {"latestSession": "2026-10-08", "updatedAt": "2026-10-08T19:00:00+00:00",
                   "days": {"s:2026-10-08": {"date": "2026-10-08", "strategySeriesId": "s", "rows": 100, "observedCodes": ["a"] * 50, "recordedAt": "x"}},
                   "membership": {}}
            for name in ("range_kr.json", "range_us.json", "aggressive_kr.json", "aggressive_us.json", "rebound_kr.json", "kr.json", "us.json"):
                (root / "research" / name).write_text(json.dumps(doc), encoding="utf-8")
            out = board.build({"strategies": {}}, now=at(2026, 10, 8, 14, 0), root=root)
            row = next(r for r in out["rows"] if r["key"] == "range" and r["market"] == "kr")
            self.assertEqual(row["status"], "lag")
            self.assertIn("판정 가능한 종목이 적습니다", row["notes"][0])
            self.assertEqual(out["counts"]["ok"] + out["counts"]["lag"] + out["counts"]["down"] + out["counts"]["wait"], len(out["rows"]))


class FunnelBoardTest(unittest.TestCase):
    def test_funnel_row_uses_session_and_flags_degraded_runs(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs" / "research").mkdir(parents=True)
            doc = {"recordedAt": "2026-10-13T09:25:00+00:00", "session": "2026-10-13", "top": [{}] * 50,
                   "degraded": {"events": "DART 주요사항 공시 2/4 구간을 읽지 못해 증자·분할 건수를 미상으로 뒀습니다"}}
            (root / "docs" / "research" / "funnel_kr.json").write_text(json.dumps(doc), encoding="utf-8")
            r = board.read_funnel("kr", root)
            self.assertEqual(r["last"], "2026-10-13")
            out = board.build({"strategies": {}}, now=at(2026, 10, 13, 14, 0), root=root)
            row = next(x for x in out["rows"] if x["key"] == "funnel" and x["market"] == "kr")
            self.assertEqual(row["status"], "lag")                  # 기록은 됐지만 일부 규칙이 미반영
            self.assertTrue(any("미상" in n for n in row["notes"]))
            self.assertEqual(row["cadence"], "daily")


class TechnicalLedgerTest(unittest.TestCase):
    def rows(self):
        base = {"status": "OK", "close": 100.0, "market": "KOSPI", "trendScore": 10}
        return [dict(base, code="000001", name="가", trendVerdict="관찰", reboundVerdict=None),
                dict(base, code="000002", name="나", trendVerdict=None, reboundVerdict="매수검토"),
                dict(base, code="000003", name="다", trendVerdict="진입보류", reboundVerdict="관찰"),
                dict(base, code="000004", name="라"),
                dict(base, code="000005", name="마", status="확인불가")]

    def test_groups_and_write_once(self):
        self.assertEqual(adapters.technical_groups({"trendVerdict": "진입보류", "reboundVerdict": "관찰"}), ["TREND_HOLD", "REBOUND_WATCH"])
        self.assertEqual(adapters.technical_groups({"trendVerdict": None}), [])
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            latest = base / "latest_kr.json"
            latest.write_text(json.dumps({"rows": self.rows()}), encoding="utf-8")
            sig = base / "signals"
            self.assertEqual(adapters.ingest_technical("kr", latest, "2026-10-08T11:50:00+00:00", sig), 1)
            self.assertEqual(adapters.ingest_technical("kr", latest, "2026-10-08T12:50:00+00:00", sig), 0)   # 같은 거래일 두 번째는 쓰지 않는다
            doc = read_gz(sig / "technical" / "kr" / "2026-10-08.json.gz")
            by = {r["symbol"]: r["groups"] for r in doc["rows"]}
            self.assertEqual(by["000001"][0], "TREND_WATCH")
            self.assertEqual(by["000002"][0], "REBOUND_REVIEW")
            self.assertNotIn("000005", by)                          # 확인불가 행은 제외
            self.assertTrue(all(r["exchange"] == "KOSPI" for r in doc["rows"]))
            self.assertEqual(doc["effectiveDate"], "2026-10-08")

    def test_unusable_file_fails_loudly(self):
        with tempfile.TemporaryDirectory() as d:
            latest = Path(d) / "latest_kr.json"
            latest.write_text(json.dumps({"rows": [{"code": "1", "status": "확인불가"}]}), encoding="utf-8")
            with self.assertRaises(ValueError):
                adapters.ingest_technical("kr", latest, "2026-10-08T11:50:00+00:00", Path(d) / "s")


class TrackerAdapterTest(unittest.TestCase):
    def test_tracker_signals_for_each_strategy_file(self):
        with tempfile.TemporaryDirectory() as d:
            research = Path(d)
            sig = {"id": "x1", "group": "RANGE_GO", "date": "2026-10-01", "code": "000001", "name": "가", "originalClose": 100.0, "benchmark": "KOSPI",
                   "outcomes": {"5": {"status": "complete", "returnPct": 2.0, "benchmarkPct": 1.0, "excessPct": 1.0, "maxDownPct": -1.0}}}
            (research / "range_kr.json").write_text(json.dumps({"signals": [sig]}), encoding="utf-8")
            rows = adapters.tracker_signals("range", "kr", research)
            self.assertEqual(rows[0]["id"], "range:kr:x1")
            self.assertEqual(rows[0]["strategy"], "range")
            self.assertEqual(rows[0]["outcomes"][5]["excessPct"], 1.0)
            self.assertEqual(adapters.tracker_signals("rebound", "us", research), [])
        self.assertEqual(set(adapters.TRACKERS), {"sepa", "range", "aggressive", "rebound"})
        self.assertEqual(adapters.LEDGER_STRATEGIES, ("funnel", "multifactor", "technical"))


if __name__ == "__main__":
    unittest.main()
