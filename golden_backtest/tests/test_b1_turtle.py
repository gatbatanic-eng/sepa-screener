"""B1 터틀 System 2 손계산 테스트. 규격 값(55/20/20/2)은 손계산이 가능하도록 작은 값(3/3/3/2)으로 바꿔 쓴다. 편도 비용 0.1%.

공통 데이터: 봉 0~7은 고가가 0.1씩 내려가는 횡보. H_i = 101 − 0.1i, L_i = H_i − 2, 시가 = 종가 = H_i − 1.
  → 모든 봉의 TR = 2 (H−L = 2, |H−전일C| = 0.9, |L−전일C| = 1.1), 그래서 N = 2.
  → 신호일 t의 매수 스탑 수준 = 최근 3봉 최고 고가 = H_{t−2}. 다음 봉 고가 H_{t+1} = H_{t−2} − 0.3 < 수준이라 돌파 전에는 체결이 없다.
  신호 봉 7: 수준 = H_5 = 100.5, N = 2 → 초기 손절 = 체결가 − 2×2 = 체결가 − 4.
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from golden_backtest.engine.simulator import simulate
from golden_backtest.indicators import donchian_high
from golden_backtest.strategies.b1_turtle import B1Turtle
from golden_backtest.tests.engine_helpers import COSTS, SPEC
from golden_backtest.tests.lookahead import synthetic_ohlcv


def base_rows():
    rows = []
    for i in range(8):
        h = 101 - 0.1 * i
        rows.append((h - 1, h, h - 2, h - 1))  # O, H, L, C
    return rows


def frame(extra):
    rows = base_rows() + extra
    idx = pd.bdate_range("2024-01-02", periods=len(rows))
    return pd.DataFrame(rows, index=idx, columns=["open", "high", "low", "close"], dtype=float)


def small(**kw):
    return B1Turtle(entry_n=3, exit_n=3, atr_n=3, stop_mult=kw.pop("stop_mult", 2.0), trade_start=kw.pop("trade_start", None))


def run(extra, **kw):
    return simulate(frame(extra), small(**kw), "T", COSTS, SPEC, warmup_bars=0)  # 손계산용 짧은 데이터라 워밍업 게이트를 끈다


class TestB1HandCalc(unittest.TestCase):
    def test_normal_trade_exit_at_20day_low_line(self):
        """신호 봉 7(수준 100.5, N=2). 봉 8: 시가 100.2 < 수준, 고가 102 ≥ 100.5 → 체결 max(100.2, 100.5) = 100.5. 손절 = 100.5 − 4 = 96.5, R = 4.
        봉 8 마감 후 청산선 = 최근 3봉 저가(L6 98.4, L7 98.3, L8 99.9) 최소 = 98.3 → 손절 96.5 → 98.3 (사유 rule).
        봉 9 마감 후 min(98.3, 99.9, 100.5) = 98.3, 봉 10 마감 후 min(99.9, 100.5, 101) = 99.9.
        봉 11: 시가 102.5 > 99.9, 저가 99.5 ≤ 99.9 → 99.9 체결. 비용 = 100.5×0.001 + 99.9×0.001 = 0.1005 + 0.0999 = 0.2004
        r = (99.9 − 100.5 − 0.2004) / 4 = −0.8004 / 4 = −0.2001, 보유 11 − 8 = 3봉"""
        extra = [(100.2, 102, 99.9, 101.8), (101.5, 103, 100.5, 102.5), (102, 104, 101, 103), (102.5, 102.6, 99.5, 100)]
        res = run(extra)
        self.assertEqual(len(res.trades), 1)
        t = res.trades[0]
        self.assertEqual((t.entry_price, t.initial_stop, t.exit_reason, t.hold_days), (100.5, 96.5, "rule", 3))
        self.assertAlmostEqual(t.exit_price, 99.9)
        self.assertAlmostEqual(t.costs, 0.2004)
        self.assertAlmostEqual(t.r_multiple, -0.2001, places=9)
        self.assertEqual((t.strategy, t.entry_model, t.stop_kind), ("B1", "M2", "intraday"))

    def test_gap_up_entry_then_gap_down_exit_at_open(self):
        """봉 8 시가 101.0 > 수준 100.5 → 체결 101.0(갭 상승). 손절 = 101 − 4 = 97, R = 4. 청산선 = min(98.4, 98.3, 100.6) = 98.3.
        봉 9 시가 97.0 ≤ 98.3 → 시가 97.0 체결(갭 하락, 사유 rule). 비용 = 0.101 + 0.097 = 0.198
        r = (97.0 − 101.0 − 0.198) / 4 = −4.198 / 4 = −1.0495"""
        res = run([(101.0, 102, 100.6, 101.8), (97.0, 98, 96, 97.5)])
        t = res.trades[0]
        self.assertEqual((t.entry_price, t.exit_price, t.exit_reason, t.hold_days), (101.0, 97.0, "rule", 1))
        self.assertAlmostEqual(t.r_multiple, -1.0495, places=9)

    def test_same_day_2n_stop_fills_at_stop_price(self):
        """봉 8: 시가 100.2, 고가 101, 저가 96 → 체결 100.5, 손절 96.5, 저가 96 ≤ 96.5 → 같은 날 96.5 체결(사유 stop). R = 4
        비용 = 0.1005 + 0.0965 = 0.197.  r = (96.5 − 100.5 − 0.197) / 4 = −4.197 / 4 = −1.04925"""
        res = run([(100.2, 101, 96, 97)])
        t = res.trades[0]
        self.assertEqual((t.entry_price, t.exit_price, t.exit_reason, t.hold_days), (100.5, 96.5, "stop", 0))
        self.assertAlmostEqual(t.r_multiple, -1.04925, places=9)

    def test_2n_stop_is_the_exit_when_it_is_above_the_20day_low_line(self):
        """stop_mult = 1: 손절 = 100.5 − 1×2 = 98.5, R = 2. 봉 8 마감 후 청산선 98.3 < 손절 98.5 → 손절선은 내려가지 않고 98.5(사유 stop).
        봉 9: 시가 100 > 98.5, 저가 98.4 ≤ 98.5 → 98.5 체결. 비용 = 0.1005 + 0.0985 = 0.199
        r = (98.5 − 100.5 − 0.199) / 2 = −2.199 / 2 = −1.0995"""
        res = run([(100.2, 102, 99.9, 101.8), (100.0, 100.1, 98.4, 99)], stop_mult=1.0)
        t = res.trades[0]
        self.assertEqual((t.initial_stop, t.exit_price, t.exit_reason), (98.5, 98.5, "stop"))
        self.assertAlmostEqual(t.r_multiple, -1.0995, places=9)

    def test_initial_stop_uses_signal_day_n_not_entry_day_range(self):
        """봉 8의 범위가 아주 커도(고가 110) 초기 손절은 신호일 N=2로 계산한 96.5다. (진입일 ATR을 쓰면 값이 달라진다 = 미래 정보)"""
        res = run([(100.2, 110, 99.9, 105), (105, 106, 104, 105)])
        self.assertEqual(res.trades[0].initial_stop, 96.5)

    def test_no_entry_until_breakout(self):
        """횡보 구간(봉 0~7)에서는 수준 > 다음 봉 고가라 체결이 없다. 봉 8에 고가 100.4 < 수준 100.5 → 체결 없음."""
        res = run([(100.0, 100.4, 99.0, 100.0), (100.0, 100.2, 99.0, 100.0)])
        self.assertEqual(res.trades, [])

    def test_trade_start_gates_signals(self):
        """신호 봉 7(수준 100.5)로 봉 8에 진입하는 거래는 trade_start가 봉 7 날짜 이전/당일일 때만 나온다.
        시작일이 봉 8이면 봉 7의 신호는 막히고, 봉 8 마감 후의 새 신호(수준 = max(H6,H7,H8) = 102)로 봉 9(고가 103 ≥ 102, 체결 102)에 진입한다."""
        extra = [(100.2, 102, 99.9, 101.8), (101.5, 103, 100.5, 102.5)]
        idx = frame(extra).index
        late = run(extra, trade_start=str(idx[8].date())).trades
        self.assertEqual([(t.entry_date, t.entry_price) for t in late], [(idx[9], 102.0)])
        early = run(extra, trade_start=str(idx[7].date())).trades
        self.assertEqual((early[0].entry_date, early[0].entry_price), (idx[8], 100.5))


class TestB1Details(unittest.TestCase):
    def test_defaults_come_from_config(self):
        s = B1Turtle()
        self.assertEqual((s.entry_n, s.exit_n, s.atr_n, s.stop_mult), (55, 20, 20, 2.0))
        self.assertEqual(s.trade_start, pd.Timestamp("2015-01-01"))
        self.assertEqual((s.code, s.entry_model, s.version), ("B1", "M2", "1.0"))

    def test_signal_day_level_equals_donchian_high_of_next_row(self):
        """t일 마감 후 수준(최근 55봉 최고 고가, t 포함) = indicators.donchian_high(high, 55)의 t+1 행 값(직전 55일)."""
        df = synthetic_ohlcv(300, seed=3)
        prep = B1Turtle(trade_start=None).prepare(df)
        dh = donchian_high(df["high"], 55)
        np.testing.assert_allclose(prep["entry_level"].values[:-1][54:], dh.values[1:][54:])
        self.assertTrue(prep["entry_level"].iloc[:54].isna().all())   # 55봉 미만은 신호 없음

    def test_exit_level_is_20_bar_low_including_signal_day(self):
        df = synthetic_ohlcv(100, seed=4)
        prep = B1Turtle(trade_start=None).prepare(df)
        self.assertAlmostEqual(prep["exit_level"].iloc[50], df["low"].iloc[31:51].min())

    def test_stop_below_zero_falls_back_to_nominal_risk(self):
        """2N이 가격보다 크면(진입가 5, N 3 → 5 − 6 < 0) 손절 없이 명목 리스크 2N = 6으로 R을 잡고 기록에 표시한다."""
        spec = B1Turtle(trade_start=None).initial_stop(pd.Series({"n": 3.0}), 5.0)
        self.assertIsNone(spec.price)
        self.assertEqual(spec.nominal_risk, 6.0)
        self.assertEqual(spec.risk_basis, "nominal_2n_stop_below_zero")

    def test_no_entry_when_n_or_level_missing(self):
        s = B1Turtle(trade_start=None)
        self.assertIsNone(s.entry_intent(pd.Series({"entry_level": np.nan, "n": 2.0}, name=pd.Timestamp("2020-01-02"))))
        self.assertIsNone(s.entry_intent(pd.Series({"entry_level": 100.0, "n": np.nan}, name=pd.Timestamp("2020-01-02"))))
        intent = s.entry_intent(pd.Series({"entry_level": 100.0, "n": 2.0}, name=pd.Timestamp("2020-01-02")))
        self.assertEqual((intent.kind, intent.price), ("buy_stop", 100.0))


if __name__ == "__main__":
    unittest.main()
