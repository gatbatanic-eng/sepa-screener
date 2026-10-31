"""롱베이스 돌파 탐지 검증 — 합성 데이터, 네트워크 불필요.

실행:  python tests/test_longbase.py   (또는 pytest)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from longbase.detect import find_longbase_breakout  # noqa: E402


def make(base_days=150, depth=0.2, bo_vol=3.0, bo_ret=0.04, tail=2, contract=True, uptrend=True, seed=1):
    """상승 → 좌측 고점 100 → base_days 박스권(수축) → 돌파일 + tail 일 유지."""
    rng = np.random.default_rng(seed)
    pre = np.linspace(40, 100, 260) if uptrend else np.linspace(100, 100, 260)
    prices = list(pre)
    n_base = base_days
    lo = 100 * (1 - depth)
    mid = (100 + lo) / 2 - 2
    for i in range(n_base):
        amp = (100 - lo) / 2 * 0.8 * (1 - 0.7 * i / n_base if contract else 1.0)
        prices.append(mid + amp * np.sin(i / 9.0) - 3)
    prices = np.array(prices)
    close = prices.copy()
    # 돌파일 + 유지
    close = np.append(close, close[-1])
    bo_close = 100 * (1 + bo_ret) + 0.5
    close[-1] = bo_close
    for _ in range(tail):
        close = np.append(close, bo_close * (1 + 0.003))
    n = len(close)
    spread = np.where(np.arange(n) < 260 + n_base, 0.012 * (1 - (0.6 if contract else 0.0) * np.clip((np.arange(n) - 260) / n_base, 0, 1)), 0.012)
    high = close * (1 + spread / 2)
    low = close * (1 - spread / 2)
    high[:260] = np.maximum(high[:260], close[:260])
    # 좌측 고점: 베이스 직전 바(259)가 정확히 100 이상
    high[259] = 100.0
    close[259] = 99.5
    low[259] = 98.5
    # 돌파일 종가 위치 상단, 거래량
    b = n - 1 - tail
    high[b] = close[b] * 1.003
    low[b] = close[b] * 0.985
    vol = np.full(n, 1_000_000.0) * (1 + 0.1 * rng.random(n))
    vol[b] = 1_000_000 * bo_vol
    idx = pd.bdate_range(end="2026-10-30", periods=n)
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol}, index=idx)


class LongBaseTest(unittest.TestCase):
    def test_detects_valid_breakout(self):
        bo = find_longbase_breakout(make())
        self.assertIsNotNone(bo)
        self.assertGreaterEqual(bo.base_days, 120)
        self.assertLessEqual(bo.depth_pct, 35)
        self.assertEqual(bo.days_ago, 2)
        self.assertGreater(bo.vol_ratio, 1.5)

    def test_short_base_rejected(self):
        self.assertIsNone(find_longbase_breakout(make(base_days=80)))

    def test_deep_base_rejected(self):
        self.assertIsNone(find_longbase_breakout(make(depth=0.5)))

    def test_low_volume_rejected(self):
        self.assertIsNone(find_longbase_breakout(make(bo_vol=1.1)))

    def test_old_breakout_rejected(self):
        self.assertIsNone(find_longbase_breakout(make(tail=15)))

    def test_extended_rejected(self):
        self.assertIsNone(find_longbase_breakout(make(bo_ret=0.15)))

    def test_no_contraction_rejected(self):
        self.assertIsNone(find_longbase_breakout(make(contract=False)))

    def test_no_lookahead(self):
        """돌파 이후 바를 잘라내도 돌파일 판정 입력(b 이하)은 동일해야 한다."""
        full = make(tail=3)
        cut = full.iloc[:-2]
        a, c = find_longbase_breakout(full), find_longbase_breakout(cut)
        self.assertIsNotNone(a); self.assertIsNotNone(c)
        self.assertEqual(a.breakout_date, c.breakout_date)
        self.assertAlmostEqual(a.pivot, c.pivot)


if __name__ == "__main__":
    unittest.main()
