import datetime as dt
import json
import re
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from advisory import config, picks, picks_track, recommend, verify


def row(code="AAA", **kw):
    base = {"market": "US", "code": code, "name": code, "sector": f"S-{code}", "close": 100.0, "strengthScore": 70, "aggressiveGo": True,
            "breakout": True, "volumeRatio": 2.0, "clv": 0.9, "pivotDistancePct": 1.0, "rsScore": 90, "highProximityPct": -1.0,
            "sectorCohortScore": 60, "initialRiskPct": 3.0, "notExtended": True, "gapPct": 0.5, "liquidityOk": True, "referenceStop": 95.0,
            "breakoutLevel": 99.0, "priceAsOf": "2026-10-02", "dataFreshness": "CURRENT",
            "marketRisk": {"blocked": False, "breadth": 0.5}, "eventRisk": {"status": "OK"}, "fundamental": {"risk": "OK"},
            "estimate": {"trend": "UP"}, "gapRisk": {"chase": False}, "regime": "GREEN"}
    base.update(kw)
    return base


def screener_row(code, **kw):
    base = {"code": code, "status": "OK", "close": 100.0, "initRisk": 5.0, "exitState": "HOLD", "riskFlag": None, "passAll": True,
            "zone": "READY", "market": "US"}
    base.update(kw)
    return base


def make_root(t: Path, rows_by_code: dict, pool: list[dict]):
    (t / "docs" / "data").mkdir(parents=True)
    (t / "docs" / "recovery" / "data").mkdir(parents=True)
    (t / "docs" / "data" / "latest_us.json").write_text(json.dumps(list(rows_by_code.values())), encoding="utf-8")
    (t / "docs" / "data" / "latest_kr.json").write_text("[]", encoding="utf-8")
    (t / "docs" / "recovery" / "data" / "latest.json").write_text(json.dumps({"strongest": pool, "sessions": {"us": "2026-10-02"}, "marketRegimes": {}}), encoding="utf-8")


class LensTest(unittest.TestCase):
    def test_lenses_are_bounded_and_reward_the_right_things(self):
        good = row()
        for name, fn in picks.LENSES:
            self.assertTrue(0 <= fn(good) <= 100, name)
        weak = row(aggressiveGo=False, breakout=False, volumeRatio=0.8, clv=0.3, rsScore=40, highProximityPct=-9, initialRiskPct=9, notExtended=False, gapPct=7, liquidityOk=False)
        for name, fn in picks.LENSES:
            self.assertLess(fn(weak), fn(good), name)
        self.assertEqual(picks.safety_lens(row(initialRiskPct=None, gapPct=None, notExtended=False, liquidityOk=False)), 0.0 + 0)

    def test_rank_orders_by_composite_then_strength(self):
        r = picks.rank([row("LOW", rsScore=50, volumeRatio=1.0, aggressiveGo=False), row("HIGH")])
        self.assertEqual([p["code"] for p in r], ["HIGH", "LOW"])

    def test_weight_formula(self):
        self.assertAlmostEqual(picks.weight(8.0, "neutral", "B"), 0.125 * 0.75 * 0.5, places=4)
        self.assertEqual(picks.weight(8.0, "offense", "C"), 0.0)                       # 기각은 비중 0
        self.assertEqual(picks.weight(2.0, "offense", "A"), config.PICK_MAX_WEIGHT)    # 상한 15%
        self.assertIsNone(picks.weight(None, "offense", "A"))

    def test_plan_falls_back_to_minus_8_percent_when_stop_is_above_price(self):
        pl = picks.plan(row(close=100.0, referenceStop=105.0, breakoutLevel=110.0))
        self.assertTrue(pl["fallback"])
        self.assertAlmostEqual(pl["plannedLossPct"], 8.0)
        self.assertAlmostEqual(pl["referenceStop"], 92.0)


