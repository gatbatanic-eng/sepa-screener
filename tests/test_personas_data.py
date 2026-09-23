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


# ---------------------------------------------------------------------------
# personas/generate.py — LLM 코멘트 생성 + 규칙 기반 대체 (네트워크 불필요, fake client)
# ---------------------------------------------------------------------------
from personas import generate as gen  # noqa: E402
from personas.logic import PERSONAS as _PERSONAS  # noqa: E402


class _FakeResp:
    def __init__(self, text):
        self.content = [type("C", (), {"text": text})()]


class _FakeClient:
    def __init__(self, reply):
        self.reply = reply
        self.calls = 0

    class _Msgs:
        def __init__(self, outer):
            self.outer = outer

        def create(self, **kwargs):
            self.outer.calls += 1
            r = self.outer.reply
            return _FakeResp(r(kwargs) if callable(r) else r)

    @property
    def messages(self):
        return self._Msgs(self)


def _leader_row(code="AAA", market="US"):
    return {"code": code, "name": "Leader Co", "market": market, "trendOk": True, "close": 100.0,
            "sma50": 90.0, "sma150": 80.0, "sma200": 70.0, "highProximity": 0.9, "highTier": "LEADER",
            "rsScore": 90.0, "regime": "GREEN", "breadth": 0.6, "exitState": "HOLD", "entryState": "TREND_OK",
            "zone": "READY", "atr20": 2.0, "initRisk": 6.0}


class GenerateTests(unittest.TestCase):
    def test_rule_based_comment_used_without_client(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs" / "data").mkdir(parents=True)
            (root / "docs" / "data" / "latest_us.json").write_text(json.dumps([_leader_row()]), encoding="utf-8")
            r = gen.generate_market("us", root, client=None, today=dt.date(2026, 9, 21))
            self.assertEqual(r["stocks"], 1)
            self.assertEqual(r["usedLLM"], 0)
            self.assertTrue(any("API_KEY" in w for w in r["warnings"]))
            out = json.loads((root / "docs" / "data" / "personas" / "us" / "AAA.json").read_text(encoding="utf-8"))
            self.assertEqual(len(out["personas"]), len(_PERSONAS))
            self.assertFalse(any(p["usedLLM"] for p in out["personas"]))
            for p in out["personas"]:
                self.assertTrue(p["comment"])
            idx = json.loads((root / "docs" / "data" / "personas" / "us" / "index.json").read_text(encoding="utf-8"))
            self.assertIn("AAA", idx["symbols"])

    def test_valid_llm_reply_is_used(self):
        reply = json.dumps({pid: f"{pid} 코멘트입니다. 근거를 종합하면 이렇습니다." for pid in _PERSONAS})
        client = _FakeClient(reply)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs" / "data").mkdir(parents=True)
            (root / "docs" / "data" / "latest_us.json").write_text(json.dumps([_leader_row()]), encoding="utf-8")
            r = gen.generate_market("us", root, client=client, model="fake-model", today=dt.date(2026, 9, 21),
                                    sleep=lambda _: None)
            self.assertEqual(r["usedLLM"], len(_PERSONAS))
            self.assertFalse(r["warnings"])
            out = json.loads((root / "docs" / "data" / "personas" / "us" / "AAA.json").read_text(encoding="utf-8"))
            self.assertTrue(all(p["usedLLM"] for p in out["personas"]))
            self.assertIn("코멘트입니다", out["personas"][0]["comment"])

    def test_forbidden_phrase_falls_back_to_rule_text_for_that_persona(self):
        reply_obj = {pid: "지금 매수하세요! 사세요!" if pid == "trend" else f"{pid} 정상 코멘트." for pid in _PERSONAS}
        client = _FakeClient(json.dumps(reply_obj))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs" / "data").mkdir(parents=True)
            (root / "docs" / "data" / "latest_us.json").write_text(json.dumps([_leader_row()]), encoding="utf-8")
            gen.generate_market("us", root, client=client, model="fake-model", today=dt.date(2026, 9, 21),
                               sleep=lambda _: None)
            out = json.loads((root / "docs" / "data" / "personas" / "us" / "AAA.json").read_text(encoding="utf-8"))
            trend = next(p for p in out["personas"] if p["id"] == "trend")
            self.assertFalse(trend["usedLLM"])
            self.assertNotIn("매수하세요", trend["comment"])
            other = next(p for p in out["personas"] if p["id"] != "trend")
            self.assertTrue(other["usedLLM"])

    def test_malformed_json_reply_falls_back_for_whole_stock(self):
        client = _FakeClient("이건 JSON이 아닙니다")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs" / "data").mkdir(parents=True)
            (root / "docs" / "data" / "latest_us.json").write_text(json.dumps([_leader_row()]), encoding="utf-8")
            r = gen.generate_market("us", root, client=client, model="fake-model", today=dt.date(2026, 9, 21),
                                    sleep=lambda _: None)
            self.assertEqual(r["usedLLM"], 0)
            self.assertTrue(any("JSON" in w for w in r["warnings"]))

    def test_all_llm_failure_is_a_problem_not_silent(self):
        client = _FakeClient(lambda kwargs: (_ for _ in ()).throw(RuntimeError("boom")))
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs" / "data").mkdir(parents=True)
            (root / "docs" / "data" / "latest_us.json").write_text(json.dumps([_leader_row()]), encoding="utf-8")
            r = gen.generate_market("us", root, client=client, model="fake-model", today=dt.date(2026, 9, 21),
                                    sleep=lambda _: None)
            self.assertTrue(r["problems"])
            self.assertEqual(r["usedLLM"], 0)

    def test_stale_stock_file_removed_when_no_longer_trend_ok(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs" / "data" / "personas" / "us").mkdir(parents=True)
            (root / "docs" / "data" / "personas" / "us" / "ZZZ.json").write_text("{}", encoding="utf-8")
            (root / "docs" / "data").mkdir(exist_ok=True)
            (root / "docs" / "data" / "latest_us.json").write_text(json.dumps([_leader_row()]), encoding="utf-8")
            gen.generate_market("us", root, client=None, today=dt.date(2026, 9, 21))
            self.assertFalse((root / "docs" / "data" / "personas" / "us" / "ZZZ.json").exists())

    def test_dry_run_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            (root / "docs" / "data").mkdir(parents=True)
            (root / "docs" / "data" / "latest_us.json").write_text(json.dumps([_leader_row()]), encoding="utf-8")
            gen.generate_market("us", root, client=None, today=dt.date(2026, 9, 21), write=False)
            self.assertFalse((root / "docs" / "data" / "personas").exists())
