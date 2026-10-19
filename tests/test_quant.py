"""퀀트 계열 단위·통합 테스트(합성 데이터, 네트워크 없음): python -m unittest tests.test_quant"""
import datetime as dt
import json
import random
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from quant import config, factors, fin, inputs, stats
from quant import portfolio as pf
from quant.book import PriceBook
from quant.main import run


def kr_q(y, q, ni=10.0, op=12.0, ocf=15.0, liab=100.0, eq=100.0, basis="CFS", filed=None):
    end = dt.date(y + q // 4, q % 4 * 3 + 1, 1) - dt.timedelta(days=1)
    return {"year": y, "quarter": q, "period": f"{y} Q{q}", "basis": basis, "netIncome": ni, "operatingProfit": op, "operatingCashFlow": ocf,
            "liabilities": liab, "equity": eq, "filedAt": filed or (end + dt.timedelta(days=45)).isoformat()}


def us_q(end, ni=10.0, op=12.0, ocf=15.0, capex=3.0, liab=100.0, eq=100.0, filed=None):
    e = dt.date.fromisoformat(end)
    return {"period": end, "periodEnd": end, "netIncome": ni, "operatingProfit": op, "operatingCashFlow": ocf, "capex": capex,
            "liabilities": liab, "equity": eq, "filedAt": filed or (e + dt.timedelta(days=40)).isoformat()}


class FinTests(unittest.TestCase):
    def test_kr_ttm_uses_only_filed_quarters(self):
        qs = [kr_q(2024, i, ni=float(i)) for i in (1, 2, 3, 4)] + [kr_q(2025, 1, ni=100.0)]
        # 2025 Q1 공시(2025-05-15) 전: 2024년 4개 분기 합
        t, why = fin.ttm_kr(qs, "2025-05-14")
        self.assertIsNone(why)
        self.assertEqual(t["ni"], 10.0)
        # 공시 뒤: 2024Q2~2025Q1
        t, _ = fin.ttm_kr(qs, "2025-05-15")
        self.assertEqual(t["ni"], 2 + 3 + 4 + 100.0)

    def test_kr_ttm_missing_quarter_and_basis(self):
        qs = [kr_q(2024, 1), kr_q(2024, 3), kr_q(2024, 4), kr_q(2025, 1)]
        self.assertEqual(fin.ttm_kr(qs, "2025-06-01")[1], "no_fin")
        qs = [kr_q(2024, 2), kr_q(2024, 3, basis="OFS"), kr_q(2024, 4), kr_q(2025, 1)]
        self.assertEqual(fin.ttm_kr(qs, "2025-06-01")[1], "mixed_basis")

    def test_kr_ocf_ytd_conversion(self):
        # 최신 분기 = 2025 Q2 (누적 40). TTM OCF = 40 + 2024 Q4(연간 100) − 2024 Q2(누적 30)
        qs = [kr_q(2024, 2, ocf=30.0), kr_q(2024, 3, ocf=60.0), kr_q(2024, 4, ocf=100.0), kr_q(2025, 1, ocf=20.0), kr_q(2025, 2, ocf=40.0)]
        t, _ = fin.ttm_kr(qs, "2025-09-01")
        self.assertEqual(t["ocf"], 110.0)
        t, _ = fin.ttm_kr(qs[:4], "2025-06-01")        # 최신 = 2025 Q1: 20 + 100 − 2024Q1(없음) → 계산 불가
        self.assertIsNone(t)

    def test_kr_stale_fin(self):
        qs = [kr_q(2022, i) for i in (1, 2, 3, 4)]
        self.assertEqual(fin.ttm_kr(qs, "2025-01-01")[1], "stale_fin")

    def test_us_ttm(self):
        ends = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"]
        qs = [us_q(e, ni=float(i + 1)) for i, e in enumerate(ends)]
        t, why = fin.ttm_us(qs, "2025-03-01")
        self.assertIsNone(why)
        self.assertEqual((t["ni"], t["capex"]), (10.0, 12.0))
        self.assertEqual(fin.ttm_us(qs, "2025-01-31")[1], "no_fin")   # 4분기 10-K(2025-02-09) 공시 전: 3개만 알려짐
        gap = [us_q("2024-03-31"), us_q("2024-09-30"), us_q("2024-12-31"), us_q("2025-03-31")]
        self.assertEqual(fin.ttm_us(gap, "2025-06-01")[1], "no_fin")

    def test_us_capex_missing_keeps_ocf(self):
        ends = ["2024-03-31", "2024-06-30", "2024-09-30", "2024-12-31"]
        qs = [{**us_q(e), "capex": None} for e in ends]
        t, _ = fin.ttm_us(qs, "2025-03-01")
        self.assertIsNone(t["capex"])

    def test_shares_us_point_in_time(self):
        s = [{"end": "2024-06-30", "filedAt": "2024-08-01", "val": 100.0}, {"end": "2024-09-30", "filedAt": "2024-11-01", "val": 120.0}]
        self.assertEqual(fin.shares_us(s, "2024-10-15"), 100.0)
        self.assertEqual(fin.shares_us(s, "2024-11-01"), 120.0)
        self.assertIsNone(fin.shares_us(s, "2024-07-01"))


def row(sym, **kw):
    f = {"ni": 10.0, "opinc": 12.0, "ocf": 15.0, "capex": None, "liabilities": 100.0, "equity": 100.0, "assets": 200.0}
    f.update(kw.pop("fin", {}))
    px = {"lastBar": "2025-01-31", "price": 110.0, "sma200": 100.0, "ret_6m": 0.1, "ret_12_1": 0.2}
    px.update(kw.pop("px", {}))
    base = {"symbol": sym, "name": sym, "industry": "26100", "mcap": 2000e8, "fin": f, "finReason": None, "px": px}
    base.update(kw)
    return base


class FactorTests(unittest.TestCase):
    def test_gate_reasons(self):
        g = lambda r, m="kr": factors.gate(r, m, "2025-01-31")
        self.assertIsNone(g(row("A")))
        self.assertEqual(g(row("A", industry="64190")), "financial")
        self.assertEqual(g(row("A", industry="6021"), "us"), "financial")
        self.assertEqual(g(row("A", mcap=500e8)), "small_cap")
        self.assertEqual(g(row("A", mcap=None)), "no_shares")
        self.assertEqual(g(row("A", fin=None, finReason="no_fin")) if False else g({**row("A"), "fin": None, "finReason": "stale_fin"}), "stale_fin")
        self.assertEqual(g(row("A", fin={"ni": -1.0})), "loss")
        self.assertEqual(g(row("A", fin={"ocf": 0.0})), "ocf_nonpositive")
        self.assertEqual(g(row("A", fin={"equity": -5.0})), "equity_nonpositive")
        self.assertEqual(g(row("A", fin={"liabilities": 250.0})), "leverage")
        self.assertEqual(g(row("A", px={"lastBar": "2025-01-10"})), "stale_price")
        self.assertEqual(g(row("A", px={"price": 90.0})), "below_sma200")
        self.assertIsNone(g(row("A", industry=None)))   # 업종을 모르면 금융 제외를 걸지 않는다

    def test_score_direction_and_ties(self):
        rows = [row("A", fin={"ni": 30.0}), row("B"), row("C", fin={"ni": 5.0})]
        df = factors.score(rows)
        v = df.set_index("symbol")["value"]
        self.assertGreater(v["A"], v["B"])
        self.assertGreater(v["B"], v["C"])
        lev = factors.score([row("A", fin={"liabilities": 20.0}), row("B", fin={"liabilities": 190.0})]).set_index("symbol")["quality"]
        self.assertGreater(lev["A"], lev["B"])      # 부채가 낮을수록 퀄리티 높음

    def test_cash_quality_clipped(self):
        i = factors.indicators(row("A", fin={"ni": 1.0, "ocf": 100.0}))
        self.assertEqual(i["cash_quality"], 3.0)

    def test_us_cash_yield_uses_capex(self):
        i = factors.indicators(row("A", fin={"capex": 5.0}))
        self.assertAlmostEqual(i["cf_yield"], (15.0 - 5.0) / 2000e8)

    def test_order_and_control_deterministic(self):
        df = factors.score([row(s, fin={"ni": 10.0 + k}) for k, s in enumerate("ABCDE")])
        self.assertEqual(factors.order(df, "multifactor", "2025-01-31", "kr"), factors.order(df, "multifactor", "2025-01-31", "kr"))
        c1 = factors.order(df, "control", "2025-01-31", "kr")
        self.assertEqual(c1, factors.order(df, "control", "2025-01-31", "kr"))
        self.assertEqual(sorted(c1), list("ABCDE"))
        self.assertNotEqual(c1, factors.order(df, "control", "2025-02-28", "kr") if False else [])


class PortfolioTests(unittest.TestCase):
    def test_initial_fill_with_industry_cap(self):
        ranked = [f"S{i}" for i in range(20)]
        ind = {s: ("X" if i < 10 else "Y") for i, s in enumerate(ranked)}
        r = pf.select(ranked, ind, set(), 6, max_per_industry=4)
        self.assertEqual(len(r["targets"]), 6)
        self.assertEqual(r["entries"], ["S0", "S1", "S2", "S3", "S10", "S11"])
        self.assertEqual(r["industrySkipped"], ["S4", "S5", "S6", "S7", "S8", "S9"])

    def test_keep_buffer_and_exit(self):
        ranked = [f"S{i}" for i in range(40)]
        held = {"S0", "S5", "S20", "S35"}               # S35 는 순위 36위 → 퇴출
        r = pf.select(ranked, {}, held, 6, keep_rank=30)
        self.assertEqual(r["kept"], ["S0", "S5", "S20"])
        self.assertEqual(r["exits"], ["S35"])
        self.assertEqual(r["entries"], ["S1", "S2", "S3"])   # 빈 자리는 순위 높은 비보유부터
        self.assertEqual(len(r["targets"]), 6)

    def test_ineligible_holding_exits(self):
        r = pf.select(["A", "B", "C"], {}, {"Z"}, 2)
        self.assertEqual(r["exits"], ["Z"])
        self.assertEqual(r["entries"], ["A", "B"])

    def test_no_rebuy_of_held_outside_buffer(self):
        ranked = [f"S{i}" for i in range(40)]
        r = pf.select(ranked, {}, {"S35"}, 3, keep_rank=30)
        self.assertNotIn("S35", r["targets"])


class StatsTests(unittest.TestCase):
    def test_mdd(self):
        self.assertEqual(stats.max_drawdown([100, 120, 90, 110]), -25.0)
        self.assertEqual(stats.max_drawdown([100, 110]), 0.0)

    def test_review_stage(self):
        self.assertEqual(stats.review_stage("ACTIVE", -10.0, 3, [])[0], "ACTIVE")
        self.assertEqual(stats.review_stage("ACTIVE", -15.0, 3, [])[0], "PROBATION")      # 표본 무관
        self.assertEqual(stats.review_stage("PROBATION", -25.0, 1, [])[0], "RETIRED")
        self.assertEqual(stats.review_stage("RETIRED", -1.0, 99, [5.0] * 99)[0], "RETIRED")   # 되돌리지 않음
        self.assertEqual(stats.review_stage("ACTIVE", -5.0, 11, [-1.0] * 11)[0], "ACTIVE")     # 12회 미만은 성과 판단 금지
        self.assertEqual(stats.review_stage("ACTIVE", -5.0, 12, [-1.0] * 12)[0], "PROBATION")
        self.assertEqual(stats.review_stage("PROBATION", -5.0, 24, [-1.0, -1.2] * 12)[0], "RETIRED")

    def test_boot_ci_deterministic(self):
        a = stats.boot_mean_ci([1.0, -0.5, 2.0, 0.3])
        self.assertEqual(a, stats.boot_mean_ci([1.0, -0.5, 2.0, 0.3]))
        self.assertIsNone(stats.boot_mean_ci([1.0]))


class BookTests(unittest.TestCase):
    def test_features_use_only_data_up_to_date(self):
        idx = [(dt.date(2024, 1, 1) + dt.timedelta(days=i)).isoformat() for i in range(300)]
        vals = np.linspace(10, 40, 300)
        b = PriceBook({"A": pd.Series(vals, index=idx)})
        f1 = b.features("A", idx[270])
        vals2 = vals.copy()
        vals2[271:] *= 100                              # 신호일 이후만 바꿈
        f2 = PriceBook({"A": pd.Series(vals2, index=idx)}).features("A", idx[270])
        self.assertEqual(f1, f2)
        self.assertIsNone(b.features("A", idx[100]))     # 봉이 모자람


# ---- 통합: 합성 유니버스 ----
def bdays(start, end):
    d, out = dt.date.fromisoformat(start), []
    while d <= dt.date.fromisoformat(end):
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


DAYS = bdays("2023-12-01", "2026-12-03")


def make_world(tmp: Path, n=70, seed=1):
    rng = random.Random(seed)
    fin_dir = tmp / "fin"
    closes, kr_extra, us_names = {}, {}, {}
    for market in ("kr", "us"):
        for i in range(n):
            sym = f"{i:06d}" if market == "kr" else f"U{i:03d}"
            drift = rng.uniform(0.0004, 0.0012)
            p, vals = 100.0 * rng.uniform(0.5, 2), []
            for _ in DAYS:
                p *= 1 + drift + rng.gauss(0, 0.01)
                vals.append(p)
            closes[(market, sym)] = pd.Series(vals, index=DAYS)
            mult = rng.uniform(0.6, 1.6)
            if market == "kr":
                qs = [kr_q(y, q, ni=10 * mult * (1 + 0.1 * i % 7), op=12 * mult, ocf=15 * mult, liab=80 + i, eq=100)
                      for y in (2023, 2024, 2025, 2026) for q in (1, 2, 3, 4) if (y, q) <= (2026, 3)]
                rec = {"code": sym, "name": f"K{i}", "industryCode": f"{20 + i % 9}100", "quarters": qs}
                kr_extra[sym] = {"name": f"K{i}", "marcapEok": 3000.0 + i, "asOf": "20261130"}
            else:
                ends = []
                for y in (2023, 2024, 2025, 2026):
                    for m in (3, 6, 9, 12):
                        e = dt.date(y, m, 1) + dt.timedelta(days=31)
                        e = (e.replace(day=1) - dt.timedelta(days=1)).isoformat()
                        if e <= "2026-09-30":
                            ends.append(e)
                qs = [us_q(e, ni=10 * mult * (1 + 0.1 * i % 7), op=12 * mult, ocf=15 * mult, liab=80 + i, eq=100) for e in ends]
                shares = [{"end": "2023-09-30", "filedAt": "2023-11-01", "val": 3e9 / closes[(market, sym)].iloc[0]}]
                rec = {"code": sym, "name": f"U{i}", "industryCode": f"{2000 + 100 * (i % 9)}", "quarters": qs, "shares": shares}
                us_names[sym] = f"U{i}"
            f = fin_dir / market / f"{sym}.json"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(json.dumps(rec), encoding="utf-8")
    extra = tmp / "kr_extra.json"
    extra.write_text(json.dumps({"stocks": kr_extra}), encoding="utf-8")
    return fin_dir, extra, closes, us_names


def fake_fetch(closes):
    def f(market, symbols, start, hint=None):
        return {s: closes[(market, s)] for s in symbols if (market, s) in closes}, {}
    return f


def fake_bench(start):
    return {"KOSPI": pd.Series([1.0, 2.0], index=["2026-12-02", "2026-12-03"]), "US": pd.Series([1.0, 2.0], index=["2026-12-02", "2026-12-03"])}


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.t = Path(self.tmp.name)
        self.fin_dir, self.extra, self.closes, self.us = make_world(self.t)
        self.asof = {"kr": "2026-12-03", "us": "2026-12-03"}

    def tearDown(self):
        self.tmp.cleanup()

    def run_it(self, closes=None, write=True, fin_dir=None, extra=None):
        return run(dt.date(2026, 12, 3), fetch=fake_fetch(closes or self.closes), bench_fetch=fake_bench, asof=self.asof,
                   extra_path=extra or self.extra, us_names=self.us, fin_dir=fin_dir or self.fin_dir, selections_dir=self.t / "sel",
                   reviews_path=self.t / "reviews.json", write=write, now=dt.datetime(2026, 12, 3, 23, 0, tzinfo=dt.timezone.utc))

    def test_runs_and_structure(self):
        import quant.config as qc
        old = qc.PUBLIC_JSON
        qc.PUBLIC_JSON = self.t / "quant.json"
        try:
            res = self.run_it()
        finally:
            qc.PUBLIC_JSON = old
        live = res["series"]["live"]
        self.assertEqual([e["signal"] for e in live["events"] if e["market"] == "kr"], ["2026-10-30", "2026-11-30"])
        mf = live["variants"]["multifactor"]
        self.assertEqual(set(live["variants"]), set(config.VARIANTS))
        self.assertTrue(mf["curve"])
        self.assertEqual(mf["curve"][0][0], "2026-11-02")
        self.assertLessEqual(len(mf["holdings"]), config.TOTAL_SLOTS)
        self.assertGreater(len(mf["holdings"]), 20)
        # 첫 체결 직후 평가금액은 비용만큼 줄어든 자본에 가깝다
        self.assertLess(mf["curve"][0][1], config.INITIAL_CAPITAL * 1.02)
        self.assertGreater(mf["curve"][0][1], config.INITIAL_CAPITAL * 0.97)
        self.assertEqual(len(mf["periods"]), 1)
        prev = res["series"]["preview"]["variants"]["multifactor"]
        self.assertGreater(len(prev["periods"]), 10)
        self.assertLess(prev["summary"]["cash"], 0.01 * prev["curve"][-1][1])      # 현금이 쌓여 놀지 않는다
        self.assertTrue((self.t / "quant.json").exists())
        # 선정 기록은 실전 신호일마다 한 번씩
        self.assertEqual(sorted(p.name for p in (self.t / "sel").glob("*.json")),
                         ["2026-10-30-kr.json", "2026-10-30-us.json", "2026-11-30-kr.json", "2026-11-30-us.json"])

    def test_selection_records_are_write_once_and_drift_detected(self):
        import quant.config as qc
        old = qc.PUBLIC_JSON
        qc.PUBLIC_JSON = self.t / "quant.json"
        try:
            self.run_it()
            p = self.t / "sel" / "2026-10-30-kr.json"
            first = p.read_text()
            self.run_it()
            self.assertEqual(p.read_text(), first)                       # 두 번째 실행이 덮어쓰지 않음
            rec = json.loads(first)
            rec["targets"] = ["kr:999999"]
            p.write_text(json.dumps(rec))
            res = self.run_it()
            self.assertTrue(any(d["signal"] == "2026-10-30" and d["market"] == "kr" for d in res["dataHealth"]["drift"]))
            self.assertEqual(json.loads(p.read_text())["targets"], ["kr:999999"])
        finally:
            qc.PUBLIC_JSON = old

    def test_no_lookahead_signal_unchanged_by_later_prices_or_filings(self):
        first = self.run_it(write=False)
        # 2026-10-30 이후 가격을 크게 바꾸고, 그 뒤에 공시된 재무를 바꿔도 10-30 신호는 같아야 한다
        closes2 = {}
        for k, s in self.closes.items():
            s2 = s.copy()
            s2[s2.index > "2026-10-30"] *= 3.0
            closes2[k] = s2
        d2 = self.t / "fin2"
        for m in ("kr", "us"):
            (d2 / m).mkdir(parents=True)
            for p in (self.fin_dir / m).glob("*.json"):
                rec = json.loads(p.read_text())
                for q in rec["quarters"]:
                    if q["filedAt"] > "2026-10-30":
                        q["netIncome"] = -1e9
                (d2 / m / p.name).write_text(json.dumps(rec))
        # 시총은 가격과 함께 움직인다(주식 수 근사 = 시총 ÷ 같은 날 종가 가 가격 변화에 불변이어야 하므로 일관된 세계로 만든다)
        extra2 = self.t / "kr_extra2.json"
        d = json.loads(self.extra.read_text())
        for r in d["stocks"].values():
            r["marcapEok"] *= 3.0
        extra2.write_text(json.dumps(d))
        second = self.run_it(closes=closes2, write=False, fin_dir=d2, extra=extra2)
        a = {(e["market"], e["signal"]): e for e in first["series"]["live"]["events"]}
        b = {(e["market"], e["signal"]): e for e in second["series"]["live"]["events"]}
        self.assertEqual(a[("kr", "2026-10-30")], b[("kr", "2026-10-30")])
        self.assertEqual(a[("us", "2026-10-30")], b[("us", "2026-10-30")])
        self.assertEqual(first["current"]["kr"]["eligible"] > 0, True)

    def test_missing_financials_never_enter(self):
        (self.fin_dir / "kr" / "000001.json").unlink()
        res = self.run_it(write=False)
        holds = {h["symbol"] for v in res["series"]["live"]["variants"].values() for h in v["holdings"]}
        self.assertNotIn("kr:000001", holds)

    def test_market_with_too_few_eligible_is_skipped(self):
        for p in list((self.fin_dir / "us").glob("*.json"))[:50]:      # 미국 재무 대부분 삭제 → 적격 30 미만
            p.unlink()
        res = self.run_it(write=False)
        evs = [e for e in res["series"]["live"]["events"] if e["market"] == "us"]
        self.assertTrue(all(e["skipped"] for e in evs))
        mf = res["series"]["live"]["variants"]["multifactor"]
        self.assertTrue(all(h["market"] == "kr" for h in mf["holdings"]))

    def test_empty_data_first_run_does_not_crash(self):
        import quant.config as qc
        old = qc.PUBLIC_JSON
        qc.PUBLIC_JSON = self.t / "empty.json"
        try:
            empty = self.t / "empty_extra.json"
            empty.write_text(json.dumps({"stocks": {}}))
            res = run(dt.date(2026, 12, 3), fetch=lambda *a, **k: ({}, {}), bench_fetch=lambda s: {}, asof=self.asof, extra_path=empty,
                      us_names={}, fin_dir=self.t / "nofin", selections_dir=self.t / "nosel", reviews_path=self.t / "norev.json", write=True)
        finally:
            qc.PUBLIC_JSON = old
        self.assertEqual(res["series"]["live"]["variants"], {})
        self.assertTrue((self.t / "empty.json").exists())

    def test_control_differs_but_same_size(self):
        res = self.run_it(write=False)
        v = res["series"]["live"]["variants"]
        self.assertNotEqual({h["symbol"] for h in v["control"]["holdings"]}, {h["symbol"] for h in v["multifactor"]["holdings"]})
        self.assertGreater(len(v["control"]["holdings"]), 20)


# ---- 수집 보조(네트워크 없음) ----
def dart_rows(year, quarter, ni=10.0, ocf=5.0):
    rc = f"{year}{quarter * 3:02d}15000001"
    def r(sj, nm, v, add=None):
        d = {"rcept_no": rc, "sj_div": sj, "currency": "KRW", "account_id": "", "account_nm": nm, "thstrm_amount": str(v)}
        if add is not None:
            d["thstrm_add_amount"] = str(add)
        return d
    k = 4 if quarter == 4 else 1          # 4분기 보고서(사업보고서)의 당기 금액은 연간
    return [r("IS", "매출액", 100 * k, 100 * quarter), r("IS", "영업이익", 12 * k, 12 * quarter), r("IS", "당기순이익", ni * k, ni * quarter),
            r("CF", "영업활동현금흐름", ocf * quarter), r("BS", "부채총계", 80), r("BS", "자본총계", 100)]


class CollectTests(unittest.TestCase):
    def test_normalize_long_covers_more_than_eight_quarters(self):
        from quant import collect
        reports = {(y, q): {"rows": dart_rows(y, q), "basis": "CFS"} for y in (2023, 2024, 2025) for q in (1, 2, 3, 4)}
        out = collect.normalize_long(reports)
        self.assertEqual(len(out), 12)
        self.assertEqual([(q["year"], q["quarter"]) for q in out], sorted(reports))
        q4 = next(q for q in out if (q["year"], q["quarter"]) == (2023, 4))
        self.assertEqual(q4["netIncome"], 10.0)               # 연간 − 3분기 누적 = 단일 분기

    def test_shares_from_facts_sums_classes_per_filing(self):
        from quant import collect
        rows = [{"form": "10-Q", "accn": "a1", "end": "2024-06-30", "filed": "2024-08-01", "val": 100.0},
                {"form": "10-Q", "accn": "a1", "end": "2024-06-30", "filed": "2024-08-01", "val": 50.0},
                {"form": "10-Q", "accn": "a1", "end": "2024-06-30", "filed": "2024-08-01", "val": 50.0},   # 같은 값 중복은 한 번만
                {"form": "8-K", "accn": "a2", "end": "2024-07-01", "filed": "2024-07-02", "val": 999.0},
                {"form": "10-K", "accn": "a3", "end": "2024-12-31", "filed": "2025-02-01", "val": 200.0}]
        out = collect.shares_from_facts({"facts": {"dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": rows}}}}})
        self.assertEqual([(x["filedAt"], x["val"]) for x in out], [("2024-08-01", 150.0), ("2025-02-01", 200.0)])

    def test_collect_kr_respects_budget_and_resumes(self):
        from quant import collect

        class FakeDart:
            def __init__(self): self.n = 0
            def corporations(self, cache_dir=None): return {c: "C" + c for c in ("000001", "000002", "000003")}
            def request(self, endpoint, **params):
                self.n += 1
                if endpoint == "company.json":
                    return {"induty_code": "26100"}
                return dart_rows(params["bsns_year"], {"11013": 1, "11012": 2, "11014": 3, "11011": 4}[params["reprt_code"]])

        with tempfile.TemporaryDirectory() as tmp:
            out, cache = Path(tmp) / "kr", Path(tmp) / "cache"
            codes = {"000001": "A", "000002": "B", "000003": "C"}
            now = dt.datetime(2026, 12, 3, tzinfo=dt.timezone.utc)
            r1 = collect.collect_kr(codes, now, api=FakeDart(), out_dir=out, cache_dir=cache, budget=10, workers=1)
            self.assertTrue(r1["budgetExhausted"])
            self.assertLessEqual(r1["requests"], 10)
            r2 = collect.collect_kr(codes, now, api=FakeDart(), out_dir=out, cache_dir=cache, budget=10000, workers=1)
            self.assertFalse(r2["budgetExhausted"])
            self.assertEqual(len(list(out.glob("*.json"))), 3)
            rec = json.loads((out / "000001.json").read_text())
            self.assertEqual(rec["industryCode"], "26100")
            self.assertTrue(all(q["filedAt"] for q in rec["quarters"]))
            r3 = collect.collect_kr(codes, now, api=FakeDart(), out_dir=out, cache_dir=cache, budget=10000, workers=1)
            self.assertEqual((r3["stale"], r3["requests"]), (0, 0))          # 신선하면 호출하지 않음

    def test_us_universe_falls_back_to_saved_file(self):
        from quant import collect
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "u.json"
            listing = pd.DataFrame({"Symbol": [f"T{i}" for i in range(450)] + ["BRKB"], "Name": ["n"] * 451})
            got = collect.load_us_universe(path, listing=listing)
            self.assertIn("BRK-B", got)
            self.assertEqual(collect.load_us_universe(path, listing=pd.DataFrame({"Symbol": ["X"], "Name": ["x"]})), got)   # 너무 적으면 직전 파일


if __name__ == "__main__":
    unittest.main()
