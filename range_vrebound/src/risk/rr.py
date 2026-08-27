"""위험/보상 비율(R/R) 계산 (스펙 24조).

RISK = entry - stop
REWARD = target - entry
RR = REWARD / RISK

전체 Risk Engine(포지션 사이징 등)은 Phase 6에서 갖추지만, RANGE-MR
Setup Score의 R/R 항목(스펙 11/13조)이 이 계산을 먼저 필요로 하므로
여기서는 RR 산식만 먼저 구현한다.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd


def compute_rr(entry: float, stop: float, target: float) -> Optional[float]:
    """RR = (target-entry) / (entry-stop). risk<=0이면 계산 불가하므로 None."""
    risk = entry - stop
    if risk <= 0:
        return None
    reward = target - entry
    return reward / risk


def compute_rr_series(entry: pd.Series, stop: pd.Series, target: pd.Series) -> pd.Series:
    """compute_rr을 시계열 세 개에 대해 일괄 적용한다 (RANGE-MR/V-REBOUND 공용)."""
    return pd.Series(
        [compute_rr(e, s, t) for e, s, t in zip(entry, stop, target)], index=entry.index, dtype="float64"
    )
