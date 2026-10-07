"""엔진 엣지 케이스. 모든 기대값은 손으로 계산했고 계산식을 주석에 적었다.

비용: 편도 0.1% = 체결가 × 0.001 (진입·청산 각각). R = 진입가 − 초기 손절가(비용 제외).
r = weight × (청산가 − 진입가 − 진입비용 − 청산비용) / R.  봉 번호는 0부터이고, 신호 봉 다음 봉(1번)에서 체결된다.
"""
from __future__ import annotations

import unittest

from golden_backtest.engine.costs import CostRate, Costs
from golden_backtest.engine.intents import EntryIntent, ExitIntent, StopSpec
from golden_backtest.engine.simulator import simulate
from golden_backtest.records.trade import trade_level_r
from golden_backtest.tests.engine_helpers import (COSTS, NEXT_OPEN, SPEC, ScriptedStrategy, buy_stop, close_stop,
                                                    exit_next_open, intraday_stop, make_df)

SIG = (100, 101, 99, 100)  # 신호 봉(0번)


def run(rows, strat, costs=COSTS, group="default"):
    return simulate(make_df(rows), strat, "TEST", costs, SPEC, group, warmup_bars=0)  # 손계산용 짧은 데이터라 워밍업 게이트를 끈다


def only(res, confirmed=True):
    trades = res.confirmed() if confirmed else res.with_open_mtm()
    assert len(trades) == 1, f"거래 행 {len(trades)}개: {trades}"
    return trades[0]


class TestM1Intraday(unittest.TestCase):
    def test_case01_m1_entry_rule_exit_next_open(self):
        """M1 정상 청산. 진입 시가 100, 손절 92 → R=8. 다음 봉 시가 110에 규칙 청산.
        비용 = 100×0.001 + 110×0.001 = 0.10 + 0.11 = 0.21
        r = (110 − 100 − 0.21) / 8 = 9.79 / 8 = 1.22375"""
        rows = [SIG, (100, 105, 98, 104), (110, 112, 108, 111), (111, 113, 109, 112)]
        s = ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92), {1: [exit_next_open("rule")]})
        t = only(run(rows, s))
        self.assertEqual((t.entry_price, t.exit_price, t.exit_reason, t.hold_days), (100, 110, "rule", 1))
        self.assertAlmostEqual(t.costs, 0.21)
        self.assertAlmostEqual(t.r_multiple, 1.22375)

    def test_case02_intraday_stop_fills_at_stop_price(self):
        """장중 스탑. 시가 95 > 손절 92, 저가 90 ≤ 92 → 손절가 92 체결.
        비용 = 0.10 + 92×0.001 = 0.192.  r = (92 − 100 − 0.192) / 8 = −8.192 / 8 = −1.024"""
        rows = [SIG, (100, 102, 99, 101), (95, 97, 90, 91)]
        t = only(run(rows, ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92))))
        self.assertEqual((t.exit_price, t.exit_reason, t.hold_days), (92, "stop", 1))
        self.assertAlmostEqual(t.r_multiple, -1.024)

    def test_case03_gap_below_stop_fills_at_open(self):
        """보유 중 갭 하락. 시가 90 ≤ 손절 92 → 시가 90 체결.
        비용 = 0.10 + 90×0.001 = 0.19.  r = (90 − 100 − 0.19) / 8 = −10.19 / 8 = −1.27375"""
        rows = [SIG, (100, 102, 99, 101), (90, 93, 89, 91)]
        t = only(run(rows, ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92))))
        self.assertEqual(t.exit_price, 90)
        self.assertAlmostEqual(t.r_multiple, -1.27375)

    def test_case12a_lower_stop_proposal_is_ignored(self):
        """손절선 하향 금지. 현재 92, 전략이 90을 제안 → 92 유지. 저가 91 ≤ 92라 92에 체결(90이었다면 체결 없음).
        r = (92 − 100 − 0.10 − 0.092) / 8 = −1.024"""
        rows = [SIG, (100, 102, 99, 101), (95, 96, 91, 92)]
        s = ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92), {1: [ExitIntent("intraday", "trailing", price=90)]})
        t = only(run(rows, s))
        self.assertEqual((t.exit_price, t.exit_reason), (92, "stop"))
        self.assertAlmostEqual(t.r_multiple, -1.024)

    def test_case12b_raised_stop_is_applied_with_trailing_reason(self):
        """손절선 상향 반영. 92 → 95. 저가 94 ≤ 95라 95에 체결, 사유는 trailing.
        비용 = 0.10 + 95×0.001 = 0.195.  r = (95 − 100 − 0.195) / 8 = −5.195 / 8 = −0.649375"""
        rows = [SIG, (100, 102, 99, 101), (97, 98, 94, 96)]
        s = ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92), {1: [ExitIntent("intraday", "trailing", price=95)]})
        t = only(run(rows, s))
        self.assertEqual((t.exit_price, t.exit_reason), (95, "trailing"))
        self.assertAlmostEqual(t.r_multiple, -0.649375)

    def test_case_m1_intraday_stop_on_entry_day(self):
        """진입 당일 intraday 손절(M1은 시가 진입 후 적용). 진입 시가 100, 손절 92, 진입일 저가 90 ≤ 92 → 92 체결, 보유 0일.
        r = (92 − 100 − 0.10 − 0.092) / 8 = −1.024"""
        rows = [SIG, (100, 102, 90, 95), (95, 96, 94, 95)]
        t = only(run(rows, ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92))))
        self.assertEqual((t.exit_price, t.hold_days, t.exit_reason), (92, 0, "stop"))
        self.assertAlmostEqual(t.r_multiple, -1.024)


