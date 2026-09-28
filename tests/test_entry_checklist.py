import unittest

import screening as S


def _r(**kw) -> S.StockResult:
    """8/8 통과 + 6개 항목 중 시장게이트·RS·52주고점·Dryup·셋업이 전부 충족인 기본 종목."""
    base = dict(
        code="000000", name="테스트", market="KOSPI",
        pass_all=True,
        market_gate_status=S.MARKET_GATE_FAVORABLE,
        rs_percentile=90.0, high52w_position=-0.05, dryup_ratio=0.5, setup_score=8.0,
        pivot_near=False, breakout_signal=False,
    )
    base.update(kw)
    return S.StockResult(**base)


class EntryTriggerTest(unittest.TestCase):
    def test_pivot_near_is_trigger(self):
        self.assertEqual(S.entry_trigger(_r(pivot_near=True)), "피벗임박")

    def test_same_day_breakout_is_trigger(self):
        self.assertEqual(S.entry_trigger(_r(breakout_signal=True)), "당일돌파")

    def test_recent_confirmed_breakout_in_valid_zone_is_trigger(self):
        r = _r(v2={"recent_breakout_days_ago": 2, "pivot_distance_pct": 1.5})
        self.assertIn("최근돌파(2일전", S.entry_trigger(r))

    def test_recent_breakout_too_old_is_not_trigger(self):
        r = _r(v2={"recent_breakout_days_ago": S.GO_RECENT_BREAKOUT_DAYS + 1, "pivot_distance_pct": 1.0})
        self.assertIsNone(S.entry_trigger(r))

    def test_recent_breakout_but_extended_or_broken_down_is_not_trigger(self):
        for dist in (5.1, 12.0, -5.1, -10.0):
            r = _r(v2={"recent_breakout_days_ago": 1, "pivot_distance_pct": dist})
            self.assertIsNone(S.entry_trigger(r), dist)

    def test_no_v2_data_falls_back_to_legacy_triggers_only(self):
        self.assertIsNone(S.entry_trigger(_r(v2={})))


class EntryChecklistTest(unittest.TestCase):
    def test_not_computed_for_non_pass_all(self):
        self.assertEqual(S.compute_entry_checklist(_r(pass_all=False)), (None, None, None))
        self.assertEqual(S.compute_entry_checklist(_r(pass_all=None)), (None, None, None))

    def test_go_is_reachable_with_pivot_near_trigger(self):
        # 예전 7항목 구조에서는 pivot_near와 breakout_signal이 동시에 참일 수 없어 GO가 불가능했다.
        count, verdict, reason = S.compute_entry_checklist(_r(pivot_near=True))
        self.assertEqual((count, verdict), (6, S.ENTRY_VERDICT_GO))
        self.assertIn("피벗임박", reason)

    def test_go_is_reachable_with_breakout_trigger(self):
        count, verdict, _ = S.compute_entry_checklist(_r(breakout_signal=True))
        self.assertEqual((count, verdict), (6, S.ENTRY_VERDICT_GO))

    def test_go_via_recent_breakout(self):
        r = _r(v2={"recent_breakout_days_ago": 3, "pivot_distance_pct": 0.5})
        count, verdict, reason = S.compute_entry_checklist(r)
        self.assertEqual((count, verdict), (6, S.ENTRY_VERDICT_GO))
        self.assertIn("최근돌파", reason)

    def test_watch_when_one_item_missing(self):
        count, verdict, _ = S.compute_entry_checklist(_r(pivot_near=True, dryup_ratio=0.9))
        self.assertEqual((count, verdict), (5, S.ENTRY_VERDICT_WATCH))

    def test_nogo_when_two_items_missing(self):
        count, verdict, _ = S.compute_entry_checklist(_r(pivot_near=True, dryup_ratio=0.9, setup_score=5.0))
        self.assertEqual((count, verdict), (4, S.ENTRY_VERDICT_NOGO))

    def test_neutral_market_gate_caps_at_watch_and_is_explained(self):
        r = _r(pivot_near=True, market_gate_status=S.MARKET_GATE_NEUTRAL)
        count, verdict, reason = S.compute_entry_checklist(r)
        self.assertEqual((count, verdict), (5, S.ENTRY_VERDICT_WATCH))
        self.assertIn("시장게이트 중립", reason)

    def test_no_trigger_is_nogo_not_go(self):
        count, verdict, reason = S.compute_entry_checklist(_r())
        self.assertEqual((count, verdict), (5, S.ENTRY_VERDICT_WATCH))
        self.assertIn("트리거 없음", reason)

    def test_v2_go_state_promotes_to_go_even_if_items_missing(self):
        r = _r(market_gate_status=S.MARKET_GATE_NEUTRAL, dryup_ratio=0.9,
               v2={"entry_state": "GO_PULLBACK"})
        count, verdict, reason = S.compute_entry_checklist(r)
        self.assertLess(count, S.GO_MIN_COUNT)
        self.assertEqual(verdict, S.ENTRY_VERDICT_GO)
        self.assertIn("GO_PULLBACK로 GO 승격", reason)

    def test_v2_go_state_ignored_for_non_pass_all(self):
        r = _r(pass_all=False, v2={"entry_state": "GO_BREAKOUT"})
        self.assertEqual(S.compute_entry_checklist(r), (None, None, None))

    def test_missing_values_do_not_count_as_satisfied(self):
        count, verdict, _ = S.compute_entry_checklist(
            _r(pivot_near=True, rs_percentile=None, high52w_position=None, dryup_ratio=None, setup_score=None))
        self.assertEqual(count, 2)
        self.assertEqual(verdict, S.ENTRY_VERDICT_NOGO)


if __name__ == "__main__":
    unittest.main()
