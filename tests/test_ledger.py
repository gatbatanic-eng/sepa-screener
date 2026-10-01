import csv
import datetime as dt
import gzip
import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from ledger import adapters, config, outcomes as oc, stats
from ledger.signals import control_sample, effective_date


def series(start: str, values: list[float]) -> pd.Series:
    days = pd.bdate_range(start, periods=len(values))
    return pd.Series(values, index=[d.date().isoformat() for d in days])


class DatesTest(unittest.TestCase):
    def test_effective_date_uses_last_final_close(self):
        # 한국 장중(00:35 UTC)에 기록된 값은 전 거래일 종가, 장 마감 뒤(08:31 UTC)는 당일
        self.assertEqual(effective_date("2026-09-30T00:35:50+00:00", "kr"), dt.date(2026, 9, 29))
        self.assertEqual(effective_date("2026-09-30T08:31:10+00:00", "kr"), dt.date(2026, 9, 30))
        # 미국은 08:23 UTC에 기록되면 전날 종가, 21시 이후면 당일
        self.assertEqual(effective_date("2026-09-30T08:23:01+00:00", "us"), dt.date(2026, 9, 29))
        self.assertEqual(effective_date("2026-09-30T22:30:00+00:00", "us"), dt.date(2026, 9, 30))

    def test_control_sample_is_deterministic_and_bounded(self):
        syms = [f"{i:06d}" for i in range(1000)]
        a = control_sample("funnel", "kr", "2026-09-30", syms)
        self.assertEqual(a, control_sample("funnel", "kr", "2026-09-30", list(reversed(syms))))
        self.assertEqual(len(a), config.CONTROL_PER_DATE)
        self.assertNotEqual(a, control_sample("funnel", "kr", "2026-10-01", syms))
        self.assertEqual(control_sample("funnel", "kr", "x", syms[:10]), syms[:10])


class OutcomeTest(unittest.TestCase):
    def setUp(self):
        self.bench = series("2026-01-05", [100 + i for i in range(30)])  # 매일 +1
        self.sig = {"date": "2026-01-05", "symbol": "A"}

    def test_complete_pending_and_excess(self):
        stock = series("2026-01-05", [100, 99, 98, 110, 120, 105] + [105] * 24)
        res = oc.compute_one(self.sig, stock, self.bench, horizons=(5, 20, 60))
        self.assertEqual(res[5]["status"], "complete")
        self.assertAlmostEqual(res[5]["returnPct"], 5.0)
        self.assertAlmostEqual(res[5]["benchmarkPct"], 5.0)
        self.assertAlmostEqual(res[5]["excessPct"], 0.0)
        self.assertAlmostEqual(res[5]["maxDownPct"], -2.0)
        self.assertEqual(res[20]["status"], "complete")
        self.assertEqual(res[60]["status"], "pending")

    def test_missing_price_is_counted_not_dropped(self):
        stock = series("2026-01-05", [100, 101, 102])  # 거래정지·상폐로 5일 뒤 가격 없음
        res = oc.compute_one(self.sig, stock, self.bench, horizons=(5,))
        self.assertEqual(res[5], {"status": "unavailable", "reason": "NO_PRICE_AT_TARGET"})
        self.assertEqual(oc.compute_one(self.sig, None, self.bench, horizons=(5,))[5]["reason"], "NO_PRICE_DATA")

    def test_complete_is_frozen(self):
        frozen = {"5": {"status": "complete", "returnPct": 1.0}}
        merged = oc.merge(frozen, {5: {"status": "complete", "returnPct": 9.0}, 20: {"status": "pending"}})
        self.assertEqual(merged["5"]["returnPct"], 1.0)
        self.assertEqual(merged["20"]["status"], "pending")


def sample(i, date, group="TOP50", symbol=None):
    return {"id": f"funnel:kr:{date}:{symbol or i}:{group}", "strategy": "funnel", "market": "kr", "group": group,
            "date": date, "symbol": symbol or f"S{i}"}


