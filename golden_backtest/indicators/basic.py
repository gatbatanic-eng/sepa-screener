"""기본 지표(순수 함수). 모든 값은 t일까지의 데이터만 쓴다. 데이터가 부족한 구간은 NaN이고 채우지 않는다.

sepa/indicators.py와 같은 정의를 독립 구현했다(교차검증은 tests/test_indicators_vs_sepa.py).
"""
from __future__ import annotations

import pandas as pd


def sma(series: pd.Series, n: int) -> pd.Series:
    """[관례] 단순이동평균."""
    return series.rolling(n, min_periods=n).mean()


def ema(series: pd.Series, n: int) -> pd.Series:
    """[관례] 지수이동평균: span=n, adjust=False(재귀식이라 인과적)."""
    return series.ewm(span=n, adjust=False, min_periods=n).mean()


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """True Range. 첫 봉은 전일 종가가 없어 high-low."""
    prev = close.shift(1)
    return pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int) -> pd.Series:
    """[관례] Wilder ATR: TR의 지수평활(alpha=1/n, adjust=False). 첫 TR을 시드로 쓰므로 초기 구간은
    SMA 시드 방식과 미세하게 다르고, 워밍업(n봉 이상, 실제 사용은 1년 워밍업) 뒤에는 차이가 사라진다."""
    return true_range(high, low, close).ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
