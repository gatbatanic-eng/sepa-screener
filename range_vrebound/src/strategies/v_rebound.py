"""V-REBOUND 전략 (스펙 14~23조).

핵심 구조: PANIC → OVERSHOOT → DAMAGE CHECK → STABILIZATION → DEMAND → RS →
BREAKOUT → R/R
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import numpy as np
import pandas as pd

from src.indicators.relative_strength import excess_return, period_return
from src.indicators.volatility import drawdown_from_high, rolling_high
from src.indicators.volume import avg_volume
from src.indicators.volume import volume_ratio as _volume_ratio
from src.risk.rr import compute_rr_series
from src.risk.stop import compute_atr_stop

if TYPE_CHECKING:
    from src.config import VReboundConfig


EPS = 1e-9


def compute_initial_filter(
    high: pd.Series, close: pd.Series, benchmark_close: pd.Series, config: "VReboundConfig"
) -> pd.DataFrame:
    """초기 필터 (스펙 15조).

    60일 고점은 실제 일중 고가(High)의 롤링 최댓값을 쓴다 (RANGE-MR 박스와
    동일한 관례). 초과하락 계산 기간은 드로다운과 같은 high_lookback_days를
    쓴다 (스펙이 둘 다 "60일"로 명시).
    """
    period_high = rolling_high(high, config.filter.high_lookback_days)
    drawdown = drawdown_from_high(close, period_high)
    excess_return_60d = excess_return(close, benchmark_close, config.filter.high_lookback_days)

    passes_filter = (drawdown <= config.filter.drawdown_max + EPS) & (
        excess_return_60d <= config.filter.excess_return_60d_max_pp + EPS
    )
    passes_filter = passes_filter.fillna(False)

    return pd.DataFrame(
        {
            "period_high": period_high,
            "drawdown": drawdown,
            "excess_return_60d": excess_return_60d,
            "passes_filter": passes_filter,
        },
        index=close.index,
    )


def track_stabilization(
    low: pd.Series, high: pd.Series, close: pd.Series, passes_filter: pd.Series, config: "VReboundConfig"
) -> pd.DataFrame:
    """저점 추적, 안정화(NEW_LOW=FALSE), 반등 회복, FIRST_REBOUND_HIGH (스펙
    17/19조).

    초기 필터(15조)가 처음 성립한 날부터 "지금까지의 최저 저가"를 인과적으로
    추적한다(Phase 0 계획 제안 2). 그 저점 이후 confirm_window_days 거래일
    동안 새 저점이 없으면 "확정 저점"으로 고정하고(NEW_LOW=FALSE =
    is_stabilized), 그 다음 min_days_after_low~max_days_after_low 거래일의
    최고가를 FIRST_REBOUND_HIGH로 잡는다. 확정 저점보다 더 낮은 새 저점이
    나오면(재패닉) 안정화가 리셋되고 FIRST_REBOUND_HIGH도 다시 미정이 된다.
    """
    n = len(low)
    candidate_low = pd.Series(np.nan, index=low.index, dtype="float64")
    candidate_low_idx_col = pd.Series(np.nan, index=low.index, dtype="float64")
    confirmed_low_price = pd.Series(np.nan, index=low.index, dtype="float64")
    is_stabilized = pd.Series(False, index=low.index)
    recovered_3pct = pd.Series(False, index=low.index)
    first_rebound_high = pd.Series(np.nan, index=low.index, dtype="float64")
    broke_confirmed_low = pd.Series(False, index=low.index)

    tracking = False
    candidate_price: Optional[float] = None
    candidate_idx: Optional[int] = None
    days_since_new_low = 0
    confirmed = False
    confirmed_idx: Optional[int] = None

    for i in range(n):
        if not tracking:
            if bool(passes_filter.iloc[i]):
                tracking = True
                candidate_price = float(low.iloc[i])
                candidate_idx = i
                days_since_new_low = 0
                confirmed = False
                confirmed_idx = None
        else:
            lo = float(low.iloc[i])
            if lo < candidate_price:
                if confirmed:
                    # 이미 안정화(NEW_LOW=FALSE)로 확정됐던 저점이 다시
                    # 깨졌다 — 재패닉으로 이 셋업은 무효화된다 (스펙 12/23조
                    # INVALIDATED에 대응하는 V-REBOUND 쪽 기준).
                    broke_confirmed_low.iloc[i] = True
                candidate_price = lo
                candidate_idx = i
                days_since_new_low = 0
                confirmed = False
                confirmed_idx = None
            else:
                days_since_new_low += 1
                if not confirmed and days_since_new_low >= config.stabilization.confirm_window_days:
                    confirmed = True
                    confirmed_idx = candidate_idx

        if not tracking:
            continue

        candidate_low.iloc[i] = candidate_price
        candidate_low_idx_col.iloc[i] = candidate_idx
        recovered_3pct.iloc[i] = close.iloc[i] >= candidate_price * (1 + config.stabilization.rebound_from_low_pct)
        is_stabilized.iloc[i] = confirmed

        if confirmed:
            confirmed_low_price.iloc[i] = candidate_price
            window_start = confirmed_idx + config.first_rebound_high.min_days_after_low
            window_end = confirmed_idx + config.first_rebound_high.max_days_after_low
            if i >= window_start:
                upper = min(i, window_end)
                first_rebound_high.iloc[i] = high.iloc[window_start : upper + 1].max()

    return pd.DataFrame(
        {
            "candidate_low": candidate_low,
            "candidate_low_idx": candidate_low_idx_col,
            "confirmed_low_price": confirmed_low_price,
            "is_stabilized": is_stabilized,
            "recovered_3pct": recovered_3pct,
            "first_rebound_high": first_rebound_high,
            "broke_confirmed_low": broke_confirmed_low,
        },
        index=low.index,
    )


def compute_volume_structure(
    volume: pd.Series, candidate_low_idx: pd.Series, config: "VReboundConfig"
) -> pd.DataFrame:
    """PANIC_VOLUME_RATIO / REBOUND_VOLUME_RATIO (스펙 18조).

    둘 다 "오늘을 제외한 직전 20일 평균거래량" 대비 배수다 (RANGE-MR
    트리거의 거래량배수와 동일한 관례). PANIC_VOLUME_RATIO는 스펙이 점수화
    대상으로 명시하지 않아 설명가능성(reasons)용으로만 계산한다 —
    REBOUND_VOLUME_RATIO만 점수/신호 조건에 쓰인다.
    """
    baseline_avg_volume = avg_volume(volume.shift(1), config.volume.avg_days)
    rebound_volume_ratio = _volume_ratio(volume, baseline_avg_volume)

    n = len(volume)
    panic_volume_ratio = pd.Series(np.nan, index=volume.index, dtype="float64")
    for i in range(n):
        idx = candidate_low_idx.iloc[i]
        if pd.isna(idx):
            continue
        idx = int(idx)
        base = baseline_avg_volume.iloc[idx]
        if pd.isna(base) or base == 0:
            continue
        panic_volume_ratio.iloc[i] = volume.iloc[idx] / base

    return pd.DataFrame(
        {
            "baseline_avg_volume": baseline_avg_volume,
            "rebound_volume_ratio": rebound_volume_ratio,
            "panic_volume_ratio": panic_volume_ratio,
        },
        index=volume.index,
    )


def compute_breakout(
    close_today: float,
    first_rebound_high: Optional[float],
    rebound_volume_ratio: Optional[float],
    rs_5d: Optional[float],
    config: "VReboundConfig",
) -> bool:
    """BREAKOUT 판정 (스펙 21조): 세 조건 모두 충족해야 TRUE."""
    if first_rebound_high is None or pd.isna(first_rebound_high):
        return False
    if rebound_volume_ratio is None or pd.isna(rebound_volume_ratio):
        return False
    if rs_5d is None or pd.isna(rs_5d):
        return False
    return (
        close_today > first_rebound_high
        and rebound_volume_ratio >= config.breakout.volume_ratio_min
        and rs_5d > 0
    )


def compute_stop_target(
    confirmed_low_price: pd.Series,
    first_rebound_high: pd.Series,
    period_high: pd.Series,
    close: pd.Series,
    atr: pd.Series,
    config: "VReboundConfig",
) -> pd.DataFrame:
    """손절/목표가 및 참고용 R/R.

    스펙에 RANGE-MR 13조에 대응하는 V-REBOUND 전용 STOP/TARGET 공식이 없어
    다음과 같이 대칭적으로 설계했다 (Phase 5 제안):
    STOP = 확정 저점 - atr_multiplier*ATR (RANGE-MR의 "support zone 하단
    - 0.5*ATR"과 동일한 구조, 지지선 대신 확정 저점을 기준으로 삼는다)
    TARGET_1 = FIRST_REBOUND_HIGH (이미 돌파를 확인한 근접 목표)
    TARGET_2 = 급락 전 60일 고점 (period_high) — 하락폭을 되돌리는 목표
    RR은 종가를 참고 entry로 쓴 "신호 시점 지표용" 값이다 (RANGE-MR과 동일한
    관례, Phase 0 제안 3).
    """
    stop = compute_atr_stop(confirmed_low_price, atr, config.stop.atr_multiplier)
    target_1 = first_rebound_high
    target_2 = period_high

    rr_1 = compute_rr_series(close, stop, target_1)
    rr_2 = compute_rr_series(close, stop, target_2)

    return pd.DataFrame(
        {"stop": stop, "target_1": target_1, "target_2": target_2, "rr_1": rr_1, "rr_2": rr_2},
        index=close.index,
    )
