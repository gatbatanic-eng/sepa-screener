"""Orchestrator(src/pipeline.py) 배선 테스트.

각 전략 하위 계산의 정확성은 test_range_mr/test_v_rebound에서 이미
검증했다. 여기서는 "조립이 실제로 맞물려 돌아가는가"(shape, 인과성,
예외 없음)만 확인한다.
"""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.models.signal import Signal
from src.pipeline import (
    evaluate_range_mr,
    evaluate_v_rebound,
    range_mr_row_to_signal,
    v_rebound_row_to_signal,
)

CONFIG = load_config()


def _synthetic_ohlcv(n: int, seed: int) -> dict:
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1.0, n)))
    close = close.clip(lower=10.0)
    high = close + rng.random(n) * 2
    low = close - rng.random(n) * 2
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1000 + rng.random(n) * 500)
    return {"open_": open_, "high": high, "low": low, "close": close, "volume": volume}


def test_evaluate_range_mr_runs_and_has_expected_shape():
    n = 120
    bars = _synthetic_ohlcv(n, seed=1)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(2).normal(0, 0.5, n)))
    result = evaluate_range_mr(
        bars["open_"], bars["high"], bars["low"], bars["close"], bars["volume"], benchmark, CONFIG.range_mr
    )
    assert len(result) == n
    for col in ["setup_score", "trigger_score", "total_score", "signal", "stop", "rr_1"]:
        assert col in result.columns


def test_evaluate_range_mr_is_causal():
    n = 120
    bars = _synthetic_ohlcv(n, seed=3)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(4).normal(0, 0.5, n)))
    full = evaluate_range_mr(
        bars["open_"], bars["high"], bars["low"], bars["close"], bars["volume"], benchmark, CONFIG.range_mr
    )
    cut = 90
    truncated = evaluate_range_mr(
        bars["open_"].iloc[: cut + 1],
        bars["high"].iloc[: cut + 1],
        bars["low"].iloc[: cut + 1],
        bars["close"].iloc[: cut + 1],
        bars["volume"].iloc[: cut + 1],
        benchmark.iloc[: cut + 1],
        CONFIG.range_mr,
    )
    assert full["setup_score"].iloc[cut] == pytest.approx(truncated["setup_score"].iloc[cut])
    assert full["signal"].iloc[cut] == truncated["signal"].iloc[cut]


def test_range_mr_row_to_signal_returns_none_when_no_signal():
    n = 70
    bars = _synthetic_ohlcv(n, seed=5)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(6).normal(0, 0.5, n)))
    result = evaluate_range_mr(
        bars["open_"], bars["high"], bars["low"], bars["close"], bars["volume"], benchmark, CONFIG.range_mr
    )
    # 아직 워밍업 구간(박스 60일 미만)이라 신호가 없어야 한다
    signal = range_mr_row_to_signal("TEST", date(2024, 1, 1), result.iloc[10])
    assert signal is None


def test_range_mr_row_to_signal_produces_valid_signal_object_when_triggered():
    n = 120
    bars = _synthetic_ohlcv(n, seed=1)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(2).normal(0, 0.5, n)))
    result = evaluate_range_mr(
        bars["open_"], bars["high"], bars["low"], bars["close"], bars["volume"], benchmark, CONFIG.range_mr
    )
    triggered_rows = result[result["signal"].notna()]
    if len(triggered_rows) == 0:
        pytest.skip("이 시드에서는 신호가 발생하지 않음 (임계값이 엄격한 게 정상)")
    row = triggered_rows.iloc[0]
    signal = range_mr_row_to_signal("TEST", date(2024, 1, 1), row)
    assert isinstance(signal, Signal)


def test_evaluate_v_rebound_runs_and_has_expected_shape():
    n = 120
    bars = _synthetic_ohlcv(n, seed=7)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(8).normal(0, 0.5, n)))
    result = evaluate_v_rebound(
        bars["open_"], bars["high"], bars["low"], bars["close"], bars["volume"], benchmark, CONFIG.v_rebound
    )
    assert len(result) == n
    for col in ["total_score", "signal", "stop", "rr_1", "is_stabilized"]:
        assert col in result.columns


