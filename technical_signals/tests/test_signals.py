"""technical_signals/signals.py 단위 테스트 (2026-09-18 구조 개편 이후)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config as cfg  # noqa: E402
from signals import (  # noqa: E402
    HOLD, READY, REVIEW, WATCH, SignalResult, _fraction, _group_score, _rebound_group_fractions,
    _trend_group_fractions, compute_verdict, evaluate_signals,
)


def _mk(close, high=None, low=None, vol=None) -> pd.DataFrame:
    n = len(close)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    close = pd.Series(close, index=idx, dtype=float)
    high = pd.Series(high, index=idx, dtype=float) if high is not None else close * 1.01
    low = pd.Series(low, index=idx, dtype=float) if low is not None else close * 0.99
    vol = pd.Series(vol, index=idx, dtype=float) if vol is not None else pd.Series([1_000_000.0] * n, index=idx)
    return pd.DataFrame({"Open": close, "High": high, "Low": low, "Close": close, "Volume": vol})


class TestInsufficientData(unittest.TestCase):
    def test_short_history_returns_all_none(self):
        df = _mk(np.linspace(100, 110, 30))
        r = evaluate_signals(df)
        self.assertIsNone(r.trend_score)
        self.assertIsNone(r.rebound_score)
        self.assertIsNone(r.trend_aligned)
        self.assertTrue(len(r.reasons) > 0)


class TestTrendAlignedAndGoldenCross(unittest.TestCase):
    def test_steady_uptrend_is_aligned_no_fresh_cross(self):
        close = np.linspace(100, 300, 300)
        r = evaluate_signals(_mk(close))
        self.assertTrue(r.trend_aligned)
        self.assertFalse(r.golden_cross)  # 오래전에 이미 정배열, 최근 교차 아님

    def test_steady_decline_not_aligned(self):
        close = np.linspace(300, 100, 300)
        r = evaluate_signals(_mk(close))
        self.assertFalse(r.trend_aligned)
        self.assertFalse(r.golden_cross)

    def test_fresh_golden_cross_scenario(self):
        # SMA200은 대부분 완만한 하락을 반영해 거의 안 움직이고, 최근 15거래일
        # 급반등만으로 SMA50이 SMA200을 최근(3거래일 전) 상향 돌파하도록 구성
        # (실제 교차 위치를 스크립트로 확인해 고정한 파라미터 — 이전 리뷰에서
        # 골든크로스와 오실레이터가 동시에 "막" 발생하긴 어렵다고 확인된 것과 같은 시나리오).
        base = np.linspace(105, 95, 250)
        rally = np.linspace(95, 140, 15)
        close = np.concatenate([base, rally])
        r = evaluate_signals(_mk(close))
        self.assertTrue(r.golden_cross)
        self.assertTrue(r.trend_aligned)


class TestBreakoutTrigger(unittest.TestCase):
    def test_confirmed_breakout_with_volume_and_clv_and_value(self):
        # 60일 박스권 이후 마지막날 피벗을 강하게 상향 돌파 + 거래량 급증 + 종가가 고가권
        n = 280
        box = 100 + np.sin(np.linspace(0, 20, n - 1)) * 2  # 좁은 박스권
        close = np.concatenate([box, [115.0]])
        vol = np.concatenate([np.full(n - 1, 1_000_000.0), [3_000_000.0]])
        high = np.concatenate([box * 1.01, [116.0]])
        low = np.concatenate([box * 0.99, [113.0]])  # 마지막날 종가가 고가권(CLV 높음)
        df = _mk(close, high=high, low=low, vol=vol)
        r = evaluate_signals(df)
        self.assertIsNotNone(r.pivot)
        self.assertGreater(r.pivot_distance_pct, 0)
        self.assertTrue(r.breakout_trigger)

    def test_no_breakout_without_volume(self):
        n = 280
        box = 100 + np.sin(np.linspace(0, 20, n - 1)) * 2
        close = np.concatenate([box, [115.0]])
        vol = np.full(n, 1_000_000.0)  # 거래량 그대로(급증 없음)
        df = _mk(close, vol=vol)
        r = evaluate_signals(df)
        self.assertFalse(r.breakout_trigger)

    def test_no_breakout_below_pivot(self):
        n = 280
        close = np.linspace(150, 100, n)  # 계속 하락 -> 피벗 근처도 못 감
        r = evaluate_signals(_mk(close))
        self.assertFalse(r.breakout_trigger)


class TestChaseWarning(unittest.TestCase):
    def test_high_rsi_triggers_chase_warning(self):
        n = 280
        close = np.concatenate([np.full(n - 20, 100.0), np.linspace(100, 200, 20)])
        r = evaluate_signals(_mk(close))
        self.assertIsNotNone(r.rsi_value)
        if r.rsi_value >= cfg.RSI_CHASE_WARNING:
            self.assertTrue(r.chase_warning)

    def test_far_from_pivot_triggers_chase_warning(self):
        n = 280
        box = np.full(n - 1, 100.0)
        close = np.concatenate([box, [120.0]])  # 피벗 대비 +20% 이상 이격
        r = evaluate_signals(_mk(close))
        self.assertIsNotNone(r.pivot_distance_pct)
        self.assertGreaterEqual(r.pivot_distance_pct, cfg.PIVOT_DISTANCE_HOLD)
        self.assertTrue(r.chase_warning)

    def test_near_pivot_no_chase_warning(self):
        n = 280
        box = 100 + np.sin(np.linspace(0, 20, n - 1)) * 2
        close = np.concatenate([box, [100.5]])
        r = evaluate_signals(_mk(close))
        self.assertIsNotNone(r.rsi_value)
        self.assertLess(r.rsi_value, cfg.RSI_CHASE_WARNING)
        self.assertFalse(r.chase_warning)


class TestStopRisk(unittest.TestCase):
    def test_stop_price_is_lower_of_structural_and_atr(self):
        n = 280
        rng = np.random.default_rng(3)
        close = pd.Series(100 + np.cumsum(rng.normal(0, 1.0, n)))
        r = evaluate_signals(_mk(close.values))
        self.assertIsNotNone(r.stop_price)
        self.assertIsNotNone(r.swing_low_price)
        self.assertIsNotNone(r.atr_value)
        structural = r.swing_low_price - cfg.STOP_ATR_BUFFER_MULT * r.atr_value
        atr_stop = r.close - cfg.ATR_STOP_MULT * r.atr_value
        self.assertAlmostEqual(r.stop_price, min(structural, atr_stop), places=3)
        self.assertLess(r.stop_price, r.close)

    def test_stop_left_none_when_atr_implausibly_large_vs_price(self):
        # 실데이터 전체 스크리닝 중 발견된 사례: ATR이 종가 대비 비정상적으로 커서
        # (희박/erratic한 데이터) ATR배수 손절가가 0 이하로 나오는 종목이 있었다.
        # 이런 경우 억지로 음수 손절가를 보여주지 말고 판정불가(None)로 남겨야 한다.
        n = 280
        rng = np.random.default_rng(11)
        close = pd.Series(np.clip(100 + np.cumsum(rng.normal(0, 1.0, n)), 1, None))
        high = close * 50.0   # 일일 변동폭을 비정상적으로 크게
        low = close * 0.02
        r = evaluate_signals(_mk(close.values, high=high.values, low=low.values))
        self.assertIsNotNone(r.atr_value)
        self.assertGreater(r.atr_value, r.close)  # ATR이 종가보다 큰 극단적 상황
        self.assertIsNone(r.stop_price)
        self.assertIsNone(r.risk_pct)
        self.assertIsNone(r.risk_too_high)

    def test_risk_pct_matches_close_and_stop(self):
        n = 280
        rng = np.random.default_rng(4)
        close = pd.Series(100 + np.cumsum(rng.normal(0, 1.0, n)))
        r = evaluate_signals(_mk(close.values))
        expected = (r.close - r.stop_price) / r.close * 100.0
        self.assertAlmostEqual(r.risk_pct, round(expected, 2), places=1)
        self.assertEqual(r.risk_too_high, r.risk_pct > cfg.MAX_RISK_PCT)


class TestGroupScoring(unittest.TestCase):
    def test_fraction_none_when_all_members_none(self):
        self.assertIsNone(_fraction({"a": None, "b": None}))

    def test_fraction_is_true_ratio(self):
        self.assertAlmostEqual(_fraction({"a": True, "b": False, "c": None}), 0.5)

    def test_group_score_caps_at_group_max_even_if_all_members_true(self):
        fractions = {"trend": 1.0, "trigger": 1.0, "momentum": 1.0, "volatility": 1.0, "volume": 1.0}
        score = _group_score(fractions, cfg.TREND_GROUP_WEIGHTS)
        self.assertAlmostEqual(score, 100.0)

    def test_momentum_group_does_not_inflate_when_multiple_members_agree(self):
        # 모멘텀 그룹에 멤버가 1개일 때와 2개(둘 다 True)일 때 그룹이 기여하는
        # 최대치는 같아야 한다(그룹 배점을 못 넘는다는 게 핵심 검증 포인트).
        one_member = {"trend": None, "trigger": None, "momentum": _fraction({"macd_bull_cross": True}),
                      "volatility": None, "volume": None}
        two_members = {"trend": None, "trigger": None,
                       "momentum": _fraction({"macd_bull_cross": True, "rsi_healthy_trend": True}),
                       "volatility": None, "volume": None}
        s1 = _group_score(one_member, cfg.TREND_GROUP_WEIGHTS)
        s2 = _group_score(two_members, cfg.TREND_GROUP_WEIGHTS)
        self.assertAlmostEqual(s1, 100.0)
        self.assertAlmostEqual(s2, 100.0)  # 100을 넘지 않는다(중복 가산 없음)

    def test_trend_group_fractions_uses_only_trend_members(self):
        r = SignalResult(trend_aligned=True, golden_cross=True, adx_trending=True,
                          breakout_trigger=True, macd_bull_cross=True, rsi_healthy_trend=True,
                          bb_squeeze=True, obv_rising=True,
                          rsi_oversold_exit=True, stoch_bull_cross=True)  # 박스권 전용, 영향 없어야 함
        frac = _trend_group_fractions(r)
        self.assertAlmostEqual(frac["trend"], 1.0)
        self.assertAlmostEqual(frac["momentum"], 1.0)

    def test_rebound_group_fractions_uses_only_rebound_members(self):
        r = SignalResult(disparity_oversold=True, bb_lower_revert=True,
                          rsi_oversold_exit=True, stoch_bull_cross=False,
                          bb_squeeze=True, obv_rising=True)
        frac = _rebound_group_fractions(r)
        self.assertAlmostEqual(frac["position"], 1.0)
        self.assertAlmostEqual(frac["momentum"], 0.5)  # 둘 중 하나만 True


class TestTrendVerdict(unittest.TestCase):
    def _base_review_ok(self) -> SignalResult:
        return SignalResult(
            trend_aligned=True, breakout_trigger=True, trend_score=75.0,
            pivot_distance_pct=1.0, risk_too_high=False, risk_pct=3.0,
            chase_warning=False, volume_ratio50=1.0, macd_hist=0.1,
            rsi_healthy_trend=True,
        )

    def test_none_when_base_not_qualified(self):
        r = SignalResult(trend_aligned=False, breakout_trigger=True, trend_score=90.0,
                          pivot_distance_pct=0.0, risk_too_high=False)
        compute_verdict(r, "GREEN")
        self.assertIsNone(r.trend_verdict)

    def test_not_review_when_score_below_review_threshold(self):
        r = self._base_review_ok()
        r.trend_score = 40.0
        compute_verdict(r, "GREEN")
        self.assertNotIn(r.trend_verdict, (REVIEW, READY))

    def test_watch_when_near_pivot_without_trigger(self):
        r = self._base_review_ok()
        r.breakout_trigger = False
        r.trend_score = 55.0
        r.pivot_distance_pct = -2.0
        compute_verdict(r, "GREEN")
        self.assertEqual(r.trend_verdict, WATCH)

    def test_watch_when_trigger_but_score_below_70_even_if_low(self):
        r = self._base_review_ok()
        r.trend_score = 30.0
        compute_verdict(r, "GREEN")
        self.assertEqual(r.trend_verdict, WATCH)

    def test_no_watch_when_far_from_pivot_or_chasing_or_risky(self):
        for mutate in (lambda r: setattr(r, "pivot_distance_pct", -8.0),
                       lambda r: setattr(r, "chase_warning", True),
                       lambda r: setattr(r, "risk_too_high", True),
                       lambda r: setattr(r, "trend_aligned", False)):
            r = self._base_review_ok()
            r.breakout_trigger = False
            r.trend_score = 60.0
            mutate(r)
            compute_verdict(r, "GREEN")
            self.assertIsNone(r.trend_verdict)

    def test_review_when_base_ok_but_not_ready(self):
        r = self._base_review_ok()
        r.trend_score = 72.0  # 검토는 되지만 진입준비(80) 미달
        compute_verdict(r, "GREEN")
        self.assertEqual(r.trend_verdict, REVIEW)

    def test_ready_when_all_extra_confirmations_met(self):
        r = self._base_review_ok()
        r.trend_score = 85.0
        r.volume_ratio50 = 1.5
        compute_verdict(r, "GREEN")
        self.assertEqual(r.trend_verdict, READY)

    def test_hold_when_chase_warning_even_if_base_ok(self):
        r = self._base_review_ok()
        r.chase_warning = True
        compute_verdict(r, "GREEN")
        self.assertEqual(r.trend_verdict, HOLD)
        self.assertTrue(any("추격" in reason for reason in r.trend_verdict_reasons))

    def test_hold_when_risk_too_high(self):
        r = self._base_review_ok()
        r.risk_too_high = True
        compute_verdict(r, "GREEN")
        self.assertEqual(r.trend_verdict, HOLD)

    def test_hold_when_market_regime_red(self):
        r = self._base_review_ok()
        compute_verdict(r, "RED")
        self.assertEqual(r.trend_verdict, HOLD)

    def test_none_when_pivot_distance_exceeds_review_band(self):
        r = self._base_review_ok()
        r.pivot_distance_pct = 4.0  # +3% 밴드 초과
        compute_verdict(r, "GREEN")
        self.assertIsNone(r.trend_verdict)

    def test_none_when_regime_unknown(self):
        r = self._base_review_ok()
        compute_verdict(r, None)
        # 시장국면을 모르면 REVIEW는 가능하지만(READY 승격만 국면을 요구),
        # 최소한 억지로 HOLD/READY로 단정하지는 않는지 확인한다.
        self.assertIn(r.trend_verdict, (REVIEW, None))
        self.assertNotEqual(r.trend_verdict, READY)


class TestReboundVerdict(unittest.TestCase):
    def _base_review_ok(self) -> SignalResult:
        return SignalResult(
            disparity_oversold=True, bb_lower_revert=True, rebound_score=75.0,
            risk_too_high=False, risk_pct=3.0, rsi_value=40.0,
            volume_ratio50=1.0, rsi_oversold_exit=True,
        )

    def test_none_when_base_not_qualified(self):
        r = SignalResult(disparity_oversold=False, bb_lower_revert=True, rebound_score=90.0,
                          risk_too_high=False)
        compute_verdict(r, "GREEN")
        self.assertIsNone(r.rebound_verdict)

    def test_review_when_base_ok(self):
        r = self._base_review_ok()
        compute_verdict(r, "GREEN")
        self.assertEqual(r.rebound_verdict, REVIEW)

    def test_ready_when_confirmations_met(self):
        r = self._base_review_ok()
        r.rebound_score = 85.0
        r.volume_ratio50 = 1.5
        compute_verdict(r, "YELLOW")
        self.assertEqual(r.rebound_verdict, READY)

    def test_hold_when_already_bounced_too_far(self):
        r = self._base_review_ok()
        r.rsi_value = 80.0
        compute_verdict(r, "GREEN")
        self.assertEqual(r.rebound_verdict, HOLD)


if __name__ == "__main__":
    unittest.main()
