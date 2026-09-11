"""
sepa/pivot.py — base 내부 저항선(피벗) 산정
==========================================

완벽한 차트 패턴 인식 대신 **안정적인 deterministic heuristic** 을 쓴다.

- 피벗은 **전일까지의 데이터만** 사용한다. 당일 가격으로 당일 피벗을 새로 만들어
  즉시 돌파 판정하는 자기참조/look-ahead 오류를 막는다 (스펙 6조).
- 우선순위: base 구간 안의 **확정된 스윙 고점** → 없으면 "전일까지 rolling 최고가".
- pivot_distance_pct = (close_today / pivot_price - 1) * 100
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from sepa.config import PivotConfig, SwingConfig
from sepa.swings import detect_swings


@dataclass(frozen=True)
class PivotResult:
    pivot_price: float | None
    pivot_distance_pct: float | None
    source: str                       # "swing" | "resistance" | "none"
    pivot_idx: int | None             # positional 인덱스 (피벗을 만든 봉)


def compute_pivot(high: pd.Series, low: pd.Series, close: pd.Series,
                  cfg: PivotConfig, swing_cfg: SwingConfig,
                  as_of_offset: int = 0) -> PivotResult:
    """
    as_of_offset > 0 이면 그만큼 전 거래일을 "오늘"로 보고 피벗을 계산한다
    (돌파 이력 스캔 시 각 봉 시점의 피벗을 재현하기 위함).
    """
    n = len(close)
    end = n - 1 - as_of_offset            # "오늘"의 positional 인덱스
    if end < cfg.min_bars_ago + 5:
        return PivotResult(None, None, "none", None)

    # 전일까지만 사용 (당일 제외)
    hist_end = end - cfg.min_bars_ago      # 포함
    lb_start = max(0, hist_end - cfg.base_lookback + 1)
    win_high = high.iloc[lb_start: hist_end + 1]
    win_low = low.iloc[lb_start: hist_end + 1]
    if win_high.empty or win_high.isna().all():
        return PivotResult(None, None, "none", None)

    resistance = float(np.nanmax(win_high.to_numpy(dtype=float)))
    resistance_idx = int(win_high.to_numpy(dtype=float).argmax()) + lb_start

    pivot_price = resistance
    pivot_idx = resistance_idx
    source = "resistance"

    if cfg.prefer_swing:
        swings = detect_swings(win_high, win_low, swing_cfg.fractal_left, swing_cfg.fractal_right)
        highs = [s for s in swings if s.kind == "H"]
        if highs:
            # base 최고가에 충분히 근접한 스윙 고점 중 가장 최근 것
            near_top = [s for s in highs if s.price >= resistance * cfg.swing_near_top_ratio]
            chosen = (near_top or highs)[-1]
            pivot_price = float(chosen.price)
            pivot_idx = int(chosen.idx) + lb_start
            source = "swing"

    today_close = float(close.iloc[end])
    dist = (today_close / pivot_price - 1.0) * 100.0 if pivot_price > 0 else None
    return PivotResult(
        pivot_price=round(pivot_price, 4),
        pivot_distance_pct=round(dist, 2) if dist is not None else None,
        source=source,
        pivot_idx=pivot_idx,
    )