class VerifyTest(unittest.TestCase):
    def audit(self, rec=None, scr=None, ctx=None, chart_close=None):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            codes = {} if scr is False else {"AAA": scr or screener_row("AAA")}
            make_root(t, codes, [])
            if chart_close is not None:
                d = t / "docs" / "data" / "stock_charts" / "us"
                d.mkdir(parents=True)
                (d / "AAA.json").write_text(json.dumps({"close": [chart_close], "priceAsOf": "2026-10-02"}), encoding="utf-8")
            rec = rec or row("AAA")
            return verify.audit({"market": "us", "code": "AAA", "name": "AAA", "price": rec["close"], "row": rec}, verify.Screener(t), ctx or {"macroRegime": "neutral", "expectedSession": {"us": "2026-10-02"}})

    def test_clean_pick_passes(self):
        v = self.audit(chart_close=100.0)
        self.assertEqual((v["verdict"], v["grade"]), ("통과", "A"))

    def test_fail_conditions_reject(self):
        self.assertEqual(self.audit(scr=False)["verdict"], "기각")                                     # 본 결과에 없음
        self.assertEqual(self.audit(scr=screener_row("AAA", close=110.0))["verdict"], "기각")          # 종가 불일치 > 1%
        self.assertEqual(self.audit(scr=screener_row("AAA", exitState="TREND_BREAK"))["verdict"], "기각")
        self.assertEqual(self.audit(scr=screener_row("AAA", initRisk=15.0))["verdict"], "기각")
        self.assertEqual(self.audit(rec=row("AAA", eventRisk={"status": "BLOCK", "date": "2026-10-06"}))["verdict"], "기각")
        self.assertEqual(self.audit(rec=row("AAA", gapRisk={"chase": True}))["verdict"], "기각")

    def test_warn_conditions_are_conditional(self):
        self.assertEqual(self.audit(rec=row("AAA", marketRisk={"blocked": True, "blockReason": "BREADTH", "breadth": 0.26}))["verdict"], "조건부")
        self.assertEqual(self.audit(ctx={"macroRegime": "risk_off", "expectedSession": {"us": "2026-10-02"}})["verdict"], "조건부")
        self.assertEqual(self.audit(scr=screener_row("AAA", passAll=False))["verdict"], "조건부")
        self.assertEqual(self.audit(rec=row("AAA", estimate={"trend": "DOWN", "deltaPct": -12}))["verdict"], "조건부")

    def test_chart_mismatch_is_a_warning_not_a_fail(self):
        self.assertEqual(self.audit(chart_close=120.0)["verdict"], "조건부")

    def test_teams_are_independent(self):
        src = lambda n: (Path(__file__).resolve().parents[1] / "advisory" / n).read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"^\s*(from|import)\s+.*\bverify\b", src("picks.py"), re.M))
        self.assertIsNone(re.search(r"^\s*(from|import)\s+.*\bpicks\b", src("verify.py"), re.M))


class RecommendTest(unittest.TestCase):
    def run_reco(self, pool, screener, stance="neutral", macro="neutral"):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            make_root(t, screener, pool)
            return recommend.run(t, stance, macro)

    def test_minimum_two_even_when_everything_is_rejected_and_marked_watch_only(self):
        pool = [row(c) for c in ("A1", "A2", "A3")]
        scr = {c: screener_row(c, exitState="TREND_BREAK") for c in ("A1", "A2", "A3")}
        r = self.run_reco(pool, scr)
        self.assertEqual(len(r["picks"]), config.PICK_MIN)
        self.assertTrue(all(p["grade"] == "C" and p["weight"] == 0.0 for p in r["picks"]))
        self.assertEqual(r["watchOnly"], 2)
        self.assertIn("검증을 통과한 공격 진입 종목이 없습니다", r["headline"])

    def test_rejected_are_replaced_by_next_candidates_up_to_three(self):
        pool = [row("A1", rsScore=99), row("A2"), row("A3"), row("A4"), row("A5", rsScore=10, volumeRatio=1.0)]
        scr = {c: screener_row(c) for c in ("A1", "A2", "A3", "A4", "A5")}
        scr["A1"]["exitState"] = "TREND_BREAK"                     # 가장 점수 높은 종목을 검증 팀이 기각
        r = self.run_reco(pool, scr)
        codes = [p["code"] for p in r["picks"]]
        self.assertNotIn("A1", codes)
        self.assertEqual(len(codes), 3)
        self.assertTrue(all(p["grade"] in ("A", "B") for p in r["picks"]))
        self.assertEqual(r["rejected"], 1)

    def test_same_sector_is_limited_when_alternatives_exist(self):
        pool = [row("A1", sector="X"), row("A2", sector="X", rsScore=88), row("A3", sector="Y", rsScore=70), row("A4", sector="Z", rsScore=60)]
        r = self.run_reco(pool, {c: screener_row(c) for c in ("A1", "A2", "A3", "A4")})
        self.assertEqual(sum(1 for p in r["picks"] if p["sector"] == "X"), 1)

    def test_empty_pool_is_reported_not_faked(self):
        r = self.run_reco([], {})
        self.assertEqual(r["picks"], [])
        self.assertIn("후보 풀이 비어", r["headline"])


