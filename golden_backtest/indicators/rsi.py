"""RSI(Wilder 평활)."""
from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, n: int) -> pd.Series:
    """[관례] Wilder RSI: 상승폭·하락폭을 alpha=1/n으로 지수평활. 평균 하락이 0이면 100, 상승·하락 모두 0이면 50."""
    delta = close.diff()
    gain = delta.clip(lower=0.0).where(delta.notna())
    loss = (-delta).clip(lower=0.0).where(delta.notna())
    avg_gain = gain.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    avg_loss = loss.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        out = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    out = out.where(avg_loss != 0, 100.0)
    out = out.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return out.where(avg_gain.notna() & avg_loss.notna())