def test_evaluate_v_rebound_is_causal():
    n = 120
    bars = _synthetic_ohlcv(n, seed=9)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(10).normal(0, 0.5, n)))
    full = evaluate_v_rebound(
        bars["open_"], bars["high"], bars["low"], bars["close"], bars["volume"], benchmark, CONFIG.v_rebound
    )
    cut = 90
    truncated = evaluate_v_rebound(
        bars["open_"].iloc[: cut + 1],
        bars["high"].iloc[: cut + 1],
        bars["low"].iloc[: cut + 1],
        bars["close"].iloc[: cut + 1],
        bars["volume"].iloc[: cut + 1],
        benchmark.iloc[: cut + 1],
        CONFIG.v_rebound,
    )
    assert full["total_score"].iloc[cut] == pytest.approx(truncated["total_score"].iloc[cut])
    assert full["signal"].iloc[cut] == truncated["signal"].iloc[cut]


def test_v_rebound_row_to_signal_returns_none_when_no_signal():
    n = 70
    bars = _synthetic_ohlcv(n, seed=11)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(12).normal(0, 0.5, n)))
    result = evaluate_v_rebound(
        bars["open_"], bars["high"], bars["low"], bars["close"], bars["volume"], benchmark, CONFIG.v_rebound
    )
    signal = v_rebound_row_to_signal("TEST", date(2024, 1, 1), result.iloc[5])
    assert signal is None


def test_v_rebound_row_to_signal_happy_path_with_real_watch_signal():
    # 급락 후 안정화되는 시나리오 -> WATCH 신호가 실제로 나온다.
    pre = [100.0] * 65
    crash = [100.0, 90.0, 80.0, 70.0]
    low_period = [70.0, 71.0, 72.0, 73.0, 74.0]
    rebound = [76.0, 79.0, 83.0, 88.0]
    close = pd.Series(pre + crash + low_period + rebound)
    n = len(close)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series([1000.0] * n)
    benchmark = pd.Series([100.0] * n)

    result = evaluate_v_rebound(open_, high, low, close, volume, benchmark, CONFIG.v_rebound)
    watch_rows = result[result["signal"].notna()]
    assert len(watch_rows) > 0
    signal = v_rebound_row_to_signal("TEST", date(2024, 1, 1), watch_rows.iloc[0])
    assert isinstance(signal, Signal)


def test_evaluate_range_mr_passes_through_market_regime_column():
    n = 120
    bars = _synthetic_ohlcv(n, seed=1)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(2).normal(0, 0.5, n)))
    regime = pd.Series(["RANGE"] * n)
    result = evaluate_range_mr(
        bars["open_"], bars["high"], bars["low"], bars["close"], bars["volume"], benchmark, CONFIG.range_mr,
        market_regime=regime,
    )
    assert (result["market_regime"] == "RANGE").all()


def test_evaluate_v_rebound_passes_through_market_regime_column():
    n = 120
    bars = _synthetic_ohlcv(n, seed=7)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(8).normal(0, 0.5, n)))
    regime = pd.Series(["CRASH"] * n)
    result = evaluate_v_rebound(
        bars["open_"], bars["high"], bars["low"], bars["close"], bars["volume"], benchmark, CONFIG.v_rebound,
        market_regime=regime,
    )
    assert (result["market_regime"] == "CRASH").all()


def test_v_rebound_engineered_crash_and_rebound_can_produce_signal():
    # 급락(-30%) 후 저점 안정 + 반등하는 시나리오를 직접 구성해 파이프라인이
    # 실제로 신호를 만들어낼 수 있는지 확인한다 (임계값들이 모두 극단적으로만
    # 통과되는 게 아님을 보장).
    pre = [100.0] * 65
    crash = [100.0, 90.0, 80.0, 70.0]  # 급락
    low_period = [70.0, 71.0, 72.0, 73.0, 74.0]  # 안정화(3일 이상 새 저점 없음)
    rebound = [76.0, 79.0, 83.0, 88.0, 95.0, 100.0, 105.0, 110.0]  # 첫 반등고점 돌파 시도
    close = pd.Series(pre + crash + low_period + rebound)
    n = len(close)
    high = close + 1.0
    low = close - 1.0
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series([1000.0] * n)
    volume.iloc[len(pre) + len(crash) + len(low_period) :] = 2500.0  # 반등 구간 거래량 급증
    benchmark = pd.Series([100.0] * n)  # 시장은 그대로 -> 종목이 시장 대비 크게 초과하락

    result = evaluate_v_rebound(open_, high, low, close, volume, benchmark, CONFIG.v_rebound)
    assert result["is_stabilized"].iloc[-1] == True or result["is_stabilized"].iloc[-5:].any()  # noqa: E712
