"""Market Regime Engine 테스트 (스펙 6조).

CONFIG의 실제 값 대신, 테스트 가독성을 위해 작은 숫자로 만든 전용 설정을
쓴다 (period가 짧을수록 합성 데이터로 경계값을 만들기 쉽다).
"""
import numpy as np
import pandas as pd
import pytest

from src.config import (
    CrashConfig,
    MarketRegimeConfig,
    RangeRegimeConfig,
    RecoveryConfig,
)
from src.market.regime import RegimeType, compute_regime_series

CONFIG = MarketRegimeConfig(
    crash=CrashConfig(return_20d_max=-0.10, drawdown_60d_max=-0.15),
    range=RangeRegimeConfig(lookback_days=10, abs_return_max=0.05, band_width_max=0.10),
    recovery=RecoveryConfig(lookback_days=5),
)


def _flat_then_crash_series(flat_days: int, crash_pct: float) -> pd.Series:
    """flat_days 동안 100에서 횡보하다가 하루 만에 crash_pct 만큼 급락하는 시계열."""
    flat = [100.0] * flat_days
    crashed = 100.0 * (1 + crash_pct)
    return pd.Series(flat + [crashed])


def test_crash_via_20d_return_boundary():
    # 20거래일 전 100 -> 오늘 90: 정확히 -10% (경계값, <= 이므로 CRASH여야 함)
    close = pd.Series([100.0] * 21 + [90.0])
    df = compute_regime_series(close, CONFIG)
    assert df["regime"].iloc[-1] == RegimeType.CRASH
    assert df["return_20d"].iloc[-1] == pytest.approx(-0.10)


def test_not_crash_just_above_20d_threshold():
    close = pd.Series([100.0] * 21 + [90.01])
    df = compute_regime_series(close, CONFIG)
    assert df["regime"].iloc[-1] != RegimeType.CRASH


def test_crash_via_60d_drawdown_even_without_20d_trigger():
    # 완만하게 60거래일에 걸쳐 -15%까지 하락 (20일 수익률 조건은 안 걸리게).
    # 60일 롤링 윈도우가 정확히 index0..59를 덮도록 60개 포인트를 쓴다.
    n = 60
    close = pd.Series(np.linspace(100.0, 85.0, n))
    df = compute_regime_series(close, CONFIG)
    last = df.iloc[-1]
    assert last["return_20d"] > -0.10  # 완만한 하락이라 20일 조건은 미충족
    assert last["drawdown_60d"] == pytest.approx(-0.15, abs=1e-6)
    assert last["regime"] == RegimeType.CRASH


def test_range_when_flat_and_narrow_band():
    # 10일 구간 동안 98~102 사이에서만 움직임: |수익률| 작고 밴드폭 좁음.
    close = pd.Series([100.0, 101.0, 99.0, 102.0, 98.0, 100.0, 101.0, 99.0, 100.0, 100.0, 101.0])
    df = compute_regime_series(close, CONFIG)
    assert df["regime"].iloc[-1] == RegimeType.RANGE


def test_normal_when_no_condition_met():
    # 꾸준한 강한 상승 추세(10일 수익률이 abs_return_max=5%를 넘도록 가파르게):
    # RANGE(밴드 좁음)도 CRASH도 아님.
    close = pd.Series(np.linspace(100.0, 200.0, 65))
    df = compute_regime_series(close, CONFIG)
    assert df["regime"].iloc[-1] == RegimeType.NORMAL


def test_recovery_immediately_after_crash_day():
    # 21일차에 CRASH 발생, 22일차는 그 자체로는 CRASH 조건이 아니지만
    # recovery.lookback_days(5) 이내이므로 RECOVERY.
    close = pd.Series([100.0] * 21 + [90.0, 91.0])
    df = compute_regime_series(close, CONFIG)
    assert df["regime"].iloc[-2] == RegimeType.CRASH  # 21일차(급락 당일)
    assert df["regime"].iloc[-1] == RegimeType.RECOVERY  # 22일차


def test_recovery_window_expires_after_lookback_days():
    # CRASH 당일 이후 recovery.lookback_days(5)를 초과하면 더 이상 RECOVERY가 아니다.
    close = pd.Series([100.0] * 21 + [90.0] + [90.5, 91.0, 91.5, 92.0, 92.5, 93.0])
    df = compute_regime_series(close, CONFIG)
    # crash_day 인덱스 = 21. lookback=5 -> 22,23,24,25,26까지 RECOVERY, 27부터는 아님.
    assert df["regime"].iloc[26] == RegimeType.RECOVERY
    assert df["regime"].iloc[27] != RegimeType.RECOVERY


def test_crash_takes_priority_over_recovery_and_range():
    # RECOVERY 관찰 기간 도중 다시 급락하면 CRASH가 우선한다.
    close = pd.Series([100.0] * 21 + [90.0, 90.5] + [90.5 * 0.89])
    df = compute_regime_series(close, CONFIG)
    assert df["regime"].iloc[-1] == RegimeType.CRASH


def test_insufficient_history_defaults_to_normal():
    close = pd.Series([100.0, 101.0, 99.0])
    df = compute_regime_series(close, CONFIG)
    assert (df["regime"] == RegimeType.NORMAL).all()


def test_regime_series_is_causal_no_lookahead():
    np.random.seed(7)
    close = pd.Series(100 + np.cumsum(np.random.randn(80)))
    cut = 50
    full = compute_regime_series(close, CONFIG)
    truncated = compute_regime_series(close.iloc[: cut + 1], CONFIG)
    assert full["regime"].iloc[cut] == truncated["regime"].iloc[cut]
    assert full["return_20d"].iloc[cut] == pytest.approx(truncated["return_20d"].iloc[cut])
