import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agents import config, portfolio as pf, report as rp, review as rv, signals as sg, stats, topic as tp
from agents.roster import ROSTER
from agents.simulate import simulate


def series(start, values):
    days = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=[d.date().isoformat() for d in days])


def sig(i, date, code="AAA", market="us", **attrs):
    return {"id": f"s{i}", "market": market, "code": code, "name": code, "date": date, "group": "TREND", "attributes": attrs}


def always(_):
    return 1.0


class SimulateTest(unittest.TestCase):
    def test_enters_next_session_close_and_pays_costs(self):
        px = series("2026-10-05", [100, 100, 110, 110])
        r = simulate(always, [sig(1, "2026-10-05")], {("us", "AAA"): px}, "2026-10-05")
        self.assertEqual(r["open"][0]["entryDate"], "2026-10-06")  # 신호 다음 거래일 종가
        self.assertEqual(r["open"][0]["entryPrice"], 100.0)
        # 포지션 = 자본/6, 진입 비용만큼 줄어든다
        self.assertLess(r["curve"][-1]["equity"], config.INITIAL_CAPITAL * 1.03)

    def test_stop_exits_at_close_and_time_stop(self):
        px = series("2026-10-05", [100, 100, 90, 90, 90])
        r = simulate(always, [sig(1, "2026-10-05")], {("us", "AAA"): px}, "2026-10-05")
        self.assertEqual(r["trades"][0]["reason"], "STOP")
        self.assertLess(r["trades"][0]["returnPct"], -10)  # -10% 종가 + 비용
        flat = series("2026-10-05", [100] * (config.HOLD_SESSIONS + 5))
        r = simulate(always, [sig(1, "2026-10-05")], {("us", "AAA"): flat}, "2026-10-05")
        self.assertEqual(r["trades"][0]["reason"], "TIME")
        self.assertEqual(r["trades"][0]["sessions"], config.HOLD_SESSIONS)

    def test_slot_limit_and_ranking(self):
        n = config.MAX_POSITIONS + 2
        sigs = [sig(i, "2026-10-05", code=f"C{i}") for i in range(n)]
        closes = {("us", f"C{i}"): series("2026-10-05", [100] * 5) for i in range(n)}
        r = simulate(lambda s: float(s["id"][1:]), sigs, closes, "2026-10-05")
        self.assertEqual(len(r["open"]), config.MAX_POSITIONS)
        self.assertEqual({s["reason"] for s in r["skipped"]}, {"NO_SLOT"})
        self.assertNotIn("C0", {p["code"] for p in r["open"]})  # 낮은 점수가 밀린다

    def test_signals_before_start_ignored_and_missing_price_counted(self):
        closes = {("us", "AAA"): series("2026-10-05", [100] * 4)}
        r = simulate(always, [sig(1, "2026-10-02"), sig(2, "2026-10-05", code="ZZZ")], closes, "2026-10-05")
        self.assertEqual(r["open"], [])
        self.assertEqual(r["skipped"][0]["reason"], "NO_PRICE_DATA")

    def test_equity_conserved_without_price_change(self):
        closes = {("kr", "A"): series("2026-10-05", [100] * 5)}
        r = simulate(always, [sig(1, "2026-10-05", code="A", market="kr")], closes, "2026-10-05")
        cost = config.COST_BPS["kr"] / 1e4 * config.INITIAL_CAPITAL / config.MAX_POSITIONS
        self.assertAlmostEqual(r["curve"][-1]["equity"], config.INITIAL_CAPITAL - cost, delta=0.01)


