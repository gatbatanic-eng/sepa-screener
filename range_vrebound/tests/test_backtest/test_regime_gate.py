"""레짐 게이팅 (Phase 0 계획 제안 1): RANGE-MR은 RANGE/NORMAL에서만,
V-REBOUND는 CRASH/RECOVERY에서만 신호를 낸다.
"""
import numpy as np
import pandas as pd

from src.config import RegimeGatesConfig, load_config
from src.pipeline import evaluate_range_mr, evaluate_v_rebound

CONFIG = load_config()


def _synthetic_ohlcv(n: int, seed: int):
    rng = np.random.default_rng(seed)
    close = pd.Series(100 + np.cumsum(rng.normal(0, 1.0, n)))
    close = close.clip(lower=10.0)
    high = close + rng.random(n) * 2
    low = close - rng.random(n) * 2
    open_ = close.shift(1).fillna(close.iloc[0])
    volume = pd.Series(1000 + rng.random(n) * 500)
    return open_, high, low, close, volume


def test_range_mr_signals_suppressed_outside_allowed_regimes():
    n = 120
    open_, high, low, close, volume = _synthetic_ohlcv(n, seed=1)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(2).normal(0, 0.5, n)))
    gate = RegimeGatesConfig(enabled=True, range_mr_regimes=["RANGE", "NORMAL"], v_rebound_regimes=["CRASH", "RECOVERY"])

    # 모든 날을 CRASH로 강제 -> RANGE-MR은 어떤 날도 신호를 내면 안 된다.
    all_crash = pd.Series(["CRASH"] * n)
    gated = evaluate_range_mr(
        open_, high, low, close, volume, benchmark, CONFIG.range_mr, market_regime=all_crash, regime_gate=gate
    )
    assert gated["signal"].isna().all() or (gated["signal"] == None).all()  # noqa: E711


def test_range_mr_signals_not_suppressed_when_gate_disabled():
    n = 120
    open_, high, low, close, volume = _synthetic_ohlcv(n, seed=1)
    benchmark = pd.Series(100 + np.cumsum(np.random.default_rng(2).normal(0, 0.5, n)))
    gate = RegimeGatesConfig(enabled=False, range_mr_regimes=["RANGE", "NORMAL"], v_rebound_regimes=["CRASH", "RECOVERY"])
    all_crash = pd.Series(["CRASH"] * n)

    ungated = evaluate_range_mr(
        open_, high, low, close, volume, benchmark, CONFIG.range_mr, market_regime=None, regime_gate=None
    )
    gated_but_disabled = evaluate_range_mr(
        open_, high, low, close, volume, benchmark, CONFIG.range_mr, market_regime=all_crash, regime_gate=gate
    )
    pd.testing.assert_series_equal(
        ungated["signal"].reset_index(drop=True), gated_but_disabled["signal"].reset_index(drop=True),
        check_names=False,
    )


def test_v_rebound_signals_suppressed_outside_allowed_regimes():
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
    gate = RegimeGatesConfig(enabled=True, range_mr_regimes=["RANGE", "NORMAL"], v_rebound_regimes=["CRASH", "RECOVERY"])

    all_normal = pd.Series(["NORMAL"] * n)
    result = evaluate_v_rebound(
        open_, high, low, close, volume, benchmark, CONFIG.v_rebound, market_regime=all_normal, regime_gate=gate
    )
    assert result["signal"].apply(lambda s: s is None).all()

    all_crash = pd.Series(["CRASH"] * n)
    result_allowed = evaluate_v_rebound(
        open_, high, low, close, volume, benchmark, CONFIG.v_rebound, market_regime=all_crash, regime_gate=gate
    )
    # CRASH 레짐에서는 원래 나왔어야 할 신호(있다면)가 그대로 남아있어야 한다
    ungated = evaluate_v_rebound(open_, high, low, close, volume, benchmark, CONFIG.v_rebound)
    assert (result_allowed["signal"].astype(str) == ungated["signal"].astype(str)).all()