class TestM2(unittest.TestCase):
    def test_case04_gap_up_fills_at_open(self):
        """M2 갭 상승. 돌파가 100, 시가 103 → 체결 max(103, 100) = 103. 손절 95 → R=8. 다음 봉 시가 111 청산.
        비용 = 103×0.001 + 111×0.001 = 0.103 + 0.111 = 0.214
        r = (111 − 103 − 0.214) / 8 = 7.786 / 8 = 0.97325"""
        rows = [(98, 99, 97, 98), (103, 105, 99, 104), (111, 113, 110, 112)]
        s = ScriptedStrategy({0: buy_stop(100)}, intraday_stop(95), {1: [exit_next_open()]})
        t = only(run(rows, s))
        self.assertEqual((t.entry_price, t.exit_price), (103, 111))
        self.assertAlmostEqual(t.r_multiple, 0.97325)

    def test_case05_stop_order_not_touched_expires(self):
        """M2 미체결. 고가 99.9 < 돌파가 100 → 체결 없음. 주문은 하루짜리라 다음 날 고가 101이어도 체결되지 않는다(전략이 다시 내야 함)."""
        rows = [(98, 99, 97, 98), (98, 99.9, 97, 99), (99, 101, 98, 100)]
        res = run(rows, ScriptedStrategy({0: buy_stop(100)}, intraday_stop(95)))
        self.assertEqual(res.trades, [])

    def test_case06a_high_equal_to_level_fills(self):
        """경계: 고가 = 돌파가면 체결. 시가 99 < 100 → 체결가 max(99, 100) = 100."""
        rows = [(98, 99, 97, 98), (99, 100, 97, 99), (99, 101, 98, 100)]
        t = only(run(rows, ScriptedStrategy({0: buy_stop(100)}, intraday_stop(95))), confirmed=False)
        self.assertEqual(t.entry_price, 100)
        self.assertEqual(t.exit_reason, "open_mtm")  # 저가 97 > 손절 95라 손절 없음

    def test_case06b_low_equal_to_stop_is_stopped(self):
        """경계: 저가 = 손절가면 손절. 체결 100, 손절 95, 저가 95 ≤ 95 → 95 체결.
        R=5, 비용 = 0.100 + 0.095.  r = (95 − 100 − 0.195) / 5 = −5.195 / 5 = −1.039"""
        rows = [(98, 99, 97, 98), (99, 101, 95, 98)]
        t = only(run(rows, ScriptedStrategy({0: buy_stop(100)}, intraday_stop(95))))
        self.assertEqual(t.exit_price, 95)
        self.assertAlmostEqual(t.r_multiple, -1.039)

    def test_case07a_same_day_stop_fills_at_stop_price(self):
        """M2 당일 손절. 돌파가 100, 시가 99, 고가 101, 저가 94, 손절 95 → 체결 100, 같은 날 95 손절. R=5.
        r = (95 − 100 − 0.100 − 0.095) / 5 = −5.195 / 5 = −1.039"""
        rows = [(98, 99, 97, 98), (99, 101, 94, 96)]
        t = only(run(rows, ScriptedStrategy({0: buy_stop(100)}, intraday_stop(95))))
        self.assertEqual((t.entry_price, t.exit_price, t.hold_days, t.exit_reason), (100, 95, 0, "stop"))
        self.assertAlmostEqual(t.r_multiple, -1.039)

    def test_case07b_open_below_stop_does_not_change_stop_fill(self):
        """M2 시가가 손절가 아래(93 < 95 < 돌파가 100)여도 체결가는 손절가 95로 고정한다.
        근거: M2는 장중 진입이라 진입 전 시가는 이후 손절 체결가와 무관하다. 체결 max(93, 100) = 100, 저가 92 ≤ 95 → 95.
        r = (95 − 100 − 0.195) / 5 = −1.039 (07a와 같다. 시가 93에 체결했다면 −1.2 쪽으로 더 나빠진다)"""
        rows = [(98, 99, 97, 98), (93, 101, 92, 96)]
        t = only(run(rows, ScriptedStrategy({0: buy_stop(100)}, intraday_stop(95))))
        self.assertEqual((t.entry_price, t.exit_price), (100, 95))
        self.assertAlmostEqual(t.r_multiple, -1.039)


