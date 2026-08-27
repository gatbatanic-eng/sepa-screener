"""상대강도(RS) 지표: 종목 수익률 - 벤치마크(시장/섹터) 수익률 (스펙 15/20조).

IBD RS가 없으므로 기간 수익률 차이로 계산하는 대체 지표다. 값의 단위는
소수(0.10 = 10%p)로 통일한다.
"""
from __future__ import annotations

import pandas as pd


def period_return(price: pd.Series, period: int) -> pd.Series:
    """price[i]/price[i-period] - 1. period 미만 구간은 NaN."""
    return price / price.shift(period) - 1.0


def excess_return(
    price: pd.Series, benchmark_price: pd.Series, period: int
) -> pd.Series:
    """종목 기간수익률 - 벤치마크 기간수익률 (초과수익률, %p)."""
    stock_return = period_return(price, period)
    benchmark_return = period_return(benchmark_price, period)
    return stock_return - benchmark_return
