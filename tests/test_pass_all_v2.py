import unittest

import screening as S


def _r(**kw) -> S.StockResult:
    """조건1~7 전부 충족 + 레거시 RS(v1) 통과인 8/8(레거시 기준) 종목."""
    base = dict(
        code="000000", name="테스트", market="KOSPI", status="OK",
        cond1_above_150_200=True, cond2_150_above_200=True, cond3_200_rising=True,
        cond4_50_above_150_200=True, cond5_above_50=True, cond6_30pct_above_low=True,
        cond7_within_25pct_high=True, cond8_rs_rank=True, rs_percentile=75.0,
        pass_all=True, met_count=8, in_universe=True,
        v2={"trend_ok": True, "cond8_v2": True, "rs_score": 85.0},
    )
    base.update(kw)
    return S.StockResult(**base)


class ApplyV2TrendAsPassAllTest(unittest.TestCase):
    def test_v2_pass_stays_pass(self):
        r = _r()
        S.apply_v2_trend_as_pass_all([r])
        self.assertIs(r.pass_all, True)
        self.assertEqual(r.met_count, 8)

    def test_legacy_pass_but_v2_rs_below_threshold_becomes_fail(self):
        # 레거시 RS 백분위 75(>=70 통과)지만 v2 RS_Score 77(<80) — 이전에는 전체통과였다.
        r = _r(v2={"trend_ok": False, "cond8_v2": False, "rs_score": 77.0})
        S.apply_v2_trend_as_pass_all([r])
        self.assertIs(r.pass_all, False)
        self.assertEqual(r.met_count, 7)

    def test_outside_v2_universe_is_not_pass_all(self):
        r = _r(in_universe=False, v2={})
        S.apply_v2_trend_as_pass_all([r])
        self.assertIs(r.pass_all, False)
        self.assertEqual(r.met_count, 7)  # 조건1~7만 셀 수 있다(조건8 판정 불가)

    def test_v2_evaluation_failed_is_not_pass_all(self):
        r = _r(v2={})
        S.apply_v2_trend_as_pass_all([r])
        self.assertIs(r.pass_all, False)

    def test_v2_trend_unknown_is_not_pass_all(self):
        r = _r(v2={"trend_ok": None, "cond8_v2": None})
        S.apply_v2_trend_as_pass_all([r])
        self.assertIs(r.pass_all, False)

    def test_v2_pass_when_legacy_rs_failed_cannot_happen_but_follows_v2(self):
        # 이론상(백분위 <70 & RS_Score >=80)은 2주 실측에서 0건이지만, 생기면 v2를 따른다.
        r = _r(cond8_rs_rank=False, pass_all=False, met_count=7)
        S.apply_v2_trend_as_pass_all([r])
        self.assertIs(r.pass_all, True)
        self.assertEqual(r.met_count, 8)

    def test_non_ok_status_is_left_untouched(self):
        r = _r(status="확인불가", pass_all=False, met_count=None)
        S.apply_v2_trend_as_pass_all([r])
        self.assertIs(r.pass_all, False)
        self.assertIsNone(r.met_count)

    def test_legacy_reference_columns_are_kept(self):
        r = _r(v2={"trend_ok": False, "cond8_v2": False})
        S.apply_v2_trend_as_pass_all([r])
        self.assertIs(r.cond8_rs_rank, True)     # 레거시 조건8은 참고 값으로 그대로
        self.assertEqual(r.rs_percentile, 75.0)

    def test_entry_verdict_only_for_v2_passers(self):
        r = _r(v2={"trend_ok": False, "cond8_v2": False})
        S.apply_v2_trend_as_pass_all([r])
        self.assertEqual(S.compute_entry_checklist(r), (None, None, None))


if __name__ == "__main__":
    unittest.main()
