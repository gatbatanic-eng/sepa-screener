"""technical_signals/indicators.py 단위 테스트 + look-ahead 회귀 테스트."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from indicators import (  # noqa: E402
    adx, bollinger, bollinger_bandwidth, crossed_down_recent, crossed_up_recent,
    disparity, ema, macd, obv, rsi, sma, stochastic,
)


def _dates(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2024-01-01", periods=n, freq="B")


def _mk(close, high=None, low=None, vol=None) -> dict:
    n = len(close)
    idx = _dates(n)
    close = pd.Series(close, index=idx, dtype=float)
    high = pd.Series(high, index=idx, dtype=float) if high is not None else close * 1.01
    low = pd.Series(low, index=idx, dtype=float) if low is not None else close * 0.99
    vol = pd.Series(vol, index=idx, dtype=float) if vol is not None else pd.Series([1_000_000.0] * n, index=idx)
    return {"close": close, "high": high, "low": low, "volume": vol}


class TestBasicIndicators(unittest.TestCase):
    def test_sma_matches_manual_mean(self):
        s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
        out = sma(s, 3)
        self.assertTrue(pd.isna(out.iloc[0]))
        self.assertTrue(pd.isna(out.iloc[1]))
        self.assertAlmostEqual(out.iloc[2], 2.0)
        self.assertAlmostEqual(out.iloc[3], 3.0)
        self.assertAlmostEqual(out.iloc[4], 4.0)

    def test_ema_is_causal_and_converges_toward_recent_values(self):
        s = pd.Series([10.0] * 50 + [20.0] * 50)
        out = ema(s, 10)
        self.assertAlmostEqual(out.iloc[9], 10.0, places=4)
        self.assertGreater(out.iloc[-1], 19.0)  # 충분히 수렴

    def test_rsi_all_up_is_100_all_down_is_0(self):
        up = pd.Series(np.linspace(100, 200, 40))
        r_up = rsi(up, 14)
        self.assertAlmostEqual(r_up.dropna().iloc[-1], 100.0, places=2)

        down = pd.Series(np.linspace(200, 100, 40))
        r_down = rsi(down, 14)
        self.assertAlmostEqual(r_down.dropna().iloc[-1], 0.0, places=2)

    def test_rsi_flat_series_is_neutral_50(self):
        flat = pd.Series([100.0] * 40)
        r = rsi(flat, 14)
        self.assertAlmostEqual(r.dropna().iloc[-1], 50.0, places=2)

    def test_macd_zero_when_flat(self):
        flat = pd.Series([100.0] * 60)
        macd_line, signal_line, hist = macd(flat, 12, 26, 9)
        self.assertAlmostEqual(macd_line.dropna().iloc[-1], 0.0, places=6)
        self.assertAlmostEqual(hist.dropna().iloc[-1], 0.0, places=6)

    def test_stochastic_bounds_0_100(self):
        rng = np.random.default_rng(0)
        close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 60)))
        high = close + 1.0
        low = close - 1.0
        k, d = stochastic(high, low, close, 14, 3, 3)
        valid_k = k.dropna()
        self.assertTrue((valid_k >= -1e-9).all() and (valid_k <= 100 + 1e-9).all())

    def test_bollinger_upper_above_lower(self):
        rng = np.random.default_rng(1)
        close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 60)))
        mid, upper, lower = bollinger(close, 20, 2.0)
        valid = mid.notna()
        self.assertTrue((upper[valid] >= mid[valid]).all())
        self.assertTrue((lower[valid] <= mid[valid]).all())

    def test_bollinger_bandwidth_positive(self):
        rng = np.random.default_rng(2)
        close = pd.Series(100 + np.cumsum(rng.normal(0, 1, 60)))
        mid, upper, lower = bollinger(close, 20, 2.0)
        bw = bollinger_bandwidth(mid, upper, lower)
        self.assertTrue((bw.dropna() >= 0).all())

    def test_obv_increases_on_up_day_decreases_on_down_day(self):
        close = pd.Series([10.0, 11.0, 10.5])
        vol = pd.Series([100.0, 200.0, 300.0])
        out = obv(close, vol)
        self.assertEqual(out.iloc[0], 0.0)
        self.assertEqual(out.iloc[1], 200.0)
        self.assertEqual(out.iloc[2], 200.0 - 300.0)

    def test_adx_high_for_strong_one_directional_trend(self):
        n = 80
        close = pd.Series(np.linspace(100, 300, n))
        high = close + 1.0
        low = close - 1.0
        adx_val, plus_di, minus_di = adx(high, low, close, 14)
        self.assertGreater(adx_val.dropna().iloc[-1], 25.0)
        self.assertGreater(plus_di.dropna().iloc[-1], minus_di.dropna().iloc[-1])

    def test_disparity_zero_when_price_equals_ma(self):
        close = pd.Series([100.0] * 30)
        ma = sma(close, 20)
        d = disparity(close, ma)
        self.assertAlmostEqual(d.dropna().iloc[-1], 0.0, places=6)


class TestCrossHelpers(unittest.TestCase):
    def test_crossed_up_recent_detects_cross_within_window(self):
        a = pd.Series([1, 1, 1, 3, 3, 3, 3])
        b = pd.Series([2, 2, 2, 2, 2, 2, 2])
        out = crossed_up_recent(a, b, window=3)
        # 3번째 인덱스(0-based)에서 교차, window=3 이므로 인덱스 3~5까지는 True, 6은 window 밖
        self.assertFalse(out.iloc[2])
        self.assertTrue(out.iloc[3])
        self.assertTrue(out.iloc[5])
        self.assertFalse(out.iloc[6])

    def test_crossed_down_recent_is_mirror_of_up(self):
        a = pd.Series([3, 3, 3, 1, 1, 1])
        b = pd.Series([2, 2, 2, 2, 2, 2])
        out = crossed_down_recent(a, b, window=2)
        self.assertTrue(out.iloc[3])
        self.assertTrue(out.iloc[4])
        self.assertFalse(out.iloc[5])

    def test_no_cross_when_never_crosses(self):
        a = pd.Series([1, 1, 1, 1, 1])
        b = pd.Series([5, 5, 5, 5, 5])
        out = crossed_up_recent(a, b, window=5)
        self.assertFalse(out.any())


class TestNoLookahead(unittest.TestCase):
    """인덱스 i 시점 값은 i까지의 데이터로 잘라도(truncate) 동일해야 한다(인과성)."""

    def setUp(self):
        rng = np.random.default_rng(42)
        n = 120
        self.close = pd.Series(100 + np.cumsum(rng.normal(0, 1.2, n)))
        self.high = self.close + rng.uniform(0.1, 1.0, n)
        self.low = self.close - rng.uniform(0.1, 1.0, n)
        self.volume = pd.Series(rng.uniform(500_000, 1_500_000, n))
        self.cut = 90  # 이 위치까지의 값을 비교

    def _assert_prefix_stable(self, full: pd.Series, truncated: pd.Series, cut: int, **kwargs):
        tail = min(3, cut)
        for i in range(cut - tail, cut):
            f, t = full.iloc[i], truncated.iloc[i]
            if pd.isna(f) and pd.isna(t):
                continue
            self.assertAlmostEqual(f, t, places=6, msg=f"index {i}: full={f} truncated={t}", **kwargs)

    def test_sma_ema_no_lookahead(self):
        c_full, c_trunc = self.close, self.close.iloc[: self.cut + 1]
        self._assert_prefix_stable(sma(c_full, 20), sma(c_trunc, 20), self.cut)
        self._assert_prefix_stable(ema(c_full, 20), ema(c_trunc, 20), self.cut)

    def test_rsi_no_lookahead(self):
        c_full, c_trunc = self.close, self.close.iloc[: self.cut + 1]
        self._assert_prefix_stable(rsi(c_full, 14), rsi(c_trunc, 14), self.cut)

    def test_macd_no_lookahead(self):
        c_full, c_trunc = self.close, self.close.iloc[: self.cut + 1]
        m1, s1, h1 = macd(c_full, 12, 26, 9)
        m2, s2, h2 = macd(c_trunc, 12, 26, 9)
        self._assert_prefix_stable(m1, m2, self.cut)
        self._assert_prefix_stable(s1, s2, self.cut)

    def test_stochastic_no_lookahead(self):
        cut = self.cut
        k1, d1 = stochastic(self.high, self.low, self.close, 14, 3, 3)
        k2, d2 = stochastic(self.high.iloc[:cut+1], self.low.iloc[:cut+1], self.close.iloc[:cut+1], 14, 3, 3)
        self._assert_prefix_stable(k1, k2, cut)
        self._assert_prefix_stable(d1, d2, cut)

    def test_bollinger_no_lookahead(self):
        c_full, c_trunc = self.close, self.close.iloc[: self.cut + 1]
        mid1, up1, lo1 = bollinger(c_full, 20, 2.0)
        mid2, up2, lo2 = bollinger(c_trunc, 20, 2.0)
        self._assert_prefix_stable(mid1, mid2, self.cut)
        self._assert_prefix_stable(up1, up2, self.cut)

    def test_obv_no_lookahead(self):
        c_full, c_trunc = self.close, self.close.iloc[: self.cut + 1]
        v_full, v_trunc = self.volume, self.volume.iloc[: self.cut + 1]
        self._assert_prefix_stable(obv(c_full, v_full), obv(c_trunc, v_trunc), self.cut)

    def test_adx_no_lookahead(self):
        cut = self.cut
        a1, p1, m1 = adx(self.high, self.low, self.close, 14)
        a2, p2, m2 = adx(self.high.iloc[:cut+1], self.low.iloc[:cut+1], self.close.iloc[:cut+1], 14)
        self._assert_prefix_stable(a1, a2, cut)

    def test_crossed_up_recent_no_lookahead(self):
        cut = self.cut
        sma_fast_full = sma(self.close, 10)
        sma_slow_full = sma(self.close, 30)
        full = crossed_up_recent(sma_fast_full, sma_slow_full, 5)

        c_trunc = self.close.iloc[: cut + 1]
        sma_fast_t = sma(c_trunc, 10)
        sma_slow_t = sma(c_trunc, 30)
        trunc = crossed_up_recent(sma_fast_t, sma_slow_t, 5)

        for i in range(cut - 2, cut):
            self.assertEqual(bool(full.iloc[i]), bool(trunc.iloc[i]), msg=f"index {i}")


if __name__ == "__main__":
    unittest.main()
