"""technical_signals/signals.py 단위 테스트."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signals import SignalResult, _go_signal, evaluate_signals  # noqa: E402


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
        self.assertIsNone(r.composite_score)
        self.assertIsNone(r.sma_fast)
        self.assertTrue(len(r.reasons) > 0)


def _golden_cross_close() -> np.ndarray:
    """SMA200은 대부분 완만한 하락 구간을 반영해 거의 안 움직이고, 최근 15거래일의
    급반등만으로 SMA50이 SMA200을 최근(3거래일 전) 상향 돌파하도록 구성한 시나리오.
    (검증: 이 파라미터로 실제 크로스 위치를 스크립트로 확인한 뒤 고정함.)"""
    base = np.linspace(105, 95, 250)
    rally = np.linspace(95, 140, 15)
    return np.concatenate([base, rally])


class TestGoldenCross(unittest.TestCase):
    def test_fresh_golden_cross_detected(self):
        df = _mk(_golden_cross_close())
        r = evaluate_signals(df)
        self.assertIsNotNone(r.sma_fast)
        self.assertTrue(r.golden_cross, "급반등 시나리오에서는 골든크로스가 감지되어야 한다")
        self.assertFalse(r.dead_cross)

    def test_steady_decline_has_no_golden_cross(self):
        n = 300
        close = np.linspace(200, 100, n)
        df = _mk(close)
        r = evaluate_signals(df)
        self.assertFalse(r.golden_cross)


class TestRSI(unittest.TestCase):
    def test_recovery_from_oversold_flags_true(self):
        # 장기 하락으로 RSI를 30 밑까지 눌러둔 뒤, 최근 4거래일 반등으로 30선을
        # 회복하도록 구성(실제 교차 위치를 스크립트로 확인해 고정한 파라미터).
        # 전체 길이는 MIN_TRADING_DAYS(260) 이상이어야 계산이 수행된다.
        base = np.linspace(150, 80, 256)
        recover = np.linspace(80, 100, 4)
        close = np.concatenate([base, recover])
        df = _mk(close)
        r = evaluate_signals(df)
        self.assertIsNotNone(r.rsi_value)
        self.assertTrue(r.rsi_oversold_exit)

    def test_flat_series_not_oversold_exit(self):
        n = 260
        close = np.full(n, 100.0) + np.random.default_rng(3).normal(0, 0.01, n)
        df = _mk(close)
        r = evaluate_signals(df)
        self.assertFalse(r.rsi_oversold_exit)


class TestBollinger(unittest.TestCase):
    def test_upper_breakout_requires_volume(self):
        n = 260
        close = np.concatenate([np.full(n - 1, 100.0), [130.0]])  # 마지막날 큰 상승
        vol = np.concatenate([np.full(n - 1, 1_000_000.0), [3_000_000.0]])  # 거래량 3배
        df = _mk(close, vol=vol)
        r = evaluate_signals(df)
        self.assertTrue(r.bb_upper_breakout)

    def test_no_breakout_without_volume_surge(self):
        n = 260
        close = np.concatenate([np.full(n - 1, 100.0), [130.0]])
        vol = np.full(n, 1_000_000.0)  # 거래량 그대로
        df = _mk(close, vol=vol)
        r = evaluate_signals(df)
        self.assertFalse(r.bb_upper_breakout)


class TestCompositeScore(unittest.TestCase):
    def test_score_in_0_100_range_when_computable(self):
        n = 320
        rng = np.random.default_rng(7)
        close = 100 + np.cumsum(rng.normal(0.1, 1.5, n))
        df = _mk(np.clip(close, 10, None))
        r = evaluate_signals(df)
        if r.composite_score is not None:
            self.assertGreaterEqual(r.composite_score, 0.0)
            self.assertLessEqual(r.composite_score, 100.0)

    def test_fresh_multi_signal_alignment_scores_higher_than_flat(self):
        # 완만한 하락 후 최근 3거래일만 반등 + 거래량 급증 → MACD매수돌파·RSI회복·
        # 볼린저 상단돌파가 "오늘" 동시에 겹치도록 구성(오래 이어진 급등이 아니라
        # 막 시작된 상승이라야 오실레이터들이 동시에 "최근" 신호로 잡힌다).
        base = np.linspace(105, 95, 257)
        tail = np.linspace(95, 105, 3)
        close = np.concatenate([base, tail])
        n = len(close)
        vol = np.concatenate([np.full(n - 1, 1_000_000.0), [3_000_000.0]])
        df = _mk(close, vol=vol)
        r = evaluate_signals(df)
        self.assertIsNotNone(r.composite_score)
        self.assertTrue(r.macd_bull_cross)
        self.assertTrue(r.rsi_oversold_exit)
        self.assertTrue(r.bb_upper_breakout)
        self.assertGreater(r.composite_score, 50.0)

        # 완전히 평평한(신호 없는) 시나리오보다는 반드시 더 높아야 한다(단조성).
        flat_df = _mk(np.full(n, 100.0), vol=np.full(n, 1_000_000.0))
        flat_r = evaluate_signals(flat_df)
        self.assertIsNotNone(flat_r.composite_score)
        self.assertGreater(r.composite_score, flat_r.composite_score)


class TestGoSignal(unittest.TestCase):
    """추세(골든크로스) AND 모멘텀(MACD/RSI/스토캐스틱 중 하나) AND 거래량
    (OBV/볼린저상단돌파 중 하나) AND ADX필터 — 4개 카테고리 하드 AND, 카테고리
    내부는 OR. _go_signal()의 조합 로직 자체를 SignalResult를 직접 구성해
    테스트한다(실데이터형 시나리오로 4개를 동시에 맞추는 건 골든크로스처럼
    느린 지표와 RSI/MACD처럼 빠른 지표가 같은 날 동시에 "막 발생"하기
    어려워 인위적이므로, 로직은 직접, end-to-end 동작은 별도로 확인한다)."""

    def test_all_four_categories_true_gives_go_signal_true(self):
        r = SignalResult(golden_cross=True, macd_bull_cross=True, rsi_oversold_exit=False,
                          stoch_bull_cross=False, obv_rising=True, bb_upper_breakout=False,
                          adx_trending=True)
        go, momentum, volume, reasons = _go_signal(r)
        self.assertTrue(go)
        self.assertTrue(momentum)
        self.assertTrue(volume)
        self.assertIn("골든크로스", reasons)
        self.assertIn("MACD매수돌파", reasons)
        self.assertIn("OBV상승", reasons)

    def test_golden_cross_false_fails_even_if_others_true(self):
        r = SignalResult(golden_cross=False, macd_bull_cross=True, rsi_oversold_exit=True,
                          stoch_bull_cross=True, obv_rising=True, bb_upper_breakout=True,
                          adx_trending=True)
        go, _, _, _ = _go_signal(r)
        self.assertFalse(go)

    def test_momentum_category_is_or_across_substrategies(self):
        # RSI만 True 여도 모멘텀 카테고리는 충족돼야 한다(MACD·스토캐스틱까지 다 요구 안 함).
        r = SignalResult(golden_cross=True, macd_bull_cross=False, rsi_oversold_exit=True,
                          stoch_bull_cross=False, obv_rising=True, bb_upper_breakout=False,
                          adx_trending=True)
        go, momentum, _, _ = _go_signal(r)
        self.assertTrue(momentum)
        self.assertTrue(go)

    def test_all_momentum_false_fails(self):
        r = SignalResult(golden_cross=True, macd_bull_cross=False, rsi_oversold_exit=False,
                          stoch_bull_cross=False, obv_rising=True, bb_upper_breakout=False,
                          adx_trending=True)
        go, momentum, _, _ = _go_signal(r)
        self.assertFalse(momentum)
        self.assertFalse(go)

    def test_adx_filter_blocks_even_when_trend_and_momentum_align(self):
        r = SignalResult(golden_cross=True, macd_bull_cross=True, rsi_oversold_exit=False,
                          stoch_bull_cross=False, obv_rising=True, bb_upper_breakout=False,
                          adx_trending=False)
        go, _, _, _ = _go_signal(r)
        self.assertFalse(go)

    def test_none_when_any_category_uncomputable(self):
        # 골든크로스 자체가 판정 불가(None)면 다른 게 전부 True 여도 억지로
        # False 가 아니라 None 이어야 한다(sepa/의 "억지 False 금지" 원칙과 동일).
        r = SignalResult(golden_cross=None, macd_bull_cross=True, obv_rising=True, adx_trending=True)
        go, _, _, reasons = _go_signal(r)
        self.assertIsNone(go)
        self.assertEqual(reasons, [])

    def test_end_to_end_partial_match_is_not_go(self):
        # 이미 검증된 실데이터형 시나리오: 모멘텀+거래량은 충족되지만
        # 골든크로스는 아직 — 카테고리 하나라도 빠지면 go_signal=False 여야 한다.
        base = np.linspace(105, 95, 257)
        tail = np.linspace(95, 105, 3)
        close = np.concatenate([base, tail])
        n = len(close)
        vol = np.concatenate([np.full(n - 1, 1_000_000.0), [3_000_000.0]])
        df = _mk(close, vol=vol)
        r = evaluate_signals(df)
        self.assertTrue(r.momentum_trigger)
        self.assertTrue(r.volume_confirm)
        self.assertFalse(r.golden_cross)
        self.assertFalse(r.go_signal)


if __name__ == "__main__":
    unittest.main()
