from datetime import date

import numpy as np
import pandas as pd

from src.market.regime import MarketRegime, RegimeType, regime_row_to_model


def test_regime_row_to_model_converts_row_with_valid_values():
    row = pd.Series(
        {
            "regime": RegimeType.CRASH,
            "return_20d": -0.12,
            "drawdown_60d": -0.18,
            "return_60d": -0.15,
            "band_width_60d": 0.20,
            "days_since_last_crash": 0,
        }
    )
    model = regime_row_to_model(date(2024, 3, 1), row)
    assert isinstance(model, MarketRegime)
    assert model.regime == RegimeType.CRASH
    assert model.return_20d == -0.12


def test_regime_row_to_model_converts_nan_to_none():
    row = pd.Series(
        {
            "regime": RegimeType.NORMAL,
            "return_20d": np.nan,
            "drawdown_60d": np.nan,
            "return_60d": np.nan,
            "band_width_60d": np.nan,
            "days_since_last_crash": None,
        }
    )
    model = regime_row_to_model(date(2024, 3, 1), row)
    assert model.return_20d is None
    assert model.drawdown_60d is None
