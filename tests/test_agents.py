import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from agents import config, signals as sg, stats
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


if __name__ == "__main__":
    unittest.main()
