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


class V2Test(unittest.TestCase):
    def sepa_rows(self):
        rows = {}
        for i in range(6):   # 강한 업종 A: 모두 50일선 위, RS 높음
            rows[f"A{i}"] = {"status": "OK", "close": 110, "sma50": 100, "rsRank": 90, "market": "KOSPI", "regime": "YELLOW"}
        for i in range(6):   # 중간 B
            rows[f"B{i}"] = {"status": "OK", "close": 110 if i < 3 else 90, "sma50": 100, "rsRank": 50, "market": "KOSPI", "regime": "YELLOW"}
        for i in range(6):   # 약한 C
            rows[f"C{i}"] = {"status": "OK", "close": 90, "sma50": 100, "rsRank": 10, "market": "KOSDAQ", "regime": "RED"}
        return rows

    def test_sector_strength_thirds_and_min_n(self):
        from daily_picks import context as X
        rows = self.sepa_rows()
        sectors = {c: c[0] for c in rows}
        rows["D0"] = {"status": "OK", "close": 200, "sma50": 100, "rsRank": 99, "market": "KOSPI", "regime": "YELLOW"}
        sectors["D0"] = "D"                                   # 종목 1개뿐인 업종은 강도를 계산하지 않는다
        st = X.sector_strength(rows, sectors)
        self.assertEqual({k: v["adj"] for k, v in st.items()}, {"A": 1, "B": 0, "C": -1})
        self.assertNotIn("D", st)
        self.assertEqual(X.market_regimes(rows), {"KOSPI": "YELLOW", "KOSDAQ": "RED"})

    def test_v2_rank_adds_sector_and_red_market_and_explains(self):
        from daily_picks import context as X, v2
        rows = self.sepa_rows()
        sectors = {c: c[0] for c in rows}
        sectors.update({"X": "A", "Y": "C", "Z": None})
        ctx = {"sectorStats": X.sector_strength(rows, sectors), "regimes": X.market_regimes(rows), "macro": {"label": "중립", "vix": 15.4, "dxy": 102.1, "usdkrw": 1343, "us10y": 5.28}, "krClose": {}}
        def row(code, score, market):
            return {"code": code, "name": code, "market": market, "price": 100.0, "score": score, "strategies": {"funnel": 3}, "funnelRank": 3,
                    "entry": {"riskPct": 3.0, "riskSource": "SEPA"}, "reject": None}
        out = v2.rank([row("X", 2, "KOSPI"), row("Y", 3, "KOSDAQ"), row("Z", 2, "KOSPI")], ctx, sectors, "kr")
        by = {p["code"]: p["v2"]["score"] for p in out["picks"]}
        self.assertEqual(by, {"X": 3, "Y": 1, "Z": 2})        # 강세 업종 +1, 약세 업종 -1과 코스닥 RED -1, 업종 미상 0
        self.assertEqual([p["code"] for p in out["picks"]], ["X", "Z", "Y"])
        kinds = [w["kind"] for w in out["picks"][0]["why"]]
        self.assertIn("sector", kinds)
        self.assertIn("macro", kinds)
        self.assertTrue(any("업종을 알 수 없어" in w["text"] for w in out["picks"][1]["why"]))

    def test_us_regime_is_single_and_us_sector_cache_is_incremental(self):
        from daily_picks import context as X, v2
        self.assertEqual(v2.regime_of({"regimes": {"US": "GREEN"}}, "NYSE", "us"), "GREEN")
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            calls = []
            def fetch(sym):
                calls.append(sym)
                return None if sym == "BAD" else "Energy"
            today = dt.date(2026, 10, 10)
            self.assertEqual(X.fetch_us_sectors(["CVX", "BAD"], root, today, fetch), 1)
            self.assertEqual(X.fetch_us_sectors(["CVX", "BAD", "XOM"], root, today, fetch), 1)   # 이미 받은 종목·못 받은 종목(7일 내)은 다시 받지 않는다
            self.assertEqual(calls, ["CVX", "BAD", "XOM"])
            self.assertEqual(X.fetch_us_sectors(["BAD"], root, today + dt.timedelta(days=8), fetch), 0)   # 7일 뒤 재시도(또 못 받음)
            self.assertEqual(calls[-1], "BAD")
            self.assertEqual(X.sector_map("us", root), {"CVX": "Energy", "XOM": "Energy"})

    def test_ledger_groups_for_v1_and_v2(self):
        now = dt.datetime(2026, 10, 10, 1, 0, tzinfo=dt.timezone.utc)
        rec = {"session": "2026-10-09", "market": "us", "picks": [{"code": "A", "name": "a", "market": "US", "price": 10.0, "score": 4, "entry": {"riskPct": 3}}],
               "picksV2": [{"code": "A", "name": "a", "market": "US", "price": 10.0, "score": 4, "v2": {"score": 5}, "entry": {"riskPct": 3}},
                           {"code": "B", "name": "b", "market": "US", "price": 20.0, "score": 3, "v2": {"score": 4}, "entry": {"riskPct": 3}}],
               "poolPrices": {"A": 10.0, "B": 20.0, "C": 30.0}, "rejectSummary": {}}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dp.write_once(root, "us", rec, now)
            adapters.ingest_picks("us", root / "research" / "daily_picks", root / "signals")
            doc = read_gz(root / "signals" / "picks" / "us" / "2026-10-09.json.gz")
            by = {r["symbol"]: r["groups"] for r in doc["rows"]}
            self.assertEqual(by["A"], ["PICK", "PICK_V2"])
            self.assertEqual(by["B"], ["PICK_V2"])
            self.assertEqual(by["C"], ["CONTROL"])


