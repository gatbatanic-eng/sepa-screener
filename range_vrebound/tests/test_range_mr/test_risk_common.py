import pandas as pd
import pytest

from src.risk.rr import compute_rr_series
from src.risk.stop import compute_atr_stop


def test_compute_atr_stop_known_value():
    reference = pd.Series([97.0])
    atr = pd.Series([2.0])
    stop = compute_atr_stop(reference, atr, atr_multiplier=0.5)
    assert stop.iloc[0] == pytest.approx(96.0)


def test_compute_rr_series_matches_scalar_compute_rr():
    entry = pd.Series([100.0, 100.0])
    stop = pd.Series([90.0, 100.0])  # 두 번째는 risk=0 (계산 불가)
    target = pd.Series([130.0, 120.0])
    result = compute_rr_series(entry, stop, target)
    assert result.iloc[0] == pytest.approx(3.0)
    assert pd.isna(result.iloc[1])