class StatsTest(unittest.TestCase):
    def test_dedupe_first_signal_per_symbol(self):
        rows = [sample(1, "2026-10-02", symbol="A"), sample(2, "2026-10-01", symbol="A"), sample(3, "2026-10-03", symbol="B")]
        kept = {s["id"] for s in stats.dedupe_first(rows)}
        self.assertEqual(kept, {"funnel:kr:2026-10-01:A:TOP50", "funnel:kr:2026-10-03:B:TOP50"})
        # 대조군은 날짜별로 따로 센다
        ctrl = [sample(1, "2026-10-01", "CONTROL", "A"), sample(2, "2026-10-02", "CONTROL", "A")]
        self.assertEqual(len(stats.dedupe_first(ctrl)), 2)

    def test_status_thresholds(self):
        self.assertEqual(stats._status(0, 0), "none")
        self.assertEqual(stats._status(29, 9), "thin")
        self.assertEqual(stats._status(30, 4), "concentrated")
        self.assertEqual(stats._status(30, 5), "ok")

    def test_independent_windows_ignore_overlapping_dates(self):
        daily = [f"2026-10-{d:02d}" for d in (1, 2, 5, 6, 7, 8, 9)]  # 연속 거래일 7개
        self.assertEqual(stats.independent_windows(daily, 5), 2)
        self.assertEqual(stats.independent_windows(daily, 1), 7)
        self.assertEqual(stats.independent_windows(["2026-10-01", "2026-10-08", "2026-10-15"], 5), 3)
        self.assertEqual(stats.independent_windows([], 5), 0)

    def test_summary_vs_control_and_ci_deterministic(self):
        dates = [f"2026-10-{d:02d}" for d in range(1, 8)]
        signals, outs = [], {}
        for d in dates:
            for k in range(6):
                s = sample(k, d, "TOP50", f"T{d}{k}")
                signals.append(s)
                outs[s["id"]] = {"5": {"status": "complete", "returnPct": 6.0, "excessPct": 2.0, "maxDownPct": -3.0}}
            for k in range(6):
                s = sample(k, d, "CONTROL", f"C{d}{k}")
                signals.append(s)
                outs[s["id"]] = {"5": {"status": "complete", "returnPct": 4.0, "excessPct": 0.0, "maxDownPct": -3.0}}
        a = stats.build(signals, outs)["funnel"]["kr"]
        b = stats.build(signals, outs)["funnel"]["kr"]
        self.assertEqual(a, b)
        top = a["TOP50"]["5"]
        self.assertEqual((top["n"], top["distinctDates"], top["independentWindows"], top["status"]), (42, 7, 1, "concentrated"))
        self.assertAlmostEqual(top["meanExcessPct"], 2.0)
        self.assertAlmostEqual(top["vsControlPct"], 2.0)
        self.assertEqual(top["excessCI95"], [2.0, 2.0])
        self.assertEqual(a["TOP50"]["20"]["status"], "none")
        self.assertEqual(a["TOP50"]["20"]["pending"], 42)
        self.assertNotIn("vsControlPct", a["CONTROL"]["5"])

    def test_unavailable_reasons_reported(self):
        s = sample(1, "2026-10-01")
        res = stats.summarize_group([s], {s["id"]: {"5": {"status": "unavailable", "reason": "NO_PRICE_AT_TARGET"}}}, 5, {}, "x")
        self.assertEqual((res["n"], res["unavailable"], res["unavailableReasons"]), (0, 1, {"NO_PRICE_AT_TARGET": 1}))


