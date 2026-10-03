import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agents import config, review as rv, signals as sg, stats
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


if __name__ == "__main__":
    unittest.main()
