"""지표 손계산 검증."""
from __future__ import annotations

import unittest

import pandas as pd

from golden_backtest.evaluation import metrics
from golden_backtest.records.trade import Trade


def _t(tid, r, reason="stop", weight=1.0, tranche="ALL", ticker="T", exit_date="2024-01-02"):
    ts = pd.Timestamp("2024-01-02")
    return Trade(ticker=ticker, strategy="T", version="1.6", signal_date=ts, entry_date=ts, entry_price=100, initial_stop=92,
                 exit_date=pd.Timestamp(exit_date), exit_price=100, exit_reason=reason, tranche=tranche, hold_days=1, costs=0, r_multiple=r,
                 mfe_r=0, mae_r=0, regime_tag=None, event_flag=None, strategy_version="0", entry_model="M1", risk_basis="stop",
                 stop_kind="intraday", trade_id=tid, weight=weight)


class TestStats(unittest.TestCase):
    RS = [1.0, -0.5, 2.0, -1.0, -0.2]

    def test_hand_computed(self):
        """r = [1.0, −0.5, 2.0, −1.0, −0.2] (청산일 순)
        승률 2/5 = 0.4, 평균 R = (1 − 0.5 + 2 − 1 − 0.2)/5 = 1.3/5 = 0.26
        평균 이익 R = (1 + 2)/2 = 1.5, 평균 손실 R = (−0.5 − 1 − 0.2)/3 = −0.56667, 중앙값 R = 정렬 [−1, −0.5, −0.2, 1, 2]의 가운데 = −0.2
        PF = 3.0 / 1.7 = 1.764706, 최대 연속 손실 = 2 (−1.0, −0.2)"""
        s = metrics.r_stats(self.RS)
        self.assertEqual((s["n"], s["wins"], s["losses"]), (5, 2, 3))
        self.assertAlmostEqual(s["win_rate"], 0.4)
        self.assertAlmostEqual(s["avg_r"], 0.26)
        self.assertAlmostEqual(s["avg_win_r"], 1.5)
        self.assertAlmostEqual(s["avg_loss_r"], -1.7 / 3)
        self.assertAlmostEqual(s["median_r"], -0.2)
        self.assertAlmostEqual(s["profit_factor"], 3.0 / 1.7)
        self.assertEqual(s["max_consecutive_losses"], 2)
        self.assertTrue(s["insufficient"])  # 5 < 30 → 판정 불가

    def test_expectancy_equals_average_r(self):
        """기대값 = 0.4×1.5 + 0.6×(−0.56667) = 0.6 − 0.34 = 0.26 = 평균 R. 그래서 보고 표에서는 뺀다."""
        s = metrics.r_stats(self.RS)
        self.assertAlmostEqual(s["expectancy"], s["avg_r"])

    def test_median_of_even_count_is_mean_of_middle_two(self):
        self.assertAlmostEqual(metrics.r_stats([1.0, 3.0, -1.0, 5.0])["median_r"], 2.0)  # 정렬 [−1, 1, 3, 5] → (1+3)/2

    def test_no_losses_has_undefined_profit_factor(self):
        s = metrics.r_stats([1.0, 2.0])
        self.assertIsNone(s["profit_factor"])
        self.assertEqual(s["avg_loss_r"], 0.0)

    def test_zero_r_is_neither_win_nor_loss_and_breaks_loss_streak(self):
        s = metrics.r_stats([-1.0, 0.0, -1.0])
        self.assertEqual((s["wins"], s["losses"], s["max_consecutive_losses"]), (0, 2, 1))

    def test_thirty_trades_is_sufficient(self):
        self.assertFalse(metrics.r_stats([0.5] * 30)["insufficient"])
        self.assertTrue(metrics.r_stats([0.5] * 29)["insufficient"])

    def test_tranche_rows_are_summed_into_one_trade_and_open_can_be_excluded(self):
        # 거래 1: 두 트랜치 0.7 + 0.3 = 1.0 (청산 1/10) / 거래 2: 확정 −0.4 (1/11) / 거래 3: 미청산 0.2 (마지막 봉 1/12)
        trades = [_t(1, 0.7, weight=0.5, tranche="A", exit_date="2024-01-05"), _t(1, 0.3, weight=0.5, tranche="B", exit_date="2024-01-10"),
                  _t(2, -0.4, exit_date="2024-01-11"), _t(3, 0.2, reason="open_mtm", exit_date="2024-01-12")]
        self.assertEqual(metrics.trade_r_list(trades, include_open=True), [1.0, -0.4, 0.2])
        self.assertEqual(metrics.trade_r_list(trades, include_open=False), [1.0, -0.4])
        self.assertAlmostEqual(metrics.trade_stats(trades, True)["avg_r"], 0.8 / 3)
        self.assertAlmostEqual(metrics.trade_stats(trades, False)["avg_r"], 0.3)

    def test_empty(self):
        self.assertEqual(metrics.r_stats([])["n"], 0)


class TestCombinedOrderIsByExitDate(unittest.TestCase):
    """여러 종목을 합칠 때 최대 연속 손실은 청산일 순으로 센다.
    A: 거래 1 r=−1 (청산 1/10), 거래 2 r=+1 (청산 1/20)   B: 거래 1 r=−1 (청산 1/12), 거래 2 r=−1 (청산 1/15)
    청산일 순: A−1(1/10), B−1(1/12), B−1(1/15), A+1(1/20) → 연속 손실 3
    (종목별로 이어 붙이면 A: −1, +1, B: −1, −1 → 연속 손실 2로 다르다)"""

    def trades(self):
        return [_t(1, -1.0, ticker="A", exit_date="2024-01-10"), _t(2, 1.0, ticker="A", exit_date="2024-01-20"),
                _t(1, -1.0, ticker="B", exit_date="2024-01-12"), _t(2, -1.0, ticker="B", exit_date="2024-01-15")]

    def test_exit_date_order(self):
        self.assertEqual(metrics.trade_r_list(self.trades(), include_open=True), [-1.0, -1.0, -1.0, 1.0])
        self.assertEqual(metrics.trade_stats(self.trades(), True)["max_consecutive_losses"], 3)

    def test_per_ticker_concatenation_would_give_a_different_answer(self):
        concatenated = [-1.0, 1.0, -1.0, -1.0]   # 종목 A 전부 + 종목 B 전부
        self.assertEqual(metrics.r_stats(concatenated)["max_consecutive_losses"], 2)

    def test_trade_ids_may_collide_across_tickers(self):
        # 두 종목 모두 trade_id 1이 있어도 합쳐지지 않는다((ticker, trade_id)로 센다)
        self.assertEqual(metrics.trade_stats(self.trades(), True)["n"], 4)

    def test_same_exit_date_ties_break_by_ticker_then_trade_id(self):
        trades = [_t(1, 1.0, ticker="B", exit_date="2024-01-10"), _t(1, -1.0, ticker="A", exit_date="2024-01-10")]
        self.assertEqual(metrics.trade_r_list(trades, include_open=True), [-1.0, 1.0])


if __name__ == "__main__":
    unittest.main()