class V3Test(unittest.TestCase):
    def test_v3_has_no_risk_filter_but_sizes_by_stop_distance(self):
        from daily_picks import v2
        ctx = {"sectorStats": {}, "regimes": {"US": "GREEN"}, "macro": None, "krClose": {}}
        def row(code, score, risk, strategies, verdict=None, liquid=None, chase=()):
            return {"code": code, "name": code, "market": "US", "price": 10.0, "score": score, "strategies": strategies, "funnelRank": None,
                    "entry": {"riskPct": risk, "riskSource": "SEPA", "verdict": verdict, "chase": list(chase), "liquidityOk": liquid, "zone": None},
                    "reject": "손절폭 8% 초과" if (risk or 0) > 8 else None}
        rows = [row("A", 3, 4.0, {"sepa": ["TREND"]}),
                row("B", 3, 20.0, {"sepa": ["TREND"]}, verdict="NO-GO", chase=["기술적 추격 경고"]),   # 필터에 걸리던 종목도 후보
                row("C", 5, 3.0, {"funnel": 1}),                                                   # SEPA 선정이 아니면 v3 후보가 아니다
                row("D", 4, 5.0, {"sepa": ["TREND"]}, liquid=False),                              # 유동성 부족은 제외
                row("E", 4, None, {"sepa": ["TREND"]})]                                            # 손절폭 미확인은 제외
        out = v2.rank_v3(rows, ctx, {}, "us", {"A": 80, "B": 95})
        self.assertEqual([p["code"] for p in out["picks"]], ["B", "A"])                           # 점수 같으면 RS 높은 순
        self.assertEqual({p["code"]: p["weight"] for p in out["picks"]}, {"B": 0.4, "A": 1.0})    # 8%/20% = 0.4, 8% 이하는 100%
        self.assertEqual(out["skipped"], {"유동성 부족": 1, "손절폭 미확인": 1})
        self.assertTrue(any("NO-GO" in w["text"] for w in out["picks"][0]["why"]))

    def test_ledger_has_v3_group(self):
        now = dt.datetime(2026, 10, 10, 1, 0, tzinfo=dt.timezone.utc)
        rec = {"session": "2026-10-09", "market": "us", "picks": [], "picksV2": [],
               "picksV3": [{"code": "B", "name": "b", "market": "US", "price": 20.0, "score": 3, "v2": {"score": 3}, "entry": {"riskPct": 20}}],
               "poolPrices": {"B": 20.0, "C": 30.0}, "rejectSummary": {}}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dp.write_once(root, "us", rec, now)
            adapters.ingest_picks("us", root / "research" / "daily_picks", root / "signals")
            doc = read_gz(root / "signals" / "picks" / "us" / "2026-10-09.json.gz")
            self.assertEqual({r["symbol"]: r["groups"] for r in doc["rows"]}, {"B": ["PICK_V3"], "C": ["CONTROL"]})


