import unittest

import numpy as np
import pandas as pd

from accumulation import backtest as bt
from accumulation.features import compute


def make(n=320, seed=1, accumulate=True):
    """완만한 상승 추세 + 마지막 25일에 거래량 2배·좁은 변동폭·종가는 고가 근처(accumulate=True)."""
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + 0.002 + rng.normal(0, 0.006, n))
    high, low = close * 1.006, close * 0.994
    vol = np.full(n, 1_000_000.0) * (1 + rng.normal(0, 0.05, n))
    if accumulate:
        close[-25:] = close[-26] * (1 + np.linspace(0, 0.02, 25))
        high[-25:] = close[-25:] * 1.004
        low[-25:] = close[-25:] * 0.994
        vol[-25:] *= 2.0
    idx = pd.bdate_range("2023-06-01", periods=n)
    return pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


class FeatureTest(unittest.TestCase):
    def test_accumulation_pattern_scores_all_four(self):
        f = compute(make())
        last = f.iloc[-1]
        self.assertTrue(last["ok"])
        self.assertEqual(int(last["score"]), 4, last.to_dict())

    def test_flat_volume_fails_A_and_E(self):
        last = compute(make(accumulate=False)).iloc[-1]
        self.assertFalse(last["A"])
        self.assertFalse(last["E"] and last["A"])

    def test_no_lookahead(self):
        df = make()
        full = compute(df)
        cut = compute(df.iloc[:-30])
        pd.testing.assert_frame_equal(full.iloc[:-30].drop(columns=[]), cut)  # 미래 값을 지워도 과거 판정이 같다

    def test_short_history_is_not_ok(self):
        self.assertFalse(compute(make(n=120)).iloc[-1]["ok"])


class BacktestTest(unittest.TestCase):
    def test_forward_return_uses_next_close_and_costs(self):
        c = pd.Series([100.0, 100.0, 110.0, 121.0, 121.0])
        r = bt.forward_returns(c, 1, 0.01)
        self.assertAlmostEqual(r.iloc[0], c.iloc[2] / c.iloc[1] - 1 - 0.01)   # 신호 0 → 진입 1일 종가, 청산 2일 종가
        self.assertTrue(np.isnan(r.iloc[-1]))

    def test_cooldown_counts_a_cluster_once(self):
        panel = pd.DataFrame({"date": pd.bdate_range("2025-01-01", periods=6).tolist() * 2, "code": ["A"] * 6 + ["B"] * 6})
        mask = pd.Series([True, True, True, False, False, True] + [False] * 6)
        keep = bt._first_of_cluster(panel, mask)
        self.assertEqual(keep.tolist(), [True, False, False, False, False, False] + [False] * 6)  # 6번째는 5일 전 신호가 있어 제외

    def test_bootstrap_is_deterministic_and_brackets_mean(self):
        x = np.random.default_rng(3).normal(0.01, 0.02, 120)
        a, b = bt.block_bootstrap_ci(x, 5), bt.block_bootstrap_ci(x, 5)
        self.assertEqual(a, b)
        self.assertLess(a[0], x.mean())
        self.assertGreater(a[1], x.mean())
        self.assertTrue(np.isnan(bt.block_bootstrap_ci(x[:6], 5)[0]))   # 표본이 모자라면 구간을 내지 않는다


if __name__ == "__main__":
    unittest.main()