class AdapterTest(unittest.TestCase):
    def test_funnel_ingest_is_immutable_and_skips_today(self):
        with tempfile.TemporaryDirectory() as d:
            funnel, ledger = Path(d) / "funnel", Path(d) / "ledger"
            snap_dir = funnel / "kr" / "snapshots"
            snap_dir.mkdir(parents=True)
            rows = [{"symbol": f"{i:06d}", "price": 100.0, "rank": i, "composite": 70.0, "excluded": i % 7 == 0,
                     "T1": "ON" if i == 3 else "OFF", "exchange": "KOSDAQ GLOBAL"} for i in range(1, 400)]
            for date in ("2026-09-30", "2026-10-01"):
                with gzip.open(snap_dir / f"{date}.json.gz", "wt", encoding="utf-8") as f:
                    json.dump({"recordedAt": f"{date}T08:31:10+00:00", "rows": rows}, f)
            n = adapters.ingest_funnel("kr", dt.date(2026, 10, 1), funnel, ledger)
            self.assertEqual(n, 1)  # 오늘(10/1) 스냅샷은 아직 끝난 날이 아니다
            self.assertEqual(adapters.ingest_funnel("kr", dt.date(2026, 10, 1), funnel, ledger), 0)
            sigs = adapters.ledger_signals("funnel", "kr", ledger)
            groups = {s["group"] for s in sigs}
            self.assertEqual(groups, {"TOP50", "T1_ON", "CONTROL"})
            self.assertTrue(all(s["date"] == "2026-09-30" for s in sigs))
            self.assertTrue(all(s["exchange"] == "KOSDAQ" for s in sigs))
            self.assertFalse(any(s["symbol"] == "000007" and s["group"] != "CONTROL" for s in sigs))  # 관문 탈락은 신호 아님
            self.assertEqual(sum(1 for s in sigs if s["group"] == "CONTROL"), config.CONTROL_PER_DATE)

    def test_multifactor_ingest_first_write_wins_per_effective_date(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / "ledger"
            path = Path(d) / "r.csv"
            with open(path, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, ["symbol", "name", "market", "signal", "composite_score", "price"])
                w.writeheader()
                w.writerows([{"symbol": "005930", "name": "삼성전자", "market": "KOSPI", "signal": "BUY", "composite_score": "81", "price": "268500"},
                             {"symbol": "AAPL", "name": "Apple", "market": "US", "signal": "WATCH", "composite_score": "60", "price": "230"},
                             {"symbol": "BAD", "name": "x", "market": "US", "signal": "SELL", "composite_score": "10", "price": ""}])
            self.assertEqual(adapters.ingest_multifactor(path, "2026-09-30T12:00:00+00:00", ledger), 2)
            self.assertEqual(adapters.ingest_multifactor(path, "2026-09-30T12:05:00+00:00", ledger), 0)
            # 22:30 UTC 실행: 한국은 같은 날(이미 있음), 미국은 새 유효일(9/30)
            self.assertEqual(adapters.ingest_multifactor(path, "2026-09-30T22:30:00+00:00", ledger), 1)
            kr = adapters.ledger_signals("multifactor", "kr", ledger)
            us = adapters.ledger_signals("multifactor", "us", ledger)
            self.assertEqual({(s["group"], s["date"]) for s in kr}, {("BUY", "2026-09-30"), ("CONTROL", "2026-09-30")})
            self.assertEqual(sorted({s["date"] for s in us}), ["2026-09-29", "2026-09-30"])

    def test_multifactor_ingest_reads_bom_csv_and_fails_loudly_on_unusable_rows(self):
        with tempfile.TemporaryDirectory() as d:
            ledger = Path(d) / "ledger"
            path = Path(d) / "bom.csv"
            cols = ["symbol", "name", "market", "signal", "composite_score", "price"]
            row = {"symbol": "005930", "name": "삼성전자", "market": "KOSPI", "signal": "BUY", "composite_score": "81", "price": "268500"}
            with open(path, "w", newline="", encoding="utf-8-sig") as f:  # screener.main과 같은 저장 방식
                w = csv.DictWriter(f, cols)
                w.writeheader()
                w.writerow(row)
            self.assertEqual(adapters.ingest_multifactor(path, "2026-10-01T18:17:00+00:00", ledger), 1)
            self.assertEqual({s["symbol"] for s in adapters.ledger_signals("multifactor", "kr", ledger)}, {"005930"})
            # 행은 있지만 쓸 수 있는 신호가 없으면 조용히 넘어가지 않고 실패한다
            bad = Path(d) / "bad.csv"
            with open(bad, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, ["ticker", "price"])
                w.writeheader()
                w.writerow({"ticker": "005930", "price": "1"})
            with self.assertRaises(ValueError):
                adapters.ingest_multifactor(bad, "2026-10-01T18:17:00+00:00", ledger)

    def test_sepa_adapter_maps_existing_outcomes(self):
        with tempfile.TemporaryDirectory() as d:
            research = Path(d)
            (research / "kr.json").write_text(json.dumps({"signals": [{
                "id": "abc", "group": "TREND", "date": "2026-09-14", "code": "036800", "name": "나이스정보통신",
                "originalClose": 8210.0, "benchmark": "KOSDAQ",
                "outcomes": {"5": {"status": "complete", "returnPct": -7.3, "benchmarkPct": 3.65, "excessPct": -10.96, "maxDownPct": -7.3},
                             "20": {"status": "pending", "observedSessions": 10}}}]}), encoding="utf-8")
            rows = adapters.sepa_signals("kr", research)
            self.assertEqual(rows[0]["outcomes"][5]["excessPct"], -10.96)
            self.assertEqual(rows[0]["outcomes"][20]["status"], "pending")
            res = stats.build(rows, {r["id"]: r["outcomes"] for r in rows})["sepa"]["kr"]["TREND"]
            self.assertEqual(res["5"]["n"], 1)
            self.assertEqual(res["20"]["pending"], 1)


class RunTest(unittest.TestCase):
    def test_update_market_computes_and_freezes(self):
        from ledger import main as lmain
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            old = (config.SIGNALS_DIR, config.OUTCOMES_DIR, config.ROOT)
            config.SIGNALS_DIR, config.OUTCOMES_DIR = base / "signals", base / "outcomes"
            try:
                doc = {"strategy": "funnel", "market": "kr", "fileDate": "2026-01-05", "recordedAt": "2026-01-05T08:00:00+00:00",
                       "effectiveDate": "2026-01-05", "rows": [{"symbol": "000001", "price": 100.0, "rank": 1, "score": 90,
                                                               "exchange": "KOSPI", "groups": ["TOP50"]}]}
                from ledger.store import write_immutable_gz
                write_immutable_gz(config.SIGNALS_DIR / "funnel" / "kr" / "2026-01-05.json.gz", doc)
                bench = {"KOSPI": series("2026-01-05", [100 + i for i in range(30)]), "KOSDAQ": series("2026-01-05", [100] * 30)}
                calls = []

                def fetch(market, symbols, start, hint=None):
                    calls.append(symbols)
                    return {"000001": series("2026-01-05", [100 + 2 * i for i in range(30)])}, {"000001": "KOSPI"}

                orig = adapters.sepa_signals
                adapters.sepa_signals = lambda market, research_dir=None: []
                try:
                    signals, outs = lmain.update_market("kr", dt.date(2026, 3, 1), bench, fetch)
                    self.assertEqual(outs[signals[0]["id"]]["5"]["status"], "complete")
                    self.assertAlmostEqual(outs[signals[0]["id"]]["5"]["excessPct"], 5.0)
                    self.assertEqual(outs[signals[0]["id"]]["60"]["status"], "pending")
                    lmain.update_market("kr", dt.date(2026, 3, 2), bench, fetch)
                    self.assertEqual(len(calls), 2)  # 아직 끝나지 않은 호라이즌이 있어 다시 조회
                finally:
                    adapters.sepa_signals = orig
            finally:
                config.SIGNALS_DIR, config.OUTCOMES_DIR, _ = old


if __name__ == "__main__":
    unittest.main()