class TrackTest(unittest.TestCase):
    def rec(self):
        return {"headline": "h", "picks": [{"market": "US", "code": "AAA", "name": "AAA", "grade": "A", "price": 100.0, "composite": 70, "weight": 0.1},
                                           {"market": "KOSDAQ", "code": "111111", "name": "K", "grade": "C", "price": 5000.0, "composite": 60, "weight": 0.0}]}

    def test_record_is_write_once_and_control_is_deterministic(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            make_root(t, {c: screener_row(c) for c in ("C1", "C2", "C3", "C4")}, [])
            d = t / "picks"
            self.assertTrue(picks_track.record("2026-10-05", self.rec(), t, d))
            first = (d / "2026-10-05.json").read_text()
            self.assertFalse(picks_track.record("2026-10-05", dict(self.rec(), headline="바뀜"), t, d))
            self.assertEqual((d / "2026-10-05.json").read_text(), first)
            e = json.loads(first)["entries"]
            self.assertEqual([x["group"] for x in e], ["PICK", "PICK", "CONTROL", "CONTROL"])
            self.assertEqual(e[1]["exchange"], "KOSDAQ")
            self.assertEqual(picks_track._control("2026-10-05", t), picks_track._control("2026-10-05", t))

    def test_update_computes_outcomes_from_next_session_close_and_summarizes_by_grade(self):
        days = [d.date().isoformat() for d in pd.bdate_range("2026-10-05", periods=50)]
        px = pd.Series([100.0 + i for i in range(50)], index=days)          # 진입일(10/6) 종가 101 → 5거래일 뒤 106
        bench = pd.Series([1000.0] * 50, index=days)
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            make_root(t, {"C1": screener_row("C1")}, [])
            d = t / "picks"
            picks_track.record("2026-10-05", {"headline": "h", "picks": [{"market": "US", "code": "AAA", "name": "AAA", "grade": "A", "price": 100.0}]}, t, d)
            fetch = lambda market, symbols, start, hint=None: ({s: px for s in symbols}, {})
            s = picks_track.update(dt.date(2026, 12, 1), fetch=fetch, bench_fetch=lambda start: {"US": bench, "KOSPI": bench, "KOSDAQ": bench},
                                   picks_dir=d, outcomes_path=t / "o.json", stats_path=t / "s.json")
            a5 = s["groups"]["A"]["5"]
            self.assertEqual(a5["n"], 1)
            self.assertAlmostEqual(a5["meanReturn"], round((106 / 101 - 1) * 100, 3), places=2)
            self.assertEqual(a5["status"], "INSUFFICIENT_SAMPLE")           # 30건 미만이면 표본 부족
            self.assertEqual(s["groups"]["PICK"]["5"]["n"], 1)
            self.assertEqual(s["groups"]["B"]["5"]["n"], 0)

    def test_summarize_counts_only_first_recommendation_per_symbol(self):
        ent = [{"id": f"2026-10-0{i}:us:AAA:PICK", "session": f"2026-10-0{i}", "market": "us", "code": "AAA", "group": "PICK", "grade": "A"} for i in (5, 6, 7)]
        out = {e["id"]: {"5": {"status": "complete", "returnPct": 1.0, "excessPct": 0.5}} for e in ent}
        s = picks_track.summarize(ent, out)
        self.assertEqual(s["groups"]["PICK"]["5"]["recorded"], 1)
        self.assertEqual(s["groups"]["A"]["5"]["n"], 1)


if __name__ == "__main__":
    unittest.main()
