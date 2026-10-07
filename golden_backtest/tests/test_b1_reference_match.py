"""엔진+B1 전략 결과를 규격만 보고 따로 쓴 참조 구현(b1_reference.py)과 거래 단위로 대조한다. 불일치는 0이어야 한다.

참조 구현의 N은 터틀 원전식(처음 20일 TR 평균 시드), 엔진 지표는 첫 TR 시드의 지수평활이라 초기 구간 값이 다르다.
차이는 (19/20)^봉수로 줄어드므로, 합성 데이터는 신호 시작일을 1000봉 뒤로 잡고 실제 종목은 수십 년 이력을 써서 비교 허용오차(1e-8) 안에 들어온다.
"""
from __future__ import annotations

import unittest

from golden_backtest.data import store
from golden_backtest.engine.simulator import simulate
from golden_backtest.strategies.b1_turtle import B1Turtle
from golden_backtest.tests import b1_reference as ref
from golden_backtest.tests.engine_helpers import COSTS, SPEC
from golden_backtest.tests.lookahead import synthetic_ohlcv

CHECK_TICKERS = ("AAPL", "NVDA", "OXY", "KDP", "KO")


def compare_one(df, **params):
    start = params.pop("trade_start", None)
    strat = B1Turtle(trade_start=start, **params)
    res = simulate(df, strat, "X", COSTS, SPEC)
    cols = {k: df[k].tolist() for k in ("open", "high", "low", "close")}
    r = ref.run_b1(list(df.index), cols["open"], cols["high"], cols["low"], cols["close"],
                   entry_n=strat.entry_n, exit_n=strat.exit_n, atr_n=strat.atr_n, stop_mult=strat.stop_mult, rate=COSTS.one_way_rate(),
                   trade_start=strat.trade_start)
    matched, mism = ref.compare(r, res.with_open_mtm())
    return len(r), matched, mism


class TestSyntheticMatch(unittest.TestCase):
    def test_matches_on_random_walks_with_spec_and_small_parameters(self):
        total = 0
        for seed in range(1, 9):
            df = synthetic_ohlcv(2500, seed=seed)
            start = str(df.index[1000].date())
            for params in ({}, {"entry_n": 20, "exit_n": 10, "atr_n": 14}, {"entry_n": 10, "exit_n": 5, "atr_n": 5, "stop_mult": 1.5}):
                with self.subTest(seed=seed, params=params):
                    n, matched, mism = compare_one(df, trade_start=start, **params)
                    total += n
                    self.assertEqual(mism, [], f"불일치 {len(mism)}건: {mism[:2]}")
                    self.assertEqual(matched, n)
        self.assertGreater(total, 200, "비교가 의미 있으려면 거래가 충분해야 한다")


class TestComparisonIsNotVacuous(unittest.TestCase):
    """비교 틀이 실제로 불일치를 잡는지(대조군): 참조 구현의 규칙을 일부러 바꾸면 불일치가 나와야 한다."""

    def setUp(self):
        self.df = synthetic_ohlcv(2500, seed=5)
        self.start = self.df.index[1000]
        strat = B1Turtle(trade_start=str(self.start.date()))
        self.res = simulate(self.df, strat, "X", COSTS, SPEC)
        self.cols = {k: self.df[k].tolist() for k in ("open", "high", "low", "close")}

    def _ref(self, **over):
        kw = dict(entry_n=55, exit_n=20, atr_n=20, stop_mult=2.0, rate=COSTS.one_way_rate(), trade_start=self.start)
        kw.update(over)
        return ref.run_b1(list(self.df.index), self.cols["open"], self.cols["high"], self.cols["low"], self.cols["close"], **kw)

    def test_same_rules_match(self):
        self.assertEqual(ref.compare(self._ref(), self.res.with_open_mtm())[1], [])

    def test_changed_stop_multiple_is_detected(self):
        self.assertTrue(ref.compare(self._ref(stop_mult=2.1), self.res.with_open_mtm())[1])

    def test_changed_exit_window_is_detected(self):
        self.assertTrue(ref.compare(self._ref(exit_n=19), self.res.with_open_mtm())[1])

    def test_changed_cost_rate_is_detected(self):
        self.assertTrue(ref.compare(self._ref(rate=0.002), self.res.with_open_mtm())[1])


class TestNSeedExplainsEarlyMismatch(unittest.TestCase):
    """실전 유니버스 대조에서 나온 유일한 불일치 원인: N 시드. 상장 후 252봉 안에서 시작하는 신호는 참조(터틀 원전 SMA 시드)와
    엔진(첫 TR 시드 지수평활)이 N을 다르게 계산해 어긋난다. 참조를 엔진과 같은 시드로 돌리면 불일치가 0이다."""

    def test_mismatch_only_with_different_seed_when_signals_start_early(self):
        mism_sma = mism_first = trades = 0
        for seed in range(1, 9):
            df = synthetic_ohlcv(1500, seed=seed)
            strat = B1Turtle(trade_start=str(df.index[60].date()))      # 데이터 시작 후 60봉부터 신호 허용(워밍업 부족)
            res = simulate(df, strat, "X", COSTS, SPEC, warmup_bars=0)   # 게이트를 끄고 초반 신호를 허용
            cols = {k: df[k].tolist() for k in ("open", "high", "low", "close")}
            kw = dict(entry_n=55, exit_n=20, atr_n=20, stop_mult=2.0, rate=COSTS.one_way_rate(), trade_start=strat.trade_start, warmup_bars=0)
            for seed_kind in ("sma", "first_tr"):
                r = ref.run_b1(list(df.index), cols["open"], cols["high"], cols["low"], cols["close"], n_seed=seed_kind, **kw)
                _, mism = ref.compare(r, res.with_open_mtm())
                if seed_kind == "sma":
                    mism_sma += len(mism); trades += len(r)
                else:
                    mism_first += len(mism)
        self.assertGreater(trades, 50)
        self.assertGreater(mism_sma, 0, "시드 차이로 불일치가 나와야 설명이 성립한다")
        self.assertEqual(mism_first, 0)


class TestRealTickerMatch(unittest.TestCase):
    def test_matches_on_validation_tickers_full_history(self):
        have = set(store.cached_symbols())
        if not set(CHECK_TICKERS) <= have:
            self.skipTest("수집 캐시 없음")
        for sym in CHECK_TICKERS:
            with self.subTest(sym=sym):
                n, matched, mism = compare_one(store.load_ohlcv(sym), trade_start="2015-01-01")
                self.assertGreater(n, 20)
                self.assertEqual(mism, [], f"{sym}: 불일치 {len(mism)}건: {mism[:2]}")
                self.assertEqual(matched, n)


if __name__ == "__main__":
    unittest.main()
