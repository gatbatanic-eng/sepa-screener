"""RANGE-MR 전략: 박스 정의 (7.2조) 및 지지선 엔진 (8조).

핵심 구조: RANGE → LOWER EXTREME → SUPPORT → REVERSAL → R/R
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

from src.indicators.momentum import rsi as _rsi
from src.indicators.relative_strength import excess_return
from src.indicators.trend import distance_from_ma, is_ma_rising, sma
from src.indicators.volatility import box_position as _box_position
from src.indicators.volatility import range_width as _range_width
from src.indicators.volatility import rolling_high, rolling_low
from src.indicators.volume import avg_volume
from src.indicators.volume import volume_ratio as _volume_ratio
from src.risk.rr import compute_rr_series
from src.risk.stop import compute_atr_stop

if TYPE_CHECKING:
    from src.config import BoxConfig, RangeMRConfig, SupportConfig


def compute_box_metrics(high: pd.Series, low: pd.Series, close: pd.Series, config: "BoxConfig") -> pd.DataFrame:
    """박스 상단/하단은 실제 일중 고가/저가의 롤링 극값을 쓰고(스펙 7.2조),
    BOX_POSITION의 "현재가"는 종가로 통일한다 (Phase 0 제안 5).
    """
    box_high = rolling_high(high, config.period_days)
    box_low = rolling_low(low, config.period_days)
    box_width = _range_width(box_high, box_low)
    box_midpoint = (box_high + box_low) / 2.0
    box_position = _box_position(close, box_high, box_low)

    passes_width = box_width.between(config.width_min, config.width_max)
    passes_position = box_position <= config.position_max
    passes_box_filter = (passes_width & passes_position).fillna(False)

    return pd.DataFrame(
        {
            "box_high": box_high,
            "box_low": box_low,
            "box_width": box_width,
            "box_midpoint": box_midpoint,
            "box_position": box_position,
            "passes_box_filter": passes_box_filter,
        },
        index=close.index,
    )


def detect_support_touches(
    low: pd.Series, close: pd.Series, box_low: pd.Series, config: "SupportConfig"
) -> pd.Series:
    """지지 터치 확인 (스펙 8조).

    박스 하단 ±tolerance_pct를 support zone으로 보고, 가격의 저가(Low)가 이
    구간에 들어온 뒤 연속으로 머무는 구간을 하나의 "터치 후보"로 묶는다
    (중복 터치 통합). 터치 후보가 끝난 뒤 confirmation_window_days 거래일
    이내에 종가가 터치 구간 최저가 대비 rebound_threshold_pct 이상
    반등하면 그 반등이 "확인된" 날짜에 터치 1회로 기록한다.

    반환값은 각 날짜에 그날 새로 확인된 터치 횟수를 담은 정수 Series다
    (터치가 실제로 시장에서 확인된 날짜에 이벤트를 기록해야 미래 데이터를
    쓰지 않는다 — 스펙 2.3조).
    """
    n = len(close)
    events = pd.Series(0, index=close.index, dtype="int64")

    in_run = False
    run_end_idx = -1
    run_min_low = np.nan
    pending: list[dict] = []

    for i in range(n):
        bl = box_low.iloc[i]
        lo = low.iloc[i]
        in_zone = pd.notna(bl) and bl * (1 - config.tolerance_pct) <= lo <= bl * (1 + config.tolerance_pct)

        if in_zone:
            if in_run:
                run_end_idx = i
                run_min_low = min(run_min_low, lo)
            else:
                in_run = True
                run_end_idx = i
                run_min_low = lo
        else:
            if in_run:
                pending.append({"end": run_end_idx, "min_low": run_min_low})
                in_run = False

        current_close = close.iloc[i]
        still_pending = []
        for touch in pending:
            deadline = touch["end"] + config.confirmation_window_days
            rebound_target = touch["min_low"] * (1 + config.rebound_threshold_pct)
            if pd.notna(current_close) and current_close >= rebound_target:
                events.iloc[i] += 1
            elif i >= deadline:
                pass  # 확인 기한 만료 — 터치로 인정하지 않는다
            else:
                still_pending.append(touch)
        pending = still_pending

    return events


def rolling_support_touch_count(touch_events: pd.Series, period_days: int) -> pd.Series:
    """박스 기간(period_days) 내에 확인된 터치 횟수 누계 (설명가능성 및 점수화용)."""
    return touch_events.rolling(window=period_days, min_periods=1).sum()


def compute_mean_reversion_metrics(
    close: pd.Series, box_midpoint: pd.Series, config: "RangeMRConfig"
) -> pd.DataFrame:
    """평균회귀 조건의 raw 값 (스펙 9조).

    VWAP은 OHLCV 데이터만으로는 계산할 수 없어 V1에서는 제외한다(스펙
    9조 "가능 시"). RSI는 보조지표로만 쓴다 — 매수조건의 핵심이 아니다.
    """
    ma = sma(close, config.mean_reversion.ma_period)
    return pd.DataFrame(
        {
            "ma": ma,
            "distance_from_ma": distance_from_ma(close, ma),
            "distance_from_midpoint": distance_from_ma(close, box_midpoint),
            "rsi": _rsi(close, config.mean_reversion.rsi_period),
        },
        index=close.index,
    )


def compute_trigger_flags(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    volume: pd.Series,
    benchmark_close: pd.Series,
    config: "RangeMRConfig",
) -> pd.DataFrame:
    """트리거 조건 5가지 (스펙 10조).

    거래량 배수의 "20일 평균거래량"은 오늘 자신을 포함하지 않은, 직전
    거래일까지의 평균을 기준선으로 쓴다 (돌파 판정과 동일하게 — 오늘의
    급증 자체가 기준선을 같이 끌어올리면 배수 조건의 의미가 희석된다).
    """
    prior_high = rolling_high(high.shift(1), config.trigger.breakout_lookback_days)
    breakout = close > prior_high

    baseline_avg_volume = avg_volume(volume.shift(1), config.trigger.volume_avg_days)
    volume_ratio_value = _volume_ratio(volume, baseline_avg_volume)
    volume_ok = volume_ratio_value >= config.trigger.volume_ratio_min

    bullish_candle = close > open_

    ma = sma(close, config.mean_reversion.ma_period)
    ma_recovery = is_ma_rising(ma, config.trigger.ma_recovery_lookback_days).fillna(False).astype(bool)

    rs_5d = excess_return(close, benchmark_close, config.trigger.rs_days)
    rs_positive = rs_5d > 0

    return pd.DataFrame(
        {
            "breakout": breakout.fillna(False),
            "volume_ratio": volume_ratio_value,
            "volume_ok": volume_ok.fillna(False),
            "bullish_candle": bullish_candle,
            "ma_recovery": ma_recovery,
            "rs_5d": rs_5d,
            "rs_positive": rs_positive.fillna(False),
        },
        index=close.index,
    )


def compute_stop_target(
    box_high: pd.Series,
    box_low: pd.Series,
    box_midpoint: pd.Series,
    close: pd.Series,
    atr: pd.Series,
    config: "RangeMRConfig",
) -> pd.DataFrame:
    """손절/목표가 및 참고용 R/R (스펙 13조).

    STOP = support zone 하단 - 0.5*ATR
    TP1 = box midpoint, TP2 = box high
    RR는 종가를 참고 entry로 써서 계산한 "신호 시점 지표용" 값이다 — 실제
    백테스트 체결가(T+1 시가, Phase 0 제안 3)와는 다르다.
    """
    support_zone_lower = box_low * (1 - config.support.tolerance_pct)
    stop = compute_atr_stop(support_zone_lower, atr, config.stop.atr_multiplier)
    target_1 = box_midpoint
    target_2 = box_high

    rr_1 = compute_rr_series(close, stop, target_1)
    rr_2 = compute_rr_series(close, stop, target_2)

    return pd.DataFrame(
        {"stop": stop, "target_1": target_1, "target_2": target_2, "rr_1": rr_1, "rr_2": rr_2},
        index=close.index,
    )