class TestCloseStop(unittest.TestCase):
    def test_case08_close_stop_exits_next_open(self):
        """종가형 손절. 손절 92, 2번 봉 종가 91.9 < 92 → 3번 봉 시가 93 체결. (2번 봉 저가 91은 영향 없음)
        비용 = 0.100 + 93×0.001 = 0.193.  r = (93 − 100 − 0.193) / 8 = −7.193 / 8 = −0.899125"""
        rows = [SIG, (100, 102, 99, 100), (100, 100, 91, 91.9), (93, 95, 92, 94)]
        t = only(run(rows, ScriptedStrategy({0: NEXT_OPEN}, close_stop(92))))
        self.assertEqual((t.exit_date, t.exit_price, t.exit_reason, t.hold_days), (make_df(rows).index[3], 93, "stop", 2))
        self.assertAlmostEqual(t.r_multiple, -0.899125)

    def test_case09_close_stop_gap_can_lose_more_than_one_r(self):
        """종가형 손절 + 갭. 다음 봉 시가가 85면 85 체결.
        비용 = 0.100 + 85×0.001 = 0.185.  r = (85 − 100 − 0.185) / 8 = −15.185 / 8 = −1.898125"""
        rows = [SIG, (100, 102, 99, 100), (100, 100, 91, 91.9), (85, 88, 84, 86)]
        t = only(run(rows, ScriptedStrategy({0: NEXT_OPEN}, close_stop(92))))
        self.assertEqual(t.exit_price, 85)
        self.assertAlmostEqual(t.r_multiple, -1.898125)

    def test_case23_m1_close_stop_entry_day_low_below_stop_but_close_above_no_stop(self):
        """M1 + close 손절. 진입일 저가 90 < 손절 92지만 종가 95 ≥ 92 → 손절 없음(장중 저가로는 손절하지 않는다).
        이후 마지막 봉 종가 97까지 보유 → open_mtm. r = (97 − 100 − 0.100 − 0.097) / 8 = −3.197 / 8 = −0.399625"""
        rows = [SIG, (100, 101, 90, 95), (96, 97, 94, 97)]
        res = run(rows, ScriptedStrategy({0: NEXT_OPEN}, close_stop(92)))
        self.assertEqual(res.confirmed(), [])
        t = only(res, confirmed=False)
        self.assertEqual((t.exit_reason, t.exit_price), ("open_mtm", 97))
        self.assertAlmostEqual(t.r_multiple, -0.399625)

    def test_case24_m1_close_stop_entry_day_close_below_stop_exits_next_open(self):
        """M1 + close 손절. 진입일 종가 91 < 손절 92 → 다음 봉 시가 90 체결.
        비용 = 0.100 + 90×0.001 = 0.190.  r = (90 − 100 − 0.19) / 8 = −10.19 / 8 = −1.27375"""
        rows = [SIG, (100, 101, 90, 91), (90, 92, 89, 91)]
        t = only(run(rows, ScriptedStrategy({0: NEXT_OPEN}, close_stop(92))))
        self.assertEqual((t.exit_price, t.exit_reason, t.hold_days), (90, "stop", 1))
        self.assertAlmostEqual(t.r_multiple, -1.27375)

    def test_close_stop_level_only_moves_up(self):
        """close 종류 손절선도 올리기만 한다. 92 → 88 제안은 무시, 종가 90 < 92 → 다음 봉 시가 청산."""
        rows = [SIG, (100, 101, 99, 100), (100, 100, 89, 90), (89, 90, 88, 89)]
        s = ScriptedStrategy({0: NEXT_OPEN}, close_stop(92), {1: [ExitIntent("close", "trailing", price=88)]})
        t = only(run(rows, s))
        self.assertEqual((t.exit_price, t.exit_reason), (89, "stop"))


