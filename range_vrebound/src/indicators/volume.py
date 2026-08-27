"""거래량 지표 (스펙 10/14/18조 거래량 배수 조건에 사용)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def avg_volume(volume: pd.Series, period: int) -> pd.Series:
    return volume.rolling(window=period, min_periods=period).mean()


def volume_ratio(volume: pd.Series, avg_volume_series: pd.Series) -> pd.Series:
    """volume / avg_volume. 평균 거래량이 0이면 계산 불가하므로 NaN."""
    with np.errstate(divide="ignore", invalid="ignore"):
        ratio = volume / avg_volume_series
    return ratio.mask(avg_volume_series == 0)
