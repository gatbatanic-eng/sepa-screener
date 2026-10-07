"""분할·배당 보정 검증: 합성 손계산(항상 실행) + 수집 캐시 불변식(캐시가 있을 때만 실행)."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from golden_backtest.data import adjust, store


def _vendor(close, adj=None, n=None):
    n = len(close)
    idx = pd.bdate_range("2021-03-01", periods=n)
    close = pd.Series(close, index=idx, dtype=float)
    adj = close if adj is None else pd.Series(adj, index=idx, dtype=float)
    return pd.DataFrame({"Open": close * 0.99, "High": close * 1.02, "Low": close * 0.98, "Close": close,
                         "Adj Close": adj, "Volume": 1000.0})


class TestCumulativeSplitFactor(unittest.TestCase):
    def setUp(self):
        self.idx = pd.bdate_range("2021-03-01", periods=5)

    def test_no_splits(self):
        np.testing.assert_allclose(adjust.cumulative_split_factor(self.idx, None).values, 1.0)
        np.testing.assert_allclose(adjust.cumulative_split_factor(self.idx, pd.Series(dtype=float)).values, 1.0)

    def test_single_split_ex_date_bar_is_already_post_split(self):
        splits = pd.Series([2.0], index=[self.idx[2]])
        # 분할일(idx2) 당일 봉은 분할 후 가격이라 곱하지 않는다. 그 전날까지만 ×2
        np.testing.assert_allclose(adjust.cumulative_split_factor(self.idx, splits).values, [2, 2, 1, 1, 1])

    def test_two_splits_compound(self):
        splits = pd.Series([4.0, 3.0], index=[self.idx[1], self.idx[3]])
        np.testing.assert_allclose(adjust.cumulative_split_factor(self.idx, splits).values, [12, 3, 3, 1, 1])

    def test_reverse_split(self):
        splits = pd.Series([0.1], index=[self.idx[2]])  # 1:10 병합 → 분할 전 가격은 ×0.1
        np.testing.assert_allclose(adjust.cumulative_split_factor(self.idx, splits).values, [0.1, 0.1, 1, 1, 1])

    def test_split_before_data_start_does_not_matter(self):
        splits = pd.Series([2.0], index=[self.idx[0] - pd.Timedelta(days=30)])
        np.testing.assert_allclose(adjust.cumulative_split_factor(self.idx, splits).values, 1.0)


class TestBuildAdjusted(unittest.TestCase):
    def test_full_adjusted_ohlc_uses_adj_over_close_ratio(self):
        v = _vendor([100.0, 100.0], adj=[50.0, 100.0])
        v["Open"], v["High"], v["Low"] = [100.0, 100.0], [110.0, 110.0], [90.0, 90.0]
        out = adjust.build_adjusted(v, None)
        # 첫 봉 비율 0.5 → O 50, H 55, L 45, C 50 / 둘째 봉 비율 1 → 그대로
        np.testing.assert_allclose(out.iloc[0][["open", "high", "low", "close"]].values, [50, 55, 45, 50])
        np.testing.assert_allclose(out.iloc[1][["open", "high", "low", "close"]].values, [100, 110, 90, 100])

    def test_raw_close_reversed_from_splits(self):
        v = _vendor([25.0, 26.0, 27.0, 28.0, 29.0])
        splits = pd.Series([2.0], index=[v.index[2]])
        out = adjust.build_adjusted(v, splits)
        np.testing.assert_allclose(out["raw_close"].values, [50, 52, 27, 28, 29])

    def test_dividend_adjustment_does_not_touch_raw_close(self):
        v = _vendor([100.0, 100.0], adj=[90.0, 100.0])
        out = adjust.build_adjusted(v, None)
        np.testing.assert_allclose(out["raw_close"].values, [100, 100])
        np.testing.assert_allclose(out["close"].values, [90, 100])

    def test_splits_lookup_failure_leaves_raw_close_nan(self):
        out = adjust.build_adjusted(_vendor([10.0, 11.0]), None, splits_ok=False)
        self.assertTrue(out["raw_close"].isna().all())  # 분할이 없다고 가정하지 않는다
        self.assertFalse(out["close"].isna().any())

    def test_dollar_volume_is_split_invariant(self):
        # 2:1 분할 전후로 가격은 절반, 거래량은 두 배(공급자 보정) → 거래대금 동일
        v = _vendor([50.0, 25.0])
        v["Volume"] = [1000.0, 2000.0]
        out = adjust.build_adjusted(v, None)
        self.assertAlmostEqual(out["dollar_volume"].iloc[0], out["dollar_volume"].iloc[1])


# --- 수집 캐시가 있을 때만 도는 불변식 테스트. 기억에 의존한 고정 가격은 쓰지 않는다. ---
# 허용 오차 근거: 분할일 당일 종목의 실제 가격 변동분만큼 어긋난다. 전 종목 분할 1,500건 중 99.6%가 25% 안이었다.
# 25%를 넘는 몇 건은 Yahoo가 분할로 기록한 합병·분리상장(예: JCI 2007, EXPE 2011, TMUS 2013)이라 당일 변동이 크다.
SPLIT_DAY_TOL = 0.25
SPLIT_SHARE_MIN = 0.99
NAMED = ("AAPL", "NVDA", "TSLA")  # 분할이 잦은 대표 종목. 전체 통계와 별도로 엄격하게 본다


def _load_splits(sym: str) -> pd.Series:
    path = store.SPLITS_DIR / f"{sym}.json"
    if not path.exists():
        return pd.Series(dtype=float)
    import json
    raw = json.loads(path.read_text(encoding="utf-8"))
    return pd.Series({pd.Timestamp(k): float(v) for k, v in raw.items()}, dtype=float)


def _naive_factor(day: pd.Timestamp, splits: pd.Series) -> float:
    """adjust.cumulative_split_factor와 다른 방식(단순 반복 곱)으로 같은 값을 구한다."""
    f = 1.0
    for ex, ratio in splits.items():
        if ex > day:
            f *= ratio
    return f


def _split_day_deviations(sym: str):
    """[(ex-date, 분할 비율, |전일 비수정 종가 / 당일 비수정 종가 ÷ 분할 비율 − 1|)], 첫 봉 이전 분할은 제외."""
    df, splits = store.load_ohlcv(sym), _load_splits(sym)
    out = []
    for ex, ratio in splits.items():
        if ex not in df.index or df.index.get_loc(ex) == 0:
            continue
        i = df.index.get_loc(ex)
        out.append((ex, ratio, abs(df["raw_close"].iloc[i - 1] / df["raw_close"].iloc[i] / ratio - 1.0)))
    return out


class TestSplitInvariantsOnCache(unittest.TestCase):
    def setUp(self):
        if not store.cached_symbols():
            self.skipTest("수집 캐시 없음 — python -m golden_backtest.data.build 후 실행")

    def test_raw_close_divided_by_cumulative_factor_equals_split_adjusted_close(self):
        # 불변식 1: 비수정 종가 ÷ 누적 분할계수 = 분할 보정 종가(공급자 Close). 계수는 별도 구현(반복 곱)으로 다시 구한다
        for sym in store.cached_symbols()[::25]:  # 20종목 표본
            df, splits = store.load_ohlcv(sym), _load_splits(sym)
            days = df.index[:: max(1, len(df) // 40)]
            for day in days:
                with self.subTest(sym=sym, day=day.date()):
                    got = df.loc[day, "raw_close"] / _naive_factor(day, splits)
                    self.assertAlmostEqual(got / df.loc[day, "v_close"], 1.0, places=9)

    def test_split_day_ratio_matches_split_ratio_for_named_tickers(self):
        # 불변식 2: 분할 전일/분할일 비수정 종가 비율 ≈ 분할 비율. 허용 오차는 SPLIT_DAY_TOL(분할일 당일 가격 변동)
        checked = 0
        for sym in NAMED:
            if sym not in store.cached_symbols():
                continue
            for ex, ratio, dev in _split_day_deviations(sym):
                checked += 1
                with self.subTest(sym=sym, ex=ex.date(), ratio=ratio):
                    self.assertLessEqual(dev, SPLIT_DAY_TOL)
        self.assertGreater(checked, 0)

    def test_split_day_ratio_holds_for_nearly_all_splits_in_universe(self):
        devs = [d for sym in store.cached_symbols() for _, _, d in _split_day_deviations(sym)]
        self.assertGreater(len(devs), 100)
        share = sum(d <= SPLIT_DAY_TOL for d in devs) / len(devs)
        self.assertGreaterEqual(share, SPLIT_SHARE_MIN, f"{len(devs)}건 중 허용 오차 안 {share:.3%}")

    def test_no_split_days_raw_and_vendor_close_move_together(self):
        # 불변식 3: 분할이 없는 날은 비수정 종가 등락 = 공급자 Close 등락
        for sym in ("AAPL", "NVDA", "TSLA", "MSFT"):
            if sym not in store.cached_symbols():
                continue
            df, splits = store.load_ohlcv(sym), _load_splits(sym)
            r_raw, r_ven = df["raw_close"].pct_change(), df["v_close"].pct_change()
            ok = pd.Series(True, index=df.index)
            for ex in splits.index:
                pos = df.index.searchsorted(ex)  # 분할일 이후 첫 봉을 제외(휴장일이 분할일이면 다음 개장일, 예: NVDA 2001-09-12 → 09-17)
                if 0 < pos < len(df):
                    ok.iloc[pos] = False
            diff = (r_raw - r_ven).abs()[ok].dropna()
            self.assertLess(float(diff.max()), 1e-9, sym)

    def test_ten_dollar_filter_uses_raw_not_adjusted(self):
        # 분할 종목은 수정 종가가 비수정 종가보다 훨씬 작다. 10달러 필터를 수정 종가에 걸면 틀린다
        df = store.load_ohlcv("NVDA") if "NVDA" in store.cached_symbols() else None
        if df is None:
            self.skipTest("NVDA 캐시 없음")
        gap = df["raw_close"] / df["close"]
        self.assertGreater(float(gap.max()), 5.0)       # 분할 누적으로 큰 격차가 실제로 있고
        self.assertAlmostEqual(float(gap.iloc[-1]), 1.0, delta=0.05)  # 최근에는 배당 보정 정도만 다르다


if __name__ == "__main__":
    unittest.main()
