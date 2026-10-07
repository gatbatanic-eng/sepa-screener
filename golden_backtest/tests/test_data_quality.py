"""데이터 품질 처리(규격 v1.2): A1 판정 불가, 시작 구간 절단, 연속 결측, 보정 오류 탐지, P1 유니버스 제외. 네트워크 불필요."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from golden_backtest.data import clean, quality
from golden_backtest.data import universe as uni


class TestA1HistoryCheck(unittest.TestCase):
    def _long(self, peak_pos, n=400, start="2013-01-02"):
        close = np.linspace(10, 50, n)
        close[peak_pos] = 1000.0
        idx = pd.bdate_range(start, periods=n)
        return pd.DataFrame({"close": close}, index=idx)

    def test_floor_start_with_peak_in_first_252_bars_is_undeterminable(self):
        res = quality.a1_history_check(self._long(100), "2014-12-31", 252, starts_at_floor=True)
        self.assertEqual(res["status"], "UNDETERMINABLE")

    def test_same_data_but_not_floor_start_is_ok(self):
        # 상장 직후 최고가여도 수집 하한에서 시작하지 않았다면 이력이 온전하다(닷컴 시기 IPO 15종목이 여기에 해당)
        res = quality.a1_history_check(self._long(100), "2014-12-31", 252, starts_at_floor=False)
        self.assertEqual(res["status"], "OK")

    def test_floor_start_with_peak_after_first_252_bars_is_ok(self):
        res = quality.a1_history_check(self._long(300), "2014-12-31", 252, starts_at_floor=True)
        self.assertEqual(res["status"], "OK")
        self.assertEqual(res["ath_pos"], 300)

    def test_boundary_pos_252_is_ok_and_251_is_not(self):
        def status(p):
            return quality.a1_history_check(self._long(p, 500), "2020-12-31", 252, True)["status"]
        self.assertEqual(status(252), "OK")
        self.assertEqual(status(251), "UNDETERMINABLE")

    def test_listed_after_asof_is_not_checked(self):
        df = self._long(10, 100, start="2016-01-04")
        self.assertEqual(quality.a1_history_check(df, "2014-12-31", 252, False)["status"], "NO_DATA_BEFORE_ASOF")

    def test_peak_after_asof_is_ignored(self):
        # asof 이후에 만든 최고가는 판정에 쓰지 않는다(미래 정보)
        df = self._long(399, 700, start="2013-01-02")
        res = quality.a1_history_check(df, "2013-12-31", 252, True)
        self.assertLess(res["bars_to_asof"], 399)
        self.assertLess(res["ath_pos"], res["bars_to_asof"])


def _vendor(rows):
    """rows: (O,H,L,C,V) 리스트 → 공급자 형식 프레임."""
    idx = pd.bdate_range("2010-01-04", periods=len(rows))
    return pd.DataFrame(rows, index=idx, columns=["Open", "High", "Low", "Close", "Volume"])


class TestStripLeadingPlaceholders(unittest.TestCase):
    FLAT = (10, 10, 10, 10, 0)
    REAL = (10, 11, 9, 10.5, 500)

    def test_run_of_five_or_more_is_cut_through_end_of_run(self):
        df, info = clean.strip_leading_placeholders(_vendor([self.FLAT] * 5 + [self.REAL] * 3), 5)
        self.assertEqual(len(df), 3)
        self.assertEqual((info["bars"], info["first"], info["last"]), (5, "2010-01-04", "2010-01-08"))
        self.assertEqual(df.index[0], pd.Timestamp("2010-01-11"))

    def test_run_of_four_is_kept(self):
        df, info = clean.strip_leading_placeholders(_vendor([self.FLAT] * 4 + [self.REAL] * 3), 5)
        self.assertEqual(len(df), 7)
        self.assertIsNone(info)

    def test_only_leading_run_is_cut_not_later_ones(self):
        rows = [self.FLAT] * 6 + [self.REAL] * 2 + [self.FLAT] * 7 + [self.REAL]
        df, info = clean.strip_leading_placeholders(_vendor(rows), 5)
        self.assertEqual(info["bars"], 6)
        self.assertEqual(len(df), 10)  # 중간의 정지 7봉은 그대로

    def test_flat_but_traded_bar_is_not_a_placeholder(self):
        traded_flat = (10, 10, 10, 10, 100)  # O=H=L=C라도 거래량이 있으면 실제 거래다
        _, info = clean.strip_leading_placeholders(_vendor([traded_flat] * 6 + [self.REAL]), 5)
        self.assertIsNone(info)

    def test_zero_volume_but_not_flat_is_not_a_placeholder(self):
        _, info = clean.strip_leading_placeholders(_vendor([(10, 11, 9, 10, 0)] * 6 + [self.REAL]), 5)
        self.assertIsNone(info)

    def test_no_leading_run(self):
        df, info = clean.strip_leading_placeholders(_vendor([self.REAL] * 5), 5)
        self.assertIsNone(info)
        self.assertEqual(len(df), 5)


class TestNanRuns(unittest.TestCase):
    def test_consecutive_removed_rows_are_reported_as_runs(self):
        idx = pd.bdate_range("2026-07-27", periods=12)
        close = [10.0] * 12
        for k in (2, 3, 4, 5, 6, 7, 10):
            close[k] = np.nan
        f = pd.DataFrame({"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": close, "Adj Close": close, "Volume": 1.0}, index=idx)
        _, info = clean.clean_ohlcv(f, pd.Timestamp("2026-12-31"))
        self.assertEqual([r["bars"] for r in info["nan_runs"]], [6, 1])
        self.assertEqual((info["nan_runs"][0]["start"], info["nan_runs"][0]["end"]), ("2026-07-29", "2026-08-05"))


class TestAdjustmentArtifacts(unittest.TestCase):
    def _df(self, close, v_close):
        idx = pd.bdate_range("2016-06-01", periods=len(close))
        return pd.DataFrame({"close": close, "v_close": v_close}, index=idx, dtype=float)

    def test_flags_only_bars_where_returns_diverge_more_than_threshold(self):
        # 3번째 봉: 수정 +30%, 공급자 +3% → 어긋남 27%p. 4번째 봉: +8% vs +3% → 5%p라 제외
        df = self._df([100, 100, 130, 140.4], [100, 100, 103, 106.09])
        res = quality.adjustment_artifacts(df, None, 0.10, 3)
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0]["date"], "2016-06-03")
        self.assertAlmostEqual(res[0]["adj_ret"], 0.30)
        self.assertAlmostEqual(res[0]["vendor_ret"], 0.03)
        self.assertFalse(res[0]["split_event"])

    def test_split_event_within_window_is_marked_explained(self):
        df = self._df([100, 100, 130, 130], [100, 100, 103, 103])
        ex = df.index[2] + pd.Timedelta(days=3)
        near = quality.adjustment_artifacts(df, pd.Series([2.0], index=[ex]), 0.10, 3)
        far = quality.adjustment_artifacts(df, pd.Series([2.0], index=[ex + pd.Timedelta(days=1)]), 0.10, 3)
        self.assertTrue(near[0]["split_event"])
        self.assertEqual(near[0]["split_ratio"], 2.0)
        self.assertFalse(far[0]["split_event"])

    def test_dividend_day_with_small_gap_is_not_flagged(self):
        df = self._df([100, 99.0, 99.5], [100, 98.0, 98.5])  # 배당 보정 1%p 차이
        self.assertEqual(quality.adjustment_artifacts(df, None, 0.10, 3), [])


class TestUniverseExclusions(unittest.TestCase):
    CFG = {"exclude": {
        "static": {"DHR": "보정 오류", "ZZZ": "목록에 없는 종목"},
        "rules": {
            "adjustment_artifact_unexplained_by_split": {"since": "2014-01-01", "reason": "분할로 설명 안 됨"},
            "consecutive_missing": {"since": "2015-01-01", "max_run": 5, "reason": "연속 결측"},
        }}}

    @staticmethod
    def rec(artifacts=(), runs=()):
        return {"artifacts": list(artifacts), "nan_runs": list(runs)}

    def test_static_exclusion_applies_only_to_collected_symbols(self):
        out = uni.compute_exclusions({"DHR": self.rec(), "AAPL": self.rec()}, self.CFG)
        self.assertEqual(list(out), ["DHR"])

    def test_unexplained_artifact_since_cutoff_excludes_but_explained_or_old_does_not(self):
        recs = {
            "A": self.rec([{"date": "2016-03-01", "split_event": False}]),   # 제외
            "B": self.rec([{"date": "2016-03-01", "split_event": True}]),    # 분할로 설명됨
            "C": self.rec([{"date": "2013-12-31", "split_event": False}]),   # 기준일 이전
        }
        self.assertEqual(sorted(uni.compute_exclusions(recs, self.CFG)), ["A"])

    def test_missing_run_must_exceed_five_bars_and_start_in_2015_or_later(self):
        recs = {
            "SIX": self.rec(runs=[{"start": "2026-07-30", "end": "2026-08-06", "bars": 6}]),   # 제외
            "FIVE": self.rec(runs=[{"start": "2026-07-30", "end": "2026-08-05", "bars": 5}]),  # 5봉은 허용
            "OLD": self.rec(runs=[{"start": "2014-12-31", "end": "2015-01-12", "bars": 9}]),   # 2014 시작
        }
        out = uni.compute_exclusions(recs, self.CFG)
        self.assertEqual(sorted(out), ["SIX"])
        self.assertIn("6봉", out["SIX"][0])

    def test_reasons_accumulate(self):
        recs = {"DHR": self.rec([{"date": "2016-07-05", "split_event": False}])}
        self.assertEqual(len(uni.compute_exclusions(recs, self.CFG)["DHR"]), 2)


if __name__ == "__main__":
    unittest.main()
