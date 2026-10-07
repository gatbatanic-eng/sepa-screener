"""체결 규칙 단위 테스트(순수 함수). 숫자는 손으로 정했다."""
from __future__ import annotations

import unittest

from golden_backtest.engine import fills


class TestEntryFills(unittest.TestCase):
    def test_next_open(self):
        self.assertEqual(fills.entry_next_open(101.5), 101.5)

    def test_buy_stop_gap_up_uses_open(self):
        self.assertEqual(fills.entry_buy_stop(100, open_=103, high=105), 103)  # max(103, 100)

    def test_buy_stop_touched_intraday_uses_level(self):
        self.assertEqual(fills.entry_buy_stop(100, open_=98, high=101), 100)   # max(98, 100)

    def test_buy_stop_exactly_touched(self):
        self.assertEqual(fills.entry_buy_stop(100, open_=98, high=100), 100)

    def test_buy_stop_not_touched(self):
        self.assertIsNone(fills.entry_buy_stop(100, open_=98, high=99.99))

    def test_buy_stop_open_exactly_at_level(self):
        self.assertEqual(fills.entry_buy_stop(100, open_=100, high=100), 100)


class TestIntradayStop(unittest.TestCase):
    def test_gap_open_below_stop_fills_at_open(self):
        self.assertEqual(fills.intraday_stop_fill(92, open_=90, low=89), 90)

    def test_open_exactly_at_stop_fills_at_open(self):
        self.assertEqual(fills.intraday_stop_fill(92, open_=92, low=90), 92)

    def test_low_touches_stop_fills_at_stop(self):
        self.assertEqual(fills.intraday_stop_fill(92, open_=95, low=91), 92)

    def test_low_exactly_at_stop_fills(self):
        self.assertEqual(fills.intraday_stop_fill(92, open_=95, low=92), 92)

    def test_not_touched(self):
        self.assertIsNone(fills.intraday_stop_fill(92, open_=95, low=92.01))


class TestEntryBarStop(unittest.TestCase):
    def test_fills_at_stop_price_even_if_open_was_below(self):
        # 시가 인자가 없다: 진입 전 시가는 체결가와 무관하다는 근거를 시그니처로 강제한다
        self.assertEqual(fills.entry_bar_intraday_stop_fill(95, low=92), 95)

    def test_low_above_stop_no_fill(self):
        self.assertIsNone(fills.entry_bar_intraday_stop_fill(95, low=95.01))

    def test_low_equal_to_stop_fills(self):
        self.assertEqual(fills.entry_bar_intraday_stop_fill(95, low=95), 95)


class TestCloseStop(unittest.TestCase):
    def test_close_below_triggers(self):
        self.assertTrue(fills.close_stop_triggered(92, 91.99))

    def test_close_equal_does_not_trigger(self):
        self.assertFalse(fills.close_stop_triggered(92, 92))

    def test_low_is_irrelevant_by_signature(self):
        # close 종류는 저가를 받지 않는다 — 진입 당일 장중 저가로 손절하지 않는다
        self.assertFalse(fills.close_stop_triggered(92, 95))


if __name__ == "__main__":
    unittest.main()
