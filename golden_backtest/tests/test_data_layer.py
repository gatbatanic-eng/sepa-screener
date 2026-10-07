"""마감 컷오프, 결측 정리, 이상치·A1 이력 검사 (네트워크 불필요)."""
from __future__ import annotations

import unittest
from datetime import datetime

import numpy as np
import pandas as pd

from golden_backtest.data import calendar, clean, quality
from golden_backtest.data.providers import fdr_provider as prov


class TestCutoff(unittest.TestCase):
    def ny(self, y, m, d, h, mi):
        return datetime(y, m, d, h, mi, tzinfo=calendar.NY)

    def test_before_close_final_uses_previous_weekday(self):
        # 2026-10-07(수) 15:00 ET → 전날 10-06
        self.assertEqual(calendar.latest_closed_us_session(self.ny(2026, 10, 7, 15, 0)), pd.Timestamp("2026-10-06"))

    def test_after_close_final_uses_today(self):
        self.assertEqual(calendar.latest_closed_us_session(self.ny(2026, 10, 7, 16, 10)), pd.Timestamp("2026-10-07"))
        self.assertEqual(calendar.latest_closed_us_session(self.ny(2026, 10, 7, 16, 9)), pd.Timestamp("2026-10-06"))

    def test_weekend_and_monday_morning_roll_back_to_friday(self):
        self.assertEqual(calendar.latest_closed_us_session(self.ny(2026, 10, 10, 12, 0)), pd.Timestamp("2026-10-09"))
        self.assertEqual(calendar.latest_closed_us_session(self.ny(2026, 10, 12, 9, 0)), pd.Timestamp("2026-10-09"))


class TestClean(unittest.TestCase):
    def frame(self):
        idx = pd.to_datetime(["2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07"])
        return pd.DataFrame({"Open": 10.0, "High": 11.0, "Low": 9.0, "Close": [10, 10, np.nan, 10, 10],
                             "Adj Close": [10, 10, np.nan, 10, 10], "Volume": 100.0}, index=idx)

    def test_unclosed_bar_and_nan_rows_are_dropped_and_reported(self):
        out, info = clean.clean_ohlcv(self.frame(), pd.Timestamp("2026-10-06"))
        self.assertEqual(info["unclosed_dropped"], 1)           # 10-07 장중 봉
        self.assertEqual(info["nan_dropped"], ["2026-10-05"])   # 조용히 채우지 않고 기록
        self.assertEqual(list(out.index.strftime("%Y-%m-%d")), ["2026-10-01", "2026-10-02", "2026-10-06"])

    def test_nonpositive_price_row_is_dropped(self):
        f = self.frame()
        f.loc["2026-10-05", ["Close", "Adj Close"]] = 0.0
        _, info = clean.clean_ohlcv(f, pd.Timestamp("2026-10-06"))
        self.assertIn("2026-10-05", info["nan_dropped"])

    def test_tz_aware_and_duplicate_index_normalized(self):
        f = self.frame().dropna()
        f.index = f.index.tz_localize("America/New_York")
        f = pd.concat([f, f.iloc[[0]]])
        out, _ = clean.clean_ohlcv(f, pd.Timestamp("2026-10-06"))
        self.assertTrue(out.index.is_unique)
        self.assertIsNone(out.index.tz)


class TestSymbolVariant(unittest.TestCase):
    def test_dual_class(self):
        self.assertEqual(prov.dual_class_variant("BRKB"), "BRK-B")
        self.assertEqual(prov.dual_class_variant("BFB"), "BF-B")
        self.assertEqual(prov.dual_class_variant("BRK.B"), "BRK-B")


def _frame(close, volume=None):
    idx = pd.bdate_range("2013-12-02", periods=len(close))
    close = pd.Series(close, index=idx, dtype=float)
    vol = pd.Series(1000.0 if volume is None else volume, index=idx, dtype=float)
    return pd.DataFrame({"close": close, "volume": vol})


class TestAnomalies(unittest.TestCase):
    def test_counts_only_since_date_and_flags_ath(self):
        # 2013-12-02부터 일봉. 2014-01-01 이전 구간의 큰 등락은 세지 않는다
        close = [100, 150] + [150] * 20 + [150, 300, 150] + [150] * 5   # 150(+50%, 2013 → 제외), 300(+100%, 2014 이후 → 신고가), 150(-50%)
        df = _frame(close)
        res = quality.anomalies_since(df, "2014-01-01", 0.40)
        dates = [m["date"] for m in res["big_moves"]]
        self.assertEqual(len(res["big_moves"]), 2)
        up, down = res["big_moves"]
        self.assertTrue(up["sets_new_ath"])
        self.assertTrue(up["holds_current_ath"])
        self.assertFalse(down["sets_new_ath"])
        self.assertNotIn("2013-12-03", dates)

    def test_adjustment_artifact_flag(self):
        # 수정 종가만 +60% 뛰고 공급자 Close는 +4%: 분리상장 보정 오류(DHR 2016-07 유형)
        df = _frame([100.0] * 25 + [160.0] * 5)
        df["v_close"] = [100.0] * 25 + [104.0] * 5
        move = quality.anomalies_since(df, "2014-01-01", 0.40)["big_moves"][0]
        self.assertTrue(move["adjustment_artifact"])
        self.assertTrue(move["sets_new_ath"])
        df["v_close"] = df["close"]  # 두 시계열이 같이 움직이면 실제 가격 변동
        self.assertFalse(quality.anomalies_since(df, "2014-01-01", 0.40)["big_moves"][0]["adjustment_artifact"])

    def test_zero_volume_since(self):
        vol = [0.0] * 3 + [1000.0] * 40
        vol[30] = 0.0
        res = quality.anomalies_since(_frame([100.0] * 43, vol), "2014-01-01", 0.4)
        self.assertEqual(res["zero_volume"], 1)  # 2013년의 0은 제외, 2014년 이후 1건


if __name__ == "__main__":
    unittest.main()
