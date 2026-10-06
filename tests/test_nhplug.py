import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from advisory import verify
from agents import config, portfolio as pf
from nhplug import data, kr_extra, probe


def agent(weight, held):
    return {"review": {"capitalWeight": weight},
            "series": {"live": {"open": [{"market": m, "code": c, "name": c, "entryDate": "2026-10-06", "entryPrice": 100.0, "lastPrice": 100.0} for m, c in held],
                                "skipped": []}}}


class ReadOnlyTest(unittest.TestCase):
    def test_orders_and_accounts_are_refused(self):
        for path in ("/krstock/order/v1/cashBuy", "/krstock/inquiry/v1/balance", "/n2/acctinfo", "/gbstock/order/v1/buy"):
            with self.assertRaises(ValueError):
                probe.call("token", path, {})


class FlowTest(unittest.TestCase):
    ROWS = [{"bsop_date1": d, "invest": f, "gigwan": g, "person": 0} for d, f, g in
            (("20261006", 999, 999), ("20261002", -10, -20), ("20261001", -10, -20), ("20260930", -10, -20), ("20260929", -10, -20), ("20260928", -10, -20), ("20260925", 1, 1))]

    def test_today_row_is_dropped_and_sums_cover_five_sessions(self):
        f = kr_extra.summarize_flow(self.ROWS, "20261006")
        self.assertEqual((f["asOf"], f["frgn5"], f["inst5"]), ("20261002", -50, -100))
        self.assertIsNone(f["frgn20"])  # 20일치가 안 차면 합계를 내지 않는다
        self.assertEqual(kr_extra.summarize_flow(self.ROWS)["frgn5"], 999 - 40)  # 날짜를 안 주면 뺄 행이 없다

    def test_load_flow_drops_old_data(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "research/nhplug").mkdir(parents=True)
            (Path(t) / data.FLOW).write_text(json.dumps({"stocks": {"005930": {"asOf": "20261002", "frgn5": 1}, "000660": {"asOf": "20260901", "frgn5": 1}}}))
            got = data.load_flow(Path(t), dt.date(2026, 10, 6))
            self.assertEqual(list(got), ["005930"])
            self.assertEqual(data.load_flow(Path(t) / "none"), {})


class SectorTest(unittest.TestCase):
    def test_group_strips_market_prefix(self):
        self.assertEqual(data.sector_group("코스닥 전기·전자"), "전기·전자")
        self.assertEqual(data.sector_group("코스피 전기·전자"), "전기·전자")
        self.assertIsNone(data.sector_group(None))

    def test_sector_cap_scales_one_sector_only(self):
        held = [("kr", f"00000{i}") for i in range(3)] + [("kr", "000009")]
        sectors = {("kr", f"00000{i}"): "전기·전자" for i in range(3)} | {("kr", "000009"): "금융"}
        rows, summary = pf.risk_review(pf.allocate({"a": agent(1.0, held)}, 8000)[0], 8000, sectors)
        cap = 8000 * config.MAX_SECTOR_WEIGHT
        elec = sum(r["target"] for r in rows if r["sector"] == "전기·전자")
        self.assertAlmostEqual(elec, cap, delta=1)
        self.assertTrue(any("업종" in x for r in rows if r["sector"] == "전기·전자" for x in r["reasons"]))
        self.assertFalse(any("업종" in x for r in rows if r["sector"] == "금융" for x in r["reasons"]))
        self.assertEqual(summary["notChecked"][0], "섹터 쏠림(미국)")

    def test_without_sector_data_nothing_changes(self):
        rows, summary = pf.risk_review(pf.allocate({"a": agent(1.0, [("kr", "000001")])}, 8000)[0], 8000)
        self.assertEqual(summary["notChecked"][0], "섹터 쏠림")
        self.assertIsNone(summary["sectorUnknown"])


class FlowAuditTest(unittest.TestCase):
    def audit(self, flow):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "research/nhplug").mkdir(parents=True)
            if flow is not None:
                (root / data.FLOW).write_text(json.dumps({"stocks": {"005930": dict(flow, asOf=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d"))}}))
            return verify._flow("005930", root)[0]

    def test_both_selling_warns(self):
        self.assertEqual(self.audit({"frgn5": -5, "inst5": -1})["level"], "WARN")

    def test_mixed_or_buying_is_ok(self):
        self.assertEqual(self.audit({"frgn5": -5, "inst5": 3})["level"], "OK")
        self.assertIn("동반 순매수", self.audit({"frgn5": 5, "inst5": 3})["text"])

    def test_missing_data_never_penalizes(self):
        self.assertEqual(self.audit(None)["level"], "OK")
        self.assertEqual(self.audit({"frgn5": None, "inst5": 3})["level"], "OK")


if __name__ == "__main__":
    unittest.main()
