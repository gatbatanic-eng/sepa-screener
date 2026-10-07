"""ADR(평균 일중 변동폭)."""
from __future__ import annotations

import pandas as pd


def adr_pct(high: pd.Series, low: pd.Series, n: int = 20) -> pd.Series:
    """[관례] ADR% = 100 × (최근 n일 high/low 비율의 평균 − 1). 쿨라매기 ADR(20) ≥ 4% 필터용."""
    return 100.0 * ((high / low).rolling(n, min_periods=n).mean() - 1.0)
