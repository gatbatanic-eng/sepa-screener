"""전략 CONFIG 로더.

strategy_config.yaml을 pydantic 모델로 검증해서 읽는다. 모든 전략 파라미터는
여기를 거쳐야 하며, 코드에 값을 하드코딩하지 않는다 (개발 원칙 2.2).
파일이 없거나 스키마가 맞지 않으면 조용히 넘어가지 않고 즉시 예외를 던진다
(개발 원칙 33 — 오류를 조용히 무시하지 않는다).
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "strategy_config.yaml"


class CrashConfig(BaseModel):
    return_lookback_days: int = 20
    return_20d_max: float
    drawdown_lookback_days: int = 60
    drawdown_60d_max: float


class RangeRegimeConfig(BaseModel):
    lookback_days: int
    abs_return_max: float
    band_width_max: float


class RecoveryConfig(BaseModel):
    lookback_days: int


class MarketRegimeConfig(BaseModel):
    crash: CrashConfig
    range: RangeRegimeConfig
    recovery: RecoveryConfig


class BoxConfig(BaseModel):
    period_days: int
    width_min: float
    width_max: float
    position_max: float


class SupportConfig(BaseModel):
    tolerance_pct: float
    confirmation_window_days: int
    rebound_threshold_pct: float
    min_touches_high_quality: int


class RangeMRTriggerConfig(BaseModel):
    breakout_lookback_days: int
    volume_ratio_min: float
    volume_avg_days: int
    rs_days: int
    ma_recovery_lookback_days: int


class RangeMRMeanReversionConfig(BaseModel):
    ma_period: int
    rsi_period: int


class RangeMRLiquidityConfig(BaseModel):
    min_avg_trading_value_krw: float


class RangeMRScoreWeights(BaseModel):
    box_stability: float
    box_position: float
    support_strength: float
    mean_reversion: float
    rsi: float
    quality: float
    liquidity: float
    rr: float


class RangeMRTriggerScoreWeights(BaseModel):
    breakout: float
    volume: float
    bullish_candle: float
    ma_recovery: float
    rs: float


class RangeMRThresholds(BaseModel):
    setup_score_min: float
    trigger_score_min: float
    buy_trigger_score_min: float
    buy_rr_min: float


class StopConfig(BaseModel):
    atr_period: int
    atr_multiplier: float


class RangeMRConfig(BaseModel):
    box: BoxConfig
    support: SupportConfig
    trigger: RangeMRTriggerConfig
    mean_reversion: RangeMRMeanReversionConfig
    liquidity: RangeMRLiquidityConfig
    score_weights: RangeMRScoreWeights
    trigger_score_weights: RangeMRTriggerScoreWeights
    thresholds: RangeMRThresholds
    stop: StopConfig


class VReboundFilterConfig(BaseModel):
    high_lookback_days: int
    drawdown_max: float
    excess_return_60d_max_pp: float


class StabilizationConfig(BaseModel):
    confirm_window_days: int
    rebound_from_low_pct: float


class VolumeConfig(BaseModel):
    avg_days: int
    rebound_ratio_min: float
    rebound_ratio_strong: float
    rebound_ratio_very_strong: float


class FirstReboundHighConfig(BaseModel):
    min_days_after_low: int
    max_days_after_low: int


class RSConfig(BaseModel):
    days: int


class BreakoutConfig(BaseModel):
    volume_ratio_min: float


class VReboundScoreWeights(BaseModel):
    abs_drawdown: float
    market_excess_drawdown: float
    sector_excess_drawdown: float
    quality: float
    stabilization: float
    volume_recovery: float
    relative_strength: float
    breakout: float


class VReboundThresholds(BaseModel):
    setup_score_min: float
    buy_score_min: float
    buy_rebound_volume_min: float
    buy_rr_min: float


class VReboundConfig(BaseModel):
    filter: VReboundFilterConfig
    stabilization: StabilizationConfig
    volume: VolumeConfig
    first_rebound_high: FirstReboundHighConfig
    rs: RSConfig
    breakout: BreakoutConfig
    score_weights: VReboundScoreWeights
    thresholds: VReboundThresholds
    stop: StopConfig


class RiskConfig(BaseModel):
    rr_min_for_buy_candidate: float
    rr_gate_uses: Literal["rr_1", "rr_2"]


class RegimeGatesConfig(BaseModel):
    enabled: bool
    range_mr_regimes: list[str]
    v_rebound_regimes: list[str]


class WalkForwardConfig(BaseModel):
    box_period_grid: list[int]
    drawdown_grid: list[float]
    volume_ratio_grid: list[float]
    rr_grid: list[float]


class BacktestConfig(BaseModel):
    holding_periods_days: list[int]
    entry_price: Literal["next_open"]
    walk_forward: WalkForwardConfig


class UniverseConfig(BaseModel):
    market: Literal["KR"]
    top_n_by_market_cap: int


class DataConfig(BaseModel):
    history_calendar_days: int
    min_trading_days: int


class StrategyConfig(BaseModel):
    model_config = {"frozen": True}

    market_regime: MarketRegimeConfig
    range_mr: RangeMRConfig
    v_rebound: VReboundConfig
    risk: RiskConfig
    regime_gates: RegimeGatesConfig
    backtest: BacktestConfig
    universe: UniverseConfig
    data: DataConfig


def load_config(path: Path | str = DEFAULT_CONFIG_PATH) -> StrategyConfig:
    """strategy_config.yaml을 읽어 StrategyConfig로 검증한다."""
    resolved = Path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"CONFIG 파일을 찾을 수 없습니다: {resolved}")
    with resolved.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    return StrategyConfig.model_validate(raw)
