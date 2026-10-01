from datetime import date

import pandas as pd
import pytest

from unittest import mock

from src.data import loader
from src.data.loader import dataframe_to_ohlcv_bars, select_top_n_by_market_cap


def test_select_top_n_by_market_cap_sorts_and_limits():
    listing = pd.DataFrame({
        "Code": ["A", "B", "C"],
        "Name": ["Alpha", "Beta", "Gamma"],
        "Marcap": [100, 300, 200],
    })
    result = select_top_n_by_market_cap(listing, top_n=2)
    assert list(result["Code"]) == ["B", "C"]


def test_select_top_n_by_market_cap_drops_missing_marcap():
    listing = pd.DataFrame({
        "Code": ["A", "B"],
        "Name": ["Alpha", "Beta"],
        "Marcap": [100, None],
    })
    result = select_top_n_by_market_cap(listing, top_n=5)
    assert list(result["Code"]) == ["A"]


def test_select_top_n_by_market_cap_missing_column_raises():
    listing = pd.DataFrame({"Code": ["A"], "Name": ["Alpha"]})
    with pytest.raises(ValueError):
        select_top_n_by_market_cap(listing, top_n=5)


def test_dataframe_to_ohlcv_bars_converts_rows():
    idx = pd.to_datetime(["2026-01-05", "2026-01-06"])
    df = pd.DataFrame({
        "Open": [100, 101], "High": [105, 106], "Low": [99, 100],
        "Close": [104, 105], "Volume": [1000, 1200],
    }, index=idx)
    bars = dataframe_to_ohlcv_bars(df, "005930")
    assert len(bars) == 2
    assert bars[0].symbol == "005930"
    assert bars[0].date == date(2026, 1, 5)
    assert bars[1].close == 105


def test_fetch_kr_universe_falls_back_to_naver_when_krx_is_down():
    naver = pd.DataFrame({
        "Code": ["005930", "000660", "035720"],
        "Name": ["삼성전자", "SK하이닉스", "카카오"],
        "Market": ["KOSPI", "KOSPI", "KOSDAQ GLOBAL"],
        "Marcap": [400.0, 300.0, 50.0],
    })
    with mock.patch.object(loader.fdr, "StockListing", side_effect=ValueError("KRX 점검")), \
            mock.patch.object(loader, "naver_kr_listing", return_value=naver) as fallback:
        out = loader.fetch_kr_universe(2)
    fallback.assert_called_once()
    assert list(out["Code"]) == ["005930", "000660"]


def test_fetch_kr_universe_uses_krx_when_available():
    krx = pd.DataFrame({"Code": ["005930"], "Name": ["삼성전자"], "Market": ["KOSPI"], "Marcap": [400.0]})
    with mock.patch.object(loader.fdr, "StockListing", return_value=krx), \
            mock.patch.object(loader, "naver_kr_listing") as fallback:
        loader.fetch_kr_universe(5)
    fallback.assert_not_called()
