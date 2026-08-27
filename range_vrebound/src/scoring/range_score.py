"""RANGE-MR Setup/Trigger Score 및 신호 분류 (스펙 11/12조).

Setup Score 8개 하위항목과 Trigger Score 5개 요소의 세부 산식은 스펙에
명시되어 있지 않다 (배점표만 있음). 기존 CONFIG 값만 재사용하는 단순
비례/이진 배점으로 구현했다 (Phase 4 제안, 새 임계값을 최소화):

- box_stability: box_width가 [width_min, width_max] 중앙에 가까울수록 만점
- box_position: position=0일 때 만점, position_max에서 0점으로 선형 감소
- support_strength: 확인된 터치 수 / min_touches_high_quality (2회 이상 만점)
- mean_reversion: 박스 중간값 대비 하단에 가까울수록 만점 (reversion_ratio)
- rsi: RSI가 낮을수록(과매도) 만점, 50 이상이면 0점
- quality: V1은 펀더멘털 데이터가 없어 항상 UNKNOWN → 분자/분모에서 제외하고
  나머지 배점을 100점으로 재환산 (Phase 0 제안 4를 RANGE-MR에도 적용)
- liquidity: 일평균거래대금 / min_avg_trading_value_krw (신설 임계값)
- rr: rr_1 / buy_rr_min

Trigger Score는 스펙에 배점표 자체가 없어, 5개 요소를 균등 이진 배점
(각 20점, CONFIG의 trigger_score_weights)으로 구현했다.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np
import pandas as pd

from src.models.signal import QualityStatus, SignalState

if TYPE_CHECKING:
    from src.config import RangeMRConfig


def compute_trigger_score(flags: pd.DataFrame, config: "RangeMRConfig") -> pd.Series:
    w = config.trigger_score_weights
    return (
        flags["breakout"].astype(float) * w.breakout
        + flags["volume_ok"].astype(float) * w.volume
        + flags["bullish_candle"].astype(float) * w.bullish_candle
        + flags["ma_recovery"].astype(float) * w.ma_recovery
        + flags["rs_positive"].astype(float) * w.rs
    )


def _clip01(series: pd.Series) -> pd.Series:
    return series.clip(lower=0.0, upper=1.0).fillna(0.0)


def compute_setup_score(metrics: pd.DataFrame, config: "RangeMRConfig") -> pd.DataFrame:
    """metrics는 다음 컬럼을 포함해야 한다: box_width, box_position,
    support_touch_count, reversion_ratio, rsi, avg_trading_value_krw, rr_1.
    """
    w = config.score_weights
    box_cfg = config.box

    width_mid = (box_cfg.width_min + box_cfg.width_max) / 2.0
    width_half_range = (box_cfg.width_max - box_cfg.width_min) / 2.0
    box_stability_raw = _clip01(1 - (metrics["box_width"] - width_mid).abs() / width_half_range)
    box_position_raw = _clip01(1 - metrics["box_position"] / box_cfg.position_max)
    support_strength_raw = _clip01(metrics["support_touch_count"] / config.support.min_touches_high_quality)
    mean_reversion_raw = _clip01(metrics["reversion_ratio"])
    rsi_raw = _clip01((50.0 - metrics["rsi"]) / 50.0)
    liquidity_raw = _clip01(metrics["avg_trading_value_krw"] / config.liquidity.min_avg_trading_value_krw)
    rr_raw = _clip01(metrics["rr_1"].fillna(0.0) / config.thresholds.buy_rr_min)

    weighted_sum = (
        box_stability_raw * w.box_stability
        + box_position_raw * w.box_position
        + support_strength_raw * w.support_strength
        + mean_reversion_raw * w.mean_reversion
        + rsi_raw * w.rsi
        + liquidity_raw * w.liquidity
        + rr_raw * w.rr
    )
    non_quality_weight_sum = (
        w.box_stability + w.box_position + w.support_strength + w.mean_reversion + w.rsi + w.liquidity + w.rr
    )
    setup_score = weighted_sum / non_quality_weight_sum * 100.0

    return pd.DataFrame(
        {
            "setup_score": setup_score,
            "quality_status": [QualityStatus.UNKNOWN] * len(metrics),
        },
        index=metrics.index,
    )


def is_setup_invalidated(passes_box_filter: bool, close: float, stop: float) -> bool:
    """지지 붕괴 또는 박스 조건 미충족 시 무효화 (스펙 12조 INVALIDATED)."""
    if not passes_box_filter:
        return True
    if pd.notna(close) and pd.notna(stop) and close < stop:
        return True
    return False


def classify_signal(
    setup_score: float,
    trigger_score: Optional[float],
    rr_1: Optional[float],
    is_invalidated: bool,
    config: "RangeMRConfig",
) -> Optional[SignalState]:
    """스펙 12조 SETUP/TRIGGER/BUY_CANDIDATE/INVALIDATED 분류.

    아무 조건도 만족하지 않으면(Setup Score < 70) 신호 없음(None)이다.
    """
    if is_invalidated:
        return SignalState.INVALIDATED

    thresholds = config.thresholds
    if setup_score < thresholds.setup_score_min:
        return None

    trigger_ok = trigger_score is not None and trigger_score >= thresholds.trigger_score_min
    buy_ok = (
        trigger_score is not None
        and trigger_score >= thresholds.buy_trigger_score_min
        and rr_1 is not None
        and rr_1 >= thresholds.buy_rr_min
    )

    if buy_ok:
        return SignalState.BUY_CANDIDATE
    if trigger_ok:
        return SignalState.TRIGGER
    return SignalState.SETUP
