"""
sepa/indicators.py — 인과적(causal) 기술 지표
=============================================

모든 함수는 pandas rolling / shift / ewm 만 사용하므로 인덱스 ``i`` 의 값은
``i`` 이전(포함) 데이터만으로 결정된다. 미래 봉이 과거 시점 값에 영향을 주지
않는다. (``tests/test_sepa_v2.py`` 의 look-ahead 회귀 테스트로 강제한다.)

데이터가 부족하면 (window 미만) 해당 구간은 ``NaN`` 으로 남는다 — 억지로 채우지
않는다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    """단순이동평균. window 미만은 NaN."""
    return series.rolling(period, min_periods=period).mean()


def ema(series: pd.Series, period: int) -> pd.Series:
    """지수이동평균 (span 방식, adjust=False → 인과적). window 미만은 NaN.

    TREND TEMPLATE 은 SMA 만 쓰지만, ENTRY/EXIT 관리(EMA10/EMA20)에는 EMA 를
    쓴다 (스펙 8조).
    """
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rolling_high(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).max()


def rolling_low(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).min()


def rolling_mean(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def pct_return(series: pd.Series, period: int) -> pd.Series:
    """period 거래일 전 대비 수익률 (소수, 0.1 = +10%)."""
    return series / series.shift(period) - 1.0


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range. 첫 봉은 prev_close 가 없어 (high-low) 로 대체된다."""
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int) -> pd.Series:
    """ATR (Wilder 지수평활, adjust=False → 인과적). window 미만은 NaN."""
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def clv(high: float, low: float, close: float) -> float | None:
    """Close Location Value = (close - low) / (high - low).

    당일 high == low (상·하한가 붙박이 등) 이면 0.5 로 안전하게 처리한다.
    입력이 결측이면 None.
    """
    if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in (high, low, close)):
        return None
    rng = high - low
    if rng <= 0:
        return 0.5
    return float((close - low) / rng)


def upper_wick_ratio(open_: float, high: float, low: float, close: float) -> float | None:
    """윗꼬리 길이 / 당일 전체 range. 클라이맥스/소진 신호 참고용."""
    if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in (open_, high, low, close)):
        return None
    rng = high - low
    if rng <= 0:
        return 0.0
    body_top = max(open_, close)
    return float((high - body_top) / rng)


def slice_causal(series: pd.Series, upto_positional: int) -> pd.Series:
    """positional 인덱스 upto (포함) 까지만 남긴다. 과거 시점 재현용."""
    return series.iloc[: upto_positional + 1]


def last_valid(series: pd.Series, n_back: int = 0) -> float | None:
    """뒤에서 n_back 번째 유효(non-NaN) 값. 없으면 None."""
    s = series.dropna()
    if len(s) <= n_back:
        return None
    return float(s.iloc[-1 - n_back])
