"""momentum_signals/portfolio.py 단위 테스트 — 일일 포지션 관리 상태머신."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as cfg  # noqa: E402
import portfolio as pf  # noqa: E402


def _mk_pos(entry=100.0, stop=90.0) -> pf.Position:
    return pf.Position(id="t1", market="KR", code="000000", name="테스트", entry_date="2026-01-02",
                        entry_price=entry, initial_stop=stop, entry_score=80.0,
                        shares_original=100, shares_remaining=100)


class TestSizePosition(unittest.TestCase):
    def test_uses_smaller_of_tier_cap_and_risk_cap(self):
        shares, invested, risk_pct = pf.size_position(
            entry_price=10_000.0, stop_price=9_500.0, score_total=80.0, market="KR",
            total_equity=1_000_000.0)
        # 리스크캡: 1,000,000*1% / 500 = 20주. tier(78~85)=300만원/10,000=300주.
        self.assertEqual(shares, 20)
        self.assertAlmostEqual(risk_pct, 20 * 500 / 1_000_000 * 100, places=3)

    def test_higher_score_gets_bigger_tier_cap(self):
        s1, _, _ = pf.size_position(10_000.0, 9_900.0, 80.0, "KR", 100_000_000.0)
        s2, _, _ = pf.size_position(10_000.0, 9_900.0, 95.0, "KR", 100_000_000.0)
        self.assertGreater(s2, s1)

    def test_zero_or_negative_risk_gives_no_shares(self):
        shares, invested, risk_pct = pf.size_position(10_000.0, 10_000.0, 80.0, "KR", 1_000_000.0)
        self.assertEqual((shares, invested, risk_pct), (0, 0.0, 0.0))


class TestProcessDayStopLoss(unittest.TestCase):
    def test_gap_down_exits_at_open_not_stop_price(self):
        pos = _mk_pos(entry=100.0, stop=90.0)
        pf.process_day(pos, "2026-01-05", open_=85.0, high=86.0, low=83.0, close=84.0, atr_value=2.0)
        self.assertEqual(pos.status, "CLOSED")
        self.assertEqual(pos.exits[-1].price, 85.0)
        self.assertEqual(pos.exits[-1].reason, "갭 손절(시가가 손절가 이하)")
        self.assertEqual(pos.shares_remaining, 0)

    def test_intraday_stop_hit_exits_at_stop_price(self):
        pos = _mk_pos(entry=100.0, stop=90.0)
        pf.process_day(pos, "2026-01-05", open_=95.0, high=96.0, low=89.0, close=91.0, atr_value=2.0)
        self.assertEqual(pos.status, "CLOSED")
        self.assertEqual(pos.exits[-1].price, 90.0)

    def test_no_stop_hit_keeps_position_open(self):
        pos = _mk_pos(entry=100.0, stop=90.0)
        pf.process_day(pos, "2026-01-05", open_=101.0, high=103.0, low=99.0, close=102.0, atr_value=2.0)
        self.assertEqual(pos.status, "OPEN")
        self.assertEqual(pos.trading_days_held, 1)


class TestProcessDayRMultiples(unittest.TestCase):
    def test_1r_moves_stop_to_breakeven(self):
        pos = _mk_pos(entry=100.0, stop=90.0)  # r_per_share=10
        pf.process_day(pos, "2026-01-05", open_=101.0, high=111.0, low=100.0, close=105.0, atr_value=2.0)
        self.assertTrue(pos.r1_hit)
        self.assertEqual(pos.current_stop, 100.0)
        self.assertEqual(pos.status, "OPEN")

    def test_2r_sells_one_third_and_starts_trailing(self):
        pos = _mk_pos(entry=100.0, stop=90.0)
        pf.process_day(pos, "2026-01-05", open_=101.0, high=121.0, low=100.0, close=115.0, atr_value=2.0)
        self.assertTrue(pos.r1_hit)
        self.assertTrue(pos.r2_hit)
        self.assertTrue(pos.trailing_active)
        self.assertEqual(pos.shares_remaining, 100 - 33)  # floor(100/3)=33
        self.assertEqual(len(pos.exits), 1)
        self.assertEqual(pos.exits[0].price, 120.0)  # entry+2R=100+20
        # 트레일링 스탑: max(진입가(1R로 본전), 종가-1.5*ATR) = max(100, 115-3)=112
        self.assertEqual(pos.current_stop, 112.0)

    def test_trailing_stop_never_moves_down(self):
        pos = _mk_pos(entry=100.0, stop=90.0)
        pf.process_day(pos, "2026-01-05", open_=101.0, high=121.0, low=100.0, close=115.0, atr_value=2.0)
        stop_after_2r = pos.current_stop
        pf.process_day(pos, "2026-01-06", open_=110.0, high=112.0, low=105.0, close=106.0, atr_value=3.0)
        # 종가-1.5*ATR = 106-4.5=101.5 < 이전 손절(112) → 내려가지 않아야 함
        self.assertEqual(pos.current_stop, stop_after_2r)


class TestProcessDayTimeStop(unittest.TestCase):
    def test_exits_at_close_on_day5_if_below_3pct(self):
        pos = _mk_pos(entry=100.0, stop=90.0)
        for i in range(1, 5):
            pf.process_day(pos, f"2026-01-{i+1:02d}", open_=100.0, high=101.0, low=99.0,
                            close=101.0, atr_value=2.0)
        self.assertEqual(pos.status, "OPEN")
        pf.process_day(pos, "2026-01-07", open_=101.0, high=102.0, low=100.5, close=101.5, atr_value=2.0)
        self.assertEqual(pos.trading_days_held, 5)
        self.assertEqual(pos.status, "CLOSED")
        self.assertEqual(pos.exits[-1].reason, "시간손절(5거래일 내 +3% 미달)")

    def test_stays_open_at_day5_if_above_3pct(self):
        pos = _mk_pos(entry=100.0, stop=90.0)
        for i in range(1, 6):
            pf.process_day(pos, f"2026-01-{i+1:02d}", open_=100.0, high=105.0, low=99.0,
                            close=104.0, atr_value=2.0)
        self.assertEqual(pos.trading_days_held, 5)
        self.assertEqual(pos.status, "OPEN")

    def test_time_stop_only_evaluated_once_at_exactly_day5(self):
        pos = _mk_pos(entry=100.0, stop=90.0)
        for i in range(1, 7):
            pf.process_day(pos, f"2026-01-{i+1:02d}", open_=100.0, high=105.0, low=99.0,
                            close=104.0, atr_value=2.0)
        # day5에서 +3% 이상이라 살아남았으면 이후 날짜에서 다시 검사하지 않는다
        self.assertTrue(pos.time_stop_checked)
        self.assertEqual(pos.status, "OPEN")


class TestProcessDayIdempotence(unittest.TestCase):
    def test_same_date_processed_twice_is_noop_second_time(self):
        pos = _mk_pos(entry=100.0, stop=90.0)
        pf.process_day(pos, "2026-01-05", open_=101.0, high=103.0, low=99.0, close=102.0, atr_value=2.0)
        pf.process_day(pos, "2026-01-05", open_=101.0, high=200.0, low=1.0, close=102.0, atr_value=2.0)
        self.assertEqual(pos.trading_days_held, 1)
        self.assertEqual(pos.status, "OPEN")

    def test_closed_position_ignores_further_updates(self):
        pos = _mk_pos(entry=100.0, stop=90.0)
        pf.process_day(pos, "2026-01-05", open_=80.0, high=81.0, low=79.0, close=80.0, atr_value=2.0)
        self.assertEqual(pos.status, "CLOSED")
        pf.process_day(pos, "2026-01-06", open_=200.0, high=210.0, low=190.0, close=205.0, atr_value=2.0)
        self.assertEqual(pos.trading_days_held, 1)
        self.assertEqual(len(pos.exits), 1)


class TestPositionSerialization(unittest.TestCase):
    def test_roundtrip_preserves_state(self):
        pos = _mk_pos(entry=100.0, stop=90.0)
        pf.process_day(pos, "2026-01-05", open_=101.0, high=121.0, low=100.0, close=115.0, atr_value=2.0)
        d = pf.position_to_dict(pos)
        restored = pf.position_from_dict(d)
        self.assertEqual(restored.current_stop, pos.current_stop)
        self.assertEqual(restored.shares_remaining, pos.shares_remaining)
        self.assertEqual(len(restored.exits), len(pos.exits))
        self.assertEqual(restored.r_multiple, pos.r_multiple)


if __name__ == "__main__":
    unittest.main()
