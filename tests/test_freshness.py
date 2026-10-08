import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

from agents import freshness as fr, report as rp

D = dt.date(2026, 10, 5)  # 월요일
UTC = dt.timezone.utc


def at(day, hh, mm=0):
    return dt.datetime(2026, 10, day, hh, mm, tzinfo=UTC)


class CheckTest(unittest.TestCase):
    def setUp(self):  # 저장소에 실제로 있는 한국 개장일 목록에 테스트가 좌우되지 않게 한다
        p = mock.patch.object(fr, "kr_last_bar", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def test_fresh_data_is_not_held(self):
        r = fr.check(D, at(6, 7, 10), {"kr": "2026-10-05", "us": "2026-10-05"})
        self.assertFalse(r["hold"] or r["forced"])
        self.assertIsNone(r["note"])

    def test_stale_us_holds_until_deadline(self):
        s = {"kr": "2026-10-05", "us": "2026-10-02"}
        r = fr.check(D, at(6, 7, 10), s)
        self.assertTrue(r["hold"])
        self.assertEqual(r["stale"]["us"]["have"], "2026-10-02")
        self.assertTrue(fr.check(D, at(6, 15, 10), s)["hold"])

    def test_after_deadline_writes_with_note(self):
        r = fr.check(D, at(6, 19, 10), {"kr": "2026-10-05", "us": "2026-10-02"})
        self.assertFalse(r["hold"])
        self.assertTrue(r["forced"])
        self.assertIn("2026-10-02", r["note"])

    def test_missing_sessions_count_as_stale(self):
        self.assertTrue(fr.check(D, at(6, 7), {})["hold"])

    def test_latest_sessions_takes_later_of_sources(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "docs/recovery/data").mkdir(parents=True)
            (root / "research").mkdir()
            (root / "docs/recovery/data/latest.json").write_text(json.dumps({"sessions": {"kr": "2026-10-02", "us": "2026-10-05"}}))
            (root / "research/kr.json").write_text(json.dumps({"latestSession": "2026-10-05"}))
            self.assertEqual(fr.latest_sessions(root), {"kr": "2026-10-05", "us": "2026-10-05"})
            self.assertEqual(fr.latest_sessions(root / "nowhere"), {"kr": "", "us": ""})


class KrHolidayTest(unittest.TestCase):
    S = {"kr": "2026-10-02", "us": "2026-10-05"}
    BARS = ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-06"]   # 10-05(월)는 휴장, 10-06(화) 봉은 이미 있다

    def bars(self, bars=None, day=6, hh=11):
        return {"bars": bars or self.BARS, "checkedAt": at(day, hh, 30)}

    def test_korean_holiday_is_not_waited_for(self):
        r = fr.check(D, at(6, 15, 10), self.S, self.bars())
        self.assertFalse(r["hold"])
        self.assertEqual(r["krClosed"], "2026-10-02")

    def test_us_is_still_required_on_a_korean_holiday(self):
        r = fr.check(D, at(6, 15, 10), {"kr": "2026-10-02", "us": "2026-10-02"}, self.bars())
        self.assertTrue(r["hold"])
        self.assertEqual(list(r["stale"]), ["us"])

    def test_open_day_still_waits_for_korean_data(self):
        r = fr.check(D, at(6, 15, 10), self.S, self.bars(["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"]))
        self.assertTrue(r["hold"])
        self.assertIsNone(r["krClosed"])

    def test_bars_checked_before_the_close_are_ignored(self):
        early = {"bars": ["2026-10-01", "2026-10-02"], "checkedAt": at(5, 6, 0)}   # 기준일 장 마감 전에 확인한 값은 믿지 않는다
        self.assertTrue(fr.check(D, at(6, 7, 10), self.S, early)["hold"])

    def test_list_that_does_not_cover_the_day_is_ignored(self):
        old = {"bars": ["2026-10-06"], "checkedAt": at(6, 11, 30)}                  # 기준일 이전 구간이 없으면 휴장인지 알 수 없다
        self.assertTrue(fr.check(D, at(6, 15, 10), self.S, old)["hold"])

    def test_missing_calendar_changes_nothing(self):
        self.assertIsNone(fr.kr_last_bar(Path("/nonexistent")))
        with mock.patch.object(fr, "kr_last_bar", return_value=None):
            self.assertTrue(fr.check(D, at(6, 7, 10), self.S)["hold"])  # 달력 정보가 없으면 예전처럼 기다린다

    def test_kr_last_bar_reads_flow_file(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "research/nhplug").mkdir(parents=True)
            (Path(t) / "research/nhplug/kr_flow.json").write_text(json.dumps({"krBars": ["20261006", "20261002"], "generatedAt": "2026-10-06T06:20:00+00:00"}))
            self.assertEqual(fr.kr_last_bar(Path(t))["bars"], ["2026-10-02", "2026-10-06"])


class MorningBasisTest(unittest.TestCase):
    """아침 보고(기준일 10-07부터): 한국은 기준일, 미국은 직전 평일 장 마감까지만 요구하고 그 뒤 시세는 쓰지 않는다."""
    W = dt.date(2026, 10, 7)  # 수요일

    def setUp(self):
        p = mock.patch.object(fr, "kr_last_bar", return_value=None)
        p.start()
        self.addCleanup(p.stop)

    def test_basis(self):
        self.assertEqual(fr.basis(self.W), {"kr": "2026-10-07", "us": "2026-10-06"})
        self.assertEqual(fr.basis(dt.date(2026, 10, 12)), {"kr": "2026-10-12", "us": "2026-10-09"})  # 월요일 → 미국 금요일
        self.assertEqual(fr.basis(D), {"kr": "2026-10-05", "us": "2026-10-05"})                      # 전환 전 기록은 같은 날 기준

    def test_morning_run_is_not_held_for_unconfirmed_us_day(self):
        r = fr.check(self.W, at(7, 23, 17), {"kr": "2026-10-07", "us": "2026-10-06"})
        self.assertFalse(r["hold"] or r["forced"])
        self.assertEqual(r["basis"]["us"], "2026-10-06")

    def test_morning_run_still_waits_for_previous_us_day(self):
        r = fr.check(self.W, at(7, 23, 17), {"kr": "2026-10-07", "us": "2026-10-05"})
        self.assertTrue(r["hold"])
        self.assertEqual(r["stale"]["us"]["need"], "2026-10-06")

    def test_report_shows_basis(self):
        rep = {"title": "t", "disclaimer": "d", "dataAsOf": "a", "sections": [], "basis": fr.basis(self.W)}
        self.assertIn("시세 기준: 한국 2026-10-07 · 미국 2026-10-06 장 마감", rp.to_markdown(rep))

    def test_prices_after_basis_are_dropped(self):
        from agents import main as am

        class Stop(Exception):
            pass

        seen = {}

        def fake_sim(select, sigs, closes, begin, end):
            seen.update(closes)
            raise Stop

        idx = ["2026-10-05", "2026-10-06", "2026-10-07"]
        fetch = lambda m, syms, start, hint=None: ({c: pd.Series([1.0, 2.0, 3.0], index=idx) for c in syms}, {})
        with mock.patch.object(am.sg, "archive", side_effect=lambda m: [{"code": "AAA" if m == "us" else "000001", "exchange": "KOSPI"}]), \
                mock.patch.object(am, "read_json", return_value={"schemaVersion": 1, "agents": {}}), \
                mock.patch.object(am, "simulate", fake_sim):
            with self.assertRaises(Stop):
                am.run(self.W, fetch=fetch, bench_fetch=lambda start: {}, asof=fr.basis(self.W))
        self.assertEqual(list(seen[("us", "AAA")].index), ["2026-10-05", "2026-10-06"])
        self.assertEqual(list(seen[("kr", "000001")].index), idx)


class HoldTest(unittest.TestCase):
    def test_hold_writes_nothing_then_later_run_writes_once(self):
        with tempfile.TemporaryDirectory() as t:
            calls = []
            orig_build, orig_dir = rp.build, rp.REPORT_DIR
            rp.REPORT_DIR = Path(t)
            rp.build = lambda kind, today: calls.append(kind) or {"kind": kind, "key": today.isoformat(), "title": "t", "date": today.isoformat(),
                                                                  "disclaimer": "d", "dataAsOf": "a", "sections": []}
            try:
                lst = Path(t) / "new.txt"
                self.assertEqual(rp.run(D, new_list=lst, hold=True), [])
                self.assertEqual(calls, [])
                self.assertFalse((Path(t) / "daily" / "2026-10-05.json").exists())
                self.assertEqual(rp.run(D, new_list=lst, note="낡은 시세"), ["daily:2026-10-05"])
                self.assertIn("⚠ 낡은 시세", (Path(t) / "daily" / "2026-10-05.md").read_text(encoding="utf-8"))
                self.assertEqual(rp.run(D, new_list=lst), [])  # 같은 날 다시 돌아도 중복 없음
            finally:
                rp.build, rp.REPORT_DIR = orig_build, orig_dir


if __name__ == "__main__":
    unittest.main()
