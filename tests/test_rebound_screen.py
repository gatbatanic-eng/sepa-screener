import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import rebound_screen
from rebound_screen import CONFIG, evaluate


def frame(vol_scale=0.07, drawdown=True, n=300, seed=3):
    """무작위 고변동 시계열. drawdown=True 면 마지막이 52주 고점 대비 크게 낮다."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-06-01", periods=n)
    r = rng.normal(0, vol_scale, n)
    if drawdown:
        r[: n // 2] = np.abs(r[: n // 2]) * 0.6 + 0.01      # 상승 후
        r[n // 2: n // 2 + 60] = -np.abs(r[n // 2: n // 2 + 60]) * 0.8 - 0.01  # 급락
        r[n // 2 + 60:] = rng.normal(0, vol_scale, n - n // 2 - 60)
    close = 1000 * np.cumprod(1 + r)
    return pd.DataFrame({"Open": close, "High": close * 1.02, "Low": close * 0.98, "Close": close,
                         "Volume": np.full(n, 500_000.0)}, index=dates)


def row(**kw):
    base = {"code": "000001", "name": "T", "market": "KOSDAQ", "status": "OK", "inUniverse": True,
            "close": 1000, "marcap": 150_000_000_000}
    base.update(kw)
    return base


class ReboundScreenTests(unittest.TestCase):
    def test_small_volatile_beaten_down_is_watch(self):
        out = evaluate(row(), frame(), "kr")
        self.assertEqual(out["status"], "OK")
        self.assertGreaterEqual(out["vol60"], CONFIG["vol60Min"])
        self.assertLessEqual(out["dd52Pct"], CONFIG["drawdown52wMax"] * 100)
        self.assertTrue(out["reboundWatch"])
        for key in ("marcapEok", "offLow25Pct", "volumeRatio", "rsi14", "ret20Pct", "ret60Pct", "value20Eok"):
            self.assertIn(key, out)

    def test_each_condition_can_exclude(self):
        self.assertFalse(evaluate(row(marcap=900_000_000_000), frame(), "kr")["reboundWatch"])          # 시총 큼
        self.assertFalse(evaluate(row(), frame(vol_scale=0.01, drawdown=False), "kr")["reboundWatch"])  # 변동성·낙폭 부족

    def test_outside_default_universe_is_not_watch(self):
        out = evaluate(row(inUniverse=False), frame(), "kr")
        self.assertEqual(out["status"], "OK")
        self.assertFalse(out["reboundWatch"])

    def test_marcap_in_eok_is_normalised(self):
        out = evaluate(row(marcap=1500), frame(), "kr")      # 1,500억(억원 단위 표기)
        self.assertTrue(out["reboundWatch"])
        self.assertAlmostEqual(out["marcapEok"], 1500)

    def test_unknown_is_none_not_false(self):
        self.assertIsNone(evaluate(row(marcap=None), frame(), "kr")["reboundWatch"])
        self.assertIsNone(evaluate(row(), None, "kr")["reboundWatch"])
        self.assertIsNone(evaluate(row(status="확인불가"), frame(), "kr")["reboundWatch"])
        self.assertIsNone(evaluate(row(), frame(n=60, drawdown=False), "kr")["reboundWatch"])
        self.assertIsNone(evaluate(row(), frame(), "us")["reboundWatch"])

    def test_export_only_for_korea_and_writes_group(self):
        payload = {"market": "kr", "rows": [row()], "strategy": {"config": {"universe": {"kr_mode": "liquidity"}}}}
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "output"
            rebound_screen.export_rebound(payload, {"000001": frame()}, out_dir=out)
            rebound_screen.export_rebound(dict(payload, market="us"), {}, out_dir=out)
            written = list(out.glob("*.json"))
            saved = json.loads(written[0].read_text(encoding="utf-8"))
        self.assertEqual([p.name for p in written], ["research_input_rebound_kr.json"])
        self.assertEqual(saved["groups"], ["REB_WATCH"])
        self.assertEqual(saved["horizons"], [5, 20, 60])
        self.assertTrue(saved["rows"][0]["reboundWatch"])
        self.assertEqual(saved["strategy"]["config"], CONFIG)

    def test_tracker_membership_and_stale_quarantine(self):
        import research_tracker as rt
        self.assertTrue(rt.membership({"status": "OK", "inUniverse": True, "reboundWatch": True}, "REB_WATCH"))
        self.assertFalse(rt.membership({"status": "OK", "inUniverse": True, "reboundWatch": False}, "REB_WATCH"))
        self.assertIsNone(rt.membership({"status": "OK", "inUniverse": True, "reboundWatch": None}, "REB_WATCH"))
        rows = [{"code": "A", "market": "KOSDAQ", "status": "OK", "reboundWatch": True, "priceAsOf": "2026-10-01"}]
        out = rt.align_latest_rows(rows, {"KOSDAQ": "2026-10-02"}, "kr")
        self.assertIsNone(out[0]["reboundWatch"])
        self.assertEqual(out[0]["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