class TestTranchesAndMtm(unittest.TestCase):
    B3_ROWS = [(49, 49.5, 48.5, 49), (50, 52, 49, 51), (51, 53, 50, 52), (52, 54, 51, 53), (53, 55, 52, 54),
               (54, 56, 53, 55), (55, 57, 54, 56), (56, 59, 55, 58), (58, 60, 57, 59)]
    TR = {"A": 0.5, "B": 0.5}

    def test_case10_two_tranches_summed(self):
        """B3 두 트랜치. M2 돌파가 50, 시가 50 → 체결 50, 손절 46 → R=4. 비중 A 50% / B 50%.
        A: 5번 봉 마감 후 '다음 봉 종가' 청산 예약 → 6번 봉 종가 56 (진입 후 5거래일째).
           비용 = 50×0.001 + 56×0.001 = 0.05 + 0.056 = 0.106,  r_A = 0.5 × (56 − 50 − 0.106) / 4 = 0.5 × 5.894 / 4 = 0.73675
        B: 7번 봉 마감 후 규칙 청산 → 8번 봉 시가 58.
           비용 = 0.05 + 0.058 = 0.108,  r_B = 0.5 × (58 − 50 − 0.108) / 4 = 0.5 × 7.892 / 4 = 0.9865
        합계 r = 0.73675 + 0.9865 = 1.72325 (= 0.5 × (5.894 + 7.892) / 4 = 6.893 / 4)"""
        manage = {5: [ExitIntent("close", "partial", tranche="A", at="next_close")],
                  7: [ExitIntent("close", "rule", tranche="B")]}
        s = ScriptedStrategy({0: buy_stop(50, self.TR)}, intraday_stop(46), manage)
        res = run(self.B3_ROWS, s)
        a, b = sorted(res.confirmed(), key=lambda t: t.tranche)
        self.assertEqual((a.tranche, a.exit_price, a.exit_reason, a.hold_days), ("A", 56, "partial", 5))
        self.assertEqual((b.tranche, b.exit_price, b.exit_reason, b.hold_days), ("B", 58, "rule", 7))
        self.assertAlmostEqual(a.r_multiple, 0.73675)
        self.assertAlmostEqual(b.r_multiple, 0.9865)
        self.assertAlmostEqual(a.costs, 0.5 * 0.106)
        self.assertAlmostEqual(trade_level_r(res.confirmed())[a.trade_id], 1.72325)
        self.assertEqual(a.trade_id, b.trade_id)

    def test_case11_breakeven_stop_for_tranche_b(self):
        """B3 5일 후 본전 손절. A는 위와 같이 56 청산(r_A = 0.73675). 6번 봉 마감 후 B의 intraday 손절을 진입가 50으로 올린다.
        7번 봉(시가 52, 저가 49): 52 > 50, 저가 49 ≤ 50 → 50 체결.
        B: 비용 = 0.05 + 0.05 = 0.10,  r_B = 0.5 × (50 − 50 − 0.10) / 4 = −0.0125.  합계 = 0.73675 − 0.0125 = 0.72425"""
        rows = self.B3_ROWS[:7] + [(52, 53, 49, 50.5), (50, 51, 49.5, 50)]
        manage = {5: [ExitIntent("close", "partial", tranche="A", at="next_close")],
                  6: [ExitIntent("intraday", "stop", price=50, tranche="B")]}
        res = run(rows, ScriptedStrategy({0: buy_stop(50, self.TR)}, intraday_stop(46), manage))
        a, b = sorted(res.confirmed(), key=lambda t: t.tranche)
        self.assertEqual((b.exit_price, b.exit_reason), (50, "stop"))
        self.assertAlmostEqual(b.r_multiple, -0.0125)
        self.assertAlmostEqual(trade_level_r(res.confirmed())[a.trade_id], 0.72425)

    def test_case13_stop_beats_scheduled_close_exit_same_day(self):
        """같은 날 장중 스탑과 종가 청산이 겹치면 스탑이 먼저. M1 진입 100, 손절 92(R=8), 두 트랜치 50%씩.
        1번 봉 마감 후 A를 '다음 봉 종가' 청산 예약. 2번 봉(저가 91 ≤ 92)에서 A·B 모두 92 손절, 종가 95 청산은 일어나지 않는다.
        r_A = r_B = 0.5 × (92 − 100 − 0.100 − 0.092) / 8 = 0.5 × (−8.192) / 8 = −0.512"""
        rows = [SIG, (100, 102, 99, 101), (97, 98, 91, 95)]
        manage = {1: [ExitIntent("close", "partial", tranche="A", at="next_close")]}
        res = run(rows, ScriptedStrategy({0: EntryIntent("next_open", None, {"A": 0.5, "B": 0.5})}, intraday_stop(92), manage))
        a, b = sorted(res.confirmed(), key=lambda t: t.tranche)
        self.assertEqual((a.exit_price, a.exit_reason), (92, "stop"))
        self.assertEqual((b.exit_price, b.exit_reason), (92, "stop"))
        self.assertAlmostEqual(a.r_multiple, -0.512)
        self.assertAlmostEqual(b.r_multiple, -0.512)

    def test_case21_open_position_marked_to_market(self):
        """데이터 끝 미청산. 진입 100, 손절 92(R=8), 마지막 종가 104로 평가(청산 비용 포함).
        비용 = 0.100 + 104×0.001 = 0.204.  r = (104 − 100 − 0.204) / 8 = 3.796 / 8 = 0.4745.  보유 2일"""
        rows = [SIG, (100, 102, 99, 101), (102, 104, 100, 103), (103, 105, 102, 104)]
        res = run(rows, ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92)))
        self.assertEqual(res.confirmed(), [])  # 확정 거래 통계에는 들어가지 않는다
        t = only(res, confirmed=False)  # 마지막 종가 평가 통계에는 들어간다
        self.assertEqual((t.exit_reason, t.exit_price, t.hold_days, t.exit_date), ("open_mtm", 104, 2, make_df(rows).index[3]))
        self.assertAlmostEqual(t.r_multiple, 0.4745)

    def test_case21b_open_two_tranches_marked_to_market(self):
        """미청산 두 트랜치. 진입 50, 손절 46(R=4), 마지막 종가 56. 각 트랜치:
        비용 = 0.05 + 0.056 = 0.106,  r = 0.5 × (56 − 50 − 0.106) / 4 = 0.5 × 5.894 / 4 = 0.73675 → 합계 1.4735"""
        rows = self.B3_ROWS[:7]
        res = run(rows, ScriptedStrategy({0: buy_stop(50, self.TR)}, intraday_stop(46)))
        trades = res.with_open_mtm()
        self.assertEqual({t.exit_reason for t in trades}, {"open_mtm"})
        self.assertAlmostEqual(trade_level_r(trades)[trades[0].trade_id], 1.4735)
        self.assertEqual(res.confirmed(), [])


