import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from agents import freshness as fr, report as rp

D = dt.date(2026, 10, 5)  # 월요일
UTC = dt.timezone.utc


def at(day, hh, mm=0):
    return dt.datetime(2026, 10, day, hh, mm, tzinfo=UTC)


class CheckTest(unittest.TestCase):
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
