"""캐시 보호: 재수집 결과가 기존 캐시보다 행이 적거나 과거 값이 바뀌었는지 판정한다."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from golden_backtest.data import cache_guard


def _frame(n=30, scale=1.0):
    idx = pd.bdate_range("2024-01-02", periods=n)
    close = pd.Series(100 * np.exp(np.cumsum(np.full(n, 0.002))), index=idx)
    return pd.DataFrame({"close": close * scale, "raw_close": close})


class TestCompareFrames(unittest.TestCase):
    def test_identical_is_ok(self):
        self.assertIsNone(cache_guard.compare_frames(_frame(), _frame()))

    def test_new_bars_appended_is_ok(self):
        self.assertIsNone(cache_guard.compare_frames(_frame(30), _frame(35)))

    def test_fewer_rows_is_flagged(self):
        res = cache_guard.compare_frames(_frame(30), _frame(1))
        self.assertIn("행 감소", res["reasons"])  # WBD: 공급자가 갑자기 1행만 반환
        self.assertEqual((res["rows_old"], res["rows_new"]), (30, 1))

    def test_missing_date_in_middle_is_flagged(self):
        new = _frame(30).drop(_frame(30).index[10])
        res = cache_guard.compare_frames(_frame(30), new)
        self.assertIn("기존 날짜 소실", res["reasons"])
        self.assertEqual(res["first_missing_date"], _frame(30).index[10].date().isoformat())

    def test_changed_raw_close_is_flagged(self):
        new = _frame(30)
        new.iloc[5, new.columns.get_loc("raw_close")] *= 1.01
        res = cache_guard.compare_frames(_frame(30), new)
        self.assertIn("비수정 종가 변경", res["reasons"])
        self.assertEqual(res["first_changed_date"], new.index[5].date().isoformat())

    def test_changed_adjusted_return_is_flagged(self):
        new = _frame(30)
        new.iloc[15:, new.columns.get_loc("close")] *= 1.05  # 15번째 봉부터 수정 종가가 계단식으로 변경 → 그 날 수익률이 바뀐다
        res = cache_guard.compare_frames(_frame(30), new)
        self.assertIn("수정 종가 수익률 변경", res["reasons"])

    def test_new_dividend_scaling_all_history_is_not_a_change(self):
        # 새 배당이 생기면 수정 종가의 과거 전 구간에 같은 배수가 곱해진다. 수익률과 비수정 종가는 그대로라 변경이 아니다
        self.assertIsNone(cache_guard.compare_frames(_frame(30, 1.0), _frame(30, 0.98)))

    def test_nan_raw_close_in_either_frame_is_skipped(self):
        new = _frame(30)
        new["raw_close"] = np.nan  # 분할 조회 실패로 비수정 종가를 못 만든 경우
        self.assertIsNone(cache_guard.compare_frames(_frame(30), new))


if __name__ == "__main__":
    unittest.main()
