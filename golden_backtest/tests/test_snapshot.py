"""데이터 스냅샷 식별자와 검증 읽기. 백테스트는 캐시만 읽고(네트워크 없음) manifest의 스냅샷과 같은 데이터만 쓴다."""
from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
import pandas as pd

from golden_backtest.data import store

ROOT = Path(__file__).resolve().parents[1]


def _df(n=10, bump=0.0):
    idx = pd.bdate_range("2024-01-02", periods=n)
    c = np.linspace(10, 12, n) + bump
    return pd.DataFrame({"open": c, "high": c * 1.01, "low": c * 0.99, "close": c, "volume": 100.0}, index=idx)


class TestHashes(unittest.TestCase):
    def test_same_content_same_hash_and_one_changed_value_changes_it(self):
        self.assertEqual(store.frame_hash(_df()), store.frame_hash(_df()))
        d = _df()
        d.iloc[3, d.columns.get_loc("high")] += 1e-9
        self.assertNotEqual(store.frame_hash(_df()), store.frame_hash(d))

    def test_hash_survives_parquet_roundtrip_and_date_unit_change(self):
        # 저장·재로드(날짜 단위가 바뀔 수 있다)해도 같은 데이터면 같은 해시여야 스냅샷 검증이 성립한다
        d = _df()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.parquet"
            d.to_parquet(path)
            back = pd.read_parquet(path)
        self.assertEqual(store.frame_hash(d), store.frame_hash(back))
        for unit in ("s", "us", "ns"):
            e = d.copy()
            e.index = e.index.astype(f"datetime64[{unit}]")
            self.assertEqual(store.frame_hash(d), store.frame_hash(e), unit)

    def test_index_and_column_names_are_part_of_the_hash(self):
        d = _df()
        self.assertNotEqual(store.frame_hash(d), store.frame_hash(d.rename(columns={"high": "hi"})))
        self.assertNotEqual(store.frame_hash(d), store.frame_hash(d.set_axis(d.index + pd.Timedelta(days=1))))

    def test_snapshot_id_has_date_prefix_and_depends_on_every_symbol_hash(self):
        a = store.make_snapshot_id("2026-10-08T01:16:03+00:00", {"AAPL": "aa", "KO": "bb"}, {"sp500": "cc"})
        self.assertTrue(a.startswith("20261008-"))
        self.assertEqual(len(a), len("20261008-") + 12)
        self.assertEqual(a, store.make_snapshot_id("2026-10-08T23:59:59+00:00", {"KO": "bb", "AAPL": "aa"}, {"sp500": "cc"}))   # 순서·시각 무관
        self.assertNotEqual(a, store.make_snapshot_id("2026-10-08T01:16:03+00:00", {"AAPL": "aa", "KO": "bX"}, {"sp500": "cc"}))
        self.assertNotEqual(a, store.make_snapshot_id("2026-10-08T01:16:03+00:00", {"AAPL": "aa", "KO": "bb"}, {"sp500": "cX"}))


class TestVerifiedLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "ohlcv").mkdir()
        (base / "manifest").mkdir()
        self.patches = [mock.patch.object(store, "OHLCV_DIR", base / "ohlcv"), mock.patch.object(store, "MANIFEST_DIR", base / "manifest")]
        for p in self.patches:
            p.start()
        store._MANIFEST_CACHE.clear()
        self.df = _df()
        self.df.to_parquet(base / "ohlcv" / "AAA.parquet")
        manifest = {"collected_at": "2026-10-08T00:00:00+00:00", "spec_version": "1.7", "snapshot": {"id": "20261008-abcdef123456"},
                    "symbols": {"AAA": {"rows": len(self.df), "first": "2024-01-02", "last": self.df.index[-1].date().isoformat(),
                                        "content_hash": store.frame_hash(self.df)}}}
        (base / "manifest" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.base = base

    def tearDown(self):
        for p in self.patches:
            p.stop()
        store._MANIFEST_CACHE.clear()
        self.tmp.cleanup()

    def test_matching_cache_loads_full_history(self):
        pd.testing.assert_frame_equal(store.load_ohlcv_verified("AAA"), self.df, check_freq=False)

    def test_snapshot_info_has_id_date_and_spec(self):
        self.assertEqual(store.snapshot_info(), {"snapshot_id": "20261008-abcdef123456", "collected_at": "2026-10-08T00:00:00+00:00", "spec_version": "1.7"})

    def test_changed_content_is_rejected(self):
        _df(bump=1e-6).to_parquet(self.base / "ohlcv" / "AAA.parquet")     # 같은 행 수·날짜, 값만 미세하게 다름
        with self.assertRaises(store.SnapshotMismatch):
            store.load_ohlcv_verified("AAA")

    def test_extra_rows_after_recollection_are_rejected(self):
        _df(n=11).to_parquet(self.base / "ohlcv" / "AAA.parquet")
        with self.assertRaises(store.SnapshotMismatch):
            store.load_ohlcv_verified("AAA")

    def test_symbol_outside_manifest_is_rejected(self):
        self.df.to_parquet(self.base / "ohlcv" / "BBB.parquet")
        with self.assertRaises(store.SnapshotMismatch):
            store.load_ohlcv_verified("BBB")


class TestRunnerReadsVerifiedFullHistory(unittest.TestCase):
    """evaluation/run.py: 검증된 스냅샷의 전체 이력만 엔진에 넘긴다. 워밍업 게이트는 전체 캐시의 첫 봉부터 센다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        (base / "ohlcv").mkdir()
        (base / "manifest").mkdir()
        self.patches = [mock.patch.object(store, "OHLCV_DIR", base / "ohlcv"), mock.patch.object(store, "MANIFEST_DIR", base / "manifest")]
        for p in self.patches:
            p.start()
        store._MANIFEST_CACHE.clear()
        idx = pd.bdate_range("2020-01-02", periods=300)
        self.df = pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1.0}, index=idx)
        self.df.to_parquet(base / "ohlcv" / "AAA.parquet")
        manifest = {"collected_at": "2026-10-08T00:00:00+00:00", "spec_version": "1.7", "snapshot": {"id": "20261008-abcdef123456"},
                    "symbols": {"AAA": {"rows": 300, "first": idx[0].date().isoformat(), "last": idx[-1].date().isoformat(),
                                        "content_hash": store.frame_hash(self.df)}}}
        (base / "manifest" / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.base = base

    def tearDown(self):
        for p in self.patches:
            p.stop()
        store._MANIFEST_CACHE.clear()
        self.tmp.cleanup()

    def _run(self, signal_bar):
        from golden_backtest.evaluation import run
        from golden_backtest.tests.engine_helpers import COSTS, NEXT_OPEN, SPEC, ScriptedStrategy, intraday_stop
        s = ScriptedStrategy({signal_bar: NEXT_OPEN}, intraday_stop(90))
        return run.run_strategy("AAA", s, COSTS, SPEC)

    def test_gate_counts_from_first_bar_of_full_cache(self):
        df, res = self._run(251)             # 전체 캐시 기준 봉 251 → 252봉 안이라 차단
        self.assertEqual(len(df), 300)       # 전체 이력(자르지 않음)
        self.assertEqual(res.trades, [])
        df, res = self._run(252)             # 봉 252는 허용
        self.assertEqual(len(res.with_open_mtm()), 1)

    def test_changed_cache_is_refused(self):
        from golden_backtest.evaluation import run
        d = self.df.copy()
        d.iloc[10, d.columns.get_loc("high")] += 1e-6
        d.to_parquet(self.base / "ohlcv" / "AAA.parquet")
        with self.assertRaises(store.SnapshotMismatch):
            run.load_history("AAA")


class TestBacktestReadsCacheOnly(unittest.TestCase):
    """백테스트 실행 코드(evaluation/, reports/, strategies/, engine/)는 네트워크 모듈을 import하지 않는다. 재수집은 data.build로만 한다."""

    FORBIDDEN = ("yfinance", "FinanceDataReader", "requests", "golden_backtest.data.providers", "golden_backtest.data.build")

    def _imports(self, path):
        names = set()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                names.update(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
                names.update(f"{node.module}.{a.name}" for a in node.names)
        return names

    def test_no_network_imports_in_backtest_code(self):
        files = [f for d in ("evaluation", "reports", "strategies", "engine", "records") for f in (ROOT / d).glob("*.py")]
        self.assertGreater(len(files), 10)
        for f in files:
            bad = [n for n in self._imports(f) if any(n == b or n.startswith(b + ".") for b in self.FORBIDDEN)]
            self.assertEqual(bad, [], f"{f.relative_to(ROOT)}가 네트워크·수집 모듈을 import한다: {bad}")


if __name__ == "__main__":
    unittest.main()