class RosterTest(unittest.TestCase):
    def test_selectors_use_only_signal_time_attributes(self):
        good = sig(1, "2026-10-05", rsRank=95, highProximity=0.98, highTier="SUPER_LEADER", zone="READY", setupScore=7,
                   exitState="HOLD", initRisk=5, marketGate="우호적", riskFlag=None)
        bad = sig(2, "2026-10-05", rsRank=70, highProximity=0.5, highTier="NORMAL", zone="LATE", setupScore=2,
                  exitState="WATCH_EXIT", initRisk=20, marketGate="중립", riskFlag="ENTRY_RISK_TOO_HIGH")
        for aid in ("trend_leader", "setup_ready", "low_risk"):
            self.assertIsNotNone(ROSTER[aid]["select"](good), aid)
            self.assertIsNone(ROSTER[aid]["select"](bad), aid)
        self.assertEqual(ROSTER["control"]["select"](good), ROSTER["control"]["select"](good))  # 결정론적

    def test_missing_attributes_never_select(self):
        empty = sig(3, "2026-10-05")
        for aid in ("trend_leader", "setup_ready", "low_risk"):
            self.assertIsNone(ROSTER[aid]["select"](empty))


class ArchiveAndStatsTest(unittest.TestCase):
    def test_archive_never_shrinks(self):
        with tempfile.TemporaryDirectory() as t:
            t = Path(t)
            (t / "research").mkdir()
            doc = {"signals": [{"id": "x1", "code": "A", "name": "A", "date": "2026-10-05", "group": "TREND",
                                "benchmark": "US", "attributes": {"rsRank": 99, "junk": 1}}]}
            (t / "research" / "us.json").write_text(json.dumps(doc))
            rows = sg.archive("us", t / "state", t / "research")
            self.assertEqual(rows[0]["attributes"].get("rsRank"), 99)
            self.assertNotIn("junk", rows[0]["attributes"])
            (t / "research" / "us.json").write_text(json.dumps({"signals": []}))
            self.assertEqual(len(sg.archive("us", t / "state", t / "research")), 1)

    def test_summary_flags_small_sample(self):
        px = series("2026-10-05", [100] * 4)
        r = simulate(always, [sig(1, "2026-10-05")], {("us", "AAA"): px}, "2026-10-05")
        s = stats.summarize(r, {"US": px})
        self.assertEqual(s["status"], "INSUFFICIENT_SAMPLE")
        self.assertEqual(stats.max_drawdown([100, 120, 90]), -25.0)


