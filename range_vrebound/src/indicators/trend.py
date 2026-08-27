"""추세 지표: 이동평균과 그 파생값 (스펙 9/10조 "20DMA" 등).

모든 함수는 인덱스 i의 값이 인덱스 0..i의 데이터에만 의존하는 causal 함수다
(스펙 2.3조 데이터 누수 금지). 데이터가 부족하면 NaN을 반환한다 — 추정치로
채우지 않는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, period: int) -> pd.Series:
    """단순이동평균. period 미만의 구간은 NaN."""
    return close.rolling(window=period, min_periods=period).mean()


def is_ma_rising(ma: pd.Series, lookback: int) -> pd.Series:
    """이동평균이 최근 lookback 거래일 동안 "상승 중"인지 (엄격 부등호).

    ma[i] > ma[i-lookback]일 때만 True. 같으면(횡보) 상승으로 보지 않는다.
    비교 대상이 없으면(데이터 부족) pandas nullable boolean으로 NA.
    """
    shifted = ma.shift(lookback)
    rising = ma > shifted
    result = rising.astype("boolean")
    result[shifted.isna() | ma.isna()] = pd.NA
    return result


def distance_from_ma(price: pd.Series, ma: pd.Series) -> pd.Series:
    """(price - ma) / ma. ma가 0이면 계산 불가하므로 NaN."""
    with np.errstate(divide="ignore", invalid="ignore"):
        result = (price - ma) / ma
    return result.mask(ma == 0)
