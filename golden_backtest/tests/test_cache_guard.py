"""캐시 보호: 재수집 결과가 기존 캐시보다 행이 적거나 과거 값이 바뀌었는지 판정한다."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from golden_backtest.data import cache_guard


def _frame(n=30, scale=1.0):
    idx = pd.bdate_range("2024-01-02", periods=n)
    close = pd.Series(100 * np.exp(np.cumsum(np.full(n, 0.002))), index=idx)
    c = close * scale
    return pd.DataFrame({"open": c * 0.995, "high": c * 1.02, "low": c * 0.98, "close": c, "raw_close": close})


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

    def test_revised_high_is_flagged_even_when_close_and_returns_are_unchanged(self):
        # 30봉 중 봉 12의 고가만 +0.1% 소급 수정. 종가·수익률·비수정 종가는 그대로라 예전 검사는 통과했다(55일 최고가 판정이 바뀔 수 있다)
        new = _frame(30)
        new.iloc[12, new.columns.get_loc("high")] *= 1.001
        res = cache_guard.compare_frames(_frame(30), new)
        self.assertIn("봉 내부 가격 비율(시가·고가·저가/종가) 변경", res["reasons"])
        self.assertEqual(res["first_shape_changed_date"], new.index[12].date().isoformat())
        self.assertEqual(res["high_shape_changed_bars"], 1)

    def test_revised_low_and_open_are_flagged_too(self):
        new = _frame(30)
        new.iloc[5, new.columns.get_loc("low")] *= 0.99
        new.iloc[8, new.columns.get_loc("open")] *= 1.01
        res = cache_guard.compare_frames(_frame(30), new)
        self.assertEqual((res["low_shape_changed_bars"], res["open_shape_changed_bars"]), (1, 1))
        self.assertEqual(res["first_shape_changed_date"], new.index[5].date().isoformat())

    def test_tiny_shape_change_below_tolerance_is_ignored(self):
        new = _frame(30)
        new.iloc[12, new.columns.get_loc("high")] *= 1 + 5e-6    # 상대 5e-6 < 1e-5
        self.assertIsNone(cache_guard.compare_frames(_frame(30), new))

    def test_whole_bar_rescaled_by_new_dividend_ratio_keeps_shape(self):
        # 새 배당으로 과거 봉의 O/H/L/C에 같은 배수가 곱해져도 봉 내부 비율은 그대로 → 변경 아님
        new = _frame(30)
        cols = [new.columns.get_loc(c) for c in ("open", "high", "low", "close")]
        new.iloc[:15, cols] = new.iloc[:15, cols] * 0.97
        res = cache_guard.compare_frames(_frame(30), new)
        self.assertNotIn("봉 내부 가격 비율(시가·고가·저가/종가) 변경", (res or {}).get("reasons", []))

    def test_nan_raw_close_in_either_frame_is_skipped(self):
        new = _frame(30)
        new["raw_close"] = np.nan  # 분할 조회 실패로 비수정 종가를 못 만든 경우
        self.assertIsNone(cache_guard.compare_frames(_frame(30), new))


if __name__ == "__main__":
    unittest.main()