def trades(n, ret, day0="2026-10-05", per_day=1):
    days = pd.bdate_range(day0, periods=max(1, n // per_day + 1))
    return [{"entryDate": days[i // per_day].date().isoformat(), "returnPct": ret(i)} for i in range(n)]


def summ(closed, mdd=-3.0):
    return {"closed": closed, "maxDrawdownPct": mdd}


class ReviewTest(unittest.TestCase):
    def test_small_sample_never_demotes_on_performance(self):
        st, ev = rv.review(None, summ(10), trades(10, lambda i: -5), trades(40, lambda i: 1), "2026-11-01")
        self.assertEqual(st["status"], "ACTIVE")
        self.assertIsNone(ev["vsControl"])

    def test_risk_rule_applies_regardless_of_sample(self):
        st, _ = rv.review(None, summ(3, -16), [], [], "2026-11-01")
        self.assertEqual(st["status"], "PROBATION")
        st, _ = rv.review(st, summ(3, -26), [], [], "2026-11-02")
        self.assertEqual(st["status"], "RETIRED")
        self.assertEqual([h["to"] for h in st["history"]], ["PROBATION", "RETIRED"])

    def test_underperforming_agent_goes_to_probation_then_retired(self):
        ctl = trades(40, lambda i: 1 + (i % 5) * 0.2, per_day=2)
        bad = trades(60, lambda i: -4 + (i % 5) * 0.2, per_day=2)
        st, ev = rv.review(None, summ(30), bad[:30], ctl, "2026-11-01")
        self.assertEqual(st["status"], "PROBATION")
        self.assertLess(ev["vsControl"]["diff"], 0)
        st, ev = rv.review(st, summ(60), bad, ctl, "2026-12-01")
        self.assertEqual(st["status"], "RETIRED")  # 구간 상한이 0 미만

    def test_probation_recovers_and_retired_is_terminal(self):
        ctl = trades(40, lambda i: 0.0 + (i % 5) * 0.2, per_day=2)
        good = trades(70, lambda i: 3 + (i % 5) * 0.2, per_day=2)
        prev = {"status": "PROBATION", "since": "2026-11-01", "closedAtChange": 30, "history": []}
        st, _ = rv.review(prev, summ(65), good, ctl, "2026-12-15")
        self.assertEqual(st["status"], "ACTIVE")
        dead = {"status": "RETIRED", "since": "2026-11-01", "closedAtChange": 30, "history": []}
        st, _ = rv.review(dead, summ(200), good, ctl, "2027-01-01")
        self.assertEqual(st["status"], "RETIRED")

    def test_bootstrap_is_deterministic(self):
        a, b = trades(40, lambda i: i % 7, per_day=2), trades(40, lambda i: i % 5, per_day=2)
        self.assertEqual(stats.cluster_bootstrap_diff(a, b), stats.cluster_bootstrap_diff(a, b))


def agent(weight, held=(), pending=()):
    return {"review": {"capitalWeight": weight},
            "series": {"live": {"open": [{"market": m, "code": c, "name": c, "entryDate": "2026-10-06", "entryPrice": 100.0, "lastPrice": 101.0}
                                         for m, c in held],
                                "skipped": [{"id": f"sepa:{m}:{c}", "code": c, "name": c, "signalDate": "2026-10-09",
                                             "reason": "NO_ENTRY_BAR_YET", "score": s} for m, c, s in pending]}}}


class PortfolioTest(unittest.TestCase):
    def test_budget_follows_weights_and_skips_retired_and_control(self):
        agents = {"a": agent(1.0), "b": agent(0.5), "c": agent(0.0), "control": {"review": {"capitalWeight": None}, "series": {"live": {"open": [], "skipped": []}}}}
        _, budgets = pf.allocate(agents, 9000)
        self.assertEqual(budgets, {"a": 6000, "b": 3000})

    def test_consensus_adds_up_and_caps_single_name(self):
        agents = {k: agent(1.0, held=[("us", "NVDA")]) for k in "abc"}
        rows, _ = pf.allocate(agents, 8000)
        self.assertAlmostEqual(rows[0]["amount"], 8000 / 6, places=2)  # 세 에이전트가 같은 종목 → 자본의 1/6
        out, _ = pf.risk_review(rows, 8000)
        self.assertEqual(out[0]["verdict"], "TRIMMED")
        self.assertEqual(out[0]["target"], round(8000 * config.MAX_SINGLE_WEIGHT, 2))

    def test_pending_limited_to_free_slots_best_score_first(self):
        pend = [("us", f"P{i}", float(i)) for i in range(10)]
        rows, _ = pf.allocate({"a": agent(1.0, held=[("us", f"H{i}") for i in range(4)], pending=pend)}, 6000)
        pcodes = {r["code"] for r in rows if r["state"] == "PENDING"}
        self.assertEqual(pcodes, {"P9", "P8"})  # 6칸 중 4칸 보유 → 2칸만, 점수 높은 순

    def test_name_cap_drops_smallest_lines(self):
        agents = {"a": agent(1.0, held=[("kr", f"K{i}") for i in range(6)]),
                  "b": agent(1.0, held=[("kr", f"L{i}") for i in range(6)]),
                  "c": agent(1.0, held=[("kr", f"M{i}") for i in range(6)])}
        out, summary = pf.risk_review(pf.allocate(agents, 8000)[0], 8000)
        self.assertEqual(summary["names"], config.MAX_NAMES)
        self.assertEqual(sum(r["verdict"] == "REJECTED" for r in out), 18 - config.MAX_NAMES)

    def test_market_cap_scales_down_one_market(self):
        out, summary = pf.risk_review(pf.allocate({"a": agent(1.0, held=[("kr", f"K{i}") for i in range(6)])}, 8000)[0], 8000)
        self.assertAlmostEqual(summary["kr"], 100 * config.MAX_MARKET_WEIGHT, delta=0.2)  # 한 종목 상한 후에도 90% → 70%로
        self.assertEqual(summary["us"], 0)
        self.assertTrue(all(r["verdict"] == "TRIMMED" for r in out))

    def test_all_retired_gives_empty_portfolio_with_warning(self):
        res = pf.build({"a": agent(0.0, held=[("us", "X")])}, 8000)
        self.assertEqual(res["lines"], [])
        self.assertTrue(res["warnings"])


class ReportTest(unittest.TestCase):
    def test_period_keys(self):
        import datetime as dt
        fri = rp.period_keys(dt.date(2026, 10, 9))      # 금요일, 월말 아님
        self.assertEqual((fri["weekly"], fri["monthly"]), ("2026-W41", None))
        self.assertIsNone(rp.period_keys(dt.date(2026, 10, 8))["weekly"])
        self.assertEqual(rp.period_keys(dt.date(2026, 10, 30))["monthly"], "2026-10")   # 금요일이자 월말
        self.assertEqual(rp.period_keys(dt.date(2026, 9, 30))["monthly"], "2026-09")    # 수요일 월말
        self.assertIsNone(rp.period_keys(dt.date(2026, 10, 1))["monthly"])
        self.assertEqual(rp.period_keys(dt.date(2026, 10, 30))["weekly"], "2026-W44")

    def test_save_is_write_once_and_indexed(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            rep = {"kind": "daily", "key": "2026-10-06", "date": "2026-10-06", "title": "일간 보고서 2026-10-06", "dataAsOf": "x",
                   "disclaimer": "d", "sections": [{"heading": "h", "bullets": ["a"]}, {"heading": "t", "table": {"columns": ["c"], "rows": [[1]]}}]}
            self.assertTrue(rp.save(rep, d))
            rep2 = dict(rep, sections=[])
            self.assertFalse(rp.save(rep2, d))
            self.assertEqual(json.loads((d / "daily" / "2026-10-06.json").read_text())["sections"][0]["heading"], "h")
            self.assertEqual(len(json.loads((d / "index.json").read_text())["reports"]), 1)
            self.assertIn("| c |", (d / "daily" / "2026-10-06.md").read_text())


class SessionDateTest(unittest.TestCase):
    def test_session_is_the_latest_weekday_whose_schedule_time_has_passed(self):
        import datetime as dt
        u = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc)
        d = dt.date
        self.assertEqual(config.session_date(u("2026-10-06T01:30")), d(2026, 10, 5))   # 월 22:40 예약이 화 01:30에 시작 → 월요일
        self.assertEqual(config.session_date(u("2026-10-05T22:50")), d(2026, 10, 5))   # 지연 없이 월 밤에 시작
        self.assertEqual(config.session_date(u("2026-10-05T07:43")), d(2026, 10, 2))   # 월요일 아침 수동·푸시 실행 → 아직 금요일 세션
        self.assertEqual(config.session_date(u("2026-10-05T22:39")), d(2026, 10, 2))   # 예약 시각 직전
        fri = config.session_date(u("2026-10-10T01:30"))                                # 금 예약이 토 01:30에 시작
        self.assertEqual(fri, d(2026, 10, 9))
        self.assertEqual(rp.period_keys(fri)["weekly"], "2026-W41")                     # 주간 보고서가 만들어진다
        self.assertEqual(config.session_date(u("2026-10-11T12:00")), d(2026, 10, 9))   # 일요일 → 금요일
        self.assertEqual(config.session_date(u("2026-10-12T03:00")), d(2026, 10, 9))   # 월 03:00 UTC는 아직 금요일 세션


class ReportMarketSplitTest(unittest.TestCase):
    def test_korea_is_reported_as_kospi_and_kosdaq(self):
        def r(market, regime, breadth, passed=False):
            return {"market": market, "status": "OK", "regime": regime, "breadth": breadth, "sizeFactor": 0.5, "passAll": passed}
        rows = {"kr": [r("KOSDAQ", "RED", 0.87)] * 3 + [r("KOSPI", "YELLOW", 0.57, True)] * 2, "us": [r("US", "GREEN", 0.26)]}
        lines = rp._section_market(rows)["bullets"]
        self.assertEqual(len(lines), 3)
        self.assertIn("KOSPI", lines[0]); self.assertIn("국면 YELLOW", lines[0]); self.assertIn("breadth 57%", lines[0]); self.assertIn("추세 통과 2", lines[0])
        self.assertIn("KOSDAQ", lines[1]); self.assertIn("국면 RED", lines[1]); self.assertIn("breadth 87%", lines[1])
        self.assertTrue(lines[2].startswith("US"))
        self.assertFalse(any(l.startswith("KR") for l in lines))


class ReportNotifyListTest(unittest.TestCase):
    def test_new_list_has_only_newly_written_reports(self):
        import datetime as dt
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            real_build, real_dir = rp.build, rp.REPORT_DIR
            rp.REPORT_DIR = d / "data"
            rp.build = lambda kind, today, **kw: {"kind": kind, "key": today.isoformat(), "date": today.isoformat(), "title": "t", "dataAsOf": "x",
                                                  "disclaimer": "d", "sections": [{"heading": "h", "bullets": ["a"]}]}
            try:
                lst = d / "new.txt"
                rp.run(dt.date(2026, 10, 6), None, lst)
                self.assertEqual(len(lst.read_text().splitlines()), 1)
                self.assertTrue(lst.read_text().strip().endswith("daily/2026-10-06.md"))
                rp.run(dt.date(2026, 10, 6), None, lst)   # 같은 날 재실행 → 새 보고서 없음 → 알림 대상 없음
                self.assertEqual(lst.read_text(), "")
            finally:
                rp.build, rp.REPORT_DIR = real_build, real_dir


class TopicTest(unittest.TestCase):
    def setUp(self):
        self.uni = {"root": Path("/nonexistent"),
                    "rows": {("us", "NVDA"): {"name": "Nvidia"}, ("us", "VRT"): {"name": "Vertiv"}, ("kr", "005930"): {"name": "삼성전자"}},
                    "meta": {"VRT": {"AI_ValueChain": "전력·냉각", "AI_Subsector": "Cooling"}},
                    "valuation": {"NVDA": {"sector": "Technology", "industry": "Semiconductors"}}}

    def test_resolve_by_code_name_theme_and_sector(self):
        names = lambda q: {(t["market"], t["code"]) for t in tp.resolve(q, self.uni)}
        self.assertEqual(names("nvda"), {("us", "NVDA")})
        self.assertEqual(names("삼성"), {("kr", "005930")})
        self.assertEqual(names("전력"), {("us", "VRT")})
        self.assertEqual(names("semiconductors"), {("us", "NVDA")})
        self.assertEqual(names("NVDA, 삼성전자"), {("us", "NVDA"), ("kr", "005930")})
        self.assertEqual(names("없는주제"), set())
        e = tp.resolve("전력", self.uni)[0]
        self.assertTrue(e["matchedBy"][0].startswith("테마"))

    def test_limit_applies(self):
        self.assertEqual(len(tp.resolve("NVDA, VRT, 삼성전자", self.uni, limit=2)), 2)

    def test_committee_uses_agent_rules(self):
        good = {"rsRank": 95, "highProximity": 0.98, "highTier": "SUPER_LEADER", "zone": "WATCH", "initRisk": 20}
        self.assertEqual(tp._committee(good, "us", "X"), ["추세 리더"])


if __name__ == "__main__":
    unittest.main()