class TestOtherEdges(unittest.TestCase):
    def test_case14_mfe_mae_in_r_units(self):
        """진입 100, 손절 92(R=8). 고가 112 → 120, 저가 96 → 94 → 청산 봉(고 119.5, 저 118)은 극값을 넘지 않는다.
        MFE = (120 − 100) / 8 = 2.5,  MAE = (94 − 100) / 8 = −0.75"""
        rows = [SIG, (100, 112, 96, 110), (110, 120, 94, 118), (119, 119.5, 118, 119)]
        s = ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92), {2: [exit_next_open()]})
        t = only(run(rows, s))
        self.assertAlmostEqual(t.mfe_r, 2.5)
        self.assertAlmostEqual(t.mae_r, -0.75)

    def test_case15_nominal_risk_has_no_stop_and_is_flagged(self):
        """A2-원전: 손절 없음, 명목 리스크 R = 3×ATR14 = 3 × 2 = 6. 진입 시가 50, 2번 봉 시가 53 청산.
        진입 봉 저가 40까지 급락해도 손절이 없으므로 청산되지 않는다.
        비용 = 0.05 + 0.053 = 0.103.  r = (53 − 50 − 0.103) / 6 = 2.897 / 6 = 0.4828333"""
        rows = [(50, 51, 49, 50), (50, 51, 40, 45), (53, 54, 52, 53)]
        spec = StopSpec(None, None, nominal_risk=6.0, risk_basis="nominal_3atr")
        t = only(run(rows, ScriptedStrategy({0: NEXT_OPEN}, spec, {1: [exit_next_open()]})))
        self.assertIsNone(t.initial_stop)
        self.assertIsNone(t.stop_kind)
        self.assertEqual(t.risk_basis, "nominal_3atr")
        self.assertAlmostEqual(t.r_multiple, 2.897 / 6)

    def test_case16_hold_days_is_bar_count_between_entry_and_exit(self):
        """진입 1번 봉, 3번 봉 마감 후 청산 → 4번 봉 시가 체결. hold_days = 4 − 1 = 3"""
        rows = [SIG, (100, 102, 99, 101), (101, 103, 100, 102), (102, 104, 101, 103), (104, 105, 103, 104)]
        t = only(run(rows, ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92), {3: [exit_next_open()]})))
        self.assertEqual(t.hold_days, 3)

    def test_case17_signal_on_last_bar_has_no_entry(self):
        """마지막 봉에서 낸 신호는 다음 봉이 없어 진입하지 않는다."""
        rows = [SIG, (100, 102, 99, 101), (101, 103, 100, 102)]
        res = run(rows, ScriptedStrategy({2: NEXT_OPEN}, intraday_stop(92)))
        self.assertEqual(res.trades, [])

    def test_case18_entry_intents_while_in_position_are_ignored(self):
        """종목당 1포지션. 모든 봉에서 진입 의도를 내도 보유 중에는 무시된다 → 거래 1건(미청산)."""
        rows = [SIG, (100, 102, 99, 101), (101, 103, 100, 102), (102, 104, 101, 103), (103, 105, 102, 104)]
        s = ScriptedStrategy({i: NEXT_OPEN for i in range(len(rows))}, intraday_stop(92))
        res = run(rows, s)
        self.assertEqual(len(res.trades), 1)
        self.assertEqual(res.trades[0].entry_price, 100)

    def test_case19_stop_at_or_above_fill_price_rejects_entry(self):
        """손절가 101 ≥ 체결가 100 → R = −1 ≤ 0 → 진입 거부(거래 없음), 거부 내역은 남는다."""
        rows = [SIG, (100, 102, 99, 101), (101, 103, 100, 102)]
        res = run(rows, ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(101)))
        self.assertEqual(res.trades, [])
        self.assertEqual(len(res.rejected_entries), 1)
        self.assertEqual(res.rejected_entries[0]["fill"], 100)

    def test_case20_cost_group_override(self):
        """종목군 비용 오버라이드: 슬리피지 0.15% → 편도 0.05% + 0.15% = 0.2%. 1번 케이스와 같은 가격.
        비용 = 100×0.002 + 110×0.002 = 0.20 + 0.22 = 0.42.  r = (110 − 100 − 0.42) / 8 = 9.58 / 8 = 1.1975"""
        costs = Costs(CostRate(0.0005, 0.0005), {"volatile": CostRate(0.0005, 0.0015)})
        rows = [SIG, (100, 105, 98, 104), (110, 112, 108, 111), (111, 113, 109, 112)]
        s = ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92), {1: [exit_next_open()]})
        t = only(run(rows, s, costs, "volatile"))
        self.assertAlmostEqual(t.costs, 0.42)
        self.assertAlmostEqual(t.r_multiple, 1.1975)

    def test_unknown_cost_group_raises(self):
        with self.assertRaises(ValueError):
            run([SIG, (100, 102, 99, 101)], ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92)), COSTS, "nope")

    def test_reentry_signal_allowed_on_bar_after_exit(self):
        """청산한 봉의 종가에서 새 신호를 내면 다음 봉에 재진입한다. 2번 봉에서 손절(92) → 2번 봉 마감 신호 → 3번 봉 시가 진입."""
        rows = [SIG, (100, 102, 99, 101), (95, 97, 90, 91), (91, 93, 90, 92), (92, 94, 91, 93)]
        s = ScriptedStrategy({0: NEXT_OPEN, 2: NEXT_OPEN}, lambda fill: intraday_stop(fill * 0.92))
        res = run(rows, s)
        self.assertEqual([t.exit_reason for t in res.trades], ["stop", "open_mtm"])
        self.assertEqual(res.trades[1].entry_price, 91)
        self.assertEqual(res.trades[0].trade_id + 1, res.trades[1].trade_id)

    def test_record_carries_versions_and_extra_fields(self):
        rows = [SIG, (100, 102, 99, 101), (95, 97, 90, 91)]
        t = only(run(rows, ScriptedStrategy({0: NEXT_OPEN}, intraday_stop(92))))
        self.assertEqual((t.strategy, t.version, t.strategy_version, t.entry_model), ("T", SPEC, "0.1", "M1"))
        self.assertEqual((t.stop_kind, t.risk_basis, t.weight, t.initial_stop), ("intraday", "stop", 1.0, 92))
        self.assertEqual(t.signal_date, make_df(rows).index[0])
        self.assertEqual(t.entry_date, make_df(rows).index[1])


