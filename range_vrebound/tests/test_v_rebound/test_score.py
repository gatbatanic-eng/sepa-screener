import pandas as pd
import pytest

from src.config import load_config
from src.models.signal import QualityStatus, SignalState
from src.scoring.v_rebound_score import classify_signal, compute_v_rebound_score

CONFIG = load_config().v_rebound


def _metrics_row(**overrides) -> pd.DataFrame:
    base = dict(
        drawdown=CONFIG.filter.drawdown_max,  # -25%, 기준 정확히 충족 -> 만점
        excess_return_60d=CONFIG.filter.excess_return_60d_max_pp,  # -10%p, 만점
        is_stabilized=True,
        recovered_3pct=True,
        rebound_volume_ratio=CONFIG.volume.rebound_ratio_very_strong,  # 2.0 -> 만점
        rs_5d=0.05,
        breakout=True,
    )
    base.update(overrides)
    return pd.DataFrame([base])


def test_v_rebound_score_full_marks_when_all_components_at_best():
    metrics = _metrics_row()
    result = compute_v_rebound_score(metrics, CONFIG)
    assert result["total_score"].iloc[0] == pytest.approx(100.0)
    assert result["quality_status"].iloc[0] == QualityStatus.UNKNOWN


def test_v_rebound_score_zero_when_all_components_at_worst():
    metrics = _metrics_row(
        drawdown=0.0,
        excess_return_60d=0.0,
        is_stabilized=False,
        recovered_3pct=False,
        rebound_volume_ratio=1.0,
        rs_5d=-0.01,
        breakout=False,
    )
    result = compute_v_rebound_score(metrics, CONFIG)
    assert result["total_score"].iloc[0] == pytest.approx(0.0)


def test_v_rebound_score_stabilization_partial_credit():
    # 안정화는 됐지만 +3% 회복 보너스는 아직 -> stabilization 배점의 2/3만
    metrics = _metrics_row(
        drawdown=0.0, excess_return_60d=0.0, rebound_volume_ratio=1.0, rs_5d=-0.01, breakout=False,
        is_stabilized=True, recovered_3pct=False,
    )
    result = compute_v_rebound_score(metrics, CONFIG)
    expected = (CONFIG.score_weights.stabilization * (2 / 3)) / _non_excluded_weight_sum() * 100
    assert result["total_score"].iloc[0] == pytest.approx(expected)


def _non_excluded_weight_sum() -> float:
    w = CONFIG.score_weights
    return w.abs_drawdown + w.market_excess_drawdown + w.stabilization + w.volume_recovery + w.relative_strength + w.breakout


def test_v_rebound_score_bounded_0_to_100_on_extreme_inputs():
    metrics = _metrics_row(drawdown=-0.99, excess_return_60d=-0.99, rebound_volume_ratio=10.0, rs_5d=5.0)
    result = compute_v_rebound_score(metrics, CONFIG)
    assert 0.0 <= result["total_score"].iloc[0] <= 100.0


def test_v_rebound_score_nan_inputs_do_not_crash():
    metrics = _metrics_row(drawdown=float("nan"), rebound_volume_ratio=float("nan"), rs_5d=float("nan"))
    result = compute_v_rebound_score(metrics, CONFIG)
    assert not pd.isna(result["total_score"].iloc[0])


@pytest.mark.parametrize("score,expected", [(69, None), (70, SignalState.SETUP)])
def test_classify_signal_setup_boundary(score, expected):
    result = classify_signal(
        total_score=score, is_stabilized=False, breakout=False, rebound_volume_ratio=0.0,
        rr_1=None, is_invalidated=False, config=CONFIG,
    )
    assert result == expected


def test_classify_signal_watch_requires_stabilization():
    result = classify_signal(
        total_score=80, is_stabilized=False, breakout=False, rebound_volume_ratio=0.0,
        rr_1=None, is_invalidated=False, config=CONFIG,
    )
    assert result == SignalState.SETUP

    result = classify_signal(
        total_score=80, is_stabilized=True, breakout=False, rebound_volume_ratio=0.0,
        rr_1=None, is_invalidated=False, config=CONFIG,
    )
    assert result == SignalState.WATCH


def test_classify_signal_buy_score_boundary_74_vs_75():
    # 스펙 23조 경계값: buy_score_min=75. 74는 WATCH까지만.
    common = dict(is_stabilized=True, breakout=True, rebound_volume_ratio=2.0, rr_1=5.0, is_invalidated=False, config=CONFIG)
    result = classify_signal(total_score=74, **common)
    assert result == SignalState.WATCH

    result = classify_signal(total_score=75, **common)
    assert result == SignalState.BUY_CANDIDATE


def test_classify_signal_buy_candidate_requires_all_conditions():
    common = dict(total_score=80, is_stabilized=True, breakout=True, rebound_volume_ratio=1.3, config=CONFIG)
    # RR 미달
    result = classify_signal(rr_1=1.99, is_invalidated=False, **common)
    assert result == SignalState.WATCH
    # RR 충족
    result = classify_signal(rr_1=2.0, is_invalidated=False, **common)
    assert result == SignalState.BUY_CANDIDATE


def test_classify_signal_buy_candidate_requires_score_75_not_just_70():
    result = classify_signal(
        total_score=70, is_stabilized=True, breakout=True, rebound_volume_ratio=2.0,
        rr_1=3.0, is_invalidated=False, config=CONFIG,
    )
    assert result == SignalState.WATCH  # score 75 미만이라 BUY_CANDIDATE 아님


def test_classify_signal_invalidated_overrides_everything():
    result = classify_signal(
        total_score=95, is_stabilized=True, breakout=True, rebound_volume_ratio=2.0,
        rr_1=5.0, is_invalidated=True, config=CONFIG,
    )
    assert result == SignalState.INVALIDATED
