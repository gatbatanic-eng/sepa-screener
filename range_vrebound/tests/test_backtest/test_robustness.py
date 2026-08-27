import pandas as pd
import pytest

from src.backtest.metrics import BacktestMetrics
from src.backtest.robustness import (
    detect_overfitting_risk,
    override_config,
    run_parameter_sensitivity,
    train_test_split_by_date,
)
from src.config import load_config

BASE_CONFIG = load_config()


def test_override_config_changes_nested_value_without_mutating_original():
    original_period = BASE_CONFIG.range_mr.box.period_days
    updated = override_config(BASE_CONFIG, "range_mr.box.period_days", 80)
    assert updated.range_mr.box.period_days == 80
    assert BASE_CONFIG.range_mr.box.period_days == original_period  # 원본 불변


def test_override_config_keeps_other_fields_intact():
    updated = override_config(BASE_CONFIG, "range_mr.box.period_days", 80)
    assert updated.range_mr.box.width_min == BASE_CONFIG.range_mr.box.width_min
    assert updated.v_rebound.filter.drawdown_max == BASE_CONFIG.v_rebound.filter.drawdown_max


def test_run_parameter_sensitivity_collects_metric_per_value():
    canned = {40: 0.05, 60: 0.06, 80: 0.04, 120: 0.055}

    def fake_run(cfg):
        period = cfg.range_mr.box.period_days
        return BacktestMetrics(total_trades=10, avg_return=canned[period])

    df = run_parameter_sensitivity(fake_run, BASE_CONFIG, "range_mr.box.period_days", [40, 60, 80, 120], "avg_return")
    assert list(df["value"]) == [40, 60, 80, 120]
    assert list(df["avg_return"]) == [0.05, 0.06, 0.04, 0.055]


def test_detect_overfitting_risk_flags_isolated_spike():
    df = pd.DataFrame({"value": [40, 60, 80, 120], "avg_return": [0.05, 0.06, 0.55, 0.055]})
    result = detect_overfitting_risk(df, "avg_return")
    assert result["risk"] is True
    assert result["best_value"] == 80


def test_detect_overfitting_risk_no_flag_when_values_are_similar():
    df = pd.DataFrame({"value": [40, 60, 80, 120], "avg_return": [0.05, 0.052, 0.048, 0.051]})
    result = detect_overfitting_risk(df, "avg_return")
    assert result["risk"] is False


def test_detect_overfitting_risk_insufficient_samples():
    df = pd.DataFrame({"value": [40, 60], "avg_return": [0.05, 0.50]})
    result = detect_overfitting_risk(df, "avg_return")
    assert result["risk"] is False
    assert "reason" in result


def test_detect_overfitting_risk_no_flag_when_rest_values_identical():
    # 나머지 값들의 표준편차가 0이면(전부 동일) z-score를 계산할 수 없다
    df = pd.DataFrame({"value": [40, 60, 80, 120], "avg_return": [0.05, 0.05, 0.05, 0.20]})
    result = detect_overfitting_risk(df, "avg_return")
    assert result["risk"] is False
    assert result["z_score"] is None


def test_train_test_split_by_date_boundary():
    idx = pd.date_range("2024-01-01", periods=10, freq="D")
    train_mask, test_mask = train_test_split_by_date(idx, "2024-01-05")
    assert train_mask.sum() == 5  # 1/1~1/5 (경계 포함)
    assert test_mask.sum() == 5  # 1/6~1/10
    assert not (train_mask & test_mask).any()