class TestWarmupGate(unittest.TestCase):
    """워밍업 게이트 [임의, 공통]: 데이터 시작 후 252봉 안(봉 번호 0~251)의 신호는 엔진이 막는다. 전략 코드는 이 규칙을 모른다.
    데이터: 300봉 횡보(시가 100, 고가 101, 저가 99, 종가 100). 신호 봉 s의 M1 주문은 봉 s+1 시가 100에 체결된다."""

    ROWS = [(100, 101, 99, 100)] * 300

    def sim(self, entries, warmup=None, rows=None):
        s = ScriptedStrategy(entries, intraday_stop(90))
        return simulate(make_df(rows or self.ROWS), s, "G", COSTS, SPEC, warmup_bars=warmup)

    def test_default_comes_from_config_and_is_252(self):
        from golden_backtest import config
        self.assertEqual(config.load("engine")["warmup_bars"], 252)

    def test_signal_on_bar_251_is_ignored_with_default_gate(self):
        """봉 번호 251 = 데이터 시작 후 252번째 봉. 252봉 안이므로 신호를 쓰지 않는다 → 거래 없음"""
        self.assertEqual(self.sim({251: NEXT_OPEN}).trades, [])

    def test_signal_on_bar_252_is_allowed_with_default_gate(self):
        """봉 252의 신호는 허용 → 봉 253 시가 100에 체결. 마지막 봉(299)까지 보유해 open_mtm 한 건: r = (100 − 100 − 0.10 − 0.10)/10 = −0.02"""
        res = self.sim({252: NEXT_OPEN})
        t = only(res, confirmed=False)
        self.assertEqual((t.entry_date, t.entry_price), (make_df(self.ROWS).index[253], 100))
        self.assertAlmostEqual(t.r_multiple, -0.02)

    def test_gate_zero_allows_first_bar_signal(self):
        res = self.sim({0: NEXT_OPEN}, warmup=0)
        self.assertEqual(only(res, confirmed=False).entry_date, make_df(self.ROWS).index[1])

    def test_gate_counts_from_the_start_of_the_data_passed_in(self):
        """df를 앞에서 100봉 잘라 넘기면 잘린 df의 봉 번호로 센다(종목별 데이터 시작 기준). 잘린 df의 봉 251(= 원래 351)은 막힌다."""
        rows = self.ROWS * 2   # 600봉
        df = make_df(rows).iloc[100:]
        s = ScriptedStrategy({251: NEXT_OPEN, 252: NEXT_OPEN}, intraday_stop(90))
        res = simulate(df, s, "G", COSTS, SPEC)
        self.assertEqual(only(res, confirmed=False).entry_date, df.index[253])  # 봉 252 신호만 통과

    def test_gate_does_not_touch_management_of_an_open_position(self):
        """봉 252에 진입한 포지션의 손절은 정상 처리된다: 봉 254 저가 85 ≤ 손절 90 → 90 체결. r = (90 − 100 − 0.10 − 0.09)/10 = −1.019"""
        rows = list(self.ROWS)
        rows[254] = (100, 101, 85, 88)
        res = self.sim({252: NEXT_OPEN}, rows=rows)
        t = only(res)
        self.assertEqual((t.exit_price, t.exit_reason), (90, "stop"))
        self.assertAlmostEqual(t.r_multiple, -1.019)


if __name__ == "__main__":
    unittest.main()
