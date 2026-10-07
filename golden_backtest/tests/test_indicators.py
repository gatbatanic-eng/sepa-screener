"""지표 손계산 기대값 검증. 기대값은 코드가 아니라 종이 계산에서 나왔다."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from golden_backtest import indicators as ind

nan = np.nan


def S(values):
    return pd.Series(values, dtype=float)


class TestBasic(unittest.TestCase):
    def test_sma(self):
        out = ind.sma(S([1, 2, 3, 4, 5]), 3)
        np.testing.assert_allclose(out.values, [nan, nan, 2, 3, 4], equal_nan=True)

    def test_ema(self):
        # span=3 → alpha=0.5. e0=1, e1=1.5, e2=2.25, e3=3.125. min_periods=3 → 앞 두 개 NaN
        out = ind.ema(S([1, 2, 3, 4]), 3)
        np.testing.assert_allclose(out.values, [nan, nan, 2.25, 3.125], equal_nan=True)

    def test_true_range_and_atr(self):
        high = S([10, 12, 11, 15])
        low = S([8, 9, 9, 10])
        close = S([9, 11, 10, 14])
        # TR0=10-8=2, TR1=max(3,|12-9|=3,|9-9|=0)=3, TR2=max(2,|11-11|=0,|9-11|=2)=2, TR3=max(5,|15-10|=5,|10-10|=0)=5
        np.testing.assert_allclose(ind.true_range(high, low, close).values, [2, 3, 2, 5])
        # n=2 → alpha=0.5, adjust=False: a0=2, a1=2.5, a2=2.25, a3=3.625 ; min_periods=2 → 첫 값 NaN
        np.testing.assert_allclose(ind.atr(high, low, close, 2).values, [nan, 2.5, 2.25, 3.625], equal_nan=True)

    def test_insufficient_data_is_nan_not_filled(self):
        self.assertTrue(ind.sma(S([1, 2]), 5).isna().all())
        self.assertTrue(ind.rsi(S([1, 2]), 14).isna().all())


class TestRsi(unittest.TestCase):
    def test_rsi_hand_computed(self):
        # close 10,11,10,12 → diff NaN,+1,-1,+2. alpha=0.5. avg_gain: p1=1(미달),p2=0.5,p3=1.25 / avg_loss: p1=0(미달),p2=0.5,p3=0.25
        # p2: RS=1 → 50. p3: RS=5 → 100-100/6 = 83.3333
        out = ind.rsi(S([10, 11, 10, 12]), 2)
        np.testing.assert_allclose(out.values, [nan, nan, 50.0, 100 - 100 / 6], equal_nan=True)

    def test_rsi_all_up_is_100_and_flat_is_50(self):
        np.testing.assert_allclose(ind.rsi(S([1, 2, 3, 4]), 2).values[2:], [100, 100])
        np.testing.assert_allclose(ind.rsi(S([5, 5, 5, 5]), 2).values[2:], [50, 50])


class TestChannels(unittest.TestCase):
    def test_donchian_excludes_today(self):
        high = S([1, 3, 2, 5, 4])
        # t=2: max(1,3)=3, t=3: max(3,2)=3, t=4: max(2,5)=5  (오늘 봉은 제외)
        np.testing.assert_allclose(ind.donchian_high(high, 2).values, [nan, nan, 3, 3, 5], equal_nan=True)

    def test_donchian_low(self):
        low = S([5, 3, 4, 1, 2])
        np.testing.assert_allclose(ind.donchian_low(low, 2).values, [nan, nan, 3, 3, 1], equal_nan=True)

    def test_breakout_compares_against_prior_highs_only(self):
        # 오늘 고가가 새 최고가여도 오늘의 돌파선(직전 n봉 고가)보다 높아야 '돌파'다 — 자기 자신과 비교하지 않는다
        high = S([10, 10, 10, 12])
        self.assertTrue((high > ind.donchian_high(high, 3)).iloc[3])
        self.assertFalse((high > ind.donchian_high(high, 3)).iloc[:3].any())


class TestAdr(unittest.TestCase):
    def test_adr_pct(self):
        high, low = S([11, 22, 33]), S([10, 20, 30])  # 비율 1.1 1.1 1.1
        np.testing.assert_allclose(ind.adr_pct(high, low, 2).values, [nan, 10.0, 10.0], equal_nan=True)


class TestRsPct(unittest.TestCase):
    def test_relative_return_and_rank(self):
        idx = pd.bdate_range("2020-01-01", periods=3)
        close = pd.DataFrame({"A": [100, 110, 121], "B": [100, 100, 100], "C": [100, 90, 99]}, index=idx, dtype=float)
        bench = pd.Series([100, 105, 110.25], index=idx)
        rel = ind.relative_return(close, bench, 2)
        # 2일 수익률: A 21%, B 0%, C -1% / 지수 10.25% → A 10.75, B -10.25, C -11.25 (%p, 소수로 0.1075...)
        np.testing.assert_allclose(rel.iloc[2].values, [0.21 - 0.1025, -0.1025, -0.01 - 0.1025])
        self.assertTrue(rel.iloc[:2].isna().all().all())
        pct = ind.rs_pct(rel)
        np.testing.assert_allclose(pct.iloc[2].values, [100.0, 200 / 3, 100 / 3])

    def test_nan_stock_is_excluded_from_that_days_universe(self):
        rel = pd.DataFrame({"A": [0.3], "B": [0.1], "C": [np.nan]})
        np.testing.assert_allclose(ind.rs_pct(rel).iloc[0].values[:2], [100.0, 50.0])
        self.assertTrue(np.isnan(ind.rs_pct(rel).iloc[0, 2]))


if __name__ == "__main__":
    unittest.main()
