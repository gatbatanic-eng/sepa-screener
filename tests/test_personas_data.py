"""personas 데이터 수집 모듈(track_record / valuation / collect) 테스트 — 네트워크 불필요."""
from __future__ import annotations

import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personas import collect, track_record as tr, valuation as val  # noqa: E402


def sig(code, date, sid="s1", group="TREND", ret=None, status=None):
    o = {}
    if ret is not None:
        o["5"] = {"status": "complete", "returnPct": ret, "excessPct": ret - 0.5}
    elif status:
        o["5"] = {"status": status}
    return {"code": code, "date": date, "group": group, "strategyId": sid, "outcomes": o}


def state(signals, days=None):
    days = days or {f"d{i}": {"date": d, "strategyId": "s1"} for i, d in enumerate(["2026-09-14", "2026-09-15"])}
    return {"signals": signals, "days": days, "latestSession": "2026-09-15"}


class TrackRecordTests(unittest.TestCase):
    def test_dedupe_keeps_earliest_across_strategy_versions(self):
        sigs = [sig("AAA", "2026-09-15", "s2"), sig("AAA", "2026-09-14", "s1"), sig("BBB", "2026-09-15", "s2")]
        kept, info = tr.dedupe_signals(sigs, "us", "TREND")
        self.assertEqual(info["raw"], 3)
        self.assertEqual(info["unique"], 2)
        self.assertEqual(info["duplicatesRemoved"], 1)
        self.assertEqual(next(k for k in kept if k["code"] == "AAA")["date"], "2026-09-14")

    def test_kr_codes_are_zero_padded_when_deduping(self):
        kept, info = tr.dedupe_signals([sig(5930, "2026-09-14"), sig("005930", "2026-09-15")], "kr", "TREND")
        self.assertEqual(info["unique"], 1)

    def test_status_none_thin_clustered_ok(self):
        self.assertEqual(tr.build_base_rate(None, "us")["status"], "none")
        pend = state([sig("A", "2026-09-14", status="pending")])
        self.assertEqual(tr.build_base_rate(pend, "us")["status"], "none")
        few = state([sig("A", "2026-09-14", ret=1.0)])
        self.assertEqual(tr.build_base_rate(few, "us")["status"], "thin")
        one_day = state([sig(f"C{i}", "2026-09-14", ret=1.0) for i in range(35)])
        self.assertEqual(tr.build_base_rate(one_day, "us")["status"], "clustered")
        dates = [f"2026-09-{d:02d}" for d in range(1, 8)]
        many = state([sig(f"C{i}", dates[i % 7], ret=1.0) for i in range(35)])
        self.assertEqual(tr.build_base_rate(many, "us")["status"], "ok")

    def test_pending_and_unavailable_are_counted_not_dropped(self):
        st = state([sig("A", "2026-09-14", ret=2.0), sig("B", "2026-09-14", status="pending"),
                    sig("C", "2026-09-14", status="unavailable")])
        h = tr.build_base_rate(st, "us")["horizons"]["5"]
        self.assertEqual((h["n"], h["pending"], h["unavailable"]), (1, 1, 1))

    def test_audit_flags_missing_state_and_missing_sessions(self):
        self.assertFalse(tr.audit(None, "us")["ok"])
        days = {"a": {"date": "2026-09-14", "strategyId": "s1"}, "b": {"date": "2026-09-17", "strategyId": "s1"}}
        a = tr.audit(state([], days), "us", today=dt.date(2026, 9, 17))
        codes = {i["code"] for i in a["issues"]}
        self.assertIn("missing_sessions", codes)


class ValuationTests(unittest.TestCase):
    def test_symbol_mapping(self):
        self.assertEqual(val.yf_symbol("BRKB"), "BRK-B")
        self.assertEqual(val.yf_symbol("brk.b"), "BRK-B")

    def test_parse_info(self):
        self.assertIsNone(val.parse_info({}))
        self.assertIsNone(val.parse_info({"quoteType": "ETF", "trailingPE": 10}))
        r = val.parse_info({"trailingPE": 20, "trailingEps": 5, "currentPrice": 100, "forwardPE": float("nan")})
        self.assertEqual(r["trailingPE"], 20.0)
        self.assertIsNone(r["forwardPE"])
        self.assertFalse(r["peInconsistent"])
        r = val.parse_info({"trailingPE": 20, "trailingEps": 2, "currentPrice": 100})
        self.assertTrue(r["peInconsistent"])
        self.assertTrue(val.parse_info({"trailingEps": -1, "marketCap": 1e9})["lossMaking"])

    def test_collect_reuses_fresh_cache_and_keeps_stale_on_failure(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "v.json"
            t0 = dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc)
            calls = []

            def ok(sym):
                calls.append(sym)
                return {"trailingPE": 10.0}, None

            s = val.collect(["AAA", "BBB"], path, fetcher=ok, now=t0, sleep=lambda _: None)
            self.assertEqual((s["fetched"], s["failed"]), (2, 0))
            s = val.collect(["AAA"], path, fetcher=ok, now=t0 + dt.timedelta(hours=1), sleep=lambda _: None)
            self.assertEqual(s["cached"], 1)
            self.assertEqual(len(calls), 2)
            s = val.collect(["AAA", "NEW"], path, fetcher=lambda sym: (None, "boom"),
                            now=t0 + dt.timedelta(hours=30), sleep=lambda _: None)
            self.assertEqual((s["failed"], s["staleKept"]), (2, 1))
            cache = val.load_cache(path)
            self.assertTrue(cache["symbols"]["AAA"]["stale"])
            self.assertEqual(cache["symbols"]["AAA"]["trailingPE"], 10.0)
            self.assertIsNone(val.get(cache, "NEW"))
            self.assertIsNotNone(val.get(cache, "AAA"))


class CollectTests(unittest.TestCase):
    def _root(self, d, rows, with_state=True):
        root = Path(d)
        (root / "docs" / "data").mkdir(parents=True)
        (root / "docs" / "data" / "latest_us.json").write_text(json.dumps(rows), encoding="utf-8")
        if with_state:
            (root / "research").mkdir()
            (root / "research" / "us.json").write_text(json.dumps(state([sig("AAA", "2026-09-14", ret=1)])), encoding="utf-8")
        return root

    def test_all_valuation_failed_is_a_problem(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, [{"code": "AAA", "trendOk": True}])
            r = collect.collect_market("us", root, fetcher=lambda s: (None, "x"), today=dt.date(2026, 9, 15))
            self.assertTrue(any("전부 실패" in p for p in r["problems"]))
            self.assertTrue((root / "docs" / "data" / "track_record_us.json").exists())

    def test_missing_tracker_is_a_problem(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, [{"code": "AAA", "trendOk": True}], with_state=False)
            r = collect.collect_market("us", root, skip_valuation=True, today=dt.date(2026, 9, 15))
            self.assertTrue(r["problems"])

    def test_partial_failure_is_warning_only(self):
        with tempfile.TemporaryDirectory() as d:
            root = self._root(d, [{"code": "AAA", "trendOk": True}, {"code": "BBB", "trendOk": True}])
            f = lambda s: ({"trailingPE": 9.0}, None) if s == "AAA" else (None, "x")  # noqa: E731
            r = collect.collect_market("us", root, fetcher=f, today=dt.date(2026, 9, 15))
            self.assertFalse(any("밸류에이션" in p for p in r["problems"]))
            self.assertTrue(any("밸류에이션" in w for w in r["warnings"]))


if __name__ == "__main__":
    unittest.main()
