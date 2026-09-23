import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from personas import build_evidence
from personas.review import build_review, generate_reviews, six_lenses, stock_id


class PersonaReviewTests(unittest.TestCase):
    def row(self, code="A", market="US"):
        return {"code": code, "name": "Example", "market": market, "status": "OK",
                "close": 101, "sma50": 98, "sma150": 95, "sma200": 90,
                "rsScore": 95, "regime": "GREEN", "sizeFactor": .8,
                "initRisk": 4, "structStop": 97, "trendOk": False}

    def test_six_lenses_preserve_evidence_without_mutating_engine_output(self):
        evidence = build_evidence(self.row())
        original = copy.deepcopy(evidence)
        cards = six_lenses(evidence)
        self.assertEqual(len(cards), 6)
        self.assertEqual([c["id"] for c in cards], ["trend", "growth", "value", "quant", "risk", "contrarian"])
        self.assertEqual(evidence, original)
        trend_checks = {f["id"] for f in cards[0]["checks"]}
        self.assertTrue({f["id"] for f in evidence["personas"][1]["checks"]}.issubset(trend_checks))
        risk = next(c for c in cards if c["id"] == "risk")
        self.assertNotIn("risk_sizing", [f["id"] for f in risk["checks"]])
        self.assertTrue(any("계좌 잔액 미연결" in s for s in risk["dataGaps"]))
        growth = next(c for c in cards if c["id"] == "growth")
        self.assertEqual(growth["state"], "판단 유보")

    def test_no_new_trade_decision_and_distinct_data_dates(self):
        recovery = {"entryPass": False, "reason": "강도 미달", "asOf": "2026-09-23"}
        r = build_review(self.row(), None, {}, {"fetchedAt": "2026-09-20", "stale": True},
                         as_of="2026-09-22", today=dt.date(2026, 9, 24), recovery=recovery)
        self.assertEqual(r["asOf"], "2026-09-22")
        self.assertEqual(r["recovery"], recovery)
        self.assertTrue(r["valuationStale"])
        self.assertNotIn("buy", r)
        self.assertTrue(all("horizon" in c for c in r["cards"]))

    def test_safe_ids_and_market_disambiguation(self):
        self.assertEqual(stock_id("KR", 5930), "kr-005930")
        self.assertEqual(stock_id("US", "brk-b"), "us-BRK-B")
        for code in ("../secret", "<script>", "", None, "a/b"):
            self.assertIsNone(stock_id("us", code))
        self.assertIsNone(stock_id("xx", "A"))

    def test_builder_includes_nontrend_selection_and_reuses_unchanged_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "docs/data"
            data.mkdir(parents=True)
            rows = [self.row("A"), self.row("B"), {**self.row("BAD"), "status": "ERROR"}]
            (data / "latest_us.json").write_text(json.dumps(rows), encoding="utf-8")
            (data / "latest_kr.json").write_text(json.dumps([self.row(5930, "KR")]), encoding="utf-8")
            (data / "history_us.json").write_text('[{"date":"2026-09-22"}]', encoding="utf-8")
            snapshot = {"entries": [{"marketBucket": "US", "code": "B", "entryPass": True,
                                     "executionPriority": 1, "entryReason": "통과"}],
                        "strongest": [{"marketBucket": "US", "code": c} for c in ("A", "B", "MISSING")],
                        "sessions": {"kr": None, "us": "2026-09-22"}}
            stats = generate_reviews(snapshot, root, today=dt.date(2026, 9, 24))
            self.assertEqual(stats["stocks"], 3)
            self.assertEqual(stats["llmCalls"], 0)
            folder = root / "docs/recovery/personas"
            catalog = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))
            self.assertEqual(catalog["automatic"], ["us-B", "us-A"])
            self.assertEqual(len(catalog["dataGaps"]), 1)
            self.assertNotIn("holdings", catalog)
            self.assertEqual(catalog["limits"], {"automatic": 5, "holdings": 3, "manual": 2})
            before = (folder / "us-A.json").stat().st_mtime_ns
            second = generate_reviews(snapshot, root, today=dt.date(2026, 9, 24))
            self.assertEqual(second["changed"], 0)
            self.assertEqual((folder / "us-A.json").stat().st_mtime_ns, before)
            rev = next(s["revision"] for s in catalog["stocks"] if s["id"] == "us-A")
            rows[0]["close"] = 140
            (data / "latest_us.json").write_text(json.dumps(rows[:1]), encoding="utf-8")
            generate_reviews(snapshot, root, today=dt.date(2026, 9, 24))
            self.assertFalse((folder / "us-B.json").exists())
            updated = json.loads((folder / "catalog.json").read_text(encoding="utf-8"))
            self.assertNotEqual(next(s["revision"] for s in updated["stocks"] if s["id"] == "us-A"), rev)

    def test_empty_data_builds_usable_empty_page(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            generate_reviews({"entries": [], "strongest": [], "sessions": {}}, root)
            out = root / "docs/recovery/personas"
            self.assertEqual(json.loads((out / "catalog.json").read_text())["stocks"], [])
            self.assertTrue((out / "index.html").exists())


if __name__ == "__main__":
    unittest.main()
