"""페르소나 판단 로직(personas/) 합성 테스트 — 네트워크 불필요.

실행:  python -m unittest tests.test_personas -v   (또는 python tests/test_personas.py)

중점:
- 재무/필드가 비어 있어도 예외 없이 "데이터 없음"으로 처리되는가
- 매수·매도 지시 표현이 어떤 문장에도 나오지 않는가(설계 원칙)
- 리스크 참고 비중 계산, 시장폭/청산경고 같은 핵심 규칙의 방향이 맞는가
- 재무 지표(TTM/PER/한국 시총 단위 정규화)가 맞게 계산되는가
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from personas import PERSONAS, build_evidence, normalize_code  # noqa: E402
from personas.fund_metrics import fund_metrics, marcap_krw  # noqa: E402

TODAY = dt.date(2026, 9, 21)
FORBIDDEN = ("매수하세요", "매수 추천", "강력 매수", "매도하세요", "매도 추천", "사세요", "파세요", "BUY", "SELL")


def leader_row(**over):
    row = {
        "code": "TST", "name": "Test Corp", "market": "US", "close": 110.0, "changePct": 0.01,
        "sma50": 100.0, "sma150": 90.0, "sma200": 80.0, "c3": True,
        "highProximity": 0.95, "highTier": "SUPER_LEADER",
        "rsScore": 92.0, "rsChange20d": 12.0, "rsLineHigh": True,
        "regime": "GREEN", "breadth": 0.65, "sizeFactor": 1.0,
        "exitState": "HOLD", "entryState": "TREND_OK", "zone": "READY", "pivotDist": -1.2, "pivotV2": 111.5,
        "contractionCount": 3.0, "contractionWidths": "12.0 | 8.0 | 4.0", "atrContraction": 0.70, "volDryup": 0.60,
        "baseLength": 30.0, "range10": 6.0, "pivotSrc": "swing", "setupReady": True, "setupQuality": 72.0,
        "atr20": 2.0, "avgTradingValue20": 5e8, "initRisk": 6.0, "structStop": 103.4, "swingLow": 104.0,
        "riskFlag": None,
    }
    row.update(over)
    return row


def us_fund(eps=(1.0, 1.0, 1.0, 1.0, 1.0, 1.2), rev_yoy=(10, 15, 20, 30, 35, 40), status="ok", skip_gap=False):
    ends = ["2025-03-31", "2025-06-30", "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"]
    qs = []
    for i, e in enumerate(ends):
        if skip_gap and i == 3:
            continue
        qs.append({
            "periodEnd": e, "period": e, "basis": "US-GAAP", "revenue": 1000.0 + 100 * i,
            "operatingProfit": 200.0 + 20 * i, "operatingMargin": 20.0 + i, "netIncome": 150.0 + 10 * i,
            "eps": eps[i], "operatingCashFlow": 180.0 + 10 * i, "freeCashFlow": 120.0, "liabilities": 500.0,
            "equity": 1000.0, "debtToEquity": 50.0,
            "revenueYoY": {"pct": float(rev_yoy[i]), "label": ""},
            "operatingProfitYoY": {"pct": 30.0, "label": ""},
            "netIncomeYoY": {"pct": 28.0, "label": ""},
            "epsYoY": {"pct": 27.0, "label": ""},
        })
    return {"status": status, "market": "us", "code": "TST", "quarters": qs}


def all_texts(ev):
    for p in ev["personas"]:
        for key in ("supports", "concerns", "checks"):
            for f in p[key]:
                yield f["text"]


class PersonaLogicTests(unittest.TestCase):
    def test_all_personas_present_and_no_crash(self):
        ev = build_evidence(leader_row(), us_fund(), today=TODAY)
        self.assertEqual([p["id"] for p in ev["personas"]], list(PERSONAS))
        self.assertTrue(ev["hasFundamentals"])
        trend = next(p for p in ev["personas"] if p["id"] == "trend")
        ids = {f["id"] for f in trend["supports"]}
        self.assertTrue({"ma_stack", "sma200_rising", "rs_leader", "regime_green"} <= ids)

    def test_missing_fundamentals_become_data_gaps(self):
        ev = build_evidence(leader_row(market="KOSPI"), None, today=TODAY)
        self.assertFalse(ev["hasFundamentals"])
        for pid in ("value", "growth"):
            p = next(x for x in ev["personas"] if x["id"] == pid)
            self.assertEqual(p["supports"], [])
            self.assertTrue(p["dataGaps"])
            self.assertTrue(p["checks"])

    def test_empty_row_does_not_crash(self):
        ev = build_evidence({"code": "X", "name": "X", "market": "US"}, None, today=TODAY)
        self.assertEqual(len(ev["personas"]), len(PERSONAS))
        self.assertTrue(ev["dataGaps"])

    def test_exit_warning_is_severity_3_concern(self):
        ev = build_evidence(leader_row(exitState="TREND_BREAK", exitReason="SMA50 이탈"), None, today=TODAY)
        for pid in ("trend", "risk", "contrarian"):
            p = next(x for x in ev["personas"] if x["id"] == pid)
            hit = [f for f in p["concerns"] if f["id"] == "exit_trend_break"]
            self.assertTrue(hit, pid)
            self.assertEqual(hit[0]["severity"], 3)

    def test_weak_breadth_and_regime_raise_concerns(self):
        ev = build_evidence(leader_row(regime="RED", breadth=0.2, sizeFactor=0.2), None, today=TODAY)
        trend = next(p for p in ev["personas"] if p["id"] == "trend")
        ids = {f["id"] for f in trend["concerns"]}
        self.assertIn("regime_red", ids)
        self.assertIn("breadth_weak", ids)
        risk = next(p for p in ev["personas"] if p["id"] == "risk")
        self.assertIn("size_very_low", {f["id"] for f in risk["concerns"]})

    def test_risk_sizing_math(self):
        ev = build_evidence(leader_row(initRisk=10.0, sizeFactor=0.5), None, today=TODAY)
        risk = next(p for p in ev["personas"] if p["id"] == "risk")
        sizing = next(f for f in risk["checks"] if f["id"] == "risk_sizing")
        self.assertAlmostEqual(sizing["metrics"]["weight"], 10.0)
        self.assertIn("10.0%", sizing["text"])
        self.assertIn("5.0%", sizing["text"])

    def test_extended_zone_is_warning_for_technical_and_risk(self):
        ev = build_evidence(leader_row(zone="EXTENDED", pivotDist=7.2), None, today=TODAY)
        tech = next(p for p in ev["personas"] if p["id"] == "technical")
        self.assertIn("zone_extended", {f["id"] for f in tech["concerns"]})
        risk = next(p for p in ev["personas"] if p["id"] == "risk")
        self.assertIn("r_extended", {f["id"] for f in risk["concerns"]})

    def test_growth_acceleration_and_decel(self):
        ev = build_evidence(leader_row(), us_fund(rev_yoy=(5, 10, 12, 15, 25, 40)), today=TODAY)
        g = next(p for p in ev["personas"] if p["id"] == "growth")
        self.assertIn("g_accel", {f["id"] for f in g["supports"]})
        ev = build_evidence(leader_row(), us_fund(rev_yoy=(50, 45, 40, 30, 20, 10)), today=TODAY)
        g = next(p for p in ev["personas"] if p["id"] == "growth")
        self.assertIn("g_decel", {f["id"] for f in g["concerns"]})
        x = next(p for p in ev["personas"] if p["id"] == "contrarian")
        self.assertIn("x_growth_decel", {f["id"] for f in x["concerns"]})

    def test_base_rate_context_is_surfaced(self):
        def rate(status, excess, dates=8):
            h = {"n": 40, "distinctSignalDates": dates, "pending": 0, "unavailable": 0,
                 "meanReturnPct": excess, "meanExcessPct": excess, "winRate": 0.4}
            return {"group": "TREND", "market": "us", "status": status, "reason": "표본 얕음", "asOf": "2026-09-18",
                    "primary": "20", "horizons": {"20": h}, "unique": 40, "seededCohort": 0}

        def concerns(ctx):
            ev = build_evidence(leader_row(), None, today=TODAY, context=ctx)
            x = next(p for p in ev["personas"] if p["id"] == "contrarian")
            return x, ev

        x, _ = concerns({"baseRates": {"TREND": rate("ok", -3.0)}})
        hit = [f for f in x["concerns"] if f["id"] == "x_base_rate"]
        self.assertTrue(hit)
        self.assertEqual(hit[0]["severity"], 2)
        # 표본이 얕으면(clustered) 성과를 반론으로 쓰지 않고 한계를 데이터 갭으로 남긴다
        x, ev = concerns({"baseRates": {"TREND": rate("clustered", -3.0, dates=1)}})
        self.assertFalse([f for f in x["concerns"] if f["id"] == "x_base_rate"])
        self.assertTrue([f for f in x["checks"] if f["id"] == "x_base_rate"])
        self.assertTrue(any("성과 트래커 표본 한계" in g for g in ev["dataGaps"]))
        # 표본 없음
        x, ev = concerns({"baseRates": {"TREND": {"group": "TREND", "market": "kr", "status": "none",
                                                   "reason": "표본 없음", "primary": None, "horizons": {}}}})
        self.assertTrue(any("성과 트래커" in g for g in ev["dataGaps"]))

    def test_yahoo_per_is_used_and_labeled(self):
        yv = {"trailingPE": 45.0, "forwardPE": 30.0, "priceToBook": 9.0, "sector": "Tech", "industry": "Software",
              "currentPrice": 100.0, "fetchedAt": "2026-09-20T00:00:00+00:00", "stale": False, "lossMaking": False}
        ev = build_evidence(leader_row(market="US"), us_fund(eps=(None,)*6), today=TODAY, valuation=yv)
        v = next(p for p in ev["personas"] if p["id"] == "value")
        ids = {f["id"] for f in v["concerns"] + v["checks"] + v["supports"]}
        self.assertIn("v_fwd_pe", ids)
        texts = " ".join(f["text"] for f in v["concerns"] + v["checks"])
        self.assertIn("Yahoo", texts)
        self.assertIn("애널리스트 추정", texts)

    def test_stale_yahoo_is_flagged(self):
        yv = {"trailingPE": 20.0, "stale": True, "fetchedAt": "2026-09-01T00:00:00+00:00", "error": "x"}
        ev = build_evidence(leader_row(market="US"), us_fund(eps=(None,)*6), today=TODAY, valuation=yv)
        self.assertTrue(any("갱신 실패" in g for g in ev["dataGaps"]))

    def test_no_directive_language(self):
        rows = [leader_row(), leader_row(exitState="STOP", zone="EXTENDED", regime="RED"),
                {"code": "X", "name": "X", "market": "KOSPI"}]
        for r in rows:
            for fund in (None, us_fund()):
                ev = build_evidence(r, fund, today=TODAY)
                for t in all_texts(ev):
                    for bad in FORBIDDEN:
                        self.assertNotIn(bad, t)

    def test_kr_code_is_zero_padded(self):
        self.assertEqual(normalize_code("kr", 500), "000500")
        self.assertEqual(normalize_code("kr", "500"), "000500")
        self.assertEqual(normalize_code("kr", 252990.0), "252990")
        self.assertEqual(normalize_code("us", "AAPL"), "AAPL")
        ev = build_evidence(leader_row(code=500, market="KOSDAQ"), None, today=TODAY)
        self.assertEqual(ev["code"], "000500")

    def test_real_snapshot_does_not_crash_or_use_directives(self):
        """저장소에 실제 스냅샷이 있으면 전 종목에 대해 돌려 본다."""
        for m in ("us", "kr"):
            path = ROOT / "docs" / "data" / f"latest_{m}.json"
            if not path.exists():
                continue
            rows = json.loads(path.read_text(encoding="utf-8"))
            for r in rows[:150]:
                fp = ROOT / "docs" / "data" / "fundamentals" / m / f"{normalize_code(m, r.get('code'))}.json"
                fund = json.loads(fp.read_text(encoding="utf-8")) if fp.exists() else None
                ev = build_evidence(r, fund, today=TODAY)
                for t in all_texts(ev):
                    for bad in FORBIDDEN:
                        self.assertNotIn(bad, t)


class FundMetricsTests(unittest.TestCase):
    def test_ttm_and_us_per(self):
        fm = fund_metrics(us_fund(), "us", close=120.0, today=TODAY)
        self.assertAlmostEqual(fm["ttm"]["eps"], 1.0 + 1.0 + 1.0 + 1.2)
        self.assertAlmostEqual(fm["valuation"]["per"], 120.0 / 4.2)
        self.assertEqual(fm["latestPeriod"], "2026-06-30")
        self.assertFalse(fm["ageDays"] > 150)

    def test_us_per_unavailable_when_eps_missing(self):
        fm = fund_metrics(us_fund(eps=(1.0, 1.0, 1.0, 1.0, None, 1.0)), "us", close=100.0, today=TODAY)  # 결측이 최근 4분기 안
        self.assertNotIn("per", fm["valuation"])
        self.assertTrue(fm["valuation"]["perNote"])

    def test_non_contiguous_quarters_skip_ttm(self):
        fm = fund_metrics(us_fund(skip_gap=True), "us", close=100.0, today=TODAY)
        self.assertEqual(fm["ttm"], {})
        self.assertTrue(fm["notes"])

    def test_kr_marcap_unit_normalization(self):
        self.assertEqual(marcap_krw(10_000), 10_000 * 1e8)       # 억원 단위로 온 값
        self.assertEqual(marcap_krw(1.0e12), 1.0e12)              # 이미 원 단위
        self.assertIsNone(marcap_krw(None))
        qs = []
        for y, q in ((2025, 3), (2025, 4), (2026, 1), (2026, 2)):
            qs.append({"year": y, "quarter": q, "basis": "CFS", "revenue": 1e11, "operatingProfit": 1e10,
                       "netIncome": 2.5e10, "operatingCashFlow": 3e10, "equity": 5e11, "liabilities": 2e11,
                       "revenueYoY": {"pct": 10.0, "label": ""}})
        fund = {"status": "ok", "market": "kr", "code": "000000", "quarters": qs}
        fm = fund_metrics(fund, "kr", marcap=10_000, today=TODAY)          # 시총 1조원, 순이익 TTM 1000억
        self.assertAlmostEqual(fm["valuation"]["per"], 10.0)
        self.assertAlmostEqual(fm["valuation"]["pbr"], 1e12 / 5e11)

    def test_bad_status_or_empty_returns_none(self):
        self.assertIsNone(fund_metrics(None, "us"))
        self.assertIsNone(fund_metrics({"status": "error", "quarters": []}, "us"))
        self.assertIsNone(fund_metrics({"status": "ok", "quarters": []}, "us"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
