"""V-REBOUND Score 및 신호 분류 (스펙 22/23조).

스펙은 8개 항목의 배점만 정의한다. 세부 산식은 RANGE-MR과 동일한 원칙
(기존 CONFIG 값만 재사용하는 단순 비례 배점, Phase 4/5 제안)으로 구현했다:

- abs_drawdown: drawdown이 filter.drawdown_max(-25%)에 도달하면 만점,
  그 이상 깊어져도 추가 가산 없음 (RANGE-MR RR 배점과 동일한 "기준 충족 시
  만점" 패턴)
- market_excess_drawdown: excess_return_60d가 excess_return_60d_max_pp에
  도달하면 만점, 동일 패턴
- sector_excess_drawdown: V1은 섹터 데이터가 best-effort로도 없으므로 항상
  제외(UNKNOWN과 동일하게 분자/분모에서 제외)
- quality: V1은 펀더멘털 데이터가 없어 항상 UNKNOWN → 제외
- stabilization: is_stabilized(NEW_LOW=FALSE)면 배점의 2/3, recovered_3pct
  보너스로 나머지 1/3 (스펙 17조 "추가 점수를 부여한다" 문구를 반영)
- volume_recovery: REBOUND_VOLUME_RATIO를 스펙 18조가 이미 제시한 3단계
  기준(1.3/1.5/2.0)으로 구간선형보간 (0.0~1.0 사이는 자연스러운 기준선)
- relative_strength: RS_5D > 0이면 만점 (스펙 21조 BREAKOUT과 동일 기준 재사용)
- breakout: BREAKOUT=TRUE면 만점
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np
import pandas as pd

from src.models.signal import QualityStatus, SignalState

if TYPE_CHECKING:
    from src.config import VReboundConfig


def _clip01(series: pd.Series) -> pd.Series:
    return series.clip(lower=0.0, upper=1.0).fillna(0.0)


def compute_v_rebound_score(metrics: pd.DataFrame, config: "VReboundConfig") -> pd.DataFrame:
    """metrics 컬럼: drawdown, excess_return_60d, is_stabilized, recovered_3pct,
    rebound_volume_ratio, rs_5d, breakout.
    """
    w = config.score_weights

    abs_drawdown_raw = _clip01(metrics["drawdown"] / config.filter.drawdown_max)
    market_excess_raw = _clip01(metrics["excess_return_60d"] / config.filter.excess_return_60d_max_pp)

    stabilized = metrics["is_stabilized"].fillna(False).astype(bool)
    recovered = metrics["recovered_3pct"].fillna(False).astype(bool)
    stabilization_raw = stabilized.astype(float) * (2.0 / 3.0) + recovered.astype(float) * (1.0 / 3.0)

    volume_bp_x = [1.0, config.volume.rebound_ratio_min, config.volume.rebound_ratio_strong, config.volume.rebound_ratio_very_strong]
    volume_bp_y = [0.0, 0.5, 0.75, 1.0]
    volume_recovery_raw = pd.Series(
        np.interp(metrics["rebound_volume_ratio"].fillna(0.0), volume_bp_x, volume_bp_y),
        index=metrics.index,
    )

    rs_raw = (metrics["rs_5d"].fillna(0.0) > 0).astype(float)
    breakout_raw = metrics["breakout"].fillna(False).astype(bool).astype(float)

    weighted_sum = (
        abs_drawdown_raw * w.abs_drawdown
        + market_excess_raw * w.market_excess_drawdown
        + stabilization_raw * w.stabilization
        + volume_recovery_raw * w.volume_recovery
        + rs_raw * w.relative_strength
        + breakout_raw * w.breakout
    )
    non_excluded_weight_sum = (
        w.abs_drawdown + w.market_excess_drawdown + w.stabilization + w.volume_recovery
        + w.relative_strength + w.breakout
    )
    total_score = weighted_sum / non_excluded_weight_sum * 100.0

    return pd.DataFrame(
        {
            "total_score": total_score,
            "quality_status": [QualityStatus.UNKNOWN] * len(metrics),
        },
        index=metrics.index,
    )


def is_rebound_invalidated(broke_confirmed_low: bool) -> bool:
    """이미 안정화(NEW_LOW=FALSE)로 확정됐던 저점이 재패닉으로 다시
    깨지면 무효화한다 (스펙 12/23조).

    "초기 필터를 더 이상 통과하지 못함"(=주가가 충분히 회복함)은 여기서
    무효화 사유로 쓰지 않는다 — 그건 V-REBOUND 전략이 노리는 성공
    시나리오이지 실패가 아니다. RANGE-MR의 "박스 붕괴"와 달리 V-REBOUND의
    초기 필터는 진입 게이트일 뿐, 트레이드가 진행되는 동안 계속 유지돼야
    하는 조건이 아니다.
    """
    return broke_confirmed_low


def classify_signal(
    total_score: float,
    is_stabilized: bool,
    breakout: bool,
    rebound_volume_ratio: Optional[float],
    rr_1: Optional[float],
    is_invalidated: bool,
    config: "VReboundConfig",
) -> Optional[SignalState]:
    """스펙 23조 SETUP/WATCH/BUY_CANDIDATE 분류."""
    if is_invalidated:
        return SignalState.INVALIDATED

    thresholds = config.thresholds
    if total_score < thresholds.setup_score_min:
        return None

    watch_ok = bool(is_stabilized)
    buy_ok = (
        total_score >= thresholds.buy_score_min
        and is_stabilized
        and breakout
        and rebound_volume_ratio is not None
        and rebound_volume_ratio >= thresholds.buy_rebound_volume_min
        and rr_1 is not None
        and rr_1 >= thresholds.buy_rr_min
    )

    if buy_ok:
        return SignalState.BUY_CANDIDATE
    if watch_ok:
        return SignalState.WATCH
    return SignalState.SETUP
