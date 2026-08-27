import pytest

from src.config import StrategyConfig, load_config


def test_load_config_returns_valid_strategy_config():
    config = load_config()
    assert isinstance(config, StrategyConfig)


def test_missing_config_file_raises():
    with pytest.raises(FileNotFoundError):
        load_config("no_such_file.yaml")


def test_range_mr_score_weights_sum_to_100():
    w = load_config().range_mr.score_weights
    total = (
        w.box_stability + w.box_position + w.support_strength + w.mean_reversion
        + w.rsi + w.quality + w.liquidity + w.rr
    )
    assert total == 100


def test_v_rebound_score_weights_sum_to_100():
    w = load_config().v_rebound.score_weights
    total = (
        w.abs_drawdown + w.market_excess_drawdown + w.sector_excess_drawdown
        + w.quality + w.stabilization + w.volume_recovery + w.relative_strength
        + w.breakout
    )
    assert total == 100


def test_box_width_min_below_max():
    box = load_config().range_mr.box
    assert box.width_min < box.width_max


def test_buy_candidate_rr_threshold_is_2_0():
    # 스펙 13/24조: R/R < 2.0이면 BUY_CANDIDATE에서 제외한다.
    config = load_config()
    assert config.range_mr.thresholds.buy_rr_min == 2.0
    assert config.v_rebound.thresholds.buy_rr_min == 2.0
    assert config.risk.rr_min_for_buy_candidate == 2.0


def test_regime_gates_reference_valid_regime_names():
    valid = {"NORMAL", "RANGE", "CRASH", "RECOVERY"}
    gates = load_config().regime_gates
    assert set(gates.range_mr_regimes) <= valid
    assert set(gates.v_rebound_regimes) <= valid
