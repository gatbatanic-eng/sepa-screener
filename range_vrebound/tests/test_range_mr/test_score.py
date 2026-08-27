import pandas as pd
import pytest

from src.config import load_config
from src.models.signal import QualityStatus, SignalState
from src.scoring.range_score import classify_signal, compute_setup_score, is_setup_invalidated

CONFIG = load_config().range_mr


def _metrics_row(**overrides) -> pd.DataFrame:
    base = dict(
        box_width=0.275,  # width_min(0.15)~width_max(0.40)의 정중앙
        box_position=0.0,
        support_touch_count=2,
        reversion_ratio=1.0,
        rsi=0.0,
        avg_trading_value_krw=CONFIG.liquidity.min_avg_trading_value_krw,
        rr_1=2.0,
    )
    base.update(overrides)
    return pd.DataFrame([base])


def test_setup_score_full_marks_when_all_components_at_best():
    metrics = _metrics_row()
    result = compute_setup_score(metrics, CONFIG)
    assert result["setup_score"].iloc[0] == pytest.approx(100.0)
    assert result["quality_status"].iloc[0] == QualityStatus.UNKNOWN


def test_setup_score_zero_when_all_components_at_worst():
    metrics = _metrics_row(
        box_width=CONFIG.box.width_min,  # 경계 -> box_stability 0점
        box_position=CONFIG.box.position_max,  # 경계 -> box_position 0점
        support_touch_count=0,
        reversion_ratio=0.0,
        rsi=50.0,
        avg_trading_value_krw=0.0,
        rr_1=0.0,
    )
    result = compute_setup_score(metrics, CONFIG)
    assert result["setup_score"].iloc[0] == pytest.approx(0.0)


def test_setup_score_components_are_clipped_not_negative_or_over():
    # 극단값(박스폭 완전히 벗어남, RSI 과매수, RR 매우 큼)도 0~만점 범위 안에 있어야 한다.
    metrics = _metrics_row(box_width=1.0, rsi=100.0, rr_1=100.0, box_position=0.9)
    result = compute_setup_score(metrics, CONFIG)
    assert 0.0 <= result["setup_score"].iloc[0] <= 100.0


def test_setup_score_nan_inputs_score_as_zero_not_crash():
    metrics = _metrics_row(rr_1=float("nan"), box_width=float("nan"))
    result = compute_setup_score(metrics, CONFIG)
    assert result["setup_score"].iloc[0] < 100.0
    assert not pd.isna(result["setup_score"].iloc[0])


def test_is_setup_invalidated_when_box_filter_fails():
    assert is_setup_invalidated(passes_box_filter=False, close=100.0, stop=90.0) is True


def test_is_setup_invalidated_when_close_below_stop():
    assert is_setup_invalidated(passes_box_filter=True, close=89.0, stop=90.0) is True


def test_is_setup_invalidated_false_when_healthy():
    assert is_setup_invalidated(passes_box_filter=True, close=95.0, stop=90.0) is False


@pytest.mark.parametrize("score,expected", [(69, None), (70, SignalState.SETUP)])
def test_classify_signal_setup_score_boundary(score, expected):
    result = classify_signal(
        setup_score=score, trigger_score=0, rr_1=None, is_invalidated=False, config=CONFIG
    )
    assert result == expected


def test_classify_signal_trigger_requires_both_scores():
    # setup>=70, trigger_score_min(60) 미만 -> TRIGGER 아님, SETUP만
    result = classify_signal(setup_score=70, trigger_score=59, rr_1=None, is_invalidated=False, config=CONFIG)
    assert result == SignalState.SETUP

    result = classify_signal(setup_score=70, trigger_score=60, rr_1=None, is_invalidated=False, config=CONFIG)
    assert result == SignalState.TRIGGER


def test_classify_signal_buy_trigger_score_boundary_74_vs_75():
    # 스펙 12조 경계값: buy_trigger_score_min=75. 74는 TRIGGER까지만, 75부터 BUY_CANDIDATE 후보.
    result = classify_signal(setup_score=80, trigger_score=74, rr_1=5.0, is_invalidated=False, config=CONFIG)
    assert result == SignalState.TRIGGER

    result = classify_signal(setup_score=80, trigger_score=75, rr_1=5.0, is_invalidated=False, config=CONFIG)
    assert result == SignalState.BUY_CANDIDATE


def test_classify_signal_buy_candidate_requires_rr_gate():
    # setup>=70, trigger>=75, RR<2.0 -> BUY_CANDIDATE 아님, TRIGGER까지만
    result = classify_signal(setup_score=80, trigger_score=80, rr_1=1.99, is_invalidated=False, config=CONFIG)
    assert result == SignalState.TRIGGER

    result = classify_signal(setup_score=80, trigger_score=80, rr_1=2.0, is_invalidated=False, config=CONFIG)
    assert result == SignalState.BUY_CANDIDATE


def test_classify_signal_invalidated_overrides_everything():
    result = classify_signal(setup_score=95, trigger_score=95, rr_1=5.0, is_invalidated=True, config=CONFIG)
    assert result == SignalState.INVALIDATED