class V4Test(unittest.TestCase):
    def chart(self, root, code, atr_frac):
        d = root / "docs" / "data" / "stock_charts" / "us"
        d.mkdir(parents=True, exist_ok=True)
        n = 60
        close = [100.0] * n
        high = [100.0 * (1 + atr_frac / 2)] * n
        low = [100.0 * (1 - atr_frac / 2)] * n
        (d / f"{code}.json").write_text(json.dumps({"close": close, "high": high, "low": low}), encoding="utf-8")

    def test_atr_from_chart_matches_range(self):
        from daily_picks import context as X
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            self.chart(root, "A", 0.05)
            self.assertAlmostEqual(X.atr_pct_from_chart("A", root), 0.05, places=3)
            self.assertIsNone(X.atr_pct_from_chart("NOPE", root))

    def test_v4_uses_trend_rs_and_atr_only_and_is_us_only(self):
        from daily_picks import v2
        ctx = {"sectorStats": {}, "regimes": {"US": "GREEN"}, "macro": None, "krClose": {}}
        def row(code, score, risk, liquid=None):
            return {"code": code, "name": code, "market": "US", "price": 10.0, "score": score, "strategies": {"sepa": ["TREND"]}, "funnelRank": None,
                    "entry": {"riskPct": risk, "riskSource": "SEPA", "verdict": "NO-GO", "chase": ["과열"], "liquidityOk": liquid, "zone": None}, "reject": "손절폭 8% 초과"}
        rows = [row("A", 3, 20.0), row("B", 3, 4.0), row("C", 5, 30.0), row("D", 5, 10.0), row("E", 5, 10.0, liquid=False), row("F", 5, 10.0)]
        sepa = {"A": {"passAll": True, "rsRank": 90}, "B": {"passAll": True, "rsRank": 95}, "C": {"passAll": True, "rsRank": 60},   # C는 RS 70 미만
                "D": {"passAll": False, "rsRank": 99}, "E": {"passAll": True, "rsRank": 99}, "F": {"passAll": True, "rsRank": 99}}
        atr = {"A": 0.04, "B": 0.05, "C": 0.06, "D": 0.06, "E": 0.06, "F": 0.02}                                                 # F는 변동성 3% 미만
        out = v2.rank_v4(rows, ctx, {}, "us", sepa, lambda c: atr.get(c))
        self.assertEqual([p["code"] for p in out["picks"]], ["B", "A"])                                                          # 점수 같으면 RS 높은 순
        self.assertEqual({p["code"]: p["weight"] for p in out["picks"]}, {"B": 1.0, "A": 0.4})                                   # NO-GO·과열 표시가 있어도 후보, 비중은 8%/손절폭
        self.assertEqual(out["candidates"], 2)
        self.assertEqual(v2.rank_v4(rows, ctx, {}, "kr", sepa, lambda c: 0.05)["picks"], [])                                   # 한국은 적용하지 않는다
        out2 = v2.rank_v4(rows, ctx, {}, "us", sepa, lambda c: None)
        self.assertEqual(out2["missingAtr"], 4)                                                                                  # 일봉이 없으면 판정하지 않고 센다

    def test_ledger_has_v4_group(self):
        now = dt.datetime(2026, 10, 10, 1, 0, tzinfo=dt.timezone.utc)
        rec = {"session": "2026-10-09", "market": "us", "picks": [], "picksV2": [], "picksV3": [],
               "picksV4": [{"code": "B", "name": "b", "market": "US", "price": 20.0, "score": 3, "v2": {"score": 3}, "entry": {"riskPct": 4}}],
               "poolPrices": {"B": 20.0, "C": 30.0}, "rejectSummary": {}}
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            dp.write_once(root, "us", rec, now)
            adapters.ingest_picks("us", root / "research" / "daily_picks", root / "signals")
            doc = read_gz(root / "signals" / "picks" / "us" / "2026-10-09.json.gz")
            self.assertEqual({r["symbol"]: r["groups"] for r in doc["rows"]}, {"B": ["PICK_V4"], "C": ["CONTROL"]})
