"""일일 스크리닝의 종목 단위 처리 로직(src/screening.py::process_symbol)을
네트워크 없이 검증한다. 실제 데이터 수집(fetch_*)은 loader.py에서 이미
분리해뒀다 — 여기서는 "가져온 데이터로 신호를 만드는" 부분만 본다.
"""
from datetime import date, timedelta

import pandas as pd
import pytest

from src.config import load_config
from src.models.market_data import OHLCVBar
from src.models.signal import Signal
from src.screening import process_symbol

CONFIG = load_config()


def _make_bars(closes: list[float], start: date = date(2023, 1, 2)) -> list[OHLCVBar]:
    bars = []
    d = start
    for c in closes:
        bars.append(OHLCVBar(date=d, symbol="TEST", open=c, high=c + 1, low=c - 1, close=c, volume=1000.0))
        d = d + timedelta(days=1)
    return bars


def test_process_symbol_returns_none_none_when_insufficient_history():
    bars = _make_bars([100.0] * 10)
    benchmark_close = pd.Series([100.0] * 10, index=[b.date for b in bars])
    range_mr_signal, v_rebound_signal = process_symbol("TEST", bars, benchmark_close, None, CONFIG)
    assert range_mr_signal is None
    assert v_rebound_signal is None


def test_process_symbol_runs_without_crash_on_sufficient_history():
    n = 300
    closes = [100.0 + (i % 7) - 3 for i in range(n)]  # 약간의 변동을 준 횡보
    bars = _make_bars(closes)
    benchmark_close = pd.Series([100.0] * n, index=[b.date for b in bars])
    range_mr_signal, v_rebound_signal = process_symbol("TEST", bars, benchmark_close, None, CONFIG)
    assert range_mr_signal is None or isinstance(range_mr_signal, Signal)
    assert v_rebound_signal is None or isinstance(v_rebound_signal, Signal)


def test_process_symbol_produces_real_signal_for_engineered_v_rebound_scenario():
    # config.data.min_trading_days(기본 260) 이상을 채워야 process_symbol이
    # "데이터 부족"으로 조기 반환하지 않는다.
    pre = [100.0] * 280
    crash = [100.0, 90.0, 80.0, 70.0]
    low_period = [70.0, 71.0, 72.0, 73.0, 74.0]
    rebound = [76.0, 79.0]  # 마지막 날("오늘")이 WATCH 구간에 들어오도록
    closes = pre + crash + low_period + rebound
    bars = _make_bars(closes)
    benchmark_close = pd.Series([100.0] * len(closes), index=[b.date for b in bars])

    _, v_rebound_signal = process_symbol("TEST", bars, benchmark_close, None, CONFIG)
    assert isinstance(v_rebound_signal, Signal)
    assert v_rebound_signal.symbol == "TEST"


def test_process_symbol_propagates_stock_name():
    pre = [100.0] * 280
    crash = [100.0, 90.0, 80.0, 70.0]
    low_period = [70.0, 71.0, 72.0, 73.0, 74.0]
    rebound = [76.0, 79.0]
    closes = pre + crash + low_period + rebound
    bars = _make_bars(closes)
    benchmark_close = pd.Series([100.0] * len(closes), index=[b.date for b in bars])

    _, v_rebound_signal = process_symbol("TEST", bars, benchmark_close, None, CONFIG, name="테스트전자")
    assert v_rebound_signal.name == "테스트전자"


def test_process_symbol_benchmark_misaligned_index_does_not_crash():
    # 벤치마크 시계열의 날짜가 종목과 완전히 일치하지 않아도(공휴일 차이 등)
    # 크래시 없이 처리돼야 한다(reindex로 정렬).
    n = 300
    closes = [100.0 + (i % 7) - 3 for i in range(n)]
    bars = _make_bars(closes)
    shifted_dates = [b.date + timedelta(days=1) for b in bars]  # 하루씩 밀린 벤치마크
    benchmark_close = pd.Series([100.0] * n, index=shifted_dates)

    range_mr_signal, v_rebound_signal = process_symbol("TEST", bars, benchmark_close, None, CONFIG)
    assert range_mr_signal is None or isinstance(range_mr_signal, Signal)
    assert v_rebound_signal is None or isinstance(v_rebound_signal, Signal)
