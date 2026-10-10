import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from daily_picks import config as C, main as dp, select
from daily_picks.entry import entry_metrics
from ledger import adapters
from ledger.store import read_gz


def frame(n=120, drift=0.0, noise=0.5, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(drift, noise, n))
    return pd.DataFrame({"High": close + 1, "Low": close - 1, "Close": close})


class EntryMetricsTest(unittest.TestCase):
    def test_risk_matches_definition_and_short_history_is_unknown(self):
        m = entry_metrics(frame())
        self.assertGreater(m["riskPct"], 0)
        self.assertAlmostEqual(m["riskPct"], (m["close"] - m["stop"]) / m["close"] * 100, places=1)
        self.assertIsNone(entry_metrics(frame(30)))               # 이력이 모자라면 0으로 채우지 않고 모른다고 한다

    def test_parabolic_rise_is_chase(self):
        up = frame(120, drift=1.5, noise=0.2)
        self.assertTrue(entry_metrics(up)["chase"])


def pool(rows):
    """rows: code -> dict(sepa=, tech=, aggr=, strategies=, rank=)"""
    return {"sepaRows": {c: r["sepa"] for c, r in rows.items() if "sepa" in r},
            "techRows": {c: r["tech"] for c, r in rows.items() if "tech" in r},
            "aggrRows": {c: r["aggr"] for c, r in rows.items() if "aggr" in r},
            "funnel": {c: {"rank": r["rank"], "name": c, "price": 100.0} for c, r in rows.items() if "rank" in r},
            "selected": {c: r["strategies"] for c, r in rows.items()}, "regime": "GREEN", "gate": "우호적", "asOf": "2026-10-09"}


class SelectTest(unittest.TestCase):
    def test_rejections_and_ranking(self):
        p = pool({
            "A": {"sepa": {"initRisk": 3.0}, "strategies": {"sepa": ["TREND"], "funnel": 5, "multifactor": ["BUY"]}, "rank": 5},   # 점수 5
            "B": {"sepa": {"initRisk": 3.0}, "strategies": {"funnel": 1}, "rank": 1},                                              # 점수 2
            "C": {"sepa": {"initRisk": 9.0}, "strategies": {"sepa": ["TREND"]}},                                                  # 손절폭 초과
            "D": {"sepa": {"initRisk": 3.0, "entryVerdict": "NO-GO"}, "strategies": {"sepa": ["TREND"]}},
            "E": {"sepa": {"initRisk": 3.0}, "tech": {"chaseWarning": True}, "strategies": {"sepa": ["TREND"]}},
            "F": {"strategies": {"funnel": 9}, "rank": 9},                                                                         # 손절폭 미확인
            "G": {"sepa": {"initRisk": 2.0}, "aggr": {"liquidityOk": False}, "strategies": {"funnel": 7}, "rank": 7},
            "H": {"sepa": {"initRisk": 2.0}, "strategies": {"multifactor": ["BUY"]}},                                              # 연구 전략만 → 풀 밖
        })
        out = select.pick("kr", p)
        self.assertEqual([r["code"] for r in out["picks"]], ["A", "B"])
        self.assertEqual(out["candidates"], 7)                                  # H 제외
        self.assertEqual(out["rejectSummary"], {"손절폭 8% 초과": 1, "SEPA 진입 NO-GO": 1, "추격(과열)": 1, "손절폭 미확인": 1, "유동성 부족": 1})
        self.assertIn("2개뿐", out["shortfall"])                                 # 3개를 못 채우면 억지로 채우지 않는다

    def test_risk_source_order_and_zero_risk_rejected(self):
        p = pool({"A": {"sepa": {"initRisk": 0.0}, "strategies": {"sepa": ["TREND"]}},
                  "B": {"aggr": {"initialRiskPct": 4.0}, "tech": {"riskPct": 6.0}, "strategies": {"funnel": 1}, "rank": 1},
                  "C": {"tech": {"riskPct": 6.0}, "strategies": {"funnel": 2}, "rank": 2}})
        self.assertEqual(select.entry_view("B", p)["riskSource"], "계좌복구")
        self.assertEqual(select.entry_view("C", p)["riskSource"], "기술적")
        self.assertEqual(select.reject_reason(select.entry_view("A", p)), "손절폭 0 이하(계산 불가)")
        direct = {"X": {"riskPct": 5.0, "chase": False}}
        p2 = pool({"X": {"strategies": {"funnel": 3}, "rank": 3}})
        self.assertTrue(select.needs_direct("X", p2))
        self.assertEqual(select.entry_view("X", p2, direct["X"])["riskSource"], "직접 계산")


class RecordTest(unittest.TestCase):
    def rec(self):
        return {"session": "2026-10-09", "market": "us", "regime": "GREEN", "candidates": 5, "suitable": 2,
                "picks": [{"code": "A", "name": "에이", "market": "US", "price": 10.0, "score": 4, "entry": {"riskPct": 3.0}},
                          {"code": "B", "name": "비", "market": "US", "price": 20.0, "score": 2, "entry": {"riskPct": 4.0}}],
                "poolPrices": {"A": 10.0, "B": 20.0, "C": 30.0, "D": 40.0}, "rejectSummary": {}}

    def test_write_once_and_ledger_control_excludes_picks(self):
        now = dt.datetime(2026, 10, 10, 1, 0, tzinfo=dt.timezone.utc)
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.assertTrue(dp.write_once(root, "us", self.rec(), now))
            self.assertFalse(dp.write_once(root, "us", dict(self.rec(), picks=[]), now))        # 두 번째는 쓰지 않는다
            self.assertEqual(len(json.loads(dp.record_path(root, "us", "2026-10-09").read_text())["picks"]), 2)
            self.assertFalse(dp.write_once(root, "us", dict(self.rec(), session=None), now))
            sig = root / "signals"
            self.assertEqual(adapters.ingest_picks("us", root / "research" / "daily_picks", sig), 1)
            self.assertEqual(adapters.ingest_picks("us", root / "research" / "daily_picks", sig), 0)
            doc = read_gz(sig / "picks" / "us" / "2026-10-09.json.gz")
            by = {r["symbol"]: r["groups"] for r in doc["rows"]}
            self.assertEqual(by["A"], ["PICK"])
            self.assertEqual(by["C"], ["CONTROL"])
            self.assertEqual(doc["effectiveDate"], "2026-10-09")

    def test_markdown_lists_picks_and_shortfall(self):
        r = dict(self.rec(), shortfall="진입 적합 종목이 2개뿐입니다", rejectSummary={"추격(과열)": 3})
        r["picks"][0]["strategies"] = {"sepa": ["TREND"]}
        r["picks"][1]["strategies"] = {"funnel": 1}
        for p in r["picks"]:
            p["entry"]["riskSource"] = "SEPA"
        md = dp.render_md({"us": r})
        self.assertIn("에이", md)
        self.assertIn("2개뿐", md)
        self.assertIn("추격(과열) 3", md)


if __name__ == "__main__":
    unittest.main()
